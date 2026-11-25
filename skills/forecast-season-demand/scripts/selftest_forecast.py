#!/usr/bin/env python3
"""이 스킬 고유 회귀 검사 — collect_pool · screen_volume · forecast_segments · build_report (합성 픽스처 · API 호출 없음).

픽스처(assets/fixtures/)는 선크림(kr) 모양의 합성 응답이다 — 기준월 2026-10 · 조회일 2026-11-19.
  T7 «선크림» intent_finder 반환 20 = limit 20 (절단) · «선스틱»은 limit 50에 7개
  T5 «썬크림»·«선 스틱» 표기변형 · «추천 선크림»은 «선크림 추천»과 시계열 동일(같은 검색의 중복) · ads_metrics 완전 중복 행
  T6 «아기 선크림» 2025-03 nv_code 결측 → 모든 합계에서 빠진다
  T4 «성분·기능» 세그먼트 전년 기저 < 3,000 → r=1 · 순위 제외
  T2 «선크림» 마지막 달(2026-10) 급락 → check_refmonth 의심 · 기본(제외)과 포함 모드의 비율 방향이 갈려 «잠정»
  T3 volume_trend 를 바꿔도 결과가 같다
  T1 기준월 · 지연 · 예측 기간 중 이미 지난 달 수
  역위상 «겨울·레저»(정점 1월) ↔ 카테고리 정점 7월
  그리고 SKILL.md 의 산출 컬럼 목록 = 스크립트 실제 출력 · 리포트가 렌더링 계약을 통과한다.

    python3 scripts/selftest_forecast.py        # 스킬 폴더에서 실행
"""
import csv, json, os, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
NOTE = "합성 픽스처 예시(assets/fixtures) — 실제 시장 값이 아니다"   # 견본 부제의 합성 표시
SKILL = HERE.parent
FIX = SKILL / "assets" / "fixtures"
THEMES = SKILL / "assets" / "themes.json"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
FILES = ("pool.csv", "screen.csv", "keywords.csv", "segment_forecast.csv", "forecast_monthly.csv", "season_calendar.csv")

sys.path.insert(0, str(HERE))
import forecast_core as FC  # noqa: E402

fails = []


def check(cond, name, detail=""):
    if not cond:
        fails.append((name, str(detail)[-400:]))


def run(args, cwd):
    p = subprocess.run([sys.executable] + [str(x) for x in args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    return p.returncode, p.stdout + p.stderr


def rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def header(path):
    return next(csv.reader(open(path, encoding="utf-8-sig")))


def documented_columns():
    """SKILL.md «산출물» 코드 블록 — 파일 이름 줄 다음의 두 칸 들여쓴 줄들이 컬럼 목록이다."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    out, cur = {}, None
    for ln in text.splitlines():
        m = re.match(r"^(\S+\.csv)\b", ln)
        if m and m.group(1) in FILES:
            cur = m.group(1)
            out[cur] = []
            continue
        if cur and ln.startswith("  ") and not ln.startswith("   "):
            out[cur] += [c.strip() for c in ln.split(",") if c.strip()]
        elif cur:
            cur = None
    return out


def pipeline(w, extra_forecast=(), horizon=6, all_file="raw/kw_all.json"):
    S = HERE
    r1 = run([S / "collect_pool.py", "--seeds", "raw/seeds.json", "--gl", "kr", "--intent", "raw/if_sunscreen.json",
              "--intent", "raw/if_sunstick.json@50", "--themes", THEMES, "-o", "pool.json", "--csv", "pool.csv",
              "--ledger", "ledger.json"], w)
    r2 = run([S / "screen_volume.py", "raw/ads_metrics.json", "--pool", "pool.json", "--seeds", "raw/seeds.json",
              "--themes", THEMES, "-o", "all_request.json", "--csv", "screen.csv", "--ledger", "ledger.json"], w)
    rc, out = run([S / "check_refmonth.py", all_file, "--queried-at", "2026-11-19", "--json"], w)
    (w / "refmonth.json").write_text(out, encoding="utf-8")
    r3 = run([S / "forecast_segments.py", all_file, "--themes", THEMES, "--horizon", horizon, "--queried-at", "2026-11-19",
              "--requested", "all_request.json", "--refmonth-check", "refmonth.json", "--ledger", "ledger.json",
              "-o", "out", *extra_forecast], w)
    return r1, r2, r3


def fresh(td, name):
    w = pathlib.Path(td) / name
    (w / "raw").mkdir(parents=True)
    for f in FIX.glob("*.json"):
        shutil.copy(f, w / "raw" / f.name)
    return w


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    with tempfile.TemporaryDirectory() as td:
        w = fresh(td, "base")
        (rc1, o1), (rc2, o2), (rc3, o3) = pipeline(w)
        check(rc1 == 0 and rc2 == 0 and rc3 == 0, "파이프라인이 끝까지 돌지 않는다", o1 + o2 + o3)
        if fails:
            return report()

        # ── 문서 컬럼 = 실제 출력
        doc = documented_columns()
        for f in FILES:
            real = header(w / f if f in ("pool.csv", "screen.csv") else w / "out" / f)
            check(doc.get(f) == real, f"SKILL.md 의 {f} 컬럼 목록이 실제 출력과 다르다", f"문서 {doc.get(f)} / 실제 {real}")

        # ── T7 절단
        led = json.loads((w / "ledger.json").read_text(encoding="utf-8"))
        col = led["steps"]["collect"]
        check(col["truncated"] == ["선크림"] and "절단" in o1, "T7 반환 = limit 인 호출을 절단으로 잡지 못한다", o1)
        check(col["n_excluded"] == 2, "범위 밖 패턴(exclude)으로 뺀 검색어 수가 다르다", col)
        pool = {r["keyword"]: r for r in rows(w / "pool.csv")}
        check(pool["썬크림"]["status"].startswith("표기변형") and pool["선 스틱"]["rep"] == "선스틱",
              "T5 표기변형을 canonical 대표로 묶지 못한다", pool.get("썬크림"))
        check([c["tool"] for c in led["calls"]] == ["intent_finder", "intent_finder", "keyword_info", "keyword_info"]
              and [c["id"] for c in led["calls"]] == ["C1", "C2", "C3", "C4"], "원장 호출 순서 · ID 가 다르다", led["calls"])
        run([HERE / "collect_pool.py", "--seeds", "raw/seeds.json", "--gl", "kr", "--intent", "raw/if_sunscreen.json",
             "--intent", "raw/if_sunstick.json@50", "--themes", THEMES, "-o", "pool.json", "--csv", "pool.csv",
             "--ledger", "ledger.json"], w)
        led2 = json.loads((w / "ledger.json").read_text(encoding="utf-8"))
        check(len(led2["calls"]) == 4, "같은 원본으로 다시 돌리면 원장 호출이 두 번 세진다", len(led2["calls"]))
        check(sum(c["cost"] for c in led2["calls"]) == 315, "원장 비용 합이 cost_detail 합과 다르다",
              [c["cost"] for c in led2["calls"]])

        # ── 시드 규칙
        (w / "six.json").write_text(json.dumps(["a", "b", "c", "d", "e", "f"]), encoding="utf-8")
        (w / "none.json").write_text("[]", encoding="utf-8")
        for sf in ("six.json", "none.json"):
            rc, out = run([HERE / "collect_pool.py", "--seeds", sf, "--gl", "kr", "--themes", THEMES, "-o", "x.json",
                           "--csv", "x.csv", "--ledger", "x_ledger.json"], w)
            check(rc == 2, f"시드 {sf} 를 거부하지 않는다(1~5개)", out)

        # ── 스크린
        scr = {r["keyword"]: r for r in rows(w / "screen.csv")}
        check(scr["선스틱 단점"]["screen"] == "미반환", "요청했는데 응답에 없는 검색어를 미반환으로 남기지 않는다", scr["선스틱 단점"])
        check(scr["선크림 지우는법"]["screen"] == "게이트 미달", "스크린 게이트가 걸리지 않는다", scr["선크림 지우는법"])
        check("완전 중복 행 1행 합침" in o2, "ads_metrics 응답의 완전 중복 행을 합치지 않는다", o2)
        rc, out = run([HERE / "screen_volume.py", "raw/ads_metrics.json", "--pool", "pool.json", "--seeds", "raw/seeds.json",
                       "--max-k", "3", "-o", "k3.json", "--csv", "k3.csv", "--ledger", "k3_ledger.json"], w)
        k3 = json.loads((w / "k3.json").read_text(encoding="utf-8"))
        check(rc == 0 and len(k3) == 5 and "선크림" in k3 and "선스틱" in k3 and "상한 초과" in out,
              "--max-k 상한(시드는 별도)이 지켜지지 않는다", out)
        am = json.loads((w / "raw" / "ads_metrics.json").read_text(encoding="utf-8"))
        for r in am["data"]:
            if r["keyword"] == "추천 선크림":
                r["ads_metrics"]["cpc"] = 0.3                   # 지표가 «선크림 추천»과 모두 같아진다
        (w / "am_same.json").write_text(json.dumps(am, ensure_ascii=False), encoding="utf-8")
        run([HERE / "screen_volume.py", "am_same.json", "--pool", "pool.json", "-o", "s2.json", "--csv", "s2.csv",
             "--ledger", "s2_ledger.json"], w)
        s2 = {r["keyword"]: r for r in rows(w / "s2.csv")}
        check(s2["추천 선크림"]["screen"].startswith("같은 검색 병합"), "T5 지표가 같은 검색을 한 번만 보내지 않는다", s2["추천 선크림"])

        # ── 예측
        kw = {r["keyword"]: r for r in rows(w / "out" / "keywords.csv")}
        sg = {r["segment"]: r for r in rows(w / "out" / "segment_forecast.csv")}
        cal = {r["segment"]: r for r in rows(w / "out" / "season_calendar.csv")}
        mon = rows(w / "out" / "forecast_monthly.csv")
        res = json.loads((w / "out" / "forecast_result.json").read_text(encoding="utf-8"))
        M = res["meta"]
        check(M["data_ref_month"] == "2026-10" and M["lag_months"] == 1 and M["horizon_elapsed_at_query"] == 1
              and M["horizon_months"][0] == "2026-11", "T1 기준월 · 지연 · 이미 지난 예측 달 수가 다르다", M)
        check(kw["아기 선크림"]["status"].startswith("결측 월") and sg["아기·민감"]["n_keywords"] == "1",
              "T6 결측 월 검색어가 합계에 들어갔다(0으로 채움)", kw["아기 선크림"])
        check(kw["추천 선크림"]["status"] == "같은 검색의 중복" and kw["추천 선크림"]["dup_of"] == "선크림 추천",
              "T5 시계열이 같은 검색을 한 번만 세지 않는다", kw["추천 선크림"])
        check(kw["썬크림"]["status"].startswith("표기변형") and int(sg["코어·일반"]["next_total_upper"]) > int(sg["코어·일반"]["next_total"]),
              "T5 표기변형 상한이 하한과 따로 나오지 않는다", sg["코어·일반"])
        check(kw["자외선 지수"]["status"] == "요청 외 반환" and kw["선크림 차이"]["status"] == "미반환",
              "요청 외 반환 · 미반환 처리가 다르다", (kw["자외선 지수"], kw["선크림 차이"]))
        check(sg["성분·기능"]["gated"] == "예" and sg["성분·기능"]["r_used"] == "1.0" and sg["성분·기능"]["rank_pct"] == "",
              "T4 작은 기저 세그먼트가 비율을 쓰거나 순위에 들어갔다", sg["성분·기능"])
        check(sg["코어·일반"]["provisional"] == "잠정" and float(sg["코어·일반"]["r_other_mode"]) < 1 < float(sg["코어·일반"]["r_used"]),
              "T2 마지막 달 포함 · 제외에서 방향이 갈리는데 «잠정»이 아니다", sg["코어·일반"])
        check(M["refmonth_check"]["suspect"] == 1, "T2 check_refmonth 의심 건수가 결과에 실리지 않았다", M["refmonth_check"])
        b = next(r for r in mon if r["segment"] == "아기·민감" and r["month"] == "2026-11")
        check(abs(int(b["forecast"]) - int(b["last_year"]) * float(sg["아기·민감"]["r_used"])) <= 1,
              "예측 = 전년 같은 달 × r 이 아니다", b)
        check(cal["겨울·레저"]["anti_phase"] == "예" and cal["겨울·레저"]["peak_month"] == "1"
              and cal["카테고리 합계"]["peak_month"] == "7", "역위상 세그먼트를 잡지 못한다", cal["겨울·레저"])
        check(cal["겨울·레저"]["peak"] == "2027-01" and cal["겨울·레저"]["peak_in_horizon"] == "예"
              and cal["제형"]["prep"] == "2027-02", "시즌 일정 날짜가 다르다", (cal["겨울·레저"], cal["제형"]))
        cat_sum = sum(int(sg[s]["next_total"]) for s in sg if s != "카테고리 합계")
        check(abs(cat_sum - int(sg["카테고리 합계"]["next_total"])) <= len(sg), "카테고리 예측이 세그먼트 예측의 합이 아니다", cat_sum)

        # T3 — volume_trend 를 바꿔도 같은 결과
        allj = json.loads((w / "raw" / "kw_all.json").read_text(encoding="utf-8"))
        for r in allj["data"]:
            r["ads_metrics"]["volume_trend"] = 9.9
        w3 = fresh(td, "t3")
        (w3 / "raw" / "kw_all.json").write_text(json.dumps(allj, ensure_ascii=False), encoding="utf-8")
        pipeline(w3)
        check((w3 / "out" / "segment_forecast.csv").read_bytes() == (w / "out" / "segment_forecast.csv").read_bytes(),
              "T3 volume_trend 가 계산에 들어간다")

        # T2 — 포함 모드 · refmonth 점검 필수 · 3개월
        wi = fresh(td, "incl")
        pipeline(wi, ["--include-last-month"])
        si = {r["segment"]: r for r in rows(wi / "out" / "segment_forecast.csv")}
        check(float(si["코어·일반"]["r_used"]) < 1, "--include-last-month 가 비율 창에 마지막 달을 넣지 않는다", si["코어·일반"])
        rc, out = run([HERE / "forecast_segments.py", "raw/kw_all.json", "--themes", THEMES, "--horizon", "6",
                       "--queried-at", "2026-11-19", "--ledger", "z.json", "-o", "z"], w)
        check(rc == 2 and "refmonth" in out, "T2 마지막 달 점검 없이 예측을 낸다", out)
        w6 = fresh(td, "h3")
        pipeline(w6, horizon=3)
        m3 = [r for r in rows(w6 / "out" / "forecast_monthly.csv") if r["segment"] == "제형"]
        check(len(m3) == 3 and m3[-1]["month"] == "2027-01", "--horizon 3 이 3개월을 내지 않는다", m3)

        # ── 핵심 규칙 단위 검사
        winter = {1: 2.4, 2: 1.6, 3: .7, 4: .4, 5: .35, 6: .35, 7: .35, 8: .4, 9: .5, 10: .8, 11: 1.4, 12: 2.2}
        check(FC.ramp_month(winter, 1) == 11 and FC.end_month(winter, 1) == 3, "상승 시작 · 시즌 끝 규칙이 다르다")
        flat = {m: 1000 for m in FC.window_months("2026-09", 40)}
        check(FC.profile(flat, "2026-09")["season_type"] == "상시형", "평평한 시계열을 상시형으로 보지 않는다")
        spike = dict(flat)
        spike["2026-03"] = 9000
        check(FC.ratio(spike, "2026-03", 1, 100)[3].startswith("비율 상·하한"), "비율 상·하한(R_MAX)이 걸리지 않는다")
        check(FC.ratio({**flat, "2025-08": None}, "2026-09", 3, 100)[0] is None, "결측 달이 있는데 비율을 계산한다")

        # ── 리포트
        rc, out = run([HERE / "build_report.py", "out/forecast_result.json", "--ledger", "ledger.json",
                       "--category", "선크림", "-o", "report.json"], w)
        rc2, out2 = run([HERE / "render_report.py", "report.json", "-o", "report.html"], w)
        check(rc == 0 and rc2 == 0, "리포트가 렌더링 계약을 통과하지 못한다", out + out2)
        if rc == 0:
            rep = json.loads((w / "report.json").read_text(encoding="utf-8"))
            ids = [b.get("id") for s in rep["sections"] for b in s["blocks"]]
            check({"D1", "D2", "D3", "A1", "A2", "A3"} <= set(ids), "요약 · 역위상 발견 카드나 실행 카드가 빠졌다", ids)
            check(len(rep["provenance"]["calls"]) == 4 and sum(c["cost"] for c in rep["provenance"]["calls"]) == 315,
                  "provenance.calls 가 수집 원장과 다르다", rep["provenance"]["calls"])
            txt = json.dumps(rep["sections"], ensure_ascii=False)
            check("성분·기능 —" not in txt.split("반대 근거")[0], "T4 게이트 미달 세그먼트가 순위 문장의 주어가 됐다")
            check("2026-10까지" in rep["boundary"], "경계 문장에 기준월이 없다", rep["boundary"])
        # 견본 합성 표시 — 견본 부제는 `--subtitle-note`로 붙인 표시로 시작해야 한다
        rc, out = run([HERE / "build_report.py", "out/forecast_result.json", "--ledger", "ledger.json",
                       "--category", "선크림", "--subtitle-note", NOTE, "-o", "report_note.json"], w)
        sub_n = json.loads((w / "report_note.json").read_text(encoding="utf-8"))["meta"]["subtitle"] if rc == 0 else ""
        check(sub_n.startswith(NOTE + " · "), "--subtitle-note 표시가 부제 앞에 붙지 않는다", sub_n)
        ex = json.loads((SKILL / "assets" / "report.example.json").read_text(encoding="utf-8"))
        check(ex["meta"]["subtitle"].startswith(NOTE), "assets/report.example.json 부제에 합성 픽스처 표시가 없다", ex["meta"]["subtitle"])
    return report()


def report():
    if fails:
        print(f"실패 {len(fails)}건")
        for n, d in fails:
            print(f"  ✗ {n}")
            if d:
                print(f"      {d[:300]}")
        return 1
    print("통과 — forecast-season-demand 고유 검사 (T1 · T2 · T3 · T4 · T5 · T6 · T7 · 역위상 · 컬럼 대조 · 리포트)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
