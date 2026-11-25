#!/usr/bin/env python3
"""저장된 계산 결과 → report.json (이 스킬 고유). SKILL.md STEP 9.

  python3 build_report.py --summary out/segment_summary_{category}.json --ledger ledger.json \\
      --queried-at 2026-10-15 -o report.json
  python3 render_report.py report.json -o report.html

· 사실 목록(facts)은 segment_summary · segment_keywords · segment_entities 의 값에서만 만든다. 문구 숫자는 {{ID}} 로만 쓴다.
· provenance.calls 는 호출 원장(ledger.json — segment_candidates.py collect · screen · ledger 가 쓴다)에서 옮긴다.
· 문장은 템플릿이다 — 순위 문장의 기준(세그먼트 절대 증가 · 전년비)과 방향어는 값에서 고른다(렌더링 계약 §3-8).
· 범위 미확인으로 보류한 후보(summary.scope_held)는 헤드라인 · D1 · A1 주어가 되지 않는다 — 별도 표와 반대 근거에만 둔다.
"""
import argparse, csv, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from segment_candidates import utf8_console  # noqa: E402

CALL_KEYS = ["id", "step", "at", "tool", "params", "purpose", "returned", "cost", "raw", "data_through", "status"]


class Facts:
    def __init__(self):
        self.d = {}

    def add(self, value, unit, label, source, calc):
        fid = f"F{len(self.d) + 1}"
        self.d[fid] = {"value": value, "unit": unit, "label": label, "source": source, "calc": calc}
        return "{{" + fid + "}}"

    def ids(self, *refs):
        return [r.strip("{}") for r in refs if r]


def read_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def num(v):
    if v in ("", None):
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True)
    ap.add_argument("--keywords", help="기본: summary 옆 segment_keywords_*.csv")
    ap.add_argument("--entities", help="기본: summary 옆 segment_entities_*.csv")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--queried-at", required=True)
    ap.add_argument("--version", default="0.1.1")
    ap.add_argument("--subtitle-note", default="", help="부제 앞에 붙일 표시 — 견본은 «합성 픽스처 예시(assets/fixtures) — 실제 시장 값이 아니다»")
    ap.add_argument("-o", "--out", default="report.json")
    a = ap.parse_args()

    sp = pathlib.Path(a.summary)
    S = json.loads(sp.read_text(encoding="utf-8"))
    K = read_csv(a.keywords or sp.with_name(S["files"]["keywords"]))
    E = read_csv(a.entities or sp.with_name(S["files"]["entities"]))
    L = json.loads(pathlib.Path(a.ledger).read_text(encoding="utf-8"))
    calls = [{k: c.get(k) for k in CALL_KEYS if k in c} for c in L.get("calls", [])]
    kw_call = next((c["id"] for c in reversed(calls) if c.get("tool") == "keyword_info" and c.get("data_through")), None)
    if not kw_call:
        sys.exit("원장에 keyword_info all 호출(data_through 있음)이 없다 — segment_candidates.py ledger 로 덧붙인 뒤 다시 돌린다")
    src = f"{kw_call} · {S['files']['keywords']}"

    F = Facts()
    seg, win, g = S["segment"], S["window"], S["gates"]
    label = seg["label"] if seg["applied"] else "전체(세그먼트 미적용)"
    ref, gl = S["data_ref_month"], S["gl"]
    seg_lab = "[추정]"   # 세그먼트 검색량 = 전체 × 현재 비중 — 파생
    f_gate = F.add(g["gate"], "건", "[가정]", "SKILL.md STEP 7 기본값", "전체 전년 동기 창 최소 볼륨")
    f_sgate = F.add(g["seg_gate"], "건", "[가정]", "SKILL.md STEP 7 기본값", "세그먼트 창 최소 볼륨")
    f_ming = F.add(round(g["min_growth"] * 100, 1), "%", "[가정]", "SKILL.md STEP 7 기본값", "급등 최소 전년비")
    f_mini = F.add(g["min_index"], "배", "[가정]", "SKILL.md STEP 7 기본값", "과대 지수 최소값")
    C = S["counts"]
    f_rows = F.add(C["rows"], "개", "[실제 데이터]", src, "keyword_info all 응답 행(완전 중복 합친 뒤)")
    surge, newc, over = S["tabs"]["surge"], S["tabs"]["newcomer"], S["tabs"]["overindex"]

    facts_top = {}
    if surge:
        t = surge[0]
        facts_top["prev"] = F.add(num(t["segment_volume_prev_year"]), "건", seg_lab, src, f"{t['keyword']} 세그먼트 전년 창({win['prev']}) = 전체 × 비중")
        facts_top["cur"] = F.add(num(t["segment_volume_window"]), "건", seg_lab, src, f"{t['keyword']} 세그먼트 최근 창({win['cur']})")
        facts_top["abs"] = F.add(num(t["segment_abs_change"]), "건", seg_lab, src, f"{t['keyword']} 세그먼트 절대 증가")
        facts_top["yoy"] = F.add(round(float(t["yoy"]) * 100, 1), "%", "[실제 데이터]", src,
                                 f"{t['keyword']} 전체 전년 동기 대비 — 세그먼트 전년비와 같다(현재 비중을 양쪽 창에 씌움)")
        if t["over_index"] != "":
            facts_top["idx"] = F.add(num(t["over_index"]), "배", "[추정]", src, f"{t['keyword']} 세그먼트 비중 ÷ 후보 풀 기준 비중")
    ty = S.get("top_surge_by_yoy")
    f_ty = None
    if surge and ty and ty["keyword"] != surge[0]["keyword"]:
        f_ty = F.add(round(float(ty["yoy"]) * 100, 1), "%", "[실제 데이터]", src, f"{ty['keyword']} 전년비(판정 행 중 최고)")
    f_expl = [(r, F.add(round(float(r["yoy"]) * 100, 1), "%", "[실제 데이터]", src, f"{r['keyword']} 전년비 — 기저 게이트 미달"),
               F.add(num(r["volume_prev_year_window"]), "건", "[실제 데이터]", src, f"{r['keyword']} 전년 창"))
              for r in S.get("exploded_out", [])[:3]]
    f_base = F.add(round(S["baseline_share"] * 100, 1), "%", "[추정]", src,
                   "후보 풀 대표 검색어의 세그먼트 비중 볼륨 가중 평균") if S.get("baseline_share") else None

    # ── 표
    def surge_rows(rs):
        out = []
        for r in rs:
            b, c_ = num(r["segment_volume_prev_year"]), num(r["segment_volume_window"])
            out.append({"q": r["keyword"], "e": r["entity"], "b": b, "c": c_, "d": num(r["segment_abs_change"]),
                        "p": round(float(r["yoy"]) * 100, 1) if r["yoy"] != "" else None,
                        "i": num(r["over_index"]),
                        "note": " · ".join(x for x in (
                            "잠정 — 다른 마지막 달 처리 모드에서 급등 기준을 넘나든다(CSV yoy_other_mode)" if r["provisional"] else "",
                            "창 이후 이슈 — 원인 근거 아님" if str(r.get("issue_timing", "")).startswith("창 이후") else "",
                            "--include 승격(범위 미확인이었음)" if str(r.get("scope", "")).startswith("승격") else "") if x)})
        return out
    cols_surge = [{"key": "q", "label": "검색어"}, {"key": "e", "label": "대상 [추정]"},
                  {"key": "b", "label": f"세그먼트 {win['prev']}", "role": "base"},
                  {"key": "c", "label": f"세그먼트 {win['cur']}", "role": "compare"},
                  {"key": "d", "label": "절대 증가", "role": "abs_change"},
                  {"key": "p", "label": "전년비", "role": "pct_change"}, {"key": "i", "label": "과대 지수"}]
    meta_common = {"period": f"{win['cur']} vs {win['prev']} (기준월 {ref})", "unit": "건(3개월 합)",
                   "source": f"keyword_info all ({kw_call}) · segment_risers.py",
                   "limits": "세그먼트 검색량은 현재 성·연령 비율을 씌운 추정 · 검색은 구매가 아니다"}
    expl_rows = [{"q": r["keyword"], "e": r["entity"], "b": num(r["segment_volume_prev_year"]), "c": num(r["segment_volume_window"]),
                  "d": num(r["segment_abs_change"]), "p": round(float(r["yoy"]) * 100, 1), "i": num(r["over_index"]),
                  "note": "전년 창이 게이트 미만 — 순위에서 뺐다"} for r in S.get("exploded_out", [])]
    expl_rows += [{"q": r["keyword"], "e": r["entity"], "b": num(r["segment_volume_prev_year"]), "c": num(r["segment_volume_window"]),
                   "d": num(r["segment_abs_change"]), "p": round(float(r["yoy"]) * 100, 1), "i": num(r["over_index"]),
                   "note": "세그먼트 전년 창이 기준 미만 — 순위에서 뺐다"} for r in S.get("seg_small_out", [])]

    trend_blocks = [{"type": "table", "caption": f"급등 — {label} 세그먼트 절대 증가 순", "columns": cols_surge,
                     "rows": surge_rows(surge),
                     "meta": dict(meta_common, method="전체 전년 창 게이트 · 세그먼트 전년 창 게이트 · 급등 최소 전년비를 모두 넘은 행만, 세그먼트 절대 증가 순 "
                                  "(마지막 달 제외 · 기준값은 부록 사실 목록의 [가정] 항목)")}]
    if surge:
        trend_blocks.append({"type": "chart", "chart": "bar",
                             "meta": dict(meta_common, title="세그먼트 절대 증가 상위",
                                          takeaway=f"1위 {surge[0]['keyword']} — 세그먼트에서 {facts_top['abs']} 늘었다",
                                          method="최근 3개월 합 − 전년 같은 3개월 합, 둘 다 현재 세그먼트 비중을 곱함", unit="건"),
                             "data": {"labels": [r["keyword"] for r in surge[:8]],
                                      "values": [num(r["segment_abs_change"]) for r in surge[:8]], "value_unit": "건"}})
    if expl_rows:
        trend_blocks.append({"type": "table", "caption": "순위에서 뺀 급등 — 기저가 작다(T4)", "columns": cols_surge, "rows": expl_rows,
                             "meta": dict(meta_common, method="게이트 미달 행 — 전년비는 기저 효과로 부푼다. 참고로만 둔다")})
    held = S.get("scope_held", [])
    if held:
        hrows = surge_rows(held)
        for h, r in zip(hrows, held):
            h["note"] = " · ".join(x for x in (
                "범위 미확인 — 순위 보류" + (f"(기준 충족: {'·'.join(r.get('would_tabs') or [])})" if r.get("would_tabs") else ""),
                "창 이후 이슈 — 원인 근거 아님" if str(r.get("issue_timing", "")).startswith("창 이후") else "") if x)
        trend_blocks.append({"type": "table", "caption": "범위 미확인으로 순위에서 보류한 후보 — trend_finder 단독 · 시드·연관어와 겹침 없음",
                             "columns": cols_surge, "rows": hrows,
                             "meta": dict(meta_common, method="범위 점검(시드·카테고리어 포함 또는 연관어 토큰 공유)을 통과하지 못한 행 — "
                                          "값은 계산했지만 순위 · 과대 지수 기준 풀 · 헤드라인에서 뺐다. 사람이 확인하면 --include 로 승격한다")})
    new_rows = [{"q": r["keyword"], "e": r["entity"], "m": r["first_active_month"], "c": num(r["segment_volume_window"]),
                 "i": num(r["over_index"]), "note": "과거 창 결측 — 전년비 없음"} for r in newc]
    trend_blocks.append({"type": "table", "caption": "신규 — 과거 창 결측에서 최근 등장",
                         "columns": [{"key": "q", "label": "검색어"}, {"key": "e", "label": "대상 [추정]"},
                                     {"key": "m", "label": "검색 시작 월"}, {"key": "c", "label": f"세그먼트 {win['cur']}"},
                                     {"key": "i", "label": "과대 지수"}],
                         "rows": new_rows, "meta": dict(meta_common, method=f"검색 시작 월이 최근 창 끝에서 {g['new_within']}개월 안 · 그 전 달은 결측 또는 0 · "
                                                        "세그먼트 최근 창이 세그먼트 게이트 이상 (기준값은 부록 사실 목록)")})

    over_rows = [{"q": r["keyword"], "e": r["entity"], "s": num(r["segment_share"]), "i": num(r["over_index"]),
                  "c": num(r["segment_volume_window"]), "note": r["share_basis"] if "추정" in r["share_basis"] else ""} for r in over]
    struct_blocks = []
    if seg["applied"]:
        struct_blocks.append({"type": "table", "caption": f"과대 지수 — {label} 비중이 후보 풀보다 큰 검색어",
                              "columns": [{"key": "q", "label": "검색어"}, {"key": "e", "label": "대상 [추정]"},
                                          {"key": "s", "label": "세그먼트 비중"}, {"key": "i", "label": "과대 지수"},
                                          {"key": "c", "label": f"세그먼트 {win['cur']}"}],
                              "rows": over_rows,
                              "meta": dict(meta_common, method="세그먼트 비중 ÷ 후보 풀 기준 비중 · 과대 지수 최소값 · 전체 최근 창 게이트 · 세그먼트 최근 창 게이트를 "
                                           "모두 넘은 행만 (기준값은 부록 사실 목록)",
                                           limits="기준은 이번 후보 풀이지 시장 전체가 아니다 · 성별 × 연령 곱은 독립 가정")})
        if over:
            struct_blocks.append({"type": "chart", "chart": "bar",
                                  "meta": dict(meta_common, title="과대 지수 상위", unit="배",
                                               takeaway=f"{over[0]['keyword']}의 {label} 비중이 후보 풀 평균보다 가장 크다",
                                               method="세그먼트 비중 ÷ 후보 풀 기준 비중",
                                               limits="기준은 이번 후보 풀 · 지수는 크기와 함께 본다"),
                                  "data": {"labels": [r["keyword"] for r in over[:8]],
                                           "values": [num(r["over_index"]) for r in over[:8]], "value_unit": "배"}})
    ent_rows = [{"r": num(e["rank_entity"]), "e": e["entity"], "n": num(e["n_keywords"]), "lo": num(e["segment_volume_lo"]),
                 "hi": num(e["segment_volume_hi"]), "b": num(e["segment_prev_year_lo"]), "d": num(e["segment_abs_change_lo"]),
                 "p": round(float(e["yoy_lo"]) * 100, 1) if e["yoy_lo"] != "" else None, "i": num(e["over_index_wavg"]),
                 "note": ("범위 미확인 — 표에만 둔다" if e["note"].startswith("범위 미확인") else "기저 미달 — 표에만 둔다")
                 if e["headline_ok"] == "아니오" else ("신규 검색어 포함" if e["has_newcomer"] else "")}
                for e in E]
    struct_blocks.append({"type": "table", "caption": "대상(엔티티)별 세그먼트 규모와 증가 — 규칙 묶음 [추정]",
                          "columns": [{"key": "r", "label": "순위"}, {"key": "e", "label": "대상"}, {"key": "n", "label": "검색어 수"},
                                      {"key": "lo", "label": "최근 하한", "role": "compare"}, {"key": "hi", "label": "최근 상한"},
                                      {"key": "b", "label": "전년 하한", "role": "base"}, {"key": "d", "label": "절대 증가(하한)", "role": "abs_change"},
                                      {"key": "p", "label": "전년비(하한)", "role": "pct_change"}, {"key": "i", "label": "과대 지수(가중)"}],
                          "rows": ent_rows,
                          "meta": dict(meta_common, method="대표 검색어만 · 하한 = 표기변형 그룹 최댓값의 합 · 상한 = 같은 검색을 뺀 합 · "
                                       "순위는 하한 절대 증가 · 세그먼트 기저 미달 대상은 순위 없음",
                                       limits="대상 묶음은 불용어 규칙이다 — 이름은 [추정]")})

    # ── 요약 · 실행
    blocks_sum = []
    if surge:
        t = surge[0]
        obs = (f"{label} 세그먼트 검색량 {facts_top['prev']} → {facts_top['cur']} (절대 증가 {facts_top['abs']} · 전년비 {facts_top['yoy']})"
               + (f" · 과대 지수 {facts_top['idx']}" if "idx" in facts_top else ""))
        if f_ty:
            obs += f" · 전년비 1위는 {ty['keyword']}({f_ty}) — 순위 기준이 다르다"
        promoted_note = (" · 이 검색어는 범위 미확인이었다가 --include 로 승격됐다(사람 판단)"
                         if str(t.get("scope", "")).startswith("승격") else "")
        blocks_sum.append({"type": "finding", "id": "D1", "evidence": F.ids(*facts_top.values(), f_ty),
                           "observed": obs,
                           "interpretation": f"{t['keyword']}은 {label}에서 절대 증가가 가장 크다 — 다음 분기 후보 [추정]",
                           "alternatives": "세그먼트 비중이 과거에도 지금과 같았다는 전제다 — 비중이 바뀌었다면 세그먼트 증가는 다르다 · "
                                           "이슈·계절 행사로 한 해만 튄 값일 수 있다" + promoted_note,
                           "limits": "성·연령 비율은 현재 시점 한 값 · 월 그레인 · 검색은 구매가 아니다",
                           "actions": ["A1"]})
    else:
        f_j = F.add(C["judged"], "개", "[실제 데이터]", src, "게이트를 넘어 판정한 검색어")
        blocks_sum.append({"type": "finding", "id": "D1", "evidence": F.ids(f_j, f_ming),
                           "observed": f"판정한 검색어 {f_j} 중 전년비 {f_ming} 이상인 것이 없다",
                           "interpretation": f"이번 후보 풀에서는 {label} 세그먼트의 뚜렷한 급등이 보이지 않는다 [추정]",
                           "alternatives": "후보 풀이 좁거나(절단 · 범위 밖 제외) 창이 비수기일 수 있다",
                           "limits": "후보 풀 밖은 보지 않았다", "actions": ["A1"]})
    counter = [f"세그먼트 전년비는 전체 전년비와 같다 — 성·연령 비율이 현재 시점 하나뿐이라 과거 창에도 같은 비중을 씌웠다"]
    if f_expl:
        counter.append("기저가 작아 전년비가 부푼 검색어는 순위에서 뺐다: "
                       + ", ".join(f"{r['keyword']} {fy}(전년 창 {fb})" for r, fy, fb in f_expl))
    if S.get("small_overindex_out"):
        counter.append("과대 지수는 크지만 볼륨이 작아 순위에서 뺀 검색어: " + ", ".join(S["small_overindex_out"]))
    if S.get("entities_small"):
        counter.append("세그먼트 기저가 작은 대상은 순위 문장에 쓰지 않고 표에만 둔다: " + ", ".join(S["entities_small"]))
    if held:
        counter.append(f"범위 미확인 후보 {len(held)}개(trend_finder 단독 · 시드·연관어와 겹침 없음)는 순위 문장에 쓰지 않고 별도 표에만 둔다"
                       + (": 기준을 넘었던 것 " + ", ".join(r["keyword"] for r in held if r.get("would_tabs"))
                          if any(r.get("would_tabs") for r in held) else ""))
    if S.get("notes"):
        counter.append("세그먼트 적용 메모는 부록 «수집 사실» 표에 있다")
    blocks_sum.append({"type": "paragraph", "html": "<b>반대 근거</b> — " + " · ".join(counter)})

    act_ev = F.ids(*(list(facts_top.values())[:2] or [f_rows]))
    tgt = surge[0]["keyword"] if surge else (over[0]["keyword"] if over else "후보 풀")
    action = {"type": "action", "id": "A1", "evidence": act_ev,
              "hypothesis": f"{label} 대상 콘텐츠·상품 기획에서 «{tgt}»를 먼저 다루면 다음 분기 관심을 먼저 잡는다",
              "deliverable": f"«{tgt}» 주제 콘텐츠 기획안 하나와 상품 구색 점검표",
              "owner_role": "콘텐츠 기획자 · MD",
              "internal_data": "해당 상품군 월 판매량 · 세그먼트별 회원 구매 비중(내부 데이터로 성·연령 비율을 확인)",
              "criteria": {"expand": "다음 기준월에도 세그먼트 절대 증가가 이어지고 내부 판매·문의가 함께 오르면 확대",
                           "revise": "검색은 오르는데 내부 구매 비중이 다른 세그먼트로 나오면 대상 세그먼트를 고친다",
                           "stop": "다음 기준월에 전년비가 급등 기준 아래로 내려가면 중단"}}

    # ── 범위
    tf_calls = [c for c in L.get("calls", []) if c.get("tool") == "trend_finder"]
    if_calls = [c for c in L.get("calls", []) if c.get("tool") == "intent_finder"]
    scr = L.get("screen") or {}
    selection = (f"시드 주변 trend_finder 이슈 · intent_finder 연관 검색어 · 시드 자체를 후보로 모아 ads_metrics 스크린 후 all 대상만 조회 "
                 f"(trend_finder 호출 {len(tf_calls)}건 · intent_finder 호출 {len(if_calls)}건"
                 + (f" · 절단 {sum(1 for c in L['calls'] if c.get('truncated'))}건" if any(c.get("truncated") for c in L["calls"]) else "")
                 + ")")
    missing = []
    if S.get("no_demography"):
        missing.append("성·연령 미반환 [데이터 공백]: " + ", ".join(S["no_demography"]))
    if S.get("not_returned"):
        missing.append("요청했지만 응답 없음: " + ", ".join(S["not_returned"]))
    missing.append(f"비교 창 결측 월로 계산 불가 {C['uncomputable']}개 — 0으로 바꾸지 않음")
    excluded = [f"게이트 미달 {C['gated']}개 · 세그먼트 기저 미달 {C['seg_gated']}개 · 같은 검색의 중복 {C['dup']}개 · 표기변형 {C['variant']}개"]
    if L.get("exclude"):
        excluded.append(f"범위 밖 정규식 «{L['exclude']}»")
    if held:
        excluded.append(f"범위 미확인 순위 보류 {len(held)}개")
    inc_all = [x for x in ((L.get("scope") or {}).get("include"), S.get("include")) if x]
    prom = list(dict.fromkeys(((L.get("scope") or {}).get("promoted") or []) + (S.get("scope_promoted") or [])))
    if prom:
        excluded.append(f"--include «{' · '.join(inc_all)}»로 승격 {len(prom)}개: {', '.join(prom)}")
    if scr:
        excluded.append(scr.get("summary", ""))
    lab_ref = "마지막 달 포함(잠정)" if S["include_last_month"] else "마지막 달 제외"
    boundary = (f"{win['prev']} 대비 {win['cur']} 검색량과 현재 시점 성·연령 비율을 관측했고, 세그먼트별 월 추이 · {ref} 이후 · 구매는 관측하지 못했다"
                if seg["applied"] else
                f"{win['prev']} 대비 {win['cur']} 전체 검색량만 관측했다 — gl={gl}에는 성·연령 비율이 없어 세그먼트 순위는 내지 않았다")
    report = {
        "meta": {"title": f"{S['category']} — {label} 세그먼트 급등 · 신규 · 과대 지수",
                 "subtitle": (f"{a.subtitle_note} · " if a.subtitle_note else "") + f"데이터 기준월 {ref} · 조회일 {a.queried_at} · {lab_ref}", "skill": "spot-segment-risers",
                 "version": a.version},
        "boundary": boundary,
        "lede_html": (f"<b>{label}</b> 세그먼트에서 급등 {F.add(C['surge'], '개', '[추정]', src, '급등 탭 행 수')} · "
                      f"신규 {F.add(C['newcomer'], '개', '[추정]', src, '신규 탭 행 수')} · "
                      f"과대 지수 {F.add(C['overindex'], '개', '[추정]', src, '과대 지수 탭 행 수')} (응답 {f_rows} 중)"),
        "scope": {"collected_at": a.queried_at, "latest_month": ref, "gl": gl, "selection": selection,
                  "missing": " · ".join(missing), "excluded": " · ".join(x for x in excluded if x),
                  "windows": {"기준": win["prev"], "비교": win["cur"], "처리": lab_ref}},
        "facts": None,
        "sections": [
            {"kind": "summary", "title": (f"{label}에서 세그먼트 절대 증가 1위는 «{surge[0]['keyword']}»다" if surge
                                          else f"{label}에서 급등 기준을 넘는 검색어가 없다"), "blocks": blocks_sum},
            {"kind": "trend", "title": "급등과 신규 — 기저가 작은 급등은 따로 둔다", "blocks": trend_blocks},
            {"kind": "structure", "title": ("과대 지수와 대상 — 세그먼트 비중이 큰 곳" if seg["applied"]
                                            else "대상별 규모와 증가 — 세그먼트 없음(gl 사양)"), "blocks": struct_blocks},
            {"kind": "action", "title": "다음 분기 검증 과제", "blocks": [action]},
            {"kind": "appendix", "title": "계산 정의와 전체 수치", "blocks": [
                {"type": "paragraph", "html": f"기준값 [가정] — 전체 전년 창 게이트 {f_gate} · 세그먼트 게이트 {f_sgate} · 급등 최소 전년비 {f_ming} · "
                                              f"과대 지수 최소 {f_mini}" + (f" · 과대 지수 기준 비중 {f_base} [추정]" if f_base else "")},
                {"type": "paragraph", "html": "세그먼트 비중 = 성별 비율 × 연령 비율(둘 다 고르면 독립 가정의 곱 [추정]) · "
                                              "세그먼트 검색량 = 전체 월 검색량 합 × 현재 비중 [추정] · 과대 지수 = 비중 ÷ 후보 풀 볼륨 가중 평균 비중 · "
                                              "전년비는 마지막 달을 뺀 3개월 창과 전년 같은 창의 비교(volume_trend 는 쓰지 않는다) · "
                                              "대상 이름은 불용어 규칙 묶음이다 [추정]"},
                {"type": "table", "caption": "수집 사실 — 원장 · 세그먼트 메모 원문",
                 "columns": [{"key": "k", "label": "출처"}, {"key": "n", "label": "내용"}],
                 "rows": [{"k": "원장", "n": str(n)} for n in L.get("notes", [])] + [{"k": "세그먼트", "n": str(n)} for n in S.get("notes", [])],
                 "meta": dict(meta_common, method="segment_candidates.py · segment_risers.py 출력 그대로", unit="문장")},
                {"type": "details", "summary": "검색어 전체 판정", "blocks": [{
                    "type": "table", "caption": "검색어별 판정 상태",
                    "columns": [{"key": "q", "label": "검색어"}, {"key": "s", "label": "판정"}, {"key": "d", "label": "성·연령"},
                                {"key": "v", "label": "최근 창"}, {"key": "b", "label": "전년 창"}, {"key": "t", "label": "탭"},
                                {"key": "sc", "label": "범위"}],
                    "rows": [{"q": r["keyword"], "s": r["gate_status"], "d": r["demography_status"], "v": num(r["volume_window"]),
                              "b": num(r["volume_prev_year_window"]), "t": r["tabs"], "sc": r.get("scope", "")} for r in K],
                    "meta": dict(meta_common, method="segment_risers.py 판정 상태 그대로", unit="건(3개월 합 · 전체)")}]}]},
        ],
        "provenance": {"calls": calls, "currency": "크레딧"},
    }
    report["facts"] = F.d
    pathlib.Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {a.out} · 사실 {len(F.d)}개 · 호출 {len(calls)}건 (cost 합 {sum(c.get('cost') or 0 for c in calls)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
