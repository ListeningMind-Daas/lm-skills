#!/usr/bin/env python3
"""path_finder 예산 게이트 — 파일럿 비율로 시드별 본 호출 비용을 계획한다 (이 스킬 고유 · 0크레딧).

path_finder 과금은 `50 + 40 × 경로 안 고유 키워드 수(U)`다. limit 은 **경로 수** 상한이라 U를 묶지 못한다 —
호출 전에는 비용이 확정되지 않는다. 그래서 파일럿 응답에서 r = U ÷ 반환 경로 수를 재고, 안전계수 s [가정]를 곱해
U_plan = ceil(limit × r × s) 로 계획한다. 멈추는 기준은 계획이 아니라 원장의 누적 실측이다.

    python3 scripts/leak_budget.py --ledger calls.jsonl --seeds 런닝화,러닝화 추천 --limit 200 \\
        --budget 20000 --volume-nodes 60 -o leak_plan.csv

출력 decision:
  done          원장에 이미 본 limit 이상으로 부른 시드
  call          예산 안 — 부른다
  recall_pilot  파일럿으로만 부른 시드를 본 limit 으로 다시 부른다(예산 안일 때)
  pilot_only    파일럿 시드 재호출이 예산 밖 — 파일럿 경로를 쓰되 limit 이 달라 다른 시드와 비중을 나란히 놓지 않는다
  hold_budget   예산 밖 — 부르지 않는다
"""
import argparse, csv, math, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from leak_lib import read_ledger, utf8_console  # noqa: E402
from series_dedup import canon  # noqa: E402

COLS = ["seed", "limit", "ratio", "ratio_source", "safety", "planned_unique_keywords", "planned_cost",
        "cumulative_planned_cost", "within_budget", "decision", "note"]


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="path_finder 예산 게이트 (0크레딧)")
    ap.add_argument("--ledger", required=True, help="calls.jsonl — 지금까지의 실측 비용과 파일럿")
    ap.add_argument("--seeds", required=True, help="시드, 쉼표로 (최대 3)")
    ap.add_argument("--limit", type=int, required=True, help="본 호출 limit (모든 시드 같게)")
    ap.add_argument("--budget", type=float, required=True, help="이번 분석 크레딧 상한")
    ap.add_argument("--safety", type=float, default=1.3, help="r 안전계수 [가정] (기본 1.3)")
    ap.add_argument("--ratio", type=float, default=None, help="파일럿 없이 쓸 r [가정]")
    ap.add_argument("--volume-nodes", type=int, default=60, help="STEP 5 노드 볼륨 조회 계획 키워드 수 M (1 × M)")
    ap.add_argument("-o", "--out", default="leak_plan.csv")
    a = ap.parse_args()

    led = read_ledger(a.ledger) if pathlib.Path(a.ledger).is_file() else []
    spent = sum((r.get("cost") or 0) for r in led)
    pf = [r for r in led if r.get("tool") == "path_finder" and r.get("status") == "ok"]
    pilots = [r for r in pf if r.get("role") == "pilot" and r.get("used_paths")]
    if pilots:
        p = pilots[-1]
        r, src = p["unique_keywords"] / max(1, p["returned"]), f"pilot {p['id']} ({p['seed']} limit {p['limit']})"
    elif a.ratio is not None:
        r, src = a.ratio, "assumed"
    else:
        sys.exit("파일럿이 원장에 없고 --ratio 도 없다 — 작은 limit 파일럿을 먼저 부르고 record_call.py 로 적는다")
    seeds = [s.strip() for s in a.seeds.split(",") if s.strip()]
    if len(seeds) > 3:
        sys.exit("시드는 최대 3개다")
    best = {}
    for x in pf:
        c = canon(x.get("seed", ""))
        if c and (x.get("limit") or 0) >= (best.get(c, {}).get("limit") or 0):
            best[c] = x
    vol_cost = 1 * a.volume_nodes
    cum = spent
    rows = []
    for s in seeds:
        c = canon(s)
        prior = best.get(c)
        u = math.ceil(a.limit * r * a.safety)
        cost = 50 + 40 * u
        row = {"seed": s, "limit": a.limit, "ratio": round(r, 3), "ratio_source": src, "safety": a.safety,
               "planned_unique_keywords": u, "planned_cost": cost}
        if prior and (prior.get("limit") or 0) >= a.limit:
            row.update(planned_unique_keywords=prior["unique_keywords"], planned_cost=0, decision="done",
                       note=f"{prior['id']} limit {prior['limit']} 실측 {prior.get('cost')}")
        else:
            ok = cum + cost + vol_cost <= a.budget
            if prior:   # 파일럿으로만 부른 시드
                row["decision"] = "recall_pilot" if ok else "pilot_only"
                row["note"] = (f"파일럿 {prior['id']} limit {prior['limit']} → 본 limit 재호출" if ok else
                               f"재호출 계획 {cost} 이 예산 밖 — 파일럿 경로(limit {prior['limit']})로만 본다. limit 맞추기 추가 비용 {cost}")
            else:
                row["decision"] = "call" if ok else "hold_budget"
                row["note"] = "" if ok else f"누적 계획 {cum + cost + vol_cost} > 예산 {a.budget:g}"
            if ok:
                cum += cost
        row["cumulative_planned_cost"] = cum
        row["within_budget"] = "yes" if row["decision"] in ("done", "call", "recall_pilot") else "no"
        rows.append(row)
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in COLS})
    print(f"예산 {a.budget:g} · 원장 실측 누적 {spent} · r={r:.3f} ({src}) × 안전계수 {a.safety} [가정]")
    print(f"식: 시드마다 50 + 40 × ceil({a.limit} × {r:.3f} × {a.safety}) · 노드 볼륨 1 × {a.volume_nodes} = {vol_cost}")
    for row in rows:
        print(f"  {row['seed']}: {row['decision']} · 계획 {row['planned_cost']} · 누적 {row['cumulative_planned_cost']}"
              + (f" · {row['note']}" if row.get("note") else ""))
    held = [x for x in rows if x["decision"] in ("hold_budget", "pilot_only")]
    if held:
        n_need = sum(1 for x in rows if x["decision"] != "done")
        room = a.budget - spent - vol_cost
        if n_need and room > 50 * n_need:
            lim = int(((room / n_need - 50) / 40) / (r * a.safety))
            print(f"⚠ 예산 밖 시드 {len(held)}개 — 예산 안 최대 limit(시드 {n_need}개 같은 limit): {max(lim, 0)}")
        else:
            print(f"⚠ 예산 밖 시드 {len(held)}개 — 남은 예산 {room:g} 로는 본 호출을 더 부를 수 없다")
    print("계획은 [추정]이다. 호출할 때마다 원장 누적 실측을 보고 예산을 넘으면 멈춘다.")
    print(f"→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
