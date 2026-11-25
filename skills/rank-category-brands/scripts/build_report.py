#!/usr/bin/env python3
"""report.json 조립 — rank_brands.py 의 저장 결과 + 호출 원장 → 공통 렌더러 입력 (rank-category-brands STEP 7, 이 스킬 고유).

  python3 scripts/build_report.py --outdir out --category running-shoes-kr --ledger ledger.jsonl -o report.json

- 사실 목록(facts)은 out/ 의 CSV · 요약 JSON 값에서만 만든다. 문구의 숫자는 {{F…}} 로만 부른다.
- 방향어(늘었다/줄었다)와 순위 기준(증감률 · 절대 증감)은 값에서 고른다(렌더링 계약 §3-8).
- provenance.calls 는 log_call.py 가 쓴 원장(ledger.jsonl)에서 만든다 — 손으로 적지 않는다.
- 기저 게이트 미만 브랜드의 변화율은 헤드라인 · 순위 문장의 주어로 쓰지 않는다(계약 T4) — 표에만 «게이트 미만»으로 둔다.
- 요약 제목은 값에서 고른다 — «논브랜드 검색이 함께 크다»는 논브랜드 몫이 1위 브랜드 몫 이상이고 «별칭 누락 의심»이 없을 때만.
  의심 행이 있으면 논브랜드 몫을 판정 보류로 적고 의심 검색어 표를 부록에 넣는다(v0.1.0 은 고정 문구였다).
"""
VERSION = "0.1.1"   # SKILL.md metadata.version 과 같이 고친다
import argparse, csv, json, pathlib, re, sys

LBL = {"real": "[실제 데이터]", "est": "[추정]", "asm": "[가정]", "gap": "[데이터 공백]"}


def read(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def num(x):
    if x in ("", None):
        return None
    f = float(x)
    return int(f) if f.is_integer() and "." not in str(x) else f


def josa(word, a, b):
    """받침 여부로 조사를 고른다(가/이 · 는/은)."""
    ch = word[-1] if word else ""
    if "가" <= ch <= "힣":
        return word + (b if (ord(ch) - 0xAC00) % 28 else a)
    return word + a


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--category", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--estimate", type=float, help="호출 전 안내한 견적(1 × K1 + 10 × K2)")
    ap.add_argument("--subtitle-note", default="", help="부제 앞에 붙일 표시 — 견본은 «합성 픽스처 예시(assets/fixtures) — 실제 시장 값이 아니다»")
    ap.add_argument("-o", "--out", default="report.json")
    a = ap.parse_args()
    od, cat = pathlib.Path(a.outdir), a.category
    S = json.load(open(od / f"rank_summary_{cat}.json", encoding="utf-8"))
    rank = read(od / f"brand_rank_{cat}.csv")
    trend = read(od / f"brand_trend_{cat}.csv")
    seg = read(od / f"segment_rank_{cat}.csv")
    calls = [json.loads(l) for l in open(a.ledger, encoding="utf-8") if l.strip()]
    if not calls:
        sys.exit("호출 원장이 비었다 — log_call.py 로 원본 응답마다 기록할 것")
    c_all = next((c["id"] for c in reversed(calls) if c.get("data_type") == "all"), calls[-1]["id"])
    c_ads = next((c["id"] for c in calls if c.get("data_type") == "ads_metrics"), c_all)
    genre = S["genre"] or cat
    W = S["windows"]
    brands = [r for r in rank if r["bucket_type"] == "brand" and r["rank_lo"]]
    brands.sort(key=lambda r: int(r["rank_lo"]))
    top = [r for r in brands if r["in_top"] == "1"]
    nb = next(r for r in rank if r["bucket_type"] == "nonbrand")
    if not brands:
        sys.exit("순위에 오른 브랜드가 없다 — 사전 · 2단 응답을 확인할 것")
    facts, n = {}, [0]

    def F(value, unit, label, source, calc):
        n[0] += 1
        fid = f"F{n[0]}"
        facts[fid] = {"value": value, "unit": unit, "label": LBL[label], "source": source, "calc": calc}
        return "{{" + fid + "}}"

    def pctv(x):
        return round(float(x) * 100, 1)

    b1 = brands[0]
    f_v1 = F(num(b1["volume_12m_lo"]), "건", "est", c_all, f"{b1['brand']} 12개월({W['y0']}) 검색량 하한 — 묶음 최댓값 합")
    f_s1 = F(pctv(b1["share_brandset_lo"]), "%", "est", c_all, "브랜드 합 대비 몫(하한 ÷ 사전 브랜드 하한 합)")
    f_g1 = F(pctv(b1["share_genre_lo"]), "%", "est", c_all, "분야 전체 대비 몫(하한 ÷ 브랜드 + 비교 + 논브랜드 하한 합)")
    top3 = brands[:3]
    f_t3 = F(round(sum(float(r["share_brandset_lo"]) for r in top3) * 100, 1), "%", "est", c_all,
             "상위 3개 브랜드의 브랜드 합 대비 몫 합(하한)")
    f_nb = F(pctv(nb["share_genre_lo"]), "%", "est", c_all, "논브랜드 분야 검색 ÷ 분야 전체(하한)")
    f_nbv = F(num(nb["volume_12m_lo"]), "건", "real", c_all, f"논브랜드 분야 검색 12개월({W['y0']}) 합 — 하한")
    sus = S.get("alias_suspect_nonbrand") or []
    f_sus = f_susv = None
    if sus:
        f_sus = F(len(sus), "행", "est", c_all, f"논브랜드로 판정됐지만 분야어 앞부분이 사전 브랜드와 닮은 행(12개월 {S['gates'].get('suspect_min_volume', 600)}건 이상)")
        f_susv = F(S["alias_suspect_nonbrand_volume"], "건", "real", c_all, "별칭 누락 의심 행의 12개월 검색량 합")
    flips = S["rank_flips"]
    f_flip = F(len(flips), "개", "est", c_all, "12개월 순위가 하한과 상한에서 다른 브랜드 수")
    f_susp = F(S["last_month_suspect"], "행", "real", c_all, f"기준월 {S['data_ref_month']} MoM 이 전년보다 30%p 이상 나쁜 행")

    ranked_yoy = sorted([r for r in brands if r["rank_yoy"]], key=lambda r: int(r["rank_yoy"]))
    gated = [r for r in brands if r["yoy_gated_out"] == "1" and r["yoy_base_12m"]]
    names = lambda rs: " · ".join(r["brand"] for r in rs)

    # ── 요약
    alone1 = num(b1["brand_alone_12m"])
    f_al = F(alone1, "건", "real", c_ads, f"{b1['brand']} 브랜드명 단독 12개월 검색량(1단 ads_metrics · 순위 밖)") if alone1 else None
    d1 = {"type": "finding", "id": "D1", "evidence": [f_v1[2:-2], f_s1[2:-2], f_g1[2:-2]],
          "observed": f"{genre} 분야 검색에서 {josa(b1['brand'], '가', '이')} 1위다(12개월 하한 {f_v1}). 브랜드 합 대비 몫은 {f_s1}, "
                      f"논브랜드까지 넣은 분야 전체 대비 몫은 {f_g1}. 상위 3개 브랜드의 몫 합은 브랜드 합 대비 {f_t3}.",
          "interpretation": "분야어를 붙여 검색할 때 먼저 떠올리는 브랜드의 순서로 읽는다 — 검색 관심의 몫이지 판매 몫이 아니다.",
          "alternatives": (f"표기변형 상한으로 세면 순위가 바뀌는 브랜드가 {f_flip} 있다({', '.join(flips)}) — 순위는 하한 기준이며 두 값을 함께 본다. "
                           if flips else "하한과 상한의 순위가 같다. ")
                          + (f"브랜드명 단독 검색({f_al})은 다른 품목 수요가 섞여 순위에서 뺐다." if f_al else ""),
          "limits": f"사전에 없는 브랜드는 논브랜드로 잡힌다. 마지막 달({S['data_ref_month']})은 빼고 {S['analysis_end_month']}까지 본다.",
          "actions": ["A1"]}
    summary_blocks = [d1]
    if ranked_yoy:
        r = ranked_yoy[0]
        base, comp, absd = num(r["yoy_base_12m"]), num(r["yoy_compare_12m"]), num(r["yoy_abs_12m"])
        fy = F(num(r["yoy_12m"]), "%", "real", c_all, f"{r['brand']} 최근 12개월 ÷ 전년 12개월 − 1 (같은 묶음 모집단)")
        fb = F(base, "건", "real", c_all, f"{r['brand']} 전년 12개월({W['y1']})")
        fc = F(comp, "건", "real", c_all, f"{r['brand']} 최근 12개월({W['y0']})")
        by_abs = max(ranked_yoy, key=lambda x: float(x["yoy_abs_12m"]))
        word = "늘었다" if absd > 0 else ("줄었다" if absd < 0 else "같다")
        extra = "" if by_abs["brand"] == r["brand"] else \
            f" 절대 증감 기준 1위는 {josa(by_abs['brand'], '다', '이다')}({F(num(by_abs['yoy_abs_12m']), '건', 'real', c_all, by_abs['brand'] + ' 최근 12개월 − 전년 12개월')})."
        summary_blocks.append({
            "type": "finding", "id": "D2", "evidence": [fy[2:-2], fb[2:-2], fc[2:-2]],
            "observed": f"전년 동기 대비 증감률 기준 1위는 {josa(r['brand'], '다', '이다')} — 전년 12개월 {fb} → 최근 12개월 {fc}, {fy} {word}.{extra}",
            "interpretation": "기저 게이트를 넘은 브랜드끼리만 비교한 증감률이다. 같은 묶음이 두 창 모두에 있는 경우만 더했다.",
            "alternatives": (f"기저가 작아 비율이 크게 나오는 브랜드({names(gated)})는 게이트 미만이라 순위에서 뺐다. " if gated else "")
                            + "출시 · 광고 · 이슈가 원인일 수 있으나 이 산출물은 원인을 판정하지 않는다.",
            "limits": f"전년 동기 게이트는 전년 12개월 합 기준 [가정]. 결측 월이 있는 묶음은 비교에서 빠진다.",
            "actions": ["A1"]})
    counter = f"반대 근거 — 논브랜드 분야 검색이 분야 전체에서 차지하는 몫은 {f_nb}(12개월 {f_nbv}). 브랜드끼리의 몫만 보면 분야 관심이 브랜드 밖에서 커지는 흐름을 놓친다."
    if sus:
        counter += (f" 다만 논브랜드 행 중 {f_sus}(12개월 {f_susv})은 앞부분이 사전 브랜드와 닮아 «별칭 누락 의심»이다"
                    f"({', '.join(x['keyword'] + '→' + x['near_brand'] for x in [y for y in sus if not re.search(r'[0-9]', y['keyword'])][:3]) or '부록 표'}) — 논브랜드 몫은 판정 보류이고, "
                    "사전(별칭 · variants)을 보강해 다시 돌리기 전에는 브랜드 순위도 과소집계일 수 있다.")
    if S["chg3y_unavailable"]:
        counter += f" 3년 흐름은 {', '.join(S['chg3y_unavailable'])}에서 계산하지 못했다(세 창 중 결측 — 0 으로 채우지 않는다)."
    if S["last_month_suspect"]:
        counter += f" 마지막 달 이상 의심 행이 있어({f_susp}) 그 달은 집계에서 뺐다."
    summary_blocks.append({"type": "paragraph", "html": counter})

    # ── 트렌드
    rows = []
    for r in top:
        row = {"brand": r["brand"], "b": num(r["yoy_base_12m"]), "c": num(r["yoy_compare_12m"]),
               "d": num(r["yoy_abs_12m"]), "p": num(r["yoy_12m"]), "q": num(r["yoy_3m"])}
        if r["yoy_gated_out"] == "1":
            row["note"] = "기저 게이트 미만 또는 결측 — 변화율을 비우고 순위에서 뺐다 [데이터 공백]"
        rows.append(row)
    trend_blocks = [{
        "type": "table", "caption": "상위 브랜드의 전년 동기 대비 — 같은 묶음 모집단",
        "columns": [{"key": "brand", "label": "브랜드"}, {"key": "b", "label": f"전년 12개월 {W['y1']}", "role": "base"},
                    {"key": "c", "label": f"최근 12개월 {W['y0']}", "role": "compare"},
                    {"key": "d", "label": "절대 증감", "role": "abs_change"}, {"key": "p", "label": "증감률", "role": "pct_change"},
                    {"key": "q", "label": "최근 3개월 전년비 %", "num": True}],
        "rows": rows,
        "meta": {"period": f"{W['y1']} 대비 {W['y0']}", "unit": "건 · %",
                 "method": "두 창 모두 값이 있는 묶음만 더한 합의 비율 · volume_trend 필드는 쓰지 않음",
                 "source": f"{c_all} keyword_info all monthly_volume",
                 "limits": "기저 게이트 미만 브랜드는 증감률을 비운다 · 검색은 관심 신호이며 판매가 아니다"}}]
    tr = {t["brand"]: t for t in trend}
    t5 = [r["brand"] for r in top[:5] if tr.get(r["brand"], {}).get("volume_y2")]
    if t5:
        trend_blocks.append({
            "type": "chart", "chart": "line",
            "meta": {"title": "3년 흐름 — 연도 창 합계", "takeaway": "세 창 모두 값이 있는 묶음만 더해 연도 간 모집단을 맞췄다.",
                     "period": f"{W['y2']} · {W['y1']} · {W['y0']}", "unit": "건(12개월 합)",
                     "method": "분석 월로 끝나는 12개월 창 셋 · 고정 모집단", "source": f"{c_all} monthly_volume",
                     "limits": "응답 시계열이 짧거나 결측이 있는 브랜드는 빠진다"},
            "data": {"x": ["2년 전", "전년", "최근"],
                     "series": [{"name": b, "values": [num(tr[b]["volume_y2"]), num(tr[b]["volume_y1"]), num(tr[b]["volume_y0"])]} for b in t5],
                     "normalized": False}})
    if ranked_yoy:
        trend_blocks.append({
            "type": "chart", "chart": "bar",
            "meta": {"title": "전년 동기 대비 증감률 — 게이트 통과 브랜드", "takeaway": "증감률과 절대 증감을 함께 본다.",
                     "period": f"{W['y1']} 대비 {W['y0']}", "unit": "% · 절대 증감 건", "method": "최근 12개월 ÷ 전년 12개월 − 1",
                     "source": f"{c_all} monthly_volume", "limits": "기저 게이트 미만은 표시하지 않는다"},
            "data": {"labels": [r["brand"] for r in ranked_yoy], "values": [num(r["yoy_12m"]) for r in ranked_yoy],
                     "abs": [num(r["yoy_abs_12m"]) for r in ranked_yoy], "value_unit": "%"}})

    # ── 관심 구조
    share_rows = [{"rank": f"{r['rank_lo']} / {r['rank_hi']}", "brand": r["brand"], "lo": num(r["volume_12m_lo"]),
                   "hi": num(r["volume_12m_hi"]), "sb": pctv(r["share_brandset_lo"]), "sg": pctv(r["share_genre_lo"]),
                   **({"note": "순위 뒤집힘 — 상한으로 세면 순위가 다르다 [추정]"} if r["rank_flip"] == "yes" else {})}
                  for r in top]
    share_rows.append({"rank": "—", "brand": "논브랜드 분야 검색", "lo": num(nb["volume_12m_lo"]), "hi": num(nb["volume_12m_hi"]),
                       "sb": None, "sg": pctv(nb["share_genre_lo"])})
    struct = [{"type": "table", "caption": "검색 점유 — 브랜드끼리의 몫과 분야 전체 중 몫",
               "columns": [{"key": "rank", "label": "순위 하한 / 상한"}, {"key": "brand", "label": "브랜드"},
                           {"key": "lo", "label": "12개월 하한", "num": True}, {"key": "hi", "label": "12개월 상한", "num": True},
                           {"key": "sb", "label": "브랜드 합 대비 %", "num": True}, {"key": "sg", "label": "분야 전체 대비 %", "num": True}],
               "rows": share_rows,
               "meta": {"period": W["y0"], "unit": "건 · %", "method": "하한 = 표기변형 묶음 최댓값 합 · 상한 = 묶음 합 · 같은 검색의 중복은 한 번만",
                        "source": f"{c_all} monthly_volume", "limits": "검색량 기준 근사 — 판매 점유가 아니다 · 브랜드명 단독 검색은 뺐다"}}]
    if seg:
        sb = [r["brand"] for r in top if any(s["brand"] == r["brand"] for s in seg)]
        cols = list(dict.fromkeys((s["segment_type"], s["segment"], s["segment_label"]) for s in seg))
        look = {(s["brand"], s["segment_type"], s["segment"]): s for s in seg}
        struct.append({
            "type": "chart", "chart": "heatmap",
            "meta": {"title": "성별 · 연령대 지수 — 그 세그먼트 안 몫 ÷ 전체 몫", "takeaway": "지수가 높은 칸은 그 세그먼트에서 상대적으로 더 많이 검색되는 브랜드다.",
                     "period": W["y0"], "unit": "지수(전체 몫과 같으면 기준값) · 칸 아래 절대 검색량 추정",
                     "method": "브랜드 12개월 하한 × 대표 검색어 성·연령 비율(볼륨 가중)", "source": f"{c_all} demography",
                     "limits": "성별과 연령대는 따로 계산한다 · 비율 창은 응답 사양을 따른다 · 순위는 세그먼트 게이트 통과분만"},
            "data": {"rows": sb, "cols": [c[2] for c in cols],
                     "values": [[num(look[(b, c[0], c[1])]["index_vs_all"]) if (b, c[0], c[1]) in look else None for c in cols] for b in sb],
                     "raw": [[num(look[(b, c[0], c[1])]["segment_volume_12m"]) if (b, c[0], c[1]) in look else None for c in cols] for b in sb]}})
        seg_rows = []
        for c in cols:
            ranked = sorted([s for s in seg if (s["segment_type"], s["segment"]) == c[:2] and s["rank_in_segment"]],
                            key=lambda s: int(s["rank_in_segment"]))
            seg_rows.append({"seg": c[2], "r1": ranked[0]["brand"] if ranked else "—",
                             "r2": ranked[1]["brand"] if len(ranked) > 1 else "—", "r3": ranked[2]["brand"] if len(ranked) > 2 else "—"})
        struct.append({"type": "table", "caption": "성별 · 연령대별 순위 — 세그먼트 게이트 통과 브랜드",
                       "columns": [{"key": "seg", "label": "세그먼트"}, {"key": "r1", "label": "1위"},
                                   {"key": "r2", "label": "2위"}, {"key": "r3", "label": "3위"}],
                       "rows": seg_rows,
                       "meta": {"period": W["y0"], "unit": "순위", "method": "세그먼트 추정 검색량 내림차순",
                                "source": f"{c_all} demography", "limits": "세그먼트 게이트 미만 · 커버리지 미달 브랜드는 순위에 없다 [가정]"}})
    else:
        struct.append({"type": "paragraph", "html": f"성별 · 연령대 순위 — {S['segment_status']}."})

    action = {"type": "action", "id": "A1", "evidence": [f_v1[2:-2], f_g1[2:-2], f_nb[2:-2]],
              "hypothesis": f"{genre} 분야어 검색에서 상위 브랜드와 논브랜드 검색의 몫을 기준선으로 두면, 다음 기준월의 순위 변화를 같은 정의로 추적할 수 있다.",
              "deliverable": "분야 브랜드 순위표(하한 · 상한) · 3년 흐름 · 성별·연령대 순위 · 다음 기준월 재실행 일정",
              "owner_role": "브랜드 · 카테고리 담당자, 리서처",
              "internal_data": "브랜드별 판매량 · 매장/온라인 유입 수(값은 내부에서만 대조)",
              "criteria": {"expand": "다음 기준월에도 순위와 증감 방향이 같으면 분야를 넓혀 같은 사전 규칙으로 돌린다",
                           "revise": "별칭 누락 의심 · 순위 뒤집힘이 나오면 사전을 고친 뒤 다시 돌린다",
                           "stop": "검색 순위와 내부 판매 순위가 반복해서 어긋나면 이 지표를 결정 근거에서 뺀다"}}

    gl = [r for r in rank if r["bucket_type"] == "brand" and not r["rank_lo"]]
    appendix = [{"type": "paragraph", "html":
                 f"데이터 기준월 {S['data_ref_month']} / 조회일 {S['queried_at']} / 지연 {S['lag_months']}개월. "
                 f"분석 창은 {S['analysis_end_month']}로 끝나는 12개월 셋({W['y2']} · {W['y1']} · {W['y0']})이다. "
                 "순위는 12개월 검색량 하한 기준이고, 점유는 «브랜드 합 대비»와 «분야 전체(논브랜드 포함) 대비» 두 분모로 낸다. "
                 "브랜드명 단독 · 분야어 없는 브랜드 검색은 다른 품목 수요가 섞여 순위 · 점유에서 뺐다. "
                 "변화율 게이트 · 세그먼트 게이트 · 고정 모집단 규칙은 [가정]이다."},
                {"type": "paragraph", "html":
                 f"데이터 공백 — 게이트 미만: {names(gated) or '없음'} · 3년 흐름 계산 불가: {', '.join(S['chg3y_unavailable']) or '없음'} · "
                 f"2단 검색량 없음: {names(gl) or '없음'} · 별칭 누락 의심: {'있음' if (S['alias_suspect'] or sus) else '없음'}."}]
    if sus:
        appendix.append({"type": "table", "caption": "별칭 누락 의심 — 논브랜드로 잡혔지만 사전 브랜드와 닮은 검색어",
                         "columns": [{"key": "q", "label": "검색어"}, {"key": "v", "label": "12개월 검색량"},
                                     {"key": "b", "label": "닮은 브랜드"}],
                         "rows": [{"q": x["keyword"], "v": x["volume_12m"], "b": x["near_brand"]} for x in sus[:15]],
                         "meta": {"period": W["y0"], "unit": "건", "method": "분야어 앞부분이 사전 키(표시명 · 별칭 · variants)와 겹치는 논브랜드 행",
                                  "source": f"{c_all} · {c_ads}", "limits": "의심이지 판정이 아니다 — 사전을 고쳐 다시 돌린 뒤 판단한다 [추정]"}})
    nb_big = float(nb["share_genre_lo"] or 0) >= float(b1["share_genre_lo"] or 0)
    if sus:
        s_title = f"{josa(b1['brand'], '가', '이')} 분야 검색 1위, 논브랜드 몫은 별칭 누락 의심으로 판정 보류"
    elif nb_big:
        s_title = f"{josa(b1['brand'], '가', '이')} 분야 검색 1위, 논브랜드 검색이 함께 크다"
    else:
        s_title = f"{josa(b1['brand'], '가', '이')} 분야 검색 1위다"

    excluded = "브랜드명 단독 · 분야어 없는 브랜드 검색(다른 품목 수요) · exclude 정규식 · 같은 검색의 중복 행"
    report = {
        "meta": {"title": f"{genre} 브랜드 검색 순위", "subtitle": (f"{a.subtitle_note} · " if a.subtitle_note else "") + f"{S['gl']} · 분석 월 {S['analysis_end_month']}",
                 "skill": "rank-category-brands", "version": VERSION},
        "boundary": f"{S['analysis_end_month']}까지의 {genre} 분야 검색 관심 순위이며, 판매 점유 · 원인 · 기준월 이후 변화는 관측하지 않았다",
        "lede_html": f"{josa(b1['brand'], '가', '이')} 1위다(브랜드 합 대비 몫 {f_s1}). 논브랜드 분야 검색이 분야 전체에서 차지하는 몫은 {f_nb}.",
        "scope": {"collected_at": S["queried_at"], "latest_month": S["analysis_end_month"], "gl": S["gl"],
                  "selection": "사용자가 확인한 브랜드 사전(정식명 · 별칭 · 모델) × 분야어 1단 스크리닝 → 2단 게이트 통과분",
                  "missing": "결측 월은 0 으로 바꾸지 않았다 — 창에 결측이 있는 묶음은 그 비교에서 뺐다",
                  "excluded": excluded,
                  "windows": {"최근 12개월": W["y0"], "전년 12개월": W["y1"], "2년 전 12개월": W["y2"], "최근 3개월": W["l3"]}},
        "facts": facts,
        "provenance": {"calls": [{k: v for k, v in c.items() if k != "data_type"} for c in calls], "currency": "크레딧",
                       **({"estimate": a.estimate} if a.estimate is not None else {})},
        "sections": [
            {"kind": "summary", "title": s_title, "blocks": summary_blocks},
            {"kind": "trend", "title": "최근 12개월은 전년과 같은 묶음으로만 비교했다", "blocks": trend_blocks},
            {"kind": "structure", "title": "브랜드끼리의 몫과 분야 전체 중 몫은 다르다", "blocks": struct},
            {"kind": "action", "title": "같은 정의로 다음 기준월을 다시 잰다", "blocks": [action]},
            {"kind": "appendix", "title": "계산 정의와 데이터 공백", "blocks": appendix}]}
    json.dump(report, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"report.json — 사실 {len(facts)}개 · 호출 {len(calls)}건 · 섹션 {len(report['sections'])}개 → {a.out}")


if __name__ == "__main__":
    main()
