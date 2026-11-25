#!/usr/bin/env python3
"""rank-category-brands 고유 자가검증 — SKILL.md 의 예시 명령을 합성 픽스처로 그대로 실행한다. API 호출 없음.

검사:
  1. SKILL.md bash 블록의 `python3 scripts/…` 명령을 적힌 그대로 실행 — 전부 exit 0
  2. SKILL.md «산출물» 절의 컬럼 목록 = 스크립트가 실제로 쓴 CSV 헤더 (순서까지)
  3. 함정 처리 — 픽스처(assets/fixtures/, 합성 값)에 심어 둔 사례
     T1/T2 기준월 · 마지막 달 제외 · 의심 행 / T3 volume_trend 미사용 / T4 작은 기저 × 큰 성장(써코니) · 세그먼트 게이트 /
     T5 시계열 동일 어순 변형(나이키) · canonical 변형 하한·상한과 순위 뒤집힘(아디다스) / T6 2024년에 생긴 검색어(온러닝) ·
     결측 월(미즈노) · 짧은 시계열 / T8 논브랜드 분리 · 두 분모 합 / 브랜드명 단독은 순위 밖 / gl≠kr 세그먼트 사양 /
     리포트 사실 · 원장 비용
  4. v0.1.1 회귀(P0 2건) — 생활가전 렌탈 합성 사전(공개 브랜드 문자열 · 지어낸 볼륨, `hr_*`):
     variants 만 걸리는 패밀리 검색(«쿠쿠 정수기» · «lg 정수기» · «웰스 정수기»)이 논브랜드로 새지 않는다 ·
     분야어가 든 별칭(«코지마 안마의자» · «노비타 비데» · 정식명 «세스코 정수기»)이 단독으로 버려지지 않는다 ·
     1단에 «코지마 안마의자 안마의자» 같은 분야어 중복 조합이 없다 · variants 를 뺀 사전에서는 «별칭 누락 의심»이
     그 행들을 찍고 리포트 요약 제목이 «판정 보류»가 된다 · 음성 대조(«정수기 렌탈» 논브랜드 · «코웨이» 단독 · «얼음 정수기» 미의심)
"""
import csv, json, os, pathlib, re, shlex, shutil, subprocess, sys, tempfile

SKILL = pathlib.Path(__file__).resolve().parents[1]
FX = SKILL / "assets" / "fixtures"
CAT = "running-shoes-kr"
NOTE = "합성 픽스처 예시(assets/fixtures) — 실제 시장 값이 아니다"   # 견본 부제의 합성 표시
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def doc_commands():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    cmds = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        joined = re.sub(r"\\\n\s*", " ", block)
        for ln in joined.splitlines():
            ln = ln.split("  #")[0].strip()
            if ln.startswith("python3 scripts/") and "selftest" not in ln:
                cmds.append(ln)
    return cmds


def doc_columns():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    sec = text.split("## 산출물", 1)[1].split("\n## ", 1)[0]
    cols, cur = {}, None
    for ln in sec.splitlines():
        m = re.match(r"^(\w+)_\{category\}\.csv", ln.strip())
        if m:
            cur = m.group(1); cols[cur] = []
        elif cur and re.match(r"^\s{2}[a-z][a-z0-9_,\s]+$", ln):
            cols[cur] += [c.strip() for c in ln.split(",") if c.strip()]
        elif cur and ln.strip() and not ln.startswith("  "):
            cur = None
    return cols


def run(argv, cwd):
    p = subprocess.run([sys.executable] + argv, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    return p.returncode, p.stdout + p.stderr


def read(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def series(kw):
    rows = json.load(open(FX / "stage2_all.json", encoding="utf-8"))["data"]
    r = next(x for x in rows if x["keyword"] == kw)
    return {m["month"]: m.get("total") for m in r["monthly_volume"]}


def y0(kw, end="2026-09"):
    s = series(kw)
    ms = sorted(s)
    i = ms.index(end)
    return sum(s[m] for m in ms[i - 11: i + 1])


def setup(tmp):
    shutil.copy(SKILL / "assets" / "brands.running-shoes-kr.example.json", tmp / "brands.json")
    for f in ("stage1_ads_metrics.json", "stage2_all.json"):
        shutil.copy(FX / f, tmp / f)


def main():
    utf8_console()
    fails = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        setup(tmp)
        cmds = doc_commands()
        need = ("plan_queries.py combos", "plan_queries.py stage2", "log_call.py", "check_refmonth.py",
                "rank_brands.py", "build_report.py", "render_report.py")
        miss = [n for n in need if not any(n in c for c in cmds)]
        if miss:
            fails.append(("SKILL.md", f"예시 명령을 찾지 못함 {miss}", ""))
        for c in cmds:
            argv = shlex.split(c)[1:]
            argv[0] = str(SKILL / argv[0])
            rc, out = run(argv, tmp)
            if rc:
                fails.append((argv[0].split(os.sep)[-1].split("/")[-1], f"문서 예시가 실패한다: {c[:90]}", out[-400:]))

        out = tmp / "out"
        files = {k: out / f"{k}_{CAT}.csv" for k in ("brand_rank", "brand_trend", "segment_rank", "keyword_map")}
        if not all(p.is_file() for p in files.values()):
            fails.append(("rank_brands", "산출 CSV 가 없다", str([p.name for p in files.values() if not p.is_file()])))
            return report(fails)
        for name, want in doc_columns().items():
            got = next(csv.reader(open(files[name], encoding="utf-8-sig")))
            if got != want:
                fails.append((name, "문서 컬럼 ≠ 실제 헤더", f"문서 {want}\n실제 {got}"))
        R = {r["brand"]: r for r in read(files["brand_rank"])}
        T = {r["brand"]: r for r in read(files["brand_trend"])}
        SEG = read(files["segment_rank"])
        KM = {r["keyword"]: r for r in read(files["keyword_map"])}
        S = json.load(open(out / f"rank_summary_{CAT}.json", encoding="utf-8"))

        # T1 · T2
        if S["data_ref_month"] != "2026-10" or S["lag_months"] != 1:
            fails.append(("T1", "기준월 · 지연을 응답에서 읽지 못했다", f"{S['data_ref_month']} · {S['lag_months']}"))
        if {r["analysis_end_month"] for r in R.values()} != {"2026-09"} or R["나이키"]["last_month_included"] != "no":
            fails.append(("T2", "마지막 달 제외 기본값 위반", ""))
        if S["last_month_suspect"] < 1:
            fails.append(("T2", "마지막 달 급락 픽스처(나이키 러닝화)를 의심으로 세지 못했다", ""))
        md = (out / f"rank_{CAT}.md").read_text(encoding="utf-8")
        if "데이터 기준월 2026-10 / 조회일 2026-11-19 / 지연 1개월" not in md:
            fails.append(("T1", "요약 헤더에 기준월 / 조회일 / 지연 3종이 없다", md[:200]))
        incl = out.parent / "out_incl" / f"brand_rank_{CAT}.csv"
        if incl.is_file():
            ri = read(incl)
            if {r["analysis_end_month"] for r in ri} != {"2026-10"} or ri[0]["last_month_included"] != "yes(잠정)":
                fails.append(("T2", "--include-last-month 가 기준월까지 쓰지 않거나 «잠정» 표기가 없다", ""))

        # T5 같은 검색의 중복 — «러닝화 나이키»는 «나이키 러닝화»와 시계열이 같아 한 번만
        exp = y0("나이키 러닝화") + y0("nike 러닝화") + y0("나이키 페가수스")
        if int(R["나이키"]["volume_12m_lo"]) != exp or int(R["나이키"]["volume_12m_hi"]) != exp:
            fails.append(("T5", f"같은 검색의 중복을 두 번 셌다: 나이키 {R['나이키']['volume_12m_lo']} ≠ {exp}", ""))
        if KM.get("러닝화 나이키", {}).get("dup_of") != "나이키 러닝화":
            fails.append(("T5", "keyword_map 에 같은 검색의 중복(dup_of)이 기록되지 않았다", ""))
        # T5 canonical 변형 — 하한 = 최댓값 · 상한 = 합 · 순위 뒤집힘
        a1, a2 = y0("아디다스 러닝화"), y0("아디다스 런닝화")
        if int(R["아디다스"]["volume_12m_lo"]) != max(a1, a2) or int(R["아디다스"]["volume_12m_hi"]) != a1 + a2:
            fails.append(("T5", "표기변형 묶음의 하한(최댓값) · 상한(합)이 틀렸다", f"{R['아디다스']['volume_12m_lo']} / {R['아디다스']['volume_12m_hi']}"))
        if R["아디다스"]["rank_flip"] != "yes" or "아디다스" not in S["rank_flips"]:
            fails.append(("T5", "하한·상한 순위가 다른데 «순위 뒤집힘»을 표시하지 않았다", ""))

        # T4 작은 기저 × 큰 성장
        s = R["써코니"]
        if s["yoy_12m"] or s["yoy_gated_out"] != "1" or s["rank_yoy"] or not s["yoy_base_12m"]:
            fails.append(("T4", "기저 게이트 미만 브랜드의 변화율을 내거나 순위에 올렸다(기준값은 남아야 한다)", str(s)[:200]))
        if T["써코니"]["chg_3y"] or T["써코니"]["chg_3y_gated_out"] != "1":
            fails.append(("T4", "3년 변화율에 게이트가 걸리지 않았다", ""))
        sg = [r for r in SEG if r["brand"] == "써코니"]
        if not sg or any(r["rank_in_segment"] or r["seg_gated_out"] != "1" for r in sg):
            fails.append(("T4", "세그먼트(그룹) 순위에 게이트 미만 브랜드가 올랐다", ""))
        top_yoy = min((r for r in R.values() if r["rank_yoy"]), key=lambda r: int(r["rank_yoy"]))
        if top_yoy["brand"] != "호카":
            fails.append(("T4", f"게이트 통과 증감률 1위가 예상(호카)과 다르다: {top_yoy['brand']}", ""))

        # T6 고정 모집단
        o = T["온러닝"]
        if o["volume_y2"] or not o["trend_status"].startswith("계산 불가") or o["groups_excluded_3y"] != "1":
            fails.append(("T6", "2024년에 생긴 검색어가 3년 합을 깨거나 0 으로 채워졌다", str(o)[:200]))
        if not R["온러닝"]["yoy_12m"]:
            fails.append(("T6", "전년 창이 모두 있는 신규 검색어의 전년 동기를 지웠다", ""))
        m = R["미즈노"]
        if m["yoy_12m"] or m["yoy_groups_n"] != "0" or not m["volume_12m_lo"]:
            fails.append(("T6", "결측 월이 있는 창을 0 으로 합산했다(미즈노 2025-02)", str(m)[:200]))
        fix = [t for t in T.values() if t["volume_y2"]]
        if any(int(t["volume_y0"]) - int(t["volume_y2"]) != int(t["abs_chg_3y"]) for t in fix):
            fails.append(("T6", "3년 절대 증감이 같은 모집단의 차가 아니다", ""))

        # T3
        if KM["나이키 러닝화"]["api_volume_trend_do_not_use"] in ("", None) or R["나이키"]["yoy_12m"] == "90.0":
            fails.append(("T3", "volume_trend 를 참고 컬럼으로 보존하지 않거나 판정에 썼다", ""))

        # T8 두 분모
        nb = R.get("_논브랜드")
        if not nb or not nb["share_genre_lo"] or nb["share_brandset_lo"]:
            fails.append(("T8", "논브랜드 행이 없거나 브랜드 합 대비 몫에 섞였다", ""))
        sb = sum(float(r["share_brandset_lo"]) for r in R.values() if r["share_brandset_lo"])
        sgn = sum(float(r["share_genre_lo"]) for r in R.values() if r["share_genre_lo"])
        if abs(sb - 1) > 2e-3 or abs(sgn - 1) > 2e-3:
            fails.append(("T8", f"두 분모의 몫 합이 1 이 아니다 ({sb:.4f} · {sgn:.4f})", ""))
        if not float(R["나이키"]["share_genre_lo"]) < float(R["나이키"]["share_brandset_lo"]):
            fails.append(("T8", "분야 전체 대비 몫이 브랜드 합 대비보다 작지 않다 — 논브랜드가 분모에 없다", ""))

        # 브랜드명 단독은 순위 밖
        if R["나이키"]["brand_alone_12m"] != "2780000" or KM["나이키"]["kind"] != "alone":
            fails.append(("귀속", "브랜드명 단독을 따로 보고하지 않았다", R["나이키"]["brand_alone_12m"]))
        if KM.get("나이키 양말", {}).get("kind") != "off_genre" or KM.get("러닝화 끈", {}).get("kind") != "excluded":
            fails.append(("귀속", "분야어 없는 브랜드 검색 · exclude 를 순위 밖으로 빼지 않았다", ""))
        if set(S["unreturned"]) & {"on running 러닝화", "on running"}:
            fails.append(("대조", "2단 요청에 없던 키워드를 미반환으로 셌다", str(S["unreturned"])))

        # 리포트 · 원장
        rep = tmp / "report.json"
        if rep.is_file():
            r = json.loads(rep.read_text(encoding="utf-8"))
            d2 = next((b for sct in r["sections"] for b in sct["blocks"] if b.get("id") == "D2"), None)
            if d2 and "써코니" in d2["observed"]:
                fails.append(("T4", "게이트 미만 브랜드가 증감률 헤드라인의 주어가 됐다", d2["observed"][:120]))
            costs = [c["cost"] for c in r["provenance"]["calls"]]
            if costs != [44, 200]:
                fails.append(("원장", f"provenance.calls 비용이 원본 cost_detail 과 다르다: {costs}", ""))
        cost = tmp / "report.cost.json"
        if not cost.is_file() or "244" not in cost.read_text(encoding="utf-8"):
            fails.append(("render_report", "비용 보고서가 없거나 소진 합(244)이 다르다", ""))
        bad = tmp / "nocost.json"
        bad.write_text(json.dumps({"data": []}), encoding="utf-8")
        rc, outp = run([str(SKILL / "scripts" / "log_call.py"), "--ledger", "l2.jsonl", "--step", "x", "--raw", "nocost.json"], tmp)
        if rc == 0:
            fails.append(("log_call", "cost_detail 이 없는 응답을 원장에 비용 없이 적었다", outp[-200:]))

        # gl≠kr — 세그먼트는 사양으로 비고 · 짧은 시계열 — 3년 흐름 계산 불가
        cfg = json.loads((tmp / "brands.json").read_text(encoding="utf-8")); cfg["gl"] = "us"
        (tmp / "brands_us.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        s2 = json.loads((tmp / "stage2_all.json").read_text(encoding="utf-8"))
        for row in s2["data"]:
            row.pop("demography", None)
            row["monthly_volume"] = row["monthly_volume"][-24:]
        (tmp / "stage2_us_short.json").write_text(json.dumps(s2, ensure_ascii=False), encoding="utf-8")
        rc, outp = run([str(SKILL / "scripts" / "rank_brands.py"), "stage2_us_short.json", "--config", "brands_us.json",
                        "--category", "us", "--queried-at", "2026-11-19", "--outdir", "out_us"], tmp)
        if rc:
            fails.append(("rank_brands", "gl=us · 24개월 응답에서 비정상 종료", outp[-300:]))
        else:
            su = json.loads((tmp / "out_us" / "rank_summary_us.json").read_text(encoding="utf-8"))
            if "gl 사양" not in su["segment_status"] or read(tmp / "out_us" / "segment_rank_us.csv"):
                fails.append(("segment", "gl≠kr 인데 세그먼트를 만들거나 사양으로 적지 않았다", su["segment_status"]))
            tu = read(tmp / "out_us" / "brand_trend_us.csv")
            if any(t["volume_y2"] or "36개월" not in t["trend_status"] for t in tu):
                fails.append(("T6", "시계열이 36개월 창보다 짧은데 3년 흐름을 계산했다", ""))

        # --subtitle-note 가 부제 앞에 붙는가 (견본 합성 표시)
        rc, outp = run([str(SKILL / "scripts" / "build_report.py"), "--outdir", "out", "--category", CAT,
                        "--ledger", "ledger.jsonl", "--subtitle-note", NOTE, "-o", "report_note.json"], tmp)
        sub_n = json.loads((tmp / "report_note.json").read_text(encoding="utf-8"))["meta"]["subtitle"] if rc == 0 else outp[-200:]
        if not sub_n.startswith(NOTE + " · "):
            fails.append(("report", "--subtitle-note 표시가 부제 앞에 붙지 않는다", sub_n))

        hr_regression(tmp, fails)

    # 견본 리포트가 이 스킬 모양인가
    ex = json.loads((SKILL / "assets" / "report.example.json").read_text(encoding="utf-8"))
    if ex.get("meta", {}).get("skill") != "rank-category-brands":
        fails.append(("report.example.json", "견본이 이 스킬의 결과 모양이 아니다", ""))
    if not ex.get("meta", {}).get("subtitle", "").startswith(NOTE):
        fails.append(("report.example.json", "견본 부제에 합성 픽스처 표시가 없다", ex.get("meta", {}).get("subtitle", "")))
    return report(fails)


# ── v0.1.1 회귀 — 생활가전 렌탈 합성 픽스처 (공개 브랜드 문자열 · 볼륨은 지어낸 값 · 실측 아님)
HR_VOL = {  # 12개월 볼륨(12의 배수 — 월별 값이 일정한 합성 시계열)
    "코웨이": 2_400_000, "코웨이 정수기": 960_000, "쿠쿠 정수기": 720_000, "lg 정수기": 360_000,
    "쿠쿠 정수기 렌탈": 144_000, "웰스 정수기": 96_000, "코지마 안마의자": 372_000, "코지마 안마의자 렌탈": 12_000,
    "노비타 비데": 312_000, "세스코 정수기": 36_000, "정수기 렌탈": 600_000, "얼음 정수기": 60_000,
}
HR_STAGE1_ONLY = {"코웨이"}          # 단독은 2단에 가지 않는다


def hr_config(with_variants):
    return {"genre": "생활가전 렌탈(합성)", "gl": "kr", "rules": {"변형치환": {}}, "themes": [],
            "brands": ["코웨이", "쿠쿠 렌탈", "LG 가전구독", "교원웰스", "코지마", "노비타", "세스코 정수기", "현대큐밍"],
            "brand_aliases": {"코웨이": ["웅진코웨이"], "쿠쿠 렌탈": ["쿠쿠홈시스"], "LG 가전구독": ["lg 케어솔루션"],
                              "코지마": ["코지마 안마의자"], "노비타": ["노비타 비데"],
                              "현대큐밍": ["현대렌탈케어"]},
            "variants": ({"쿠쿠 렌탈": ["쿠쿠 정수기", "쿠쿠 비데"], "LG 가전구독": ["lg 정수기"], "교원웰스": ["웰스 정수기"]}
                         if with_variants else {}),
            "genre_terms": ["렌탈", "정수기", "비데", "안마의자"], "category_terms": ["정수기 렌탈"],
            "exclude": "", "probe_alone": True}


def hr_rows(keys, stage2):
    months = [f"{y}-{m:02d}" for y in range(2022, 2027) for m in range(1, 13)][10:58]   # 2022-11 ~ 2026-10
    out = []
    for i, k in enumerate(keys):
        v = HR_VOL[k]
        row = {"keyword": k, "ads_metrics": {"volume_total": v, "volume_avg": v // 12, "volume_trend": 0.0,
                                             "cpc": round(0.5 + i / 100, 2), "competition": "MEDIUM", "competition_index": 40 + i}}
        if stage2:
            row["monthly_volume"] = [{"month": m, "total": v // 12} for m in months]
        out.append(row)
    return out


def hr_regression(tmp, fails):
    sys.path.insert(0, str(SKILL / "scripts"))
    import importlib
    rc_mod = importlib.import_module("rank_common")
    hr = tmp / "hr"
    hr.mkdir()
    for name, wv in (("brands_hr.json", True), ("brands_hr_gap.json", False)):
        (hr / name).write_text(json.dumps(hr_config(wv), ensure_ascii=False), encoding="utf-8")
    s1 = {"cost_detail": {"total_cost": len(HR_VOL)}, "data": hr_rows(list(HR_VOL), False)}
    s2 = {"cost_detail": {"total_cost": 10 * (len(HR_VOL) - 1)},
          "data": hr_rows([k for k in HR_VOL if k not in HR_STAGE1_ONLY], True)}
    (hr / "stage1_ads_metrics.json").write_text(json.dumps(s1, ensure_ascii=False), encoding="utf-8")
    (hr / "stage2_all.json").write_text(json.dumps(s2, ensure_ascii=False), encoding="utf-8")

    # ① 귀속 규칙 단위 대조 (rank_common.classify)
    fixed = rc_mod.load_config(hr / "brands_hr.json")
    want = {"쿠쿠 정수기": ("쿠쿠 렌탈", "model"), "쿠쿠 정수기 렌탈": ("쿠쿠 렌탈", "model"), "lg 정수기": ("LG 가전구독", "model"),
            "웰스 정수기": ("교원웰스", "model"), "코지마 안마의자": ("코지마", "genre"), "노비타 비데": ("노비타", "genre"),
            "세스코 정수기": ("세스코 정수기", "genre"), "쿠쿠 렌탈": ("쿠쿠 렌탈", "genre"),
            "코지마": ("코지마", "alone"), "코웨이": ("코웨이", "alone"), "코웨이 정수기": ("코웨이", "genre"),
            "정수기 렌탈": ("_논브랜드", "category"), "얼음 정수기": ("_논브랜드", "category"), "코웨이 고객센터": ("코웨이", "off_genre"),
            "현대렌탈케어": ("현대큐밍", "alone"), "현대렌탈케어 정수기": ("현대큐밍", "genre")}   # 회사명 가운데 «렌탈»은 분야어 별칭이 아니다
    bad = {k: rc_mod.classify(k, fixed) for k in want if rc_mod.classify(k, fixed) != want[k]}
    if bad:
        fails.append(("P0 귀속", "variants · 분야어 든 별칭 귀속이 SKILL.md 규칙과 다르다", str(bad)))

    # ② 1단 조합 — 분야어 든 별칭에 분야어를 다시 붙이지 않는다
    rc, outp = run([str(SKILL / "scripts" / "plan_queries.py"), "combos", "--config", "brands_hr.json", "-o", "s1_req.json"], hr)
    req = json.loads((hr / "s1_req.json").read_text(encoding="utf-8")) if rc == 0 else []
    dup = [q for q in req if q in ("코지마 안마의자 안마의자", "노비타 비데 비데", "세스코 정수기 정수기", "쿠쿠 렌탈 렌탈")]
    keep = {"코지마 안마의자", "노비타 비데", "세스코 정수기", "쿠쿠 렌탈", "현대렌탈케어 정수기", "코지마 안마의자 렌탈"}
    if rc or dup or not keep <= set(req):
        fails.append(("1단 조합", "분야어 든 별칭에 분야어를 또 붙이거나 그 별칭을 빠뜨렸다", f"{dup} {outp[-200:]}"))

    # ③ 끝까지 — 보정 사전(variants 있음): 패밀리 · 분야어 든 별칭이 브랜드로, 논브랜드는 진짜 일반어만
    common = ["--stage1", "stage1_ads_metrics.json", "--queried-at", "2026-11-19"]
    rc, outp = run([str(SKILL / "scripts" / "rank_brands.py"), "stage2_all.json", "--config", "brands_hr.json",
                    "--category", "hr", "--outdir", "out_fixed"] + common, hr)
    if rc:
        fails.append(("P0 끝까지", "합성 렌탈 픽스처에서 rank_brands 비정상 종료", outp[-300:]))
        return
    R = {r["brand"]: r for r in read(hr / "out_fixed" / "brand_rank_hr.csv")}
    S = json.loads((hr / "out_fixed" / "rank_summary_hr.json").read_text(encoding="utf-8"))
    exp = {"쿠쿠 렌탈": HR_VOL["쿠쿠 정수기"] + HR_VOL["쿠쿠 정수기 렌탈"], "LG 가전구독": HR_VOL["lg 정수기"],
           "교원웰스": HR_VOL["웰스 정수기"], "코지마": HR_VOL["코지마 안마의자"] + HR_VOL["코지마 안마의자 렌탈"],
           "노비타": HR_VOL["노비타 비데"], "세스코 정수기": HR_VOL["세스코 정수기"], "코웨이": HR_VOL["코웨이 정수기"],
           "_논브랜드": HR_VOL["정수기 렌탈"] + HR_VOL["얼음 정수기"]}
    got = {b: int(R[b]["volume_12m_lo"] or 0) for b in exp}
    if got != exp:
        fails.append(("P0 과소집계", "패밀리 검색 · 분야어 든 별칭이 브랜드 순위에 들어가지 않았다(또는 논브랜드가 부풀었다)",
                      str({b: (got[b], exp[b]) for b in exp if got[b] != exp[b]})))
    if R["코웨이"]["brand_alone_12m"] != str(HR_VOL["코웨이"]) or R["코웨이"]["rank_lo"] != "1":
        fails.append(("귀속 음성", "브랜드명 단독 «코웨이»가 순위에 섞이거나 단독 보고가 빠졌다", str(R["코웨이"])[:200]))
    if S["alias_suspect_nonbrand"]:
        fails.append(("별칭 누락 의심", "보정 사전인데 진짜 논브랜드(«정수기 렌탈» · «얼음 정수기»)를 의심으로 찍었다",
                      str(S["alias_suspect_nonbrand"])))

    # ④ variants 를 뺀 사전 — 패밀리 검색은 논브랜드로 가지만 «별칭 누락 의심»이 반드시 찍히고 리포트에 오른다
    rc, outp = run([str(SKILL / "scripts" / "rank_brands.py"), "stage2_all.json", "--config", "brands_hr_gap.json",
                    "--category", "hr", "--outdir", "out_gap"] + common, hr)
    if rc:
        fails.append(("별칭 누락 의심", "variants 없는 사전에서 rank_brands 비정상 종료", outp[-300:]))
        return
    SG = json.loads((hr / "out_gap" / "rank_summary_hr.json").read_text(encoding="utf-8"))
    flagged = {x["keyword"]: x["near_brand"] for x in SG["alias_suspect_nonbrand"]}
    want_f = {"쿠쿠 정수기": "쿠쿠 렌탈", "lg 정수기": "LG 가전구독", "쿠쿠 정수기 렌탈": "쿠쿠 렌탈", "웰스 정수기": "교원웰스"}
    if flagged != want_f:
        fails.append(("별칭 누락 의심", "사전 브랜드와 닮은 고볼륨 논브랜드 행을 찍지 못했거나 진짜 논브랜드를 찍었다",
                      f"찍힘 {flagged} · 기대 {want_f}"))
    if SG["alias_suspect_nonbrand_volume"] != sum(HR_VOL[k] for k in want_f) or SG["alias_suspect_n"] < len(want_f):
        fails.append(("별칭 누락 의심", "의심 볼륨 합 · 건수가 틀렸다", str(SG["alias_suspect_nonbrand_volume"])))
    KG = {r["keyword"]: r for r in read(hr / "out_gap" / "keyword_map_hr.csv")}
    if KG["쿠쿠 정수기"]["alias_suspect_brand"] != "쿠쿠 렌탈" or KG["정수기 렌탈"]["alias_suspect_brand"]:
        fails.append(("별칭 누락 의심", "keyword_map 의 alias_suspect_brand 가 틀렸다", ""))
    md = (hr / "out_gap" / "rank_hr.md").read_text(encoding="utf-8")
    if "별칭 누락 의심 4행" not in md:
        fails.append(("별칭 누락 의심", "요약 md 에 의심 행 경고가 없다", md[:200]))
    for sub in ("out_gap", "out_fixed"):
        for raw, step in (("stage1_ads_metrics.json", "1단"), ("stage2_all.json", "2단")):
            run([str(SKILL / "scripts" / "log_call.py"), "--ledger", f"{sub}.ledger.jsonl", "--step", step, "--raw", raw,
                 "--at", "2026-11-19 10:00"], hr)
    titles = {}
    for sub in ("out_gap", "out_fixed"):
        rc, outp = run([str(SKILL / "scripts" / "build_report.py"), "--outdir", sub, "--category", "hr",
                        "--ledger", f"{sub}.ledger.jsonl", "-o", f"{sub}.report.json"], hr)
        rc2, outp2 = run([str(SKILL / "scripts" / "render_report.py"), f"{sub}.report.json", "-o", f"{sub}.html"], hr) \
            if rc == 0 else (1, "")
        if rc or rc2:
            fails.append(("리포트", f"{sub} 리포트 조립 · 렌더 실패", (outp + outp2)[-300:]))
            continue
        rj = json.loads((hr / f"{sub}.report.json").read_text(encoding="utf-8"))
        titles[sub] = next(sc["title"] for sc in rj["sections"] if sc["kind"] == "summary")
        if rj["meta"]["version"] != "0.1.1":
            fails.append(("리포트", "report.json meta.version 이 SKILL.md 판과 다르다", rj["meta"]["version"]))
        if sub == "out_gap" and not any(b.get("type") == "table" and "별칭 누락 의심" in b.get("caption", "")
                                        for sc in rj["sections"] for b in sc["blocks"]):
            fails.append(("리포트", "별칭 누락 의심 표가 리포트 부록에 없다", ""))
    if titles and ("판정 보류" not in titles.get("out_gap", "") or "판정 보류" in titles.get("out_fixed", "")
                   or "논브랜드 검색이 함께 크다" in titles.get("out_gap", "")):
        fails.append(("리포트", "요약 제목이 별칭 누락 의심을 반영하지 않는다(«논브랜드 검색이 함께 크다» 고정 문구)", str(titles)))


def report(fails):
    if fails:
        print(f"selftest_rank 실패 {len(fails)}건")
        for n, why, d in fails:
            print(f"  ✗ {n}: {why}")
            if d:
                print(f"      {d.strip()[:300]}")
        return 1
    print("selftest_rank 통과 — 문서 예시 명령 · 컬럼 · 함정(T1·T2·T3·T4·T5·T6·T8) · 리포트 원장 · "
          "v0.1.1 회귀(패밀리 귀속 · 분야어 든 별칭 · 별칭 누락 의심) 검증 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
