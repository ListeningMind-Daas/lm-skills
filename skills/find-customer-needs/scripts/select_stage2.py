#!/usr/bin/env python3
"""2단 조회 대상 M 선정 (find-customer-needs STEP 5 · 이 스킬 고유).

M은 규칙으로 정한다 — 손으로 고르면 재현되지 않는다. 그룹 = canonical(중복 행 `dup_of`·경계 밖 제외),
대표 = 그룹 내 volume_total 최댓값 행.

  B      = 카테고리 기준선 대표 (--baseline, 기본 --category) — 게이트와 무관하게 항상
  M_main = 축 분류 그룹 대표 ∧ volume_total ≥ --gate-annual (기본 12,000)
  M_pain = Pain 그룹 대표 ∧ --pain-gate-annual (기본 1,200) ≤ volume_total < --gate-annual,
           Pain 항목 × 브랜드 범위마다 볼륨 순 상위 --pain-per-item (기본 5)
  크레딧 = 10 × (B + M_main + M_pain)   (keyword_info · data_type=all)

Pain은 긴 꼬리에 있어 12,000 게이트만 쓰면 시계열을 거의 못 받는다(제습기 평가에서
Pain 항목이 «시계열 없음»으로 남았다). 작은 Pain 그룹은 voc_axes.py가 **항목 단위로 합산한 뒤** 게이트를 걸어 추세를 낸다.

    python3 select_stage2.py --universe keyword_universe.csv --axes axes.json --category 전동칫솔 -o pool_M.json
"""
import argparse, csv, json, re, unicodedata
from collections import defaultdict


def num(v):
    try:
        return float(v) if v not in ("", None) else 0.0
    except ValueError:
        return 0.0


def sc(kw):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", kw).lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--axes", help="exclude 패턴(경계 밖)을 읽는다")
    ap.add_argument("--category", required=True)
    ap.add_argument("--baseline", help="기준선 검색어(쉼표 구분) — 기본 --category")
    ap.add_argument("--gate-annual", type=int, default=12000)
    ap.add_argument("--pain-gate-annual", type=int, default=1200)
    ap.add_argument("--pain-per-item", type=int, default=5)
    ap.add_argument("--max-m", type=int, help="M 상한 — 기준선 → M_main(볼륨 순) → M_pain 순으로 채운다")
    ap.add_argument("-o", "--out", default="pool_M.json")
    a = ap.parse_args()
    excl = []
    if a.axes:
        excl = [re.compile(p) for p in json.load(open(a.axes, encoding="utf-8")).get("exclude", [])]
    rows = [r for r in csv.DictReader(open(a.universe, encoding="utf-8-sig"))
            if not (r.get("dup_of") or "").strip() and not any(p.search(r["canonical"]) for p in excl)]
    rep = {}
    for r in rows:
        if r["canonical"] not in rep or num(r["volume_total"]) > num(rep[r["canonical"]]["volume_total"]):
            rep[r["canonical"]] = r
    base_c = {sc(x) for x in (a.baseline or a.category).split(",") if x.strip()}
    B = [r for c, r in rep.items() if c in base_c]
    ax = lambda r: (r.get("theme_primary") or "").split(":", 1)[0]
    main_ = sorted((r for r in rep.values() if ax(r) in ("Pain", "Feature", "Attribute")
                    and num(r["volume_total"]) >= a.gate_annual and r not in B),
                   key=lambda r: -num(r["volume_total"]))
    cells = defaultdict(list)
    for r in rep.values():
        v = num(r["volume_total"])
        if ax(r) == "Pain" and a.pain_gate_annual <= v < a.gate_annual and r not in B:
            cells[(r["theme_primary"], bool(r.get("brand")))].append(r)
    pain = []
    for rs in cells.values():
        pain += sorted(rs, key=lambda r: -num(r["volume_total"]))[: a.pain_per_item]
    pain.sort(key=lambda r: -num(r["volume_total"]))
    pick = B + main_ + pain
    if a.max_m:
        pick = pick[: a.max_m]
    json.dump([r["keyword"] for r in pick], open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    nb, nm, npn = (sum(1 for r in pick if r in g) for g in (B, main_, pain))
    print(f"2단 대상 M = 기준선 {nb} + 축 분류 ≥{a.gate_annual:,} {nm} + Pain 긴 꼬리 "
          f"{a.pain_gate_annual:,}~{a.gate_annual:,} {npn} = {len(pick)}")
    print(f"크레딧 = 10 × {len(pick)} = {10 * len(pick):,}  (keyword_info · data_type=all) — Pain 추가분 10 × {npn} = {10 * npn:,}")
    if not B:
        print(f"⚠ 기준선 «{a.baseline or a.category}»이 유니버스에 없다 — 기준선 대비 추세를 낼 수 없다 [데이터 공백]")
    print(f"→ {a.out}")


if __name__ == "__main__":
    main()
