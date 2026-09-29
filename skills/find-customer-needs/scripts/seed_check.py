#!/usr/bin/env python3
"""시드 존재 검증 (find-customer-needs STEP 1 · 이 스킬 고유).

keyword_info 응답 키워드는 요청 문자열과 다를 수 있다(`음파칫솔` → `음파 칫솔`). 문자열 그대로
대조하면 살아 있는 시드를 죽었다고 판정한다. 그래서 **canonical로** 대조한다(계약 §1).

  살아 있음   : canonical이 같은 응답 행이 있고 volume_total > 0 (응답 표기가 다르면 함께 적는다)
  볼륨 0      : 응답 행은 있으나 volume_total이 0·없음 → 확장하지 않는다 [데이터 공백]
  미반환      : 요청했는데 응답에 canonical이 같은 행이 없다 → 0이 아니라 미반환 [데이터 공백]
  요청 안 한 변형 행은 따로 적는다 — 과금은 요청 수이고 이 행은 과금되지 않는다

    python3 seed_check.py seeds.json seed_check.json --rules axes.json -o seeds_live.json
"""
import argparse, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, load_rules  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", help="요청한 시드 목록 JSON")
    ap.add_argument("response", help="keyword_info(ads_metrics) 응답 JSON")
    ap.add_argument("--rules", help="변형치환 — 인라인 JSON · 사전 파일 · axes.json")
    ap.add_argument("-o", "--out", default="seeds_live.json", help="살아 있는 시드(요청 표기) 목록")
    a = ap.parse_args()
    rules = load_rules(a.rules)
    seeds = json.load(open(a.seeds, encoding="utf-8"))
    o = json.load(open(a.response, encoding="utf-8"))
    rows = o.get("data", o) if isinstance(o, dict) else o
    by_c = {}
    for d in rows:
        if isinstance(d, dict) and d.get("keyword"):
            by_c.setdefault(canon(d["keyword"], rules), []).append(d)
    live, zero, missing, seen = [], [], [], set()
    for s in seeds:
        c = canon(s, rules); seen.add(c)
        hit = by_c.get(c)
        if not hit:
            missing.append(s); continue
        best = max(hit, key=lambda d: (d.get("ads_metrics") or {}).get("volume_total") or 0)
        v = (best.get("ads_metrics") or {}).get("volume_total") or 0
        (live if v > 0 else zero).append((s, best["keyword"], v))
    extra = [d["keyword"] for c, ds in by_c.items() if c not in seen for d in ds]
    print(f"시드 {len(seeds)}개 (과금 1 × {len(seeds)} = {len(seeds)}) · 응답 {len(rows)}행 — canonical 대조")
    for s, k, v in live:
        print(f"  살아 있음  {s}" + (f" → 응답 표기 «{k}»" if k != s else "") + f" · {v:,} [실제 데이터]")
    for s, k, v in zero:
        print(f"  볼륨 0     {s} → 확장하지 않는다 [데이터 공백]")
    for s in missing:
        print(f"  미반환     {s} → 0이 아니라 미반환 [데이터 공백] · 과금은 됐다")
    if extra:
        print(f"  요청 안 한 변형 행 {len(extra)}개(과금 없음): {', '.join(extra[:10])}")
    json.dump([s for s, _, _ in live], open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"→ {a.out}  (살아 있는 시드 {len(live)}개 — STEP 2 확장 대상)")


if __name__ == "__main__":
    main()
