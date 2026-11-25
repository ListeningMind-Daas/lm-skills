#!/usr/bin/env python3
"""수집 원장 — 저장한 API 응답마다 한 줄씩 호출 기록을 남긴다 (이 스킬 고유 · API 호출 없음).

리포트의 호출 순서 · 호출 로그 · 비용 보고서(report.json `provenance.calls`)는 이 원장에서 만든다.
손으로 적지 않는다(렌더링 계약 §9). 비용은 응답 `cost_detail.total_cost`를 그대로 옮긴다 —
계정 사용량(`used_credits`)은 월간 누적이라 이번 분석 소진으로 쓰지 않는다(계약 «크레딧 용어»).

    # 호출 1건을 저장한 직후
    python3 ledger.py add --ledger ledger.jsonl --tool intent_finder --step 확장 \
        --raw raw/if_선크림.json --params "keyword=선크림 · limit 100 · gl=kr" --purpose "연관 검색어"
    # 저장본을 다시 쓸 때(0크레딧)
    python3 ledger.py add --ledger ledger.jsonl --tool keyword_info --step 재계산 \
        --raw raw/metrics_all.json --reused-from C5 --purpose "사전 수정 후 재집계"
    # keyword_info 를 1,000개씩 나눠 받았을 때 — 계약 «응답 병합» 규칙대로 합친다
    python3 ledger.py merge -o metrics_ads.json raw/ads_1.json raw/ads_2.json
    # 이번 분석 소진 합계
    python3 ledger.py total --ledger ledger.jsonl
"""
import argparse, datetime, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cep_lib import data_rows, load_json, utf8_console  # noqa: E402


def read(ledger):
    p = pathlib.Path(ledger)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def formula_cost(tool, resp):
    """cost_detail 이 없을 때의 결정식 재계산 — 상태에 «식으로 대체»를 남긴다."""
    data = data_rows(resp)
    req = resp.get("request_detail") or {}
    if tool == "intent_finder":
        return 30 * max(1, len(req.get("keywords") or [1])) + 2 * len(data)
    if tool == "path_finder":
        return 50 + 40 * len({k for p in data if isinstance(p, list) for k in p})
    if tool == "keyword_info":
        n = len(req.get("keywords") or data)
        return (10 if req.get("data_type") == "all" else 1) * n
    return 0


def cmd_add(a):
    resp = load_json(a.raw)
    rows = read(a.ledger)
    cid = f"C{len(rows) + 1}"
    data = data_rows(resp)
    months = [m.get("month") for r in data if isinstance(r, dict)
              for m in (r.get("monthly_volume") or []) if isinstance(m, dict) and m.get("month")]
    status = "ok" if data else "빈 응답 [데이터 공백]"
    if a.reused_from:
        cost = 0
    elif isinstance((resp.get("cost_detail") or {}).get("total_cost"), (int, float)):
        cost = resp["cost_detail"]["total_cost"]
    else:
        cost = formula_cost(a.tool, resp)
        status += " · cost_detail 없음 — 결정식으로 대체 [추정]"
    req = resp.get("request_detail") or {}
    params = a.params or " · ".join(f"{k}={v if not isinstance(v, list) else f'{len(v)}개'}"
                                    for k, v in req.items())
    row = {"id": cid, "step": a.step, "at": a.at or datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "tool": a.tool, "params": params, "purpose": a.purpose, "returned": len(data),
           "cost": cost, "raw": a.raw, "data_through": max(months) if months else None, "status": status}
    if a.limit is not None:
        row["limit"] = a.limit
        row["truncated"] = len(data) >= a.limit
    if a.reused_from:
        row["reused_from"] = a.reused_from
    with open(a.ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{cid} {a.tool} · 반환 {len(data)} · 비용 {cost}" + (" · 절단(반환 = limit)" if row.get("truncated") else ""))


def cmd_total(a):
    rows = read(a.ledger)
    by = {}
    for r in rows:
        by[r["tool"]] = by.get(r["tool"], 0) + r["cost"]
    print(f"호출 {len(rows)}건 · 이번 분석 소진 {sum(r['cost'] for r in rows):,} (cost_detail 합) · "
          + " · ".join(f"{k} {v:,}" for k, v in by.items()))


def cmd_merge(a):
    parts = [load_json(p) for p in a.parts]
    gl = {(p.get("request_detail") or {}).get("gl") for p in parts}
    dt = {(p.get("request_detail") or {}).get("data_type") for p in parts}
    if len(gl) > 1 or len(dt) > 1:
        sys.exit(f"gl · data_type 이 부분마다 다르다 — 합치지 않는다: gl={gl} data_type={dt}")
    out = {"_merged_from": a.parts,
           "request_detail": {"keywords": [k for p in parts for k in (p.get("request_detail") or {}).get("keywords") or []],
                              "gl": gl.pop(), "data_type": dt.pop()},
           "cost_detail": {"total_cost": sum((p.get("cost_detail") or {}).get("total_cost") or 0 for p in parts),
                           "parts": [p.get("cost_detail") for p in parts]},
           "data": [r for p in parts for r in data_rows(p)]}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(parts)}개 응답 → {a.out} · 행 {len(out['data'])} · 비용 {out['cost_detail']['total_cost']}")


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="수집 원장 (0크레딧)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add")
    p.add_argument("--ledger", required=True); p.add_argument("--tool", required=True)
    p.add_argument("--step", required=True); p.add_argument("--raw", required=True)
    p.add_argument("--params", default=""); p.add_argument("--purpose", default="")
    p.add_argument("--at", default=""); p.add_argument("--limit", type=int)
    p.add_argument("--reused-from", default="")
    p = sp.add_parser("total"); p.add_argument("--ledger", required=True)
    p = sp.add_parser("merge"); p.add_argument("-o", "--out", required=True); p.add_argument("parts", nargs="+")
    a = ap.parse_args()
    {"add": cmd_add, "total": cmd_total, "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    main()
