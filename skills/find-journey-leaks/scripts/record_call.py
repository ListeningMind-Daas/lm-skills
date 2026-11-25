#!/usr/bin/env python3
"""호출 원장 calls.jsonl 에 호출 한 건을 적는다 (이 스킬 고유 · 0크레딧).

API를 부른 **직후** 원본 응답을 파일로 저장하고 이 스크립트로 원장에 남긴다. 비용 · 반환 수 · 고유 키워드 수 ·
절단 여부는 손으로 적지 않고 저장 응답에서 읽는다. journey_leaks.py 와 build_leak_report.py 는 이 원장만 믿는다.

    python3 scripts/record_call.py --ledger calls.jsonl --tool path_finder --role main --seed 런닝화 \\
        --limit 200 --raw raw/pf_런닝화.json --step "본 호출" --purpose "런닝화 출발 경로 표본"

role: seed_volume(시드 ads_metrics) · pilot(작은 limit 파일럿) · main(본 호출) · node_volume(노드 ads_metrics)

T7 — path_finder 는 «요청 limit»과 «반환 경로 수»를 함께 적고, 반환 ≥ limit 이면 truncated=true 다.
keyword_info 는 «요청 키워드 수»와 «반환 행 수»를 적고, 요청했는데 응답에 없는 키워드 수(missing)를 센다.
"""
import argparse, datetime, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from leak_lib import response_paths, total_cost, utf8_console  # noqa: E402
from series_dedup import canon  # noqa: E402


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="호출 원장에 한 건 추가 (0크레딧)")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--tool", required=True, choices=["path_finder", "keyword_info"])
    ap.add_argument("--role", required=True, choices=["seed_volume", "pilot", "main", "node_volume"])
    ap.add_argument("--raw", required=True, help="저장한 원본 응답(요청 헤더 · 키는 저장하지 않는다)")
    ap.add_argument("--seed", default="")
    ap.add_argument("--limit", type=int, default=None, help="path_finder 요청 limit")
    ap.add_argument("--time-point", default="curr")
    ap.add_argument("--gl", default="")
    ap.add_argument("--step", default="")
    ap.add_argument("--purpose", default="")
    ap.add_argument("--at", default="", help="호출 시각 (기본: 지금)")
    ap.add_argument("--reused-from", default="", help="저장본 재사용이면 원래 호출 ID · 파일 — 비용 0으로 적는다")
    a = ap.parse_args()

    led = pathlib.Path(a.ledger)
    raw_p = pathlib.Path(a.raw)
    resp = json.loads(raw_p.read_text(encoding="utf-8"))
    rd = resp.get("request_detail") or {}
    prev = [ln for ln in (led.read_text(encoding="utf-8").splitlines() if led.is_file() else []) if ln.strip()]
    cid = f"C{len(prev) + 1}"
    rel = str(raw_p.resolve().relative_to(led.resolve().parent)) if raw_p.resolve().is_relative_to(led.resolve().parent) \
        else str(raw_p.resolve())
    cost = total_cost(resp)
    row = {"id": cid, "step": a.step, "at": a.at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "tool": a.tool, "role": a.role, "seed": a.seed, "raw": rel.replace("\\", "/"), "status": "ok"}
    warn = []
    if a.tool == "path_finder":
        limit = a.limit if a.limit is not None else rd.get("limit")
        paths = response_paths(resp)
        returned = len((resp.get("data") or []))
        uniq = len({k for p in (resp.get("data") or []) if isinstance(p, list) for k in p})
        seed = a.seed or rd.get("keyword") or ""
        row.update({"seed": seed, "limit": limit, "returned": returned, "used_paths": len(paths),
                    "unique_keywords": uniq, "time_point": rd.get("time_point") or a.time_point,
                    "truncated": (None if limit is None else returned >= limit),
                    "params": f"keyword={seed} · gl={a.gl or rd.get('gl', '')} · limit={limit} · time_point={rd.get('time_point') or a.time_point}",
                    "cost_formula": 50 + 40 * uniq})
        if returned == 0:
            row["status"] = "empty"
        if limit is None:
            warn.append("limit 미상 — 절단 «판정 불가»로 남는다")
    else:
        req = list(rd.get("keywords") or [])
        dt = rd.get("data_type") or ""
        data = [r for r in resp.get("data") or [] if isinstance(r, dict)]
        got = {canon(r.get("keyword", "")) for r in data}
        missing = [k for k in req if canon(k) not in got]
        row.update({"requested": len(req), "returned": len(data), "missing": len(missing), "data_type": dt,
                    "params": f"keywords {len(req)}개 · gl={a.gl or rd.get('gl', '')} · data_type={dt}",
                    "cost_formula": 1 * len(req) if dt == "ads_metrics" else (10 * len(req) if dt == "all" else None)})
        if dt and dt != "ads_metrics":
            warn.append(f"data_type={dt} — 이 스킬은 시계열을 읽지 않아 ads_metrics(1/kw)로 충분하다. "
                        "ads_info 를 썼다면 단가를 cost_detail 로 확인할 것(계약 §1 v1.3)")
        if cost is None:
            cost = len(req)
            warn.append("cost_detail 없음 — 요청 키워드 수로 적었다")
    if a.reused_from:
        row["cost"], row["reused_from"] = 0, a.reused_from
    else:
        row["cost"] = cost if cost is not None else row.get("cost_formula")
        if cost is None:
            warn.append("cost_detail 없음 — 식 재계산값으로 적었다 [추정]")
    row["purpose"] = a.purpose
    with led.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    for w in warn:
        print(f"⚠ {w}")
    extra = ""
    if a.tool == "path_finder":
        extra = f" · limit {row['limit']} → 반환 {row['returned']}{' (절단)' if row['truncated'] else ''} · 고유 {row['unique_keywords']}"
    print(f"{cid} {a.tool}/{a.role} 기록 · 비용 {row['cost']}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
