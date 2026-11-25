#!/usr/bin/env python3
"""의도 5층 · 구매 단계 분류와 볼륨 가중 분포 (decode-search-intent 고유 스크립트).

세 하위 명령이 있다.

  upstream  STEP 4.5 상류 보강 판정 — 절단 호출이 있고 «시드 밖» L1·L2 그룹이 10개 미만이면 보강.
  pick      2단 조회의 대상을 고른다 — `all`(10/kw)을 걸 키워드 목록.
            canonical 그룹마다 대표 표기 1개만 뽑고, 같은 검색의 중복 행·경계 밖 행은 뽑지 않는다.
  report    분류·집계·교차확인을 하고 CSV와 요약 MD를 쓴다.

분류는 **규칙 사전**(`intent_dict.json`)으로만 한다. 사전에 안 걸린 검색어는 «미분류»로 남기고
억지로 층에 넣지 않는다 — 미분류 비중이 곧 사전의 빈칸이다.

  경계(exclude)  사전 `exclude` 패턴에 걸린 행은 «경계 밖» — 분포·2단 대상에서 빠지고 따로 센다.
  브랜드         사전 `brands`. 긴 이름은 canonical 부분일치, **짧은 이름**(한글 1자 · 영문·숫자 3자 이하)은
                 검색어의 **단어(공백 단위 토큰) 전체가 일치**할 때만 — `려`가 `가려움`에 걸리지 않게.
  층(layer)      브랜드가 걸리면 L5, 아니면 사전 themes 배열에서 먼저 선언된 층. 걸린 층 전부는 layer_facets.
                 layer_facets_beyond_seed는 **시드에 들어 있는 사전 표현을 뺀** 층 — 시드 토큰
                 (`탈모`)이 모든 반환에 들어가 층 포함률이 자명해지는 것을 막는다. L5 브랜드도 포함한다
                 (브랜드가 시드에 들어 있을 때만 뺀다 — 예전에는 늘 빠져 «시드 표현 제외» L5가 0.0%였다).
  단계(stage)    사전 stages 배열을 순서대로 평가해 먼저 걸린 것. 전부는 stage_facets.

집계(계약 T5):
  ① 시계열·지표가 완전히 같은 행은 같은 검색의 중복이다(`dup_of`, series_dedup.py) — 한 번만 센다.
     canonical이 달라도(어순·복수형·동의어) 적용한다.
  ② 남은 표기변형 그룹은 **하한 = 그룹 내 최댓값**과 **상한 = 그룹 내 합**을 둘 다 낸다 [추정].
     분포·순위는 하한 기준이고, 상한 기준에서 순위가 뒤집히는 구분은 표에 ⚠️로 적는다.

API 의도 라벨(`intents`)은 0/1 플래그이고 두 개가 동시에 1일 수 있다. 단계 규칙과의 일치는
«그 단계의 플래그가 1인가»로 본다(최댓값 하나를 고르지 않는다).

    python3 intent_layers.py upstream --universe keyword_universe.csv --dict intent_dict.json \\
        --seeds seeds.json --call "러닝화=if_러닝화.json@100"
    python3 intent_layers.py pick --universe keyword_universe.csv --dict intent_dict.json \\
        --seeds seeds.json --top 50 --per-layer 5 -o gated.json
    python3 intent_layers.py report --universe keyword_universe.csv --dict intent_dict.json \\
        --seeds seeds.json --intents ki_all.json --gated gated.json \\
        --call "러닝화=if_러닝화.json@100" --queried-at 2026-09-28 \\
        -o intent_layers.csv --report intent_summary.md
"""
import argparse, csv, json, pathlib, re, sys, unicodedata
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import apply_rules, canon as _canon, dedup_map  # noqa: E402  (계약 T5)

LAYER_BRAND = "L5 브랜드"
UNCLASSIFIED = "미분류"
STAGE_TO_API = {"탐색": "i", "비교": "c", "구매": "t"}   # keyword_info `intents` 플래그와의 대응
UPSTREAM_MIN = 10        # [가정] STEP 4.5 — 시드 밖 L1·L2 그룹이 이보다 적으면 보강
AGREE_MIN_N = 20         # [가정] 일치율을 결론에 쓰기 위한 최소 대조 그룹 수
SHORT_HANGUL = 1         # [가정] 이 길이 이하 한글 브랜드는 단어 일치만
SHORT_LATIN = 3          # [가정] 이 길이 이하 영문·숫자 브랜드는 단어 일치만


def load(path):
    o = json.load(open(path, encoding="utf-8"))
    return o["data"] if isinstance(o, dict) and "data" in o else o


def load_keywords(path):
    """키워드 배열 JSON · intent_finder/keyword_info 응답 · 요청 본문({"keywords": [...]}) 모두 받는다."""
    o = json.load(open(path, encoding="utf-8"))
    if isinstance(o, dict):
        o = o.get("keywords") or o.get("data") or []
    return [x if isinstance(x, str) else x.get("keyword") for x in o if x]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ── 사전 ──────────────────────────────────────────────────────────────────
class Dict:
    def __init__(self, path):
        cfg = json.load(open(path, encoding="utf-8"))
        self.subs = cfg.get("rules", {}).get("변형치환", {})
        self.themes = [(t["name"], re.compile(t["pattern"])) for t in cfg.get("themes", [])]
        self.stages = [(s["name"], re.compile(s["pattern"])) for s in cfg.get("stages", [])]
        self.brands = [b for b in cfg.get("brands", []) if b]
        ex = cfg.get("exclude")
        if isinstance(ex, dict):
            ex = ex.get("pattern")
        if isinstance(ex, list):
            ex = "|".join(p for p in ex if p)
        self.exclude = re.compile(ex) if ex else None

    def canon(self, kw):
        return _canon(kw, self.subs)

    def tokens(self, kw):
        s = apply_rules(unicodedata.normalize("NFKC", kw).lower(), self.subs)
        return set(s.split())

    @staticmethod
    def is_short(bc):
        if all(ord(ch) < 128 for ch in bc):
            return len(bc) <= SHORT_LATIN
        return len(bc) <= SHORT_HANGUL

    def brand_of(self, kw):
        """(브랜드, 가드로 막힌 짧은 브랜드) — 짧은 브랜드는 단어 전체 일치만 인정한다."""
        c, toks, blocked = self.canon(kw), None, ""
        for b in self.brands:
            bc = self.canon(b)
            if not bc or bc not in c:
                continue
            if self.is_short(bc):
                toks = toks if toks is not None else {self.canon(t) for t in self.tokens(kw)}
                if bc not in toks:
                    blocked = blocked or b
                    continue
            return b, ""
        return "", blocked

    def layers(self, c):
        return [n for n, p in self.themes if p.search(c)]

    def layers_beyond(self, c, seed_terms):
        """시드에 들어 있는 사전 표현만으로 걸린 층은 뺀다."""
        out = []
        for n, p in self.themes:
            terms = {m.group(0) for m in p.finditer(c) if m.group(0)}
            if terms - seed_terms.get(n, set()):
                out.append(n)
        return out


def seed_list(a, rows):
    if getattr(a, "seeds", None):
        return load_keywords(a.seeds)
    return sorted({r.get("source_seed", "") for r in rows} - {"", "(미상·다중시드)"})


def annotate(rows, d, seeds, metrics=None):
    """경계·중복·브랜드(가드)·층·시드 밖 층을 행에 붙인다. pick/upstream/report 공통."""
    seed_c = {d.canon(s) for s in seeds}
    seed_terms = defaultdict(set)
    for sc in seed_c:
        for n, p in d.themes:
            seed_terms[n] |= {m.group(0) for m in p.finditer(sc) if m.group(0)}
    have_dup = rows and "dup_of" in rows[0]
    if not have_dup and metrics:
        dm = dedup_map(load(metrics), d.subs)
        for r in rows:
            v = dm.get(r["keyword"], {})
            r["dup_of"] = "" if v.get("rep", r["keyword"]) == r["keyword"] else v["rep"]
            r["dup_basis"] = v.get("basis", "")
        have_dup = True
    blocked = defaultdict(list)
    for r in rows:
        r["_vol"] = num(r.get("volume_total"))
        r["canonical"] = r.get("canonical") or d.canon(r["keyword"])
        r.setdefault("dup_of", ""); r.setdefault("dup_basis", "")
        c = r["canonical"]
        r["boundary_excluded"] = "Y" if d.exclude and d.exclude.search(c) else ""
        r["brand"], blk = d.brand_of(r["keyword"])
        if blk:
            blocked[blk].append(r["keyword"])
        facets = d.layers(c)
        beyond = d.layers_beyond(c, seed_terms)
        if r["brand"] and not any(d.canon(r["brand"]) in sc for sc in seed_c):
            beyond = [LAYER_BRAND] + beyond
        hits = [n for n, p in d.stages if p.search(c)]
        r["stage"] = hits[0] if hits else UNCLASSIFIED
        r["layer"] = LAYER_BRAND if r["brand"] else (facets[0] if facets else UNCLASSIFIED)
        r["layer_facets"] = "|".join(([LAYER_BRAND] if r["brand"] else []) + facets)
        r["layer_facets_beyond_seed"] = "|".join(beyond)
        r["is_seed"] = "Y" if c in seed_c else ""
        r["_live"] = not r["dup_of"] and not r["boundary_excluded"]
    return have_dup, blocked


def groups_of(rows):
    """집계 단위 — 경계 밖·같은 검색의 중복 행을 뺀 canonical 그룹."""
    g = defaultdict(list)
    for r in rows:
        if r["_live"]:
            g[r["canonical"]].append(r)
    return g


def representative(members):
    return max(members, key=lambda r: (r["_vol"], -len(r["keyword"])))


def parse_calls(specs):
    calls = []
    for spec in specs:
        label, rest = spec.split("=", 1)
        for part in rest.split(","):
            path, _, lim = part.partition("@")
            n = len(load(path))
            lim = int(lim) if lim else None
            calls.append((label if "," not in rest else f"{label}·{pathlib.Path(path).stem}",
                          lim, n, lim is not None and n >= lim))
    return calls


def upstream_check(reps, calls):
    """STEP 4.5 — 절단 호출이 있고 시드 밖 L1·L2 논브랜드 그룹이 UPSTREAM_MIN 미만이면 보강."""
    up = [r for r in reps if not r["brand"] and not r["is_seed"]
          and any(f.startswith(("L1", "L2")) for f in r["layer_facets_beyond_seed"].split("|") if f)]
    up.sort(key=lambda r: -r["_vol"])
    truncated = [c[0] for c in calls if c[3]]
    fire = bool(truncated) and len(up) < UPSTREAM_MIN
    return fire, up, truncated


def upstream_lines(fire, up, truncated):
    L = [f"- 절단 호출 {len(truncated)}건 · 시드 밖 L1·L2 논브랜드 그룹 {len(up)}개 (기준 {UPSTREAM_MIN}개 미만) [가정]"]
    if fire:
        cand = ", ".join(f"«{r['keyword']}»" for r in up[:2]) or "없음 — 사전 L1·L2 패턴을 먼저 보강"
        L.append(f"- **발동** — 보강 시드 후보(시드 밖 L1·L2 볼륨 상위 2): {cand}. 1회만 돌리고 라벨 `R2·<시드>`로 기록한다")
    elif not truncated:
        L.append("- 미발동 — 절단 호출이 없다")
    else:
        L.append(f"- 미발동 — 시드 밖 L1·L2 그룹이 {len(up)}개로 기준 이상. 절단 경고(L1·L2 하한)는 유지한다")
    return L


def read_universe(a):
    rows = list(csv.DictReader(open(a.universe, encoding="utf-8-sig")))
    d = Dict(a.dict)
    have_dup, blocked = annotate(rows, d, seed_list(a, rows), getattr(a, "metrics", None))
    return rows, d, have_dup, blocked


def print_guards(rows, have_dup, blocked):
    dups = [r for r in rows if r["dup_of"]]
    ex = [r for r in rows if r["boundary_excluded"]]
    if not have_dup:
        print("⚠️ 유니버스에 dup_of 컬럼이 없다 — build_universe.py를 다시 돌리거나 --metrics를 줄 것 "
              "(같은 검색의 중복을 가리지 못하면 하한이 하한이 아니다)")
    print(f"같은 검색의 중복 {len(dups)}행 병합(볼륨 {sum(r['_vol'] for r in dups):,.0f} 한 번만 셈) [추정] · "
          f"경계 밖 {len(ex)}행 제외(볼륨 {sum(r['_vol'] for r in ex):,.0f}) [가정]")
    for b, kws in blocked.items():
        print(f"브랜드 가드: 짧은 브랜드 «{b}»가 단어 밖에서만 걸린 {len(kws)}행은 브랜드로 보지 않았다 "
              f"(예: {', '.join(kws[:3])})")


# ── upstream ──────────────────────────────────────────────────────────────
def cmd_upstream(a):
    rows, d, have_dup, blocked = read_universe(a)
    print_guards(rows, have_dup, blocked)
    reps = [representative(m) for m in groups_of(rows).values()]
    fire, up, truncated = upstream_check(reps, parse_calls(a.call))
    print("STEP 4.5 상류 보강 판정")
    for ln in upstream_lines(fire, up, truncated):
        print(ln)


# ── pick ──────────────────────────────────────────────────────────────────
def cmd_pick(a):
    rows, d, have_dup, blocked = read_universe(a)
    print_guards(rows, have_dup, blocked)
    g = groups_of(rows)
    hi = {c: sum(r["_vol"] for r in m) for c, m in g.items()}
    reps = [representative(m) for m in g.values()]
    reps.sort(key=lambda r: -r["_vol"])                       # 순위는 하한(그룹 최댓값) 기준
    chosen = {r["keyword"]: r for r in reps[: a.top]}
    top_hi = sorted(reps, key=lambda r: -hi[r["canonical"]])[: a.top]
    only_hi = [r["keyword"] for r in top_hi if r["keyword"] not in chosen]
    # 층별 표본은 **시드 밖 층(facet)** 기준 — 주 층은 시드 토큰(L3)에 끌려가 L1·L2가 비기 쉽다.
    # 브랜드 행도 그 안의 L1·L2 표현으로 해당 층 표본에 들어간다(예전에는 L5에만 넣어 «헤라 수분 선크림»의
    # 수분이 L1 표본에서 빠졌다). 층 안에서는 브랜드 여부와 무관하게 볼륨 순이다
    by_layer = defaultdict(list)
    for r in reps:
        if r["is_seed"]:
            continue
        strata = [f for f in r["layer_facets_beyond_seed"].split("|") if f] or [r["layer"]]
        for s in strata:
            by_layer[s].append(r)
    added = defaultdict(int)
    for layer, rs in by_layer.items():          # 상위 목록에 이미 든 것은 세지 않고 층마다 per_layer개를 더한다
        for r in rs:
            if added[layer] >= a.per_layer:
                break
            if r["keyword"] not in chosen:
                chosen[r["keyword"]] = r
                added[layer] += 1
    # 단계-API 대조 표본 보장 — 탐색·비교·구매로 분류된 그룹이 --min-stage-groups개 이상 들어가도록 단계를
    # 돌아가며 볼륨 순으로 더한다(선크림 예시 실행: 기본 선택만으로는 대조 가능 그룹이 모자라 일치율 결론 불가)
    stage_names = [n for n, _ in d.stages if n in STAGE_TO_API]
    stage_pool = {n: [r for r in reps if r["stage"] == n] for n in stage_names}
    def n_staged():
        return sum(1 for r in chosen.values() if r["stage"] in STAGE_TO_API)
    before_stage, added_stage = n_staged(), defaultdict(int)
    while n_staged() < a.min_stage_groups:
        moved = False
        for n in stage_names:
            nxt = next((r for r in stage_pool[n] if r["keyword"] not in chosen), None)
            if nxt and n_staged() < a.min_stage_groups:
                chosen[nxt["keyword"]] = nxt; added_stage[n] += 1; moved = True
        if not moved:
            break
    out = [r["keyword"] for r in sorted(chosen.values(), key=lambda r: -r["_vol"])]
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"canonical {len(reps)}그룹 중 2단 대상 {len(out)}개 "
          f"(상위 {a.top} + 시드 밖 층마다 상위 목록 밖 {a.per_layer}개"
          + (f" + 단계 대조 보강 {sum(added_stage.values())}개" if added_stage else "") + f") → {a.out}")
    print("층별 추가: " + (" · ".join(f"{k} +{v}" for k, v in sorted(added.items())) or "없음"))
    staged = n_staged()
    print(f"단계 분류 그룹(탐색·비교·구매) {staged}개 — 단계-API 대조 후보 (보강 전 {before_stage}개"
          + (" · 보강 " + " · ".join(f"{k} +{v}" for k, v in added_stage.items()) if added_stage else "") + ")")
    if staged < max(a.min_stage_groups, AGREE_MIN_N):
        print(f"⚠️ 단계 분류 그룹이 {staged}개 < {max(a.min_stage_groups, AGREE_MIN_N)} — 사전 stages 패턴이 적게 걸린다. "
              "일치율을 결론에 쓰지 못할 수 있다(STEP 6). stages를 보강하거나 --min-stage-groups를 올릴 것")
    print("  (n 플래그만 켜진 그룹은 report 대조에서 빠지므로 실제 대조 수는 이보다 작을 수 있다)")
    print(f"2단 크레딧 = 10 × {len(out)} = {10 * len(out):,}")
    if only_hi:
        print(f"⚠️ 순위 뒤집힘: 상한(표기변형 합) 기준이면 상위 {a.top}에 드는 그룹 {len(only_hi)}개 "
              f"(하한 기준 선정에서 빠짐): {', '.join(only_hi[:10])}")


# ── report ────────────────────────────────────────────────────────────────
def month_diff(a, b):
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def flags_of(it):
    """intents는 0/1 플래그다. 분수 응답(구형)이 오면 0.5 이상을 1로 본다."""
    return {k: num(it.get(k)) >= 0.5 for k in ("i", "n", "c", "t")}


def cmd_report(a):
    rows, d, have_dup, blocked = read_universe(a)
    if not d.stages:
        sys.exit("사전에 stages가 없다 — 구매 단계를 분류할 수 없다 (SKILL.md «의도 사전» 참조)")
    print_guards(rows, have_dup, blocked)

    # keyword_info(all) 2단 응답 — API 의도 라벨과 데이터 기준월. 요청↔응답 대조는 canonical로 한다
    api, ref_month, all_kw = {}, "", set()
    if a.intents:
        for x in load(a.intents):
            all_kw.add(d.canon(x["keyword"]))
            it = x.get("intents") or {}
            if it:
                api[d.canon(x["keyword"])] = flags_of(it)
            months = [m.get("month", "") for m in x.get("monthly_volume") or [] if "total" in m]
            if months:
                ref_month = max(ref_month, max(months))
    lag = month_diff(ref_month, a.queried_at[:7]) if ref_month and a.queried_at else ""
    missing2 = [k for k in (load_keywords(a.gated) if a.gated else []) if d.canon(k) not in all_kw]
    have = {r["canonical"] for r in rows}
    missing1 = []
    for spec in a.requested:
        missing1 += [k for k in load_keywords(spec) if d.canon(k) not in have]

    multi = 0
    for r in rows:
        c = r["canonical"]
        hits = [n for n, p in d.stages if p.search(c)]
        r["stage"] = hits[0] if hits else UNCLASSIFIED
        r["stage_facets"] = "|".join(hits)
        fl = api.get(c)
        if fl:
            r.update({f"api_{k}": int(v) for k, v in fl.items()})
            on = [k for k in ("i", "n", "c", "t") if fl[k]]
            r["api_intent_flags"] = "|".join(on)
            want = STAGE_TO_API.get(r["stage"])
            if want is None or not on or on == ["n"]:
                r["stage_api_agree"] = ""
            else:
                r["stage_api_agree"] = "Y" if fl[want] else "N"
        else:
            r.update({"api_i": "", "api_n": "", "api_c": "", "api_t": "", "api_intent_flags": "",
                      "stage_api_agree": ""})

    g = groups_of(rows)
    grp_of = {}
    for c, members in g.items():
        rep = representative(members)
        hi = sum(r["_vol"] for r in members)
        for r in members:
            r["group_volume_lo"] = int(rep["_vol"])
            r["group_volume_hi"] = int(hi)
            r["group_n_variants"] = len(members)
            r["is_group_representative"] = "Y" if r is rep else ""
        rep["_hi"] = hi
        grp_of[c] = rep
        multi += len([k for k in rep["api_intent_flags"].split("|") if k]) >= 2
    by_kw = {r["keyword"]: r for r in rows}
    for r in rows:                       # 중복·경계 밖 행 — 어느 그룹에 세였는지(또는 빠졌는지)만 보인다
        if r["_live"]:
            continue
        rep = grp_of.get(by_kw.get(r["dup_of"], {}).get("canonical", "")) if r["dup_of"] else None
        r["group_volume_lo"] = rep["group_volume_lo"] if rep else ""
        r["group_volume_hi"] = rep["group_volume_hi"] if rep else ""
        r["group_n_variants"] = rep["group_n_variants"] if rep else ""
        r["is_group_representative"] = ""

    cols = ["keyword", "canonical", "volume_total", "dup_of", "dup_basis", "boundary_excluded",
            "group_volume_lo", "group_volume_hi", "group_n_variants", "is_group_representative",
            "is_seed", "brand", "layer", "layer_facets", "layer_facets_beyond_seed", "stage", "stage_facets",
            "api_intent_flags", "api_i", "api_n", "api_c", "api_t", "stage_api_agree",
            "discovery_round", "source_seed", "data_ref_month", "queried_at",
            "label_volume", "label_group_volume", "label_layer", "label_api_intent"]
    rows.sort(key=lambda r: -r["_vol"])
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            r["data_ref_month"] = ref_month
            r["queried_at"] = a.queried_at
            r["label_volume"] = "실제 데이터" if r.get("volume_total") not in ("", None) else "데이터 공백"
            r["label_group_volume"] = "추정"
            r["label_layer"] = "추정" if r["layer"] != UNCLASSIFIED else "데이터 공백"
            r["label_api_intent"] = "실제 데이터" if r["api_intent_flags"] else "데이터 공백"
            w.writerow({k: r.get(k, "") for k in cols})

    # 분포 — canonical 대표 행만(중복·경계 밖 제외), 전체 vs 논브랜드
    reps = [r for r in rows if r.get("is_group_representative") == "Y"]
    def dist(key, subset):
        """구분별 (그룹 수, 하한 볼륨, 상한 볼륨, 하한 비중, 상한 비중, 하한 순위, 상한 순위)."""
        t_lo = sum(r["_vol"] for r in subset) or 1
        t_hi = sum(r["_hi"] for r in subset) or 1
        lo, hi, cnt = defaultdict(float), defaultdict(float), defaultdict(int)
        for r in subset:
            lo[r[key]] += r["_vol"]; hi[r[key]] += r["_hi"]; cnt[r[key]] += 1
        order_lo = sorted(lo, key=lambda k: (-lo[k], k))
        rank_hi = {k: i for i, k in enumerate(sorted(hi, key=lambda k: (-hi[k], k)), 1)}
        return {k: (cnt[k], lo[k], hi[k], lo[k] / t_lo, hi[k] / t_hi, i, rank_hi[k])
                for i, k in enumerate(order_lo, 1)}
    nonbrand = [r for r in reps if not r.get("brand")]
    grp_sum = sum(r["_vol"] for r in reps)         # 하한
    grp_sum_hi = sum(r["_hi"] for r in reps)       # 상한
    dups = [r for r in rows if r["dup_of"]]
    excl = [r for r in rows if r["boundary_excluded"] and not r["dup_of"]]

    calls = parse_calls(a.call)
    truncated = sum(1 for c in calls if c[3])
    fire, up, trunc_labels = upstream_check(reps, calls)

    agree = [r for r in reps if r["stage_api_agree"] in ("Y", "N")]
    agree_rate = (sum(1 for r in agree if r["stage_api_agree"] == "Y") / len(agree)) if agree else None

    L = []
    L.append(f"# 검색 의도 분해 — {a.title}\n")
    L.append(f"- 데이터 기준월: {ref_month or '미확인(2단 all 응답 없음) [데이터 공백]'} "
             f"/ 조회일: {a.queried_at or '미기재'} / 지연: {str(lag) + '개월' if lag != '' else '—'} [실제 데이터]")
    L.append("- 검색량 = `volume_total`(데이터 기준월로 끝나는 12개월 합, 단위 건) [실제 데이터]")
    L.append(f"- 행 {len(rows)} → 같은 검색의 중복 {len(dups)}행 병합(볼륨 {sum(r['_vol'] for r in dups):,.0f}을 한 번만 셈 · "
             f"시계열·지표 일치 규칙) [추정] · 경계 밖 {len(excl)}행 제외(볼륨 {sum(r['_vol'] for r in excl):,.0f}) [가정]")
    L.append(f"- 남은 행 → canonical {len(reps)}그룹 · 그룹 볼륨 **하한 {grp_sum:,.0f} ~ 상한 {grp_sum_hi:,.0f}** "
             "(하한 = 표기변형 그룹 최댓값 · 상한 = 그룹 합) [추정]")
    L.append("- 시계열이 다른 표기변형이 같은 검색인지 다른 검색인지 응답으로는 알 수 없다. 비중·순위는 **하한 기준**이고, "
             "상한 기준에서 순위가 뒤집히는 구분은 ⚠️로 표시한다")
    if not have_dup:
        L.append("- ⚠️ 유니버스에 `dup_of`가 없어 같은 검색의 중복을 가리지 못했다 — 하한이 부풀었을 수 있다 [데이터 공백]")
    for b, kws in blocked.items():
        L.append(f"- 브랜드 가드: 짧은 브랜드 «{b}»가 단어 밖에서만 걸린 {len(kws)}행은 브랜드로 세지 않았다 [가정]")
    if missing1 or missing2:
        L.append(f"- 미반환 [데이터 공백]: 1단 {len(missing1)}개"
                 + (f"({', '.join(missing1[:5])})" if missing1 else "")
                 + f" · 2단 {len(missing2)}개" + (f"({', '.join(missing2[:5])})" if missing2 else "")
                 + " — 요청했으나 응답에 없다(과금은 요청 수 기준). 결측 0으로 세지 않는다")
    L.append("")
    L.append("## 확장 절단 점검\n")
    L.append("| 호출 | 요청 limit | 반환 | 판정 |\n|---|---:|---:|---|")
    for label, lim, n, t in calls:
        L.append(f"| {label} | {lim if lim is not None else '—'} | {n} | "
                 f"{'절단 — 상위 볼륨 표본, 분포 확정 보류' if t else '미절단'} |")
    if truncated:
        L.append(f"\n> 절단 {truncated}건. 아래 분포는 «시장 의도 구성»이 아니라 **볼륨 상위 표본의 구성**이다. "
                 "꼬리(대개 니즈·목표층)가 잘렸으므로 L1·L2 비중은 하한으로 읽는다.")
    L.append("\n## 상류 보강 판정 (STEP 4.5)\n")
    L += upstream_lines(fire, up, trunc_labels)
    flips = []
    def rank_cell(dd, k):
        if k not in dd:
            return "—"
        i, j = dd[k][5], dd[k][6]
        return f"{i}" if i == j else f"{i} → {j} ⚠️"
    for title, key in (("의도 5층", "layer"), ("구매 단계", "stage")):
        L.append(f"\n## {title} 분포 (볼륨 가중 · canonical 그룹 · 비중은 하한 기준 / 상한 기준) [추정]\n")
        L.append("| 구분 | 전체 그룹 | 전체 볼륨 하한 ~ 상한 | 전체 비중 하한 기준 | 전체 비중 상한 기준 | 전체 순위 하한→상한 "
                 "| 논브랜드 그룹 | 논브랜드 비중 하한 기준 | 논브랜드 비중 상한 기준 | 논브랜드 순위 하한→상한 |\n"
                 "|---|---:|---:|---:|---:|---|---:|---:|---:|---|")
        da, dn = dist(key, reps), dist(key, nonbrand)
        for k, (c, lo, hi, s_lo, s_hi, _, _) in da.items():
            nc, _, _, ns_lo, ns_hi, _, _ = dn.get(k, (0, 0, 0, 0.0, 0.0, 0, 0))
            L.append(f"| {k} | {c} | {lo:,.0f} ~ {hi:,.0f} | {s_lo:.1%} | {s_hi:.1%} | {rank_cell(da, k)} "
                     f"| {nc} | {ns_lo:.1%} | {ns_hi:.1%} | {rank_cell(dn, k)} |")
        for scope, dd in (("전체", da), ("논브랜드", dn)):
            moved = [k for k, v in dd.items() if v[5] != v[6]]
            if moved:
                flips.append(f"{title}·{scope}")
                L.append(f"\n> ⚠️ 순위 뒤집힘({scope}): 상한(표기변형 합) 기준이면 "
                         + ", ".join(f"«{k}» {dd[k][5]}→{dd[k][6]}위" for k in moved)
                         + ". 결론은 하한 순위로 쓰되 이 사실을 함께 적는다.")
    fac, fac_hi, fac_b = defaultdict(float), defaultdict(float), defaultdict(float)
    for r in reps:
        for f in set(r["layer_facets"].split("|")) - {""}:
            fac[f] += r["_vol"]; fac_hi[f] += r["_hi"]
        for f in set(r["layer_facets_beyond_seed"].split("|")) - {""}:
            fac_b[f] += r["_vol"]
    L.append("\n## 층 포함률 (주 층과 무관하게 해당 층 표현이 들어간 볼륨 비중) [추정]\n")
    L.append("| 층 | 포함 비중 하한 기준 | 포함 비중 상한 기준 | 시드 표현 제외 · 하한 기준 |\n|---|---:|---:|---:|")
    for f in sorted(fac, key=lambda k: -fac[k]):
        L.append(f"| {f} | {fac[f] / (grp_sum or 1):.1%} | {fac_hi[f] / (grp_sum_hi or 1):.1%} "
                 f"| {fac_b.get(f, 0) / (grp_sum or 1):.1%} |")
    L.append("\n> «시드 표현 제외»는 시드에 들어 있는 사전 표현(예: 시드 `탈모샴푸`의 `탈모`)만으로 걸린 층을 뺀 값이다. "
             "니즈 근거로는 이 열을 쓴다 — 시드 토큰은 모든 반환에 들어가 포함률을 자명하게 만든다.")
    L.append("\n## 하한·상한 순위 뒤집힘\n")
    L.append(f"- {'⚠️ ' + ' · '.join(flips) + ' 분포에서 뒤집힘 — 위 표의 ⚠️ 참조' if flips else '없음 — 하한·상한 어느 쪽이든 같은 순위'} [추정]")
    L.append("\n## API 의도 라벨과의 교차확인\n")
    if agree_rate is None:
        L.append("- 대조 가능한 그룹 없음 — 2단 `all` 응답이 없거나 단계가 전부 미분류 [데이터 공백]")
    else:
        L.append(f"- 단계 규칙 vs `intents` 플래그 일치율: {agree_rate:.1%} ({len(agree)}그룹 대조) [추정] — "
                 "그 단계의 플래그(탐색=i · 비교=c · 구매=t)가 1이면 일치")
        L.append(f"- `intents`는 0/1 플래그다. 2개 이상 켜진 그룹 {multi}개 — 어느 하나로 고르지 않고 해당 플래그만 본다 [실제 데이터]")
        L.append("- `n`(내비게이션)만 켜진 그룹과 단계 미분류 그룹은 대조에서 뺐다")
        if len(agree) < AGREE_MIN_N:
            L.append(f"- ⚠️ 대조 {len(agree)}그룹 < {AGREE_MIN_N} — 표본이 작아 일치율을 결론에 쓰지 않는다. "
                     "2단 대상을 늘리거나(`pick --per-layer`) 불일치 그룹을 하나씩 읽는다 [가정]")
    L.append("\n## 라벨\n")
    L.append("- [실제 데이터] 검색량·API `intents` 플래그·기준월 · [추정] 층·단계 분류, 일치율, 같은 검색의 중복 판정, "
             "그룹 볼륨 하한(최댓값)·상한(합)과 그에 기댄 비중·순위(규칙 기반 집계) · "
             "[가정] 사전 패턴과 선언 순서, 경계 패턴, 짧은 브랜드 가드, 순위를 하한 기준으로 두는 규칙, 보강·표본 기준 · "
             "[데이터 공백] 미분류, 2단 미조회 그룹, 미반환 키워드")
    open(a.report, "w", encoding="utf-8").write("\n".join(L) + "\n")

    unc = sum(r["_vol"] for r in reps if r["layer"] == UNCLASSIFIED) / (grp_sum or 1)
    unc_hi = sum(r["_hi"] for r in reps if r["layer"] == UNCLASSIFIED) / (grp_sum_hi or 1)
    print(f"{len(rows)}행 → canonical {len(reps)}그룹 · 논브랜드 {len(nonbrand)}그룹")
    print(f"그룹 볼륨 하한 {grp_sum:,.0f} ~ 상한 {grp_sum_hi:,.0f} [추정]")
    print(f"층 미분류 볼륨 비중 하한 기준 {unc:.1%} · 상한 기준 {unc_hi:.1%} — 크면 사전(themes) 보강")
    print("순위 뒤집힘: " + (" · ".join(flips) + " — 요약 MD의 ⚠️ 참조" if flips else "없음"))
    print(f"절단 호출 {truncated}/{len(calls)}" + (" — 분포 확정 보류" if truncated else ""))
    print(f"STEP 4.5 보강: {'발동' if fire else '미발동'} (시드 밖 L1·L2 논브랜드 {len(up)}그룹)")
    if agree_rate is not None:
        print(f"단계-API 일치율 {agree_rate:.1%} ({len(agree)}그룹 · 플래그 2개 이상 {multi}그룹)"
              + (f" ⚠️ 표본 {AGREE_MIN_N} 미만" if len(agree) < AGREE_MIN_N else ""))
    if missing1 or missing2:
        print(f"미반환 [데이터 공백]: 1단 {len(missing1)} · 2단 {len(missing2)}")
    print(f"데이터 기준월 {ref_month or '미확인'} · 조회일 {a.queried_at or '미기재'}")
    print(f"→ {a.out} · {a.report}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    def common(p):
        p.add_argument("--universe", required=True)
        p.add_argument("--dict", required=True, help="의도 사전 JSON (themes·brands·stages·exclude)")
        p.add_argument("--seeds", help="시드 목록 JSON — 없으면 유니버스 source_seed 값을 쓴다")
        p.add_argument("--metrics", help="dup_of 컬럼이 없는 옛 유니버스용 — keyword_info 응답으로 중복을 판정한다")
    u = sub.add_parser("upstream", help="STEP 4.5 상류 보강 판정")
    common(u)
    u.add_argument("--call", action="append", default=[], help="라벨=intent_finder응답.json@limit[,…]")
    p = sub.add_parser("pick", help="2단(all) 조회 대상 선정")
    common(p)
    p.add_argument("--top", type=int, default=50)
    p.add_argument("--per-layer", type=int, default=5)
    p.add_argument("--min-stage-groups", type=int, default=AGREE_MIN_N,
                   help=f"단계(탐색·비교·구매)로 분류된 그룹이 이 수 이상 들어가도록 보강 — 단계-API 대조 표본 "
                        f"(기본 {AGREE_MIN_N} = 일치율 결론 최소 표본 · 0이면 보강 안 함)")
    p.add_argument("-o", "--out", default="gated.json")
    r = sub.add_parser("report", help="분류·집계·교차확인")
    common(r)
    r.add_argument("--intents", help="keyword_info(all) 2단 응답 JSON")
    r.add_argument("--gated", help="2단 요청 목록(gated.json) — 미반환 대조")
    r.add_argument("--requested", action="append", default=[], help="1단 요청 목록·요청 본문 JSON — 미반환 대조")
    r.add_argument("--call", action="append", default=[], help="라벨=intent_finder응답.json@limit[,…]")
    r.add_argument("--queried-at", default="")
    r.add_argument("--title", default="")
    r.add_argument("-o", "--out", default="intent_layers.csv")
    r.add_argument("--report", default="intent_summary.md")
    a = ap.parse_args()
    {"upstream": cmd_upstream, "pick": cmd_pick, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
