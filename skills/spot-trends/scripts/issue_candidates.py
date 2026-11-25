#!/usr/bin/env python3
"""trend_finder/issue_list 응답 → 검색량 판정용 후보 목록 + 이슈 근거 CSV + 수집 사실(meta). SKILL.md STEP 1·3.

이슈는 검색량이 아니다. 이 스크립트는 «무엇을 keyword_info로 잴지»만 정하고, 판정은 trend_signals.py가 한다.

  추출   python3 issue_candidates.py tf_14d.json [tf_90d.json ...] -o candidates.json \\
             --evidence-out issue_evidence.csv [--meta-out issue_meta.json] [--rules '{"썬":"선"}'] \\
             [--exclude '캠핑장|차박'] [--user-candidates mine.json] [--no-seed]
         · 기간은 응답 request_detail.period_days에서 읽는다(없으면 `파일@90`으로 붙인다)
         · 시드 자체를 후보 맨 앞에 넣는다(기본) — 빼려면 --no-seed
         · --exclude(범위 밖 정규식)에 걸린 이슈는 후보에서 빼되 근거 CSV에는 남긴다(trend_signals.py가 «범위 밖 제외»로 센다)
         · 넓힘 판정은 **범위 안 이슈 수**로 한다 — 같은 시드의 범위 안 이슈가 0이고 가장 넓은 기간이 90일 미만이면 exit 3
         · 띄어쓴 시드는 단어 하나만 맞아도 매칭된다(계약 §1) — 경고하고 붙여 쓴 형태 조회를 제안한다
  스크린 python3 issue_candidates.py --screen ads_metrics.json --candidates candidates.json \\
             --gate 3000 --window 3 -o all_candidates.json [--meta issue_meta.json] [--rules …]
         · volume_avg × window ≥ gate [가정]인 후보만 `all`(10/kw)로 보낸다 — 측정된 표기 중 volume_avg가 가장 큰 것으로
         · 같은 canonical(--rules 치환 포함)의 응답 행은 한 후보로 묶고, 지표까지 같은 행(series_dedup.dedup_map)은 같은 검색으로 병합한다
         · 탈락·미반환은 버리지 않는다 — meta의 screen.rows에 volume_avg와 함께 남고 trend_signals.py가 사유별로 적는다

크레딧 결정식(계약 §1): trend_finder = 50 × 결과가 있는 호출 + 2 × 반환 이슈 — 빈 결과 호출은 0(환경마다 확인).
예산 상한은 호출마다 50 + 2 × limit로 잡는다.
"""
import argparse, csv, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, dedup_map, load_rules  # noqa: E402  계약 T5

EVIDENCE_COLS = ["seed", "gl", "keyword", "canonical", "variants", "issue_score_max", "n_items",
                 "n_index_dates", "index_date_first", "index_date_last", "content_types", "topics",
                 "found_in_periods", "summary_ai", "source_checked", "source_links"]
WIDEN_TO = 90          # 범위 안 이슈가 없으면 여기까지 한 번 넓힌다 (365는 선택)
TF_SEED, TF_ITEM = 50, 2


def load_response(spec):
    path, _, days = spec.partition("@")
    o = json.load(open(path, encoding="utf-8"))
    req = o.get("request_detail") or {}
    period = int(days) if days else req.get("period_days")
    if period is None:
        sys.exit(f"{path}: 기간을 알 수 없다 — request_detail.period_days가 없으면 `{path}@14`처럼 붙인다")
    items = [d for d in (o.get("data") or []) if isinstance(d, dict) and d.get("keyword")]
    cost = (o.get("cost_detail") or {}).get("total_cost")
    return {"path": path, "seed": req.get("keyword", ""), "gl": req.get("gl", ""), "period": int(period),
            "items": items, "total": o.get("total"), "limit": req.get("limit"), "cost": cost}


def as_list(v):
    return [x for x in v if x] if isinstance(v, list) else ([v] if v else [])


def excluder(pattern):
    pat = re.compile(pattern) if pattern else None
    return (lambda kw: bool(pat and pat.search(kw)))


def collect_notes(resp, out_of):
    """시드별 넓힘·절단·띄어쓰기 판정. 넓힘은 범위 안 이슈 수로 한다(P1-4)."""
    need_widen, notes, spaced = [], [], []
    for seed in dict.fromkeys(r["seed"] for r in resp):
        rs = [r for r in resp if r["seed"] == seed]
        tried = "·".join(str(r["period"]) for r in rs)
        n_in = {r["period"]: sum(1 for it in r["items"] if not out_of(it["keyword"])) for r in rs}
        if " " in seed.strip():
            spaced.append(seed)
            notes.append(f"⚠️ 띄어쓴 시드 «{seed}» — 단어 하나만 맞아도 이슈를 가져온다(계약 §1). 범위 안 이슈 수로 판단하고, "
                         f"붙여 쓴 «{seed.replace(' ', '')}»도 조회하면 비교할 수 있다(상한 {TF_SEED} + {TF_ITEM} × limit)")
        for r in rs:
            n_ex = len(r["items"]) - n_in[r["period"]]
            if r["items"]:
                notes.append(f"{seed} {r['period']}일: 반환 {len(r['items'])}건 · 범위 안 {n_in[r['period']]}건"
                             + (f" · 범위 밖 {n_ex}건(--exclude)" if n_ex else "") + " [실제 데이터]")
            # 절단은 반환이 limit 에 닿았을 때만 — total 이 커도 반환이 limit 미만이면 절단이 아니다
            if r["total"] is not None and r["total"] > len(r["items"]) and (r["limit"] is None or len(r["items"]) >= r["limit"]):
                notes.append(f"⚠️ {seed} {r['period']}일: 매칭 {r['total']}건 중 {len(r['items'])}건 반환 — limit 절단 (T7) · "
                             "후보가 전부가 아니다")
        if any(n_in.values()):
            first = next(r for r in rs if n_in[r["period"]])
            if first is not rs[0]:
                notes.append(f"{seed}: {tried}일 조회 — {rs[0]['period']}일 범위 안 이슈 0건 → {first['period']}일로 넓혀 "
                             f"{n_in[first['period']]}건 [실제 데이터]")
        elif max(r["period"] for r in rs) < WIDEN_TO:
            need_widen.append(seed)
            notes.append(f"⚠️ {seed}: {tried}일 범위 안 이슈 0건 — period_days={WIDEN_TO}로 한 번 더 조회한 뒤 결론을 낸다 "
                         f"(상한 {TF_SEED} + {TF_ITEM} × limit · 빈 결과면 0). 이슈를 지어내지 않는다")
        else:
            notes.append(f"{seed}: {tried}일 모두 범위 안 이슈 0건 → «이슈 없음» [실제 데이터 — 그 기간 매칭 없음]"
                         + ("" if max(r["period"] for r in rs) >= 365 else " · 365일은 선택"))
    return need_widen, notes, spaced


def credit_check(resp):
    n_ret = sum(len(r["items"]) for r in resp)
    n_hit = sum(1 for r in resp if r["items"])
    formula = TF_SEED * n_hit + TF_ITEM * n_ret
    upper = sum(TF_SEED + TF_ITEM * (r["limit"] or len(r["items"])) for r in resp)
    actual = [r["cost"] for r in resp if r["cost"] is not None]
    match = bool(actual) and len(actual) == len(resp) and sum(actual) == formula
    line = (f"크레딧(trend_finder): 결정식 {TF_SEED}×{n_hit}호출(결과 있음) + {TF_ITEM}×{n_ret}반환 = {formula}"
            f" · 빈 결과 호출 {len(resp) - n_hit}건은 0 · 호출 전 상한 {upper}")
    if actual:
        line += f" · cost_detail 합 {sum(actual)}" + ("" if match else " ⚠️ 결정식과 다르다 — 환경 단가 확인")
    else:
        line += " · cost_detail 없음"
    return {"formula": formula, "upper": upper, "actual": sum(actual) if actual else None, "match": match, "line": line}


def extract(a):
    rules, out_of = load_rules(a.rules), excluder(a.exclude)
    resp = sorted((load_response(s) for s in a.responses), key=lambda r: (r["seed"], r["period"]))
    need_widen, notes, spaced = collect_notes(resp, out_of)

    ev = {}
    for r in resp:
        for it in sorted(r["items"], key=lambda x: -(x.get("issue_score") or 0)):
            c = canon(it["keyword"], rules)
            e = ev.setdefault(c, {"seed": r["seed"], "gl": r["gl"], "keyword": it["keyword"], "canonical": c,
                                  "_var": [], "_score": [], "_dates": set(), "_ct": [], "_tp": [], "_per": [],
                                  "_sum": as_list(it.get("summary")), "_src": []})
            for k, v in (("_var", it["keyword"]), ("_per", r["period"])):
                if v not in e[k]:
                    e[k].append(v)
            e["_score"].append(it.get("issue_score") or 0)
            e["_dates"].update(as_list(it.get("index_date")))
            e["_ct"] += [x for x in as_list(it.get("content_type")) if x not in e["_ct"]]
            e["_tp"] += [x for x in as_list(it.get("topic")) if x not in e["_tp"]]
            e["_src"] += [x for x in as_list(it.get("source_links")) if x not in e["_src"]]
    rows = []
    for e in sorted(ev.values(), key=lambda e: -max(e["_score"])):
        d = sorted(e["_dates"])
        rows.append({"seed": e["seed"], "gl": e["gl"], "keyword": e["keyword"], "canonical": e["canonical"],
                     "variants": " / ".join(e["_var"]), "issue_score_max": round(max(e["_score"]), 4),
                     "n_items": len(e["_score"]), "n_index_dates": len(d),
                     "index_date_first": d[0] if d else "", "index_date_last": d[-1] if d else "",
                     "content_types": " / ".join(e["_ct"]), "topics": " / ".join(e["_tp"]),
                     "found_in_periods": " / ".join(map(str, e["_per"])),
                     "summary_ai": " / ".join(e["_sum"][:a.max_sentences]), "source_checked": "",
                     "source_links": " | ".join(e["_src"][:a.max_sources])})
    excluded = [r["keyword"] for r in rows if any(out_of(v) for v in r["variants"].split(" / "))]

    user = []
    if a.user_candidates:
        u = json.load(open(a.user_candidates, encoding="utf-8"))
        user = [k if isinstance(k, str) else k.get("keyword") for k in (u.get("data", u) if isinstance(u, dict) else u)]
    seeds = [] if a.no_seed else [s for s in dict.fromkeys(r["seed"] for r in resp) if s and not out_of(s)]
    cands, seen = [], set()
    for k in seeds + [r["keyword"] for r in rows if r["keyword"] not in excluded] + [k for k in user if k]:
        if canon(k, rules) not in seen:
            seen.add(canon(k, rules)); cands.append(k)
    pathlib.Path(a.out).write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(a.evidence_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=EVIDENCE_COLS); w.writeheader(); w.writerows(rows)

    credit = credit_check(resp)
    n_raw = sum(len(r["items"]) for r in resp)
    for n in notes:
        print(n)
    print(f"이슈 {n_raw}건 → canonical {len(rows)}개 (표기변형·반복 {n_raw - len(rows)}건 병합 [추정]"
          + (f" · --rules {len(rules)}쌍" if rules else "") + ")"
          + (f" · 범위 밖 {len(excluded)}개(후보에서 뺌 · 근거 CSV에는 남김)" if excluded else "")
          + f" · 시드 {len(seeds)}개{' (--no-seed)' if a.no_seed else ''} · 사용자 후보 {len(user)}개"
          + f" · keyword_info 후보 {len(cands)}개")
    print(credit["line"])
    print(f"다음: keyword_info ads_metrics 1×{len(cands)} = {len(cands)} → 스크린 통과분만 all 10×M")
    meta_out = a.meta_out or str(pathlib.Path(a.evidence_out).with_name("issue_meta.json"))
    meta = {"seeds": list(dict.fromkeys(r["seed"] for r in resp)), "spaced_seeds": spaced,
            "calls": [{"seed": r["seed"], "period": r["period"], "returned": len(r["items"]),
                       "in_scope": sum(1 for it in r["items"] if not out_of(it["keyword"])),
                       "total": r["total"], "limit": r["limit"], "cost": r["cost"]} for r in resp],
            "notes": notes, "credit": credit, "exclude": a.exclude or "", "rules": rules,
            "n_raw": n_raw, "n_canonical": len(rows), "excluded": excluded, "seed_candidates": seeds,
            "n_candidates": len(cands), "need_widen": need_widen}
    pathlib.Path(meta_out).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {a.out} · {a.evidence_out} · {meta_out}  (summary_ai는 AI 생성 요약 [추정] — 출처로 확인한 문장만 source_checked에)")
    return 3 if need_widen else 0


def screen(a):
    """ads_metrics 스크린 · 표기변형 묶음(--rules) · 같은 검색 병합(dedup_map) — P1-5."""
    rules = load_rules(a.rules)
    cands = json.load(open(a.candidates, encoding="utf-8"))
    cands = [k if isinstance(k, str) else k.get("keyword") for k in cands]
    o = json.load(open(a.screen, encoding="utf-8"))
    data = [d for d in (o.get("data", o) if isinstance(o, dict) else o) if isinstance(d, dict) and d.get("keyword")]
    avg = lambda d: (d.get("ads_metrics") or {}).get("volume_avg") or 0
    by = {}
    for d in data:
        by.setdefault(canon(d["keyword"], rules), []).append(d)
    dm = dedup_map(data, rules)
    rows, keep, merges, taken = [], [], [], {}
    for k in cands:
        ds = sorted(by.get(canon(k, rules), []), key=lambda d: -avg(d))
        row = {"keyword": k, "canonical": canon(k, rules), "response_keywords": " / ".join(d["keyword"] for d in ds),
               "volume_avg": "", "volume_avg_hi": "", "screen": "미반환", "merged_into": "", "sent_as": ""}
        rows.append(row)
        if not ds:
            continue
        uniq = {dm.get(d["keyword"], {}).get("rep", d["keyword"]): avg(d) for d in ds}
        row["volume_avg"], row["volume_avg_hi"] = avg(ds[0]), sum(uniq.values())
        if len(ds) > 1:
            merges.append(f"표기변형 묶음 [추정]: {k} ← {row['response_keywords']} · volume_avg 하한 {avg(ds[0]):,} ~ "
                          f"상한 {row['volume_avg_hi']:,} (같은 검색의 중복은 한 번만)")
        owner = next((taken[r] for r in uniq if r in taken), None)
        if owner:
            row["screen"], row["merged_into"] = "병합", owner
            merges.append(f"같은 검색 병합 [추정 — 지표 일치]: {k} → {owner} (all에 한 번만 보낸다)")
            continue
        for r in uniq:
            taken[r] = k
        if avg(ds[0]) * a.window >= a.gate:
            row["screen"], row["sent_as"] = "통과", ds[0]["keyword"]
            keep.append(ds[0]["keyword"])
        else:
            row["screen"] = "탈락"
    pathlib.Path(a.out).write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    cnt = lambda s: sum(1 for r in rows if r["screen"] == s)
    summary = (f"스크린 [가정 volume_avg × {a.window} ≥ {a.gate:,}]: 후보 {len(cands)} → all 대상 {len(keep)} · "
               f"탈락 {cnt('탈락')}(볼륨 측정됨) · 미반환 {cnt('미반환')} [데이터 공백] · 같은 검색 병합 {cnt('병합')}")
    print(summary)
    for m in merges:
        print("  " + m)
    print("  탈락·미반환은 버리지 않는다 — trend_signals.py가 사유별(스크린 탈락 · 검색량 미반환)로 volume_avg와 함께 적는다")
    ads_cost = (o.get("cost_detail") or {}).get("total_cost")
    print(f"크레딧(keyword_info): ads_metrics 1×{len(cands)} = {len(cands)} (cost_detail {ads_cost if ads_cost is not None else '없음'}"
          + (" ⚠️ 후보 수와 다르다 — 요청 목록이 candidates.json과 같은지 확인" if ads_cost not in (None, len(cands)) else "")
          + f") · all 10×{len(keep)} = {10 * len(keep)} → {a.out}")
    if a.meta:
        meta = json.load(open(a.meta, encoding="utf-8"))
        meta["screen"] = {"gate": a.gate, "window": a.window, "summary": summary, "merges": merges,
                          "ads_metrics_cost": (o.get("cost_detail") or {}).get("total_cost"),
                          "all_credits_formula": 10 * len(keep), "sent": keep, "rows": rows}
        pathlib.Path(a.meta).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  스크린 결과를 {a.meta}에 기록")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("responses", nargs="*", help="issue_list 응답 JSON (기간 표기가 없으면 파일@일수)")
    ap.add_argument("-o", "--out", default="candidates.json")
    ap.add_argument("--evidence-out", default="issue_evidence.csv")
    ap.add_argument("--meta-out", help="수집 사실 JSON (기본: 근거 CSV 옆 issue_meta.json)")
    ap.add_argument("--rules", default="", help="표기변형 치환 — 인라인 JSON · 파일 · themes.json(rules.변형치환)")
    ap.add_argument("--exclude", default="", help="범위 밖 이슈 정규식 — 후보에서 빼고 넓힘 판정에서도 뺀다")
    ap.add_argument("--user-candidates", help="사용자가 준 후보 목록 JSON — 합쳐서 내보낸다")
    ap.add_argument("--no-seed", action="store_true", help="시드 자체를 후보에 넣지 않는다")
    ap.add_argument("--max-sources", type=int, default=3)
    ap.add_argument("--max-sentences", type=int, default=2)
    ap.add_argument("--screen", help="ads_metrics 응답 JSON — 스크린 모드")
    ap.add_argument("--candidates", help="스크린 모드 입력 후보 목록")
    ap.add_argument("--meta", help="스크린 모드: 추출 단계의 issue_meta.json — 스크린 결과를 덧붙인다")
    ap.add_argument("--gate", type=int, default=3000)
    ap.add_argument("--window", type=int, default=3)
    a = ap.parse_args()
    if a.screen:
        if not a.candidates:
            sys.exit("--screen에는 --candidates가 필요하다")
        return screen(a)
    if not a.responses:
        sys.exit("issue_list 응답 파일이 없다")
    return extract(a)


if __name__ == "__main__":
    sys.exit(main())
