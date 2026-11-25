#!/usr/bin/env python3
"""report.json 조립 — cep_map.json(저장 계산 결과)과 ledger.jsonl(수집 원장)에서만 만든다 (이 스킬 고유 · 0크레딧).

  - facts(사실 목록)는 cep_map.json 의 값으로만 만든다. 문구의 숫자는 {{ID}}로만 부른다
  - provenance.calls 는 ledger.jsonl 그대로 — 손으로 적지 않는다(렌더링 계약 §9)
  - CEP 이름 해석 · 메시지 가설 · 대안 설명은 규칙 문장이 기본이고, 에이전트가 쓴 문장은 --notes 로 넣는다.
    어느 쪽이든 [추정]이다. notes 문장에 숫자를 직접 쓰면 렌더러가 멈춘다 — {{ID}}만 쓴다

    python3 build_report.py --map out/cep_map.json --ledger ledger.jsonl -o report.json [--notes notes.json] [--estimate 6000]
    python3 render_report.py report.json -o report.html

notes.json 형식(모두 선택): {"D1": {"interpretation": "…", "alternatives": "…"}, "A1": {"hypothesis": "…", "deliverable": "…"}}
"""
import argparse, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cep_lib import load_json, utf8_console  # noqa: E402

TOP = 6   # 표 · 차트에 싣는 순위 CEP 수 (나머지는 부록 전체 표)


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="report.json 조립 (0크레딧)")
    ap.add_argument("--map", required=True); ap.add_argument("--ledger", required=True)
    ap.add_argument("--notes"); ap.add_argument("--estimate", type=int)
    ap.add_argument("--version", default="0.1.1")
    ap.add_argument("-o", "--out", default="report.json")
    a = ap.parse_args()

    doc = load_json(a.map)
    m, ceps = doc["meta"], doc["ceps"]
    notes = load_json(a.notes) if a.notes else {}
    calls = [json.loads(l) for l in open(a.ledger, encoding="utf-8") if l.strip()]
    if not calls:
        sys.exit("수집 원장이 비었다 — 호출 기록 없는 리포트는 만들지 않는다")
    first = {}
    for c in calls:   # 사실 출처로 쓸 호출 ID — 도구별 첫(재사용 아닌) 호출
        if c.get("cost", 0) >= 0 and c["tool"] not in first and not c.get("reused_from"):
            first[c["tool"]] = c["id"]
    src_vol = first.get("keyword_info", calls[0]["id"])
    src_all = next((c["id"] for c in calls if c["tool"] == "keyword_info" and "all" in str(c.get("params"))), src_vol)
    src_path = first.get("path_finder", "")

    facts, n = {}, [0]

    def F(value, unit, label, source, calc):
        n[0] += 1
        fid = f"F{n[0]}"
        facts[fid] = {"value": value, "unit": unit, "label": label, "source": source, "calc": calc}
        return "{{" + fid + "}}"

    pct = lambda x: round(float(x) * 100, 1)
    sit = [c for c in ceps if c["kind"] == "상황"]
    ranked = sorted([c for c in sit if c["priority_rank"] != ""], key=lambda c: c["priority_rank"])
    gated = [c for c in sit if c["gated_out"] == "Y"]
    unc = next((c for c in ceps if c["kind"] == "미분류"), None)

    f_sit = F(m["situation_volume_lo"], "건", "[추정]", src_vol, "상황 CEP 그룹 하한 합(12개월 · 카테고리 결속 검색어)")
    f_nb = F(m["situation_nonbrand_lo"], "건", "[추정]", src_vol, "그중 브랜드가 없는 검색어(하한)")
    f_unc = F(pct(m["unclassified_share_lo"] or 0), "%", "[추정]", src_vol, "미분류 하한 ÷ 카테고리 결속 검색량 하한")
    f_gate = F(m["gates"]["cep_12m"], "건", "[가정]", "cep_map.py --gate", "CEP 12개월 하한 순위 게이트")
    f_trunc = F(len(m["truncated_calls"]), "건", "[실제 데이터]", "pool_calls.csv", "반환 수 = limit 인 호출")
    f_susp = F(m["last_month_suspect"], "건", "[추정]", src_all, "마지막 달 MoM이 전년 같은 구간보다 크게 나쁨")
    f_cost = F(sum(c["cost"] for c in calls), "크레딧", "[실제 데이터]", "ledger.jsonl", "cost_detail.total_cost 합")
    shifts = m.get("rank_shifts_incl_last") or []
    f_shift = F(len(shifts), "개", "[추정]", src_all,
                f"순위 창({m.get('rank_window')}) 순위와 마지막 달 포함 12개월 순위가 다른 상황 CEP 수"
                + (f" — {' · '.join(shifts[:5])}" if shifts else ""))
    f_part = F(pct(m.get("partial_token_share_lo") or 0), "%", "[추정]", src_vol,
               "상황 CEP 하한 중 주 사전 일치가 더 긴 어절의 앞부분(어절 일부)인 비중 — 사전 오탐 위험 신호")

    per = {}
    for c in ranked[:TOP]:
        k = c["cep"]
        per[k] = {"nb": F(c["window_nonbrand_lo"], "건", "[추정]", src_vol,
                          f"«{k}» 논브랜드 그룹 하한 합 · 순위 창 {c['rank_window']}"),
                  "all": F(c["volume_12m_lo"], "건", "[추정]", src_vol, f"«{k}» 그룹 하한 합(volume_total · 마지막 달 포함 12개월)")}
        if c["yoy_12m"] != "":
            per[k]["yoy"] = F(pct(c["yoy_12m"]), "%", "[실제 데이터]", src_all,
                              f"«{k}» 시계열 모집단 합 {m['windows']['cur12']} ÷ {m['windows']['prev12']} − 1")
        if c["volume_12m_lo"]:
            per[k]["bshare"] = F(pct(c["brand_volume_lo"] / c["volume_12m_lo"]), "%", "[추정]", src_vol,
                                 f"«{k}» 브랜드 검색어 하한 ÷ CEP 하한")

    top = ranked[0] if ranked else None
    comp = next((c for c in ranked if c["brand_link_call"] == "경쟁 브랜드 우위"), None)

    def note(cid, key, default):
        return (notes.get(cid) or {}).get(key) or default

    # ── 요약 — 발견 카드
    summary = []
    if top:
        p = per[top["cep"]]
        summary.append({"type": "finding", "id": "D1", "evidence": [x.strip("{}") for x in p.values()],
                        "observed": f"«{top['cep']}» 논브랜드 검색량 {p['nb']}(하한) · 전체 {p['all']}"
                                    + (f" · 전년 12개월 대비 {p['yoy']}" if "yoy" in p else " · 추세 판정 보류"),
                        "interpretation": note("D1", "interpretation",
                                               f"브랜드 없이 검색되는 진입 상황 중 가장 크다 — 이 상황을 먼저 잡는 메시지 후보 [추정]"),
                        "alternatives": note("D1", "alternatives",
                                             "사전 순서 때문에 다른 상황의 검색어가 이 CEP로 모였을 수 있다 — multi_cep=Y 검색어 확인"),
                        "limits": "검색은 구매·브랜드 연상이 아니다 · 순위는 논브랜드 하한 기준 · 분류는 규칙 사전 [추정]",
                        "actions": ["A1"]})
    if comp and comp is not top:
        p = per.get(comp["cep"], {})
        ev = [x.strip("{}") for x in p.values()]
        summary.append({"type": "finding", "id": "D2", "evidence": ev or [f_sit.strip("{}")],
                        "observed": f"«{comp['cep']}»은 브랜드 검색 비중 {p.get('bshare', f_sit)} — 선두 «{comp['top_competitor']}»",
                        "interpretation": note("D2", "interpretation", "경쟁 브랜드가 이 상황과 함께 검색된다 — 획득 과제 [추정]"),
                        "alternatives": note("D2", "alternatives", "브랜드 사전 누락이면 비중이 낮게 나온다 — 상위 브랜드 검색어 확인"),
                        "limits": "함께 검색됨은 연상·선호가 아니다 · 경로 동반은 세션 인접 집계",
                        "actions": ["A2"] if len(ranked) > 1 else []})
    summary.append({"type": "paragraph", "html":
                    f"<b>반대 근거</b> — 미분류가 결속 검색량의 {f_unc}이고, 절단 호출 {f_trunc} · 마지막 달 미완결 의심 {f_susp}이 있다. "
                    f"마지막 달을 넣어 매기면 순위가 바뀌는 CEP가 {f_shift}이고, 상황 CEP 검색량의 {f_part}는 사전 일치가 "
                    "더 긴 낱말의 앞부분이다(오탐 위험). 사전 빈칸 · 반환분 기준 수집 · 창에서 뺀 마지막 달이 순위를 바꿀 수 있다."})
    summary.append({"type": "paragraph", "html":
                    f"<b>가능한 결정</b> — 상황 CEP 검색량 {f_sit} 중 논브랜드 {f_nb}를 기준으로 캠페인 상황 후보를 고르고, "
                    "성수기 달에 맞춰 메시지를 배치한다. 순위 밖(게이트 미만) CEP는 표에만 둔다."})

    # ── 추세
    def sums(c):
        s = {r["month"]: r["v"] for r in c.get("series") or []}
        w = m["windows"]
        if not w["cur12"]:
            return None, None
        from cep_lib import window
        cur = window(w["cur12"].split("~")[1], 12)
        prev = window(w["prev12"].split("~")[1], 12)
        if any(s.get(x) is None for x in cur + prev):
            return None, None
        return sum(s[x] for x in prev), sum(s[x] for x in cur)

    trows = []
    for c in sit:
        b, cu = sums(c)
        row = {"q": c["cep"], "b": b, "c": cu, "d": (cu - b) if b is not None else None,
               "p": pct(c["yoy_12m"]) if c["yoy_12m"] != "" else None, "s": c["trend_call"]}
        if c["gated_out"] == "Y":
            row["note"] = "순위 게이트 미만 — 순위 문장의 주어로 쓰지 않음"
        elif b is None:
            row["note"] = "시계열 없음 또는 결측 월 — 비교하지 않음"
        elif row["p"] is None:
            row["note"] = "전년 창 합이 추세 게이트 미만 — 증감률 내지 않음"
        trows.append(row)
    season = [c for c in sit if c.get("season_index")]
    heat = None
    if season:
        rows_raw = []
        for c in season:
            by = {}
            for r in c["series"]:
                if r["v"] is not None:
                    by.setdefault(int(r["month"][5:7]), []).append(r["v"])
            rows_raw.append([round(sum(by[k]) / len(by[k])) for k in range(1, 13)])
        heat = {"type": "chart", "chart": "heatmap",
                "meta": {"title": "CEP별 달력 월 계절 지수", "takeaway": "성수기 달이 CEP마다 다르다 — 같은 달에 비교하면 역위상 CEP가 작게 보인다",
                         "period": f"{season[0]['series'][0]['month']}~{m['window_end']}", "unit": "지수(월평균=1) · 월평균 건",
                         "method": "달력 월 평균 ÷ 전체 월평균 · 결측 월이 있는 검색어는 모집단에서 뺌",
                         "source": f"keyword_info all ({src_all})", "limits": "2단 조회 대상만 · 검색어 표본 합 — 시장 전체가 아님"},
                "data": {"rows": [c["cep"] for c in season], "cols": [f"{k}월" for k in range(1, 13)],
                         "values": [[c["season_index"][str(k)] if str(k) in c["season_index"] else c["season_index"].get(k)
                                     for k in range(1, 13)] for c in season], "raw": rows_raw}}
    trend = [{"type": "table", "caption": "CEP별 전년 동기 12개월 비교",
              "columns": [{"key": "q", "label": "CEP"}, {"key": "b", "label": "전년 12개월", "role": "base"},
                          {"key": "c", "label": "최근 12개월", "role": "compare"},
                          {"key": "d", "label": "절대 증감", "role": "abs_change"},
                          {"key": "p", "label": "증감률(%)", "role": "pct_change"}, {"key": "s", "label": "판정"}],
              "rows": trows,
              "meta": {"title": "CEP별 전년 동기 12개월 비교", "takeaway": "증감률과 절대 증감을 함께 본다",
                       "period": f"{m['windows']['prev12']} vs {m['windows']['cur12']}", "unit": "건 · %",
                       "method": "CEP 시계열 모집단(비교 창에 결측 없는 대표 검색어) 월 합 · 마지막 달 제외",
                       "source": f"keyword_info all ({src_all})", "limits": "2단 조회 대상만 합산 · volume_trend 미사용"}}]
    if heat:
        trend.append(heat)

    # ── 구조
    srows = [{"r": c["priority_rank"] or "—", "q": c["cep"], "nb": c["window_nonbrand_lo"], "nbh": c["window_nonbrand_hi"],
              "ri": c["priority_rank_incl_last"] or "—",
              "bv": c["brand_volume_lo"], "so": c["situation_only_volume_lo"], "pp": c["path_pre_paths"],
              "pk": c["peak_months"].replace("|", "·") or "—", "bl": c["brand_link_call"]} for c in sit]
    pts = [{"label": c["cep"], "x": pct(c["yoy_12m"]), "y": pct(c["brand_volume_lo"] / c["volume_12m_lo"]),
            "size": c["window_nonbrand_lo"]} for c in ranked if c["yoy_12m"] != "" and c["volume_12m_lo"]]
    structure = [{"type": "table", "caption": "CEP 지도 — 논브랜드 검색량 순",
                  "columns": [{"key": "r", "label": "순위"}, {"key": "q", "label": "CEP"},
                              {"key": "nb", "label": "논브랜드 하한(순위 창)", "num": True}, {"key": "nbh", "label": "논브랜드 상한(순위 창)", "num": True},
                              {"key": "ri", "label": "마지막 달 포함 순위"},
                              {"key": "bv", "label": "브랜드 검색", "num": True}, {"key": "so", "label": "상황만(카테고리어 없음)", "num": True},
                              {"key": "pp", "label": "시드 앞 경로", "num": True}, {"key": "pk", "label": "성수기 월"},
                              {"key": "bl", "label": "브랜드 연결"}],
                  "rows": srows,
                  "meta": {"title": "CEP 지도", "takeaway": "순위는 논브랜드 하한 하나로 매기고 추세 · 계절 · 브랜드는 옆 열로 본다",
                           "period": m.get("rank_window") or m["windows"]["cur12"] or "—", "unit": "건(순위 창 12개월 합)",
                           "method": "상황 사전 규칙 분류 · 표기변형 그룹 하한(최댓값)·상한(합) · 같은 검색 중복 제외",
                           "source": f"intent_finder · path_finder · keyword_info ({src_vol})",
                           "limits": "사전에 없는 상황은 미분류 · 상황만 열은 다른 모집단이라 합산하지 않음 · 시계열 없는 그룹은 마지막 달 포함 12개월 합으로 대신함"}}]
    if pts:
        structure.append({"type": "chart", "chart": "scatter",
                          "meta": {"title": "추세 × 브랜드 검색 비중", "takeaway": "브랜드 비중이 낮고 오르는 CEP가 선점 후보다",
                                   "period": f"{m['windows']['prev12']} vs {m['windows']['cur12']}", "unit": "% · 건",
                                   "method": "x = 전년 12개월 대비 증감률, y = 브랜드 검색어 하한 ÷ CEP 하한, 면적 = 논브랜드 하한",
                                   "source": f"keyword_info ({src_vol} · {src_all})", "limits": "순위 CEP만 · 브랜드 사전 누락 시 y가 낮게 나온다"},
                          "data": {"x_label": "전년 12개월 대비(%)", "y_label": "브랜드 검색 비중(%)",
                                   "size_label": "논브랜드 검색량(건)", "points": pts}})
    br = [b for b in doc["brands"] if b["cooccur_volume_lo"] or b["path_cooccur_paths"]]
    if br:
        structure.append({"type": "table", "caption": "브랜드 × CEP 연결",
                          "columns": [{"key": "cep", "label": "CEP"}, {"key": "brand", "label": "브랜드"}, {"key": "role", "label": "역할"},
                                      {"key": "cooccur_volume_lo", "label": "함께 검색된 검색량", "num": True},
                                      {"key": "path_cooccur_paths", "label": "경로 동반", "num": True},
                                      {"key": "cep_paths", "label": "CEP 경로", "num": True}],
                          "rows": br,
                          "meta": {"title": "브랜드 × CEP 연결", "takeaway": "기준 · 경쟁 브랜드가 그 상황 검색에 함께 나오는 정도",
                                   "period": m["windows"]["cur12"] or "—", "unit": "건 · 경로 수",
                                   "method": "CEP 검색어에 브랜드명이 든 그룹의 하한 합 · 같은 경로에 브랜드 검색어가 있는 경로 수",
                                   "source": f"keyword_info ({src_vol})" + (f" · path_finder ({src_path})" if src_path else ""),
                                   "limits": "함께 검색됨은 연상 · 선호가 아니다 · 경로는 세션 인접"}})

    # ── 실행
    actions = []
    for i, c in enumerate([x for x in [top, comp] if x][:2], 1):
        p = per.get(c["cep"], {})
        actions.append({"type": "action", "id": f"A{i}", "evidence": [x.strip("{}") for x in p.values()] or [f_sit.strip("{}")],
                        "hypothesis": note(f"A{i}", "hypothesis",
                                           f"«{c['cep']}» 상황을 전면에 둔 메시지가 이 상황의 논브랜드 검색 유입을 받는다 [추정]"),
                        "deliverable": note(f"A{i}", "deliverable", "상황 메시지 초안 · 랜딩 첫 화면 문구 · 검색 광고 문안 묶음"),
                        "owner_role": note(f"A{i}", "owner_role", "브랜드 마케터"),
                        "internal_data": note(f"A{i}", "internal_data", "캠페인별 유입 · 전환 · 상황 문구별 클릭"),
                        "criteria": {"expand": note(f"A{i}", "expand", "성수기 달 유입이 다른 상황 메시지보다 높음"),
                                     "revise": note(f"A{i}", "revise", "유입은 있으나 전환이 낮음 — 상황 문구를 바꿔 재시험"),
                                     "stop": note(f"A{i}", "stop", "성수기 두 달 동안 대조 메시지와 차이 없음")}})
    if not actions:
        actions.append({"type": "paragraph", "html": "게이트를 넘는 상황 CEP가 없다 — 사전 보강 또는 시드 추가 후 다시 본다."})

    # ── 부록
    unc_rows = [{"q": r["keyword"], "v": r["volume_lo"]} for r in doc.get("unclassified_top") or []]
    appendix = [{"type": "paragraph", "html":
                 f"계산 정의 — 순위 = 게이트 {f_gate} 이상인 상황 CEP의 논브랜드 하한 순(가중 합성 점수 없음) · 볼륨은 추세와 같은 창(마지막 달 제외)의 월 합. "
                 "추세 = CEP 시계열 모집단 월 합의 전년 동기 비교(마지막 달 제외). 계절 지수 = 달력 월 평균 ÷ 전체 월평균. "
                 f"이번 산출물 소진 {f_cost}."},
                {"type": "paragraph", "html":
                 "한계 — 세션 인접 경로는 개인 여정이 아니다 · 검색은 구매가 아니다 · 사전에 없는 상황은 미분류로 남는다 · "
                 "CEP 이름 해석과 메시지 문장은 규칙 · 에이전트가 쓴 [추정]이다."}]
    if unc_rows:
        appendix.append({"type": "table", "caption": "미분류 상위 검색어 — 사전 보강 후보",
                         "columns": [{"key": "q", "label": "검색어"}, {"key": "v", "label": "12개월 하한", "num": True}],
                         "rows": unc_rows,
                         "meta": {"title": "미분류 상위 검색어", "takeaway": "상황이면 사전에 넣고 다시 집계한다(0크레딧)",
                                  "period": m["windows"]["cur12"] or "—", "unit": "건", "method": "미분류 그룹 하한 상위",
                                  "source": f"keyword_info ({src_vol})", "limits": "구매 탐색어는 미분류가 아니라 비상황으로 둔다"}})

    rep = {
        "meta": {"title": f"{m['keyword']} 진입 상황(CEP) 지도", "subtitle": f"{m['gl']} · {m.get('domain') or '분야 미지정'}",
                 "skill": "map-entry-situations", "version": a.version},
        "boundary": f"{m['data_ref_month']}까지의 검색에서 본 진입 상황 지도이며, 다음 성수기 · 구매 · 브랜드 연상(인식)은 관측하지 않았다",
        "lede_html": (f"상황 CEP 검색량 {f_sit} 중 논브랜드 {f_nb}. "
                      + (f"1순위는 «{top['cep']}»이다. " if top else "게이트를 넘는 상황 CEP가 없다. ")
                      + f"미분류 비중 {f_unc} — 사전 빈칸만큼 지도가 비어 있다."),
        "scope": {"collected_at": m["queried_at"], "latest_month": m["data_ref_month"], "gl": m["gl"],
                  "selection": "키워드 · 상황 탐침어의 연관 검색어(intent_finder) + 시드 앞 세션 인접 검색(path_finder) → 상황 사전 규칙 분류",
                  "missing": f"미반환 {len(m['not_returned'])}개 · 비교 창 결측 월 검색어는 시계열 모집단에서 뺌 · 지연 {m['lag_months']}개월",
                  "excluded": f"뜻 고정 제외(경계 밖) {len(m['excluded_boundary'])}개 · 같은 검색 중복 {m['dup_rows']}행 · 게이트 미만 CEP {len(gated)}개",
                  "windows": {"기준": m["windows"]["prev12"], "비교": m["windows"]["cur12"], "마지막 달": m["last_month"]}},
        "facts": facts,
        "sections": [{"kind": "summary", "title": (f"«{top['cep']}»이 브랜드 없이 가장 많이 검색되는 진입 상황이다" if top
                                                   else "게이트를 넘는 진입 상황이 없다"), "blocks": summary},
                     {"kind": "trend", "title": "CEP마다 성수기가 달라 같은 창의 전년 동기로만 비교한다", "blocks": trend},
                     {"kind": "structure", "title": "논브랜드 크기 · 브랜드 연결로 본 CEP 지도", "blocks": structure},
                     {"kind": "action", "title": "상위 상황부터 메시지를 시험한다", "blocks": actions},
                     {"kind": "appendix", "title": "계산 정의 · 미분류 · 한계", "blocks": appendix}],
        "provenance": {"calls": calls, "currency": "크레딧", **({"estimate": a.estimate} if a.estimate else {})},
    }
    json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"사실 {len(facts)}개 · 호출 {len(calls)}건 · 섹션 {len(rep['sections'])}개 → {a.out}")


if __name__ == "__main__":
    main()
