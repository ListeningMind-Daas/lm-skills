#!/usr/bin/env python3
"""세그먼트 후보 수집 · 스크린 · 호출 원장 (이 스킬 고유). SKILL.md STEP 1~4.

후보는 trend_finder(issue_list) 급부상 이슈 + intent_finder 연관 검색어 + 시드 자체다.
이슈는 검색량이 아니다 — 이 스크립트는 «무엇을 keyword_info로 잴지»만 정하고, 순위는 segment_risers.py가 매긴다.

  수집   python3 segment_candidates.py collect --tf tf_sun_14d.json tf_sun_90d.json tf_cushion_14d.json \\
             --intent if_sun.json if_cushion.json -o candidates.json --sources-out candidate_sources.csv \\
             --ledger ledger.json [--rules '{"썬":"선"}'] [--exclude '광고 모델'] [--entity-rules assets/entity_rules.json]
             [--scope-terms '선스틱,선케어'] [--include '<범위 미확인 후보를 승격할 정규식>']
         · **범위 점검(의무 · 0크레딧)**: trend_finder 에서만 온 후보는 시드·카테고리어(--scope-terms)를 포함하거나
           intent_finder 연관어와 토큰(2자 이상 · 불용어 제외)을 하나 이상 공유해야 «범위 확인»이다. 아니면
           «범위 미확인» — 표에는 남지만 순위·헤드라인 주어에서 빠진다(segment_risers.py). --include 로 승격하면 원장에 남는다
         · 넓힘 판정은 시드별 **범위 안 이슈 수**(범위 밖 제외 · 범위 확인 또는 승격)로 한다 — 0이고 가장 넓은 기간이 90일 미만이면 exit 3(90일로 한 번 더)
         · issue_list total > 반환 이고 반환 = limit 이면 «limit 절단»(T7) · intent_finder 반환 = limit 이어도 절단
  스크린 python3 segment_candidates.py screen ads_metrics.json --candidates candidates.json \\
             --gate 3000 --window 3 [--max-all 80] -o all_candidates.json --ledger ledger.json [--rules …]
         · volume_avg × window ≥ gate [가정]인 후보만 `all`(10/kw)로 보낸다 · --max-all 상한이면 volume_avg 큰 순
  원장   python3 segment_candidates.py ledger kw_all.json --ledger ledger.json --step "월별 수집" \\
             --purpose "48개월 검색량 · 성 · 연령"     # keyword_info all 같은 호출을 원장에 덧붙인다

크레딧 결정식(계약 §1):
  trend_finder  = 50 × 결과가 있는 호출 + 2 × 반환 이슈 (빈 결과 호출은 0 · 호출 전 상한 50 + 2 × limit)
  intent_finder = 30 × 입력 + 2 × 반환
  keyword_info  = 1 × N (ads_metrics) + 10 × M (all)
"""
import argparse, csv, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, dedup_map, load_rules  # noqa: E402  계약 T5

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_ENTITY_RULES = HERE.parent / "assets" / "entity_rules.json"
SOURCE_COLS = ["keyword", "canonical", "entity", "sources", "seeds", "issue_score_max", "issue_index_date_first",
               "issue_index_date_last", "issue_summary_ai", "issue_sources", "scope", "excluded"]
SCOPE_HELD = "범위 미확인"          # 이 접두어로 시작하는 scope 는 순위·헤드라인에서 보류한다(segment_risers.py)
SCOPE_PROMOTED = "승격"
TF_SEED, TF_ITEM, IF_IN, IF_OUT = 50, 2, 30, 2
WIDEN_TO = 90


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# ── 대상(엔티티) 묶음 — 결정적 규칙. LLM 묶음을 쓰지 않는다 [추정]
def load_entity_rules(path=None):
    p = pathlib.Path(path) if path else DEFAULT_ENTITY_RULES
    if not p.is_file():
        return {"stopwords": [], "suffixes": []}
    o = json.loads(p.read_text(encoding="utf-8"))
    return {"stopwords": [s.lower() for s in o.get("stopwords", [])], "suffixes": o.get("suffixes", [])}


def entity_of(kw, rules=None, erules=None):
    """검색어 → (엔티티 키, 표시 이름). 키는 canon, 이름은 남은 토큰을 띄어 쓴 것."""
    erules = erules or {"stopwords": [], "suffixes": []}
    toks = [t for t in str(kw).split() if t.lower() not in erules["stopwords"]]
    if toks:
        for suf in erules["suffixes"]:
            if toks[-1].endswith(suf) and len(toks[-1]) > len(suf):
                toks[-1] = toks[-1][: -len(suf)]
                break
    name = " ".join(toks) if toks else str(kw)
    return canon(name, rules), name


# ── 호출 원장 — 리포트 provenance.calls 의 원천. 손으로 적지 않는다
def ledger_load(path):
    p = pathlib.Path(path)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"calls": [], "notes": []}


def ledger_add(led, **call):
    call["id"] = f"C{len(led['calls']) + 1}"
    led["calls"].append(call)
    return call["id"]


def ledger_save(led, path):
    pathlib.Path(path).write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")


def at_of(o):
    s = str(o.get("request_at") or "")
    return s[:16].replace("T", " ") if s else ""


def load_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def as_list(v):
    return [x for x in v if x] if isinstance(v, list) else ([v] if v else [])


# ── 범위 점검 — trend_finder 단독 후보의 결정적 관련성 규칙 [가정]
def scope_tokens(words, rules, erules):
    """intent_finder 연관어 이웃의 토큰 집합(canonical · 2자 이상 · 대상 불용어 제외)."""
    out = set()
    for w in words:
        for t in str(w).split():
            c = canon(t, rules)
            if len(c) >= 2 and c not in erules["stopwords"]:
                out.add(c)
    return out


def scope_of(kws, terms, nb, rules, erules):
    """kws(후보 표기들) → (확인 여부, 근거). terms = {canonical: 원래 표기} 시드·카테고리어."""
    for kw in kws:
        c = canon(kw, rules)
        for tc, orig in terms.items():
            if tc and tc in c:
                return True, f"확인 — 시드·카테고리어 «{orig}» 포함"
    for kw in kws:
        for t in str(kw).split():
            c = canon(t, rules)
            if len(c) >= 2 and c not in erules["stopwords"] and c in nb:
                return True, f"확인 — 연관어 토큰 «{c}» 공유"
    return False, f"{SCOPE_HELD} — 시드·카테고리어·연관어 토큰과 겹치는 말 없음"


def intent_words(path):
    o = load_json(path)
    body = o.get("body", o) if isinstance(o, dict) else {"data": o}
    words = [d if isinstance(d, str) else (d or {}).get("keyword") for d in (body.get("data") or [])]
    return o, body, [w for w in words if w]


# ── 수집
def collect(a):
    rules, erules = load_rules(a.rules), load_entity_rules(a.entity_rules)
    pat = re.compile(a.exclude) if a.exclude else None
    out_of = lambda kw: bool(pat and pat.search(kw))
    inc = re.compile(a.include) if a.include else None
    led = ledger_load(a.ledger) if a.ledger else {"calls": [], "notes": []}
    notes, need_widen = [], []
    cand = {}   # canonical → 정보

    def add(kw, src, seed, issue=None):
        c = canon(kw, rules)
        e = cand.setdefault(c, {"keyword": kw, "canonical": c, "sources": [], "seeds": [], "score": None,
                                "date": "", "date0": "", "summary": [], "links": [], "aliases": [],
                                "excluded": out_of(kw)})
        if kw not in e["aliases"]:
            e["aliases"].append(kw)
        if src not in e["sources"]:
            e["sources"].append(src)
        if seed and seed not in e["seeds"]:
            e["seeds"].append(seed)
        if issue:
            sc = issue.get("issue_score")
            if sc is not None and (e["score"] is None or sc > e["score"]):
                e["score"] = sc
            d = max(as_list(issue.get("index_date")) or [""])
            e["date"] = max(e["date"], d)
            d0 = min(as_list(issue.get("index_date")) or [""])
            if d0 and (not e["date0"] or d0 < e["date0"]):
                e["date0"] = d0
            e["summary"] += [s for s in as_list(issue.get("summary")) if s not in e["summary"]]
            e["links"] += [s for s in as_list(issue.get("source_links")) if s not in e["links"]]

    # 시드를 먼저 넣는다 — 같은 canonical 이면 시드 표기가 후보 이름이 된다(썬크림이 아니라 선크림)
    seeds_all = []
    for spec in a.tf or []:
        req = load_json(spec.partition("@")[0]).get("request_detail") or {}
        seeds_all += [x for x in as_list(req.get("keyword")) if x not in seeds_all]
    for path in a.intent or []:
        o = load_json(path); body = o.get("body", o) if isinstance(o, dict) else {}
        seeds_all += [x for x in as_list((body.get("request_detail") or {}).get("keywords")) if x not in seeds_all]
    if not a.no_seed:
        for x in seeds_all:
            add(x, "seed", x)
    # 범위 점검 기준 — 시드 · 카테고리어(--scope-terms) · intent_finder 연관어 이웃 토큰
    extra = [t.strip() for t in re.split(r"[,，]", a.scope_terms or "") if t.strip()]
    terms = {canon(t, rules): t for t in seeds_all + extra if canon(t, rules)}
    nb = scope_tokens([w for p in (a.intent or []) for w in intent_words(p)[2]], rules, erules)

    def in_scope(kw):
        """trend_finder 이슈 한 건이 넓힘 판정에서 «범위 안»인가 — 범위 밖 아님 · 범위 확인 또는 승격."""
        if out_of(kw):
            return False
        ok, _ = scope_of([kw], terms, nb, rules, erules)
        return ok or bool(inc and inc.search(kw))

    # trend_finder — 시드별 기간순
    tfs = []
    for spec in a.tf or []:
        path, _, days = spec.partition("@")
        o = load_json(path)
        req = o.get("request_detail") or {}
        period = int(days) if days else req.get("period_days")
        if period is None:
            sys.exit(f"{path}: 기간을 알 수 없다 — request_detail.period_days 가 없으면 `{path}@14`처럼 붙인다")
        items = [d for d in (o.get("data") or []) if isinstance(d, dict) and d.get("keyword")]
        tfs.append({"path": path, "o": o, "seed": req.get("keyword", ""), "period": int(period), "items": items,
                    "total": o.get("total"), "limit": req.get("limit"),
                    "cost": (o.get("cost_detail") or {}).get("total_cost")})
    tfs.sort(key=lambda r: (r["seed"], r["period"]))
    n_hit = n_ret = 0
    for r in tfs:
        n_in = sum(1 for it in r["items"] if in_scope(it["keyword"]))
        trunc = (r["total"] is not None and r["total"] > len(r["items"])
                 and (r["limit"] is None or len(r["items"]) >= r["limit"]))
        status = "ok" if r["items"] else "빈 결과(실측 · 0 과금)"
        ledger_add(led, step="이슈 후보", at=at_of(r["o"]), tool="trend_finder",
                   params=f"keyword={r['seed']} · period_days={r['period']} · limit={r['limit']}",
                   purpose="급부상 이슈로 후보 모으기", returned=len(r["items"]), cost=r["cost"] if r["cost"] is not None else 0,
                   raw=r["path"], data_through=None, status=status,
                   truncated=bool(trunc), in_scope=n_in, total=r["total"], limit=r["limit"])
        if r["items"]:
            n_hit += 1; n_ret += len(r["items"])
        if trunc:
            notes.append(f"⚠️ trend_finder {r['seed']} {r['period']}일: 매칭 {r['total']}건 중 {len(r['items'])}건 반환 — "
                         "limit 절단(T7) · 후보가 전부가 아니다")
        for it in r["items"]:
            add(it["keyword"], "trend_finder", r["seed"], it)
    for seed in dict.fromkeys(r["seed"] for r in tfs):
        rs = [r for r in tfs if r["seed"] == seed]
        n_in = {r["period"]: sum(1 for it in r["items"] if in_scope(it["keyword"])) for r in rs}
        tried = "·".join(str(r["period"]) for r in rs)
        if " " in seed.strip():
            notes.append(f"⚠️ 띄어쓴 시드 «{seed}» — 단어 하나만 맞아도 이슈를 가져온다(계약 §1). 범위 안 건수로 판단한다")
        if any(n_in.values()):
            first = next(r for r in rs if n_in[r["period"]])
            if first is not rs[0]:
                notes.append(f"trend_finder {seed}: {rs[0]['period']}일 범위 안 이슈 0건 → {first['period']}일로 넓혀 "
                             f"{n_in[first['period']]}건 [실제 데이터]")
        elif max(r["period"] for r in rs) < WIDEN_TO:
            need_widen.append(seed)
            notes.append(f"⚠️ trend_finder {seed}: {tried}일 범위 안 이슈 0건 — period_days={WIDEN_TO}로 한 번 더 조회한 뒤 "
                         f"결론을 낸다(상한 {TF_SEED} + {TF_ITEM} × limit · 빈 결과면 0). 이슈를 지어내지 않는다")
        else:
            notes.append(f"trend_finder {seed}: {tried}일 모두 범위 안 이슈 0건 → «이슈 없음» [실제 데이터 — 그 기간 매칭 없음]")

    # intent_finder — 문자열만 반환한다(계약 §1)
    n_if_in = n_if_out = 0
    for path in a.intent or []:
        o, body, words = intent_words(path)
        req = body.get("request_detail") or {}
        seeds = as_list(req.get("keywords") or req.get("keyword"))
        limit = req.get("limit")
        trunc = limit is not None and len(words) >= limit
        cost = (body.get("cost_detail") or {}).get("total_cost")
        n_if_in += max(1, len(seeds)); n_if_out += len(words)
        ledger_add(led, step="연관 확장", at=at_of(body), tool="intent_finder",
                   params=f"keywords={'·'.join(seeds)} · limit={limit}", purpose="연관 검색어로 후보 넓히기",
                   returned=len(words), cost=cost if cost is not None else 0, raw=path, data_through=None,
                   status="ok" if words else "빈 결과", truncated=bool(trunc), limit=limit)
        if trunc:
            notes.append(f"⚠️ intent_finder {'·'.join(seeds)}: 반환 {len(words)} = limit {limit} — 절단(T7) · "
                         "후보 풀이 연관 검색어 전부가 아니다(수렴으로 읽지 않는다)")
        for w in words:
            add(w, "intent_finder", seeds[0] if seeds else "")

    if a.user_candidates:
        for k in load_json(a.user_candidates):
            add(k if isinstance(k, str) else k.get("keyword"), "user", "")

    rows, cands, held, promoted, n_conf = [], [], [], [], 0
    order = sorted(cand.values(), key=lambda e: ("seed" not in e["sources"], -(e["score"] or 0), e["keyword"]))
    for e in order:
        ek, en = entity_of(e["keyword"], rules, erules)
        if set(e["sources"]) == {"trend_finder"}:
            ok, scope = scope_of(e["aliases"], terms, nb, rules, erules)
            if not ok and inc and any(inc.search(k) for k in e["aliases"]):
                scope = f"{SCOPE_PROMOTED}(--include «{a.include}») — 원래 {scope}"
                if not e["excluded"]:
                    promoted.append(e["keyword"])
            elif not ok and not e["excluded"]:
                held.append(e["keyword"])
            elif ok and not e["excluded"]:
                n_conf += 1
        else:
            scope = "확인 — 시드·연관어·사용자 출처"
        rows.append({"keyword": e["keyword"], "canonical": e["canonical"], "entity": en,
                     "sources": " / ".join(e["sources"]), "seeds": " / ".join(e["seeds"]),
                     "issue_score_max": e["score"] if e["score"] is not None else "",
                     "issue_index_date_first": e["date0"], "issue_index_date_last": e["date"],
                     "issue_summary_ai": " / ".join(e["summary"][:2]),
                     "issue_sources": " | ".join(e["links"][:3]), "scope": scope,
                     "excluded": "범위 밖(--exclude)" if e["excluded"] else ""})
        if not e["excluded"]:
            cands.append(e["keyword"])
    pathlib.Path(a.out).write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(a.sources_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SOURCE_COLS); w.writeheader(); w.writerows(rows)

    tf_formula = TF_SEED * n_hit + TF_ITEM * n_ret
    tf_actual = sum(r["cost"] or 0 for r in tfs)
    if_formula = IF_IN * n_if_in + IF_OUT * n_if_out
    credit = (f"크레딧: trend_finder 결정식 {TF_SEED}×{n_hit}(결과 있는 호출) + {TF_ITEM}×{n_ret}(반환) = {tf_formula}"
              f" · cost_detail 합 {tf_actual}" + ("" if tf_formula == tf_actual else " ⚠️ 결정식과 다르다 — 환경 단가 확인")
              + f" / intent_finder {IF_IN}×{n_if_in} + {IF_OUT}×{n_if_out} = {if_formula}")
    notes.append(credit)
    n_ex = sum(1 for r in rows if r["excluded"])
    notes.append(f"후보 {len(rows)}개(canonical 병합 [추정]) · 범위 밖 {n_ex}개 제외 · keyword_info 후보 {len(cands)}개 "
                 f"→ 다음: ads_metrics 1×{len(cands)} = {len(cands)}")
    scope_line = (f"범위 점검 [가정 — 시드·카테고리어 포함 또는 연관어 토큰 공유]: trend_finder 단독 후보 "
                  f"{n_conf + len(held) + len(promoted)}개 중 확인 {n_conf} · 범위 미확인 {len(held)}"
                  + (f" · --include 승격 {len(promoted)}" if promoted else ""))
    if held:
        scope_line += (" — 범위 미확인 후보는 표에 남지만 순위·헤드라인 주어에서 보류한다(승격은 --include): "
                       + ", ".join(held[:20]) + (f" 외 {len(held) - 20}개" if len(held) > 20 else ""))
    if promoted:
        scope_line += " · 승격: " + ", ".join(promoted)
    notes.append(scope_line)
    led["notes"] = led.get("notes", []) + notes
    led["need_widen"] = need_widen
    led["exclude"] = a.exclude or ""
    led["scope"] = {"rule": "trend_finder 단독 후보는 시드·카테고리어 포함 또는 intent_finder 연관어 토큰 공유 시 확인",
                    "terms": list(terms.values()), "neighbourhood_tokens": len(nb), "confirmed": n_conf,
                    "held": held, "include": a.include or "", "promoted": promoted}
    if a.ledger:
        ledger_save(led, a.ledger)
    for n in notes:
        print(n)
    print(f"→ {a.out} · {a.sources_out}" + (f" · {a.ledger}" if a.ledger else ""))
    return 3 if need_widen else 0


# ── 스크린 (ads_metrics 1/kw → all 10/kw 대상)
def screen(a):
    rules = load_rules(a.rules)
    cands = [k if isinstance(k, str) else k.get("keyword") for k in load_json(a.candidates)]
    o = load_json(a.metrics)
    data = [d for d in (o.get("data", o) if isinstance(o, dict) else o) if isinstance(d, dict) and d.get("keyword")]
    avg = lambda d: (d.get("ads_metrics") or {}).get("volume_avg") or 0
    by = {}
    for d in data:
        by.setdefault(canon(d["keyword"], rules), []).append(d)
    dm = dedup_map(data, rules)
    passed, rows, taken = [], [], {}
    for k in cands:
        ds = sorted(by.get(canon(k, rules), []), key=lambda d: -avg(d))
        if not ds:
            rows.append((k, "미반환", 0)); continue
        rep = dm.get(ds[0]["keyword"], {}).get("rep", ds[0]["keyword"])
        if rep in taken:
            rows.append((k, f"같은 검색 병합 → {taken[rep]}", avg(ds[0]))); continue
        taken[rep] = k
        if avg(ds[0]) * a.window >= a.gate:
            passed.append((ds[0]["keyword"], avg(ds[0])))
            rows.append((k, "통과", avg(ds[0])))
        else:
            rows.append((k, "탈락", avg(ds[0])))
    led0 = ledger_load(a.ledger) if a.ledger else {}
    held = {canon(k, rules) for k in (led0.get("scope") or {}).get("held", [])}
    # --max-all 상한에서는 범위 미확인 후보를 뒤로 미룬다(순위에 못 드는 후보가 범위 안 후보의 자리를 먹지 않게)
    passed.sort(key=lambda x: (canon(x[0], rules) in held, -x[1]))
    capped = []
    if a.max_all and len(passed) > a.max_all:
        capped = [k for k, _ in passed[a.max_all:]]
        passed = passed[: a.max_all]
    keep = [k for k, _ in passed]
    pathlib.Path(a.out).write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    cnt = lambda s: sum(1 for r in rows if r[1].startswith(s))
    summary = (f"스크린 [가정 volume_avg × {a.window} ≥ {a.gate:,}]: 후보 {len(cands)} → all 대상 {len(keep)} · "
               f"탈락 {cnt('탈락')}(볼륨 측정됨) · 미반환 {cnt('미반환')} [데이터 공백] · 같은 검색 병합 {cnt('같은')}"
               + (f" · 상한 --max-all {a.max_all}로 뺀 {len(capped)}개" if capped else ""))
    cost = (o.get("cost_detail") or {}).get("total_cost") if isinstance(o, dict) else None
    if cost is not None and cost != len(cands):
        summary += f" · ⚠️ cost_detail {cost} ≠ 후보 {len(cands)} — 요청 목록이 candidates.json 과 같은지 · 환경 단가 확인"
    print(summary)
    print(f"크레딧(keyword_info): ads_metrics 1×{len(cands)} = {len(cands)} (cost_detail {cost if cost is not None else '없음'})"
          f" · 다음 all 10×{len(keep)} = {10 * len(keep)} → {a.out}")
    if a.ledger:
        led = ledger_load(a.ledger)
        ledger_add(led, step="스크린", at=at_of(o) if isinstance(o, dict) else "", tool="keyword_info",
                   params=f"data_type=ads_metrics · 검색어 {len(cands)}개 · gl={(o.get('request_detail') or {}).get('gl', '')}",
                   purpose="볼륨으로 all 대상 거르기", returned=len(data), cost=cost if cost is not None else 0,
                   raw=a.metrics, data_through=None, status="ok")
        led["screen"] = {"gate": a.gate, "window": a.window, "summary": summary, "sent": keep, "capped": capped,
                         "rows": [{"keyword": k, "screen": s, "volume_avg": v} for k, s, v in rows]}
        led["notes"] = led.get("notes", []) + [summary]
        ledger_save(led, a.ledger)
    return 0


def ledger_cmd(a):
    led = ledger_load(a.ledger)
    o = load_json(a.response)
    req = o.get("request_detail") or {}
    data = o.get("data") or []
    months = [m.get("month") for d in data for m in (d.get("monthly_volume") or []) if m.get("month")]
    cost = (o.get("cost_detail") or {}).get("total_cost")
    cid = ledger_add(led, step=a.step, at=at_of(o), tool=a.tool,
                     params=f"data_type={req.get('data_type', '')} · 검색어 {len(req.get('keywords') or [])}개 · gl={req.get('gl', '')}",
                     purpose=a.purpose, returned=len(data), cost=cost if cost is not None else 0, raw=a.response,
                     data_through=max(months) if months else None, status="ok")
    ledger_save(led, a.ledger)
    print(f"원장 {a.ledger}에 {cid} 추가 — {a.tool} 반환 {len(data)} · cost {cost}")
    return 0


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--tf", nargs="*", help="issue_list 응답 JSON (기간 표기가 없으면 파일@일수)")
    c.add_argument("--intent", nargs="*", help="intent_finder 응답 JSON")
    c.add_argument("-o", "--out", default="candidates.json")
    c.add_argument("--sources-out", default="candidate_sources.csv")
    c.add_argument("--ledger", help="호출 원장 JSON (없으면 만든다)")
    c.add_argument("--rules", default="")
    c.add_argument("--exclude", default="")
    c.add_argument("--scope-terms", default="", help="범위 점검에 더할 카테고리어(쉼표 구분) — 시드는 자동 포함")
    c.add_argument("--include", default="", help="범위 미확인 후보를 승격할 정규식(원장 scope.promoted 에 남는다)")
    c.add_argument("--entity-rules")
    c.add_argument("--user-candidates")
    c.add_argument("--no-seed", action="store_true")
    s = sub.add_parser("screen")
    s.add_argument("metrics", help="keyword_info ads_metrics 응답 JSON")
    s.add_argument("--candidates", required=True)
    s.add_argument("-o", "--out", default="all_candidates.json")
    s.add_argument("--gate", type=int, default=3000)
    s.add_argument("--window", type=int, default=3)
    s.add_argument("--max-all", type=int, default=0, help="all 대상 상한 M (0 = 없음)")
    s.add_argument("--ledger")
    s.add_argument("--rules", default="")
    l = sub.add_parser("ledger")
    l.add_argument("response")
    l.add_argument("--ledger", required=True)
    l.add_argument("--tool", default="keyword_info")
    l.add_argument("--step", default="월별 수집")
    l.add_argument("--purpose", default="48개월 검색량 · 성 · 연령")
    a = ap.parse_args()
    if a.cmd == "collect":
        if not (a.tf or a.intent):
            sys.exit("--tf 또는 --intent 응답이 하나는 있어야 한다")
        return collect(a)
    if a.cmd == "screen":
        return screen(a)
    return ledger_cmd(a)


if __name__ == "__main__":
    sys.exit(main())
