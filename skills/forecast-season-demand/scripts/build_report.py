#!/usr/bin/env python3
"""STEP 10 · 저장된 계산 결과(forecast_result.json) + 수집 원장(ledger.json) → report.json.

  python3 scripts/build_report.py out/forecast_result.json --ledger ledger.json --category 선크림 -o report.json
  python3 scripts/render_report.py report.json -o report.html

- 문구의 숫자는 전부 `facts`에서 `{{ID}}`로 부른다. 사실은 이 스크립트가 결과 파일에서만 만든다 — 손으로 넣지 않는다.
- `provenance.calls` 는 원장(ledger.json)의 호출 줄을 그대로 옮긴다(호출 순서 · 조건 · 반환 수 · 비용 · 원본 파일).
- 해석 문장(interpretation · hypothesis 등)은 규칙으로 고른 틀 문장이고 `[추정]`을 붙인다. 에이전트가 고쳐 쓸 때도
  숫자는 `{{ID}}`로만 쓰고 `[추정]` 표시를 지우지 않는다.
- 순위 문장(«가장 크게 오른다»)의 주어는 게이트 통과 세그먼트만이다(계약 T4). 증감률 1위와 절대 증감 1위가 다르면 둘 다 쓴다(렌더링 계약 §3-8).
"""
import argparse, json, pathlib, sys

LBL_REAL, LBL_EST, LBL_ASM, LBL_GAP = "[실제 데이터]", "[추정]", "[가정]", "[데이터 공백]"


def load(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--category", required=True, help="리포트 제목에 쓸 카테고리 이름 (예: 선크림)")
    ap.add_argument("--estimate", type=int, help="호출 전에 제시한 예산(결정식 상한)")
    ap.add_argument("--subtitle-note", default="", help="부제 앞에 붙일 표시 — 견본은 «합성 픽스처 예시(assets/fixtures) — 실제 시장 값이 아니다»")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    R, led = load(a.result), load(a.ledger)
    M, segs, cat = R["meta"], R["segments"], R["category"]
    steps = led.get("steps") or {}
    col, scr = steps.get("collect") or {}, steps.get("screen") or {}
    calls = led.get("calls") or []
    c_all = next((c["id"] for c in calls if c.get("data_through")), calls[-1]["id"] if calls else "C1")
    c_if = [c["id"] for c in calls if c.get("tool") == "intent_finder"]
    H, ref, hm = M["horizon"], M["data_ref_month"], M["horizon_months"]
    span = f"{hm[0]}~{hm[-1]}"

    facts, n = {}, [0]

    def F(value, unit, label, calc, source=None):
        n[0] += 1
        fid = f"F{n[0]}"
        facts[fid] = {"value": value, "unit": unit, "label": label, "source": source or c_all, "calc": calc}
        return fid

    def pct(x):
        return None if x is None else round(x * 100, 1)

    # ── 카테고리 사실
    f_next = F(cat["next_total"], "건", LBL_EST, f"세그먼트 예측 합 · {span}")
    f_ly = F(cat["next_total_last_year"], "건", LBL_REAL, f"전년 같은 달 합 · {hm[0][:4]}년 기준 12개월 전")
    f_yoy = F(pct(cat["next_yoy"]), "%", LBL_EST, "예측 합 ÷ 전년 같은 달 합 − 1")
    f_lo = F(cat["next_lo"], "건", LBL_EST, "세그먼트별 r 후보(마지막 달 제외·포함 · 12개월) 최소 적용 합")
    f_hi = F(cat["next_hi"], "건", LBL_EST, "세그먼트별 r 후보 최대 적용 합")
    f_bt = F(pct(cat["backtest_err"]), "%", LBL_EST, f"기준을 {H}개월 앞당겨 같은 규칙으로 맞춘 합계 오차") \
        if cat.get("backtest_err") is not None else None
    f_gate = F(M["gate"], "건", LBL_ASM, f"세그먼트 비율의 전년 {M['window']}개월 합 하한(T4)", "SKILL.md STEP 7")
    f_share = F(round(scr["screened_share"] * 100, 1), "%", LBL_EST, "all 조회분 volume_total ÷ 측정된 풀 volume_total",
                next((c["id"] for c in calls if "ads_metrics" in c.get("params", "")), c_all)) \
        if scr.get("screened_share") is not None else None
    cal_cat = cat["calendar"]

    # ── 세그먼트 사실과 순위
    seg_f = {}
    for s in segs:
        seg_f[s["segment"]] = {"next": F(s["next_total"], "건", LBL_EST, f"{s['segment']} 예측 합 · {span}"),
                               "ly": F(s["next_total_last_year"], "건", LBL_REAL, f"{s['segment']} 전년 같은 달 합"),
                               "yoy": F(pct(s["next_yoy"]), "%", LBL_EST, f"{s['segment']} 예측 ÷ 전년 − 1"),
                               "abs": F(s["next_abs_change"], "건", LBL_EST, f"{s['segment']} 예측 − 전년")}
    elig = [s for s in segs if s["gated"] == "아니오"]
    top_pct = next((s for s in segs if s.get("rank_pct") == 1), None)
    top_abs = next((s for s in segs if s.get("rank_abs") == 1), None)
    gated = [s["segment"] for s in segs if s["gated"] == "예"]
    prov = [s["segment"] for s in segs if s["provisional"]]
    anti = [s for s in segs if s["calendar"].get("anti_phase") == "예"]
    weak = [s["segment"] for s in segs if s["quality"] != "양호"]

    dir_word = "늘어날" if (cat["next_yoy"] or 0) > 0 else ("줄어들" if (cat["next_yoy"] or 0) < 0 else "같을")
    peak_txt = f"{cal_cat['peak_month']}월" if cal_cat.get("peak_month") else "판정 불가"

    findings, actions = [], []
    findings.append({"type": "finding", "id": "D1", "evidence": [f_next, f_ly, f_yoy] + ([f_bt] if f_bt else []),
                     "observed": f"다음 {H}개월({span}) 예측 {{{{{f_next}}}}} · 전년 같은 달 합 {{{{{f_ly}}}}}",
                     "interpretation": f"[추정] 최근 {M['window']}개월의 전년 대비 흐름이 이어지면 {a.category} 검색 수요는 전년보다 {{{{{f_yoy}}}}} {dir_word} 것으로 계산된다. 카테고리 정점은 {peak_txt}({cal_cat['season_type']}).",
                     "alternatives": f"최근 흐름이 바뀌면 범위 {{{{{f_lo}}}}}~{{{{{f_hi}}}}} 안에서 달라진다"
                                     + (f" · 같은 규칙의 과거 점검 오차 {{{{{f_bt}}}}}" if f_bt else ""),
                     "limits": "검색량은 관심 신호이지 판매량이 아니다 · 예측 기간 실측은 아직 없다 · 수집된 검색어 표본 기준",
                     "actions": ["A1"]})
    if top_pct:
        if top_abs and top_abs["segment"] != top_pct["segment"]:
            obs = (f"증감률 1위 {top_pct['segment']} {{{{{seg_f[top_pct['segment']]['yoy']}}}}} · "
                   f"절대 증감 1위 {top_abs['segment']} {{{{{seg_f[top_abs['segment']]['abs']}}}}}")
            ev = [seg_f[top_pct['segment']]['yoy'], seg_f[top_abs['segment']]['abs']]
        else:
            obs = (f"{top_pct['segment']} — 증감률 {{{{{seg_f[top_pct['segment']]['yoy']}}}}} · "
                   f"절대 증감 {{{{{seg_f[top_pct['segment']]['abs']}}}}} 모두 1위")
            ev = [seg_f[top_pct['segment']]['yoy'], seg_f[top_pct['segment']]['abs']]
        findings.append({"type": "finding", "id": "D2", "evidence": ev + [f_gate], "observed": obs,
                         "interpretation": f"[추정] 다음 {H}개월 증가분은 {top_abs['segment'] if top_abs else top_pct['segment']} 쪽에서 가장 크게 나온다는 계산이다",
                         "alternatives": "세그먼트 비율은 최근 몇 개월의 전년비 하나에 기대므로 일시적 이슈가 섞였을 수 있다 — 세그먼트 표의 범위를 함께 본다",
                         "limits": f"순위는 전년 기저가 게이트 {{{{{f_gate}}}}} 이상인 세그먼트만 비교했다"
                                   + (f" (제외: {', '.join(gated)})" if gated else ""),
                         "actions": ["A2"]})
    if anti:
        s = anti[0]
        findings.append({"type": "finding", "id": "D3", "evidence": [seg_f[s['segment']]['next']],
                         "observed": f"{s['segment']} 정점 {s['calendar']['peak_month']}월 — 카테고리 정점 {peak_txt}과 다르다 (예측 {{{{{seg_f[s['segment']]['next']}}}}})",
                         "interpretation": "[추정] 카테고리 합계 일정으로 움직이면 이 세그먼트의 시즌을 놓친다 — 별도 일정이 필요하다",
                         "alternatives": "세그먼트 사전(themes.json)의 패턴이 다른 수요를 섞었을 수 있다 — 세그먼트 검색어 목록을 확인한다",
                         "limits": "계절 지수는 최근 3개 12개월 블록의 평균이다",
                         "actions": ["A3"]})

    against = []
    if prov:
        against.append(f"마지막 달을 넣으면 비율 방향이 뒤집히는 «잠정» 세그먼트: {', '.join(prov)}")
    if gated:
        against.append(f"전년 기저가 작아 비율을 적용하지 않은(r=1) 세그먼트: {', '.join(gated)}")
    if weak:
        against.append(f"과거 점검 오차가 커 «참고»로 볼 세그먼트: {', '.join(weak)}")
    if col.get("truncated"):
        against.append(f"연관 검색어가 limit 에 잘린 시드: {', '.join(col['truncated'])} — 풀은 상위 표본이다")
    if not against:
        against.append("잠정 · 게이트 미달 · 점검 «참고» 세그먼트가 없다 — 그래도 예측은 최근 흐름이 이어진다는 가정 위에 있다")

    # ── 표 · 차트
    seg_rows = []
    for s in segs + [cat]:
        note = []
        if s["gated"] == "예":
            note.append("작은 기저 — 비율 미적용")
        if s["provisional"]:
            note.append("잠정")
        if s["calendar"].get("anti_phase") == "예":
            note.append("역위상")
        seg_rows.append({"seg": s["segment"], "b": s["next_total_last_year"], "c": s["next_total"],
                         "d": s["next_abs_change"], "p": pct(s["next_yoy"]),
                         "rng": f"{s['next_lo']:,}~{s['next_hi']:,}", "up": s["next_total_upper"],
                         "q": s["quality"], "note": " · ".join(note) or None})
    hist = cat["history"]
    hx = list(hist.keys())
    x = hx + hm
    line = {"type": "chart", "chart": "line",
            "meta": {"title": f"{a.category} 월별 검색량 — 실측과 예측", "takeaway": f"예측은 전년 같은 달에 최근 전년비를 곱한 값이다 [추정]",
                     "period": f"{hx[0]}~{hm[-1]}", "unit": "건(월)", "method": "실측 = 세그먼트 하한 합 · 예측 = 세그먼트 예측 합",
                     "source": f"keyword_info all ({c_all})", "limits": "예측 구간은 미관측 · 수집 검색어 표본 기준"},
            "data": {"x": x, "series": [{"name": "실측", "values": [hist[m] for m in hx] + [None] * H},
                                        {"name": "예측 [추정]", "values": [None] * len(hx) + [f["forecast"] for f in cat["forecast"]]},
                                        {"name": "전년 같은 달", "values": [None] * len(hx) + [f["last_year"] for f in cat["forecast"]]}]}}
    table_seg = {"type": "table", "caption": f"세그먼트별 다음 {H}개월 예측",
                 "columns": [{"key": "seg", "label": "세그먼트"}, {"key": "b", "label": "전년 같은 달 합", "role": "base"},
                             {"key": "c", "label": "예측 합", "role": "compare"}, {"key": "d", "label": "절대 증감", "role": "abs_change"},
                             {"key": "p", "label": "증감률(%)", "role": "pct_change"}, {"key": "rng", "label": "범위"},
                             {"key": "up", "label": "표기변형 상한"}, {"key": "q", "label": "점검"}],
                 "rows": seg_rows,
                 "meta": {"takeaway": "증감률과 절대 증감을 함께 본다", "period": f"{span} vs 12개월 전",
                          "unit": "건(기간 합) · %", "method": "예측 = 전년 같은 달 × r · 범위 = r 후보 최소~최대",
                          "source": f"keyword_info all ({c_all})", "limits": "작은 기저 세그먼트는 비율 미적용 · 순위 문장에서 뺀다"}}
    prof = cat.get("profile")
    heat = None
    if prof:
        heat = {"type": "chart", "chart": "heatmap",
                "meta": {"title": f"{a.category} 계절 지수 — 12개월 블록별", "takeaway": f"정점 {peak_txt} · 블록 정점 일치 {cal_cat['peak_consistency']}",
                         "period": M["profile_window"], "unit": "지수(블록 월평균 = 1) · 건",
                         "method": "그 달 값 ÷ 블록 월평균", "source": f"keyword_info all ({c_all})",
                         "limits": "블록은 달력 해가 아니라 비율 창 끝에서 거꾸로 자른 12개월"},
                "data": {"rows": [b["label"] for b in prof["blocks"]], "cols": [f"{c}월" for c in range(1, 13)],
                         "values": [[b["index"][str(c)] for c in range(1, 13)] for b in prof["blocks"]],
                         "raw": [[dict(zip([int(mm[5:]) for mm in _months(b["label"])], b["values"]))[c] for c in range(1, 13)]
                                 for b in prof["blocks"]]}}
    cal_rows = [{"seg": s["segment"], **{k: s["calendar"].get(k) for k in
                ("season_type", "peak_month", "peak_consistency", "prep", "launch", "campaign_from", "peak", "end", "status", "anti_phase")}}
                for s in segs + [cat]]
    table_cal = {"type": "table", "caption": "시즌 일정 — 준비 · 출시 · 캠페인 · 정점",
                 "columns": [{"key": "seg", "label": "세그먼트"}, {"key": "season_type", "label": "유형"},
                             {"key": "peak_month", "label": "정점 월"}, {"key": "peak_consistency", "label": "정점 반복"},
                             {"key": "prep", "label": "준비 시작"}, {"key": "launch", "label": "출시"},
                             {"key": "campaign_from", "label": "캠페인 시작"}, {"key": "peak", "label": "정점"},
                             {"key": "end", "label": "시즌 끝"}, {"key": "status", "label": "조회일 기준"},
                             {"key": "anti_phase", "label": "역위상"}],
                 "rows": cal_rows,
                 "meta": {"takeaway": "세그먼트마다 정점이 다르면 일정을 따로 잡는다", "period": M["profile_window"],
                          "unit": "월", "method": "상승 시작 = 정점에서 거꾸로 지수가 1 이상인 첫 달 · 준비 = 상승 두 달 전 · 출시 = 한 달 전",
                          "source": f"keyword_info all ({c_all})", "limits": "규칙 기반 [가정] — 업종 리드타임에 맞춰 조정"}}
    bar = {"type": "chart", "chart": "bar",
           "meta": {"title": "세그먼트별 예측 전년비", "takeaway": "증감률 막대 옆에 절대 증감을 함께 적었다",
                    "period": f"{span} vs 12개월 전", "unit": "% · 건", "method": "예측 합 ÷ 전년 같은 달 합 − 1",
                    "source": f"keyword_info all ({c_all})", "limits": "작은 기저 세그먼트는 비율 미적용이라 막대가 0에 가깝다"},
           "data": {"labels": [s["segment"] for s in segs], "values": [pct(s["next_yoy"]) for s in segs],
                    "abs": [s["next_abs_change"] for s in segs], "value_unit": "%"}}

    # ── 실행 카드 (틀 문장 · [추정])
    def act(aid, ev, hyp, deliv, crit):
        return {"type": "action", "id": aid, "evidence": ev, "hypothesis": hyp, "deliverable": deliv,
                "owner_role": "상품기획 · MD · 캠페인 담당", "internal_data": "월 판매량 · 재고 회전 · 캠페인 유입 (이름만 — 값은 내부 시스템)",
                "criteria": crit}
    actions.append(act("A1", [f_next, f_yoy],
                       f"[추정] 카테고리 준비를 {cal_cat.get('prep') or '—'}에 시작하면 {cal_cat.get('peak') or '정점'} 수요 상승에 맞출 수 있다",
                       "시즌 일정표(준비 · 출시 · 캠페인 · 정점) 초안",
                       {"expand": "예측 기간 첫 두 달 실측이 범위 상단 이상", "revise": "실측이 범위 안이지만 정점 달이 앞뒤로 움직임",
                        "stop": "실측이 범위 하단 미만으로 두 달 이어짐"}))
    if top_pct:
        tgt = top_abs or top_pct
        actions.append(act("A2", [seg_f[tgt['segment']]['abs']],
                           f"[추정] {tgt['segment']} 세그먼트에 물량 · 콘텐츠를 먼저 배정하면 증가분을 받는다",
                           f"{tgt['segment']} 상위 검색어 기준 상품 · 콘텐츠 목록",
                           {"expand": "세그먼트 실측 전년비가 예측 방향과 같음", "revise": "방향은 같지만 크기가 범위 밖",
                            "stop": "실측 방향이 예측과 반대"}))
    if anti:
        s = anti[0]
        actions.append(act("A3", [seg_f[s['segment']]['next']],
                           f"[추정] {s['segment']}는 카테고리와 따로 {s['calendar'].get('prep') or '—'} 준비 · {s['calendar'].get('peak') or '—'} 정점 일정으로 운영한다",
                           f"{s['segment']} 전용 시즌 일정 · 기획전 초안",
                           {"expand": "정점 달 실측이 전년 같은 달 이상", "revise": "정점 달이 한 달 이상 어긋남",
                            "stop": "정점 달 실측이 전년의 절반 미만"}))

    status_rows = [{"k": k, "v": v} for k, v in (M.get("keyword_counts") or {}).items()]
    rc = M.get("refmonth_check")
    rc_txt = "미점검 [데이터 공백]" if not rc else f"검사 {rc.get('checked')}행 중 의심 {rc.get('suspect')}행"

    report = {
        "meta": {"title": f"{a.category} 시즌 수요 예측 — 다음 {H}개월", "subtitle": (f"{a.subtitle_note} · " if a.subtitle_note else "") + f"검색량 기준 · 데이터 기준월 {ref}",
                 "skill": "forecast-season-demand", "version": "0.1.0"},
        "boundary": f"{ref}까지 관측한 월별 검색량과 과거 3년 계절 패턴으로 계산한 {span} 예측이며, 이 기간의 실제 검색량은 아직 관측되지 않았다"
                    + (f" (조회일 기준 그중 {M['horizon_elapsed_at_query']}개월은 이미 지났거나 진행 중)" if M["horizon_elapsed_at_query"] else ""),
        "lede_html": f"{a.category} 검색 수요(수집된 검색어 기준)는 {span}에 <b>{{{{{f_next}}}}}</b>로 전년 같은 달 합 {{{{{f_ly}}}}}보다 "
                     f"{{{{{f_yoy}}}}} {dir_word} 것으로 계산된다 [추정]. 카테고리 정점은 {peak_txt}이며 준비 시작 권장 시점은 {cal_cat.get('prep') or '—'}이다 [가정]."
                     + (f" 정점이 다른 세그먼트({', '.join(s['segment'] for s in anti)})는 일정을 따로 본다." if anti else ""),
        "scope": {"collected_at": M["queried_at"], "latest_month": ref, "gl": M.get("gl") or col.get("gl") or "—",
                  "selection": f"시드 {', '.join(col.get('seeds') or [])} → intent_finder 연관 검색어 → ads_metrics 볼륨 스크린 → 통과분 all 48개월",
                  "missing": f"결측 월 · 이력 부족 검색어는 모든 합계에서 뺐다({len(M.get('excluded_missing') or [])}개) · 미반환 {len(M.get('unreturned') or [])}개 [데이터 공백]",
                  "excluded": f"범위 밖 {col.get('n_excluded', 0)}개(사전 패턴) · 스크린 탈락 {(scr.get('counts') or {}).get('게이트 미달', 0)}개 · 같은 검색 중복은 한 번만",
                  "windows": {"비율 창": segs[0]["ratio_window"], "예측": span,
                              "계절 프로파일": M["profile_window"], "마지막 달": M["last_month_mode"]}},
        "facts": facts,
        "sections": [
            {"kind": "summary", "title": f"{a.category} 수요는 다음 {H}개월 전년보다 {dir_word} 것으로 계산된다 — 정점은 {peak_txt}",
             "blocks": findings + [
                 {"type": "paragraph", "html": "<b>반대 근거</b> — " + " · ".join(against)},
                 {"type": "paragraph", "html": "<b>가능한 결정</b> — 준비 시작 시점 확정 · 세그먼트별 물량 배분" + (" · 역위상 세그먼트 별도 일정" if anti else "") + f". 마지막 달 완결성 점검: {rc_txt}."}]},
            {"kind": "trend", "title": "전년 같은 달에 최근 흐름을 곱한 예측과 그 범위",
             "blocks": [line, table_seg] + ([heat] if heat else [])},
            {"kind": "structure", "title": "세그먼트마다 정점과 일정이 다를 수 있다",
             "blocks": [table_cal, bar] + ([{"type": "paragraph", "html": "⚠️ " + " · ".join(R.get("warnings") or [])}] if R.get("warnings") else [])},
            {"kind": "action", "title": "일정은 세그먼트별로, 규모는 범위로 잡는다", "blocks": actions},
            {"kind": "appendix", "title": "계산 정의와 출처",
             "blocks": [{"type": "paragraph", "html": f"예측 = 전년 같은 달 실측 × r. r = 최근 {M['window']}개월 합 ÷ 전년 같은 {M['window']}개월 합(마지막 달 {M['last_month_mode']}). "
                                                      f"전년 합이 게이트 {{{{{f_gate}}}}} 미만이면 r = 1. 범위는 마지막 달 제외 · 포함 · 12개월 r 중 최소~최대. "
                                                      "계절 지수는 최근 3개 12개월 블록의 평균이고 시점 규칙은 [가정]이다. volume_trend 는 쓰지 않았다."
                                                      + (f" 예측에 들어간 검색량은 측정된 풀의 {{{{{f_share}}}}} [추정]." if f_share else "")},
                        {"type": "table", "caption": "검색어 처리 결과", "columns": [{"key": "k", "label": "상태"}, {"key": "v", "label": "검색어 수"}],
                         "rows": status_rows}]},
        ],
        "provenance": {"calls": [{k: c.get(k) for k in ("id", "step", "at", "tool", "params", "purpose", "returned", "cost",
                                                          "raw", "data_through", "status")} for c in calls],
                       "currency": "크레딧", **({"estimate": a.estimate} if a.estimate else {})},
    }
    pathlib.Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report.json — 사실 {len(facts)}개 · 섹션 {len(report['sections'])}개 · 호출 {len(calls)}건 → {a.out}")
    print(f"다음: python3 scripts/render_report.py {a.out} -o report.html")
    return 0


def _months(label):
    a, b = label.split("~")
    out, m = [], a
    while True:
        out.append(m)
        if m == b:
            return out
        y, mm = int(m[:4]), int(m[5:7]) + 1
        if mm == 13:
            y, mm = y + 1, 1
        m = f"{y:04d}-{mm:02d}"


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
