#!/usr/bin/env python3
"""STEP 4 · keyword_info `ads_metrics` 응답으로 풀을 거른다 → `all`에 보낼 목록 (all_request.json · screen.csv).

  python3 scripts/screen_volume.py raw/ads_metrics.json --pool pool.json --seeds seeds.json \
      --min-total 1200 --max-k 80 [--themes themes.json] -o all_request.json --csv screen.csv --ledger ledger.json

2단 조회의 1단이다(계약 §1). 풀 전량은 `ads_metrics`(1/kw)로 볼륨만 보고, 통과분만 `all`(10/kw)로 48개월을 산다.
- 통과 = `volume_total`(최신 12개월 합, 계약 §2) ≥ `--min-total` `[가정]` 을 큰 순서로 `--max-k` 개까지 `[가정]`.
  시드는 볼륨과 무관하게 넣는다(카테고리의 닻 · 최대 5개). 단, 응답에 없으면 넣지 않는다.
- 응답 키워드는 요청 문자열과 다를 수 있다 — canonical 로 대조한다(계약 §1). 요청했는데 응답에 없으면 «미반환» `[데이터 공백]`.
- 응답 안의 완전 중복 행은 합치고(`collapse_identical`), 지표가 모두 같고 토큰이 겹치는 검색어는 같은 검색으로 보고
  한 번만 보낸다(`dedup_map` · 계약 T5 ①) — 10크레딧씩 아낀다.
- `screened_share` = 통과분 volume_total 합 ÷ 측정된 풀 volume_total 합. 예측에 들어가는 검색량이 풀의 몇 %인지다 `[추정]`.
- 크레딧: `1 × 요청 키워드 수`. cost_detail 과 다르면 «환경 단가 확인».
"""
import argparse, csv, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, collapse_identical, dedup_map, load_rules, report_collapse  # noqa: E402
import ledger as L  # noqa: E402

SCREEN_COLS = ["keyword", "canonical", "response_keyword", "volume_total", "volume_avg", "screen", "dup_of", "rank"]


def load_json(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("metrics")
    ap.add_argument("--pool", required=True)
    ap.add_argument("--seeds")
    ap.add_argument("--themes")
    ap.add_argument("--min-total", type=int, default=1200)
    ap.add_argument("--max-k", type=int, default=80)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--ledger", required=True)
    a = ap.parse_args()

    rules = load_rules(a.themes) if a.themes else {}
    obj = load_json(a.metrics)
    rows = obj.get("data", []) if isinstance(obj, dict) else obj
    n_raw = len(rows)
    rows, merged, conflicts = collapse_identical(rows)
    report_collapse(merged, conflicts, n_raw)
    pool = load_json(a.pool)
    seeds = [" ".join(s.split()) for s in load_json(a.seeds)] if a.seeds else []
    rd = (obj.get("request_detail") or {}) if isinstance(obj, dict) else {}
    requested = rd.get("keywords") or pool
    if rd.get("keywords") and [canon(k, rules) for k in rd["keywords"]] != [canon(k, rules) for k in pool]:
        print("⚠️ 응답 request_detail.keywords 가 pool.json 과 다르다 — 다른 목록을 요청했는지 확인할 것")

    by_c = {}
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("keyword"), str):
            by_c.setdefault(canon(r["keyword"], rules), []).append(r)
    dm = dedup_map(rows, rules)

    out = []
    for kw in pool:
        c = canon(kw, rules)
        cands = by_c.get(c) or []
        if not cands:
            out.append({"keyword": kw, "canonical": c, "response_keyword": "", "volume_total": None,
                        "volume_avg": None, "screen": "미반환", "dup_of": "", "rank": ""})
            continue
        best = max(cands, key=lambda r: ((r.get("ads_metrics") or {}).get("volume_total") or 0))
        am = best.get("ads_metrics") or {}
        rep = dm.get(best["keyword"], {}).get("rep", best["keyword"])
        out.append({"keyword": kw, "canonical": c, "response_keyword": best["keyword"],
                    "volume_total": am.get("volume_total"), "volume_avg": am.get("volume_avg"),
                    "screen": "", "dup_of": "" if rep == best["keyword"] else rep, "rank": ""})

    # 같은 검색(지표 일치) — 대표 응답 키워드를 이미 보낸 풀 항목은 «같은 검색 병합»
    measured = sorted([r for r in out if r["screen"] != "미반환"],
                      key=lambda r: (-(r["volume_total"] or 0), r["keyword"]))
    sent, sent_resp, k = [], set(), 0
    seed_c = {canon(s, rules) for s in seeds}
    for i, r in enumerate(measured, 1):
        r["rank"] = i
        rep = r["dup_of"] or r["response_keyword"]
        if rep in sent_resp:
            r["screen"] = f"같은 검색 병합 → {rep}"
            continue
        if r["canonical"] in seed_c:
            r["screen"] = "통과(시드)"
        elif (r["volume_total"] or 0) < a.min_total:
            r["screen"] = "게이트 미달"
            continue
        elif k >= a.max_k:
            r["screen"] = "상한 초과"
            continue
        else:
            r["screen"] = "통과"
            k += 1
        sent.append(r["response_keyword"])
        sent_resp.add(rep)

    tot = sum(r["volume_total"] or 0 for r in measured if not r["screen"].startswith("같은 검색"))
    passed = sum(r["volume_total"] or 0 for r in measured if r["screen"].startswith("통과"))
    share = round(passed / tot, 4) if tot else None

    pathlib.Path(a.out).write_text(json.dumps(sent, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SCREEN_COLS)
        w.writeheader()
        for r in out:
            w.writerow({kk: ("" if r[kk] is None else r[kk]) for kk in SCREEN_COLS})

    formula = 1 * len(requested)
    cost, src = L.cost_of(obj, formula)
    led = L.load(a.ledger)
    L.put(led, {"step": "STEP 4 볼륨 스크린", "step_order": 4, "at": L.saved_at(a.metrics, obj),
                "tool": "keyword_info", "params": f"data_type=ads_metrics · 검색어 {len(requested)}개",
                "purpose": "풀 전량 볼륨으로 48개월 조회 대상 고르기", "returned": len(rows), "cost": cost,
                "cost_source": src, "formula": f"1 × {len(requested)} = {formula}",
                "raw": str(a.metrics).replace("\\", "/"), "data_through": None, "status": "ok"})
    counts = {}
    for r in out:
        key = "같은 검색 병합" if r["screen"].startswith("같은 검색") else r["screen"]
        counts[key] = counts.get(key, 0) + 1
    led["steps"]["screen"] = {"min_total": a.min_total, "max_k": a.max_k, "counts": counts,
                              "n_sent": len(sent), "screened_share": share, "pool_total": tot, "passed_total": passed,
                              "unreturned": [r["keyword"] for r in out if r["screen"] == "미반환"],
                              "credit_formula": formula, "credit_actual": cost}
    L.save(a.ledger, led)

    print(f"풀 {len(pool)}개 · 응답 {len(rows)}행 · " + " · ".join(f"{k2} {v}" for k2, v in counts.items()))
    print(f"all 요청 {len(sent)}개 → {a.out}  (다음 호출 크레딧 10 × {len(sent)} = {10 * len(sent)})")
    if share is not None:
        print(f"예측에 들어갈 검색량 비중(통과 ÷ 측정 풀, volume_total 기준): {share:.1%} [추정]")
    if counts.get("미반환"):
        print(f"미반환 {counts['미반환']}개 [데이터 공백]: {', '.join(r['keyword'] for r in out if r['screen'] == '미반환')[:200]}")
    if formula != cost:
        print(f"⚠️ 크레딧 결정식 {formula} ≠ cost_detail {cost} — 환경 단가 확인")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
