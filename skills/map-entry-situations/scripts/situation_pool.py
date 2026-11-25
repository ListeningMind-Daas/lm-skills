#!/usr/bin/env python3
"""상황 후보 풀 — intent_finder 연관어 + path_finder 시드 앞 검색을 모은다 (이 스킬 고유 · API 호출 없음).

  - intent_finder 응답(키워드 문자열 배열)은 호출마다 파일 하나, `파일@limit`으로 받는다.
    반환 수 = limit 이면 그 호출은 **절단**이다 — 더 있을 수 있는데 받지 않았다(계약 T7)
  - path_finder 응답(경로 배열)에서 시드(canonical 일치)의 **첫 위치 앞 --pre-steps 걸음**만 후보로 뽑는다.
    방향은 요청 파라미터가 아니라 경로 안 위치로 정한다. 시드가 0경로에서 발견되면 시드 표기 변경
    (예: 선크림 → 썬크림)을 의심하고 멈춘다(종료 코드 3) — themes.json rules.변형치환에 규칙을 넣고 다시 돌린다
  - canonical(NFKC·소문자·변형치환·공백 제거)을 붙이고, sense.json exclude 에 걸린 검색어는 «경계 밖»으로
    행만 남기고 keyword_info 요청 목록에서 뺀다 — 다른 뜻의 검색어에 크레딧을 쓰지 않는다

    python3 situation_pool.py --sense sense.json --themes themes.json \
        --intent raw/if_선크림.json@100 --intent raw/if_여름선크림.json@100 \
        --paths raw/path_main.json@60 -o pool.csv --keywords-out pool_keywords.json --calls-out pool_calls.csv
"""
import argparse, csv, json, pathlib, sys
from collections import OrderedDict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cep_lib import (Lexicon, data_rows, load_json, load_sense, parse_spec, paths_of,  # noqa: E402
                     pre_seed, utf8_console)

POOL_COLS = ["keyword", "canonical", "source", "source_seed", "path_pre_paths", "anchor_hit",
             "boundary", "cep_hint", "kind_hint", "brand"]
CALL_COLS = ["file", "tool", "seed", "limit", "returned", "truncated", "usable_paths", "seed_found_paths"]


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="상황 후보 풀 조립 (0크레딧)")
    ap.add_argument("--sense", required=True); ap.add_argument("--themes", required=True)
    ap.add_argument("--intent", action="append", default=[], help="intent_finder 응답 파일@limit (호출마다)")
    ap.add_argument("--paths", action="append", default=[], help="path_finder 응답 파일@limit")
    ap.add_argument("--pre-steps", type=int, default=3, help="시드 앞 몇 걸음까지 후보로 볼지 [가정]")
    ap.add_argument("--allow-seed-missing", action="store_true")
    ap.add_argument("--allow-unconfirmed", action="store_true", help="selftest 용 — 실제 분석에서 쓰지 않는다")
    ap.add_argument("-o", "--out", default="pool.csv")
    ap.add_argument("--keywords-out", default="pool_keywords.json")
    ap.add_argument("--calls-out", default="pool_calls.csv")
    a = ap.parse_args()

    sense = load_sense(a.sense, a.allow_unconfirmed)
    lex = Lexicon(load_json(a.themes), sense)
    pool = OrderedDict()   # 원문 검색어 → 행

    def put(kw, source, seed, pre=0):
        kw = " ".join(str(kw).split())
        if not kw:
            return
        r = pool.get(kw)
        if r is None:
            c = lex.canon(kw)
            bucket, kind, _ = lex.classify(kw)
            r = pool[kw] = {"keyword": kw, "canonical": c, "source": source, "source_seed": seed,
                            "path_pre_paths": 0, "anchor_hit": "Y" if lex.anchor_hit(c) else "N",
                            "boundary": (f"경계 밖({lex.excluded(c)})" if lex.excluded(c) else ""),
                            "cep_hint": bucket, "kind_hint": kind, "brand": "|".join(lex.brands_in(c))}
        elif source not in r["source"].split("|"):
            r["source"] += "|" + source
        r["path_pre_paths"] += pre

    put(sense["keyword"], "seed", sense["keyword"])
    for s in sense.get("seeds") or []:
        put(s, "seed", s)

    calls = []
    for spec in a.intent:
        f, lim = parse_spec(spec)
        resp = load_json(f)
        req = resp.get("request_detail") or {}
        lim = lim if lim is not None else req.get("limit")
        seed = ",".join(req.get("keywords") or []) or pathlib.Path(f).stem
        kws = [k for k in data_rows(resp) if isinstance(k, str)]
        for k in kws:
            put(k, "intent", seed)
        calls.append({"file": f, "tool": "intent_finder", "seed": seed, "limit": lim if lim is not None else "",
                      "returned": len(kws), "truncated": ("판정 불가" if lim is None else ("Y" if len(kws) >= lim else "N")),
                      "usable_paths": "", "seed_found_paths": ""})

    stop = False
    for spec in a.paths:
        f, lim = parse_spec(spec)
        resp = load_json(f)
        req = resp.get("request_detail") or {}
        lim = lim if lim is not None else req.get("limit")
        ps = paths_of(resp)
        found = 0
        for p in ps:
            pre = pre_seed(p, lex, a.pre_steps)
            if pre is None:
                continue
            found += 1
            seen = set()
            for k in pre:
                put(k, "path_pre", sense["keyword"], 0)
                kk = " ".join(k.split())
                if kk not in seen:
                    pool[kk]["path_pre_paths"] += 1
                    seen.add(kk)
        calls.append({"file": f, "tool": "path_finder", "seed": req.get("keyword", sense["keyword"]),
                      "limit": lim if lim is not None else "", "returned": len(data_rows(resp)),
                      "truncated": ("판정 불가" if lim is None else ("Y" if len(data_rows(resp)) >= lim else "N")),
                      "usable_paths": len([p for p in ps if len(p) >= 2]), "seed_found_paths": found})
        if ps and found == 0:
            print(f"⚠️ {f}: 시드 «{sense['keyword']}»가 {len(ps)}경로 어디에도 없다 — path_finder가 시드 표기를 "
                  "바꿔 돌려줬을 수 있다(예: 선크림 → 썬크림). themes.json rules.변형치환에 규칙을 넣고 다시 돌릴 것")
            stop = not a.allow_seed_missing

    if stop:
        sys.exit(3)

    rows = list(pool.values())
    with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=POOL_COLS); w.writeheader(); w.writerows(rows)
    with open(a.calls_out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CALL_COLS); w.writeheader(); w.writerows(calls)
    send = [r["keyword"] for r in rows if not r["boundary"]]
    json.dump(send, open(a.keywords_out, "w", encoding="utf-8"), ensure_ascii=False)

    trunc = [c for c in calls if c["truncated"] == "Y"]
    out_b = [r for r in rows if r["boundary"]]
    print(f"후보 {len(rows)}개 (intent {sum('intent' in r['source'] for r in rows)} · "
          f"시드 앞 검색 {sum('path_pre' in r['source'] for r in rows)}) · 경계 밖 {len(out_b)}개 제외 → "
          f"keyword_info 요청 {len(send)}개 = {len(send)} 크레딧(ads_metrics 1 × N)")
    for c in calls:
        extra = f" · 시드 발견 경로 {c['seed_found_paths']}/{c['usable_paths']}" if c["tool"] == "path_finder" else ""
        print(f"  {c['tool']} {c['seed']}: 반환 {c['returned']} / limit {c['limit'] or '?'} · 절단 {c['truncated']}{extra}")
    if trunc:
        print(f"절단 호출 {len(trunc)}건 — 수집 범위 «보류»: 이 호출의 결과는 반환분 기준이다(계약 T7). "
              "더 받으려면 limit을 올려 다시 부른다(크레딧 재소모 — 사용자 결정)")
    if out_b:
        print("경계 밖(뜻 고정 exclude): " + ", ".join(r["keyword"] for r in out_b[:8]))
    print(f"→ {a.out} · {a.keywords_out} · {a.calls_out}")


if __name__ == "__main__":
    main()
