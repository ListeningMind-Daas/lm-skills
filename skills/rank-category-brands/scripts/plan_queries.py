#!/usr/bin/env python3
"""1단 요청 목록 생성(combos)과 2단 대상 선별(stage2) — rank-category-brands STEP 2 · 3 (이 스킬 고유).

  python3 scripts/plan_queries.py combos --config brands.json -o stage1_keywords.json
  python3 scripts/plan_queries.py stage2 stage1_ads_metrics.json --config brands.json \
      --requested stage1_keywords.json --min-volume 600 -o stage2_keywords.json

combos  — 브랜드(정식명 + 별칭) × 분야어 · 모델/라인(variants) · 브랜드명 단독(probe_alone) · 논브랜드 분야어
          (category_terms)를 만들고 canonical 로 중복을 뺀다. 분야어가 이미 든 별칭(«코지마 안마의자»)은 그대로 한 번
          넣고, 그 별칭에 이미 든 분야어는 다시 붙이지 않는다(«코지마 안마의자 안마의자» 금지 · «코지마 안마의자 렌탈»은 유지).
          크레딧 `1 × K1` 을 출력한다.
stage2  — 1단(ads_metrics) 응답에서 순위·점유에 들어가는 행(genre · model · compare · category)만,
          같은 검색의 중복(지표 일치 · series_dedup)은 대표 1행만, 12개월 볼륨 `--min-volume` 이상만 남긴다.
          버킷마다 가장 큰 행을 먼저 넣고 나머지는 볼륨 순 — `--max-k2` 로 상한을 둔다. 크레딧 `10 × K2` 를 출력한다.
          2단 조회 전에 «별칭 누락 의심»(사전 브랜드와 닮은 논브랜드 행)을 출력한다 — 사전을 고친 뒤 2단을 부른다.
"""
import argparse, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, collapse_identical, dedup_map, report_collapse  # noqa: E402
from rank_common import COUNTED, alias_gap_suspects, classify, load_config, load_rows, utf8_console, vol_total  # noqa: E402


def combos(a):
    cfg = load_config(a.config)
    out, seen = [], set()

    def add(q):
        k = canon(q, cfg["rules"])
        if k and k not in seen:
            seen.add(k); out.append(q)

    for b in cfg["brands"]:
        gq = set(cfg["genre_qualified"][b])
        for al in cfg["aliases"][b]:
            if al in gq:
                add(al)                     # 분야어가 이미 들었다 — 그대로 한 번(분야 한정 검색 · genre)
            has = set(cfg["alias_terms"][b].get(al, ()))
            for t in cfg["genre_terms"]:
                if t not in has:            # 별칭에 이미 든 분야어는 다시 붙이지 않는다
                    add(f"{al} {t}")
        for v in cfg["variants"][b]:
            add(v)
        if cfg["probe_alone"]:
            for al in cfg["aliases"][b]:
                if al not in gq:
                    add(al)
    for t in cfg["genre_terms"] + cfg["category_terms"]:
        add(t)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    gq = [a for b in cfg["brands"] for a in cfg["genre_qualified"][b]]
    if gq:
        print(f"분야어가 든 별칭 {len(gq)}개 — 그대로 조회(든 분야어는 다시 안 붙임) · 단독이 아니라 분야 한정 검색(genre)으로 센다: "
              + ", ".join(gq[:8]) + (" …" if len(gq) > 8 else ""))
    print(f"1단 요청 {len(out)}개 (canonical 중복 제외) → {a.out}")
    print(f"크레딧(1단 ads_metrics): 1 × K1 = 1 × {len(out)} = {len(out)}")
    if len(out) > 1000:
        print("⚠️ 1회 상한 1,000개를 넘는다 — 나눠 호출하고 응답을 한 파일로 합칠 것(계약 §1)")


def stage2(a):
    cfg = load_config(a.config)
    rows = load_rows(a.metrics)
    n_raw = len(rows)
    rows, merged, conflicts = collapse_identical(rows)
    report_collapse(merged, conflicts, n_raw)
    if a.requested:
        req = json.load(open(a.requested, encoding="utf-8"))
        got = {canon(r["keyword"], cfg["rules"]) for r in rows}
        miss = [q for q in req if canon(q, cfg["rules"]) not in got]
        print(f"요청 {len(req)} · 응답 {len(rows)}행 · 미반환 {len(miss)}개 [데이터 공백] — 0 건이 아니다(과금은 요청 수)"
              + (f": {', '.join(miss[:8])}" + (" …" if len(miss) > 8 else "") if miss else ""))
    dm = dedup_map(rows, cfg["rules"])
    cand, by_bucket_all = [], {}
    for r in rows:
        bucket, kind = classify(r["keyword"], cfg)
        if kind not in COUNTED or dm[r["keyword"]]["rep"] != r["keyword"]:
            continue
        v = vol_total(r)
        by_bucket_all[bucket] = by_bucket_all.get(bucket, 0) + v
        if v >= a.min_volume:
            cand.append((bucket, v, r["keyword"]))
    cand.sort(key=lambda x: (-x[1], x[2]))
    first, rest, seen_b = [], [], set()
    for bucket, v, kw in cand:
        (rest if bucket in seen_b else first).append((bucket, v, kw)); seen_b.add(bucket)
    picked = first + rest
    if a.max_k2:
        picked = picked[: a.max_k2]
    sel = {}
    for bucket, v, _ in picked:
        sel[bucket] = sel.get(bucket, 0) + v
    out = [kw for _, _, kw in picked]
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"2단 대상 {len(out)}개 (게이트 12개월 {a.min_volume:,}건 이상 · 같은 검색의 중복 제외) → {a.out}")
    print("버킷별 2단 커버리지(1단 12개월 볼륨 기준) [추정]:")
    for b in cfg["brands"] + ["_비교(복수 브랜드)", "_논브랜드"]:
        tot = by_bucket_all.get(b, 0)
        cov = sel.get(b, 0) / tot if tot else None
        flag = "" if cov is None or cov >= a.min_coverage else f"  ⚠️ {a.min_coverage:.0%} 미만 — 게이트를 낮추거나 과소집계를 [데이터 공백]으로 적을 것"
        print(f"  {b}: 1단 {tot:,} · 2단 {sel.get(b, 0):,} · " + ("1단 볼륨 0 — 별칭·분야어 확인" if cov is None else f"{cov:.1%}") + flag)
    sus = alias_gap_suspects(((r["keyword"], vol_total(r)) for r in rows), cfg, a.min_volume)
    if sus:
        nb_tot = by_bucket_all.get("_논브랜드", 0)
        sv = sum(x["volume_12m"] for x in sus)
        print(f"⚠️ 별칭 누락 의심 {len(sus)}행 · 12개월 {sv:,}건"
              + (f"(논브랜드 1단 볼륨의 {sv / nb_tot:.1%})" if nb_tot else "")
              + " — 사전 브랜드와 닮은 검색이 논브랜드로 잡혔다. 별칭 · variants 를 보강하고 다시 돌린 뒤 2단을 부를 것:")
        for x in sus[:10]:
            print(f"  {x['keyword']} ({x['volume_12m']:,}) → {x['near_brand']}?")
    print(f"크레딧(2단 all): 10 × K2 = 10 × {len(out)} = {10 * len(out)}")


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("combos"); c.add_argument("--config", required=True); c.add_argument("-o", "--out", required=True)
    s = sp.add_parser("stage2"); s.add_argument("metrics"); s.add_argument("--config", required=True)
    s.add_argument("--requested"); s.add_argument("--min-volume", type=int, default=600,
                                                  help="2단 진입 12개월 볼륨 하한 (기본 600 = 월 50건) [가정]")
    s.add_argument("--min-coverage", type=float, default=0.9); s.add_argument("--max-k2", type=int)
    s.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    (combos if a.cmd == "combos" else stage2)(a)


if __name__ == "__main__":
    main()
