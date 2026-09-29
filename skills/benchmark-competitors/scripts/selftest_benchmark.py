#!/usr/bin/env python3
"""이 스킬 고유 스크립트의 자가검증 — SKILL.md의 예시 명령을 합성 픽스처로 그대로 실행한다.

`selftest.py`는 공유 정본의 사본이라 이 스킬 고유 스크립트(plan_queries·brand_benchmark)를
모른다(사본을 고치면 드리프트가 난다). 그래서 이 파일이 따로 있다. STEP 3에서 둘 다 돌린다.

검사하는 것:
  1. SKILL.md 코드 블록의 `python3 scripts/…` 명령을 문서에 적힌 그대로 실행 — 전부 exit 0
  2. SKILL.md «산출물» 절의 컬럼 목록 = 스크립트가 실제로 쓴 CSV 헤더 (순서까지)
  3. 함정 처리가 실제로 도는가 — 표기변형 대표 1행 · 결측 월 계산 불가 · 마지막 달 제외 ·
     기저 게이트 · 제외 규칙 · 논브랜드 버킷 · 브랜드 합 점유율 합계 1 ·
     표기변형 하한(최댓값)≠상한(합) · 상한에서 순위가 뒤집히는 사전의 rank_flip·경고 (계약 T5)
  4. v1.2 회귀 (2026-09-28 실측 평가 결함):
     P0-1 게이트 밖 메시지 칸을 «0건 [실제 데이터]»로 경고하지 않는다 · 1단 실제 0이면 경고한다
     P1-1 2단 선별이 테마 칸을 예약하고 테마 커버리지를 보고한다
     P1-2 구글 지표 공유 행(오타 별칭 포함)을 하한·월 패널에서 한 번만 센다 · 결측 오타가 yoy를 지우지 않는다
     P1-3 브랜드명 단독 비중 컬럼 · P1-4 테마 정규식이 브랜드+모델 연결부에서 가짜 매칭하지 않는다
     P2-1 점유율 하한·상한을 범위(«~»)로 적지 않는다 · P2-2 review-config가 별칭을 brands에 넣는다
API 호출도 크레딧도 필요 없다.

    python3 scripts/selftest_benchmark.py        # 스킬 폴더에서 실행
"""
import csv, json, pathlib, re, shlex, subprocess, sys, tempfile
sys.dont_write_bytecode = True

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
MONTHS = [f"{y}-{m:02d}" for y in range(2022, 2027) for m in range(1, 13)]
MONTHS = MONTHS[MONTHS.index("2022-08"): MONTHS.index("2026-07") + 1]      # 48개월
REF, QUERIED = "2026-07", "2026-09-28"


def series(base, dip_last=False, missing=(), start=None):
    out = []
    for i, m in enumerate(MONTHS):
        if start and m < start:
            continue
        if m in missing:
            out.append({"month": m, "gg": 10, "nv_code": 1})           # total 키 없음
            continue
        v = base + (i % 12) * base // 20
        if dip_last and m == REF:
            v = int(v * 0.2)
        out.append({"month": m, "gg": v // 3, "nv": v - v // 3, "total": v})
    return out


def row(kw, base, vt=0.5, **kw2):
    s = series(base, **kw2)
    return {"keyword": kw, "ads_metrics": {"volume_total": sum(r.get("total", 0) for r in s[-12:]),
                                           "volume_avg": base, "volume_trend": vt},
            "monthly_volume": s}


def fixture(tmp):
    cfg = {"rules": {"변형치환": {"런닝": "러닝"}},
           "brands": ["나이키", "호카", "아식스"], "focal_brand": "호카",
           "brand_aliases": {"호카": ["hoka"]},
           "variants": {"호카": ["호카 본디", "호카 마하"], "나이키": ["나이키 페가수스"]},
           "exclude": ["주가"], "category_terms": ["러닝화"], "probe_modifiers": ["추천", "할인"],
           "themes": [{"name": "비교·추천", "pattern": "추천|vs"}, {"name": "가격·할인", "pattern": "할인|가격"}],
           "no_match": {"with_brand": "브랜드·제품명", "without_brand": "코어·일반"}}
    all_rows = [
        row("호카", 20000), row("호카 본디", 5000), row("호카본디", 4800),        # 표기변형 쌍
        row("나이키", 60000), row("나이키 페가수스", 8000, missing=("2025-11",)),  # 결측 월
        row("아식스", 150),                                                        # 게이트 미달
        row("나이키 vs 호카", 300),                                                # 복수 브랜드
        row("러닝화", 40000, dip_last=True), row("런닝화", 39000),                # 논브랜드 + 표기변형
        row("러닝화 추천", 12000), row("호카 추천", 900), row("나이키 추천", 1500),
        row("나이키 할인", 2000),                                                  # 호카 메시지 공백
        row("나이키 주가", 30000),                                                 # 제외 규칙
    ]
    stage1 = [{"keyword": r["keyword"], "ads_metrics": r["ads_metrics"]} for r in all_rows]
    stage1.append({"keyword": "호카 마하", "ads_metrics": {"volume_total": 50, "volume_avg": 4}})
    (tmp / "benchmark.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    (tmp / "stage1_ads_metrics.json").write_text(json.dumps({"data": stage1}, ensure_ascii=False), encoding="utf-8")
    (tmp / "stage2_all.json").write_text(json.dumps({"data": all_rows}, ensure_ascii=False), encoding="utf-8")


def flip_fixture(tmp):
    """상한에서 순위가 뒤집히는 사전 — 브룩스 1행(1,000) vs 뉴발란스 표기변형 3행(600씩).
    2단에는 plan_queries처럼 그룹 대표 1행만 들어가고, 1단에 모든 표기변형이 있다."""
    cfg = {"rules": {"변형치환": {"new balance": "뉴발란스", "newbalance": "뉴발란스"}},   # 치환은 공백 제거 전
           "brands": ["브룩스", "뉴발란스"], "focal_brand": "브룩스", "category_terms": ["러닝화"],
           "themes": [], "no_match": {"with_brand": "브랜드·제품명", "without_brand": "코어·일반"}}
    s2 = [row("브룩스 러닝화", 1000), row("뉴발란스 러닝화", 600), row("러닝화", 5000)]
    s1 = [{"keyword": r["keyword"], "ads_metrics": r["ads_metrics"]} for r in s2]
    s1 += [{"keyword": "뉴발란스러닝화", "ads_metrics": {"volume_total": s2[1]["ads_metrics"]["volume_total"]}},
           {"keyword": "new balance 러닝화", "ads_metrics": {"volume_total": s2[1]["ads_metrics"]["volume_total"]}}]
    (tmp / "flip.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    (tmp / "flip_stage1.json").write_text(json.dumps({"data": s1}, ensure_ascii=False), encoding="utf-8")
    (tmp / "flip_stage2.json").write_text(json.dumps({"data": s2}, ensure_ascii=False), encoding="utf-8")


def mrow(kw, gg, nv, cpc=0.3, ci=50, missing=()):
    """구글·네이버 분해와 광고 지표가 있는 행 — 구글 지표 공유 판정용."""
    mv = []
    for m in MONTHS:
        if m in missing:
            mv.append({"month": m, "gg": gg // 12, "nv_code": 1}); continue
        mv.append({"month": m, "gg": gg // 12, "nv": nv // 12, "total": gg // 12 + nv // 12})
    return {"keyword": kw, "ads_metrics": {"volume_total": gg + nv, "gg_volume_total": gg, "nv_volume_total": nv,
                                           "cpc": cpc, "competition_index": ci, "volume_trend": 0.1},
            "monthly_volume": mv}


def regressions(tmp):
    """v1.2 결함 회귀 — 각 결함을 재현하는 최소 픽스처."""
    f = []
    run = lambda *args: subprocess.run([sys.executable, *map(str, args)], cwd=tmp, capture_output=True, text=True)
    cfg = {"rules": {"변형치환": {}}, "brands": ["나이키", "호카"], "focal_brand": "호카",
           "brand_aliases": {"나이키": ["nike", "나이키도스"]}, "category_terms": ["러닝화"],
           "themes": [{"name": "가격·할인", "pattern": "할인|가격"}, {"name": "기능·쿠션", "pattern": "카본|쿠션"}],
           "no_match": {"with_brand": "브랜드·제품명", "without_brand": "코어·일반"}}
    (tmp / "r.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    s2 = [mrow("나이키", 60000, 240000, cpc=0.2, ci=40),
          mrow("nike", 12000, 3000), mrow("nikep", 12000, 0, missing=("2025-06", "2025-07")),   # 구글 공유 · 결측 오타
          mrow("나이키도스", 60000, 120, cpc=0.2, ci=40),                                     # 오타 별칭 · 구글 공유
          mrow("호카", 24000, 96000, cpc=0.5, ci=70), mrow("호카 본디", 3000, 9000, cpc=0.6, ci=80),
          mrow("나이키 할인", 1200, 4800, cpc=0.7, ci=90), mrow("러닝화", 30000, 90000, cpc=0.9, ci=99)]
    s1 = [{"keyword": d["keyword"], "ads_metrics": d["ads_metrics"]} for d in s2]
    s1.append({"keyword": "호카 할인", "ads_metrics": {"volume_total": 258, "gg_volume_total": 0, "nv_volume_total": 258}})
    (tmp / "r_s2.json").write_text(json.dumps({"data": s2}, ensure_ascii=False), encoding="utf-8")
    (tmp / "r_s1.json").write_text(json.dumps({"data": s1}, ensure_ascii=False), encoding="utf-8")
    p = run(HERE / "brand_benchmark.py", "r_s2.json", "--config", "r.json", "--category", "r", "--stage1", "r_s1.json",
            "--queried-at", QUERIED, "--outdir", "out_r")
    if p.returncode:
        return [("regression", "v1.2 회귀 픽스처 실행 실패", (p.stdout + p.stderr)[-300:])]
    md = (tmp / "out_r" / "gap_alert_r.md").read_text(encoding="utf-8")
    if "메시지 공백: «가격·할인»" in md:
        f.append(("P0-1", "게이트 밖(1단 258건) 메시지 칸을 메시지 공백으로 경고했다", ""))
    if "게이트 밖(1단 12개월 258건" not in md:
        f.append(("P0-1", "게이트 밖 칸이 1단 볼륨과 함께 판정 보류로 표시되지 않았다", ""))
    tc = {(r["theme"], r["brand"]): r for r in read(tmp / "out_r" / "theme_coverage_r.csv")}
    if not tc.get(("가격·할인", "호카"), {}).get("cell_status", "").startswith("게이트 밖"):
        f.append(("P0-1", "theme_coverage의 cell_status가 게이트 밖이 아니다", str(tc.get(("가격·할인", "호카")))))
    # 1단에서 실제 0이면 경고한다
    s1z = [dict(x) for x in s1]
    s1z[-1] = {"keyword": "호카 할인", "ads_metrics": {"volume_total": 0}}
    (tmp / "r_s1z.json").write_text(json.dumps({"data": s1z}, ensure_ascii=False), encoding="utf-8")
    run(HERE / "brand_benchmark.py", "r_s2.json", "--config", "r.json", "--category", "rz", "--stage1", "r_s1z.json",
        "--queried-at", QUERIED, "--outdir", "out_rz")
    mdz = (tmp / "out_rz" / "gap_alert_rz.md").read_text(encoding="utf-8")
    if "메시지 공백: «가격·할인» 수식어 검색에 호카 1단 12개월 0건" not in mdz:
        f.append(("P0-1", "1단 실제 0인 메시지 칸을 경고하지 않았다", ""))
    # P1-2 · P2-6
    bd = {r["brand"]: r for r in read(tmp / "out_r" / "bounds_r.csv")}
    lo_exp = 300000 + 15000 + 6000    # 나이키(+나이키도스) · nike(+nikep) · 나이키 할인 — 묶음 최댓값의 합
    if bd["나이키"]["volume_12m_lo"] != str(lo_exp):
        f.append(("P1-2", f"구글 공유 행을 하한에서 여러 번 셌다: {bd['나이키']['volume_12m_lo']} ≠ {lo_exp}", ""))
    hi_exp = 60000 + 240000 + 120 + 12000 + 3000 + 0 + 1200 + 4800
    if bd["나이키"]["volume_12m_hi"] != str(hi_exp):
        f.append(("P1-2", f"상한이 구글 공유 1회 + 네이버 합이 아니다: {bd['나이키']['volume_12m_hi']} ≠ {hi_exp}", ""))
    b = read(tmp / "out_r" / "benchmark_r.csv")
    nk = next(r for r in b if r["month"] == "2025-07" and r["brand"] == "나이키")
    if nk["month_complete"] != "1" or not nk["sov_brandset"]:
        f.append(("P2-6", "결측 이력이 있는 비대표 오타 변형이 월 패널을 비웠다", str(nk)))
    km = {r["keyword"]: r for r in read(tmp / "out_r" / "keyword_map_r.csv")}
    if km["nikep"]["representative"] != "0" or km["나이키도스"]["dup_basis"] != "구글 지표 공유":
        f.append(("P1-2", "구글 공유 행이 대표로 남았거나 dup_basis가 없다",
                  str({k: (km[k]["representative"], km[k]["dup_basis"]) for k in ("nikep", "나이키도스")})))
    # P1-3
    if bd["나이키"]["brand_alone_share_lo"] in ("", None) or float(bd["나이키"]["brand_alone_share_lo"]) < 0.9:
        f.append(("P1-3", "브랜드명 단독 비중이 없거나 틀렸다", bd["나이키"].get("brand_alone_share_lo")))
    if "브랜드명 단독 비중" not in md:
        f.append(("P1-3", "gap_alert에 브랜드명 단독 비중이 없다", ""))
    # P1-4
    if km["호카 본디"]["theme_primary"] == "기능·쿠션":
        f.append(("P1-4", "«호카 본디»가 공백 제거 후 «카본»에 걸렸다", ""))
    sys.path.insert(0, str(HERE))
    from bench_common import Config
    c = Config(tmp / "r.json")
    if c.theme("호카본디", "호카") != "브랜드·제품명" or c.theme("나이키 카본 러닝화", "나이키") != "기능·쿠션":
        f.append(("P1-4", "브랜드 토큰 가림·토큰 경계 판정이 틀렸다", ""))
    # P2-1
    if re.search(r"하한 \d+\.\d% ~ 상한", md):
        f.append(("P2-1", "점유율 하한·상한을 범위(«~»)로 적었다", ""))
    # P1-1 · P2-2 · 미반환
    (tmp / "r_req.json").write_text(json.dumps(["나이키", "호카 할인", "호카 쿠션"], ensure_ascii=False), encoding="utf-8")
    p = run(HERE / "plan_queries.py", "stage2", "r_s1.json", "--config", "r.json", "--requested", "r_req.json",
            "--min-volume", "100000", "--max-k2", "5", "-o", "r_pick.json")
    picked = json.load(open(tmp / "r_pick.json", encoding="utf-8")) if not p.returncode else []
    if "나이키 할인" not in picked or len(picked) > 5:
        f.append(("P1-1", f"테마 예약이 없다(볼륨 게이트만 적용): {picked}", p.stdout[-300:]))
    if "테마 커버리지" not in p.stdout or "미반환" not in p.stdout or "호카 쿠션" not in p.stdout:
        f.append(("P1-1", "stage2가 테마 커버리지·미반환을 보고하지 않는다", p.stdout[-300:]))
    if "nikep" in picked or "나이키도스" in picked:
        f.append(("P1-2", "구글 공유 행이 2단 대상에 들어갔다", str(picked)))
    run(HERE / "plan_queries.py", "review-config", "--config", "r.json", "-o", "r_review.json")
    rv = json.load(open(tmp / "r_review.json", encoding="utf-8"))
    if "nike" not in rv.get("brands", []):
        f.append(("P2-2", "review-config가 별칭을 brands에 넣지 않았다", str(rv.get("brands"))))
    return f


def doc_commands():
    """SKILL.md bash 블록에서 `python3 scripts/…` 명령을 뽑는다 (줄 이음 `\\` 처리)."""
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
    """«산출물» 절에서 파일명 줄 다음의 들여쓴 컬럼 줄을 읽는다."""
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


def read(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)
        cmds = doc_commands()
        if not any("brand_benchmark.py" in c for c in cmds) or not any("plan_queries.py" in c for c in cmds):
            fails.append(("SKILL.md", "예시 명령을 찾지 못함 (plan_queries·brand_benchmark)", ""))
        for c in cmds:
            argv = shlex.split(c)
            argv[1] = str(SKILL / argv[1])
            p = subprocess.run([sys.executable] + argv[1:], cwd=tmp, capture_output=True, text=True)
            if p.returncode:
                fails.append((argv[1].split("/")[-1], f"문서 예시가 실패한다: {c[:80]}", (p.stdout + p.stderr)[-300:]))

        out = tmp / "out"
        cat = "running-shoes-kr"
        files = {"benchmark": out / f"benchmark_{cat}.csv", "message_share": out / f"message_share_{cat}.csv",
                 "keyword_map": out / f"keyword_map_{cat}.csv", "bounds": out / f"bounds_{cat}.csv",
                 "theme_coverage": out / f"theme_coverage_{cat}.csv"}
        if all(p.is_file() for p in files.values()):
            for name, want in doc_columns().items():
                got = next(csv.reader(open(files[name], encoding="utf-8-sig")))
                if got != want:
                    fails.append((name, "문서 컬럼 ≠ 실제 헤더", f"문서 {want}\n실제 {got}"))
            b = read(files["benchmark"])
            end = {r["analysis_end_month"] for r in b}
            if end != {"2026-06"}:
                fails.append(("brand_benchmark", f"마지막 달 제외 기본값 위반: 분석 종료 월 {end}", ""))
            nov = [r for r in b if r["month"] == "2025-11"]
            if not nov or any(r["month_complete"] != "0" or r["sov_brandset"] for r in nov):
                fails.append(("brand_benchmark", "결측 월(2025-11)의 점유율을 계산해버렸다 (0 합산)", ""))
            h = next(r for r in b if r["month"] == "2026-06" and r["brand"] == "호카")
            i = MONTHS.index("2026-06")      # 호카 + 호카 본디(대표) + 호카 추천 — 호카본디는 빠져야 한다
            exp = sum(series(v)[i]["total"] for v in (20000, 5000, 900))
            if int(h["volume"]) != exp:
                fails.append(("brand_benchmark", f"표기변형 이중 계상: 호카 {h['volume']} ≠ {exp}", ""))
            a = next(r for r in b if r["month"] == "2026-06" and r["brand"] == "아식스")
            if a["yoy_gated_out"] != "1" or a["yoy_3m"]:
                fails.append(("brand_benchmark", "기저 게이트 미달 브랜드에 yoy_3m이 나왔다", ""))
            s = sum(float(r["sov_brandset"]) for r in b if r["month"] == "2026-06" and r["sov_brandset"])
            if abs(s - 1) > 1e-3:
                fails.append(("brand_benchmark", f"브랜드 합 점유율 합계 {s:.4f} ≠ 1", ""))
            nb = next(r for r in b if r["month"] == "2026-06" and r["bucket_type"] == "nonbrand")
            if not nb["sov_category"]:
                fails.append(("brand_benchmark", "논브랜드 버킷의 카테고리 점유율이 없다 (T8)", ""))
            if any(r["series_basis"] != "대표 행(하한 기준)" for r in b):
                fails.append(("brand_benchmark", "월 패널에 series_basis(대표 행·하한 기준) 표시가 없다", ""))
            bd = {r["brand"]: r for r in read(files["bounds"])}
            h12 = bd.get("호카", {})
            # 호카 = 호카(20000) + 호카 본디 그룹 + 호카 추천 + 호카 마하(1단 꼬리 50)
            vt = lambda base: sum(r.get("total", 0) for r in series(base)[-12:])
            lo_exp = vt(20000) + vt(5000) + vt(900) + 50
            hi_exp = lo_exp + vt(4800)
            if h12.get("volume_12m_lo") != str(lo_exp) or h12.get("volume_12m_hi") != str(hi_exp):
                fails.append(("brand_benchmark", f"호카 12개월 하한·상한 {h12.get('volume_12m_lo')}~"
                              f"{h12.get('volume_12m_hi')} ≠ {lo_exp}~{hi_exp} (lo≠hi 픽스처)", ""))
            for k in ("sov_brandset_12m", "sov_category_12m"):
                if abs(sum(float(r[k + "_hi"]) for r in bd.values() if r[k + "_hi"]) -
                       (1 if k == "sov_category_12m" else sum(float(r[k + "_hi"]) for r in bd.values()
                                                             if r["bucket_type"] == "brand"))) > 1e-3:
                    fails.append(("brand_benchmark", f"{k}_hi 가 상한 분모로 계산되지 않았다", ""))
            if abs(sum(float(r["sov_brandset_12m_hi"]) for r in bd.values() if r["sov_brandset_12m_hi"]) - 1) > 1e-3:
                fails.append(("brand_benchmark", "상한 점유율(브랜드 합 대비) 합계 ≠ 1 — 분모를 섞었다", ""))
            if any(r["label"] != "추정" for r in bd.values()):
                fails.append(("brand_benchmark", "bounds의 label이 4-Label(추정)이 아니다", ""))
            if [bd[x]["rank_lo"] for x in ("나이키", "호카", "아식스")] != ["1", "2", "3"] or \
                    any(bd[x]["rank_flip"] != "no" for x in ("나이키", "호카", "아식스")):
                fails.append(("brand_benchmark", "뒤집힘 없는 사전에서 rank_lo·rank_flip이 틀렸다", ""))
            km = read(files["keyword_map"])
            kh = next((r for r in km if r["keyword"] == "호카 본디"), {})
            if kh.get("group_rows") != "2" or kh.get("group_volume_lo") == kh.get("group_volume_hi"):
                fails.append(("brand_benchmark", "keyword_map의 그룹 하한·상한이 틀렸다", ""))
            if not any(r["keyword"] == "나이키 주가" and r["excluded"] for r in km):
                fails.append(("brand_benchmark", "제외 규칙이 keyword_map에 표시되지 않음", ""))
            md = (out / f"gap_alert_{cat}.md").read_text(encoding="utf-8")
            for need in ("데이터 기준월 2026-07", "조회일 2026-09-28", "지연", "마지막 달", "메시지 공백", "[데이터 공백]",
                         "하한", "상한", "대표 행(하한 기준)"):
                if need not in md:
                    fails.append(("brand_benchmark", f"gap_alert에 «{need}» 없음", ""))
            if "[파생]" in md:
                fails.append(("brand_benchmark", "gap_alert에 4-Label 밖 라벨 [파생]이 있다", ""))
        else:
            fails.append(("brand_benchmark", "산출 파일이 만들어지지 않음", ""))

        # 상한에서 순위가 뒤집히는 사전 — 순위는 하한 기준, 뒤집힘은 컬럼과 경고로
        flip_fixture(tmp)
        p = subprocess.run([sys.executable, str(HERE / "brand_benchmark.py"), "flip_stage2.json", "--config", "flip.json",
                            "--category", "flip", "--stage1", "flip_stage1.json", "--queried-at", QUERIED,
                            "--outdir", "out_flip"], cwd=tmp, capture_output=True, text=True)
        fb = tmp / "out_flip" / "bounds_flip.csv"
        if p.returncode or not fb.is_file():
            fails.append(("brand_benchmark", "뒤집힘 픽스처 실행 실패", (p.stdout + p.stderr)[-300:]))
        else:
            f = {r["brand"]: r for r in read(fb)}
            if not (f["브룩스"]["rank_lo"] == "1" and f["뉴발란스"]["rank_lo"] == "2"
                    and f["브룩스"]["rank_hi"] == "2" and f["뉴발란스"]["rank_hi"] == "1"
                    and f["브룩스"]["rank_flip"] == "yes" and f["뉴발란스"]["rank_flip"] == "yes"):
                fails.append(("brand_benchmark", "상한에서 순위가 뒤집혔는데 rank_flip이 표시되지 않았다",
                              str({k: (v["rank_lo"], v["rank_hi"], v["rank_flip"]) for k, v in f.items()})))
            if int(f["뉴발란스"]["volume_12m_hi"]) != 3 * int(f["뉴발란스"]["volume_12m_lo"]):
                fails.append(("brand_benchmark", "뉴발란스 표기변형 3행의 상한이 하한의 3배가 아니다", ""))
            md = (tmp / "out_flip" / "gap_alert_flip.md").read_text(encoding="utf-8")
            if "순위 뒤집힘" not in md or "⚠️ 뒤집힘" not in md:
                fails.append(("brand_benchmark", "gap_alert에 순위 뒤집힘 경고가 없다", ""))

        fails += regressions(tmp)

        s2 = tmp / "stage2_keywords.json"
        if s2.is_file():
            picked = json.load(open(s2, encoding="utf-8"))
            if "호카본디" in picked or "호카 마하" in picked:
                fails.append(("plan_queries", f"표기변형·꼬리가 2단에 들어갔다: {picked}", ""))

    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {detail.strip()[:300]}")
        return 1
    print(f"통과 — 문서 예시 명령 {len(cmds)}건 실행 · 컬럼 대조 · 함정 처리 검증 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
