#!/usr/bin/env python3
"""호출 원장 — 원본 응답을 저장할 때마다 한 줄씩 남긴다 (rank-category-brands STEP 2 · 3, 이 스킬 고유).

  python3 scripts/log_call.py --ledger ledger.jsonl --step "1단 스크리닝" --raw stage1_ads_metrics.json \
      --purpose "브랜드×분야어 · 논브랜드 12개월 볼륨"

비용은 응답의 `cost_detail.total_cost`(나눠 받아 합친 응답이면 parts 합)에서 읽는다 — 손으로 적지 않는다.
계정 누적 사용량(`used_credits`)은 이번 분석 소진이 아니다(계약 §1). 반환 수 · 데이터 마지막 달 · 호출 조건도
원본 응답의 `request_detail` · `data` 에서 읽는다. 저장본을 다시 쓰면 `--reused-from C1` 로 비용 0 을 남긴다.
build_report.py 가 이 원장으로 report.json 의 provenance.calls 를 만든다.
"""
import argparse, json, pathlib, sys
from datetime import datetime


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--step", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--purpose", default="")
    ap.add_argument("--tool", default="keyword_info")
    ap.add_argument("--at", default=None, help="호출 시각 (기본: 지금)")
    ap.add_argument("--reused-from")
    a = ap.parse_args()
    obj = json.load(open(a.raw, encoding="utf-8"))
    rows = obj.get("data", []) if isinstance(obj, dict) else obj
    rd = obj.get("request_detail") or {} if isinstance(obj, dict) else {}
    cd = obj.get("cost_detail") or {} if isinstance(obj, dict) else {}
    cost = cd.get("total_cost")
    if cost is None and cd.get("parts"):
        cost = sum((p or {}).get("total_cost") or 0 for p in cd["parts"])
    if a.reused_from:
        cost = 0
    if cost is None:
        sys.exit("cost_detail.total_cost 가 없다 — 응답 원본을 그대로 저장했는지 확인할 것(비용을 손으로 적지 않는다)")
    months = [m.get("month") for r in rows if isinstance(r, dict) for m in r.get("monthly_volume") or []
              if isinstance(m, dict) and m.get("month")]
    led = pathlib.Path(a.ledger)
    prev = [json.loads(l) for l in led.read_text(encoding="utf-8").splitlines() if l.strip()] if led.is_file() else []
    entry = {"id": f"C{len(prev) + 1}", "step": a.step, "at": a.at or datetime.now().strftime("%Y-%m-%d %H:%M"),
             "tool": a.tool,
             "params": f"data_type={rd.get('data_type', '?')} · gl={rd.get('gl', '?')} · 검색어 {len(rd.get('keywords') or [])}개",
             "purpose": a.purpose, "returned": len(rows), "cost": cost, "raw": a.raw,
             "data_through": max(months) if months else None, "status": "ok",
             "data_type": rd.get("data_type")}
    if a.reused_from:
        entry["reused_from"] = a.reused_from
    with open(led, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"{entry['id']} {a.step} · {entry['params']} · 반환 {len(rows)}행 · 비용 {cost} → {a.ledger}")


if __name__ == "__main__":
    main()
