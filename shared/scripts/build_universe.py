#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""유니버스 조립 — 확장 결과에 지표를 붙이고 정규화·테마를 부여한다 (SKILL.md STEP 4~5).

intent_finder는 키워드 문자열만 준다. 지표를 붙이지 않으면 CSV가 만들어지지 않는다.
그리고 한국어에서는 API가 요청하지 않은 표기변형(선크림/썬크림)을 함께 반환하므로,
행은 보존하되 canonical 그룹키로 묶는다. 그룹 볼륨은 하한(그룹 내 최댓값)과 상한(그룹 내 합)을
병기한다 — 변형 행이 같은 검색의 중복인지 서로 다른 검색인지 응답만으로는 알 수 없다(계약 T5).

사용법:
  python3 build_universe.py --metrics <ads_info.json> --themes <themes.json> \
      --round "S1·시드=seeds.json" --round "R1·확장1=intent_r1.json" \
      [--trend trend.csv] -o keyword_universe.csv
"""
import argparse, csv, json, pathlib, re, sys, unicodedata
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon as _canon, dedup_map  # noqa: E402  같은 검색의 중복 판정 (계약 T5)


def load(path):
    o = json.load(open(path, encoding="utf-8"))
    return o["data"] if isinstance(o, dict) and "data" in o else o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--themes", required=True)
    ap.add_argument("--round", action="append", default=[], help="라벨=파일 (시간순)")
    ap.add_argument("--trend", help="trend_yoy.py 산출 CSV — 있으면 yoy 컬럼을 붙인다")
    ap.add_argument("--seeds", help="시드 목록 JSON — source_seed 귀속에 사용")
    ap.add_argument("-o", "--out", default="keyword_universe.csv")
    ap.add_argument("--ref-month", default="")
    ap.add_argument("--queried-at", default="")
    a = ap.parse_args()

    cfg = json.load(open(a.themes, encoding="utf-8"))
    subs = cfg.get("rules", {}).get("변형치환", {})
    themes = [(t["name"], re.compile(t["pattern"])) for t in cfg["themes"]]
    brands = cfg.get("brands", [])
    nm = cfg.get("no_match", {})

    def canon(kw):
        return _canon(kw, subs)   # 변형치환은 띄어쓰기를 지우기 전에 적용한다(단어 경계 보존)

    round_of = {}
    for spec in a.round:
        label, path = spec.split("=", 1)
        for kw in load(path):
            k = kw if isinstance(kw, str) else kw.get("keyword")
            round_of.setdefault(canon(k), label)

    seeds = json.load(open(a.seeds, encoding="utf-8")) if a.seeds else []
    trend = {}
    if a.trend:
        for r in csv.DictReader(open(a.trend, encoding="utf-8-sig")):
            trend[r["keyword"]] = r

    metrics = load(a.metrics)
    dmap = dedup_map(metrics, subs)
    rows = []
    for d in metrics:
        kw = d["keyword"]; m = d.get("ads_metrics") or {}
        c = canon(kw)
        brand = next((b for b in brands if canon(b) in c), "")
        facets = [n for n, p in themes if p.search(c)]
        theme = facets[0] if facets else (nm.get("with_brand", "") if brand else nm.get("without_brand", ""))
        gg, nv = m.get("gg_volume_total") or 0, m.get("nv_volume_total") or 0
        t = trend.get(kw, {})
        rows.append({
            "keyword": kw, "canonical": c,
            "dup_of": "" if dmap.get(kw, {}).get("rep", kw) == kw else dmap[kw]["rep"],
            "dup_basis": dmap.get(kw, {}).get("basis", ""),
            "volume_total": m.get("volume_total"), "volume_avg": m.get("volume_avg"),
            "gg_volume_total": gg, "nv_volume_total": nv,
            "nv_share": round(nv / (gg + nv), 3) if gg + nv else "",
            "cpc": m.get("cpc"), "competition": m.get("competition"),
            "competition_index": m.get("competition_index"),
            "brand": brand, "theme_primary": theme, "facets": "|".join(facets),
            "discovery_round": round_of.get(c, "V·표기변형(자동반환)"),
            "source_seed": next((s for s in seeds if canon(s) in c), "(미상·다중시드)"),
            "yoy": t.get("yoy", ""), "yoy_ex_last_month": t.get("yoy_ex_last_month", ""),
            "mom_last": t.get("mom_last", ""), "last_month_flag": t.get("last_month_flag", ""),
            "missing_months_in_window": t.get("missing_months_in_window", ""),
            "api_volume_trend_do_not_use": m.get("volume_trend"),
            "data_ref_month": a.ref_month or t.get("data_ref_month", ""),
            "queried_at": a.queried_at,
            "label_metric": "실제 데이터", "label_classification": "추정",
        })
    rows.sort(key=lambda r: -(r["volume_total"] or 0))

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    groups = len({r["canonical"] for r in rows if not r["dup_of"]})
    lo, hi, lo_nb, hi_nb = {}, {}, {}, {}
    n_dup = sum(1 for r in rows if r["dup_of"])
    for r in rows:
        if r["dup_of"]:          # 같은 검색의 중복 — 한 번만 센다
            continue
        c, v = r["canonical"], (r["volume_total"] or 0)
        lo[c] = max(lo.get(c, 0), v); hi[c] = hi.get(c, 0) + v
        if not r["brand"]:
            lo_nb[c] = max(lo_nb.get(c, 0), v); hi_nb[c] = hi_nb.get(c, 0) + v
    t_lo, t_hi = sum(lo.values()), sum(hi.values())
    print(f"{len(rows)}행 → 같은 검색의 중복 {n_dup}행 제외(시계열·지표 일치 [추정]) → canonical {groups}그룹")
    print(f"그룹 기준 볼륨: 하한 {t_lo:,} ~ 상한 {t_hi:,}  "
          "※ 하한=그룹 내 최댓값(변형이 같은 검색의 중복일 때) · 상한=그룹 내 합(서로 다른 검색일 때) [추정]")
    if t_lo and t_hi:
        print(f"논브랜드 볼륨 비중: 하한 기준 {sum(lo_nb.values()) / t_lo:.1%} · 상한 기준 {sum(hi_nb.values()) / t_hi:.1%}")
    flagged = sum(1 for r in rows if r["last_month_flag"])
    print(f"브랜드 포함 행: {sum(1 for r in rows if r['brand'])}")
    if flagged:
        print(f"마지막 달 미완결 의심: {flagged}행 — 추세 인용 시 yoy_ex_last_month와 교차확인")
    tv = Counter()
    for r in rows:
        if not r["dup_of"]:
            tv[r["theme_primary"]] += r["volume_total"] or 0
    print("\n테마 구성 (볼륨 비중 · 중복 제외 행 합 = 상한 기준)")
    for t, v in tv.most_common():
        print(f"  {t:<14}{v:>12,}  {v/sum(tv.values()):>6.1%}")
    print(f"\n→ {a.out}")


if __name__ == "__main__":
    main()
