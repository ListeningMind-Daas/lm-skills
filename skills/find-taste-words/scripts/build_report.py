#!/usr/bin/env python3
"""report.json 조립 — find-taste-words STEP 8.

taste_words.py의 저장 계산 결과(taste_summary_*.json · taste_words_*.csv · taste_tribes_*.csv ·
taste_collection_*.csv · taste_evidence_*.csv)와 호출 원장(taste_ledger.py)만 읽는다. 숫자를 새로 만들지 않는다.
  · 사실 목록(facts)은 위 파일의 값에서 만들고, 문구의 숫자는 {{F…}}로만 부른다
  · provenance.calls 는 원장을 그대로 옮긴다 — 손으로 적지 않는다
  · 판정이 «감각어 희소»이면 묶음을 결론으로 쓰지 않고 «보류»로 적는다

    python3 scripts/build_report.py --summary taste_summary_커피원두.json --ledger calls_ledger.json -o report.json
    python3 scripts/render_report.py report.json -o report.html
"""
import argparse, csv, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from taste_words import utf8_console  # noqa: E402

SKILL_VERSION = "0.1.1"


def rows(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig"))) if p.is_file() else []


def num(v):
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return None


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--top-words", type=int, default=10)
    ap.add_argument("--subtitle-note", default="", help="부제 앞에 붙일 표시 — 견본은 «합성 픽스처 예시(assets/fixtures) — 실제 시장 값이 아니다»")
    ap.add_argument("-o", "--out", default="report.json")
    a = ap.parse_args()

    sp = pathlib.Path(a.summary)
    s = json.loads(sp.read_text(encoding="utf-8"))
    d = sp.parent
    o = s["outputs"]
    words, tribes = rows(d / o["words"]), rows(d / o["tribes"])
    coll, evid = rows(d / o["collection"]), rows(d / o["evidence"])
    led = json.loads(pathlib.Path(a.ledger).read_text(encoding="utf-8"))
    calls = led.get("calls") or []
    cids = [c["id"] for c in calls]
    if_ids = [c["id"] for c in calls if c["tool"] == "intent_finder"]
    ki_ids = [c["id"] for c in calls if c["tool"] == "keyword_info"]
    serp_ids = [c["id"] for c in calls if c["tool"] == "serp"]
    span = lambda ids: (ids[0] + (f"–{ids[-1]}" if len(ids) > 1 else "")) if ids else "원장"   # noqa: E731
    kw, sumf = s["keyword"], sp.name
    sparse = s["verdict"].startswith("감각어 희소")
    groups = [t for t in tribes if t["status"] == "묶음"]
    top = groups[0] if groups else None
    avoid = sorted((w for w in words if num(w["avoid_units"]) and num(w["avoid_units"]) >= num(w["want_units"])),
                   key=lambda w: (-num(w["avoid_units"]), w["word"]))

    F = {}

    def fact(fid, value, unit, label, source, calc):
        F[fid] = {"value": value, "unit": unit, "label": label, "source": source, "calc": calc}
        return "{{%s}}" % fid

    f_q = fact("F1", s["n_query_groups"], "개", "[실제 데이터]", f"{span(ki_ids)} · {sumf}",
               "유니버스 canonical 그룹 수 — 같은 검색 중복(dup_of) · 경계 밖 제외")
    f_s = fact("F2", s["n_serp_units"], "개", "[실제 데이터]", f"{span(serp_ids)} · {sumf}",
               "SERP 문구를 문장으로 자르고 canonical이 같은 문장을 한 번만 센 수")
    f_dens = fact("F3", round(s["taste_density"] * 100, 1), "%", "[추정]", sumf,
                  "감각어·감정어가 하나 이상 든 단위 ÷ 전체 단위 (사전 매칭)")
    f_w = fact("F4", s["n_words"], "개", "[추정]", sumf, "사전에 걸린 대표 낱말 수(표기변형은 한 낱말)")
    f_t = fact("F5", s["n_tribes"], "개", "[추정]", sumf, "구성 낱말 2개 이상인 공기 묶음 수")
    f_g = fact("F6", len(s["serp_keywords"]), "개", "[실제 데이터]", span(serp_ids), "SERP를 조회한 검색어 수(게이트 통과)")
    f_tr = fact("F7", s["n_truncated_calls"], "건", "[실제 데이터]", f"{span(if_ids)} · {o['collection']}",
                "intent_finder 호출 중 반환 수가 요청 limit에 닿은 호출")
    f_cost = fact("F8", sum(c.get("cost") or 0 for c in calls), "크레딧", "[실제 데이터]", span(cids),
                  "호출 원장 cost_detail.total_cost 합")
    f_sus = fact("F15", round(s.get("boundary_suspect_share", 0) * 100, 1), "%", "[추정]", sumf,
                 "단위×낱말 적중 중 매칭 뒤 같은 어절에 조사 · 어미가 아닌 글자가 이어지는 것(더 긴 낱말 속 앞부분 일치 의심)의 비중")
    if top:
        f_tlo = fact("F9", num(top["query_volume_lo"]), "건", "[추정]", o["tribes"],
                     f"{top['tribe']} 구성 낱말이 든 검색어 그룹 검색량 합 — 그룹 최댓값 기준(하한)")
        f_thi = fact("F10", num(top["query_volume_hi"]), "건", "[추정]", o["tribes"],
                     f"{top['tribe']} 같은 검색어 그룹의 그룹 합 기준(상한)")
        f_tu = fact("F11", num(top["units"]), "개", "[추정]", o["tribes"], f"{top['tribe']} 구성 낱말이 든 단위 수")
        f_coh = fact("F12", num(top["cohesion"]), "", "[추정]", o["tribes"], f"{top['tribe']} 구성 낱말 쌍 평균 자카드")
    if avoid:
        aw = avoid[0]
        f_av = fact("F13", num(aw["avoid_units"]), "개", "[추정]", o["words"],
                    f"«{aw['word']}» 뒤·앞에 부정 표지(없는·덜한·안)가 붙은 단위 수")
        f_avs = fact("F14", round(num(aw["avoid_share"]) * 100, 1), "%", "[추정]", o["words"], f"«{aw['word']}» 회피 단위 ÷ 출현 단위")

    cov_word = s["coverage_status"]
    cov_txt = {"보류": f"수집 범위 판정은 <b>보류</b>다 — intent_finder 호출 중 {f_tr}이 요청 limit에 닿아 더 있을 수 있다",
               "수렴": "수집 범위는 라운드 신규 기여 기준으로 <b>수렴</b>했다(절단 호출 없음)",
               "미판정": "수집 범위는 <b>미판정</b>이다 — 라운드 기여 곡선을 돌리지 않았다"}[cov_word]

    # ── 요약
    summary_blocks = []
    if sparse or not top:
        summary_blocks.append({
            "type": "finding", "id": "D1", "evidence": ["F3", "F4", "F5"],
            "observed": f"감각어·감정어가 든 단위 비중 {f_dens} · 걸린 낱말 {f_w} · 묶음 {f_t}",
            "interpretation": "[추정] 이 카테고리는 감각 언어보다 조건·가격·절차의 언어로 검색된다 — 취향 묶음 판정을 보류한다",
            "alternatives": "사전이 이 카테고리의 감각 표현을 못 담았을 수 있다 — 사전 밖 후보를 보고 보강한 뒤 다시 돌린다(0크레딧)",
            "limits": f"검색어와 화면 문구만 봤다 — 리뷰 · 상담 기록의 감정 언어는 관측하지 않았다 · 경계 의심 적중 {f_sus}",
            "actions": ["A1"]})
    else:
        summary_blocks.append({
            "type": "finding", "id": "D1", "evidence": ["F9", "F10", "F11", "F12"],
            "observed": f"가장 큰 취향 묶음 {top['tribe']}은 «{top['words']}» — 구성 낱말이 든 검색어 검색량 하한 {f_tlo} ~ 상한 {f_thi} · 단위 {f_tu}",
            "interpretation": "[추정] 이 낱말들은 같은 검색어 · 같은 화면 문장에 함께 쓰인다 — 제품 언어 · 메시지의 한 묶음 후보다",
            "alternatives": "함께 쓰인 것은 연관이지 같은 사람의 취향이라는 뜻이 아니다 · 화면 문장은 판매자 · 매체가 쓴 문구일 수 있다",
            "limits": f"평균 자카드 {f_coh} · 사전 규칙 분류(경계 의심 적중 {f_sus}) · 검색은 구매나 실제 맛 평가가 아니다",
            "actions": ["A1"]})
    counter = [f"<b>반대 근거</b> — {cov_txt}."]
    if avoid:
        counter.append(f"«{avoid[0]['word']}»은 회피로 더 자주 쓰였다(회피 단위 {f_av} · 비중 {f_avs}) — 이 말을 장점으로 쓰면 소비자 언어와 반대가 된다.")
    if not sparse:
        counter.append(f"감각어가 든 단위는 전체의 {f_dens}뿐이다 — 나머지 검색은 감각이 아닌 이유로 일어난다.")
    summary_blocks.append({"type": "paragraph", "html": " ".join(counter)})

    sections = [{"kind": "summary",
                 "title": (f"{kw}는 «{top['words']}»의 말로 함께 검색된다" if top and not sparse
                           else f"{kw}는 감각어가 적어 취향 묶음 판정을 보류한다"),
                 "blocks": summary_blocks}]

    # ── 관심 구조
    t_cols = [{"key": "tribe", "label": "묶음"}, {"key": "status", "label": "상태"}, {"key": "words", "label": "낱말"},
              {"key": "axes", "label": "감각 축"}, {"key": "units", "label": "단위"},
              {"key": "query_volume_lo", "label": "검색량 하한"}, {"key": "query_volume_hi", "label": "검색량 상한"},
              {"key": "serp_exposure_lo", "label": "화면 노출 검색량"}, {"key": "avoid_words", "label": "회피 낱말"},
              {"key": "cohesion", "label": "평균 자카드"}]
    trows = [{k["key"]: (num(t[k["key"]]) if k["key"] in ("units", "query_volume_lo", "query_volume_hi", "serp_exposure_lo", "cohesion")
                         else t[k["key"]]) for k in t_cols} for t in tribes]
    tw = words[: a.top_words]
    struct = [
        {"type": "table", "caption": "취향 묶음 — 함께 쓰인 감각어·감정어", "columns": t_cols, "rows": trows,
         "meta": {"title": "취향 묶음", "takeaway": "묶음은 같은 단위에 함께 나온 낱말이다 — 단독은 함께 쓰인 짝이 없는 낱말",
                  "period": "검색량은 최신 12개월 합 · SERP는 저장 스냅샷", "unit": "검색어 그룹 검색량 건 · 단위 개",
                  "method": "평균 자카드가 임계 이상인 묶음에 합류하는 결정적 단일 통과 · 하한은 그룹 최댓값 · 상한은 그룹 합",
                  "source": f"{o['tribes']} · {span(cids)}", "limits": "공기는 연관이지 인과나 같은 사람이 아니다 · 사전 밖 표현은 빠진다"}},
        {"type": "chart", "chart": "bar",
         "meta": {"title": "감각어·감정어별 검색량 하한", "takeaway": "검색어에 직접 들어간 감각어의 규모 — 화면 문장에만 나온 말은 막대가 없다",
                  "period": "최신 12개월 합", "unit": "건", "method": "낱말이 든 검색어 canonical 그룹의 그룹 최댓값 합",
                  "source": o["words"], "limits": "표기변형이 다른 검색이면 상한까지 클 수 있다 — 표의 상한 열을 함께 본다"},
         "data": {"labels": [w["word"] for w in tw], "values": [num(w["query_volume_lo"]) or 0 for w in tw], "value_unit": "건"}},
        {"type": "table", "caption": "낱말별 크기 · 회피 · 화면 노출", "columns": [
            {"key": "word", "label": "낱말"}, {"key": "axis", "label": "축"}, {"key": "variants", "label": "표기변형"},
            {"key": "query_volume_lo", "label": "검색량 하한"}, {"key": "query_volume_hi", "label": "검색량 상한"},
            {"key": "serp_units", "label": "화면 문장"}, {"key": "serp_exposure_lo", "label": "화면 노출 검색량"},
            {"key": "want_units", "label": "선호 단위"}, {"key": "avoid_units", "label": "회피 단위"}, {"key": "tribe", "label": "묶음"}],
         "rows": [{k: (num(w[k]) if k in ("query_volume_lo", "query_volume_hi", "serp_units", "serp_exposure_lo", "want_units", "avoid_units")
                       else w[k]) for k in ("word", "axis", "variants", "query_volume_lo", "query_volume_hi", "serp_units",
                                            "serp_exposure_lo", "want_units", "avoid_units", "tribe")} for w in tw],
         "meta": {"title": "낱말별 크기", "takeaway": "검색량과 화면 노출은 다른 무게다 — 합치지 않는다",
                  "period": "최신 12개월 합 · SERP 저장 스냅샷", "unit": "건 · 개",
                  "method": "검색량은 낱말이 든 검색어 그룹 합 · 화면 노출은 낱말이 뜬 SERP 검색어들의 검색량 하한 합",
                  "source": o["words"], "limits": "화면 노출은 그 문구를 실제로 읽은 수가 아니다"}},
    ]
    sections.append({"kind": "structure",
                     "title": ("묶음별로 보면 감각 축이 갈린다" if groups else "함께 쓰인 짝이 적어 낱말 단위로만 본다"),
                     "blocks": struct})

    # ── 기회와 실행
    if sparse or not top:
        act = {"type": "action", "id": "A1", "evidence": ["F3", "F4"],
               "hypothesis": "[가정] 이 카테고리의 선택 언어는 감각이 아니라 조건 · 절차 · 신뢰다",
               "deliverable": "구매 기준 언어 점검 — find-customer-needs(기능·속성·불편) · decode-search-intent(의도 층) 결과와 대조한 메시지 초안",
               "owner_role": "상품기획자 · 마케터", "internal_data": "상담 · 리뷰 원문의 감정 표현, 문의 유형",
               "criteria": {"expand": "리뷰 원문에서 감각 · 감정 표현이 반복되면 사전을 보강해 이 스킬을 다시 돌린다",
                            "revise": "사전 밖 후보에 감각어가 많으면 사전을 고친 뒤 재집계(0크레딧)",
                            "stop": "리뷰에서도 감각 언어가 거의 없으면 취향 메시지 작업을 접는다"}}
    else:
        act = {"type": "action", "id": "A1", "evidence": ["F9", "F10", "F12"],
               "hypothesis": f"[가정] «{top['words']}» 낱말로 쓴 제품 문구가 지금 문구보다 상세 페이지 첫 화면에서 더 오래 읽힌다",
               "deliverable": "상세 페이지 첫 화면 · 검색 광고 문구 시안 두 벌(묶음 낱말 사용 / 현행)",
               "owner_role": "마케터 · 상품기획자", "internal_data": "상세 페이지 체류 · 클릭률 · 전환율, 리뷰 원문의 같은 낱말 빈도",
               "criteria": {"expand": "묶음 낱말 시안의 클릭률 · 전환율이 현행보다 높고 리뷰에도 같은 낱말이 나온다",
                            "revise": "차이가 없으면 둘째 묶음 낱말로 바꿔 다시 시험한다",
                            "stop": "회피 낱말이 리뷰 불만과 겹치면 그 낱말을 쓰지 않는다"}}
    sections.append({"kind": "action", "title": "묶음 낱말을 문구 시험에 걸어 본다" if not sparse else "감각 대신 선택 기준 언어를 본다",
                     "blocks": [act]})

    # ── 근거 부록
    sections.append({"kind": "appendix", "title": "전체 낱말 · 수집 범위 · 근거 문장", "blocks": [
        {"type": "paragraph", "html": f"단위는 검색어 그룹 {f_q}과 SERP 문장 {f_s}이다. SERP는 {f_g} 검색어에서 조회했다. "
                                      f"이번 산출물 소진은 {f_cost}이다. {cov_txt}."},
        {"type": "details", "summary": "수집 범위 — 호출별 요청 limit 과 반환 수(절단 검사)", "blocks": [
            {"type": "table", "caption": "intent_finder 호출별 절단 검사",
             "columns": [{"key": k, "label": lb} for k, lb in (("round", "라운드"), ("file", "파일"), ("seeds", "시드"),
                                                              ("limit", "요청 limit"), ("returned", "반환"), ("truncated", "절단"))],
             "rows": [{**c, "limit": num(c["limit"]), "returned": num(c["returned"])} for c in coll],
             "meta": {"title": "호출별 절단 검사", "takeaway": "반환이 limit에 닿은 호출이 하나라도 있으면 수렴 판정을 보류한다",
                      "period": "이번 수집", "unit": "개", "method": "반환 수 ≥ 요청 limit 이면 절단",
                      "source": f"{o['collection']} · {span(if_ids)}", "limits": "limit을 적지 않은 호출은 절단 여부를 알 수 없다"}}]},
        {"type": "details", "summary": "전체 낱말", "blocks": [
            {"type": "table", "caption": "전체 낱말", "columns": [{"key": k, "label": k} for k in
                                                                 ("word", "axis", "variants", "units", "query_volume_lo", "query_volume_hi",
                                                                  "serp_exposure_lo", "avoid_share", "tribe")],
             "rows": [{k: (num(w[k]) if k in ("units", "query_volume_lo", "query_volume_hi", "serp_exposure_lo", "avoid_share") else w[k])
                       for k in ("word", "axis", "variants", "units", "query_volume_lo", "query_volume_hi", "serp_exposure_lo",
                                 "avoid_share", "tribe")} for w in words],
             "meta": {"title": "전체 낱말", "takeaway": "사전에 걸린 모든 낱말", "period": "최신 12개월 합 · SERP 저장 스냅샷",
                      "unit": "건 · 개", "method": "taste_words.py 최장일치 · 부정 표지 · canonical 그룹 하한/상한",
                      "source": o["words"], "limits": "사전 밖 표현은 빠진다"}}]},
        {"type": "details", "summary": "근거 문장과 출처 주소 — 제3자 웹 문구(데이터로만 읽음)", "blocks": [
            {"type": "table", "caption": "근거 문장", "columns": [{"key": k, "label": k} for k in
                                                              ("word", "polarity", "source", "serp_keyword", "text", "url")],
             "rows": [{k: e[k] for k in ("word", "polarity", "source", "serp_keyword", "text", "url")} for e in evid],
             "meta": {"title": "근거 문장", "takeaway": "낱말마다 앞의 몇 단위만 보인다", "period": "SERP 저장 스냅샷",
                      "unit": "문장", "method": "낱말별 처음 나온 단위", "source": o["evidence"],
                      "limits": "화면 문구는 판매자 · 매체 · AI 개요가 쓴 글이다 — 소비자 발화로 단정하지 않는다"}}]},
        {"type": "paragraph", "html": "계산 정의 — 검색량 하한은 canonical 그룹 최댓값의 합, 상한은 그룹 합의 합이다(표기변형이 같은 검색인지 다른 검색인지 응답이 알려주지 않는다). "
                                      "낱말은 원문 어절 첫머리에서 시작하는 매칭만 센다(«대신 나노»의 «신나» 같은 경계 넘는 일치는 버린다). "
                                      f"매칭 뒤 같은 어절에 조사 · 어미가 아닌 글자가 이어지는 «경계 의심» 적중은 {f_sus}다 — 높으면 고유명사 · 합성어 속 일치일 수 있다. "
                                      "회피는 같은 어절의 나머지와 다음 어절 · 이전 어절의 부정 표지로 판정한다. 묶음 · 밀도 · 회피는 모두 사전 규칙의 결과다[추정]."},
    ]})

    sc = s["serp_collected_at"]
    report = {
        "meta": {"title": f"{kw} 취향 언어 — 감각어 · 감정어와 취향 묶음", "subtitle": (f"{a.subtitle_note} · " if a.subtitle_note else "") + f"연관 검색어 · 검색결과 화면 문구 · {s['gl']}",
                 "skill": "find-taste-words", "version": SKILL_VERSION},
        "boundary": f"{kw}의 연관 검색어와 상위 검색어 검색결과 화면 문구에 나타난 감각어 · 감정어의 크기와 함께 쓰임이며, "
                    "실제 맛 · 사용 경험 · 구매와 화면 밖 리뷰는 관측하지 않았다",
        "lede_html": (f"검색어 그룹 {f_q}과 화면 문장 {f_s}에서 감각어 · 감정어 {f_w}을 찾았고 함께 쓰인 묶음은 {f_t}이다. "
                      + ("감각어 밀도가 낮아 묶음은 결론으로 쓰지 않는다." if sparse else
                         f"가장 큰 묶음은 «{top['words']}»이다." if top else "")),
        "scope": {"collected_at": s.get("queried_at") or "미기재", "latest_month": "기준월 미사용 — ads_metrics 최신 12개월 합(시계열을 읽지 않는다)",
                  "gl": s["gl"],
                  "selection": f"시드 + 감각 탐침어 → intent_finder 확장 → ads_metrics 전량 → SERP 게이트(규모 상위 + 감각어 포함 상위)",
                  "missing": ("검색량 없는 SERP 검색어: " + ", ".join(s["serp_keywords_without_volume"]))
                             if s["serp_keywords_without_volume"] else "미반환 · 결측은 0으로 채우지 않았다",
                  "excluded": f"같은 검색 중복 행 · 경계 밖 패턴 · 이미지 · 동영상 등 문구가 짧은 블록 · 카테고리어 자체({', '.join(s['params']['heads'])})",
                  "windows": {"검색량": "최신 12개월 합(volume_total)",
                              "SERP 스냅샷": (sc[0][:10] + (" ~ " + sc[-1][:10] if len(sc) > 1 else "")) if sc else "collected_at 없음"}},
        "facts": F,
        "provenance": {"calls": calls, "currency": led.get("currency", "크레딧"),
                       **({"estimate": led["estimate"]} if led.get("estimate") is not None else {})},
        "sections": sections,
    }
    pathlib.Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"사실 {len(F)}개 · 호출 {len(calls)}건 · 소진 {F['F8']['value']:,} → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
