#!/usr/bin/env python3
"""trend_alert_{category}.md 작성 — 스크립트 출력(CSV·issue_meta.json)에서만 값을 옮긴다(수기 수치 없음). SKILL.md STEP 8.

    python3 write_alert.py trend_signals.csv --category "KR 선스틱" --queried-at 2026-09-28 \\
        --gate 3000 [--include-last-month] [--note "가정: …"] -o trend_alert_sunstick.md \\
        [--issue-only trend_signals_issue_only.csv --issue-meta issue_meta.json]

절 구성 — ① 헤더(기준월·조회일·지연·게이트·등급 기준·작은 풀 경고·확산 폭 강도) ② 수집 사실(issue_meta: 호출·넓힘·
limit 절단·띄어쓴 시드·크레딧 대조·범위 밖·스크린·병합) ③ 판정 행 전체(등급 유무와 관계없이 전부 · 수치 병기)
④ 신생·저기저 급등 후보(기저가 게이트 미만이거나 전년 0 — 측정된 현재·전년 볼륨, 성장률은 참고) ⑤ 이슈 신호만 ·
검색량 미확인(사유별 — 스크린 탈락은 volume_avg 병기) ⑥ 라벨·한계.

신호 CSV에 이슈 근거(issue_*)가 붙어 있는데 --issue-only·--issue-meta가 없으면 멈춘다(미판정 후보·수집 사실이 조용히 빠지는 것을 막는다).
기준월은 CSV의 data_ref_month(= 응답 monthly_volume 최신 월)에서 읽고, 지연은 조회일과의 차로 계산한다.
«잠정»은 trend_signals.py가 두 모드(마지막 달 제외·포함)를 비교해 붙인 표시를 그대로 옮긴다.
"""
import argparse, csv, json, sys

SMALL_POOL_ABS, SMALL_POOL_SHARE = 20, 0.25   # trend_signals.py와 같은 [가정]
WEAK_SPREAD = 50                               # [가정] 후보가 이보다 적으면 확산 폭은 약한 근거


def lag(ref, queried):
    return (int(queried[:4]) - int(ref[:4])) * 12 + int(queried[5:7]) - int(ref[5:7])


def num(v, fmt="{:,}"):
    try:
        return fmt.format(int(float(v)))
    except (TypeError, ValueError):
        return "—"


def pct(v):
    return f"{float(v):+.0%}" if v not in ("", None) else "—"


def pp(r):
    return f"{float(r['accel']) * 100:+.0f}" if r["accel"] else f"— ({r['accel_note'] or '계산 불가'})"


def issue_cell(r):
    return f"{r['issue_score']} · {r['issue_n_dates']}일" if r.get("issue_score") else "—"


def meta_lines(meta):
    """수집 사실 — issue_candidates.py가 기록한 값만 옮긴다(P2)."""
    L = ["", "## 수집 사실 (trend_finder · 스크린)", ""]
    for c in meta.get("calls", []):
        L.append(f"- «{c['seed']}» {c['period']}일: 반환 {c['returned']}건 · 범위 안 {c['in_scope']}건 · 매칭 total "
                 f"{c['total'] if c['total'] is not None else '—'} · limit {c['limit']} · cost_detail {c['cost']} [실제 데이터]")
    for n in meta.get("notes", []):
        if "반환" in n and "범위 안" in n and "절단" not in n:
            continue          # 호출 줄과 같은 내용
        L.append(f"- {n}")
    L.append(f"- {meta['credit']['line']}")
    ex = meta.get("excluded") or []
    if ex:
        L.append(f"- 범위 밖 제외 {len(ex)}개 (STEP 2 · --exclude — keyword_info로 보내지 않음): "
                 + ", ".join(ex[:12]) + (f" 외 {len(ex) - 12}개" if len(ex) > 12 else ""))
    if meta.get("seed_candidates"):
        L.append(f"- 시드 자체를 후보에 포함: {', '.join(meta['seed_candidates'])} (--no-seed로 뺄 수 있다)")
    sc = meta.get("screen")
    if sc:
        L.append(f"- {sc['summary']} · ads_metrics cost_detail {sc.get('ads_metrics_cost', '—')} · "
                 f"all 결정식 {sc['all_credits_formula']}")
        L += [f"  - {m}" for m in sc.get("merges", [])]
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--category", required=True)
    ap.add_argument("--queried-at", required=True, help="조회일 YYYY-MM-DD")
    ap.add_argument("--gate", type=int, default=3000, help="trend_signals.py에 준 --gate 값")
    ap.add_argument("--include-last-month", action="store_true", help="trend_signals.py를 이 옵션으로 돌렸으면 함께 준다")
    ap.add_argument("--note", default="", help="가정·범위 제외 등 한 줄")
    ap.add_argument("--issue-only", help="trend_signals.py --evidence가 쓴 «이슈 신호만 · 검색량 미확인» CSV")
    ap.add_argument("--issue-meta", help="issue_candidates.py가 쓴 issue_meta.json — 수집 사실 절")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    R = list(csv.DictReader(open(a.csv, encoding="utf-8-sig")))
    if not R:
        sys.exit("CSV가 비었다")
    ref, end = R[0]["data_ref_month"], R[0]["window_end"]
    if a.include_last_month != (ref == end):
        sys.exit(f"--include-last-month 표시가 CSV와 어긋난다 (기준월 {ref} · 창 종료월 {end})")
    has_issue = any(r.get("issue_score") for r in R)
    if has_issue and not (a.issue_only and a.issue_meta):
        sys.exit("신호 CSV에 이슈 근거가 있다 — --issue-only <..._issue_only.csv>와 --issue-meta <issue_meta.json>을 함께 줘야 "
                 "미판정 이슈 후보와 수집 사실(절단·넓힘·크레딧)이 빠지지 않는다")
    meta = json.load(open(a.issue_meta, encoding="utf-8")) if a.issue_meta else None
    n_gate = sum(1 for r in R if r["gated_out"] == "게이트 미달")
    n_unc = sum(1 for r in R if r["gated_out"] == "계산 불가")
    n_dup = sum(1 for r in R if r["gated_out"] == "같은 검색의 중복")
    live = [r for r in R if not r["gated_out"]]
    groups = {}
    for r in R:
        if not r["dup_of"]:
            groups.setdefault(r["canonical"], []).append(r)
    multi = {c: rs for c, rs in groups.items() if len(rs) > 1}
    graded = [r for r in R if r["grade"]]
    basis = next((r.get("grade_basis") for r in live if r.get("grade_basis")), "")
    mode = "마지막 달 포함(요청) — 미완결 가능성 있음" if a.include_last_month else "마지막 달 제외(기본)"
    n_cand = len(meta["screen"]["rows"]) if meta and meta.get("screen") else len(R)
    n_diff = sum(1 for r in live if r["diffusion"] != "")

    L = [f"# Trend Alert — {a.category}", "",
         f"데이터 기준월 **{ref}** / 조회일 **{a.queried_at}** / 지연 **{lag(ref, a.queried_at)}개월** [실제 데이터]", "",
         f"- 계산 창 종료월 {end} — {mode}",
         f"- 기저 게이트 전년 동기 창 ≥ {a.gate:,}건 (가속도의 직전 창에도 적용) [가정] · 등급 절대 하한 성장률 > 0 [가정]",
         "- 등급 기준: " + (f"**{basis}** — 백분위 대신 절대 기준 [가정]: S 성장률 ≥ 50%·지속 ≥ 6개월·가속도 ≥ 0 / "
                          "A ≥ 30%·≥ 3개월·가속도 > −20%p(또는 계산 불가) / B ≥ 15%·≥ 2개월"
                          if basis.startswith("절대") else "백분위 점수 S≥0.85 A≥0.70 B≥0.55 [가정]"),
         f"- 행 {len(R)} · 판정 {len(live)} · 게이트 미달 {n_gate} · 계산 불가 {n_unc} · 같은 검색의 중복 {n_dup} [데이터 공백·추정]",
         f"- canonical {len(groups)}그룹 · 표기변형 묶음: "
         + (", ".join(f"{' / '.join(r['keyword'] for r in rs)} (전년 동기 창 하한 {int(rs[0]['group_prev_year_lo']):,} ~ "
                      f"상한 {int(rs[0]['group_prev_year_hi']):,}건 [추정])" for rs in multi.values()) or "없음"),
         f"- 두 모드 비교: 등급 보유 {sum(1 for r in R if r['grade'] or r['grade_other_mode'])}행 중 "
         f"«잠정» {sum(1 for r in R if r['provisional'])}행 (등급·성장률 부호가 모드에 따라 달라짐)"]
    if len(live) < max(SMALL_POOL_ABS, SMALL_POOL_SHARE * len(R)):
        L.append(f"- ⚠️ 판정 풀이 작다: {len(live)}/{len(R)}행 [가정 기준 {SMALL_POOL_ABS}행 또는 {SMALL_POOL_SHARE:.0%}] — "
                 "등급은 이 몇 행 안의 판단이다. 비수기 계절 품목이면 trend_signals.py --window 12로 다시 계산해 본다(0크레딧)")
    if n_cand < WEAK_SPREAD or n_diff < len(live):
        L.append(f"- 확산 폭은 약한 근거: 후보 {n_cand}개(기준 {WEAK_SPREAD}개 [가정]) · 확산 계산된 판정 행 {n_diff}/{len(live)}"
                 " — 확산은 후보군 안에서만 잰다")
    if a.note:
        L.append(f"- {a.note}")
    if meta:
        L += meta_lines(meta)
    L += write_tables(a, R, live, graded)
    open(a.out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"→ {a.out} (판정 {len(live)}행 · 등급 {len(graded)}행 · 잠정 {sum(1 for r in R if r['provisional'])}행)")


GRADE_ORDER = {"S": 0, "A": 1, "B": 2}


def is_fresh(r):
    """신생·저기저 — 기저가 게이트 미만이거나 전년 동기 창이 0인데, 최근 창 볼륨은 측정됐다."""
    if r["dup_of"] or not r.get("volume_window") or float(r["volume_window"]) <= 0:
        return ""
    if r["gated_out"] == "게이트 미달":
        return "기저 게이트 미달"
    if r["gated_out"] == "계산 불가" and float(r["volume_prev_year_window"] or 0) == 0:
        return "전년 동기 0 — 신생" + (f" (결측 {r['missing_in_window']}개월 포함)" if r["missing_in_window"] not in ("", "0") else "")
    return ""


def write_tables(a, R, live, graded):
    L = ["", f"## 판정 행 전체 ({len(live)}행 — 등급 유무와 관계없이)", "",
         "| 등급 [추정] | 키워드 | 성장률 YoY | 가속도(%p) | 지속(개월) | 확산 | 최근 창 | 전년 동기 창 | 이슈 점수 · 인덱스일 수 | 비고 |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(live, key=lambda r: (GRADE_ORDER.get(r["grade"], 9), -float(r["growth"] or 0))):
        note = " · ".join(x for x in (r["grade_floor"], "" if r["grade"] or r["grade_floor"] else "기준 미달",
                                      r["provisional"], r.get("grade_basis", "")) if x)
        L.append(f"| {r['grade'] or '—'} | {r['keyword']} | {pct(r['growth'])} | {pp(r)} | {r['persistence_months']} | "
                 f"{r['diffusion'] or '—'} | {num(r.get('volume_window'))} | {num(r['volume_prev_year_window'])} | "
                 f"{issue_cell(r)} | {note} |")
    if not live:
        L.append("| — | 판정 행 없음 | | | | | | | | |")
    if not graded:
        L += ["", "등급을 받은 신호 없음 — 판정 행의 수치는 위 표 그대로다."]
    ev = [r for r in graded if r.get("issue_score")]
    if ev:
        L += ["", "**등급 신호의 이슈 근거** (trend_finder · 구글 기반 인덱스 — 이슈는 검색량이 아니다)"]
        for r in ev:
            L.append(f"- {r['keyword']}: 요약 {r['issue_summary_ai'] or '—'} [추정 — AI 생성 요약]"
                     + (f" · 출처 확인 문장: {r['issue_source_checked']} [실제 데이터]" if r.get("issue_source_checked") else "")
                     + (f" · 출처 {r['issue_sources'].split(' | ')[0]}" if r.get("issue_sources") else ""))

    fresh = [(r, is_fresh(r)) for r in R if is_fresh(r)]
    L += ["", f"## 신생·저기저 급등 후보 ({len(fresh)}건 — 기저가 게이트 미만 · 성장률은 참고)", "",
          f"검색량은 **측정됐다** [실제 데이터]. 다만 전년 동기 창이 게이트({a.gate:,}건) 미만이거나 0이라 성장률이 기저 효과로 "
          "부풀고, 그래서 **등급을 매기지 않는다**. «신생 키워드 감지»는 이 절이 답한다 — 크기(최근 창·최신 월)와 검색 시작 월을 본다.", "",
          "| 키워드 | 구분 [데이터 공백] | 최근 창 | 전년 동기 창 | 최신 월 볼륨 | 검색 시작 월 | 성장률 (참고) | 이슈 점수 · 인덱스일 수 |",
          "|---|---|---:|---:|---:|---|---:|---:|"]
    for r, why in sorted(fresh, key=lambda x: -float(x[0]["volume_window"])):
        L.append(f"| {r['keyword']} | {why} | {num(r['volume_window'])} | {num(r['volume_prev_year_window'])} | "
                 f"{num(r.get('volume_latest'))} ({r['data_ref_month']}) | {r.get('first_active_month') or '—'} | "
                 f"{pct(r['growth']) if r['growth'] else '정의 불가'} | {issue_cell(r)} |")
    if not fresh:
        L.append("| — | 없음 | | | | | | |")
    if a.issue_only:
        L += issue_only_section(a, R, {r["canonical"] for r, _ in fresh})
    L += ["",
          "라벨: 성장률·가속도·지속성·확산·볼륨 = [실제 데이터]에서 직접 계산 · 점수·등급·표기변형 그룹 볼륨·병합 = [추정] · "
          "게이트·하한·절대 기준·마지막 달 처리 = [가정] · 미달·결측·미반환 = [데이터 공백] · 이슈 요약(AI 생성) = [추정] · "
          "출처로 확인한 문장 = [실제 데이터]",
          "한계: 월 그레인·기준월은 응답에서 읽음(«몇 개월 빠른 신호»는 지연을 빼고 말한다) · 원인은 이슈 요약·출처로 가설만 제시(인과 단정 금지) · "
          "이슈 인덱스는 구글 기반(네이버 비중이 큰 시장에서 «국내 이슈 전체» 아님)이고 이슈는 검색량이 아님 · "
          "확산 폭은 후보군 내부 · 검색은 관심 신호이지 구매가 아님 · volume_trend 필드는 판정에 쓰지 않음"]
    return L


def issue_only_section(a, R, fresh_canon):
    O = list(csv.DictReader(open(a.issue_only, encoding="utf-8-sig")))
    ex = [o for o in O if o["reason"] == "범위 밖 제외"]
    moved = [o for o in O if o["reason"] != "범위 밖 제외" and o["canonical"] in fresh_canon]
    T = [o for o in O if o not in ex and o not in moved]
    by = {}
    for o in T:
        key = o["reason"].split(" → ")[0]
        by[key] = by.get(key, 0) + 1
    ref = R[0]["data_ref_month"]
    n_after = sum(1 for o in T if o["after_ref_month"])
    L = ["", f"## 이슈 신호만 · 검색량 미확인 ({len(T)}건)", "",
         "trend_finder가 잡았지만 검색량(keyword_info)으로 판정하지 못한 후보다. **등급을 매기지 않는다.** «미확인»은 «안 뜬다»가 아니라 "
         "«아직 모른다»다. 인덱스는 구글 기반이라 «국내 이슈 전체»가 아니다.", "",
         "- 사유별: " + (" · ".join(f"{k} {v}" for k, v in by.items()) or "없음")
         + (f" · 신생·저기저 절로 옮김 {len(moved)}" if moved else "")
         + (f" · 범위 밖 제외 {len(ex)}(아래 표에 없음)" if ex else "") + " [데이터 공백]",
         f"- «기준월 이후» = 마지막 인덱스일이 데이터 기준월 {ref}보다 늦다 — 그 이슈 기간의 검색량은 아직 월간 집계에 없다. "
         f"조회 기간이 짧을수록(14일) 거의 모든 이슈가 «예»다(여기서 {n_after}/{len(T)}) — 재판정 날짜가 아니다. 이슈 문자열이 검색어가 아닐 수 있어(사건명·인물명 등) "
         "다음 기준월에 판정된다는 보장은 없다",
         *(["- «스크린 탈락»은 볼륨이 **측정됐다**(volume_avg = 최신 3개월 평균 · ads_metrics) — 스크린 기준 미만이라 "
            "시계열(all)을 사지 않았을 뿐이다. «검색량 미반환»은 응답에 행이 없었다"] if by.get("스크린 탈락") else []),
         "", "| 키워드 | 미확인 사유 [데이터 공백] | volume_avg (스크린) | 이슈 점수 | 인덱스일 수 | 최근 인덱스일 | 기준월 이후 | 요약 [추정] | 출처 확인 문장 [실제 데이터] |",
         "|---|---|---:|---:|---:|---|---|---|---|"]
    for o in T:
        L.append(f"| {o['keyword']} | {o['reason']} | {num(o.get('volume_avg'))} | {o['issue_score'] or '— (이슈 근거 없음)'} | "
                 f"{o['issue_n_dates'] or '—'} | {o['index_date_last'] or '—'} | {o['after_ref_month'] or '—'} | "
                 f"{o['summary_ai'] or '—'} | {o['source_checked'] or '— (미확인)'} |")
    if not T:
        L.append("| — | 없음 | | | | | | | |")
    return L


if __name__ == "__main__":
    main()
