#!/usr/bin/env python3
"""SERP 조회 대상 게이트 — find-taste-words STEP 4.

serp는 `30 × 키워드`로 과금된다. 유니버스 전량에 걸면 300개 유니버스가 9,000이다. 그래서 `ads_metrics`(1)로
검색량을 확보한 유니버스에서 **규칙으로** 상위 G개만 고른다(손으로 고르면 재현되지 않는다).

  대상 단위   = canonical 그룹(같은 검색 중복 `dup_of` 행 · 경계 밖 `exclude` 제외) — 표기변형 둘에 30을 두 번 내지 않는다(계약 T5)
  대표 검색어 = 그룹 안 volume_total 최댓값 행
  A 줄(규모)  = volume_total ≥ --min-volume 인 그룹을 검색량 순으로 --top 개
  B 줄(감각)  = 감각어·감정어가 이미 든 그룹(사전 매칭 · 카테고리어 가림) 중 A에 없는 것을 검색량 순으로 --top-taste 개
  상한        = --max-credits 를 주면 G ≤ floor(max_credits ÷ 30) — B 줄 끝부터 자른다
  크레딧      = 30 × G

lexicon의 category_terms가 비어 있지 않으면 그중 하나를 포함하는 그룹만 대상이 된다(카테고리 경계).

    python3 scripts/serp_gate.py --universe keyword_universe.csv --lexicon themes.커피원두.json --keyword 커피원두 --top 10 --top-taste 5 --max-credits 450 -o serp_targets.json
"""
import argparse, csv, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from series_dedup import canon as _canon  # noqa: E402
from taste_words import load_lexicon, scan, utf8_console  # noqa: E402

SERP_UNIT = 30   # 계약 §1 — serp `30 × 키워드`


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--lexicon", required=True)
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--top", type=int, default=10, help="A 줄 — 검색량 상위 그룹 수 [가정]")
    ap.add_argument("--top-taste", type=int, default=5, help="B 줄 — 감각어가 든 그룹 수 [가정]")
    ap.add_argument("--min-volume", type=int, default=1200, help="12개월 검색량 하한(월 100 환산) [가정]")
    ap.add_argument("--max-credits", type=int, default=0, help="SERP 예산 상한 — 0이면 상한 없음")
    ap.add_argument("-o", "--out", default="serp_targets.json")
    a = ap.parse_args()

    lex = load_lexicon(a.lexicon)
    canon = lambda s: _canon(s, lex["subs"])   # noqa: E731
    heads = sorted({canon(a.keyword)} | {canon(x) for x in lex["category_terms"]}, key=lambda h: (-len(h), h))
    cats = [canon(x) for x in lex["category_terms"]]

    groups, out_of_bound = {}, []
    for r in csv.DictReader(open(a.universe, encoding="utf-8-sig")):
        if r.get("dup_of"):
            continue
        c = r.get("canonical") or canon(r["keyword"])
        if lex["exclude"] and lex["exclude"].search(c):
            out_of_bound.append(r["keyword"]); continue
        if cats and not any(t in c for t in cats):
            out_of_bound.append(r["keyword"]); continue
        v = int(float(r.get("volume_total") or 0))
        g = groups.get(c)
        if g is None or v > g["volume_total"]:
            groups[c] = {"keyword": r["keyword"], "canonical": c, "volume_total": v}

    ranked = sorted(groups.values(), key=lambda g: (-g["volume_total"], g["canonical"]))
    passing = [g for g in ranked if g["volume_total"] >= a.min_volume]
    lane_a = passing[:a.top]
    chosen = {g["canonical"] for g in lane_a}
    lane_b = []
    for g in passing:
        if len(lane_b) >= a.top_taste:
            break
        if g["canonical"] in chosen:
            continue
        hits = scan(g["keyword"], lex, heads)   # 원문(띄어쓰기 있음) — 어절 경계 매칭
        if hits:
            lane_b.append(dict(g, taste_words=" · ".join(sorted({w["word"] for w, _, _ in hits}))))
    targets = [dict(g, lane="A·규모") for g in lane_a] + [dict(g, lane="B·감각") for g in lane_b]
    cut = []
    if a.max_credits:
        cap = a.max_credits // SERP_UNIT
        if len(targets) > cap:
            cut = targets[cap:]
            targets = targets[:cap]
    res = {
        "keyword": a.keyword, "credits": SERP_UNIT * len(targets), "formula": f"{SERP_UNIT} × {len(targets)}",
        "targets": targets, "cut_by_budget": [t["keyword"] for t in cut],
        "below_min_volume": len(ranked) - len(passing), "out_of_boundary": len(out_of_bound),
        "params": {"top": a.top, "top_taste": a.top_taste, "min_volume": a.min_volume, "max_credits": a.max_credits},
    }
    pathlib.Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"SERP 대상 {len(targets)}개 (A·규모 {sum(1 for t in targets if t['lane'].startswith('A'))} · "
          f"B·감각 {sum(1 for t in targets if t['lane'].startswith('B'))}) → 크레딧 {SERP_UNIT} × {len(targets)} = {res['credits']:,}")
    print(f"canonical 그룹 {len(ranked)} · 검색량 하한 미달 {res['below_min_volume']} · 경계 밖 {len(out_of_bound)}"
          + (f" · 예산으로 자름 {len(cut)}: {', '.join(res['cut_by_budget'])}" if cut else ""))
    for t in targets:
        print(f"  {t['lane']}  {t['keyword']}  {t['volume_total']:,}" + (f"  [{t['taste_words']}]" if t.get("taste_words") else ""))
    print(f"→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
