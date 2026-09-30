#!/usr/bin/env python3
"""수집 단계 준비 — 죽은 시드 판정 · 확장 반환 검토 시트 · 2단 대상 선정 (SKILL.md STEP 2·3.5·5).

이 스크립트는 validate-product-ideas 고유 스크립트다(공유 사본이 아니다). API를 부르지 않는다(0크레딧).

다섯 하위 명령:

  seeds   — 1단(ads_metrics) 응답으로 시드를 «살아 있음 / 0건 / 미반환»으로 가른다 (STEP 2)
            요청 문자열과 응답 문자열은 다를 수 있다(`제로슈가 아이스크림` → `제로 슈가 아이스크림`).
            대조는 canonical(층 사전 `rules.변형치환` 적용)로 한다 — 문자열 그대로 대조하면 살아 있는
            시드를 죽었다고 판정한다. 죽은 시드도 1단에서 과금됐다(요청 키워드 수 기준).
      python3 scripts/prepare_stages.py seeds --seeds seeds_by_layer.json \\
          --metrics stage1_seeds.json --layers layers.json -o seed_check.json

  universe — 확장 반환 중 1단 시드 응답에 없는 키워드를 유니버스 볼륨 요청 목록으로 만든다 (STEP 3 끝)
            대조는 canonical — 이미 시드로 볼륨을 받은 키워드에 다시 1크레딧을 내지 않는다.
      python3 scripts/prepare_stages.py universe --seed-metrics stage1_seeds.json --layers layers.json \
          --expansion if_저당아이스크림.json --expansion if_다이어트아이스크림.json -o universe_request.json

  merge   — 1단 시드 응답 + 유니버스 응답을 하나로 합친다 → metrics_stage1.json (STEP 3 끝)
            metrics_stage1.json = «시드 응답(stage1_seeds.json) + 유니버스 응답(stage1_universe.json)». 같은 키워드는
            한 번만(먼저 준 파일 우선). STEP 3 coverage_curve · STEP 3.5 review · STEP 5 build_universe가 이 파일을 읽는다.
      python3 scripts/prepare_stages.py merge stage1_seeds.json stage1_universe.json -o metrics_stage1.json

  review  — intent_finder 반환을 검토 시트로 펼친다 (STEP 3.5 · 2단 전)
            층 사전의 `exclude`(동음이의·무관 검색 패턴)에 걸린 반환과, 반환 대부분이 제외된 시드
            (동음이의 의심)를 표시한다. 프록시 시드(`proxies`) 중 확장하지 않은 것은 «반환으로 검토 불가».
            검토 결과는 사람이 층 사전에 적는다: 무관하면 `exclude`에 패턴+사유, 가설과 맞으면 `reviewed`.
      python3 scripts/prepare_stages.py review --layers layers.json \\
          --expansion if_저당아이스크림.json --expansion if_다이어트아이스크림.json \\
          --metrics metrics_stage1.json -o proxy_review.csv

  stage2  — 유니버스(build_universe.py 산출, 1단 지표)에서 2단(`all`) 대상을 고른다 (STEP 5)
            ① 제외 패턴·같은 검색의 중복(dup_of)·볼륨 0/미반환 행은 뺀다 ② 층마다 canonical 그룹
            대표(볼륨 최대 행)만 ③ volume_total ≥ --min-volume ④ 층당 최대 --max-per-layer ·
            최소 --min-per-layer(미달이면 게이트 아래에서 볼륨순으로 채우고 [가정]으로 표시)
            ⑤ `(미분류)`·브랜드 무층 행은 2단에 넣지 않고 건수·볼륨 비중을 보고한다.
      python3 scripts/prepare_stages.py stage2 --universe universe_pre.csv --layers layers.json \\
          -o stage2_keywords.json

층 사전(layers.json) 추가 키 — build_universe.py는 이 키들을 읽지 않는다(무시한다):
  "exclude":  [{"pattern": 정규식(canonical 대상), "reason": 사유}]   — 집계·2단에서 뺀다
  "proxies":  [시드 …]    — 가설 제품 대신 쓴 프록시 시드(신조어 가설 · 살아 있는 동음이의 후보 제품명)
  "reviewed": [키워드 …]  — 검토에서 가설과 맞다고 확인한 키워드
"""
import argparse, csv, json, pathlib, re, sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon as _canon  # noqa: E402  변형치환은 띄어쓰기를 지우기 전에 적용한다

META_KEYS = {"hypothesis", "gl", "_note", "_eval_note"}


# ── 공통 (idea_signals.py도 import 한다) ─────────────────────────────────
def load_data(path):
    o = json.load(open(path, encoding="utf-8"))
    if isinstance(o, dict):
        o = o.get("data", [])
    return o


class Layers:
    """층 사전 — themes.json 스키마 + exclude · proxies · reviewed."""

    def __init__(self, path):
        cfg = json.load(open(path, encoding="utf-8")) if path else {}
        self.rules = (cfg.get("rules") or {}).get("변형치환", {})
        self.themes = [(t["name"], re.compile(t["pattern"])) for t in cfg.get("themes", [])]
        self.brands = cfg.get("brands", [])
        self.no_match = cfg.get("no_match", {})
        self.excl = [(re.compile(e["pattern"]), e.get("reason", "")) for e in cfg.get("exclude", [])]
        self.proxies = {self.canon(k) for k in cfg.get("proxies", [])}
        self.reviewed = {self.canon(k) for k in cfg.get("reviewed", [])}

    def canon(self, kw):
        return _canon(kw, self.rules)

    def layer(self, kw):
        c = self.canon(kw)
        hit = [n for n, p in self.themes if p.search(c)]
        if hit:
            return hit[0]
        brand = any(self.canon(b) in c for b in self.brands)
        return self.no_match.get("with_brand" if brand else "without_brand", "(미분류)") or "(미분류)"

    def excluded(self, kw):
        """제외 패턴에 걸리면 사유, 아니면 None."""
        c = self.canon(kw)
        for p, why in self.excl:
            if p.search(c):
                return why or p.pattern
        return None

    def layer_names(self):
        return [n for n, _ in self.themes]


def seed_of(path, obj):
    """확장 파일의 시드 — 응답 request_detail이 있으면 그것, 없으면 파일명(if_<시드>.json)."""
    if isinstance(obj, dict):
        kws = (obj.get("request_detail") or {}).get("keywords")
        if kws:
            return kws[0]
    stem = pathlib.Path(path).stem
    return re.sub(r"^(probe_)?if_", "", stem)


def expansion_map(paths, layers):
    """canonical 키워드 → 그 키워드를 반환한 시드 canonical 집합."""
    out = defaultdict(set)
    for p in paths:
        path = p.rpartition("@")[0] or p
        obj = json.load(open(path, encoding="utf-8"))
        s = layers.canon(seed_of(path, obj))
        for kw in load_data(path):
            k = kw if isinstance(kw, str) else (kw or {}).get("keyword")
            if k:
                out[layers.canon(k)].add(s)
    return out


def load_seeds(path):
    """seeds.json(목록) 또는 seeds_by_layer.json(층 → 목록) → [(시드, 선언 층)]"""
    o = json.load(open(path, encoding="utf-8"))
    if isinstance(o, list):
        return [(s, "") for s in o]
    return [(s, k) for k, v in o.items() if k not in META_KEYS and isinstance(v, list) for s in v]


# ── seeds ──────────────────────────────────────────────────────────────
def cmd_universe(a):
    L = Layers(a.layers)
    have = {L.canon(d["keyword"]) for d in load_data(a.seed_metrics) if isinstance(d, dict) and d.get("keyword")}
    seen, req, n_ret = set(have), [], 0
    for p in a.expansion:
        path = p.rpartition("@")[0] or p
        for kw in load_data(path):
            k = kw if isinstance(kw, str) else (kw or {}).get("keyword")
            if not k:
                continue
            n_ret += 1
            c = L.canon(k)
            if c not in seen:
                seen.add(c); req.append(k)
    json.dump(req, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"확장 반환 {n_ret}행 · 시드 응답과 canonical이 같은 {n_ret - len(req)}행 제외(이미 볼륨 있음 · 반환 간 중복 포함) "
          f"→ 유니버스 요청 N = {len(req)}개")
    print(f"유니버스 크레딧 = 1 × {len(req)} = {len(req)}  (keyword_info · data_type=ads_metrics → stage1_universe.json)")
    print(f"→ {a.out}")


def cmd_merge(a):
    out, seen, per = [], set(), []
    for path in a.files:
        n = 0
        for d in load_data(path):
            if isinstance(d, dict) and d.get("keyword") and d["keyword"] not in seen:
                seen.add(d["keyword"]); out.append(d); n += 1
        per.append(f"{pathlib.Path(path).name} {n}행")
    json.dump({"data": out}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"1단 지표 병합: {' + '.join(per)} = {len(out)}행 (같은 키워드는 먼저 준 파일 행) → {a.out}")


def cmd_seeds(a):
    L = Layers(a.layers)
    rows = [d for d in load_data(a.metrics) if isinstance(d, dict)]
    by_canon = defaultdict(list)
    for d in rows:
        by_canon[L.canon(d["keyword"])].append(d)
    alive, dead, respelled = [], [], []
    for s, declared in load_seeds(a.seeds):
        hits = by_canon.get(L.canon(s), [])
        vols = [(h.get("ads_metrics") or {}).get("volume_total") for h in hits]
        layer = L.layer(s)
        if not hits:
            status = "미반환"
        elif not any(v for v in vols):
            status = "0건"
        else:
            status = "살아 있음"
        rec = {"seed": s, "declared_layer": declared, "layer": layer, "status": status,
               "returned_as": [h["keyword"] for h in hits], "volume_total": max((v or 0) for v in vols) if vols else None}
        if hits and s not in rec["returned_as"]:
            respelled.append(rec)
        (alive if status == "살아 있음" else dead).append(rec)
    per_layer = defaultdict(lambda: [0, 0])
    for r in alive + dead:
        per_layer[r["layer"]][0] += 1
        per_layer[r["layer"]][1] += r["status"] != "살아 있음"
    silent = sorted(l for l, (n, d) in per_layer.items() if n and n == d)
    out = {"alive": [r["seed"] for r in alive], "dead": dead, "respelled": respelled,
           "silent_layers": silent, "requested": len(alive) + len(dead)}
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"시드 {out['requested']}개 요청(1단 과금 {out['requested']}크레딧 — 죽은 시드도 과금) → "
          f"살아 있음 {len(alive)} · 죽음 {len(dead)} [데이터 공백]")
    for r in dead:
        print(f"  ✗ {r['seed']} ({r['layer']}) — {r['status']}")
    for r in respelled:
        print(f"  ↺ {r['seed']} → 응답 표기 {r['returned_as']} (canonical 대조로 살아 있음 판정)")
    for l in silent:
        print(f"  ⚠️ {l} 층 시드 전멸 — «검색 신호 없음 [데이터 공백]». «시장 없음»이 아니다. 프록시를 하나 더 시도한다")
    print(f"확장(intent_finder)에는 살아 있는 시드 {len(alive)}개만 넣는다 → {a.out}")


# ── review ─────────────────────────────────────────────────────────────
def cmd_review(a):
    L = Layers(a.layers)
    vol = {}
    if a.metrics:
        for d in load_data(a.metrics):
            if isinstance(d, dict):
                vol[L.canon(d["keyword"])] = (d.get("ads_metrics") or {}).get("volume_total")
    rows, summary, expanded = [], [], set()
    for p in a.expansion:
        path = p.rpartition("@")[0] or p
        obj = json.load(open(path, encoding="utf-8"))
        seed = seed_of(path, obj)
        sc = L.canon(seed); expanded.add(sc)
        n = nx = 0; vx = vt = 0
        for kw in load_data(path):
            k = kw if isinstance(kw, str) else (kw or {}).get("keyword")
            if not k:
                continue
            c = L.canon(k)
            why = L.excluded(k)
            v = vol.get(c)
            if c != sc:
                n += 1; nx += bool(why)
                vt += v or 0; vx += (v or 0) if why else 0
            status = f"제외 — {why}" if why else ("검토 완료(가설과 맞음)" if c in L.reviewed else "미검토")
            rows.append({"seed": seed, "keyword": k, "canonical": c, "volume_total": v if v is not None else "",
                         "layer": L.layer(k), "proxy_seed": sc in L.proxies, "status": status})
        share = nx / n if n else None
        suspect = bool(share is not None and share >= a.suspect_share)
        summary.append((seed, n, nx, share, suspect, L.excluded(seed)))
    for pc in sorted(L.proxies - expanded):
        rows.append({"seed": pc, "keyword": "", "canonical": pc, "volume_total": vol.get(pc, ""),
                     "layer": L.layer(pc), "proxy_seed": True,
                     "status": "확장 없음 — 반환으로 검토 불가(미검토 프록시)"})
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["seed", "keyword", "canonical", "volume_total", "layer",
                                          "proxy_seed", "status"])
        w.writeheader(); w.writerows(rows)
    print("시드별 확장 반환 검토 (0크레딧) — 시드 자신은 비율에서 뺀다")
    for seed, n, nx, share, suspect, self_ex in summary:
        tag = " ⚠️ 동음이의 의심 — 시드 자체를 exclude할지 검토" if suspect and not self_ex else ""
        tag = f" (시드 제외됨 — {self_ex})" if self_ex else tag
        print(f"  {seed}: 반환 {n} · 제외 패턴 {nx}"
              + (f" ({share:.0%})" if share is not None else "") + tag)
    unrev = [r for r in rows if r["status"].startswith(("미검토", "확장 없음"))]
    print(f"미검토 {len(unrev)}행 — 무관하면 layers.json exclude(패턴+사유), 가설과 맞으면 reviewed에 적는다")
    np = sorted(L.proxies - expanded)
    if np:
        print(f"⚠️ 확장하지 않은 프록시 시드 {len(np)}개 — 반환으로 검토할 수 없다: {', '.join(np)}")
    print(f"→ {a.out}")


# ── stage2 ─────────────────────────────────────────────────────────────
def cmd_stage2(a):
    L = Layers(a.layers)
    uni = list(csv.DictReader(open(a.universe, encoding="utf-8-sig")))
    layer_names = set(L.layer_names())

    def v(r):
        try:
            return float(r.get("volume_total") or 0)
        except ValueError:
            return 0.0
    total = sum(v(r) for r in uni) or 1
    skipped = defaultdict(lambda: [0, 0.0])
    groups = defaultdict(list)
    for r in uni:
        why = L.excluded(r["keyword"])
        if why:
            skipped["제외 패턴"][0] += 1; skipped["제외 패턴"][1] += v(r); continue
        if r.get("dup_of"):
            skipped["같은 검색의 중복"][0] += 1; skipped["같은 검색의 중복"][1] += v(r); continue
        if v(r) <= 0:
            skipped["볼륨 0·미반환(죽은 행)"][0] += 1; continue
        layer = r.get("theme_primary") or "(미분류)"
        if layer not in layer_names:
            skipped[f"층 아님: {layer}"][0] += 1; skipped[f"층 아님: {layer}"][1] += v(r); continue
        groups[(layer, r["canonical"])].append(r)
    by_layer = defaultdict(list)
    for (layer, _), rs in groups.items():
        by_layer[layer].append(max(rs, key=v))
    sel, log = [], []
    for layer in L.layer_names():
        rs = sorted(by_layer.get(layer, []), key=lambda r: -v(r))
        pick = [r for r in rs if v(r) >= a.min_volume][:a.max_per_layer]
        fill = [r for r in rs if r not in pick][:max(0, a.min_per_layer - len(pick))]
        sel += [r["keyword"] for r in pick + fill]
        log.append(f"  {layer:<10} {len(pick) + len(fill)}개 (게이트 통과 {len(pick)}"
                   + (f" + 최소 {a.min_per_layer} 채움 {len(fill)} [가정]" if fill else "") + f") / 그룹 {len(rs)}")
        if not rs:
            log.append(f"  {layer:<10} 2단 대상 없음 — 볼륨 있는 그룹 0 · 카드에 «검색 신호 없음 [데이터 공백]»")
    pathlib.Path(a.out).write_text(json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"2단 대상 M = {len(sel)} → all 예상 {len(sel) * 10:,}크레딧 (10 × {len(sel)})")
    print("\n".join(log))
    for k, (n, vv) in skipped.items():
        print(f"  뺀 행 — {k}: {n}행" + (f" · 볼륨 비중 {vv / total:.1%}" if vv else ""))
    un = skipped.get("층 아님: (미분류)", [0, 0.0])
    if un[1] / total >= a.unclassified_warn:
        print(f"⚠️ (미분류) 볼륨 비중 {un[1] / total:.1%} — 층 사전 패턴을 보강한 뒤 다시 고른다")
    if L.proxies:
        chosen = {L.canon(k) for k in sel}
        unrev = sorted(c for c in chosen & L.proxies if c not in L.reviewed)
        if unrev:
            print(f"⚠️ 미검토 프록시가 2단에 들어간다: {', '.join(unrev)} — review 후 reviewed/exclude에 적을 것")
    print(f"→ {a.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("seeds", help="1단 응답으로 죽은 시드 판정 (canonical 대조)")
    s.add_argument("--seeds", required=True, help="seeds.json(목록) 또는 seeds_by_layer.json")
    s.add_argument("--metrics", required=True, help="1단 ads_metrics 응답")
    s.add_argument("--layers", help="층 사전 — 변형치환·층 배정")
    s.add_argument("-o", "--out", default="seed_check.json")
    u = sp.add_parser("universe", help="확장 반환 → 유니버스 볼륨 요청 목록 (시드 응답에 있는 것은 뺀다)")
    u.add_argument("--seed-metrics", required=True, help="1단 시드 ads_metrics 응답 (stage1_seeds.json)")
    u.add_argument("--layers", help="층 사전 — 변형치환(canonical 대조)")
    u.add_argument("--expansion", action="append", default=[], help="intent_finder 응답(@limit 허용)")
    u.add_argument("-o", "--out", default="universe_request.json")
    m = sp.add_parser("merge", help="1단 시드 응답 + 유니버스 응답 → metrics_stage1.json")
    m.add_argument("files", nargs="+", help="keyword_info(ads_metrics) 응답 JSON — 앞 파일 우선")
    m.add_argument("-o", "--out", default="metrics_stage1.json")
    r = sp.add_parser("review", help="확장 반환 검토 시트 (동음이의·무관 검색)")
    r.add_argument("--layers", required=True)
    r.add_argument("--expansion", action="append", default=[], help="intent_finder 응답(@limit 허용)")
    r.add_argument("--metrics", help="유니버스 ads_metrics 응답 — 볼륨 열")
    r.add_argument("--suspect-share", type=float, default=0.5,
                   help="시드 외 반환 중 제외 패턴 비율이 이 이상이면 동음이의 의심 (기본 0.5) [가정]")
    r.add_argument("-o", "--out", default="proxy_review.csv")
    t = sp.add_parser("stage2", help="2단(all) 대상 선정")
    t.add_argument("--universe", required=True, help="build_universe.py 산출(1단 지표)")
    t.add_argument("--layers", required=True)
    t.add_argument("--min-volume", type=int, default=1200, help="12개월 합 게이트 (기본 1200) [가정]")
    t.add_argument("--max-per-layer", type=int, default=15)
    t.add_argument("--min-per-layer", type=int, default=3)
    t.add_argument("--unclassified-warn", type=float, default=0.2, help="(미분류) 볼륨 비중 경고 임계")
    t.add_argument("-o", "--out", default="stage2_keywords.json")
    a = ap.parse_args()
    {"seeds": cmd_seeds, "universe": cmd_universe, "merge": cmd_merge, "review": cmd_review,
     "stage2": cmd_stage2}[a.cmd](a)


if __name__ == "__main__":
    main()
