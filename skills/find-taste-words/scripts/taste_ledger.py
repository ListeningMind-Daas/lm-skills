#!/usr/bin/env python3
"""호출 원장 — find-taste-words가 API 응답을 저장할 때마다 한 줄씩 남긴다 (STEP 1·2·3·5).

리포트의 호출 순서 · 호출 로그 · 비용 보고서(provenance.calls)는 이 원장에서만 만든다 — 손으로 적지 않는다.
원본 응답 파일을 읽어 호출 조건(request_detail) · 반환 수(data 길이) · 비용(cost_detail.total_cost)을 적는다.
`cost_detail`이 없는 파일은 거부한다 — 비용을 추정해 채우지 않는다. 저장본을 다시 쓴 것이면 `--reused-from`을 주고 비용 0으로 적는다.
응답 본문 · 요청 헤더 · 키는 원장에 옮기지 않는다(계약 §6).

    python3 scripts/taste_ledger.py --ledger calls_ledger.json --step 확장 --tool intent_finder --raw intent_r1_커피원두.json --purpose "연관 검색어 확장"
"""
import argparse, datetime, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from taste_words import utf8_console  # noqa: E402

PARAM_KEYS = ("keyword", "keywords", "gl", "data_type", "limit", "volume_threshold", "num", "device", "features",
              "sort", "order")


def params_of(rd):
    parts = []
    for k in PARAM_KEYS:
        if k in rd and rd[k] not in (None, "", []):
            v = rd[k]
            if isinstance(v, list):
                v = f"{len(v)}개" if len(v) > 3 else " · ".join(map(str, v))
            parts.append(f"{k}={v}")
    return " · ".join(parts) or "(request_detail 없음)"


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--step", required=True)
    ap.add_argument("--tool", required=True, choices=("keyword_info", "intent_finder", "serp"))
    ap.add_argument("--raw", required=True, help="저장한 원본 응답 파일")
    ap.add_argument("--purpose", default="")
    ap.add_argument("--reused-from", default="", help="저장본 재사용이면 원래 호출 ID 또는 파일 — 비용 0")
    ap.add_argument("--at", default="", help="호출 시각 (없으면 파일 수정 시각)")
    ap.add_argument("--estimate", type=float, help="이번 분석 견적(크레딧) — 원장에 한 번 적는다")
    a = ap.parse_args()

    led_p = pathlib.Path(a.ledger)
    led = json.loads(led_p.read_text(encoding="utf-8")) if led_p.is_file() else {"calls": [], "currency": "크레딧"}
    if a.estimate is not None:
        led["estimate"] = a.estimate
    raw_p = pathlib.Path(a.raw)
    obj = json.loads(raw_p.read_text(encoding="utf-8"))
    rd = obj.get("request_detail") or {}
    if a.reused_from:
        cost = 0
    else:
        cd = obj.get("cost_detail")
        if not isinstance(cd, dict) or not isinstance(cd.get("total_cost"), (int, float)):
            sys.exit(f"✗ {a.raw}: cost_detail.total_cost 가 없다 — 비용을 추정해 채우지 않는다. "
                     "저장본 재사용이면 --reused-from 을 준다")
        cost = cd["total_cost"]
    data = obj.get("data")
    returned = len(data) if isinstance(data, list) else None
    at = a.at or datetime.datetime.fromtimestamp(raw_p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    if any(c.get("raw") == a.raw and not a.reused_from for c in led["calls"]):
        sys.exit(f"✗ {a.raw} 는 이미 원장에 있다 — 같은 호출을 두 번 적지 않는다")
    cid = f"C{len(led['calls']) + 1}"
    status = "ok" if obj.get("result", "OK") in ("OK", "ok", None) else str(obj.get("result"))
    if a.tool == "intent_finder" and returned == 0:
        status = "empty"
    led["calls"].append({"id": cid, "step": a.step, "at": at, "tool": a.tool, "params": params_of(rd),
                         "purpose": a.purpose or a.tool, "returned": returned, "cost": cost, "raw": a.raw,
                         "data_through": obj.get("collected_at", "")[:10] or None, "status": status,
                         **({"reused_from": a.reused_from} if a.reused_from else {})})
    led_p.write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(c["cost"] for c in led["calls"])
    print(f"{cid} {a.tool} · 반환 {returned} · 비용 {cost} → 원장 {len(led['calls'])}건 · 합 {total:,} {led.get('currency', '크레딧')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
