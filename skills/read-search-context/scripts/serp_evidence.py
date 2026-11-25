#!/usr/bin/env python3
"""serp 원본 응답 → 검색 목적 해석에 쓸 근거 (이 스킬 고유 스크립트). API 호출 없음.

검색어마다 AI 개요 본문과 검색 결과 1~3위만 남긴다. serp 는 요청 시점의 화면이 아니라 **저장된 화면**을
돌려주므로 수집일(collected_at)을 반드시 함께 적는다. 저장본이 없는 검색어는 빈 응답(`data: []`)으로 온다 —
실패가 아니라 [데이터 공백]이다.

    python3 scripts/serp_evidence.py --targets out/serp_targets.csv --serp-dir raw/serp --out out/evidence.json

파일 이름은 `<코드>.json` (예: raw/serp/E1.json). 근거 번호는 `E2-AI`(AI 개요) · `E2-1`(1위) 형식이다.
"""
import argparse, csv, json, pathlib, sys

EVIDENCE_KEYS = ["code", "keyword", "status", "collected_at", "aio", "aio_refs", "top"]


def aio_lines(block):
    """AI 개요 본문 — 문단과 목록(목록 안의 목록 포함)을 화면 순서대로 줄로 편다."""
    out = []

    def walk(node, depth):
        s = str(node.get("snippet") or "").strip()
        if s:
            out.append("  " * depth + ("- " if depth else "") + s)
        for child in node.get("list") or []:
            if isinstance(child, dict):
                walk(child, depth + 1)
    for tb in block.get("text_blocks") or []:
        if isinstance(tb, dict):
            walk(tb, 0)
    return out


def trim(resp):
    if isinstance(resp, list):
        resp = {"data": resp}
    if not isinstance(resp, dict):
        return {"status": "error", "reason": "응답 형식을 읽지 못함"}
    if resp.get("result") not in (None, "OK", "SUCCESS"):
        return {"status": "error", "reason": str(resp.get("reason") or resp.get("result"))}
    data = [b for b in resp.get("data") or [] if isinstance(b, dict)]
    if not data:
        return {"status": "no_snapshot", "collected_at": resp.get("collected_at")}
    ab = next((b for b in data if b.get("type") == "ai_overview"), None)
    org = sorted((b for b in data if b.get("type") == "organic_results"),
                 key=lambda b: b.get("position") if isinstance(b.get("position"), int) else 99)[:3]
    return {"status": "ok", "collected_at": resp.get("collected_at"),
            "aio": aio_lines(ab) if ab else [],
            "aio_refs": [{"title": str(r.get("title") or ""), "source": str(r.get("source") or ""), "url": str(r.get("url") or "")}
                         for r in (ab.get("references") if ab else None) or [] if isinstance(r, dict)][:10],
            "top": [{"position": b.get("position"), "site": str(b.get("site_name") or ""), "title": str(b.get("title") or ""),
                     "url": str(b.get("url") or ""), "snippet": str(b.get("snippet") or "")} for b in org]}


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--serp-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = []
    for t in csv.DictReader(open(a.targets, encoding="utf-8-sig")):
        f = pathlib.Path(a.serp_dir) / f"{t['code']}.json"
        base = {"code": t["code"], "keyword": t["keyword"], "status": None, "collected_at": None, "aio": [], "aio_refs": [], "top": []}
        if not f.is_file():
            out.append({**base, "status": "not_called"})
            continue
        try:
            out.append({**base, **trim(json.loads(f.read_text(encoding="utf-8")))})
        except (OSError, ValueError) as e:
            out.append({**base, "status": "error", "reason": str(e)[:120]})
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n = lambda st: sum(1 for x in out if x["status"] == st)
    print(f"SERP 대상 {len(out)}개 · 저장본 있음 {n('ok')} · AI 개요 있음 {sum(1 for x in out if x['aio'])} [실제 데이터]")
    print(f"저장본 없음 {n('no_snapshot')} · 부르지 않음 {n('not_called')} [데이터 공백] · 오류 {n('error')} → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
