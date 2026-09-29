#!/usr/bin/env python3
"""검색 점유율(SoV · 검색량 기준 근사) 산출 — 이 스킬 고유 스크립트 (SKILL.md STEP 3·6).

점유율은 «브랜드 키워드 검색량 합»으로 끝나지 않는다. 결과를 조용히 틀리게 만드는 것이 다섯 있다:

  1. 같은 검색의 중복 행 — 응답에 요청하지 않은 어순·복수형·동의어 변형이 함께 오고, 그중 일부는
     지표(볼륨·CPC·경쟁도)나 월 시계열까지 **완전히 같다**. canonical(띄어쓰기 제거)로는 묶이지 않는다
     (`shark cordless stick vacuum` = `stick vacuum cordless shark`). → `series_dedup.dedup_map`으로
     먼저 걸러 한 번만 센다(계약 T5 ①). 미국 실측에서 이것만으로 분모가 35.5% 부풀었다.
  2. 표기변형 — 그 밖의 같은 canonical 그룹(네스프레소/nespresso)은 같은 검색인지 다른 검색인지
     응답이 알려주지 않는다 → 하한(그룹 최댓값)·상한(그룹 합)을 병기하고 점유율도 각자의 분모로 낸다(T5 ②).
  3. 카테고리 밖 브랜드 검색 — "네스프레소" 단독은 머신인지 캡슐인지 모른다 → 분모에서 제외하고
     따로 적는다. 수집하지 않았으면 0이 아니라 «미수집»이다(--probe-terms로 수집 목록을 만든다).
  4. 논브랜드 — 두 점유율(전체 대비 · 브랜드 간)을 모두 낸다.
  5. 모델명 — 모델명(`버츄오`)은 브랜드를 알려주지만 **품목은 알려주지 않는다**(캡슐/머신). 카테고리
     단어 없이 모델명만 걸린 검색은 `model_only`로 분리해 따로 보고한다(--include-model-only로 포함).

판정 순서(canonical 기준, 그룹 전체가 같은 판정): exclude → 카테고리 패턴 → 모델명만 → 카테고리 밖.

시계열(--series, keyword_info all 응답)을 주면 전년 동기 대비 점유 변화를 낸다:
  - 점유 변화는 **대표 행(하한 기준)** 시계열로 계산한다(series_basis)
  - 시계열이 완전히 같은 행도 같은 검색의 중복으로 합친다(12개월 값도 함께 바뀔 수 있다)
  - `volume_trend` 필드는 쓰지 않는다 (계약 §2)
  - 마지막 달은 기본 제외 — 포함 모드도 함께 계산해 두 모드의 부호가 다르면 «잠정»으로 표시
  - 비교 창에 결측 월이 있는 키워드는 0으로 더하지 않고 «계산 불가»로 뺀다
  - 전년 창 볼륨이 --gate 미만인 버킷은 점유 변화를 내지 않는다

사용법:
  # 0단 — 브랜드 단독·비교 검색 수집 목록 (1단 유니버스에 더한다: N + N(N−1)/2개)
  python3 sov_calc.py --themes themes.json --probe-terms probe_terms.json

  # 1단 — ads_metrics 응답만으로 12개월 점유 + 2단 조회 대상 목록
  python3 sov_calc.py --metrics metrics.json --themes themes.json --requested universe.json \\
      --round "네스프레소=if_nespresso.json@100" --round "일반=if_generic.json@100,if_generic2.json@100" \\
      --series-gate 1000 --series-targets series_targets.json \\
      -o sov_keywords.csv --brand-out sov_brands.csv

  # 2단 — 게이트 통과분의 all 응답을 더해 점유 변화까지
  python3 sov_calc.py ... --series series.json --gate 3000 --queried-at 2026-09-28
"""
import argparse, csv, json, pathlib, re, sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon as _canon, dedup_map  # noqa: E402  같은 검색의 중복 판정 (계약 T5)

GENERIC = "논브랜드(일반)"
OUTSIDE = "카테고리 밖(분모 제외)"
NOT_COLLECTED = "미수집"
NOT_RETURNED = "미반환"


def load(path):
    o = json.load(open(path, encoding="utf-8"))
    return o["data"] if isinstance(o, dict) and "data" in o else o


def load_keywords(path):
    return [x if isinstance(x, str) else x.get("keyword") for x in load(path)]


def load_requested(path):
    """요청 목록 — 키워드 배열 JSON 또는 keyword_info 응답(request_detail.keywords)."""
    o = json.load(open(path, encoding="utf-8"))
    cost = None
    if isinstance(o, dict):
        cost = (o.get("cost_detail") or {}).get("total_cost")
        o = (o.get("request_detail") or {}).get("keywords") or o.get("keywords") or []
    return [x if isinstance(x, str) else x.get("keyword") for x in o], cost


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def window(end, size):
    return [shift(end, i) for i in range(size - 1, -1, -1)]


def acc(series, months):
    """(합계, 결측월수). 결측은 0이 아니라 결측 — total 키가 없는 달은 세지 않는다."""
    tot = miss = 0
    for m in months:
        r = series.get(m)
        if r is None or "total" not in r:
            miss += 1
        else:
            tot += r["total"]
    return tot, miss


def pct(x, d):
    return round(x / d * 100, 1) if d else ""


def sgn(x):
    return (x > 0) - (x < 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", action="append", default=[],
                    help="keyword_info ads_metrics 응답 (여러 번 줄 수 있다)")
    ap.add_argument("--themes", required=True, help="themes.json — brands·변형치환·category·brand_models")
    ap.add_argument("--probe-terms", help="브랜드 단독 N개 + 비교 'A vs B' N(N−1)/2개 목록을 쓸 JSON 경로")
    ap.add_argument("--requested", action="append", default=[],
                    help="1단에 요청한 키워드 목록(배열 JSON 또는 응답의 request_detail) — 요청↔응답 대조")
    ap.add_argument("--round", action="append", default=[],
                    help="intent_finder 라운드 `라벨=파일[@limit][,파일[@limit]…]`. 라벨이 브랜드명이면 그 브랜드의 라운드")
    ap.add_argument("--include-model-only", action="store_true",
                    help="카테고리 단어 없이 모델명만 걸린 검색을 브랜드 점유에 포함 (기본은 분리 — 리포트에 명시)")
    ap.add_argument("--series-gate", type=int, default=1000,
                    help="2단(all) 조회 대상 게이트 — 카테고리 안 대표 행 중 volume_total ≥ 이 값")
    ap.add_argument("--series-targets", help="2단 조회 대상 키워드 목록을 쓸 JSON 경로")
    ap.add_argument("--series", action="append", default=[], help="keyword_info all 응답 (2단)")
    ap.add_argument("--gate", type=int, default=3000, help="점유 변화 판정 기저 게이트 — 전년 창 볼륨")
    ap.add_argument("--window", type=int, default=3, help="점유 변화 비교 창 개월 수 (기본 3)")
    ap.add_argument("--include-last-month", action="store_true",
                    help="마지막 달을 창에 포함 (기본은 제외 — 리포트에 반드시 명시)")
    ap.add_argument("--queried-at", default=date.today().isoformat())
    ap.add_argument("-o", "--out", default="sov_keywords.csv")
    ap.add_argument("--brand-out", default="sov_brands.csv")
    a = ap.parse_args()

    cfg = json.load(open(a.themes, encoding="utf-8"))
    subs = cfg.get("rules", {}).get("변형치환", {})
    cat = cfg.get("category") or {}
    if not cat.get("pattern"):
        sys.exit("themes.json에 category.pattern이 없다 — 카테고리 경계가 곧 분모다. 먼저 정의할 것")

    def canon(kw):
        return _canon(kw, subs)   # 변형치환은 띄어쓰기를 지우기 전에 적용한다(단어 경계 보존)

    cat_re = re.compile(cat["pattern"])
    cat_ex = re.compile(cat["exclude"]) if cat.get("exclude") else None
    name_brands = list(cfg.get("brands", []))
    models = {b: re.compile(p) for b, p in (cfg.get("brand_models") or {}).items() if p}
    # brand_models에만 있는 브랜드(짧은 이름 — '일리'⊂'데일리')도 버킷이다. 이름 부분일치로는 잡지 않는다
    model_only_brands = [b for b in models if b not in name_brands]
    brands = name_brands + model_only_brands
    if not brands:
        sys.exit("brands가 비어 있다 — 전부 논브랜드로 잡혀 점유 계산이 무의미하다")
    bcanon = {b: canon(b) for b in brands}

    # ── 0단: 브랜드 단독·비교 검색 수집 목록 (카테고리 밖 버킷과 비교 검색이 구조상 비지 않게)
    if a.probe_terms:
        pairs = [f"{x} vs {y}" for i, x in enumerate(brands) for y in brands[i + 1:]]
        terms = brands + pairs
        json.dump(terms, open(a.probe_terms, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"브랜드 단독 {len(brands)}개 + 비교 {len(pairs)}개 = {len(terms)}개 → 1단(ads_metrics) "
              f"유니버스에 더한다 · {len(terms):,}크레딧(1 × 요청 키워드) → {a.probe_terms}")
        if not a.metrics:
            return 0
    if not a.metrics:
        sys.exit("--metrics가 필요하다 (--probe-terms만 쓸 때는 생략 가능)")

    def classify(c):
        hit = [b for b in name_brands if bcanon[b] in c]
        mhit = [b for b, p in models.items() if p.search(c)]
        allb = hit + [b for b in mhit if b not in hit]
        via_model = bool(allb) and all(b not in hit for b in allb)
        if cat_ex and cat_ex.search(c):
            return "excluded", "category.exclude", allb
        if cat_re.search(c):
            if not allb:
                return "generic", "category.pattern", allb
            return ("brand" if len(allb) == 1 else "multi_brand"), ("model" if via_model else "brand"), allb
        if mhit:
            # 모델명은 브랜드를 알려주지만 품목(캡슐/머신·스틱/핸디)을 알려주지 않는다
            if a.include_model_only:
                return ("brand" if len(allb) == 1 else "multi_brand"), "model(카테고리 단어 없음·포함)", allb
            return "model_only", "brand_models(카테고리 단어 없음)", allb
        if len(allb) >= 2:
            return "out_of_category", "비교(카테고리 미특정)", allb
        return "out_of_category", ("브랜드만" if allb else "경계 밖"), allb

    # ── 1단: ads_metrics 행
    raw, seen_kw = [], set()
    for path in a.metrics:
        for d in load(path):
            kw = d.get("keyword")
            if not isinstance(kw, str) or kw in seen_kw:   # 같은 키워드가 여러 파일에 있으면 한 번만
                continue
            seen_kw.add(kw)
            raw.append(d)

    series = {}
    series_rows = []
    for path in a.series:
        for d in load(path):
            if d.get("monthly_volume"):
                series[d["keyword"]] = {x["month"]: x for x in d["monthly_volume"] if isinstance(x, dict)}
                series_rows.append(d)

    # ── 같은 검색의 중복 (계약 T5 ①) — 지표 일치(1단)와 시계열 일치(2단)
    dmap = dedup_map(raw, subs)
    dup_of = {k: (v["rep"], v["basis"]) for k, v in dmap.items() if v["rep"] != k}
    if series_rows:
        for k, v in dedup_map(series_rows, subs).items():
            if v["rep"] != k and k not in dup_of and k in seen_kw:
                dup_of[k] = (v["rep"], v["basis"])

    def root(k):
        while k in dup_of:
            k = dup_of[k][0]
        return k

    # 묶음의 대표는 카테고리 안 표현을 우선한다 — 같은 검색이면 가장 구체적인 표현(카테고리 단어가 있는 쪽)이
    # 그 검색이 무엇인지 알려준다(`dyson v8` = `dyson v8 stick vacuum` → 스틱으로 센다) [가정]
    in_scopes = ("brand", "multi_brand", "generic")
    counted = in_scopes + (("model_only",) if a.include_model_only else ())
    bundles = defaultdict(list)
    for d in raw:
        bundles[root(d["keyword"])].append(d["keyword"])
    rep_of, mixed = {}, []
    for r0, mem in bundles.items():
        if len(mem) < 2:
            continue
        mem = sorted(mem, key=lambda k: (len(k), k))
        sc = {k: classify(canon(k))[0] for k in mem}
        new_rep = next((k for k in mem if sc[k] in counted), r0)
        if len(set(sc.values())) > 1:
            mixed.append((new_rep, sorted(set(sc.values()))))
        basis = next((dup_of[k][1] for k in mem if k in dup_of), "")
        for k in mem:
            rep_of[k] = (new_rep, basis)

    rows = []
    for d in raw:
        m = d.get("ads_metrics") or {}
        kw = d["keyword"]
        rep = rep_of.get(kw, (kw, ""))[0]
        rows.append({"keyword": kw, "canonical": canon(kw), "volume_total": m.get("volume_total") or 0,
                     "dup_of": rep if rep != kw else "", "dup_basis": rep_of[kw][1] if rep != kw else "",
                     "api_volume_trend_do_not_use": m.get("volume_trend", "")})
    live = [r for r in rows if not r["dup_of"]]
    dups = [r for r in rows if r["dup_of"]]

    groups = defaultdict(list)
    for r in live:
        groups[r["canonical"]].append(r)
    for c, g in groups.items():
        rep = max(g, key=lambda r: r["volume_total"])
        scope, matched, allb = classify(c)
        gsum = sum(r["volume_total"] for r in g)
        for r in g:
            r.update({"group_rows": len(g), "scope": scope, "brands_hit": "|".join(allb),
                      "brand_weight": round(1 / len(allb), 4) if allb and scope in ("brand", "multi_brand", "model_only",
                                                                                    "out_of_category") else "",
                      "matched_by": matched, "group_rep": 1 if r is rep else 0,
                      "group_volume_lo": rep["volume_total"], "group_volume_hi": gsum})
    for r in dups:   # 중복 행은 행으로 보존하되 어디에도 세지 않는다
        scope, matched, allb = classify(r["canonical"])
        r.update({"group_rows": "", "scope": scope, "brands_hit": "|".join(allb), "brand_weight": "",
                  "matched_by": matched, "group_rep": 0, "group_volume_lo": "", "group_volume_hi": ""})

    # ── 요청↔응답 대조 (과금은 요청한 키워드 수 — 계약 §1)
    requested, req_cost = [], []
    for p in a.requested:
        ks, cost = load_requested(p)
        requested += [k for k in ks if k]
        if cost is not None:
            req_cost.append(cost)
    requested = list(dict.fromkeys(requested))
    resp_kw = {r["keyword"] for r in rows}
    resp_canon = defaultdict(list)
    for r in rows:
        resp_canon[r["canonical"]].append(r["keyword"])
    req_canon = {canon(k) for k in requested}
    missing, renamed = [], []
    for k in requested:
        if k in resp_kw:
            continue
        if canon(k) in resp_canon:
            renamed.append((k, resp_canon[canon(k)]))
        else:
            missing.append(k)
    unrequested = [r for r in rows if requested and r["keyword"] not in requested and r["canonical"] not in req_canon]

    def collected(term_canon):
        """수집 상태 — 응답에 있음 / 요청했는데 미반환 / 요청 안 함(미수집)."""
        if term_canon in resp_canon:
            return "ok"
        if term_canon in req_canon:
            return NOT_RETURNED
        return NOT_COLLECTED

    # ── 절단 기록 (T7) — 호출 단위로 검사하고, 잘린 라운드가 키워드를 공급한 버킷을 모두 표시
    rounds_out, own_round, round_canon = [], set(), {}
    for spec in a.round:
        label, paths = spec.split("=", 1)
        kws, cut, calls = [], False, []
        for path in paths.split(","):
            limit = None
            if "@" in path:
                path, lim = path.rsplit("@", 1)
                limit = int(lim)
            got = load_keywords(path)
            kws += got
            c_cut = limit is not None and len(got) >= limit
            cut = cut or c_cut
            calls.append((path, len(got), limit, c_cut))
        key = next((b for b in brands if canon(label) == bcanon[b]), GENERIC)
        own_round.add(key)
        round_canon[label] = {canon(k) for k in kws if k}
        rounds_out.append((label, key, calls, cut))
    all_round_canon = set().union(*round_canon.values()) if round_canon else set()
    no_metric = len(all_round_canon - set(resp_canon))

    # ── 2단 대상 (카테고리 안 대표 행 · 중복 제외)
    targets = sorted({r["keyword"] for r in live if r["group_rep"] and r["scope"] in in_scopes
                      and r["volume_total"] >= a.series_gate})
    for r in rows:
        r["series_target"] = 1 if r["keyword"] in targets else 0
    if a.series_targets:
        json.dump(targets, open(a.series_targets, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ── 버킷 집계 (12개월 합 — volume_total)
    buckets = brands + [GENERIC, OUTSIDE]
    B = {k: defaultdict(float) for k in buckets}

    def bucket_share(r):
        if r["scope"] in ("brand", "multi_brand"):
            return [(b, r["brand_weight"]) for b in r["brands_hit"].split("|")]
        if r["scope"] == "generic":
            return [(GENERIC, 1.0)]
        if r["scope"] == "out_of_category":
            return [(OUTSIDE, 1.0)]
        return []

    fed = defaultdict(set)   # 버킷 → 이 버킷에 키워드를 공급한 라운드 라벨
    for r in live:
        for label, cs in round_canon.items():
            if r["canonical"] in cs:
                for b, _ in bucket_share(r):
                    fed[b].add(label)
        # 상한(hi) = 그룹 내 모든 행의 합 · 하한(lo) = 그룹 대표 행(최댓값)만 — 계약 T5 ②
        for b, w in bucket_share(r):
            B[b]["hi"] += r["volume_total"] * w
            if r["scope"] == "multi_brand":
                B[b]["multi_hi"] += r["volume_total"] * w
            if r["group_rep"]:
                B[b]["lo"] += r["volume_total"] * w
                B[b]["kw"] += 1
                if r["scope"] == "multi_brand":
                    B[b]["multi_lo"] += r["volume_total"] * w
                if r["series_target"]:
                    B[b]["series_kw"] += 1
        model_only_row = r["scope"] == "model_only" or r["matched_by"].startswith("model(카테고리")
        if r["brands_hit"] and (model_only_row or r["scope"] == "out_of_category"):
            bs = r["brands_hit"].split("|")
            for b in bs:
                w = 1 / len(bs)
                if model_only_row:
                    B[b]["mo_hi"] += r["volume_total"] * w
                    if r["group_rep"]:
                        B[b]["mo_lo"] += r["volume_total"] * w
                elif len(bs) >= 2 and r["group_rep"]:
                    B[b]["outcmp"] += r["group_volume_lo"] * w

    # 브랜드 단독 검색 볼륨(카테고리 밖) — 수집하지 않았으면 0이 아니라 «미수집»
    brand_only = {}
    for b in brands:
        st = collected(bcanon[b])
        brand_only[b] = max(r["volume_total"] for r in rows if r["canonical"] == bcanon[b]) if st == "ok" else st
    # 비교 검색('A vs B') — 응답에 두 브랜드 이상 걸린 행이 하나라도 있으면 수집된 것으로 본다
    any_cmp_seen = any(len(classify(r["canonical"])[2]) >= 2 for r in rows)
    req_cmp = any(len(classify(c)[2]) >= 2 for c in req_canon)
    cmp_global = "ok" if any_cmp_seen else (NOT_RETURNED if req_cmp else NOT_COLLECTED)
    cmp_status = {b: cmp_global for b in brands}
    outside_collected = any(v != NOT_COLLECTED for v in brand_only.values()) or B[OUTSIDE]["hi"] > 0

    # ── 2단: 시계열 점유 변화 (기본 모드 + 반대 모드 — 마지막 달 포함/제외 부호 대조)
    ref = end = end_alt = ""
    lag = ""
    excluded_missing = 0
    no_series = []
    if series:
        ref = max(max(s) for s in series.values())
        end = ref if a.include_last_month else shift(ref, 1)
        end_alt = shift(ref, 1) if a.include_last_month else ref
        qy, qm = int(a.queried_at[:4]), int(a.queried_at[5:7])
        lag = (qy - int(ref[:4])) * 12 + (qm - int(ref[5:7]))
        modes = {"": end, "alt_": end_alt}
        members = defaultdict(list)   # 대표 → 같은 검색의 중복 행 (시계열도 같은 검색이므로 대신 쓸 수 있다)
        for r in dups:
            members[r["dup_of"]].append(r["keyword"])
        for c, g in groups.items():
            # 그룹당 한 행의 시계열만 쓴다 (하한 기준) — 대표 행 → 그 중복 행 → 같은 그룹의 다른 행 순
            rep_row = next(r for r in g if r["group_rep"])
            pool = [rep_row["keyword"]] + members[rep_row["keyword"]] + [
                k for r in g if not r["group_rep"] for k in [r["keyword"]] + members[r["keyword"]]]
            key = next((k for k in pool if k in series), None)
            if key is None:
                if rep_row["series_target"]:
                    no_series.append(rep_row["keyword"])
                continue
            for r in [rep_row]:
                s = series[key]
                for pre, e in modes.items():
                    v1, m1 = acc(s, window(e, a.window))
                    v0, m0 = acc(s, window(shift(e, 12), a.window))
                    if m1 or m0:
                        if not pre:
                            excluded_missing += 1
                        continue
                    for b, w in bucket_share(r):
                        B[b][pre + "w1"] += v1 * w
                        B[b][pre + "w0"] += v0 * w

    inside = [b for b in buckets if b != OUTSIDE]
    # 점유율의 분모는 하한·상한 각자의 합이다 (하한 볼륨 ÷ 상한 분모처럼 섞지 않는다)
    denom = {k: sum(B[b][k] for b in inside) for k in ("lo", "hi")}
    denom_brand = {k: sum(B[b][k] for b in brands) for k in ("lo", "hi")}

    def ranks(k):
        return {b: 1 + sum(1 for x in brands if B[x][k] > B[b][k]) for b in brands}
    rank = {k: ranks(k) for k in ("lo", "hi")}
    flips = [b for b in brands if rank["lo"][b] != rank["hi"][b]]
    d = {pre: (sum(B[b][pre + "w1"] for b in inside), sum(B[b][pre + "w0"] for b in inside)) for pre in ("", "alt_")}

    def change(x, pre):
        s1, s0 = pct(x[pre + "w1"], d[pre][0]), pct(x[pre + "w0"], d[pre][1])
        if s1 == "" or s0 == "" or x[pre + "w0"] < a.gate:
            return "", "", ""
        return s1, s0, round(s1 - s0, 1)

    out = []
    for b in buckets:
        x = B[b]
        if not series:
            status = "시계열 미조회"
        elif b == OUTSIDE:
            status = "해당 없음(분모 제외)"
        elif x["w0"] < a.gate:
            status = "기저 미달"
        else:
            status = "ok"
        s1, s0, pp = change(x, "") if status == "ok" else ("", "", "")
        _, _, pp_alt = change(x, "alt_") if status == "ok" else ("", "", "")
        provisional = "잠정" if pp != "" and pp_alt != "" and sgn(pp) != sgn(pp_alt) else ""
        if b == OUTSIDE:
            tr, tr_rounds = "", ""
        else:
            cut_labels = sorted(lb for lb, key, _, cut in rounds_out if cut and (lb in fed[b] or key == b))
            tr = "yes" if cut_labels else ("no" if b in own_round else "미확장")
            tr_rounds = "|".join(cut_labels)
        is_brand = b in brands
        multi_lo = round(x["multi_lo"]) if (x["multi_hi"] or not is_brand or cmp_status[b] == "ok") else cmp_status[b]
        multi_hi = round(x["multi_hi"]) if (x["multi_hi"] or not is_brand or cmp_status[b] == "ok") else cmp_status[b]
        vol_lo = round(x["lo"]) if (b != OUTSIDE or outside_collected) else NOT_COLLECTED
        vol_hi = round(x["hi"]) if (b != OUTSIDE or outside_collected) else NOT_COLLECTED
        out.append({
            "bucket": b, "keywords": int(x["kw"]),
            "volume_12m_lo": vol_lo, "volume_12m_hi": vol_hi,
            "sov_pct_lo": "" if b == OUTSIDE else pct(x["lo"], denom["lo"]),
            "sov_pct_hi": "" if b == OUTSIDE else pct(x["hi"], denom["hi"]),
            "sov_pct_branded_only_lo": pct(x["lo"], denom_brand["lo"]) if is_brand else "",
            "sov_pct_branded_only_hi": pct(x["hi"], denom_brand["hi"]) if is_brand else "",
            "rank_lo": rank["lo"][b] if is_brand else "",
            "rank_hi": rank["hi"][b] if is_brand else "",
            "rank_flip": ("yes" if b in flips else "no") if is_brand else "",
            "multi_brand_volume_lo": multi_lo, "multi_brand_volume_hi": multi_hi,
            "model_only_volume_lo": round(x["mo_lo"]) if is_brand else "",
            "model_only_volume_hi": round(x["mo_hi"]) if is_brand else "",
            "brand_only_volume": brand_only[b] if is_brand else "",
            "outside_comparison_volume": (round(x["outcmp"]) if cmp_status[b] == "ok" else cmp_status[b]) if is_brand else "",
            "variant_list_truncated": tr, "truncated_rounds": tr_rounds,
            "series_keywords": int(x["series_kw"]),
            "series_basis": "대표 행(하한 기준)" if series else "",
            "window_volume_recent": round(x["w1"]) if series else "",
            "window_volume_prev_year": round(x["w0"]) if series else "",
            "share_recent_pct": s1, "share_prev_year_pct": s0, "share_change_pp": pp,
            "share_change_pp_other_mode": pp_alt, "share_change_provisional": provisional,
            "share_change_status": status,
            "data_ref_month": ref, "window_end": end,
            # 볼륨은 [실제 데이터]지만 버킷 배정·중복 판정은 규칙이다 — 행 전체의 라벨은 [추정]
            "label": "추정",
        })

    kcols = ["keyword", "canonical", "dup_of", "dup_basis", "group_rows", "group_rep", "volume_total",
             "group_volume_lo", "group_volume_hi", "scope", "brands_hit", "brand_weight", "matched_by",
             "series_target", "api_volume_trend_do_not_use"]
    rows.sort(key=lambda r: (-r["volume_total"], r["keyword"]))
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=kcols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    with open(a.brand_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)

    # ── 사람이 읽는 요약
    rng = lambda lo, hi: f"하한 {lo:,.0f} ~ 상한 {hi:,.0f}"
    two = lambda lo, hi: f"하한 기준 {lo}% · 상한 기준 {hi}%"   # 비율은 범위(~)가 아니라 두 값 — 하한 기준이 더 클 수 있다
    print(f"카테고리: {cat.get('name', '(이름 없음)')} · 키워드 {len(rows)}행 → 같은 검색의 중복 {len(dups)}행 제외 "
          f"→ canonical {len(groups)}그룹")
    if ref:
        print(f"데이터 기준월 {ref} / 조회일 {a.queried_at} / 지연 약 {lag}개월 · 비교 창 끝 {end} "
              f"({'마지막 달 포함' if a.include_last_month else '마지막 달 제외'})")
    else:
        print("데이터 기준월: 미확인 [데이터 공백] — ads_metrics에는 월 시계열이 없다. "
              "2단(all) 조회 후 기준월을 헤더에 적을 것")
    dvol = sum(r["volume_total"] for r in dups)
    din = sum(r["volume_total"] for r in dups if r["scope"] in in_scopes)
    nb = defaultdict(int)
    for r in dups:
        nb[r["dup_basis"]] += 1
    if dups:
        print(f"같은 검색의 중복 [추정]: {len(dups)}행 · {dvol:,}건 제외 ("
              + " · ".join(f"{k} {v}행" for k, v in nb.items())
              + f") — 그중 카테고리 안 {din:,}건. 제외하지 않으면 분모가 그만큼 부푼다(계약 T5 ①)")
        if mixed:
            print(f"  중복 묶음 중 판정이 갈린 묶음 {len(mixed)}개 — 카테고리 안 표현을 대표로 셌다 [가정]: "
                  + " · ".join(f"`{k}`({'/'.join(v)})" for k, v in mixed[:5]))
        ex = sorted(dups, key=lambda r: -r["volume_total"])[:5]
        print("  예: " + " · ".join(f"`{r['keyword']}` = `{r['dup_of']}`" for r in ex))
    else:
        print("같은 검색의 중복: 0행 — 지표·시계열이 완전히 같은 묶음이 없었다 [추정]. "
              "하한=상한이어도 이것이 «중복이 없다»는 증명은 아니다(판정 규칙의 범위만큼)")
    print(f"카테고리 안 12개월 볼륨: {rng(denom['lo'], denom['hi'])} [추정] — 하한=표기변형 그룹 최댓값 · "
          "상한=그룹 합. 같은 검색의 중복을 뺀 뒤의 값이다(계약 T5)")
    print(f"\n{'버킷':<18}{'키워드':>6}{'12개월 볼륨(하한~상한)':>26}{'SoV%(하/상)':>14}{'브랜드간%(하/상)':>16}"
          f"{'순위(하/상)':>10}{'점유변화pp':>11}  절단")
    for r in out:
        sv = f"{r['sov_pct_lo']}/{r['sov_pct_hi']}" if r["sov_pct_lo"] != "" else ""
        bv = f"{r['sov_pct_branded_only_lo']}/{r['sov_pct_branded_only_hi']}" if r["sov_pct_branded_only_lo"] != "" else ""
        rk = f"{r['rank_lo']}/{r['rank_hi']}{' ⚠' if r['rank_flip'] == 'yes' else ''}" if r["rank_lo"] != "" else ""
        vl = r["volume_12m_lo"]
        vol = f"{vl:>12,} ~{r['volume_12m_hi']:>12,}" if isinstance(vl, int) else f"{vl:>26}"
        chg = (f"{r['share_change_pp']}{' 잠정' if r['share_change_provisional'] else ''}"
               if r["share_change_pp"] != "" else r["share_change_status"])
        print(f"{r['bucket']:<18}{r['keywords']:>6}{vol}{sv:>14}{bv:>16}{rk:>10}{chg:>11}  {r['variant_list_truncated']}")
    print("점유율은 하한 볼륨 ÷ 하한 분모, 상한 볼륨 ÷ 상한 분모로 각각 계산했다 [추정]. 순위·판정은 하한 기준. "
          "비율은 범위가 아니라 두 값이다 — 하한 기준이 상한 기준보다 클 수 있다")
    if flips:
        print("⚠️ 순위 뒤집힘: " + ", ".join(f"{b} 하한 {rank['lo'][b]}위 → 상한 {rank['hi'][b]}위" for b in flips)
              + " — 표기변형이 같은 검색인지에 따라 순위가 달라진다. 순위를 단정하지 말고 두 값을 함께 적을 것")
    else:
        print("브랜드 순위는 하한·상한에서 같다")
    print(f"\n논브랜드 비중: {two(pct(B[GENERIC]['lo'], denom['lo']), pct(B[GENERIC]['hi'], denom['hi']))} "
          "[추정] — 브랜드 간 점유(브랜드간%)와 전체 점유(SoV%)를 구분해 인용할 것")
    ml, mh = sum(B[b]["multi_lo"] for b in brands), sum(B[b]["multi_hi"] for b in brands)
    if mh:
        print(f"다중 브랜드 비교 검색(카테고리 안) {rng(ml, mh)}건을 걸린 브랜드 수로 균등 분배함 [가정]")
    elif not any_cmp_seen:
        print(f"비교 검색: {NOT_COLLECTED} [데이터 공백] — 'A vs B' 검색을 1단에 넣지 않았다(0건이 아니라 모름). "
              "--probe-terms 목록을 1단에 더할 것")
    if outside_collected:
        print(f"카테고리 밖 브랜드 검색 {rng(B[OUTSIDE]['lo'], B[OUTSIDE]['hi'])}건은 분모에서 제외함 "
              "— 브랜드 단독·카테고리 단어 없는 비교 검색은 품목을 특정하지 않는다")
        print("  브랜드 단독: " + " · ".join(f"{b} {v:,}" if isinstance(v, int) else f"{b} {v}"
                                          for b, v in brand_only.items()))
    else:
        print(f"카테고리 밖 브랜드 검색: {NOT_COLLECTED} [데이터 공백] — 브랜드 단독 검색을 1단에 넣지 않았다"
              "(«0건»이 아니다). --probe-terms 목록을 1단에 더할 것")
    mo = sum(B[b]["mo_lo"] for b in brands)
    if mo:
        verb = "포함함 [가정]" if a.include_model_only else "분리함 — 점유에 넣지 않았다"
        print(f"모델명만(카테고리 단어 없음) {rng(mo, sum(B[b]['mo_hi'] for b in brands))}건 {verb}: "
              + " · ".join(f"{b} {round(B[b]['mo_lo']):,}" for b in brands if B[b]["mo_lo"])
              + " — 모델명은 품목을 특정하지 않는다(캡슐/머신 등)")
    for label, key, calls, cut in rounds_out:
        if cut:
            fed_b = sorted(b for b in buckets if label in fed[b])
            print(f"⚠️ 절단: {label} 라운드 — " + ", ".join(f"{pathlib.Path(p).name} 반환 {n} = limit {lim}"
                                                         for p, n, lim, c in calls if c)
                  + f". 이 라운드가 키워드를 공급한 버킷 전부({', '.join(fed_b) or key})를 절단으로 표시했다 — "
                  "과소일 수 있으며 수렴(목록 완결) 판정을 보류한다")
    if no_metric:
        print(f"⚠️ 라운드에서 찾았지만 지표가 없는 키워드 {no_metric}개 — keyword_info에 넣지 않았거나 미반환")
    if requested:
        print(f"\n요청↔응답 대조 (canonical): 요청 {len(requested)}개 → 과금 1 × {len(requested)} = {len(requested):,}크레딧"
              + (f" (응답 cost_detail 합 {sum(req_cost):,})" if req_cost else "")
              + f" · 응답 {len(rows)}행 — 과금은 요청 키워드 수이며 응답 행 수와 무관하다")
        print(f"  미반환 {len(missing)}개 [데이터 공백] — «0건»이 아니다 (과금은 됐다)"
              + (": " + ", ".join(missing[:15]) + (" …" if len(missing) > 15 else "") if missing else ""))
        if renamed:
            print(f"  표기가 바뀌어 반환 {len(renamed)}개: "
                  + ", ".join(f"{k} → {'/'.join(v)}" for k, v in renamed[:10]) + (" …" if len(renamed) > 10 else ""))
        if unrequested:
            uv = sum(r["volume_total"] for r in unrequested)
            top = sorted(unrequested, key=lambda r: -r["volume_total"])[:8]
            print(f"  요청하지 않은 행 {len(unrequested)}개 · {uv:,}건 (과금 안 됨 · 표기변형·오탈자 포함 — 분류에 그대로 들어갔다): "
                  + ", ".join(f"{r['keyword']}({r['scope']})" for r in top))
    else:
        print("\n요청↔응답 대조: 요청 목록 미제공(--requested) — 미반환 키워드를 확인할 수 없다 [데이터 공백]")
    if series:
        print("점유 변화는 그룹 대표 행 시계열(하한 기준)으로 계산했다 — 비대표 표기변형은 2단에서 조회하지 않아 "
              "상한 기준 월 시계열은 없다 [데이터 공백]")
        print(f"비교 창 결측으로 계산 불가: {excluded_missing}그룹 (0으로 합산하지 않음)")
        if no_series:
            print(f"2단 대상인데 시계열 응답이 없는 그룹 {len(no_series)}개 [데이터 공백] — 점유 변화 분모에서 빠졌다"
                  f"(미반환이거나 다른 대상 목록으로 조회함): " + ", ".join(no_series[:8]) + (" …" if len(no_series) > 8 else ""))
        print(f"기저 게이트 {a.gate:,} (전년 창 볼륨) 미달 버킷은 점유 변화를 내지 않음 [가정]")
        prov = [r["bucket"] for r in out if r["share_change_provisional"]]
        other = "마지막 달 제외" if a.include_last_month else "마지막 달 포함"
        print(f"반대 모드({other}, 창 끝 {end_alt})와 부호가 다른 버킷: "
              + (", ".join(prov) + " → «잠정»" if prov else "없음"))
    if a.series_targets:
        cov = sum(r["volume_total"] for r in live if r["series_target"])
        print(f"2단(all) 조회 대상 {len(targets)}개 → {len(targets) * 10:,}크레딧 "
              f"(카테고리 안 볼륨의 하한 기준 {pct(cov, denom['lo'])}% · 상한 기준 {pct(cov, denom['hi'])}% 커버) "
              f"→ {a.series_targets}")
    print(f"\n→ {a.out} · {a.brand_out}")


if __name__ == "__main__":
    main()
