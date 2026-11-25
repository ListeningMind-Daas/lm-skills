#!/usr/bin/env python3
"""수집 원장(ledger.json) — 저장한 응답마다 호출 한 줄을 남긴다 (collect_pool · screen_volume · forecast_segments 공용).

원장은 리포트의 호출 순서 · 호출 로그 · 비용 보고서(`provenance.calls`)의 유일한 출처다. 손으로 적지 않는다.
같은 원본 파일(raw)을 다시 넣으면 그 줄을 바꾼다 — 스크립트를 다시 돌려도 호출이 두 번 세지지 않는다.

비용은 응답 `cost_detail.total_cost`를 쓴다. 없으면 계약 결정식으로 계산하고 `cost_source` = «결정식»으로 남긴다.
시각은 응답의 `_saved_at`(저장할 때 붙인 값)을, 없으면 파일 수정 시각을 쓴다.
"""
import datetime, json, os, pathlib


def load(path):
    p = pathlib.Path(path)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"calls": [], "steps": {}}


def save(path, led):
    for i, c in enumerate(led["calls"], 1):
        c["id"] = f"C{i}"
    pathlib.Path(path).write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")


def saved_at(path, obj):
    if isinstance(obj, dict) and obj.get("_saved_at"):
        return str(obj["_saved_at"])
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")


def cost_of(obj, formula_value):
    """(비용, 출처). cost_detail 이 있으면 그것, 없으면 결정식 값."""
    cd = obj.get("cost_detail") if isinstance(obj, dict) else None
    if isinstance(cd, dict) and isinstance(cd.get("total_cost"), (int, float)):
        return cd["total_cost"], "cost_detail"
    return formula_value, "결정식"


def put(led, call):
    """raw 가 같은 줄은 바꾸고, 없으면 덧붙인다. 순서는 단계(step_order) → 원래 순서."""
    led["calls"] = [c for c in led["calls"] if c.get("raw") != call["raw"]] + [call]
    led["calls"].sort(key=lambda c: c.get("step_order", 9))
