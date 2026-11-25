#!/usr/bin/env python3
"""path_finder 예산 게이트 (이 스킬 고유 스크립트) — 호출 전에 돌린다. API 호출 없음.

path_finder는 가장 비싼 커넥터이고, **호출 전에는 비용이 확정되지 않는다.**
과금은 `50 + 40 × 경로 안 고유 키워드 수(U)`인데 U는 응답을 받아야 안다. limit은 경로 수
상한이지 U 상한이 아니다(경로 하나에 키워드가 여러 개 들어 있다). 그래서:

  1. 출발 후보의 볼륨(keyword_info ads_metrics, 1/kw)으로 경로가 거의 안 나올 후보를 거른다
  2. 파일럿 1회(작은 limit)로 «경로당 고유 키워드 비율 r = U / 사용 경로»를 실측한다
  3. 나머지 후보의 계획 비용 = 50 + 40 × ceil(limit × r) 을 누적해 예산 안에서만 «호출»로 둔다

r은 시드마다 다르다. 실데이터 평가(캠핑의자 KR)에서 파일럿 r로 세운 계획이 대부분 과대였지만 한 시드는
**과소**였다. 선크림(KR) 예시 실행에서는 파일럿 시드와 본 호출 시드가 달라 계획이 크게 과대였다.
계획은 안전하다고 가정하지 않고, 호출마다 cost_detail 실측으로 누적해 예산을 넘으면 멈춘다.

사용자가 지정한 시드는 `--seeds a,b`로 고정한다 — 볼륨 순보다 먼저 예산을 배정한다(게이트·예산은
그대로 적용하고, 보류되면 이유를 note에 적는다). `--seeds-only`면 나머지 후보는 `hold_not_selected`로
**행을 남긴다**(후보를 지운 metrics로 계획을 다시 세우면 보류 후보가 요약에서 사라진다).

파일럿 시드는 파일럿 limit으로만 경로가 있다. `--recall-pilot`이면 그 시드를 본 limit으로 다시 부르는
계획을 예산 안에서 세우고, 아니면 limit을 맞추는 데 드는 추가 비용을 출력한다.

keyword_info는 표기를 바꿔 돌려주거나(`캠핑의자` → `캠핑 의자`) 표기변형 후보를 한 행으로 합친다
(`무선 청소기`·`무선청소기` 요청 → 1행). 과금은 요청 수 기준이다. 그래서 요청↔응답과 파일럿 시드는
canonical(NFKC·소문자·공백 제거)로 대조하고, 합쳐진 후보는 `hold_merged`, 응답에 없는 후보는
`hold_not_returned`로 **행을 남긴다** — 조용히 사라지지 않는다.

    python3 path_budget.py --metrics start_metrics.json --limit 50 --budget 6000 \\
        --pilot resp_pilot.json -o path_plan.csv [--seeds 선크림,선스틱 --seeds-only] [--recall-pilot]

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
    ap.add_argument("--seeds", default="", help="고정할 시드 (쉼표 구분) — 볼륨 순보다 먼저 예산을 배정한다")
    ap.add_argument("--seeds-only", action="store_true", help="--seeds 밖 후보는 부르지 않는다(hold_not_selected 행으로 남김)")
    ap.add_argument("--recall-pilot", action="store_true",
                    help="파일럿 limit ≠ 본 limit이면 파일럿 시드를 본 limit으로 다시 부르는 계획을 세운다(예산 안에서만)")
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

    pins = [k.strip() for k in a.seeds.split(",") if k.strip()]
    pin_c = [canon(k) for k in pins]
    if a.seeds_only and not pins:
        sys.exit("--seeds-only는 --seeds와 함께 쓴다")

    def pin_rank(c):
        for key in (canon(c["keyword"]), canon(c["requested"] or c["keyword"])):
            if key in pin_c:
                return pin_c.index(key)
        return None
    for q, qc in zip(pins, pin_c):
        if not any(pin_rank(c) == pin_c.index(qc) for c in cands):
            cands.append({"keyword": q, "requested": "", "v": None, "status": "pin_missing"})

    pilot_c, pilot_cost, ratio, ratio_src, pilot_limit = None, 0, a.ratio, "assumed", None
    pilot_warn = ""
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
            if pilot_c:
                from journey_paths import seed_variant_scan  # noqa: E402  (같은 스킬 고유 스크립트)
                pcs = [[canon(k) for k in q] for q in paths]
                found = sum(pilot_c in cs for cs in pcs)
                only, variants, rules = seed_variant_scan(pilot_c, pcs)
                if found == 0:
                    pilot_warn = (f"⚠️ 파일럿 시드 «{pr['keyword']}»가 파일럿 경로 {len(paths)}개 중 0개에 있다"
                                  + (f" — 한 글자 다른 표기 {', '.join(f'«{v}» {n}경로' for v, n in variants.items())}. "
                                     f"path_finder가 시드 표기를 바꿔 돌려준 것으로 보인다. STEP 4 단계 사전 rules.변형치환에 "
                                     f"{json.dumps(rules, ensure_ascii=False)}를 넣는다" if variants else "")
                                  + " (r 계산에는 영향 없음)")
        else:
            ratio_src = "pilot_empty"

    spent = kw_cost + pilot_cost
    cum = spent
    out_rows = []
    planned_u = math.ceil(a.limit * ratio)
    planned_each = 50 + 40 * planned_u
    def order(x):
        rk = pin_rank(x) if x["status"] in ("", "pin_missing") else None
        return (x["status"] not in ("", "pin_missing"), rk is None, rk or 0, -(x["v"] or 0))
    equalize = None
    for c in sorted(cands, key=order):
        kw, v = c["keyword"], c["v"]
        gate = v is not None and v >= a.min_volume
        pu, planned, lim, note = planned_u, planned_each, a.limit, ""
        pinned = pin_rank(c) is not None
        is_pilot = pilot_c and not c["status"] and pilot_c in (canon(kw), canon(c["requested"] or kw))
        mismatch = is_pilot and pilot_limit is not None and pilot_limit != a.limit
        if is_pilot and not (mismatch and a.recall_pilot and cum + planned <= a.budget):
            decision, pu, planned = "pilot_done", "", pilot_cost
            lim = pilot_limit if pilot_limit is not None else ""
            if mismatch:
                equalize = (kw, planned_each, cum)
                note = (f"파일럿 limit {pilot_limit} ≠ 본 limit {a.limit} — 이 시드의 비중을 다른 시드와 나란히 비교하지 않는다. "
                        f"본 limit으로 다시 부르는 계획 {planned_each}"
                        + (" — 예산 부족으로 재호출 보류" if a.recall_pilot else " (--recall-pilot)"))
        elif is_pilot:
            decision = "call"
            cum += planned
            note = (f"파일럿(limit {pilot_limit}) 시드를 본 limit {a.limit}으로 재호출 — limit 불일치 해소. "
                    f"파일럿 비용 {pilot_cost}은 이미 소진")
        elif c["status"] == "pin_missing":
            decision, pu, planned = "hold_not_in_metrics", "", ""
            note = "고정 시드(--seeds)인데 출발 후보 응답에 없다 — ads_metrics를 먼저 받아 게이트를 판정한다 [데이터 공백]"
        elif c["status"] == "merged":
            decision, pu, planned = "hold_merged", "", ""
            note = f"응답에서 «{c['into']}»와 한 행으로 합쳐짐 — 자기 볼륨 없음 [데이터 공백], 따로 부르지 않는다"
        elif c["status"] == "not_returned":
            decision, pu, planned = "hold_not_returned", "", ""
            note = "요청했으나 응답에 없음 — 미반환 [데이터 공백] (과금은 됨)"
        elif c["status"] == "unrequested":
            decision, pu, planned = "hold_unrequested", "", ""
            note = "요청하지 않은 변형 행 — 출발 후보가 아니다"
        elif a.seeds_only and not pinned:
            decision, pu, planned = "hold_not_selected", "", ""
            note = "--seeds 밖 후보 — 이번 계획에서 부르지 않는다(사용자 지정 범위)"
        elif v is None:
            decision = "hold_no_volume"
        elif not gate:
            decision = "hold_volume"
        elif cum + planned > a.budget:
            decision = "hold_budget"
        else:
            decision = "call"
            cum += planned
        if pinned and decision.startswith("hold") and decision != "hold_not_in_metrics" and not note:
            why = {"hold_no_volume": "볼륨 없음", "hold_volume": f"볼륨 게이트({a.min_volume}) 미달",
                   "hold_budget": f"계획 {planned}이 남은 예산 {a.budget - cum}을 넘음"}.get(decision, decision)
            note = f"고정 시드(--seeds)지만 {why} — 게이트·예산은 고정 시드에도 적용된다"
        elif pinned and decision == "call" and not note:
            note = "고정 시드(--seeds)"
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
    recalled = any(r["decision"] == "call" and r["note"].startswith("파일럿(") for r in out_rows)
    if pilot_limit is not None and pilot_limit != a.limit and not recalled:
        print(f"⚠️ 파일럿 limit {pilot_limit} ≠ 본 limit {a.limit} — 파일럿 시드의 경로는 limit {pilot_limit} 기준이다. "
              "요약에서 다른 시드와 비중을 나란히 비교하지 않는다")
    budget_held = [r for r in out_rows if r["decision"] == "hold_budget"]
    if budget_held:
        g = n_call + len(budget_held)
        left = a.budget - spent
        sug = [f"{n}시드 limit {max_limit(left // n, ratio)}" for n in range(1, g + 1)]
        print(f"예산 안 최대 limit (남은 {left} · r={ratio:.3f} · 시드 수별 균등 배분) [추정]: " + " · ".join(sug))
    if equalize:
        kw, cost, _ = equalize
        fits = cum + cost <= a.budget
        print(f"limit 맞추기: 파일럿 시드 «{kw}»를 본 limit {a.limit}으로 다시 부르는 계획 {cost} · 이 계획 뒤 남는 예산 "
              f"{a.budget - cum} → {'들어감 — --recall-pilot으로 계획에 넣는다' if fits and not a.recall_pilot else ('들어감' if fits else '안 들어감')} [추정]")
    if pilot_warn:
        print(pilot_warn)
    print("계획 비용은 [추정]이다. r은 시드마다 다르다(관측: 계획이 과소였던 사례와 크게 과대였던 사례가 모두 있다) "
          "— 여유를 두고, "
          "호출 후 cost_detail.total_cost로 누적을 다시 계산해 누적 실측이 예산을 넘으면 다음 호출을 멈춘다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
