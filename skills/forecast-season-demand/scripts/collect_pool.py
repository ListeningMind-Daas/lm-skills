#!/usr/bin/env python3
"""STEP 2~3 · intent_finder 응답 → 검색어 풀 (pool.json · pool.csv) + 수집 원장.

  python3 scripts/collect_pool.py --seeds seeds.json --gl kr \
      --intent raw/if_선크림.json@100 --intent raw/if_선스틱.json@100 \
      --themes themes.json -o pool.json --csv pool.csv --ledger ledger.json

- 시드는 1~5개(문자열 배열). 0개 · 6개 이상이면 exit 2 — STEP 1로 돌아간다.
- `--intent 파일[@limit]` 은 시드마다 한 건. 시드는 응답 `request_detail.keywords`에서, limit 은 `@limit` 또는
  `request_detail.limit` 에서 읽는다. limit 을 모르면 절단 여부를 «미상»으로 남긴다 `[데이터 공백]`.
- **절단(계약 T7)**: 반환 수 ≥ limit 이면 그 시드의 연관 검색어는 잘린 것이다. 풀은 «상위 limit 개 표본»이고
  카테고리 전체가 아니다 — `ledger.steps.collect.truncated` 에 남아 리포트 범위 · 한계에 옮겨진다.
- **표기변형(계약 T5)**: `series_dedup.canon`(themes.json `rules.변형치환`)으로 같은 canonical 을 한 줄로 묶는다.
  대표는 먼저 나온 표기(시드가 먼저)다. 묶인 표기는 status «표기변형 → 대표»로 행이 남는다.
- **범위 밖**: themes.json `exclude.pattern`(canonical 에 대한 정규식)에 걸린 검색어는 status «범위 밖»으로 남기고
  keyword_info 에 보내지 않는다. 패턴은 STEP 3에서 사람이 확인한다 — 모델이 판단하지 않는다.
- 크레딧: intent_finder 호출마다 `30 × 입력(1) + 2 × 반환`. cost_detail 과 다르면 «환경 단가 확인»을 띄운다.
"""
import argparse, csv, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, load_rules  # noqa: E402
import ledger as L  # noqa: E402

POOL_COLS = ["keyword", "canonical", "rep", "origin_seeds", "first_rank", "status", "exclude_match"]


def load_json(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--gl", required=True, choices=("kr", "jp", "us"))
    ap.add_argument("--intent", action="append", default=[], help="intent_finder 응답 파일[@limit] — 시드마다 한 건")
    ap.add_argument("--themes", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--ledger", required=True)
    a = ap.parse_args()

    seeds = [" ".join(s.split()) for s in load_json(a.seeds) if isinstance(s, str) and s.strip()]
    if not seeds:
        print("시드가 없다 — STEP 1에서 시드를 정한 뒤 다시 돌린다(카테고리 이름 하나라도 좋다)")
        return 2
    if len(seeds) > 5:
        print(f"시드 {len(seeds)}개 — 최대 5개다. 카테고리를 대표하는 5개로 줄인다(크레딧 30 × 시드 + 2 × 반환)")
        return 2
    themes = load_json(a.themes)
    rules = load_rules(a.themes)
    ex = (themes.get("exclude") or {}).get("pattern") or ""
    ex_re = re.compile(ex) if ex else None

    rows, by_canon = [], {}

    def add(kw, seed, rank):
        kw = " ".join(str(kw).split())
        if not kw:
            return
        c = canon(kw, rules)
        if c in by_canon:
            r = by_canon[c]
            if seed not in r["origin_seeds"]:
                r["origin_seeds"].append(seed)
            if kw != r["keyword"] and kw not in {x["keyword"] for x in rows}:
                rows.append({"keyword": kw, "canonical": c, "rep": r["keyword"], "origin_seeds": [seed],
                             "first_rank": rank, "status": f"표기변형 → {r['keyword']}", "exclude_match": ""})
            return
        m = ex_re.search(c) if ex_re else None
        r = {"keyword": kw, "canonical": c, "rep": kw, "origin_seeds": [seed], "first_rank": rank,
             "status": "범위 밖" if m else "포함", "exclude_match": m.group(0) if m else ""}
        by_canon[c] = r
        rows.append(r)

    for s in seeds:
        add(s, s, 0)

    led = L.load(a.ledger)
    calls, truncated, unknown_limit = [], [], []
    seen_seeds = set()
    for spec in a.intent:
        path, _, lim = spec.partition("@")
        obj = load_json(path)
        rd = (obj.get("request_detail") or {}) if isinstance(obj, dict) else {}
        data = obj.get("data", []) if isinstance(obj, dict) else obj
        kws = [x if isinstance(x, str) else (x or {}).get("keyword") for x in data]
        kws = [k for k in kws if k]
        seed = (rd.get("keywords") or [None])[0] or (rd.get("keyword"))
        if not seed:
            print(f"✗ {path}: request_detail.keywords 가 없다 — 어느 시드의 응답인지 알 수 없다")
            return 2
        seed = " ".join(seed.split())
        if seed not in seeds:
            print(f"⚠️ {path}: 시드 «{seed}»가 seeds.json 에 없다 — 시드 목록에 넣거나 파일을 뺀다")
        seen_seeds.add(seed)
        limit = int(lim) if lim else rd.get("limit")
        cut = None if limit is None else len(kws) >= int(limit)
        if cut:
            truncated.append(seed)
        if limit is None:
            unknown_limit.append(seed)
        for i, k in enumerate(kws, 1):
            add(k, seed, i)
        formula = 30 * 1 + 2 * len(kws)
        cost, src = L.cost_of(obj, formula)
        calls.append({"seed": seed, "limit": limit, "returned": len(kws), "truncated": cut,
                      "cost": cost, "formula": formula})
        L.put(led, {"step": "STEP 2 연관 검색어", "step_order": 2, "at": L.saved_at(path, obj),
                    "tool": "intent_finder", "params": f"seed={seed} · limit {limit if limit is not None else '미상'} · gl={a.gl}",
                    "purpose": "시드의 연관 검색어로 풀 만들기", "returned": len(kws), "cost": cost,
                    "cost_source": src, "formula": f"30 × 1 + 2 × {len(kws)} = {formula}",
                    "raw": str(path).replace("\\", "/"), "data_through": None,
                    "status": "ok" if kws else "empty"})

    missing = [s for s in seeds if s not in seen_seeds]
    if missing:
        print(f"⚠️ intent_finder 응답이 없는 시드: {', '.join(missing)} — 시드 자체만 풀에 들어간다")

    keep = [r["keyword"] for r in rows if r["status"] == "포함"]
    pathlib.Path(a.out).write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=POOL_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "origin_seeds": " / ".join(r["origin_seeds"])})

    n_var = sum(1 for r in rows if r["status"].startswith("표기변형"))
    n_ex = sum(1 for r in rows if r["status"] == "범위 밖")
    f_sum = sum(c["formula"] for c in calls)
    c_sum = sum(c["cost"] for c in calls)
    led["steps"]["collect"] = {"seeds": seeds, "gl": a.gl, "calls": calls, "truncated": truncated,
                               "unknown_limit": unknown_limit, "n_raw": len(rows), "n_variants": n_var,
                               "n_excluded": n_ex, "n_pool": len(keep), "exclude_pattern": ex,
                               "excluded": [r["keyword"] for r in rows if r["status"] == "범위 밖"],
                               "credit_formula": f_sum, "credit_actual": c_sum}
    L.save(a.ledger, led)

    print(f"시드 {len(seeds)}개 · intent_finder {len(calls)}건 · 반환 합 {sum(c['returned'] for c in calls)}")
    print(f"풀 {len(keep)}개 (표기변형 묶음 {n_var}행 · 범위 밖 {n_ex}행) → {a.out}")
    for c in calls:
        tag = "절단 — 반환 = limit" if c["truncated"] else ("limit 미상 — 절단 판정 불가 [데이터 공백]"
                                                           if c["truncated"] is None else "절단 아님")
        print(f"  {c['seed']}: 반환 {c['returned']} / limit {c['limit']} · {tag}")
    if truncated:
        print(f"⚠️ 절단(T7): {', '.join(truncated)} — 풀은 연관 검색어 상위 표본이다. «카테고리 전체 수요»라고 쓰지 않는다")
    if f_sum != c_sum:
        print(f"⚠️ 크레딧 결정식 {f_sum} ≠ cost_detail 합 {c_sum} — 환경 단가 확인")
    else:
        print(f"크레딧 intent_finder: 결정식 {f_sum} = cost_detail 합 {c_sum}")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
