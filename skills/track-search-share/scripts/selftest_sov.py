#!/usr/bin/env python3
"""이 스킬 고유 스크립트(sov_calc.py) 계약 검증 — SKILL.md의 예시 명령을 그대로 돌린다.

공유 `selftest.py`는 공유 스크립트(check_refmonth·trend_yoy·build_universe·coverage_curve)만
검사한다. 점유 계산은 이 스킬에만 있으므로 여기서 따로 검사한다. 둘 다 통과해야 한다:

    python3 scripts/selftest.py && python3 scripts/selftest_sov.py

합성 픽스처(캡슐 커피머신 KR 모양)만 쓴다 — API 호출도 크레딧도 필요 없다.
픽스처는 이 스킬이 막아야 하는 함정을 일부러 전부 밟는다:
  표기변형 이중 계상 · 카테고리 밖 브랜드 단독 검색 · 논브랜드 · 다중 브랜드 비교 검색 ·
  절단된 라운드 · 결측 월 · 마지막 달 급락 · 기저 미달 브랜드 · 엉뚱한 volume_trend ·
  하한(그룹 최댓값)≠상한(그룹 합)인 표기변형 그룹 · 상한에서 브랜드 순위가 뒤집히는 사전(계약 T5)
"""
import csv, json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "sov_calc.py"
MONTHS = [f"{y}-{m:02d}" for y in (2023, 2024, 2025, 2026) for m in range(1, 13)]
LAST = "2026-07"

# SKILL.md 산출물 절의 컬럼 목록과 같아야 한다
KCOLS = ["keyword", "canonical", "dup_of", "dup_basis", "group_rows", "group_rep", "volume_total",
         "group_volume_lo", "group_volume_hi", "scope", "brands_hit", "brand_weight", "matched_by",
         "series_target", "api_volume_trend_do_not_use"]
BCOLS = ["bucket", "keywords", "volume_12m_lo", "volume_12m_hi", "sov_pct_lo", "sov_pct_hi",
         "sov_pct_branded_only_lo", "sov_pct_branded_only_hi", "rank_lo", "rank_hi", "rank_flip",
         "multi_brand_volume_lo", "multi_brand_volume_hi", "model_only_volume_lo", "model_only_volume_hi",
         "brand_only_volume", "outside_comparison_volume", "variant_list_truncated", "truncated_rounds",
         "series_keywords", "series_basis", "window_volume_recent", "window_volume_prev_year",
         "share_recent_pct", "share_prev_year_pct", "share_change_pp", "share_change_pp_other_mode",
         "share_change_provisional", "share_change_status", "data_ref_month", "window_end", "label"]


def am(kw, vol, trend=0.0, cpc=None, ci=None):
    m = {"volume_total": vol, "volume_avg": vol // 12, "volume_trend": trend}
    if cpc is not None:   # CPC·경쟁도가 있어야 «지표 일치» 중복 판정 대상이 된다
        m.update({"cpc": cpc, "competition_index": ci, "gg_volume_total": vol // 3, "nv_volume_total": vol - vol // 3})
    return {"keyword": kw, "ads_metrics": m}


def series(kw, base, grow=0.0, missing=(), dip_last=False):
    out = []
    for i, m in enumerate(MONTHS[: MONTHS.index(LAST) + 1]):
        if m in missing:
            out.append({"month": m, "gg": 10, "nv_code": 1})     # total 키 없음
            continue
        v = int(base * (1 + grow) ** (i / 12))
        if dip_last and m == LAST:
            v = int(v * 0.2)
        out.append({"month": m, "gg": v // 3, "nv": v - v // 3, "total": v})
    return {"keyword": kw, "monthly_volume": out}


def fixture(t: pathlib.Path):
    w = lambda n, o: (t / n).write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
    w("themes.json", {
        "rules": {"변형치환": {"nespresso": "네스프레소", "dolcegusto": "돌체구스토", "커피메이커": "커피머신"}},
        "themes": [],
        "brands": ["네스프레소", "돌체구스토", "드롱기"],
        # exclude 수정 회귀: `캡슐(호환)`이 `캡슐 호환 커피머신`(머신)까지 자르던 것 → 호환캡슐·캡슐호환$
        "category": {"name": "캡슐 커피머신", "pattern": "머신|커피기계",
                     "exclude": "캡슐$|캡슐호환$|호환캡슐|캡슐(추천|가격)"},
        # 일리: brand_models에만 있는 짧은 브랜드 (P1-2 KeyError 회귀)
        "brand_models": {"네스프레소": "버츄오|시티즈|에센자", "일리": "^일리"},
        "no_match": {"with_brand": "브랜드·제품명", "without_brand": "코어·일반"},
    })
    w("metrics.json", {"data": [
        am("네스프레소 커피머신", 60000, trend=-0.9),   # volume_trend는 엉뚱한 값 — 쓰이면 안 된다
        am("nespresso 커피머신", 60000),                # 표기변형 — 같은 그룹 (지표 없음 → 중복 판정 안 함)
        am("버츄오", 30000),                            # 모델명만 — 품목 미특정 → model_only (P1-3)
        am("버츄오 머신", 5000),                        # 모델명 + 카테고리 단어 → 네스프레소
        am("네스프레소", 200000),                       # 브랜드 단독 → 카테고리 밖
        am("네스프레소 캡슐", 90000),                   # 카테고리 exclude
        am("캡슐 호환 커피머신", 7000),                  # exclude에 걸리면 안 된다 → 논브랜드
        am("돌체구스토 머신", 20000),
        am("드롱기 커피머신", 1500),                    # 기저 미달 후보
        am("네스프레소 돌체구스토 머신 비교", 8000),     # 다중 브랜드
        am("커피머신 추천", 50000),                     # 논브랜드
        am("캡슐 커피머신", 40000),                     # 논브랜드
        am("일리 커피머신", 2500),                      # brand_models 전용 브랜드
        # 어순 변형 — 지표가 완전히 같다 = 같은 검색의 중복 (P0-1). 한 번만 센다
        am("커피머신 가정용 추천", 12000, cpc=1.0, ci=80),
        am("가정용 커피머신 추천", 12000, cpc=1.0, ci=80),  # 요청하지 않은 행
    ]})
    w("requested.json", ["네스프레소 커피머신", "버츄오", "버츄오 머신", "네스프레소", "네스프레소 캡슐",
                         "캡슐 호환 커피머신", "돌체구스토 머신", "드롱기 커피머신", "네스프레소 돌체구스토 머신 비교",
                         "커피머신추천", "캡슐 커피머신", "일리 커피머신", "커피머신 가정용 추천",
                         "브레빌 커피머신"])   # 커피머신추천 → 표기 바뀌어 반환 · 브레빌 커피머신 → 미반환
    w("if_nespresso.json", {"data": ["네스프레소 커피머신", "nespresso 커피머신", "버츄오"]})  # limit 3 → 절단
    w("if_generic.json", {"data": ["커피머신 추천", "캡슐 커피머신"]})                           # limit 100 → 미절단
    w("if_generic2.json", {"data": ["돌체구스토 머신", "커피머신 추천"]})                        # limit 2 → 절단
    w("series.json", {"data": [
        series("네스프레소 커피머신", 5000, grow=0.10, dip_last=True),
        series("버츄오 머신", 400, grow=0.30),
        series("돌체구스토 머신", 1700, grow=-0.10, missing=("2025-05",)),   # 비교 창 결측
        series("드롱기 커피머신", 120),
        series("네스프레소 돌체구스토 머신 비교", 700),
        series("커피머신 추천", 4200),
        series("캡슐 커피머신", 3300),
        series("캡슐 호환 커피머신", 600),
        series("커피머신 가정용 추천", 1000),
    ]})


def flip_fixture(t: pathlib.Path):
    """상한에서 순위가 뒤집히는 사전 — 브레빌 1행 1,000 vs 필립스 표기변형 3행 600씩.
    하한: 브레빌 1,000 > 필립스 600 · 상한: 필립스 1,800 > 브레빌 1,000."""
    w = lambda n, o: (t / n).write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
    w("themes_flip.json", {"rules": {"변형치환": {"philips": "필립스", "커피메이커": "커피머신"}}, "themes": [],
                           "brands": ["브레빌", "필립스"], "category": {"name": "커피머신", "pattern": "머신"}})
    w("metrics_flip.json", {"data": [am("브레빌 커피머신", 1000), am("필립스 커피머신", 600),
                                     am("philips 커피머신", 600), am("필립스 커피메이커", 600),
                                     am("커피머신", 5000)]})


def run(cmd, t):
    p = subprocess.run([sys.executable, str(SCRIPT)] + cmd, cwd=t, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def rows(path):
    with open(path, encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def main():
    fails = []
    ok = lambda cond, what: None if cond else fails.append(what)
    R = ["--round", "네스프레소=if_nespresso.json@3", "--round", "일반=if_generic.json@100,if_generic2.json@2"]
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        fixture(t)
        # 0단 — SKILL.md STEP 3 수집 목록 (브랜드 단독 N + 비교 N(N−1)/2)
        rc, out = run(["--themes", "themes.json", "--probe-terms", "probe_terms.json"], t)
        pt = json.load(open(t / "probe_terms.json", encoding="utf-8")) if rc == 0 else []
        ok(len(pt) == 4 + 6 and "네스프레소" in pt and "네스프레소 vs 돌체구스토" in pt and "일리" in pt,
           f"--probe-terms가 브랜드 단독 N + 비교 N(N−1)/2를 쓰지 않았다: {pt}")

        # 1단 — SKILL.md STEP 3 예시 그대로 (limit만 픽스처 크기에 맞춤)
        rc, out = run(["--metrics", "metrics.json", "--themes", "themes.json", "--requested", "requested.json"] + R +
                      ["--series-gate", "1000", "--series-targets", "series_targets.json",
                       "-o", "sov_keywords.csv", "--brand-out", "sov_brands.csv"], t)
        if rc:
            print(f"✗ 1단 예시 명령 비정상 종료\n{out[-400:]}"); return 1
        kf, kr = rows(t / "sov_keywords.csv")
        bf, br = rows(t / "sov_brands.csv")
        ok(kf == KCOLS, f"키워드 CSV 컬럼이 문서와 다르다: {kf}")
        ok(bf == BCOLS, f"버킷 CSV 컬럼이 문서와 다르다: {bf}")
        k = {r["keyword"]: r for r in kr}
        b = {r["bucket"]: r for r in br}
        # P0-1 — 어순 변형 + 지표 완전 일치 = 같은 검색의 중복 → 한 번만
        ok(k["커피머신 가정용 추천"]["dup_of"] == "가정용 커피머신 추천" and k["커피머신 가정용 추천"]["dup_basis"] == "지표 일치"
           and k["커피머신 가정용 추천"]["group_volume_lo"] == "", "지표가 같은 어순 변형이 중복으로 걸러지지 않았다(P0-1)")
        ok("같은 검색의 중복 [추정]: 1행 · 12,000건" in out, "중복 제외 행·볼륨이 요약에 없다")
        ok(k["네스프레소"]["scope"] == "out_of_category", "브랜드 단독 검색이 카테고리 밖으로 분리되지 않았다")
        ok(k["네스프레소 캡슐"]["scope"] == "excluded", "category.exclude가 적용되지 않았다")
        ok(k["캡슐 호환 커피머신"]["scope"] == "generic", "exclude가 머신 검색(캡슐 호환 커피머신)까지 잘랐다(P2)")
        # P1-3 — 모델명만(카테고리 단어 없음)은 분리, 모델명 + 카테고리 단어는 브랜드
        ok(k["버츄오"]["scope"] == "model_only" and k["버츄오"]["brands_hit"] == "네스프레소",
           "카테고리 단어 없는 모델명 검색이 카테고리 안으로 들어갔다(P1-3)")
        ok(k["버츄오 머신"]["scope"] == "brand" and k["버츄오 머신"]["matched_by"] == "model",
           "모델명 + 카테고리 단어 검색이 브랜드로 귀속되지 않았다")
        ok(b["네스프레소"]["model_only_volume_lo"] == "30000", "모델명만 볼륨이 따로 보고되지 않았다")
        # P1-2 — brand_models에만 있는 짧은 브랜드
        ok("일리" in b and b["일리"]["volume_12m_lo"] == "2500" and k["일리 커피머신"]["brands_hit"] == "일리",
           "brand_models 전용 브랜드가 버킷이 되지 않았다(P1-2 KeyError 회귀)")
        ok(k["커피머신 추천"]["scope"] == "generic", "논브랜드가 분리되지 않았다")
        ok(k["네스프레소 돌체구스토 머신 비교"]["scope"] == "multi_brand"
           and k["네스프레소 돌체구스토 머신 비교"]["brand_weight"] == "0.5", "다중 브랜드 균등 분배 실패")
        grp = [k["네스프레소 커피머신"], k["nespresso 커피머신"]]
        ok(len({r["canonical"] for r in grp}) == 1 and sum(int(r["group_rep"]) for r in grp) == 1,
           "표기변형이 한 그룹으로 묶이지 않았거나 대표 행이 하나가 아니다")
        ok(all(r["group_volume_lo"] == "60000" and r["group_volume_hi"] == "120000" for r in grp),
           "표기변형 그룹의 하한(최댓값)·상한(합)이 틀렸다 — lo≠hi 픽스처")
        # 하한: 네스프레소 = 60000 + 5000 + 8000/2 = 69000 · 분모 = 69000 + 24000 + 1500 + 2500 + 109000 = 206000
        # 상한: 네스프레소 = 120000 + 5000 + 4000 = 129000 · 분모 = 266000 (중복 12,000은 어느 쪽에도 없다)
        ok(b["네스프레소"]["volume_12m_lo"] == "69000", f"네스프레소 하한 {b['네스프레소']['volume_12m_lo']} ≠ 69000")
        ok(b["네스프레소"]["volume_12m_hi"] == "129000", "네스프레소 상한(그룹 합)이 틀렸다")
        ok(b["네스프레소"]["sov_pct_lo"] == "33.5", f"SoV 하한 {b['네스프레소']['sov_pct_lo']} ≠ 33.5")
        ok(b["네스프레소"]["sov_pct_hi"] == "48.5",
           f"SoV 상한 {b['네스프레소']['sov_pct_hi']} ≠ 48.5 (상한 분모 266000으로 따로 계산해야 한다)")
        ok(b["네스프레소"]["sov_pct_branded_only_lo"] == "71.1", "브랜드 간 점유(하한)가 틀렸다")
        ok(b["네스프레소"]["sov_pct_branded_only_hi"] == "82.2", "브랜드 간 점유(상한)가 틀렸다")
        ok(b["논브랜드(일반)"]["sov_pct_lo"] == "52.9" and b["논브랜드(일반)"]["sov_pct_hi"] == "41.0",
           "논브랜드 비중(하한·상한)이 틀렸다")
        ok(b["네스프레소"]["multi_brand_volume_lo"] == "4000" and b["네스프레소"]["multi_brand_volume_hi"] == "4000",
           "다중 브랜드 분배 볼륨이 틀렸다")
        ok(b["카테고리 밖(분모 제외)"]["sov_pct_lo"] == "" and b["카테고리 밖(분모 제외)"]["volume_12m_lo"] == "200000",
           "카테고리 밖 버킷이 분모에 들어갔거나 수집한 브랜드 단독 볼륨이 없다")
        # P1-1 — 수집하지 않은 브랜드 단독 검색은 0이 아니라 «미수집»
        ok(b["네스프레소"]["brand_only_volume"] == "200000" and b["돌체구스토"]["brand_only_volume"] == "미수집",
           "브랜드 단독 검색 수집 상태(수집·미수집)가 구분되지 않았다(P1-1)")
        ok([b[x]["rank_lo"] for x in ("네스프레소", "돌체구스토", "일리", "드롱기")] == ["1", "2", "3", "4"]
           and all(b[x]["rank_flip"] == "no" for x in ("네스프레소", "돌체구스토", "드롱기")),
           "뒤집힘이 없는 사전에서 순위·rank_flip이 틀렸다")
        ok("하한 기준" in out and "상한 기준" in out and "[추정]" in out and "[파생]" not in out,
           "요약에 하한·상한 두 값과 [추정] 라벨이 없거나 4-Label 밖 라벨이 있다")
        # 절단 — 호출 단위 · 잘린 라운드가 공급한 버킷 전부 (P2)
        ok(b["네스프레소"]["variant_list_truncated"] == "yes", "limit에 붙은 라운드를 절단으로 기록하지 않았다")
        ok(b["돌체구스토"]["variant_list_truncated"] == "yes" and b["돌체구스토"]["truncated_rounds"] == "일반",
           "잘린 일반 라운드가 공급한 브랜드 버킷을 절단으로 표시하지 않았다")
        ok(b["논브랜드(일반)"]["variant_list_truncated"] == "yes", "잘린 호출이 있는 일반 라운드를 절단으로 기록하지 않았다")
        ok(b["드롱기"]["variant_list_truncated"] == "미확장", "확장하지 않은 브랜드 표시가 틀렸다")
        ok("절단" in out and "보류" in out, "절단 경고가 출력되지 않았다")
        ok("데이터 공백" in out, "1단에서 기준월 미확인을 [데이터 공백]으로 표시하지 않았다")
        # 요청↔응답 대조 (P2 · 계약 §1)
        ok("미반환 1개" in out and "브레빌 커피머신" in out, "요청했는데 반환되지 않은 키워드가 미반환으로 나오지 않았다")
        ok("커피머신추천 → 커피머신 추천" in out, "표기가 바뀌어 반환된 키워드를 canonical로 대조하지 않았다")
        ok("요청하지 않은 행 1개" in out and "과금 1 × 14 = 14크레딧" in out,
           "요청하지 않은 행 또는 요청 키워드 수 기준 과금이 출력되지 않았다")
        tg = json.load(open(t / "series_targets.json", encoding="utf-8"))
        ok("드롱기 커피머신" in tg and "네스프레소" not in tg and "nespresso 커피머신" not in tg
           and "버츄오" not in tg and "커피머신 가정용 추천" not in tg,
           f"2단 대상 목록이 틀렸다(카테고리 밖·모델명만·비대표·중복 행 제외, 게이트 적용): {tg}")

        # 2단 — SKILL.md STEP 6 예시 그대로
        rc, out = run(["--metrics", "metrics.json", "--themes", "themes.json", "--requested", "requested.json",
                       "--series", "series.json"] + R + ["--gate", "3000", "--queried-at", "2026-09-28",
                       "-o", "sov_keywords.csv", "--brand-out", "sov_brands.csv"], t)
        if rc:
            print(f"✗ 2단 예시 명령 비정상 종료\n{out[-400:]}"); return 1
        _, br = rows(t / "sov_brands.csv")
        b = {r["bucket"]: r for r in br}
        ok(b["네스프레소"]["variant_list_truncated"] == "yes", "2단에서 절단 기록이 사라졌다")
        ok(b["네스프레소"]["data_ref_month"] == LAST, "기준월이 시계열 최신 월이 아니다")
        ok(b["네스프레소"]["window_end"] == "2026-06", "마지막 달이 기본 제외되지 않았다")
        ok("지연 약 2개월" in out, "기준월/조회일/지연 3종이 출력되지 않았다")   # 픽스처 기준월 2026-07에서 나온 값
        ok(b["돌체구스토"]["share_change_status"] == "기저 미달" and b["돌체구스토"]["share_change_pp"] == "",
           "결측 월 키워드가 0으로 합산됐거나 기저 미달 처리가 안 됐다")
        ok("계산 불가: 1그룹" in out, "결측 월 계산 불가 건수가 출력되지 않았다")
        ok("시계열 응답이 없는 그룹 1개" in out, "2단 대상인데 시계열이 없는 그룹을 [데이터 공백]으로 적지 않았다")
        ok(b["드롱기"]["share_change_status"] == "기저 미달", "기저 게이트가 적용되지 않았다")
        ok(b["네스프레소"]["share_change_status"] == "ok" and b["네스프레소"]["share_change_pp"] not in ("", "0.0"),
           "게이트 통과 브랜드의 점유 변화가 안 나왔다")
        # P2 — 마지막 달 포함·제외 부호가 다르면 자동 «잠정»
        ok(b["네스프레소"]["share_change_pp_other_mode"] not in ("", b["네스프레소"]["share_change_pp"])
           and b["네스프레소"]["share_change_provisional"] == "잠정" and "«잠정»" in out,
           "두 모드의 부호가 다른데 «잠정»이 자동 표시되지 않았다")
        ok(b["네스프레소"]["series_basis"] == "대표 행(하한 기준)" and "상한 기준 월 시계열은 없다" in out,
           "점유 변화가 대표 행(하한 기준)이라는 표시가 없다")
        rc, out2 = run(["--metrics", "metrics.json", "--themes", "themes.json", "--series", "series.json",
                        "--include-last-month", "-o", "k2.csv", "--brand-out", "b2.csv"], t)
        _, br2 = rows(t / "b2.csv")
        b2 = {r["bucket"]: r for r in br2}
        ok(rc == 0 and b2["네스프레소"]["window_end"] == LAST
           and b2["네스프레소"]["share_change_pp"] == b["네스프레소"]["share_change_pp_other_mode"],
           "--include-last-month가 창을 바꾸지 않거나 반대 모드 값과 다르다")
        # --include-model-only — 모델명만 검색을 포함하면 브랜드 볼륨에 들어간다
        rc, out4 = run(["--metrics", "metrics.json", "--themes", "themes.json", "--include-model-only",
                        "-o", "k4.csv", "--brand-out", "b4.csv"], t)
        _, br4 = rows(t / "b4.csv")
        b4 = {r["bucket"]: r for r in br4}
        ok(rc == 0 and b4["네스프레소"]["volume_12m_lo"] == "99000" and "포함함 [가정]" in out4,
           "--include-model-only가 모델명만 검색을 포함하지 않았다")

        # 상한에서 순위가 뒤집히는 사전 — 순위는 하한 기준, 뒤집힘은 표시 · 수집 안 한 버킷은 «미수집»
        flip_fixture(t)
        rc, out3 = run(["--metrics", "metrics_flip.json", "--themes", "themes_flip.json",
                        "-o", "k3.csv", "--brand-out", "b3.csv"], t)
        _, br3 = rows(t / "b3.csv")
        b3 = {r["bucket"]: r for r in br3}
        ok(rc == 0 and b3["브레빌"]["rank_lo"] == "1" and b3["필립스"]["rank_lo"] == "2",
           "순위가 하한 기준이 아니다")
        ok(b3["필립스"]["rank_hi"] == "1" and b3["브레빌"]["rank_hi"] == "2"
           and b3["브레빌"]["rank_flip"] == "yes" and b3["필립스"]["rank_flip"] == "yes",
           "상한에서 순위가 뒤집혔는데 rank_flip으로 표시되지 않았다")
        ok(b3["필립스"]["volume_12m_lo"] == "600" and b3["필립스"]["volume_12m_hi"] == "1800",
           "필립스 표기변형 3행의 하한·상한이 틀렸다")
        ok("순위 뒤집힘" in out3, "순위 뒤집힘 경고가 출력되지 않았다")
        ok(b3["카테고리 밖(분모 제외)"]["volume_12m_lo"] == "미수집" and b3["브레빌"]["multi_brand_volume_lo"] == "미수집"
           and "미수집 [데이터 공백]" in out3,
           "브랜드 단독·비교 검색을 수집하지 않았는데 «미수집» 대신 0으로 보고했다(P1-1)")

    if fails:
        print(f"실패 {len(fails)}건\n")
        for f in fails:
            print(f"  ✗ {f}")
        print("\n문서의 예시와 sov_calc.py가 어긋나 있다. 배포 전에 고칠 것.")
        return 1
    print("통과 — sov_calc.py 계약 검증 완료 (1단·2단 예시 명령 · 함정 픽스처 · API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
