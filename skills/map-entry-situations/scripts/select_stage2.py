#!/usr/bin/env python3
"""2단 조회 대상 선정 — 시계열(`keyword_info` all, 10/kw)을 살 검색어만 규칙으로 고른다 (이 스킬 고유 · 0크레딧).

전량에는 ads_metrics(1/kw)만 걸었다. 시계열은 CEP 추세·계절성 판정에 쓰는 검색어에만 산다:

  B      = 카테고리 기준선(sense.keyword 그룹 대표) — 게이트와 무관하게 항상
  M_main = 상황(CEP) 그룹 대표 ∧ 카테고리 결속(anchor_hit=Y) ∧ volume_total ≥ --gate-12m  [가정 12,000]
  M_tail = 상황 그룹 대표 ∧ anchor_hit=Y ∧ --tail-min ≤ volume_total < --gate-12m 중
           CEP마다 볼륨 순 상위 --tail-per-cep개   [가정 1,200 · 5] — 작은 CEP도 추세를 합산으로 볼 수 있게
  크레딧 = 10 × (B + M_main + M_tail)

그룹 = canonical(같은 검색 중복 dup_of · 경계 밖 · 미반환 제외), 대표 = 그룹 내 volume_total 최댓값 행.
표기변형은 대표 하나만 산다 — 같은 수요에 10크레딧을 두 번 내지 않는다. 미분류 · 비상황 · 시드 앞 검색 중
카테고리어가 없는 것(상황 자체의 크기)은 2단 대상이 아니다.

    python3 select_stage2.py --pool pool.csv --metrics metrics_ads.json --sense sense.json \
        --themes themes.json -o stage2_keywords.json
"""
import argparse, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cep_lib import Lexicon, build_universe, data_rows, load_json, load_sense, read_pool, utf8_console  # noqa: E402


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="2단 조회 대상 선정 (0크레딧)")
    ap.add_argument("--pool", required=True); ap.add_argument("--metrics", required=True)
    ap.add_argument("--sense", required=True); ap.add_argument("--themes", required=True)
    ap.add_argument("--gate-12m", type=int, default=12000)
    ap.add_argument("--tail-min", type=int, default=1200)
    ap.add_argument("--tail-per-cep", type=int, default=5)
    ap.add_argument("--allow-unconfirmed", action="store_true")
    ap.add_argument("-o", "--out", default="stage2_keywords.json")
    a = ap.parse_args()

    sense = load_sense(a.sense, a.allow_unconfirmed)
    lex = Lexicon(load_json(a.themes), sense)
    rows, groups, _ = build_universe(read_pool(a.pool), data_rows(load_json(a.metrics)), lex)
    reps = {c: next(r for r in g if r["keyword"] == r["canonical_rep"]) for c, g in groups.items()}

    base = reps.get(lex.seed)
    picked, why = [], {}
    if base:
        picked.append(base["keyword"]); why[base["keyword"]] = "B 기준선"
    main_, tail = [], {}
    for c, r in reps.items():
        if r["kind"] != "상황" or r["anchor_hit"] != "Y" or r["keyword"] in why:
            continue
        v = r["volume_total"]
        if v >= a.gate_12m:
            main_.append(r)
        elif v >= a.tail_min:
            tail.setdefault(r["cep"], []).append(r)
    for r in sorted(main_, key=lambda x: -x["volume_total"]):
        picked.append(r["keyword"]); why[r["keyword"]] = "M_main"
    n_tail = 0
    for cep, rs in sorted(tail.items()):
        for r in sorted(rs, key=lambda x: -x["volume_total"])[:a.tail_per_cep]:
            picked.append(r["keyword"]); why[r["keyword"]] = "M_tail"; n_tail += 1

    credit = 10 * len(picked)
    json.dump({"keywords": picked, "why": why, "credit": credit,
               "rule": {"gate_12m": a.gate_12m, "tail_min": a.tail_min, "tail_per_cep": a.tail_per_cep}},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    skipped = sum(1 for r in reps.values() if r["kind"] == "상황" and r["keyword"] not in why)
    print(f"2단 대상 {len(picked)}개 = B {1 if base else 0} + M_main {len(main_)} + M_tail {n_tail} "
          f"→ 크레딧 10 × {len(picked)} = {credit:,} (keyword_info data_type=all)")
    if not base:
        print(f"⚠️ 기준선 «{sense['keyword']}» 그룹이 응답에 없다 — 카테고리 기준선 추세는 [데이터 공백]")
    print(f"상황 그룹 중 시계열을 사지 않는 그룹 {skipped}개 — 추세 열이 «시계열 없음»으로 남는다 [데이터 공백]")
    print(f"→ {a.out}")


if __name__ == "__main__":
    main()
