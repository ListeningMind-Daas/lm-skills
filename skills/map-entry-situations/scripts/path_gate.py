#!/usr/bin/env python3
"""path_finder 예산 게이트 (이 스킬 고유 · API 호출 없음) — 본 호출 전에 돌린다.

path_finder 과금은 `50 + 40 × 경로 안 고유 키워드 수(U)`다. limit은 **경로 수** 상한이라 U를 묶지 못하고,
U는 응답을 받아야 안다. 그래서 trace-search-journey와 같은 순서로 막는다:

  1. 작은 limit 파일럿 1회로 r = U ÷ 사용 경로 수를 잰다 (파일럿이 없으면 --ratio, 기본 1.0 [가정])
  2. 본 호출 계획 비용 = 50 + 40 × ceil(limit × r)
  3. 남은 예산 = 예산 − 원장 실측 소진(ledger.jsonl의 cost 합) − 뒤 단계 예약(--reserve)
  4. 계획 ≤ 남은 예산이면 call, 아니면 hold + 남은 예산 안 최대 limit 제안

r은 시드마다 다르고 과대·과소가 모두 관측됐다(trace-search-journey 실측). 계획은 안전한 상한이 아니다 —
본 호출 뒤에는 응답 cost_detail 실측으로 원장에 적고, 실측 누적이 예산을 넘으면 뒤 단계를 줄인다.

    python3 path_gate.py --budget 6000 --ledger ledger.jsonl --reserve 1200 \
        --limit 60 --pilot raw/path_pilot.json [--json]
"""
import argparse, json, math, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cep_lib import load_json, paths_of, utf8_console  # noqa: E402


def planned(limit, r):
    return 50 + 40 * math.ceil(limit * r)


def max_limit(room, r):
    if r <= 0 or room < 90:
        return 0
    L = int(((room - 50) // 40) / r)
    while L > 0 and planned(L, r) > room:
        L -= 1
    return L


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="path_finder 본 호출 예산 게이트 (0크레딧)")
    ap.add_argument("--budget", type=int, required=True, help="이번 분석 총 예산 (크레딧)")
    ap.add_argument("--ledger", help="수집 원장 — 지금까지의 실측 소진")
    ap.add_argument("--reserve", type=int, default=0, help="뒤 단계 예약: 1 × N(ads_metrics) + 10 × M(all) 추정")
    ap.add_argument("--limit", type=int, required=True, help="본 호출 limit (경로 수 상한)")
    ap.add_argument("--pilot", help="파일럿 path_finder 응답")
    ap.add_argument("--ratio", type=float, default=1.0, help="파일럿이 없을 때 r [가정]")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    spent = 0
    if a.ledger and pathlib.Path(a.ledger).is_file():
        spent = sum(json.loads(l)["cost"] for l in open(a.ledger, encoding="utf-8") if l.strip())
    r, src, pilot_note = a.ratio, "assumed", ""
    if a.pilot:
        resp = load_json(a.pilot)
        ps = [p for p in paths_of(resp) if len(p) >= 2]
        u = len({k for p in paths_of(resp) for k in p})
        if ps:
            r, src = u / len(ps), "pilot"
            plim = (resp.get("request_detail") or {}).get("limit")
            pilot_note = f"파일럿 경로 {len(ps)} · 고유 키워드 {u}" + (f" · limit {plim}" if plim else "")
    plan = planned(a.limit, r)
    room = a.budget - spent - a.reserve
    decision = "call" if plan <= room else "hold"
    out = {"budget": a.budget, "spent": spent, "reserve": a.reserve, "room": room, "limit": a.limit,
           "ratio": round(r, 3), "ratio_source": src, "planned_cost": plan, "decision": decision,
           "max_limit_in_room": max_limit(room, r)}
    if a.json:
        print(json.dumps(out, ensure_ascii=False)); return
    print(f"r = {r:.2f} ({'파일럿 실측 — ' + pilot_note if src == 'pilot' else '가정값 [가정] — 계획을 «가정 기반»으로 고지'})")
    print(f"본 호출 계획 = 50 + 40 × ceil({a.limit} × {r:.2f}) = {plan:,} [추정]")
    print(f"남은 예산 = {a.budget:,} − 실측 소진 {spent:,} − 예약 {a.reserve:,} = {room:,}")
    if decision == "call":
        print(f"판정: call — limit {a.limit}로 부른다. 호출 뒤 cost_detail 실측을 원장에 적는다")
    else:
        print(f"판정: hold — 계획이 남은 예산을 넘는다. 남은 예산 안 최대 limit = {out['max_limit_in_room']} "
              "(0이면 본 호출 없이 파일럿 경로만 쓴다 — 반환분 기준으로 표기)")


if __name__ == "__main__":
    main()
