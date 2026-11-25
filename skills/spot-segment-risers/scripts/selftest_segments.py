#!/usr/bin/env python3
"""이 스킬 고유 회귀 검사 — 합성 픽스처(assets/fixtures) · API 호출 없음.

  G1 수집 — 빈 issue_list 만 있으면 exit 3(90일로 넓혀 재조회) · 이슈를 지어내지 않는다
  G2 수집 — 시드 표기 우선 · 범위 밖 제외 · issue_list 절단(total > 반환 = limit) · intent_finder 절단(반환 = limit) (T7)
            · 크레딧 결정식 50 × 결과 있는 호출 + 2 × 반환 = cost_detail 합 · 빈 결과 호출 0 · 원장 기록
  G3 스크린 — volume_avg × 3 ≥ gate 만 all 대상 · 미반환은 [데이터 공백]으로 남김
  G4 kr 세그먼트 — 기저 폭발(글로우 쿠션 +9900%)은 급등 순위에서 빠짐(T4) · 세그먼트 기저 미달 · 신규(과거 창 결측)
            · 같은 검색의 중복(T5) · demography 미반환은 순위에서 빼고 [데이터 공백] · 비교 창 결측 월은 계산 불가(T6)
            · 마지막 달 급락은 «잠정»(T2) · 작은 볼륨 과대 지수는 순위에서 빠짐(T4) · 작은 대상은 헤드라인에서 빠짐
  G5 jp — demography 사양상 없음 → 세그먼트 미적용 · 과대 지수 없음 · 그 사실을 notes 에 적음
  G6 demography 파서 — 퍼센트 · 비율(0~1) · 중첩형 · 알 수 없는 모양은 «형식 확인 필요»
  G7 report.json 조립 → 공통 렌더러 통과(kr · jp) · assets/report.example.json 렌더 통과
  G8 SKILL.md 산출물 컬럼 목록 = 실제 출력
  G9 범위 점검(v0.1.1 P0) — trend_finder 단독 후보: 시드어 포함 · 연관어 토큰 공유는 «확인», 겹침 없는 인명 이슈는
     «범위 미확인»으로 급등 1위 기준을 넘어도 순위 · 헤드라인(D1 · A1 · 대상 순위)에서 빠지고 표에는 남는다 ·
     --include(collect · segment_risers 둘 다)로 승격하면 순위에 들고 원장 · summary · 리포트에 남는다 ·
     이슈 첫 인덱스가 창 끝보다 뒤면 «창 이후 이슈 — 원인 근거 아님»(P1)

    python3 scripts/selftest_segments.py        # 스킬 폴더에서 실행
"""
import csv, json, os, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE.parent / "assets" / "fixtures"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
RULES = '{"썬":"선"}'
NOTE = "합성 픽스처 예시(assets/fixtures) — 실제 시장 값이 아니다"   # 견본 부제의 합성 표시
sys.path.insert(0, str(HERE))


def run(args, cwd):
    p = subprocess.run([sys.executable] + [str(x) for x in args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    return p.returncode, p.stdout + p.stderr


def read(p):
    with open(p, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        return rd.fieldnames, list(rd)


def doc_columns(block):
    lines = (HERE.parent / "SKILL.md").read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == block:
            cols = []
            for nxt in lines[i + 1:]:
                if not re.match(r"^\s+[a-z_]", nxt):
                    break
                cols += [c.strip() for c in nxt.split(",") if c.strip()]
            return cols
    return None


def g9_fixtures(t):
    """가상 볼륨 · 가상 인명(«한도윤»)으로 만든 합성 응답. 기준월 2026-09 · 창 2026-06~08 vs 2025-06~08."""
    issues = [("가방 스트랩 교체", 60.0, "2026-07-15"), ("레더 토트", 55.0, "2026-07-20"), ("한도윤", 90.0, "2026-09-10")]
    tf = {"result": "OK", "request_at": "2026-10-15T00:00:00Z", "total": 3,
          "request_detail": {"keyword": "가방", "gl": "kr", "period_days": 14, "limit": 10},
          "cost_detail": {"total_cost": 56},
          "data": [{"keyword": k, "issue_score": sc, "index_date": d, "summary": [f"{k} 관련 합성 요약"],
                    "source_links": ["https://example.com/a"]} for k, sc, d in issues]}
    ifd = {"result": "OK", "request_at": "2026-10-15T00:00:00Z", "cost_detail": {"total_cost": 34},
           "request_detail": {"keywords": ["가방"], "gl": "kr", "limit": 20}, "data": ["레더 백팩", "가방 추천"]}
    months = []
    y, m = 2022, 10
    for _ in range(48):
        months.append(f"{y}-{m:02d}"); m += 1
        if m == 13:
            y, m = y + 1, 1

    def row(kw, base, cur, f=50, ma=40):
        demo = {"f_gender_ratio": 100 - f, "m_gender_ratio": f, "a0_ratio": 0, "a13_ratio": 10, "a20_ratio": 15,
                "a25_ratio": 15, "a30_ratio": 10, "a40_ratio": 50 - ma, "a50_ratio": ma}
        mv = [{"month": mo, "total": cur if mo >= "2026-01" else base} for mo in months]
        return {"keyword": kw, "monthly_volume": mv, "demography": demo}
    data = [row("가방", 5000, 5000), row("가방 스트랩 교체", 2000, 4000), row("레더 토트", 3000, 3300),
            row("한도윤", 3000, 30000), row("레더 백팩", 4000, 4100), row("가방 추천", 3500, 3500)]
    kw = {"result": "OK", "request_at": "2026-10-15T00:00:00Z", "cost_detail": {"total_cost": 60},
          "request_detail": {"keywords": [d["keyword"] for d in data], "gl": "kr", "data_type": "all"}, "data": data}
    for name, o in (("g9_tf.json", tf), ("g9_if.json", ifd), ("g9_kw.json", kw)):
        (t / name).write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")


def g9(t, SC, SR, BR, RR, F):
    g9_fixtures(t)
    seg = ["--gl", "kr", "--gender", "m", "--age", "a50", "--category", "g9"]
    # ① 기본 — 승격 없음
    rc, out = run([SC, "collect", "--tf", "g9_tf.json", "--intent", "g9_if.json", "-o", "g9_c.json",
                   "--sources-out", "g9_s.csv", "--ledger", "g9_led.json"], t)
    if rc != 0:
        F.append(("G9", f"범위 점검 수집 exit {rc}", out[-300:])); return
    _, srows = read(t / "g9_s.csv")
    sc = {r["keyword"]: r["scope"] for r in srows}
    if not sc.get("가방 스트랩 교체", "").startswith("확인 — 시드"):
        F.append(("G9", "시드어를 품은 이슈 키워드가 범위 확인되지 않았다", sc.get("가방 스트랩 교체")))
    if "연관어 토큰" not in sc.get("레더 토트", ""):
        F.append(("G9", "연관어와 토큰을 공유하는 이슈 키워드가 범위 확인되지 않았다", sc.get("레더 토트")))
    if not sc.get("한도윤", "").startswith("범위 미확인"):
        F.append(("G9", "시드·연관어와 겹침 없는 인명 이슈가 «범위 미확인»이 아니다", sc.get("한도윤")))
    led = json.loads((t / "g9_led.json").read_text(encoding="utf-8"))
    if (led.get("scope") or {}).get("held") != ["한도윤"]:
        F.append(("G9", "원장 scope.held 에 범위 미확인 후보가 남지 않았다", str(led.get("scope"))[:200]))
    if "한도윤" not in json.loads((t / "g9_c.json").read_text(encoding="utf-8")):
        F.append(("G9", "범위 미확인 후보가 keyword_info 후보에서 사라졌다(표에 남아야 한다)", ""))
    run([SC, "ledger", "g9_kw.json", "--ledger", "g9_led.json"], t)
    rc, out = run([SR, "g9_kw.json"] + seg + ["--sources", "g9_s.csv", "-d", "g9o"], t)
    if rc != 0:
        F.append(("G9", f"segment_risers exit {rc}", out[-300:])); return
    _, rows = read(t / "g9o" / "segment_keywords_g9.csv")
    _, erows = read(t / "g9o" / "segment_entities_g9.csv")
    S = json.loads((t / "g9o" / "segment_summary_g9.json").read_text(encoding="utf-8"))
    R, E = {r["keyword"]: r for r in rows}, {r["entity"]: r for r in erows}
    h = R["한도윤"]
    if h["rank_surge"] or h["rank_overindex"] or not h["tabs"].startswith("범위 미확인"):
        F.append(("G9", "범위 미확인 인명 이슈가 순위에 들어갔거나 표에서 보류 표시가 없다", str(h)[:250]))
    if R["가방 스트랩 교체"]["rank_surge"] != "1":
        F.append(("G9", "범위 안 이슈 키워드가 급등 1위가 아니다", R["가방 스트랩 교체"]["rank_surge"]))
    held = {x["keyword"]: x for x in S.get("scope_held", [])}
    if "급등" not in (held.get("한도윤") or {}).get("would_tabs", []):
        F.append(("G9", "summary.scope_held 에 «급등 기준 충족이지만 보류»가 남지 않았다", str(S.get("scope_held"))[:200]))
    if E["한도윤"]["headline_ok"] != "아니오" or E["한도윤"]["rank_entity"] or "한도윤" in S["entities_small"]:
        F.append(("G9", "범위 미확인 대상이 헤드라인 순위에 들어갔거나 «기저 미달»로 잘못 분류됐다", str(E["한도윤"])[:200]))
    if not h["issue_timing"].startswith("창 이후") or not R["가방 스트랩 교체"]["issue_timing"].startswith("창 안"):
        F.append(("G9", "창 끝보다 뒤 이슈를 «원인 근거 아님»으로 가르지 않았다(P1)",
                  f"{h['issue_timing']} / {R['가방 스트랩 교체']['issue_timing']}"))
    if R["가방 스트랩 교체"]["over_index"] == "" or S["pool_size"] != 5:
        F.append(("G9", "범위 미확인 행이 과대 지수 기준 풀에 들어갔다", f"pool {S['pool_size']}"))
    rc, out = run([BR, "--summary", "g9o/segment_summary_g9.json", "--ledger", "g9_led.json", "--queried-at", "2026-10-15",
                   "-o", "g9_r.json"], t)
    if rc != 0:
        F.append(("G9", f"build_report exit {rc}", out[-300:])); return
    rj = json.loads((t / "g9_r.json").read_text(encoding="utf-8"))
    d1 = next(b for b in rj["sections"][0]["blocks"] if b.get("id") == "D1")
    a1 = next(b for s_ in rj["sections"] for b in s_["blocks"] if b.get("id") == "A1")
    if "한도윤" in rj["sections"][0]["title"] or "한도윤" in d1["interpretation"] or "한도윤" in a1["hypothesis"]:
        F.append(("G9", "범위 미확인 인명이 헤드라인 · D1 · A1 주어가 됐다(P0 재발)", rj["sections"][0]["title"]))
    if "범위 미확인" not in json.dumps(rj, ensure_ascii=False):
        F.append(("G9", "리포트에 범위 미확인 보류 표 · 반대 근거가 없다", ""))
    rc, out = run([RR, "g9_r.json", "-o", "g9_r.html"], t)
    if rc != 0:
        F.append(("G9", "범위 보류 표가 든 리포트가 렌더 계약을 어겼다", out[-300:]))
    # ② collect --include 로 승격 → 순위 1위 · 원장 · 리포트에 기록
    run([SC, "collect", "--tf", "g9_tf.json", "--intent", "g9_if.json", "-o", "g9_c2.json", "--sources-out", "g9_s2.csv",
         "--ledger", "g9_led2.json", "--include", "한도윤"], t)
    _, srows2 = read(t / "g9_s2.csv")
    led2 = json.loads((t / "g9_led2.json").read_text(encoding="utf-8"))
    if not {r["keyword"]: r["scope"] for r in srows2}.get("한도윤", "").startswith("승격")             or (led2.get("scope") or {}).get("promoted") != ["한도윤"] or (led2.get("scope") or {}).get("include") != "한도윤":
        F.append(("G9", "--include 승격이 근거 CSV · 원장에 기록되지 않았다", str(led2.get("scope"))[:200]))
    run([SC, "ledger", "g9_kw.json", "--ledger", "g9_led2.json"], t)
    run([SR, "g9_kw.json"] + seg + ["--sources", "g9_s2.csv", "-d", "g9p"], t)
    _, prow = read(t / "g9p" / "segment_keywords_g9.csv")
    if {r["keyword"]: r for r in prow}["한도윤"]["rank_surge"] != "1":
        F.append(("G9", "collect --include 로 승격한 후보가 순위에 들지 않았다", ""))
    rc, out = run([BR, "--summary", "g9p/segment_summary_g9.json", "--ledger", "g9_led2.json", "--queried-at", "2026-10-15",
                   "-o", "g9_r2.json"], t)
    rj2 = json.loads((t / "g9_r2.json").read_text(encoding="utf-8")) if rc == 0 else {}
    if "승격" not in (rj2.get("scope") or {}).get("excluded", "") or "승격" not in json.dumps(rj2.get("sections"), ensure_ascii=False):
        F.append(("G9", "승격이 리포트 범위 · D1 반대 해석에 기록되지 않았다", out[-200:]))
    # ③ segment_risers --include(사후 승격 · 0크레딧) → summary.scope_promoted
    run([SR, "g9_kw.json"] + seg + ["--sources", "g9_s.csv", "--include", "한도윤", "-d", "g9q"], t)
    Sq = json.loads((t / "g9q" / "segment_summary_g9.json").read_text(encoding="utf-8"))
    if Sq.get("scope_promoted") != ["한도윤"] or not Sq["tabs"]["surge"] or Sq["tabs"]["surge"][0]["keyword"] != "한도윤":
        F.append(("G9", "segment_risers --include 사후 승격이 순위 · summary.scope_promoted 에 반영되지 않았다",
                  str(Sq.get("scope_promoted"))))


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    SC, SR, BR, RR = HERE / "segment_candidates.py", HERE / "segment_risers.py", HERE / "build_report.py", HERE / "render_report.py"
    F = []
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        for p in FIX.glob("*.json"):
            shutil.copy(p, t / p.name)

        # G1
        rc, out = run([SC, "collect", "--tf", "tf_sun_14d.json", "-o", "c0.json", "--sources-out", "s0.csv"], t)
        c0 = json.loads((t / "c0.json").read_text(encoding="utf-8")) if (t / "c0.json").is_file() else None
        if rc != 3 or "범위 안 이슈 0건" not in out:
            F.append(("G1", "빈 issue_list 에 넓힘(exit 3)을 요구하지 않았다", out[-300:]))
        if c0 != ["선크림"]:
            F.append(("G1", "빈 결과인데 시드 말고 후보가 생겼다(이슈를 지어냄)", str(c0)))

        # G2
        rc, out = run([SC, "collect", "--tf", "tf_sun_14d.json", "tf_sun_90d.json", "tf_cushion_14d.json",
                       "--intent", "if_sun.json", "if_cushion.json", "-o", "candidates.json",
                       "--sources-out", "candidate_sources.csv", "--ledger", "ledger.json", "--rules", RULES,
                       "--exclude", "광고 모델", "--scope-terms", "선스틱"], t)
        cands = json.loads((t / "candidates.json").read_text(encoding="utf-8"))
        if rc != 0:
            F.append(("G2", f"수집 exit {rc}", out[-300:]))
        if "선크림" not in cands or "썬크림" in cands:
            F.append(("G2", "시드 표기(선크림)가 후보 이름이 되지 않았다", str(cands[:4])))
        if any("광고 모델" in c for c in cands):
            F.append(("G2", "--exclude 범위 밖 이슈가 후보에 남았다", ""))
        if out.count("절단(T7)") < 2:
            F.append(("G2", "issue_list · intent_finder 절단 경고가 둘 다 나오지 않았다", out[-400:]))
        if "= 114 · cost_detail 합 114" not in out or "결정식과 다르다" in out:
            F.append(("G2", "trend_finder 크레딧 결정식(50 × 2 + 2 × 7 = 114)이 cost_detail 과 대조되지 않았다", out[-300:]))
        led = json.loads((t / "ledger.json").read_text(encoding="utf-8"))
        c1 = led["calls"][0]
        if len(led["calls"]) != 5 or c1["cost"] != 0 or not c1["status"].startswith("빈 결과"):
            F.append(("G2", "원장에 호출 5건 · 빈 결과 호출 0 크레딧이 기록되지 않았다", json.dumps(c1, ensure_ascii=False)[:200]))
        hdr, srows = read(t / "candidate_sources.csv")
        if hdr != doc_columns("candidate_sources.csv"):
            F.append(("G8", "SKILL.md candidate_sources.csv 컬럼 ≠ 실제 출력", f"{hdr} / {doc_columns('candidate_sources.csv')}"))
        if not any(r["excluded"] for r in srows):
            F.append(("G2", "범위 밖 후보가 근거 CSV 에서 사라졌다(남겨야 한다)", ""))

        # G3
        rc, out = run([SC, "screen", "ads_metrics_kr.json", "--candidates", "candidates.json", "--gate", "3000",
                       "-o", "all_candidates.json", "--ledger", "ledger.json", "--rules", RULES], t)
        sent = json.loads((t / "all_candidates.json").read_text(encoding="utf-8"))
        if "선크림 바르는 순서" in sent or "쿠션 퍼프 세척" in sent or "톤업 선크림" not in sent:
            F.append(("G3", "스크린이 volume_avg × 3 ≥ 3000 을 지키지 않았다", str(sent)))
        if "미반환 1 [데이터 공백]" not in out:
            F.append(("G3", "미반환 후보가 [데이터 공백]으로 집계되지 않았다", out[-300:]))
        run([SC, "ledger", "kw_all_kr.json", "--ledger", "ledger.json"], t)

        # G4
        rc, out = run([SR, "kw_all_kr.json", "--gl", "kr", "--gender", "f", "--age", "a20s", "--category", "kr",
                       "--sources", "candidate_sources.csv", "--rules", RULES, "--requested", "all_candidates.json", "-d", "out"], t)
        if rc != 0:
            F.append(("G4", f"segment_risers exit {rc}", out[-400:]))
        else:
            hdr, rows = read(t / "out" / "segment_keywords_kr.csv")
            ehdr, erows = read(t / "out" / "segment_entities_kr.csv")
            S = json.loads((t / "out" / "segment_summary_kr.json").read_text(encoding="utf-8"))
            R = {r["keyword"]: r for r in rows}
            E = {r["entity"]: r for r in erows}
            if hdr != doc_columns("segment_keywords_{category}.csv"):
                F.append(("G8", "SKILL.md segment_keywords 컬럼 ≠ 실제 출력", f"{hdr}"))
            if ehdr != doc_columns("segment_entities_{category}.csv"):
                F.append(("G8", "SKILL.md segment_entities 컬럼 ≠ 실제 출력", f"{ehdr}"))
            doc_s = doc_columns("segment_summary_{category}.json")
            if doc_s is None or [k for k in doc_s if k not in S] or [k for k in S if k not in doc_s]:
                F.append(("G8", "SKILL.md segment_summary 키 ≠ 실제 출력", f"{list(S)} / {doc_s}"))
            g = R["글로우 쿠션"]
            if g["rank_surge"] or not g["gate_status"].startswith("게이트 미달") or float(g["yoy"]) < 50:
                F.append(("G4", "기저 폭발(전년 창 60 → +9900%)이 급등 순위에 들어갔거나 게이트 사유가 없다(T4)", str(g)[:200]))
            if not any(x["keyword"] == "글로우 쿠션" for x in S["exploded_out"]):
                F.append(("G4", "기저 폭발 행이 summary.exploded_out 에 없다 — 반대 근거로 못 적는다", ""))
            if R["톤업 선크림"]["rank_surge"] != "1":
                F.append(("G4", "세그먼트 절대 증가 1위가 톤업 선크림이 아니다", R["톤업 선크림"]["rank_surge"]))
            if not R["남자 선크림"]["gate_status"].startswith("세그먼트 기저 미달") or R["남자 선크림"]["rank_surge"]:
                F.append(("G4", "전체로는 크지만 세그먼트 기저가 작은 행이 순위에 남았다", R["남자 선크림"]["gate_status"]))
            n = R["선스틱 신상"]
            if n["is_newcomer"] != "예" or n["rank_newcomer"] != "1" or n["yoy"] or n["first_active_month"] != "2026-01":
                F.append(("G4", "과거 창 결측 → 최근 등장 검색어가 신규로 잡히지 않았다", str(n)[:200]))
            if R["썬크림"]["dup_of"] != "선크림" or R["썬크림"]["tabs"]:
                F.append(("G4", "시계열 동일 행(썬크림)이 같은 검색의 중복으로 빠지지 않았다(T5)", str(R["썬크림"])[:150]))
            m = R["무기자차 선크림"]
            if not m["demography_status"].startswith("미반환") or m["tabs"] or "무기자차 선크림" not in S["no_demography"]:
                F.append(("G4", "kr 인데 demography 없는 행이 [데이터 공백]으로 빠지지 않았다", str(m)[:200]))
            if R["선쿠션"]["gate_status"] != "계산 불가(비교 창 결측 월)" or R["선쿠션"]["yoy"]:
                F.append(("G4", "비교 창 결측 월을 0으로 합산했다(T6)", R["선쿠션"]["gate_status"]))
            if not R["쿨링 선크림"]["provisional"].startswith("잠정"):
                F.append(("G4", "마지막 달 급락으로 급등 판정이 갈리는데 «잠정»이 없다(T2)", str(R["쿨링 선크림"])[:200]))
            if R["마스크 쿠션"]["rank_overindex"] or "마스크 쿠션" not in S["small_overindex_out"]:
                F.append(("G4", "작은 볼륨의 과대 지수(비중 100%)가 순위에 들어갔다(T4)", R["마스크 쿠션"]["over_index"]))
            if S["data_ref_month"] != "2026-09" or R["톤업 선크림"]["window_end"] != "2026-08":
                F.append(("G4", "기준월을 응답에서 읽지 않았거나 마지막 달 제외가 기본이 아니다(T1·T2)", S["data_ref_month"]))
            if E["글로우 쿠션"]["headline_ok"] != "아니오" or E["글로우 쿠션"]["rank_entity"]:
                F.append(("G4", "세그먼트 기저가 작은 대상이 헤드라인 순위에 들어갔다(T4)", str(E["글로우 쿠션"])[:200]))
            if "선크림" not in E or "선크림 추천" not in E["선크림"]["keywords"]:
                F.append(("G4", "불용어 규칙으로 «선크림 추천»이 대상 «선크림»에 묶이지 않았다", str(list(E))))

        # G5 jp
        run([SC, "ledger", "kw_all_jp.json", "--ledger", "ledger_jp.json"], t)
        rc, out = run([SR, "kw_all_jp.json", "--gender", "f", "--age", "a20s", "--category", "jp", "-d", "outjp"], t)
        if rc != 0:
            F.append(("G5", f"jp exit {rc}", out[-300:]))
        else:
            _, jrows = read(t / "outjp" / "segment_keywords_jp.csv")
            SJ = json.loads((t / "outjp" / "segment_summary_jp.json").read_text(encoding="utf-8"))
            if SJ["segment"]["applied"] or not any("사양" in n for n in SJ["notes"]):
                F.append(("G5", "jp 에서 세그먼트를 적용했거나 «데이터 사양» 메모가 없다", str(SJ["notes"])))
            if any(r["over_index"] or r["rank_overindex"] for r in jrows):
                F.append(("G5", "jp 에서 과대 지수가 나왔다(demography 없음)", ""))
            if any(not r["demography_status"].startswith("사양상 없음") for r in jrows):
                F.append(("G5", "jp demography 상태가 «사양상 없음»이 아니다(공백으로 오기)", ""))
            if any(r["rank_surge"] for r in jrows if r["keyword"] == "トーンアップ 日焼け止め"):
                F.append(("G5", "jp 기저 폭발 행이 급등 순위에 들어갔다(T4)", ""))

        # G6 demography 파서
        from segment_risers import parse_demo
        pct, st = parse_demo({"f_gender_ratio": 60, "m_gender_ratio": 40, "a20_ratio": 30, "a25_ratio": 70})
        frac, st2 = parse_demo({"f_gender_ratio": 0.6, "m_gender_ratio": 0.4, "a30_ratio": 1.0})
        nest, st3 = parse_demo({"gender": {"f": 55, "m": 45}, "age": {"a20": 100}})
        bad, st4 = parse_demo({"female": "many"})
        if not pct or abs(pct["f"] - 0.6) > 1e-9 or not frac or abs(frac["f"] - 0.6) > 1e-9:
            F.append(("G6", "퍼센트 · 비율(0~1) 모양을 같은 값으로 읽지 못했다", f"{pct} {frac}"))
        if not nest or abs(nest["a20"] - 1.0) > 1e-9:
            F.append(("G6", "중첩형 demography 를 펼치지 못했다", f"{nest} {st3}"))
        if bad is not None or not st4.startswith("형식 확인 필요"):
            F.append(("G6", "알 수 없는 모양을 «형식 확인 필요»로 막지 않았다", st4))

        # G6b — kr 이고 세그먼트가 전체면 과대 지수 없음
        rc, out = run([SR, "kw_all_kr.json", "--gl", "kr", "--category", "kr_all", "--rules", RULES, "-d", "outall"], t)
        _, arows = read(t / "outall" / "segment_keywords_kr_all.csv")
        if any(r["over_index"] for r in arows) or "과대 지수는 정의되지 않는다" not in out:
            F.append(("G6", "세그먼트 전체(all/all)인데 과대 지수를 냈다", out[-200:]))

        # G7 리포트
        for summ, led_f, name in (("out/segment_summary_kr.json", "ledger.json", "kr"),
                                  ("outjp/segment_summary_jp.json", "ledger_jp.json", "jp")):
            if not (t / summ).is_file():
                continue
            rc, out = run([BR, "--summary", summ, "--ledger", led_f, "--queried-at", "2026-10-15", "-o", f"r_{name}.json"], t)
            if rc != 0:
                F.append(("G7", f"build_report {name} exit {rc}", out[-300:])); continue
            rc, out = run([RR, f"r_{name}.json", "-o", f"r_{name}.html"], t)
            if rc != 0:
                F.append(("G7", f"{name} report.json 이 렌더 계약을 어겼다", out[-400:]))
            rj = json.loads((t / f"r_{name}.json").read_text(encoding="utf-8"))
            if name == "jp" and "세그먼트 순위는 내지 않았다" not in rj["boundary"]:
                F.append(("G7", "jp 리포트 경계 문장에 «세그먼트 미적용»이 없다", rj["boundary"]))
            if sum(c["cost"] for c in rj["provenance"]["calls"]) != sum(c["cost"] for c in
                                                                         json.loads((t / led_f).read_text(encoding="utf-8"))["calls"]):
                F.append(("G7", "provenance.calls 비용 합이 원장과 다르다", ""))
        ex = HERE.parent / "assets" / "report.example.json"
        rc, out = run([RR, ex, "-o", t / "example.html"], t)
        if rc != 0:
            F.append(("G7", "assets/report.example.json 이 렌더되지 않는다", out[-300:]))
        ex_sub = json.loads(ex.read_text(encoding="utf-8"))["meta"]["subtitle"]
        if not ex_sub.startswith(NOTE):
            F.append(("G7", "assets/report.example.json 부제에 합성 픽스처 표시가 없다", ex_sub))
        if (t / "out/segment_summary_kr.json").is_file():          # --subtitle-note 가 부제 앞에 붙는가
            rc, out = run([BR, "--summary", "out/segment_summary_kr.json", "--ledger", "ledger.json", "--queried-at", "2026-10-15",
                           "--subtitle-note", NOTE, "-o", "r_note.json"], t)
            sub_n = json.loads((t / "r_note.json").read_text(encoding="utf-8"))["meta"]["subtitle"] if rc == 0 else out[-200:]
            if not sub_n.startswith(NOTE + " · "):
                F.append(("G7", "--subtitle-note 표시가 부제 앞에 붙지 않는다", sub_n))

        # G9 범위 점검(v0.1.1 P0 회귀) — 합성 픽스처를 이 자리에서 만든다(가상 볼륨 · 가상 인명)
        g9(t, SC, SR, BR, RR, F)

    if F:
        print(f"실패 {len(F)}건")
        for k, why, d in F:
            print(f"  ✗ {k}: {why}")
            if d:
                print(f"      {str(d)[:300]}")
        return 1
    print("통과 — 세그먼트 경로 G1~G9 (수집 · 절단 · 스크린 · 기저 폭발 · 신규 · 중복 · 결측 · 잠정 · jp 사양 · 파서 · 리포트 · 컬럼 대조"
          " · 범위 점검)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
