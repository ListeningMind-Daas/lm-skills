#!/usr/bin/env python3
"""세그먼트 급등 · 신규 · 과대 지수 순위 (이 스킬 고유). SKILL.md STEP 6~8.

  python3 segment_risers.py kw_all.json --gl kr --gender f --age a20s --category "KR 선크림·쿠션" \\
      --sources candidate_sources.csv [--rules '{"썬":"선"}'] [--entity-rules assets/entity_rules.json] \\
      [--gate 3000] [--seg-gate 1000] [--window 3] [--min-growth 0.3] [--min-index 1.2] [--min-pool 5] \\
      [--new-within 12] [--include-last-month] [--requested all_candidates.json] [--include '<승격 정규식>'] [-d out/]

출력 (-d 폴더):
  segment_keywords_{category}.csv · segment_entities_{category}.csv · segment_summary_{category}.json

세그먼트 계산 [추정 — 아래 전제]:
  · demography 는 검색어별 **현재 시점 하나**의 성별 비율과 연령 비율(주변 분포)이다. 월별 분할도, 성별 × 연령 칸도 없다.
  · 세그먼트 비중 = 성별 비율 × 연령 비율 (둘 다 고르면 독립 가정의 곱 — [추정]) · 한쪽만 고르면 그 비율 그대로.
  · 세그먼트 검색량 = 전체 검색량 × 세그먼트 비중 — 현재 비중을 과거 창에도 그대로 씌운다. 그래서 **세그먼트 전년비는
    전체 전년비와 같다**. 세그먼트마다 달라지는 것은 절대 증가량(순위 기준)과 과대 지수다.
  · 과대 지수 = 검색어의 세그먼트 비중 ÷ 후보 풀 전체의 볼륨 가중 평균 비중 (풀 기준이지 시장 기준이 아니다).
  · demography 필드 모양은 키트 계약에 적혀 있지 않다 [가정]: `f_gender_ratio`·`m_gender_ratio`·`a{NN}_ratio`(퍼센트)를
    읽고, 합이 1 근처면 비율(0~1)로 본다. 모양이 다르면 `demography_status` = «형식 확인 필요»로 순위에서 뺀다.
  · gl ≠ kr 이면 demography 는 사양상 없다 — 세그먼트 없이 전체 순위만 낸다(그 사실을 summary 에 적는다).

범위 보류(v0.1.1 · P0 수정):
  · candidate_sources.csv 의 scope 가 «범위 미확인»(trend_finder 단독 · 시드·카테고리어·연관어 토큰과 겹침 없음)인 행과
    그 표기를 품은 응답 행은 값은 다 계산하되 **순위 · 과대 지수 기준 풀 · 대상 헤드라인에서 뺀다** → summary.scope_held.
    --include 정규식에 맞으면 승격(scope «승격(segment_risers --include …)») → summary.scope_promoted.
  · issue_timing: 이슈 첫 인덱스 날짜가 계산 창 끝보다 뒤면 «창 이후 이슈 — 원인 근거 아님»(그 창의 원인 가설로 쓰지 않는다).
"""
import argparse, csv, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, collapse_identical, dedup_map, load_rules, report_collapse  # noqa: E402
from segment_candidates import SCOPE_HELD, entity_of, load_entity_rules, utf8_console  # noqa: E402

KW_COLS = ["keyword", "canonical", "dup_of", "variant_of", "entity", "sources", "scope", "issue_timing", "demography_status",
           "gender_share", "age_share", "segment_share", "share_basis",
           "volume_window", "volume_prev_year_window", "missing_in_window", "yoy", "yoy_other_mode", "provisional",
           "segment_volume_window", "segment_volume_prev_year", "segment_abs_change", "over_index",
           "first_active_month", "is_newcomer", "gate_status", "tabs",
           "rank_surge", "rank_newcomer", "rank_overindex", "data_ref_month", "window_end"]
ENT_COLS = ["entity", "n_keywords", "keywords", "segment_volume_lo", "segment_volume_hi", "segment_prev_year_lo",
            "segment_abs_change_lo", "yoy_lo", "over_index_wavg", "has_newcomer", "headline_ok", "rank_entity", "note"]
GENDER = {"all": ("전체", None), "f": ("여성", "f_gender_ratio"), "m": ("남성", "m_gender_ratio")}
AGE = {"all": ("전체", []), "a13": ("10대(13~19)", ["a13"]), "a20s": ("20대", ["a20", "a25"]),
       "a20": ("20~24세", ["a20"]), "a25": ("25~29세", ["a25"]), "a30": ("30대", ["a30"]),
       "a40": ("40대", ["a40"]), "a50": ("50대 이상", ["a50"])}
AGE_KEYS = ["a0", "a13", "a20", "a25", "a30", "a40", "a50"]
DEMO_GL = {"kr"}


def shift(month, n):
    y, m = map(int, month.split("-"))
    t = y * 12 + (m - 1) + n
    return f"{t // 12}-{t % 12 + 1:02d}"


def window(end, size):
    return [shift(end, -i) for i in range(size - 1, -1, -1)]


def mv_map(d):
    """month → total(결측이면 None). nv_code 월은 total 키가 없다 — 0으로 바꾸지 않는다(계약 §2)."""
    out = {}
    for r in d.get("monthly_volume") or []:
        if isinstance(r, dict) and r.get("month"):
            out[r["month"]] = r.get("total")
    return out


def wsum(series, months):
    vals = [series.get(m) for m in months]
    miss = sum(1 for v in vals if v is None)
    return (None if miss else sum(vals)), miss


# ── demography 파서 — 방어적으로 [가정]
def parse_demo(raw):
    """→ (dict{f,m,a0..a50} 0~1 비율 | None, status)."""
    if isinstance(raw, list):
        raw = next((x for x in raw if isinstance(x, dict)), None)
    if not isinstance(raw, dict) or not raw:
        return None, "미반환"
    flat = dict(raw)
    for k in ("gender", "age"):                       # 중첩형이 오면 펼친다 — 관측된 적 없음 [가정]
        if isinstance(raw.get(k), dict):
            for kk, vv in raw[k].items():
                flat[f"{kk}_gender_ratio" if k == "gender" else f"a{str(kk).lstrip('a')}_ratio"] = vv

    def num(k):
        v = flat.get(k)
        try:
            return None if v is None or isinstance(v, bool) else float(v)
        except (TypeError, ValueError):
            return None
    g = {"f": num("f_gender_ratio"), "m": num("m_gender_ratio")}
    ages = {k: num(f"{k}_ratio") for k in AGE_KEYS}
    if g["f"] is None and g["m"] is None and all(v is None for v in ages.values()):
        return None, "형식 확인 필요(알려진 비율 키 없음)"
    gs = sum(v for v in g.values() if v is not None)
    as_ = sum(v for v in ages.values() if v is not None)
    scale = None
    for total in (gs, as_):
        if total and 90 <= total <= 110:
            scale = 100.0
        elif total and 0.9 <= total <= 1.1:
            scale = scale or 1.0
    if scale is None:
        return None, f"형식 확인 필요(비율 합 성별 {gs:g} · 연령 {as_:g})"
    out = {k: (v / scale if v is not None else None) for k, v in {**g, **ages}.items()}
    return out, "반환"


def seg_share(demo, gender, age):
    """→ (성별 몫, 연령 몫, 세그먼트 비중, 기준)."""
    gsh = 1.0 if gender == "all" else demo.get(gender)
    if age == "all":
        ash = 1.0
    else:
        parts = [demo.get(k) for k in AGE[age][1]]
        ash = None if any(p is None for p in parts) else sum(parts)
    if gsh is None or ash is None:
        return gsh, ash, None, "비율 키 없음"
    basis = ("성별 × 연령 곱(독립 가정) [추정]" if gender != "all" and age != "all"
             else "성별 비율 [실제 데이터]" if gender != "all" else "연령 비율 [실제 데이터]")
    return gsh, ash, gsh * ash, basis


def r4(v):
    return "" if v is None else round(v, 4)


def r0(v):
    return "" if v is None else int(round(v))


def run(a):
    rules, erules = load_rules(a.rules), load_entity_rules(a.entity_rules)
    o = json.loads(pathlib.Path(a.response).read_text(encoding="utf-8"))
    data = [d for d in (o.get("data", o) if isinstance(o, dict) else o) if isinstance(d, dict) and d.get("keyword")]
    data, merged, conflicts = collapse_identical(data)
    report_collapse(merged, conflicts, len(data) + merged)
    gl = (a.gl or (o.get("request_detail") or {}).get("gl") or "").lower()
    segment_on = gl in DEMO_GL and not (a.gender == "all" and a.age == "all")
    seg_label = (f"{AGE[a.age][0]} {GENDER[a.gender][0]}" if a.gender != "all" and a.age != "all"
                 else GENDER[a.gender][0] if a.gender != "all" else AGE[a.age][0] if a.age != "all" else "전체")
    notes = []
    if gl not in DEMO_GL and (a.gender != "all" or a.age != "all"):
        notes.append(f"gl={gl} 는 demography 를 돌려주지 않는다(데이터 사양 — 공백 아님). 요청 세그먼트 «{seg_label}» 대신 "
                     "전체 검색량 순위만 낸다 · 과대 지수 없음")
    if gl in DEMO_GL and not segment_on:
        notes.append("세그먼트가 전체(성별 all · 연령 all)라 과대 지수는 정의되지 않는다 — 급등 · 신규만 낸다")

    series = {d["keyword"]: mv_map(d) for d in data}
    allm = [m for s in series.values() for m in s]
    if not allm:
        sys.exit("monthly_volume 이 없다 — keyword_info data_type=all 응답이 필요하다(ads_metrics 에는 시계열이 없다)")
    ref = max(allm)                                         # 기준월은 응답에서 읽는다(T1) — 배열 순서를 가정하지 않는다
    end = ref if a.include_last_month else shift(ref, -1)   # 마지막 달 제외가 기본(T2)
    alt_end = shift(ref, -1) if a.include_last_month else ref
    cur_m, prev_m = window(end, a.window), window(shift(end, -12), a.window)
    alt_cur, alt_prev = window(alt_end, a.window), window(shift(alt_end, -12), a.window)

    dm = dedup_map(data, rules)
    src = {}
    if a.sources and pathlib.Path(a.sources).is_file():
        with open(a.sources, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                src[r["canonical"]] = r
    inc = re.compile(a.include) if a.include else None
    held_c = [c for c, r in src.items() if (r.get("scope") or "").startswith(SCOPE_HELD) and len(c) >= 2]
    promoted = []

    def scope_for(kw, c):
        """→ (scope 문자열, 보류 여부). 후보 CSV 에 없는 응답 행은 보류 후보의 표기를 품으면 보류를 물려받는다."""
        r = src.get(c)
        sc = (r or {}).get("scope") or ""
        if not r:
            par = next((h for h in held_c if h in c), None)
            if par:
                sc = f"{SCOPE_HELD} — 보류 후보 «{src[par]['keyword']}»의 표기를 품은 응답 행"
        if sc.startswith(SCOPE_HELD) and inc and inc.search(kw):
            promoted.append(kw)
            return f"승격(segment_risers --include «{a.include}») — 원래 {sc}", False
        return sc, sc.startswith(SCOPE_HELD)

    def timing(c):
        """이슈 첫 인덱스 날짜 vs 계산 창 — 창 끝보다 뒤면 그 창의 원인 근거가 아니다(시점 역전)."""
        r = src.get(c) or {}
        d = r.get("issue_index_date_first") or r.get("issue_index_date_last") or ""
        if not d:
            return ""
        if d[:7] > end:
            return f"창 이후 이슈(첫 인덱스 {d} > 창 끝 {end}) — 원인 근거 아님"
        if d[:7] < cur_m[0]:
            return f"창 이전 이슈({d}) — 원인 가설로만 [추정]"
        return f"창 안 이슈({d}) — 원인 가설로만 [추정]"
    rows = []
    for d in data:
        kw = d["keyword"]
        s = series[kw]
        cur, m1 = wsum(s, cur_m)
        prev, m2 = wsum(s, prev_m)
        acur, _ = wsum(s, alt_cur)
        aprev, _ = wsum(s, alt_prev)
        active = sorted(m for m, v in s.items() if v)
        first = active[0] if active else ""
        earlier = [m for m in s if m < first] if first else []
        newcomer = bool(first) and first > shift(end, -a.new_within) and all(not s.get(m) for m in earlier) and m1 == 0
        demo, dstat = parse_demo(d.get("demography"))
        if gl not in DEMO_GL:
            dstat = f"사양상 없음(gl={gl})"
        elif dstat == "미반환":
            dstat = "미반환 [데이터 공백]"
        if segment_on and demo:
            gsh, ash, share, basis = seg_share(demo, a.gender, a.age)
        elif segment_on:
            gsh = ash = share = None; basis = "세그먼트 계산 불가"
        else:
            gsh = ash = None; share = 1.0; basis = "전체(세그먼트 없음)"
        k_ent, n_ent = entity_of(kw, rules, erules)
        c = canon(kw, rules)
        sc, held = scope_for(kw, c)
        rows.append({"keyword": kw, "canonical": c, "dup_of": "", "variant_of": "", "entity": n_ent, "_ek": k_ent,
                     "sources": (src.get(c) or {}).get("sources", ""), "scope": sc, "_held": held,
                     "issue_timing": timing(c), "demography_status": dstat,
                     "gender_share": r4(gsh), "age_share": r4(ash), "segment_share": r4(share), "share_basis": basis,
                     "_share": share, "_cur": cur, "_prev": prev,
                     "volume_window": r0(cur), "volume_prev_year_window": r0(prev), "missing_in_window": m1 + m2,
                     "yoy": "", "yoy_other_mode": "", "provisional": "",
                     "segment_volume_window": r0(cur * share) if cur is not None and share is not None else "",
                     "segment_volume_prev_year": r0(prev * share) if prev is not None and share is not None else "",
                     "segment_abs_change": "", "over_index": "", "first_active_month": first,
                     "is_newcomer": "예" if newcomer else "", "gate_status": "", "tabs": "",
                     "rank_surge": "", "rank_newcomer": "", "rank_overindex": "",
                     "data_ref_month": ref, "window_end": end,
                     "_alt": (acur / aprev - 1) if acur is not None and aprev else None})

    # T5 — 같은 검색(시계열 일치) · 표기변형 그룹(같은 canonical): 순위는 그룹 대표 한 행으로
    by_c = {}
    for r in rows:
        rep = dm.get(r["keyword"], {}).get("rep", r["keyword"])
        if rep != r["keyword"]:
            r["dup_of"] = rep
            r["gate_status"] = "같은 검색의 중복"
        by_c.setdefault(r["canonical"], []).append(r)
    for grp in by_c.values():
        live = [r for r in grp if not r["dup_of"]]
        live.sort(key=lambda r: -(r["_cur"] or 0))
        for r in live[1:]:
            r["variant_of"] = live[0]["keyword"]
            r["gate_status"] = f"표기변형 — 대표 {live[0]['keyword']}"
        for r in grp:
            r["_group"] = grp

    # 과대 지수 기준 비중 — 후보 풀(대표 행 · 세그먼트 계산 가능 · 최근 창 측정)의 볼륨 가중 평균 [추정]
    pool = [r for r in rows if not r["gate_status"] and r["_share"] is not None and r["_cur"] and not r["_held"]]
    baseline = None
    if segment_on and len(pool) >= a.min_pool:
        baseline = sum(r["_share"] * r["_cur"] for r in pool) / sum(r["_cur"] for r in pool)
    elif segment_on:
        notes.append(f"세그먼트 비중이 있는 대표 검색어 {len(pool)}개 < {a.min_pool} [가정] — 과대 지수 기준이 불안정해 내지 않는다")

    for r in rows:
        if r["gate_status"]:
            continue
        cur, prev, share = r["_cur"], r["_prev"], r["_share"]
        if share is None:
            r["gate_status"] = f"세그먼트 계산 불가 — demography {r['demography_status']}"
            continue
        if baseline and r["_cur"]:
            r["over_index"] = round(share / baseline, 3)
        if r["is_newcomer"]:
            r["gate_status"] = "신규(과거 창 결측 → 최근 등장)"
            continue
        if cur is None or prev is None:
            r["gate_status"] = "계산 불가(비교 창 결측 월)"
            continue
        if prev == 0:
            r["gate_status"] = "계산 불가(전년 창 0)"
            continue
        r["yoy"] = round(cur / prev - 1, 4)
        r["yoy_other_mode"] = r4(r["_alt"])
        r["segment_abs_change"] = r0(cur * share - prev * share)
        if prev < a.gate:
            r["gate_status"] = f"게이트 미달(전년 창 {int(prev):,} < {a.gate:,})"
        elif prev * share < a.seg_gate:
            r["gate_status"] = f"세그먼트 기저 미달(세그먼트 전년 창 {int(prev * share):,} < {a.seg_gate:,})"
        else:
            r["gate_status"] = "판정"
        alt = r["_alt"]
        if alt is not None and (alt >= a.min_growth) != (r["yoy"] >= a.min_growth):
            r["provisional"] = f"잠정(다른 모드 전년비 {alt:+.1%})"

    def tab(name, cands, key):
        cands.sort(key=key)
        for i, r in enumerate(cands, 1):
            r[f"rank_{name}"] = i
            r["tabs"] = (r["tabs"] + " · " if r["tabs"] else "") + {"surge": "급등", "newcomer": "신규", "overindex": "과대 지수"}[name]
        return cands

    is_surge = lambda r: r["gate_status"] == "판정" and r["yoy"] >= a.min_growth
    is_new = lambda r: r["gate_status"].startswith("신규") and r["_cur"] is not None and r["_cur"] * r["_share"] >= a.seg_gate
    is_over = lambda r: (r["over_index"] != "" and r["over_index"] >= a.min_index
                         and not r["gate_status"].startswith(("같은", "표기"))
                         and r["_cur"] is not None and r["_cur"] >= a.gate and r["_cur"] * r["_share"] >= a.seg_gate)
    live = [r for r in rows if not r["_held"]]          # 범위 미확인 행은 순위 · 헤드라인 후보에서 뺀다(표에는 남긴다)
    surge = tab("surge", [r for r in live if is_surge(r)], lambda r: -(r["_cur"] - r["_prev"]) * r["_share"])
    newc = tab("newcomer", [r for r in live if is_new(r)], lambda r: -r["_cur"] * r["_share"])
    over = tab("overindex", [r for r in live if is_over(r)], lambda r: (-r["over_index"], -r["_cur"]))
    small_over = [r["keyword"] for r in live if r["over_index"] != "" and r["over_index"] >= a.min_index
                  and not r["rank_overindex"] and not r["gate_status"].startswith(("같은", "표기"))]
    small_new = [r["keyword"] for r in live if r["gate_status"].startswith("신규") and not r["rank_newcomer"]]
    top_yoy = max((r for r in live if r["gate_status"] == "판정"), key=lambda r: r["yoy"], default=None)
    exploded = [r for r in live if r["gate_status"].startswith("게이트 미달") and r["yoy"] != "" and r["yoy"] >= a.min_growth]
    seg_small = [r for r in live if r["gate_status"].startswith("세그먼트 기저") and r["yoy"] != "" and r["yoy"] >= a.min_growth]
    held_rows = [r for r in rows if r["_held"] and not r["dup_of"] and not r["variant_of"]]
    for r in held_rows:
        would = [n for n, f in (("급등", is_surge), ("신규", is_new), ("과대 지수", is_over)) if r["_share"] is not None and f(r)]
        r["_would"] = would
        r["tabs"] = "범위 미확인 — 순위 보류" + (f"(기준 충족: {'·'.join(would)})" if would else "")
    held_rows.sort(key=lambda r: (not r["_would"], -((r["_cur"] or 0) * (r["_share"] or 0))))

    # 엔티티 — 대표 행만 · 그룹 하한(최댓값) ~ 상한(같은 검색 빼고 합) [추정] · 기저 미달 엔티티는 헤드라인에서 뺀다(T4)
    ents = {}
    for r in rows:
        if r["dup_of"] or r["variant_of"] or r["_share"] is None:
            continue
        ents.setdefault(r["_ek"], []).append(r)
    erows = []
    for ek, rs_all in ents.items():
        rs = [r for r in rs_all if not r["_held"]] or rs_all
        ent_held = all(r["_held"] for r in rs)          # 범위 미확인 검색어만 든 대상 — 표에만 둔다
        n_held_out = sum(1 for r in rs_all if r["_held"]) if not ent_held else 0
        lo = hi = plo = alo = 0.0
        n_cur = n_both = 0
        for r in rs:
            sh = r["_share"]
            mem = [m for m in r["_group"] if not m["dup_of"] and m["_cur"] is not None]
            if r["_cur"] is not None:
                lo += r["_cur"] * sh; hi += sum(m["_cur"] for m in mem) * sh; n_cur += 1
            if r["_cur"] is not None and r["_prev"] is not None and not r["is_newcomer"]:
                plo += r["_prev"] * sh; alo += (r["_cur"] - r["_prev"]) * sh; n_both += 1
        wi = [(r["over_index"], r["_cur"]) for r in rs if r["over_index"] != "" and r["_cur"]]
        # 전년 창을 계산할 수 있는 검색어가 하나도 없으면(신규 · 결측) 증가 순위 없이 최근 규모만 본다
        ok = lo >= a.seg_gate and (plo >= a.seg_gate or n_both == 0) and not ent_held
        note = []
        if ent_held:
            note.append("범위 미확인(trend_finder 단독 · 시드·연관어와 겹침 없음) — 헤드라인 주어로 쓰지 않는다 · 승격은 --include")
        elif n_held_out:
            note.append(f"범위 미확인 검색어 {n_held_out}개는 대상 합계에서 뺌")
        if n_both < n_cur:
            note.append(f"전년 창 계산 불가 {n_cur - n_both}개는 증감에서 뺌(T6 모집단 고정)")
        if n_both == 0 and ok:
            note.append("전년 비교 가능한 검색어 없음 — 증가 순위 없음(최근 규모만)")
        if n_cur == 0:
            note.append("최근 창 결측 — 계산 불가(T6) · 0으로 바꾸지 않는다")
        elif not ok and not ent_held:
            note.append(f"기저 미달 — 세그먼트 최근 {round(lo):,} · 전년 {round(plo):,} < {a.seg_gate:,} · 표에만 둔다(T4)")
        erows.append({"entity": rs[0]["entity"], "n_keywords": len(rs), "keywords": " / ".join(r["keyword"] for r in rs),
                      "segment_volume_lo": r0(lo) if n_cur else "", "segment_volume_hi": r0(hi) if n_cur else "", "segment_prev_year_lo": r0(plo) if n_both else "",
                      "segment_abs_change_lo": r0(alo) if n_both else "",
                      "yoy_lo": round(alo / plo, 4) if plo else "",
                      "over_index_wavg": round(sum(i * v for i, v in wi) / sum(v for _, v in wi), 3) if wi else "",
                      "has_newcomer": "예" if any(r["is_newcomer"] for r in rs) else "",
                      "headline_ok": "예" if ok else "아니오", "rank_entity": "", "note": " · ".join(note)})
    ranked = sorted([e for e in erows if e["headline_ok"] == "예" and e["segment_abs_change_lo"] != ""],
                    key=lambda e: -e["segment_abs_change_lo"])
    for i, e in enumerate(ranked, 1):
        e["rank_entity"] = i
    erows.sort(key=lambda e: (e["rank_entity"] == "", e["rank_entity"] or 0, -(e["segment_volume_lo"] or 0)))  # 결측("")은 0으로 정렬만

    out = pathlib.Path(a.dir); out.mkdir(parents=True, exist_ok=True)
    cat = a.category.replace(" ", "_").replace("/", "_")
    rows.sort(key=lambda r: (r["rank_surge"] == "", r["rank_surge"] or 0, r["rank_newcomer"] == "", r["rank_newcomer"] or 0,
                             r["rank_overindex"] == "", r["rank_overindex"] or 0, -(r["_cur"] or 0)))
    kp, ep, sp = out / f"segment_keywords_{cat}.csv", out / f"segment_entities_{cat}.csv", out / f"segment_summary_{cat}.json"
    with open(kp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=KW_COLS, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    with open(ep, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=ENT_COLS); w.writeheader(); w.writerows(erows)

    missing_req = []
    if a.requested and pathlib.Path(a.requested).is_file():
        got = {canon(d["keyword"], rules) for d in data}
        missing_req = [k for k in json.loads(pathlib.Path(a.requested).read_text(encoding="utf-8"))
                       if canon(k, rules) not in got]
    cnt = lambda p: sum(1 for r in rows if r["gate_status"].startswith(p))
    after = [r["keyword"] for r in rows if r["issue_timing"].startswith("창 이후")]
    if held_rows:
        notes.append(f"범위 미확인 {len(held_rows)}개(trend_finder 단독 · 시드·카테고리어·연관어 토큰과 겹침 없음)는 순위 · 과대 지수 기준 풀 · "
                     "대상 헤드라인에서 뺐다 — 표에는 남는다: "
                     + ", ".join(r["keyword"] + (f"(기준 충족: {'·'.join(r['_would'])})" if r["_would"] else "") for r in held_rows[:10])
                     + (f" 외 {len(held_rows) - 10}개" if len(held_rows) > 10 else ""))
    if promoted:
        notes.append(f"--include «{a.include}»로 승격한 범위 미확인 행: {', '.join(promoted)} — 순위에 들어간다(사람 판단)")
    if after:
        notes.append(f"이슈 첫 인덱스가 계산 창 끝({end})보다 뒤인 후보 {len(after)}개는 이 창 증감의 원인 근거로 쓰지 않는다: "
                     + ", ".join(after[:10]))
    brief = lambda r: {"keyword": r["keyword"], "entity": r["entity"], "yoy": r["yoy"], "volume_window": r["volume_window"],
                       "volume_prev_year_window": r["volume_prev_year_window"], "segment_share": r["segment_share"],
                       "segment_volume_window": r["segment_volume_window"], "segment_volume_prev_year": r["segment_volume_prev_year"],
                       "segment_abs_change": r["segment_abs_change"], "over_index": r["over_index"],
                       "first_active_month": r["first_active_month"], "provisional": r["provisional"], "share_basis": r["share_basis"],
                       "scope": r["scope"], "issue_timing": r["issue_timing"]}
    summ = {"category": a.category, "gl": gl, "segment": {"gender": a.gender, "age": a.age, "label": seg_label,
            "applied": segment_on, "basis": ("성별 × 연령 곱(독립 가정) [추정]" if a.gender != "all" and a.age != "all" else "단일 축 비율")},
            "data_ref_month": ref, "window": {"size": a.window, "end": end, "cur": f"{cur_m[0]}~{cur_m[-1]}",
                                              "prev": f"{prev_m[0]}~{prev_m[-1]}"},
            "include_last_month": a.include_last_month,
            "gates": {"gate": a.gate, "seg_gate": a.seg_gate, "min_growth": a.min_growth, "min_index": a.min_index,
                      "min_pool": a.min_pool, "new_within": a.new_within},
            "baseline_share": round(baseline, 4) if baseline else None, "pool_size": len(pool),
            "counts": {"rows": len(rows), "surge": len(surge), "newcomer": len(newc), "overindex": len(over),
                       "judged": cnt("판정"), "gated": cnt("게이트 미달"), "seg_gated": cnt("세그먼트 기저"),
                       "uncomputable": cnt("계산 불가"), "no_segment": cnt("세그먼트 계산 불가"),
                       "dup": cnt("같은 검색"), "variant": cnt("표기변형"), "newcomer_all": cnt("신규"),
                       "provisional": sum(1 for r in rows if r["provisional"]), "collapsed": merged,
                       "scope_held": len(held_rows), "scope_promoted": len(promoted), "issue_after_window": len(after),
                       "entities": len(erows), "entities_ranked": len(ranked)},
            "tabs": {"surge": [brief(r) for r in surge], "newcomer": [brief(r) for r in newc],
                     "overindex": [brief(r) for r in over]},
            "top_surge_by_yoy": brief(top_yoy) if top_yoy else None,
            "entities_ranked": ranked, "entities_small": [e["entity"] for e in erows if e["headline_ok"] == "아니오" and not e["note"].startswith(SCOPE_HELD)],
            "exploded_out": [brief(r) for r in exploded], "seg_small_out": [brief(r) for r in seg_small], "small_overindex_out": small_over, "small_newcomer_out": small_new,
            "no_demography": [r["keyword"] for r in rows if r["demography_status"].startswith(("미반환", "형식"))],
            "scope_held": [dict(brief(r), would_tabs=r["_would"]) for r in held_rows],
            "scope_promoted": promoted, "include": a.include or "", "issue_after_window": after,
            "not_returned": missing_req, "notes": notes,
            "files": {"keywords": kp.name, "entities": ep.name}}
    sp.write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"데이터 기준월 {ref} · 계산 창 {cur_m[0]}~{cur_m[-1]} vs 전년 {prev_m[0]}~{prev_m[-1]}"
          f" ({'마지막 달 포함' if a.include_last_month else '마지막 달 제외 — 기본'})")
    print(f"세그먼트 «{seg_label}» · gl={gl} · {'적용' if segment_on else '미적용(전체 순위)'}"
          + (f" · 기준 비중 {baseline:.3f} (후보 풀 {len(pool)}개 볼륨 가중 [추정])" if baseline else ""))
    for n in notes:
        print("⚠️ " + n)
    print(f"순위: 급등 {len(surge)} · 신규 {len(newc)} · 과대 지수 {len(over)} | 게이트 미달 {cnt('게이트 미달')} · "
          f"세그먼트 기저 미달 {cnt('세그먼트 기저')} · 계산 불가 {cnt('계산 불가')} · 세그먼트 계산 불가 {cnt('세그먼트 계산 불가')} · "
          f"같은 검색 {cnt('같은 검색')} · 표기변형 {cnt('표기변형')}")
    if exploded:
        print("  기저가 작아 전년비가 부푼 행(순위에서 뺌 · T4): "
              + ", ".join(f"{r['keyword']} {r['yoy']:+.0%} (전년 창 {r['volume_prev_year_window']:,})" for r in exploded))
    if seg_small:
        print("  전체로는 올랐지만 세그먼트 기저가 작아 순위에서 뺀 행(T4): "
              + ", ".join(f"{r['keyword']} {r['yoy']:+.0%} (세그먼트 전년 창 {r['segment_volume_prev_year']:,})" for r in seg_small))
    if small_over:
        print(f"  과대 지수는 높지만 볼륨 게이트 미달이라 순위에서 뺀 행: {', '.join(small_over)}")
    if top_yoy and surge and top_yoy["keyword"] != surge[0]["keyword"]:
        print(f"  급등 1위 기준 차이 — 세그먼트 절대 증가 1위 {surge[0]['keyword']} · 전년비 1위 {top_yoy['keyword']} (둘 다 적는다)")
    if missing_req:
        print(f"  요청했지만 응답에 없음 [데이터 공백]: {', '.join(missing_req)}")
    print(f"→ {kp} · {ep} · {sp}")
    return 0


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("response", help="keyword_info data_type=all 응답 JSON")
    ap.add_argument("--gl", default="")
    ap.add_argument("--gender", choices=list(GENDER), default="all")
    ap.add_argument("--age", choices=list(AGE), default="all")
    ap.add_argument("--category", required=True)
    ap.add_argument("--sources", help="candidate_sources.csv (출처 컬럼)")
    ap.add_argument("--rules", default="")
    ap.add_argument("--entity-rules")
    ap.add_argument("--gate", type=int, default=3000, help="전체 전년 창 최소 볼륨 [가정]")
    ap.add_argument("--seg-gate", type=int, default=1000, help="세그먼트 창 최소 볼륨 [가정]")
    ap.add_argument("--window", type=int, default=3)
    ap.add_argument("--min-growth", type=float, default=0.3)
    ap.add_argument("--min-index", type=float, default=1.2)
    ap.add_argument("--min-pool", type=int, default=5)
    ap.add_argument("--new-within", type=int, default=12)
    ap.add_argument("--include-last-month", action="store_true")
    ap.add_argument("--requested", help="all 에 요청한 목록 JSON — 미반환을 [데이터 공백]으로 적는다")
    ap.add_argument("--include", default="", help="범위 미확인 행을 순위에 올릴 정규식(사람 판단 · summary.scope_promoted 에 남는다)")
    ap.add_argument("-d", "--dir", default=".")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
