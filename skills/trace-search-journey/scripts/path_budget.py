#!/usr/bin/env python3
"""path_finder 예산 게이트 (이 스킬 고유 스크립트) — 호출 전에 돌린다. API 호출 없음.

path_finder는 가장 비싼 커넥터이고, **호출 전에는 비용이 확정되지 않는다.**
과금은 `50 + 40 × 경로 안 고유 키워드 수(U)`인데 U는 응답을 받아야 안다. limit은 경로 수
상한이지 U 상한이 아니다(경로 하나에 키워드가 여러 개 들어 있다). 그래서:

  1. 출발 후보의 볼륨(keyword_info ads_metrics, 1/kw)으로 경로가 거의 안 나올 후보를 거른다
  2. 파일럿 1회(작은 limit)로 «경로당 고유 키워드 비율 r = U / 사용 경로»를 실측한다
  3. 나머지 후보의 계획 비용 = 50 + 40 × ceil(limit × r) 을 누적해 예산 안에서만 «호출»로 둔다

r은 시드마다 다르다. 2026-09-28 실측(캠핑의자 KR)에서 파일럿 r = 1.25(limit 20)였고 본 호출 r은
0.91~1.29였다 — 계획이 2건은 과대, 1건은 **과소(−40)**였다. 계획은 안전하다고 가정하지 않고, 호출마다
cost_detail 실측으로 누적해 예산을 넘으면 멈춘다.

keyword_info는 표기를 바꿔 돌려주거나(`캠핑의자` → `캠핑 의자`) 표기변형 후보를 한 행으로 합친다
(`무선 청소기`·`무선청소기` 요청 → 1행). 과금은 요청 수 기준이다. 그래서 요청↔응답과 파일럿 시드는
canonical(NFKC·소문자·공백 제거)로 대조하고, 합쳐진 후보는 `hold_merged`, 응답에 없는 후보는
`hold_not_returned`로 **행을 남긴다** — 조용히 사라지지 않는다.

    python3 path_budget.py --metrics start_metrics.json --limit 50 --budget 6000 \\
        --pilot resp_pilot.json -o path_plan.csv

파일럿이 없으면 `--ratio`(기본 1.0, [가정])를 쓴다.
"""
import argparse, csv, json, math, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon  # noqa: E402  (계약 T5 — 요청↔응답·파일럿 대조)


def max_limit(per_seed, ratio):
    """50 + 40 × ceil(L × r) ≤ per_seed 를 만족하는 최대 L (없으면 0)."""
    if ratio <= 0 or per_seed < 90:
        return 0
    L = int(((per_seed - 50) // 40) / ratio)
    while L > 0 and 50 + 40 * math.ceil(L * ratio) > per_seed:
        L -= 1
    return L


def main():
    ap = argparse.ArgumentParser(description="path_finder 호출 계획·예산 게이트 (0크레딧)")
    ap.add_argument("--metrics", required=True, help="출발 후보의 keyword_info(ads_metrics) 응답 JSON")
    ap.add_argument("--limit", type=int, required=True, help="본 호출에 쓸 limit (경로 수 상한)")
    ap.add_argument("--budget", type=int, required=True, help="이번 분석 총 예산 (크레딧)")
    ap.add_argument("--pilot", help="파일럿 path_finder 응답 JSON — r 실측에 쓴다")
    ap.add_argument("--ratio", type=float, default=1.0, help="파일럿이 없을 때의 r [가정]")
    ap.add_argument("--min-volume", type=int, default=1000, help="volume_avg 게이트 [가정]")
    ap.add_argument("-o", "--out", default="path_plan.csv")
    a = ap.parse_args()

    body = json.loads(pathlib.Path(a.metrics).read_text(encoding="utf-8"))
    rows = body.get("data", body) if isinstance(body, dict) else body
    req = ((body.get("request_detail") or {}).get("keywords") if isinstance(body, dict) else None) or []
    measured_kw = (body.get("cost_detail") or {}).get("total_cost") if isinstance(body, dict) else None
    # 과금은 요청 수 기준 — 반환 행 수로 세면 합쳐진 후보만큼 과소 기록된다
    kw_cost = measured_kw if isinstance(measured_kw, (int, float)) else (len(req) or len(rows))

    # 요청 ↔ 응답 대조 (canonical). 정확히 같은 문자열을 먼저 짝짓는다
    cands, used = [], set()
    for r in rows:
        kw = r.get("keyword", "")
        v = (r.get("ads_metrics") or {}).get("volume_avg")
        match = kw if kw in req and kw not in used else next(
            (q for q in req if q not in used and canon(q) == canon(kw)), "")
        if match:
            used.add(match)
        cands.append({"keyword": kw, "requested": match, "v": v, "status": "" if match or not req else "unrequested"})
    returned_c = {canon(c["keyword"]): c["keyword"] for c in cands}
    for q in req:
        if q in used:
            continue
        into = returned_c.get(canon(q))
        cands.append({"keyword": q, "requested": q, "v": None,
                      "status": "merged" if into else "not_returned", "into": into})

    pilot_c, pilot_cost, ratio, ratio_src, pilot_limit = None, 0, a.ratio, "assumed", None
    if a.pilot:
        p = json.loads(pathlib.Path(a.pilot).read_text(encoding="utf-8"))
        paths = [q for q in (p.get("data") or []) if isinstance(q, list) and len(q) >= 2]
        uniq = {" ".join(str(k).split()) for q in paths for k in q if str(k).strip()}
        measured = (p.get("cost_detail") or {}).get("total_cost")
        pilot_cost = measured if isinstance(measured, (int, float)) else 50 + 40 * len(uniq)
        pr = p.get("request_detail") or {}
        pilot_c = canon(pr["keyword"]) if pr.get("keyword") else None
        pilot_limit = pr.get("limit")
        if paths:
            ratio, ratio_src = len(uniq) / len(paths), "pilot"
        else:
            ratio_src = "pilot_empty"

    spent = kw_cost + pilot_cost
    cum = spent
    out_rows = []
    planned_u = math.ceil(a.limit * ratio)
    planned_each = 50 + 40 * planned_u
    for c in sorted(cands, key=lambda x: (x["status"] != "", -(x["v"] or 0))):
        kw, v = c["keyword"], c["v"]
        gate = v is not None and v >= a.min_volume
        pu, planned, lim, note = planned_u, planned_each, a.limit, ""
        if pilot_c and not c["status"] and pilot_c in (canon(kw), canon(c["requested"] or kw)):
            decision, pu, planned = "pilot_done", "", pilot_cost
            lim = pilot_limit if pilot_limit is not None else ""
            if pilot_limit is not None and pilot_limit != a.limit:
                note = f"파일럿 limit {pilot_limit} ≠ 본 limit {a.limit} — 이 시드의 비중을 다른 시드와 나란히 비교하지 않는다"
        elif c["status"] == "merged":
            decision, pu, planned = "hold_merged", "", ""
            note = f"응답에서 «{c['into']}»와 한 행으로 합쳐짐 — 자기 볼륨 없음 [데이터 공백], 따로 부르지 않는다"
        elif c["status"] == "not_returned":
            decision, pu, planned = "hold_not_returned", "", ""
            note = "요청했으나 응답에 없음 — 미반환 [데이터 공백] (과금은 됨)"
        elif c["status"] == "unrequested":
            decision, pu, planned = "hold_unrequested", "", ""
            note = "요청하지 않은 변형 행 — 출발 후보가 아니다"
        elif v is None:
            decision = "hold_no_volume"
        elif not gate:
            decision = "hold_volume"
        elif cum + planned > a.budget:
            decision = "hold_budget"
        else:
            decision = "call"
            cum += planned
        if c["requested"] and c["requested"] != kw and not note:
            note = f"요청 표기 «{c['requested']}» → 응답 표기 «{kw}»"
        out_rows.append({
            "keyword": kw, "requested_keyword": c["requested"], "volume_avg": "" if v is None else v,
            "volume_gate": int(gate), "limit": lim, "ratio": round(ratio, 3), "ratio_source": ratio_src,
            "planned_unique_keywords": pu, "planned_cost": planned,
            "cumulative_planned_cost": cum if decision == "call" else "",
            "within_budget": int(decision == "call"), "decision": decision, "note": note,
        })

    cols = ["keyword", "requested_keyword", "volume_avg", "volume_gate", "limit", "ratio", "ratio_source",
            "planned_unique_keywords", "planned_cost", "cumulative_planned_cost",
            "within_budget", "decision", "note"]
    with open(a.out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)

    n_call = sum(r["decision"] == "call" for r in out_rows)
    print(f"예산 {a.budget} · 이미 소진: keyword_info {kw_cost} + 파일럿 {pilot_cost} = {spent} [실제 데이터]")
    print(f"r = {ratio:.3f} ({ratio_src}) · 호출 {n_call}건 · 계획 누적 {cum} "
          f"· 남는 예산 {a.budget - cum} [추정]")
    held = [r for r in out_rows if r["decision"].startswith("hold")]
    if held:
        print(f"보류 {len(held)}건: " + ", ".join(f"{r['keyword']}({r['decision']})" for r in held))
    if pilot_limit is not None and pilot_limit != a.limit:
        print(f"⚠️ 파일럿 limit {pilot_limit} ≠ 본 limit {a.limit} — 파일럿 시드의 경로는 limit {pilot_limit} 기준이다. "
              "요약에서 다른 시드와 비중을 나란히 비교하지 않는다")
    budget_held = [r for r in out_rows if r["decision"] == "hold_budget"]
    if budget_held:
        g = n_call + len(budget_held)
        left = a.budget - spent
        sug = [f"{n}시드 limit {max_limit(left // n, ratio)}" for n in range(1, g + 1)]
        print(f"예산 안 최대 limit (남은 {left} · r={ratio:.3f} · 시드 수별 균등 배분) [추정]: " + " · ".join(sug))
    print("계획 비용은 [추정]이다. r은 시드마다 다르다(실측 0.91~1.29, 계획 과소 사례 있음) — 여유를 두고, "
          "호출 후 cost_detail.total_cost로 누적을 다시 계산해 누적 실측이 예산을 넘으면 다음 호출을 멈춘다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
