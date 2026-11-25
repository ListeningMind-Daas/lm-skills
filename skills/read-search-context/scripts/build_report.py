#!/usr/bin/env python3
"""리포트 조립 (이 스킬 고유 스크립트) — 저장 계산 결과 + 에이전트가 쓴 해석 → report.json. API 호출 없음.

숫자는 여기서 만든 사실 목록(facts)에서만 나온다. 에이전트는 `brief` 가 낸 입력을 읽고 해석(analysis.json)만 쓰고,
`assemble` 이 그 해석을 검사한 뒤 공통 렌더러 입력으로 조립한다. 호출 기록은 수집 원장(calls.jsonl)에서 만든다.

    python3 scripts/build_report.py brief    --run <실행 폴더>
    python3 scripts/build_report.py assemble --run <실행 폴더> --analysis <실행 폴더>/analysis.json -o <실행 폴더>/report.json
    python3 scripts/render_report.py <실행 폴더>/report.json -o <실행 폴더>/report.html
"""
import argparse, csv, json, pathlib, re, sys

ROLES = ("콘텐츠", "퍼포먼스", "브랜드", "상품기획", "SEO", "마케팅")
OVERCLAIM = ("전환율", "이탈률", "한 사람이", "구매로 이어", "시장 점유율", "시장 규모는")
ANALYSIS_KEYS = ["lede", "summary", "paths", "actions"]


def skill_version():
    """판 번호의 정본은 SKILL.md frontmatter 다."""
    f = pathlib.Path(__file__).resolve().parent.parent / "SKILL.md"
    m = re.search(r'^\s*version:\s*"?([\w.]+)"?', f.read_text(encoding="utf-8"), re.M) if f.is_file() else None
    return m.group(1) if m else "0"


def read_csv(f):
    return list(csv.DictReader(open(f, encoding="utf-8-sig"))) if pathlib.Path(f).is_file() else []


def load(run):
    run = pathlib.Path(run)
    j = lambda p, d=None: json.loads((run / p).read_text(encoding="utf-8")) if (run / p).is_file() else d
    calls = [json.loads(x) for x in (run / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] \
        if (run / "calls.jsonl").is_file() else []
    return {"run": run, "req": j("request.json", {}), "sum": j("out/context_summary.json", {}), "tree": j("out/context_tree.json", {}),
            "ev": j("out/evidence.json", []), "calls": calls, "edges": read_csv(run / "out/context_edges.csv"),
            "nodes": read_csv(run / "out/context_nodes.csv"), "reps": read_csv(run / "out/representative_paths.csv"),
            "targets": read_csv(run / "out/serp_targets.csv"), "trend_targets": read_csv(run / "out/trend_targets.csv"),
            "trend_rows": read_csv(run / "out/trend.csv"), "refmonth": j("out/refmonth.json", None), "monthly": j("raw/monthly.json", None)}


GATE_DEFAULT = 3000       # [가정] trend_yoy.py --gate 기본값과 같게 둔다 — 전년 같은 석 달 합이 이보다 작으면 순위 문장의 주어로 쓰지 않는다


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    k = y * 12 + (m - 1) - n
    return f"{k // 12}-{k % 12 + 1:02d}"


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def trend_of(d, gate):
    """월별 검색량 단계 결과 → 대상별 전년 비교 행. 월별 검색량을 받지 않았으면 None."""
    rm, rows = d.get("refmonth"), {r["keyword"]: r for r in d.get("trend_rows") or []}
    if not rm or not d.get("trend_targets"):
        return None
    ref = rm["data_ref_month"]
    out = []
    for t in d["trend_targets"]:
        r = rows.get(t["keyword"])
        if not r:
            out.append({**t, "row": None}); continue
        b, c = num(r.get("sum_3m_prev_year")), num(r.get("sum_3m_cur"))
        yoy, yx = fnum(r.get("yoy")), fnum(r.get("yoy_ex_last_month"))
        out.append({**t, "row": r, "base": b, "cur": c, "abs": (c - b) if (b is not None and c is not None and yoy is not None) else None,
                    "pct": round(yoy * 100, 1) if yoy is not None else None, "pct_x": round(yx * 100, 1) if yx is not None else None,
                    "below_gate": b is not None and yoy is not None and b < gate,
                    # 공유 규칙(최신 달이 전월보다 크게 떨어짐)에 걸려도, 최신 달을 넣은 전년비가 뺀 것보다 높으면 미완결 근거가 약하다
                    "flag": bool(r.get("last_month_flag")) and yoy is not None and yx is not None and yoy < yx,
                    "weak_flag": bool(r.get("last_month_flag")) and not (yoy is not None and yx is not None and yoy < yx),
                    "flip": yoy is not None and yx is not None and yoy * yx < 0,
                    "missing": num(r.get("missing_months_in_window")) or 0})
    series = {}
    for r in (d.get("monthly") or {}).get("data") or []:
        if isinstance(r, dict) and r.get("keyword") == d["trend_targets"][0]["keyword"]:
            series = {m["month"]: m.get("total") for m in r.get("monthly_volume") or [] if isinstance(m, dict)}
    return {"ref": ref, "lag": rm.get("lag_months"), "queried": rm.get("queried_at"), "rows": out, "gate": gate,
            "cur": [shift(ref, i) for i in (2, 1, 0)], "prev": [shift(ref, i) for i in (14, 13, 12)], "seed_series": series}


def num(v):
    return int(v) if str(v).strip().lstrip("-").isdigit() else None


def facts_of(d, gate=GATE_DEFAULT):
    s, calls = d["sum"], d["calls"]
    vol = {n["canonical"]: n for n in d["nodes"]}
    cid = lambda tool, step=None: next((c["id"] for c in calls if c["tool"] == tool and (not step or c.get("step") == step)), "raw")
    pc = cid("path_finder")
    ok = [e for e in d["ev"] if e["status"] == "ok"]
    F = {}

    def add(k, value, unit, label, source, calc):
        F[k] = {"value": value, "unit": unit, "label": label, "source": source, "calc": calc}
    add("F1", s.get("requested_limit") or d["req"].get("limit"), "개", "[가정]", "request.json", "요청한 경로 수")
    add("F2", s.get("returned_paths"), "개", "[실제 데이터]", pc, "받은 경로 수")
    add("F3", s.get("seed_paths"), "개", "[실제 데이터]", pc, "받은 경로 중 시드를 지나는 경로 수")
    add("F4", s.get("unique_keywords"), "개", "[실제 데이터]", pc, "받은 경로에 나온 서로 다른 검색어 수")
    add("F5", s.get("unique_groups"), "개", "[추정]", pc, "표기를 정규화해 묶은 뒤의 검색어 묶음 수")
    add("F6", s.get("variant_loops"), "건", "[추정]", pc, "표기만 바뀐 인접 — 앞뒤 검색에서 뺀 수")
    if num(s.get("seed_volume")) is not None:
        add("F7", num(s["seed_volume"]), "건", "[실제 데이터]", cid("keyword_info", "검색량"), "시드의 volume_avg(최근 3개월 평균 월 검색량)")
    add("F8", len(d["ev"]), "개", "[가정]", "out/serp_targets.csv", "검색 결과 화면을 보기로 한 검색어 수")
    add("F9", len(ok), "개", "[실제 데이터]", "out/evidence.json", "저장된 검색 결과 화면이 있던 검색어 수")
    add("F10", sum(1 for e in ok if e["aio"]), "개", "[실제 데이터]", "out/evidence.json", "AI 개요가 있던 검색어 수")
    add("F11", len(d["ev"]) - len(ok), "개", "[데이터 공백]", "out/evidence.json", "검색 결과 화면을 보기로 한 검색어 중 저장본이 없거나 예산으로 부르지 않은 수(대상에 넣지 않은 검색어는 세지 않는다)")
    add("F12", sum(c.get("cost") or 0 for c in calls), "크레딧", "[실제 데이터]", "calls.jsonl", "호출 비용(cost_detail.total_cost)의 합")
    add("F13", s.get("volume_missing"), "개", "[데이터 공백]", cid("keyword_info", "검색량"), "검색량이 돌아오지 않은 묶음 수")
    for side, tag in (("post", "B"), ("pre", "A")):
        for i, n in enumerate((d["tree"].get(side) or [])[:5], 1):
            add(f"{tag}{i}N", n["n"], "개", "[실제 데이터]", pc, f"시드 바로 {'뒤' if side == 'post' else '앞'} 검색어 «{n['label']}» — 그 이음이 나온 경로 수")
            add(f"{tag}{i}S", n["s"], "개", "[추정]", pc, f"그 경로들의 서로 다른 줄기(앞 {s.get('stem_len')}걸음) 수")
            v = num((vol.get(n["k"]) or {}).get("volume_avg"))
            if v is not None:
                lab = (vol.get(n["k"]) or {}).get("volume_label") or "[실제 데이터]"
                add(f"{tag}{i}V", v, "건", lab, cid("keyword_info", "검색량"), f"검색어 «{n['label']}» — volume_avg(최신 3개월 평균 월 검색량 · 구글+네이버 · 계약 §2 · 묶음이면 하한)")
            gs = fnum((vol.get(n["k"]) or {}).get("google_share"))
            if gs is not None:
                add(f"{tag}{i}G", gs, "%", "[실제 데이터]", cid("keyword_info", "검색량"), f"검색어 «{n['label']}» — 구글 비중 = gg_volume_avg ÷ (gg_volume_avg + nv_volume_avg)")
    tr = trend_of(d, gate)
    if tr:
        mc = cid("keyword_info_monthly")
        add("F14", tr["lag"], "개월", "[실제 데이터]", mc, f"조회일 {tr['queried']}과 월별 검색량 기준월 {tr['ref']}의 차이(지연)")
        add("F15", sum(1 for x in tr["rows"] if x.get("flag")), "개", "[추정]", mc, "최신 달이 전년 같은 구간보다 30%p 이상 나쁘게 떨어졌고, 최신 달을 넣으면 전년비가 낮아져 미완결이 의심되는 검색어 수")
        add("F17", sum(1 for x in tr["rows"] if x.get("flip")), "개", "[실제 데이터]", mc, "최신 달을 빼고 석 달을 한 달 앞당기면 전년비의 방향(늘었다 · 줄었다)이 바뀌는 검색어 수")
        add("F16", gate, "건", "[가정]", "request.json", "기저 게이트 — 전년 같은 석 달 합이 이보다 작으면 순위 문장의 주어로 쓰지 않는다(trend_yoy.py --gate)")
        w = f"{tr['cur'][0]}~{tr['cur'][-1]}", f"{tr['prev'][0]}~{tr['prev'][-1]}"
        for x in tr["rows"]:
            if not x.get("row"):
                continue
            k, g = x["code"], " · 기저 게이트 미만 — 순위 문장의 주어로 쓰지 않는다" if x["below_gate"] else ""
            add(f"{k}B", x["base"], "건", "[실제 데이터]", mc, f"«{x['keyword']}» 전년 같은 석 달({w[1]}) monthly_volume total 합{g}")
            add(f"{k}C", x["cur"], "건", "[실제 데이터]", mc, f"«{x['keyword']}» 최근 석 달({w[0]}) monthly_volume total 합")
            if x["pct"] is not None:
                add(f"{k}D", x["abs"], "건", "[실제 데이터]", mc, f"«{x['keyword']}» 최근 석 달 − 전년 같은 석 달{g}")
                add(f"{k}P", x["pct"], "%", "[실제 데이터]", mc, f"«{x['keyword']}» 전년 같은 석 달 대비 증감률{g}")
            if x["pct_x"] is not None:
                add(f"{k}X", x["pct_x"], "%", "[실제 데이터]", mc, f"«{x['keyword']}» 최신 달을 뺀 석 달의 전년 대비 증감률 — 마지막 달 미완결 교차 확인용{g}")
    return F


def ev_index(ev):
    """근거 번호 → 내용. E2-AI · E2-1 형식."""
    idx = {}
    for e in ev:
        if e["status"] != "ok":
            continue
        if e["aio"]:
            idx[f"{e['code']}-AI"] = " ".join(x.strip() for x in e["aio"])
        for o in e["top"]:
            idx[f"{e['code']}-{o['position']}"] = f"[{o['site']}] {o['title']} — {o['snippet']}"
    return idx


def cmd_brief(a):
    d = load(a.run)
    F, s = facts_of(d, a.gate), d["sum"]
    vol = {n["canonical"]: n for n in d["nodes"]}
    L = [f"# 해석 입력 — 시드 «{s.get('seed')}» · {d['req'].get('gl')} · 현재 시점 · 조회일 {d['req'].get('queried_at')}", "",
         "아래 데이터만 근거로 `analysis.json` 을 쓴다. 숫자는 사실 ID(`{{F3}}`)로만 쓴다 — 문장에 숫자를 직접 적으면 렌더러가 멈춘다.",
         "**사실 ID 는 단위까지 붙어 나온다**(`{{F3}}` → «100개»). 뒤에 단위(개 · 건 · 크레딧)를 다시 쓰지 않는다 — 쓰면 조립이 멈춘다.",
         "검색어 · 제목처럼 숫자가 든 원문은 문장에 옮기지 않고 근거 번호(`E2-1`)나 경로 번호(`P1`)로 가리킨다.",
         "경로는 집계된 세션 인접 관계다 — «한 사람이 A 다음 B 를 검색했다»로 쓰지 않는다. 원인을 단정하지 않는다.",
         "**경로 수와 검색량을 함께 본다** — 경로 수가 큰 갈래도 검색량(`B1V` …)이 작으면 수요가 작다. 실행 카드의 우선순위는 둘을 견줘 정하고, 다르면 그 차이를 적는다.",
         "AI 개요 · 검색 결과 문구 속 제품 설명 · 효과 · 수치는 옮긴 말이다 — 사실처럼 단정하지 않는다.",
         "**구글 비중**(`B1G` …)은 콘텐츠 · 광고를 어디에 둘지(구글 · 유튜브 쪽인지 네이버 쪽인지) 정할 때 쓴다. 검색량 자체는 구글+네이버 합산으로 쓴다.", "",
         "## 사실 목록", "| ID | 값 | 라벨 | 뜻 |", "|---|---|---|---|"]
    L += [f"| {k} | {v['value']} {v['unit']} | {v['label']} | {v['calc']} |" for k, v in F.items()]
    if s.get("truncated"):
        L += ["", "**절단**: 받은 경로 수가 요청 수와 같다 — «전체 경로»라고 쓰지 않는다."]
    for side, name, step in (("post", "바로 뒤", "다음"), ("pre", "바로 앞", "더 앞")):
        L += ["", f"## 시드 {name} 검색 (경로 수 · 줄기 수 · 검색량)"]
        for n in (d["tree"].get(side) or [])[:10]:
            v = (vol.get(n["k"]) or {}).get("volume_avg") or "없음"
            gsh = (vol.get(n["k"]) or {}).get("google_share")
            L.append(f"- {n['label']} — 경로 {n['n']} · 줄기 {n['s']} · 검색량 {v}" + (f" · 구글 비중 {gsh}%" if gsh not in (None, "") else "") + (" · **줄기 1개: 순위로 쓰지 않는다**" if n["s"] == 1 and n["n"] > 1 else ""))
            for c in (n.get("c") or [])[:4]:
                L.append(f"  - {step}: {c['label']} — 경로 {c['n']} · 줄기 {c['s']}")
    tr = trend_of(d, a.gate)
    if tr:
        L += ["", f"## 작년 같은 시기와 비교 (월별 검색량 · 기준월 {tr['ref']} · 조회일 {tr['queried']} · 지연 F14)",
              f"최근 석 달 {tr['cur'][0]}~{tr['cur'][-1]} vs 전년 같은 석 달 {tr['prev'][0]}~{tr['prev'][-1]}. 비교하는 것은 **검색량뿐**이다 — 경로는 현재 시점 하나다.",
              f"전년 합이 게이트({tr['gate']:,}건) 미만인 검색어는 «가장 크게 늘었다» 같은 순위 문장의 주어로 쓰지 않는다. 최신 달 미완결 의심이면 «최신 달 제외»(…X) 값으로 교차 확인한다.",
              "증감률만 쓰지 않는다 — 전년 · 최근 합과 증감(…B · …C · …D)을 함께 쓴다. `volume_trend` 필드는 쓰지 않는다."]
        for x in tr["rows"]:
            if not x.get("row"):
                L.append(f"- {x['code']} «{x['keyword']}» — 월별 검색량이 돌아오지 않았다 [데이터 공백]"); continue
            if x["pct"] is None:
                L.append(f"- {x['code']} «{x['keyword']}» — 계산 불가(비교 창 결측 월 {x['missing']}개 또는 전년 합 0) [데이터 공백]"); continue
            tags = (" · **게이트 미만**" if x["below_gate"] else "") + (" · **최신 달 미완결 의심**" if x["flag"] else "") \
                + (" · 최신 달 급락이지만 전년 같은 달보다 강함 — 미완결로 보지 않는다" if x.get("weak_flag") else "") \
                + (" · **창을 한 달 옮기면 방향이 바뀐다 — 늘었다 · 줄었다를 단정하지 않는다**" if x.get("flip") else "")
            L.append(f"- {x['code']} «{x['keyword']}» — 전년 {x['code']}B {x['base']} → 최근 {x['code']}C {x['cur']} · 증감 {x['code']}D · 전년비 {x['code']}P"
                     + (f" · 최신 달 제외 {x['code']}X" if x["pct_x"] is not None else "") + tags)
    L += ["", "## 대표 경로"]
    L += [f"- {r['path_id']} · {r['path']} (갈래 경로 {r['branch_path_count'] or '–'} · 줄기 {r['branch_stem_count'] or '–'} · {r['reason']})" for r in d["reps"]]
    L += ["", "## 검색 결과 화면 근거"]
    for e in d["ev"]:
        if e["status"] != "ok":
            L.append(f"- {e['code']} «{e['keyword']}» — 근거 없음({e['status']}) [데이터 공백]")
            continue
        L.append(f"- {e['code']} «{e['keyword']}» (수집일 {str(e.get('collected_at') or '')[:10]})")
        L.append(f"  - {e['code']}-AI: " + (" ".join(x.strip() for x in e["aio"])[:1200] if e["aio"] else "AI 개요 없음"))
        L += [f"  - {e['code']}-{o['position']}: [{o['site']}] {o['title']} — {o['snippet'][:300]}" for o in e["top"]]
    out = d["run"] / "out"
    (out / "brief.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (out / "facts.json").write_text(json.dumps(F, ensure_ascii=False, indent=1), encoding="utf-8")
    tpl = {"lede": "두세 문장 요약 — 숫자는 {{F3}} 처럼",
           "summary": {"findings": ["핵심 발견 1~3개"], "support": "지지 근거", "counter": "반대 근거 — 줄기 1개 이음 · 저장본 없음 · 절단 등",
                       "decisions": "담당(콘텐츠 · 퍼포먼스 · 브랜드/상품기획)별로 가능한 결정"},
           "paths": [{"path_id": r["path_id"], "observed": "경로와 검색 결과 화면에서 본 것", "interpretation": "무엇을 하려는 검색인가 · 지금 검색 결과가 그 목적을 채우는가 [추정]",
                      "alternatives": "다르게 읽을 수 있는 경우", "limits": "판단 한계", "evidence": ["E1-AI", "E2-1"], "actions": ["A1"]} for r in d["reps"]],
           "headings": {"summary": "(선택) 요약 헤딩 — 결론을 말하는 문장", "trend": "(선택)", "around": "(선택)", "branches": "(선택)", "purpose": "(선택)", "action": "(선택)"},
           "takeaways": {"post": "(선택) 막대 차트 한 줄 해석", "trend": "(선택) 전년 비교 표 한 줄 해석", "seasonal": "(선택) 월별 선 차트 한 줄 해석"},
           "actions": [{"id": "A1", "paths": ["P1"], "hypothesis": "검증할 가설", "deliverable": "결과물(글 · 광고 키워드 · 랜딩 페이지 · 메시지)",
                        "owner_role": "콘텐츠·SEO | 퍼포먼스 마케터 | 브랜드·상품기획", "internal_data": "함께 볼 내부 지표 이름",
                        "criteria": {"expand": "확대 조건", "revise": "수정 조건", "stop": "중단 조건"}}]}
    (out / "analysis.template.json").write_text(json.dumps(tpl, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"해석 입력 → {out / 'brief.md'} · 사실 {len(F)}개 → facts.json · 작성 틀 → analysis.template.json")
    return 0


JOSA = {"이라": ("이라", "라"), "라": ("이라", "라"), "으로": ("으로", "로"), "로": ("으로", "로"), "은": ("은", "는"), "는": ("은", "는"), "을": ("을", "를"), "를": ("을", "를"),
        "과": ("과", "와"), "와": ("과", "와"), "가": ("이", "가")}


def josa_for(unit, j):
    """단위의 마지막 글자 받침으로 맞는 조사를 고른다 — 사실 ID 는 «값+단위»로 렌더된다(«443건»)."""
    if not unit:
        return None
    ch = unit[-1]
    if ch == "%":
        ch = "트"                                   # «퍼센트»로 읽는다
    if not ("가" <= ch <= "힣"):
        return None
    jong = (ord(ch) - 0xAC00) % 28
    with_b, without_b = JOSA[j]
    if j in ("으로", "로") and jong == 8:          # ㄹ 받침은 «로»
        return "로"
    return with_b if jong else without_b


def check_analysis(an, d, idx):
    errs, warns = [], []
    need = lambda cond, msg: errs.append(msg) if not cond else None
    for k in ANALYSIS_KEYS:
        need(an.get(k), f"`{k}` 가 비었다")
    sm = an.get("summary") or {}
    need(1 <= len(sm.get("findings") or []) <= 3, "summary.findings 는 1~3개")
    for k in ("support", "counter", "decisions"):
        need(str(sm.get(k) or "").strip(), f"summary.{k} 가 비었다 — 반대 근거 없이 요약하지 않는다")
    rep_ids = [r["path_id"] for r in d["reps"]]
    got = {p.get("path_id"): p for p in an.get("paths") or []}
    need(set(got) == set(rep_ids), f"paths 는 대표 경로마다 하나씩 — 필요 {rep_ids} · 받은 {sorted(map(str, got))}")
    act_ids = [x.get("id") for x in an.get("actions") or []]
    for pid, p in got.items():
        for k in ("observed", "interpretation", "alternatives", "limits"):
            need(str(p.get(k) or "").strip(), f"{pid}.{k} 가 비었다")
        bad = [c for c in p.get("evidence") or [] if c not in idx]
        need(not bad, f"{pid} 의 근거 번호가 저장된 근거에 없다: {bad}")
        if not p.get("evidence"):
            need("데이터 공백" in str(p.get("limits")), f"{pid} 는 근거 번호가 없다 — limits 에 «데이터 공백»을 밝힌다")
        need(not [x for x in p.get("actions") or [] if x not in act_ids], f"{pid} 가 없는 실행 카드를 가리킨다")
    need(3 <= len(act_ids) <= 5, f"실행 카드는 3~5개 — 받은 {len(act_ids)}개")
    need(len(set(act_ids)) == len(act_ids) and all(act_ids), "실행 카드 ID 가 비었거나 겹친다")
    for x in an.get("actions") or []:
        for k in ("hypothesis", "deliverable", "owner_role", "internal_data"):
            need(str(x.get(k) or "").strip(), f"{x.get('id')}.{k} 가 비었다")
        need(all(str((x.get("criteria") or {}).get(k) or "").strip() for k in ("expand", "revise", "stop")), f"{x.get('id')} 의 확대 · 수정 · 중단 조건이 비었다")
        need(any(r in str(x.get("owner_role")) for r in ROLES), f"{x.get('id')}.owner_role 에 담당 역할이 없다")
        need(not [p for p in x.get("paths") or [] if p not in rep_ids], f"{x.get('id')} 가 없는 경로를 가리킨다")
    text = json.dumps(an, ensure_ascii=False)
    facts = facts_of(d)
    bad_josa = []
    for fid, j in re.findall(r"\{\{\s*(\w+)\s*\}\}(이라|라|으로|로|은|는|을|를|과|와|가)(?=[\s,.·:;)\]」»'\"<]|$)", text):
        want = josa_for(str((facts.get(fid) or {}).get("unit") or ""), j)
        if want and want != j:
            bad_josa.append(f"{{{{{fid}}}}}{j} → {{{{{fid}}}}}{want}")
    need(not bad_josa, f"사실 ID 뒤 조사가 단위와 맞지 않는다(예: «443건로» → «443건으로»): {sorted(set(bad_josa))[:4]}")
    dup = sorted(set(re.findall(r"\{\{\s*\w+\s*\}\}\s*(?:개|건|크레딧)", text)))
    need(not dup, f"사실 ID 뒤에 단위를 또 썼다 — 렌더하면 «100개 개»가 된다: {dup[:4]}")
    prose = json.loads(text)
    for x in prose.get("actions") or []:
        x.pop("internal_data", None)                       # 내부 지표 이름(예: 랜딩 이탈률)은 주장이 아니다
    blob = json.dumps(prose, ensure_ascii=False)
    warns += [f"과한 표현 후보 «{w}» — 세션 인접 검색으로는 말할 수 없다. «답할 수 없다»고 알리는 문장이면 그대로 둔다" for w in OVERCLAIM if w in blob]
    return errs, warns


def table(caption, cols, rows, meta=None):
    b = {"type": "table", "caption": caption, "columns": [{"key": k, "label": lab} for k, lab in cols], "rows": rows}
    if meta:
        b["meta"] = meta
    return b


def cmd_assemble(a):
    d = load(a.run)
    an = json.loads(pathlib.Path(a.analysis).read_text(encoding="utf-8"))
    idx = ev_index(d["ev"])
    errs, warns = check_analysis(an, d, idx)
    for w in warns:
        print(f"⚠ {w}")
    if errs:
        print("✗ 해석(analysis.json)을 고친 뒤 다시 조립한다:\n" + "\n".join(f"  - {e}" for e in errs), file=sys.stderr)
        return 1
    F, s, req = facts_of(d, a.gate), d["sum"], d["req"]
    pick = lambda m: {k: v for k, v in (m or {}).items() if str(v or "").strip() and not str(v).startswith("(선택)")}
    hd, tk = pick(an.get("headings")), pick(an.get("takeaways"))      # 작성 틀의 «(선택)» 자리는 비운 것으로 본다
    tr = trend_of(d, a.gate)
    vol = {n["canonical"]: n for n in d["nodes"]}
    v_of = lambda k: num((vol.get(k) or {}).get("volume_avg"))
    period = f"현재 시점(curr) · 조회일 {req.get('queried_at')}"
    base_meta = {"period": period, "source": "path_finder 원본 응답", "limits": "받은 경로 기준 · 세션 인접 집계이며 사람 수가 아니다"}
    ctx = pathlib.Path(__file__).resolve().parent.parent / "assets" / "context.json"
    boundary = ("시드를 지나는 경로 중 받은 {{F2}}(현재 시점 · 요청 {{F1}})와 검색 결과 화면 저장본 {{F9}}를 관측했고, "
                + (f"월별 검색량은 기준월 {tr['ref']}까지 받았다. 받지 않은 경로 · 지난 시점의 경로 · 구매 여부는 관측하지 못했다." if tr
                   else "받지 않은 경로 · 과거 시점 · 구매 여부는 관측하지 못했다."))
    if s.get("truncated"):
        boundary += " 받은 경로 수가 요청 수와 같아 전체 경로가 아니다."

    def side_rows(side):
        return [{"q": n["label"], "n": n["n"], "s": n["s"], "v": v_of(n["k"]), "g": fnum((vol.get(n["k"]) or {}).get("google_share")),
                 "l": (vol.get(n["k"]) or {}).get("volume_label", ""),
                 "note": "줄기 한 개 — 순위로 읽지 않는다" if n["s"] == 1 and n["n"] > 1 else ""} for n in (d["tree"].get(side) or [])[:10]]
    vhead = "월 검색량(건 · 구글+네이버)" if req.get("gl") == "kr" else "월 검색량(건)"
    side_cols = [("q", "검색어"), ("n", "경로 수"), ("s", "줄기 수"), ("v", vhead)] + ([("g", "구글 비중(%)")] if req.get("gl") == "kr" else []) + [("l", "검색량 라벨")]
    post = (d["tree"].get("post") or [])[:8]
    sm = an["summary"]
    S = [{"kind": "summary", "title": hd.get("summary") or "요약 — 앞뒤 검색과 검색 목적",
          "blocks": [{"type": "paragraph", "html": x} for x in sm["findings"]] + [
              {"type": "paragraph", "html": f"<b>지지 근거</b> {sm['support']}"},
              {"type": "paragraph", "html": f"<b>반대 근거</b> {sm['counter']}"},
              {"type": "paragraph", "html": f"<b>가능한 결정</b> {sm['decisions']}"}]}]
    if tr:
        rows_t = [x for x in tr["rows"]]
        tb = [{"q": x["keyword"], "b": x.get("base"), "c": x.get("cur"), "ab": x.get("abs"), "p": x.get("pct"), "x": x.get("pct_x"),
               "note": "월별 검색량 없음 [데이터 공백]" if not x.get("row") else
                       "계산 불가 — 비교 창에 결측 월 또는 전년 합 0 [데이터 공백]" if x.get("pct") is None else
                       " · ".join(n for n in ("기저 게이트 미만(전년 석 달 합이 게이트보다 작다) — 순위로 읽지 않는다" if x["below_gate"] else "",
                                              "최신 달 미완결 의심 — 최신 달 제외 값으로 교차 확인" if x["flag"] else "",
                                              "최신 달이 전월보다 크게 떨어졌지만 전년 같은 달보다는 강하다 — 미완결 근거가 약하다" if x.get("weak_flag") else "",
                                              "창을 한 달 옮기면 방향이 바뀐다 — 방향을 단정하지 않는다" if x.get("flip") else "") if n)} for x in rows_t]
        win = f"최근 {tr['cur'][0]}~{tr['cur'][-1]} · 전년 {tr['prev'][0]}~{tr['prev'][-1]}"
        tm = {"period": win, "unit": "건 · %", "source": "keyword_info/monthly_volume 원본 응답",
              "method": "monthly_volume 의 total 을 석 달씩 더해 견준다. 결측 월이 있으면 0 으로 채우지 않고 계산하지 않는다",
              "limits": "검색량 기준 · 월 그레인 · 최신 달은 아직 채워지는 중일 수 있다 · 경로는 견주지 않는다(현재 시점 하나)",
              "takeaway": tk.get("trend") or "전년 같은 석 달과 최근 석 달의 검색량 합을 나란히 둔다. 게이트 미만 행은 순위로 읽지 않는다."}
        B = [{"type": "paragraph", "html": f"월별 검색량 기준월 {tr['ref']} · 조회일 {tr['queried']} · 지연 {{{{F14}}}}. 비교는 {win}. "
                                         "비교하는 것은 검색량뿐이다 — 검색 경로는 현재 시점 하나만 받았다. "
                                         "전년 같은 석 달 합이 기저 게이트 {{F16}}보다 작은 검색어는 순위로 읽지 않는다."}]
        if F.get("F17", {}).get("value"):
            B.append({"type": "paragraph", "html": "석 달 창을 한 달 앞당기면 전년비의 방향이 바뀌는 검색어가 {{F17}} 있다 — 계절의 꼭대기 · 바닥이 창 경계에 걸린 경우다. 그 검색어는 늘었다 · 줄었다를 단정하지 않는다."})
        if F.get("F15", {}).get("value"):
            B.append({"type": "paragraph", "html": "최신 달이 아직 덜 찬 것으로 보이는 검색어가 {{F15}} 있다 — «최신 달 제외 전년비» 열로 교차 확인한다."})
        B.append({**table("전년 같은 석 달과 최근 석 달", [("q", "검색어"), ("b", "전년 같은 석 달(건)"), ("c", "최근 석 달(건)"), ("ab", "증감(건)"),
                                                     ("p", "전년비(%)"), ("x", "최신 달 제외 전년비(%)")], tb, {**tm, "title": "전년 같은 석 달과 최근 석 달"}),
                  "columns": [{"key": "q", "label": "검색어"}, {"key": "b", "label": "전년 같은 석 달(건)", "role": "base"},
                              {"key": "c", "label": "최근 석 달(건)", "role": "compare"}, {"key": "ab", "label": "증감(건)", "role": "abs_change"},
                              {"key": "p", "label": "전년비(%)", "role": "pct_change"}, {"key": "x", "label": "최신 달 제외 전년비(%)"}]})
        ss = tr["seed_series"]
        last12 = [shift(tr["ref"], i) for i in range(11, -1, -1)]
        if sum(1 for m in last12 if ss.get(m) is not None) >= 2:
            B.append({"type": "chart", "chart": "line", "meta": {"title": f"«{tr['rows'][0]['keyword']}» 월별 검색량 — 최근 12개월과 그 전 12개월",
                      "takeaway": tk.get("seasonal") or "같은 달끼리 견준다. 결측 월은 끊어 그린다.",
                      "period": f"{shift(tr['ref'], 23)}~{tr['ref']}", "unit": "건", "method": "monthly_volume 의 total(검색어 한 행에 최근 4년 월별 값이 들어 있다 — 과금은 행 단위) — 그 전 12개월 계열은 12개월 앞 같은 달",
                      "source": "keyword_info/monthly_volume 원본 응답", "limits": "월 그레인 · 최신 달 미완결 가능 · 결측 월은 0 이 아니다"},
                      "data": {"x": last12, "series": [{"name": "최근 12개월", "values": [ss.get(m) for m in last12]},
                                                        {"name": "그 전 12개월", "values": [ss.get(shift(m, 12)) for m in last12]}],
                               "normalized": False}})
        S.append({"kind": "trend", "title": hd.get("trend") or "작년 같은 시기와 비교 (Q5)", "blocks": B})
    b1 = []
    if post:
        b1.append({"type": "chart", "chart": "bar", "meta": {**base_meta, "title": "시드 바로 뒤 검색 상위",
                   "takeaway": tk.get("post") or "막대는 그 검색어가 시드 바로 뒤에 온 경로 수다. 줄기 수가 한 개인 항목은 순위로 읽지 않는다.",
                   "unit": "경로 수(개)", "method": "시드 바로 뒤 이음을 표기 묶음 기준으로 집계 — 표기만 바뀐 인접은 제외"},
                   "data": {"labels": [n["label"] for n in post], "values": [n["n"] for n in post], "value_unit": "개"}})
    b1.append(table("시드 바로 뒤 검색", side_cols, side_rows("post")))
    b1.append(table("시드 바로 앞 검색", side_cols, side_rows("pre")))
    S.append({"kind": "structure", "title": hd.get("around") or "시드 바로 앞뒤 검색 (Q1)", "blocks": b1})
    S.append({"kind": "structure", "title": hd.get("branches") or "뒤로 갈수록 붙는 조건과 갈래 (Q2)", "blocks": [
        table("시드 뒤 갈래와 다음 단계", [("q", "갈래"), ("next", "다음 단계 상위"), ("n", "경로 수"), ("s", "줄기 수"), ("v", vhead)]
              + ([("g", "구글 비중(%)")] if req.get("gl") == "kr" else []),
              [{"q": n["label"], "next": " · ".join(c["label"] for c in (n.get("c") or [])[:4]) or "—", "n": n["n"], "s": n["s"], "v": v_of(n["k"]),
                "g": fnum((vol.get(n["k"]) or {}).get("google_share"))}
               for n in (d["tree"].get("post") or [])[:5]]),
        {"type": "paragraph", "html": "갈래를 펼쳐 보려면 함께 나온 맵 파일(<code>context_map.html</code>)을 연다. 맵은 시드에서 네 걸음까지, 단계마다 상위 세 개를 먼저 보여 준다 [가정]."}]})
    got = {p["path_id"]: p for p in an["paths"]}
    b3 = [table("대표 경로", [("id", "번호"), ("path", "경로"), ("n", "갈래 경로 수"), ("s", "갈래 줄기 수"), ("why", "고른 이유")],
                [{"id": r["path_id"], "path": r["path"].replace(" > ", " → "), "n": num(r["branch_path_count"]), "s": num(r["branch_stem_count"]), "why": r["reason"]} for r in d["reps"]])]
    for i, r in enumerate(d["reps"], 1):
        p = got[r["path_id"]]
        fact = next((f"B{j}N" for j, n in enumerate((d["tree"].get("post") or [])[:5], 1) if n["label"] == r["branch"]), "F3")
        codes = " · ".join(p.get("evidence") or []) or "근거 번호 없음"
        b3.append({"type": "finding", "id": f"D{i}", "evidence": [fact, "F9"], "observed": f"{r['path_id']} — {p['observed']} (근거 {codes})",
                   "interpretation": p["interpretation"], "alternatives": p["alternatives"], "limits": p["limits"], "actions": p.get("actions") or []})
    ev_rows = []
    for e in d["ev"]:
        day = str(e.get("collected_at") or "")[:10] or None
        if e["status"] != "ok":
            ev_rows.append({"code": e["code"], "q": e["keyword"], "day": day, "kind": "근거 없음", "site": "", "text": {"no_snapshot": "저장본 없음", "not_called": "부르지 않음"}.get(e["status"], "오류") + " [데이터 공백]"})
            continue
        ev_rows.append({"code": f"{e['code']}-AI", "q": e["keyword"], "day": day, "kind": "AI 개요", "site": "", "text": " ".join(x.strip() for x in e["aio"])[:600] if e["aio"] else "AI 개요 없음"})
        ev_rows += [{"code": f"{e['code']}-{o['position']}", "q": e["keyword"], "day": day, "kind": f"{o['position']}위", "site": o["site"], "text": f"{o['title']} — {o['snippet']}"[:400]} for o in e["top"]]
    b3.append({"type": "details", "summary": "근거 — 검색 결과 화면(AI 개요 · 1~3위)", "blocks": [
        {"type": "paragraph", "html": "아래 AI 개요 · 검색 결과 문구는 저장된 화면에서 옮긴 것이다. 그 안의 제품 설명 · 효과 · 수치는 확인하지 않았다 — 밖에 쓰기 전에 사실 확인을 거친다."},
        table("검색 결과 화면 근거", [("code", "근거 번호"), ("q", "검색어"), ("day", "수집일"), ("kind", "구분"), ("site", "사이트"), ("text", "내용")], ev_rows)]})
    S.append({"kind": "structure", "title": hd.get("purpose") or "갈래별 검색 목적 (Q3)", "blocks": b3})
    acts = []
    for x in an["actions"]:
        f = [next((f"B{j}N" for j, n in enumerate((d["tree"].get("post") or [])[:5], 1)
                   if n["label"] == next((r["branch"] for r in d["reps"] if r["path_id"] == pid), None)), "F3") for pid in x.get("paths") or []] or ["F3"]
        acts.append({"type": "action", "id": x["id"], "evidence": sorted(set(f)), "hypothesis": x["hypothesis"], "deliverable": x["deliverable"],
                     "owner_role": x["owner_role"], "internal_data": x["internal_data"], "criteria": x["criteria"]})
    S.append({"kind": "action", "title": hd.get("action") or "어디에 무엇을 낼까 (Q4)", "blocks": acts})
    has_ev = lambda r: any(e["status"] == "ok" for e in d["ev"] if e["keyword"] in r["path"].split(" > "))
    q3 = sum(1 for r in d["reps"] if has_ev(r))
    status = [{"id": "Q1", "q": "시드 바로 앞과 뒤에 무엇을 검색하나", "st": "답함" if d["edges"] else "미해결"},
              {"id": "Q2", "q": "뒤로 갈수록 어떤 조건이 붙어 갈래가 나뉘나", "st": "답함" if any(n.get("c") for n in d["tree"].get("post") or []) else "일부"},
              {"id": "Q3", "q": "갈래마다 무엇을 하려는 검색인가", "st": "답함" if d["reps"] and q3 == len(d["reps"]) else ("일부" if q3 else "미해결")},
              {"id": "Q4", "q": "어디에 무엇을 내야 하나", "st": "답함 — 검증 전 가설"},
              {"id": "Q5", "q": "작년 같은 시기와 비교하면 검색량이 어떻게 달라졌나",
               "st": "조회하지 않음" if not tr else ("답함 — 검색량만(경로는 현재 시점)" if any(x.get("pct") is not None for x in tr["rows"]) else "미해결 — 계산 불가")}]
    S.append({"kind": "appendix", "title": "근거 부록", "blocks": [
        table("질문별 상태", [("id", "질문"), ("q", "내용"), ("st", "상태")], status),
        table("앞뒤 이음 전체(네 걸음까지)", [("pos", "위치"), ("d", "걸음"), ("f", "앞 검색어"), ("t", "뒤 검색어"), ("n", "경로 수"), ("s", "줄기 수")],
              [{"pos": {"pre_seed": "시드 앞", "post_seed": "시드 뒤"}[e["position"]], "d": num(e["depth"]), "f": e["from_example"], "t": e["to_example"], "n": num(e["path_count"]), "s": num(e["stem_count"])} for e in d["edges"]]),
        table("검색어와 검색량", [("q", "검색어"), ("var", "묶인 표기"), ("v", "월 검색량 하한(건)"), ("vu", "상한(건)")]
              + ([("gg", "구글(건)"), ("nv", "네이버(건)"), ("g", "구글 비중(%)")] if req.get("gl") == "kr" else []) + [("l", "라벨"), ("p", "나온 경로 수")],
              [{"q": n["example"], "var": n["variants"], "v": num(n["volume_avg"]), "vu": num(n["volume_avg_upper"]),
                "gg": num(n.get("google_avg")), "nv": num(n.get("naver_avg")), "g": fnum(n.get("google_share")),
                "l": n["volume_label"], "p": num(n["paths_with_keyword"])} for n in d["nodes"]]),
        {"type": "paragraph", "html": "<b>계산 정의</b> 경로 수 = 그 이음이 나온 서로 다른 경로의 수. 줄기 수 = 그 경로들의 앞 세 걸음이 서로 다른 가짓수 [가정]. "
                                      "표기 묶음 = 대소문자 · 띄어쓰기 · 치환 규칙을 맞춘 뒤 같은 검색어 [추정]. 월 검색량 = <code>volume_avg</code>(한국은 구글+네이버 합산 · 구글 비중 = 구글 ÷ (구글 + 네이버) · 최근 석 달 평균), 묶음은 하한(가장 큰 값)과 상한(합)을 함께 둔다. "
                                      "검색 목적과 실행 카드는 해석이다 [추정]."}]})
    report = {"meta": {"title": f"{'검색 맥락' if req.get('lang', 'kr') == 'kr' else 'Search Context'} — {s.get('seed')}", "subtitle": f"{req.get('gl')} · 현재 시점 · 조회일 {req.get('queried_at')}", "skill": "read-search-context",
                       "version": a.version},
              "boundary": boundary, "lede_html": an["lede"],
              "scope": {"collected_at": req.get("queried_at"), "latest_month": tr["ref"] if tr else "확인 불가(월별 검색량 미조회 — ads_metrics 만 사용)", "gl": req.get("gl"),
                        "selection": f"시드 «{s.get('seed')}» 하나 · path_finder 현재 시점 · 받은 순서 그대로 · " + ("회사 맥락 적용" if ctx.is_file() else "회사 맥락 미설정(범용)"),
                        "missing": f"검색량 없는 묶음 {s.get('volume_missing')}개 · 검색 결과 화면 근거 없는 검색어 {F['F11']['value']}개",
                        "excluded": f"표기만 바뀐 인접 {s.get('variant_loops')}건(앞뒤 검색에서 제외) · 시드를 지나지 않는 경로 {s.get('off_seed_paths')}개",
                        "windows": ({"base": f"{tr['prev'][0]}~{tr['prev'][-1]}(월별 검색량)", "compare": f"{tr['cur'][0]}~{tr['cur'][-1]}(월별 검색량) · 경로는 현재 시점 하나"}
                                    if tr else {"base": "현재 시점(curr) 하나", "compare": "비교 없음"})},
              "facts": F,
              "provenance": {"calls": [{k: c.get(k) for k in ("id", "step", "at", "tool", "params", "purpose", "returned", "cost", "raw", "status")} for c in d["calls"]],
                             "estimate": req.get("estimate"), "currency": "크레딧",
                             **({"monthly_used": m} if (m := next((c.get("monthly_used") for c in reversed(d["calls"]) if c.get("monthly_used")), None)) else {})},
              "sections": S}
    out = pathlib.Path(a.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report.json → {out} · 사실 {len(F)}개 · 발견 카드 {len(d['reps'])}개 · 실행 카드 {len(acts)}개 · 호출 {len(d['calls'])}건")
    print(f"다음: python3 scripts/render_report.py {out} -o {out.with_suffix('.html')}")
    return 0


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    s = ap.add_subparsers(dest="cmd", required=True)
    x = s.add_parser("brief"); x.add_argument("--run", required=True); x.add_argument("--gate", type=int, default=GATE_DEFAULT)
    x = s.add_parser("assemble"); x.add_argument("--run", required=True); x.add_argument("--analysis", required=True)
    x.add_argument("-o", "--out", required=True); x.add_argument("--version", default=skill_version())
    x.add_argument("--gate", type=int, default=GATE_DEFAULT, help="trend_yoy.py --gate 와 같은 값")
    a = ap.parse_args()
    return cmd_brief(a) if a.cmd == "brief" else cmd_assemble(a)


if __name__ == "__main__":
    sys.exit(main())
