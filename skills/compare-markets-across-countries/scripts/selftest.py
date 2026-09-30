#!/usr/bin/env python3
"""이 스킬의 번들 스크립트 계약 검증 — SKILL.md의 예시 명령이 실제로 도는지 확인한다.

공유 selftest(shared/scripts/selftest.py)는 이 스킬 고유 스크립트
(`cross_country.py`)를 모른다. 그래서 이 파일은 공유 사본이 아니라 **스킬 자체 selftest**다:
공유 스크립트 2종(check_refmonth·trend_yoy)과 고유 스크립트를 SKILL.md에 적힌 명령 그대로
합성 픽스처에 돌린다. API 호출도 크레딧도 필요 없다.

픽스처가 일부러 밟는 함정:
  - kr: 요청하지 않은 띄어쓰기 변형(무선 청소기) · 제외어(로봇청소기) · 결측 월(nv_code) ·
        마지막 달 급락 · demography 반환
  - us: 엔진 분해 없이 total만 · demography 없음(사양) · 기준월이 kr보다 한 달 늦음
  - jp: 반각 가나 변형(ｺｰﾄﾞﾚｽ掃除機 → NFKC로 같은 그룹 — 하한≠상한) · handheld 개념 미반환(데이터 공백)
  - us: 단복수 변형(handheld vacuums → 변형치환으로 같은 그룹) — handheld 지수가 하한에서는 kr보다
        작고 상한에서는 크다(국가 순위 뒤집힘)

    python3 scripts/selftest.py        # 스킬 폴더에서 실행
"""
import csv, json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ASSET = HERE.parent / "assets" / "concepts.stick-vacuum.example.json"


def months(last, n=48):
    y, m = int(last[:4]), int(last[5:7])
    out = []
    for i in range(n - 1, -1, -1):
        t = y * 12 + (m - 1) - i
        out.append(f"{t // 12}-{t % 12 + 1:02d}")
    return out


def row(kw, base, last, split=True, missing=(), dip_last=False, demo=None, growth=0.0):
    mv = []
    ms = months(last)
    for i, mo in enumerate(ms):
        if mo in missing:
            mv.append({"month": mo, "gg": 10, "nv_code": 1}); continue
        season = 1 + 0.5 * (int(mo[5:7]) in (11, 12, 1))
        v = int(base * season * (1 + growth) ** (i / 12))
        if dip_last and mo == last:
            v = int(v * 0.2)
        mv.append({"month": mo, "gg": v // 3, "nv": v - v // 3, "total": v} if split
                  else {"month": mo, "total": v})
    vt = sum(r.get("total", 0) for r in mv[-12:])
    am = {"volume_total": vt, "volume_avg": vt // 12, "volume_trend": -0.9, "cpc": 0.4 if split else 1.2,
          "competition_index": 60}
    if split:
        am |= {"gg_volume_total": vt // 3, "nv_volume_total": vt - vt // 3}
    d = {"keyword": kw, "ads_metrics": am, "monthly_volume": mv}
    if demo:
        d["demography"] = demo
    return d


def fixture(tmp):
    demo = {"m_gender_ratio": 40.0, "f_gender_ratio": 60.0, "a30": 1, "a30_ratio": 35.0, "a40_ratio": 25.0}
    kr = [row("청소기", 20000, "2026-07", demo=demo),
          row("무선청소기", 8000, "2026-07", demo=demo, growth=0.3, dip_last=True),
          row("무선 청소기", 7900, "2026-07", demo=demo, growth=0.3),          # 요청 안 한 표기변형
          row("스틱청소기", 3000, "2026-07", demo=demo, missing=("2026-05",)),  # 결측 월
          row("로봇청소기", 9000, "2026-07", demo=demo),                        # 제외어
          row("핸디청소기", 2000, "2026-07", demo=demo)]
    us = [row("vacuum cleaner", 60000, "2026-08", split=False),
          row("cordless vacuum", 20000, "2026-08", split=False, growth=0.1),
          row("cordless vacuums", 20000, "2026-08", split=False, growth=0.1),  # 시계열 완전 동일 → 같은 검색(P1-2)
          row("stick vacuum", 9000, "2026-08", split=False),
          row("handheld vacuum", 5000, "2026-08", split=False),
          row("handheld vacuums", 3000, "2026-08", split=False),           # 단복수 변형 → 순위 뒤집힘
          row("robot vacuum", 30000, "2026-08", split=False)]
    jp = [row("掃除機", 30000, "2026-07", split=False),
          row("コードレス掃除機", 6000, "2026-07", split=False, growth=0.2),
          row("ｺｰﾄﾞﾚｽ掃除機", 5000, "2026-07", split=False)]                  # 반각 가나
    for gl, rows in (("kr", kr), ("us", us), ("jp", jp)):
        (tmp / f"all_{gl}.json").write_text(json.dumps({"data": rows}, ensure_ascii=False), encoding="utf-8")
        screen = [{"keyword": r["keyword"], "ads_metrics": r["ads_metrics"]} for r in rows]
        if gl == "kr":
            screen.append({"keyword": "미니청소기", "ads_metrics": {"volume_total": 300}})  # 게이트 미달
        (tmp / f"screen_{gl}.json").write_text(json.dumps({"data": screen}, ensure_ascii=False), encoding="utf-8")
    (tmp / "concepts.json").write_text(ASSET.read_text(encoding="utf-8"), encoding="utf-8")


def run(cmd, tmp):
    p = subprocess.run([sys.executable] + cmd, cwd=tmp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def read(path):
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


# SKILL.md «산출물»에 적힌 컬럼 — 스크립트를 고치면 여기와 문서를 함께 고친다
MAIN_COLS = ["country", "concept_id", "concept_label", "groups_used", "multi_variant_groups",
             "volume_12m_lo", "volume_12m_hi", "anchor_volume_12m_lo", "anchor_volume_12m_hi",
             "index_vs_anchor_lo", "index_vs_anchor_hi", "index_reading",
             "index_rank_lo", "index_rank_hi", "index_rank_flip",
             "yoy_3m", "yoy_basis", "yoy_status", "yoy_label", "bucket_warning", "variant_yoy_split",
             "window_share_12m", "offseason_flag",
             "prev_year_window_volume", "missing_in_window", "missing_in_12m", "peak_month", "peak_share",
             "base_country", "season_corr_vs_base", "cpc_index", "competition_index_avg",
             "engine_split", "nv_share_lo", "nv_share_hi", "demography_status", "f_gender_ratio", "top_age_band",
             "data_ref_month", "window_end"]
KW_COLS = ["country", "concept_id", "keyword", "canonical", "match_type", "group_size",
           "volume_total", "representative", "used_in_hi", "dup_of", "dup_basis"]
SCREEN_COLS = ["country", "concept_id", "keyword", "canonical", "match_type", "requested",
               "volume_total", "passed_gate", "reason"]


def main():
    fails = []
    S = str(HERE)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)

        # STEP 3 예시
        rc, out = run([f"{S}/check_refmonth.py", "all_kr.json", "--queried-at", "2026-09-28"], tmp)
        if rc or "2026-07" not in out:
            fails.append(("check_refmonth", "기준월 미출력 또는 비정상 종료", out))
        elif "이상 의심: 0행" in out:
            fails.append(("check_refmonth", "마지막 달 급락 픽스처를 감지하지 못함", out))

        # STEP 2 예시
        rc, out = run([f"{S}/cross_country.py", "select", "--concepts", "concepts.json",
                       "--screen", "kr=screen_kr.json", "--screen", "us=screen_us.json",
                       "--screen", "jp=screen_jp.json", "--gate", "1200", "-o", "stage2"], tmp)
        if rc:
            fails.append(("select", "비정상 종료", out))
        else:
            rep = read(tmp / "stage2" / "screen_report.csv")
            if list(rep[0].keys()) != SCREEN_COLS:
                fails.append(("select", f"screen_report 컬럼 불일치: {list(rep[0].keys())}", ""))
            kr2 = json.load(open(tmp / "stage2" / "stage2_kr.json", encoding="utf-8"))
            if "로봇청소기" in kr2 or "미니청소기" in kr2:
                fails.append(("select", "제외어 또는 게이트 미달이 2단에 들어감", str(kr2)))
            if "무선 청소기" in kr2:
                fails.append(("select", "자동 반환 표기변형이 2단에 중복으로 들어감(10크레딧 이중 지출)", str(kr2)))
            if "청소기" not in kr2:
                fails.append(("select", "기준어가 2단에서 빠짐", str(kr2)))
            if not any(r["reason"] == "응답에 없음" for r in rep):
                fails.append(("select", "요청했는데 응답에 없는 동의어를 보고하지 않음", ""))
            if "크레딧" not in out:
                fails.append(("select", "2단 크레딧 예상치를 출력하지 않음", out))

        # STEP 4 예시
        rc, out = run([f"{S}/cross_country.py", "compare", "--concepts", "concepts.json",
                       "--metrics", "kr=all_kr.json", "--metrics", "us=all_us.json",
                       "--metrics", "jp=all_jp.json", "--queried-at", "2026-09-28",
                       "--yoy-gate", "3000", "-o", "cross_country_stick-vacuum.csv"], tmp)
        if rc:
            fails.append(("compare", "비정상 종료", out))
        else:
            rows = read(tmp / "cross_country_stick-vacuum.csv")
            kws = read(tmp / "cross_country_stick-vacuum_keywords.csv")
            if list(rows[0].keys()) != MAIN_COLS:
                fails.append(("compare", f"비교표 컬럼 불일치: {list(rows[0].keys())}", ""))
            if list(kws[0].keys()) != KW_COLS:
                fails.append(("compare", f"키워드표 컬럼 불일치: {list(kws[0].keys())}", ""))
            g = {(r["country"], r["concept_id"]): r for r in rows}
            if "기준월이 다르다" not in out or g[("us", "cordless_stick")]["window_end"] != "2026-06":
                fails.append(("compare", "국가별 기준월 차이를 공통 창(가장 이른 기준월 −1)으로 맞추지 않음", out))
            if "마지막 달 제외" not in out:
                fails.append(("compare", "마지막 달 처리 방식이 출력에 없음", out))
            ks = {(r["country"], r["keyword"]): r for r in kws}
            if ks[("kr", "무선 청소기")]["canonical"] != ks[("kr", "무선청소기")]["canonical"] or \
               ks[("kr", "무선 청소기")]["representative"] == ks[("kr", "무선청소기")]["representative"]:
                fails.append(("compare", "띄어쓰기 변형 그룹의 대표 행이 하나가 아님(T5)", ""))
            if ks[("jp", "ｺｰﾄﾞﾚｽ掃除機")]["canonical"] != ks[("jp", "コードレス掃除機")]["canonical"]:
                fails.append(("compare", "반각 가나 변형이 같은 그룹으로 묶이지 않음(NFKC)", ""))
            if ks[("kr", "로봇청소기")]["concept_id"] != "__excluded__":
                fails.append(("compare", "제외어가 개념에 합산됨", ""))
            if not g[("kr", "cordless_stick")]["yoy_status"].startswith("계산 불가"):
                fails.append(("compare", "결측 월이 있는 개념이 계산됨(0 합산 의심, T6)", ""))
            if g[("us", "cordless_stick")]["yoy_status"] != "ok" or not g[("us", "cordless_stick")]["yoy_3m"]:
                fails.append(("compare", "정상 개념의 yoy_3m이 계산되지 않음", str(g[("us", "cordless_stick")])))
            if not g[("us", "__anchor__")]["demography_status"].startswith("미반환(gl 사양"):
                fails.append(("compare", "us demography 미반환을 사양으로 표기하지 않음", ""))
            if g[("kr", "handheld")]["demography_status"] != "반환" or not g[("kr", "handheld")]["top_age_band"]:
                fails.append(("compare", "kr demography가 집계되지 않음", ""))
            if not g[("jp", "handheld")]["yoy_status"].startswith("데이터 공백"):
                fails.append(("compare", "jp에서 미반환된 개념을 데이터 공백 행으로 남기지 않음", ""))
            if g[("us", "cordless_stick")]["engine_split"] == g[("kr", "cordless_stick")]["engine_split"]:
                fails.append(("compare", "국가별 엔진 구성 차이(engine_split)를 구분하지 못함", ""))
            if g[("kr", "cordless_stick")]["index_vs_anchor_lo"] != "" or \
               g[("kr", "cordless_stick")]["index_vs_anchor_hi"] != "":
                fails.append(("compare", "12개월 창에 결측 월이 있는데 규모 지수가 계산됨(T6)", ""))
            if float(g[("kr", "__anchor__")]["index_vs_anchor_lo"]) != 1.0 or \
               float(g[("kr", "__anchor__")]["index_vs_anchor_hi"]) != 1.0:
                fails.append(("compare", "기준어 지수가 1이 아님", ""))
            # T5 하한≠상한: jp 코드리스 그룹(コードレス掃除機 + ｺｰﾄﾞﾚｽ掃除機)
            jc = g[("jp", "cordless_stick")]
            lo, hi = int(jc["volume_12m_lo"]), int(jc["volume_12m_hi"])
            if not lo < hi or jc["multi_variant_groups"] != "1":
                fails.append(("compare", "표기변형 그룹의 하한(최댓값)·상한(합)이 구분되지 않음(T5)", str(jc)))
            ja = g[("jp", "__anchor__")]
            if abs(float(jc["index_vs_anchor_lo"]) - lo / int(ja["volume_12m_lo"])) > 1e-3 or \
               abs(float(jc["index_vs_anchor_hi"]) - hi / int(ja["volume_12m_hi"])) > 1e-3:
                fails.append(("compare", "지수 하한·상한이 각자의 분모로 나뉘지 않음", str(jc)))
            if jc["yoy_basis"] != "그룹 대표 행(하한 기준)":
                fails.append(("compare", "yoy_3m이 대표 행(하한) 기준임을 적지 않음", str(jc)))
            # 순위 뒤집힘: handheld — 하한 kr 1위·us 2위, 상한 us 1위·kr 2위
            kh, uh = g[("kr", "handheld")], g[("us", "handheld")]
            if (kh["index_rank_lo"], kh["index_rank_hi"], uh["index_rank_lo"], uh["index_rank_hi"]) != ("1", "2", "2", "1") \
               or kh["index_rank_flip"] != "뒤집힘" or uh["index_rank_flip"] != "뒤집힘":
                fails.append(("compare", "상한에서 국가 순위가 뒤집혔는데 표시하지 않음(T5)", f"kr {kh}\nus {uh}"))
            if "하한(그룹 최댓값) ~ 상한(그룹 합) [추정]" not in out or "[파생]" in out:
                fails.append(("compare", "하한·상한 라벨이 계약 4-Label([추정])이 아님", out))
            if "순위가 뒤집힌다" not in out:
                fails.append(("compare", "순위 뒤집힘 경고가 출력에 없음", out))
            if g[("jp", "cordless_stick")]["index_rank_flip"] != "":
                fails.append(("compare", "뒤집히지 않은 칸에 뒤집힘이 찍힘", str(jc)))
            # 번역어·음역어는 표기변형이 아니다 — 掃除機(기준어)와 コードレス掃除機는 별개 그룹
            if ks[("jp", "掃除機")]["canonical"] == ks[("jp", "コードレス掃除機")]["canonical"]:
                fails.append(("compare", "서로 다른 검색어가 한 그룹으로 묶임", ""))
            yoy = float(g[("us", "cordless_stick")]["yoy_3m"])
            if not 0.05 < yoy < 0.15:   # 픽스처 성장률 10%
                fails.append(("compare", f"yoy_3m이 픽스처 성장률(약 10%)과 어긋남: {yoy}", ""))

            # ── 2026-09-28 실데이터 평가 회귀 ──
            # P1-2: 시계열이 같은 변형(cordless vacuums)은 상한에 더하지 않는다 → 하한 = 상한
            uc = g[("us", "cordless_stick")]
            if uc["volume_12m_lo"] != uc["volume_12m_hi"] or ks[("us", "cordless vacuums")]["dup_of"] != "cordless vacuum" \
                    or ks[("us", "cordless vacuums")]["used_in_hi"] != "False":
                fails.append(("compare", "시계열이 같은 변형을 상한에 이중 합산 (P1-2)", str(uc)))
            # P1-3: us(엔진 분해 없음)의 yoy_3m은 버킷 경고와 [추정] 라벨
            if uc["yoy_label"] != "[추정]" or "버킷" not in uc["bucket_warning"]:
                fails.append(("compare", "버킷 반올림 값의 yoy_3m을 [실제 데이터]로 라벨 (P1-3)", str(uc)))
            # P1-4: 비교 창의 연 볼륨 비중을 낸다 (픽스처: 성수기 11·12·1월 → 4~6월 창 = 3/13.5)
            if abs(float(uc["window_share_12m"] or 0) - 3 / 13.5) > 0.01 or uc["offseason_flag"]:
                fails.append(("compare", "비교 창 비중 계산이 틀렸다 (P1-4)", str(uc)))
            if "성장률로 국가를 줄 세우지 않는다" in out:
                fails.append(("compare", "비수기가 아닌데 비수기 경고", out))
            # P1-5: 순위의 기준어 의존을 항상 적는다
            if "기준어 선택에 의존" not in out or uc["index_reading"] != "비중(≤1)":
                fails.append(("compare", "지수 읽는 법·기준어 의존 표기 없음 (P1-5)", str(uc)))

        # P1-4: 비수기 임계를 올리면 같은 창이 비수기로 표시된다
        rc, out = run([f"{S}/cross_country.py", "compare", "--concepts", "concepts.json",
                       "--metrics", "us=all_us.json", "--queried-at", "2026-09-28",
                       "--offseason-share", "0.25", "-o", "cc_off.csv"], tmp)
        if rc or "성장률로 국가를 줄 세우지 않는다" not in out:
            fails.append(("compare", "비수기 창 경고가 없다 (P1-4)", out[-300:]))
        # P1-5: 기준어가 개념보다 작으면(지수 > 1) 배율로 표시
        cj = json.loads((tmp / "concepts.json").read_text(encoding="utf-8"))
        cj["anchor"]["us"] = ["stick vacuum"]
        (tmp / "concepts_small_anchor.json").write_text(json.dumps(cj, ensure_ascii=False), encoding="utf-8")
        rc, out = run([f"{S}/cross_country.py", "compare", "--concepts", "concepts_small_anchor.json",
                       "--metrics", "us=all_us.json", "--queried-at", "2026-09-28", "-o", "cc_anchor.csv"], tmp)
        ca = {r["concept_id"]: r for r in read(tmp / "cc_anchor.csv")} if not rc else {}
        if rc or not ca.get("cordless_stick", {}).get("index_reading", "").startswith("배율") or "지수 > 1" not in out:
            fails.append(("compare", "지수 > 1(기준어가 개념을 포괄하지 않음)을 표시하지 않음 (P1-5)", out[-300:]))
        # P1-1: 개념 동의어가 모두 게이트 미달이면 기준어만으로 2단을 부르지 않는다
        rc, out = run([f"{S}/cross_country.py", "select", "--concepts", "concepts.json",
                       "--screen", "us=screen_us.json", "--gate", "1000000000", "-o", "stage2_hi"], tmp)
        us2 = json.load(open(tmp / "stage2_hi" / "stage2_us.json", encoding="utf-8")) if not rc else None
        if rc or us2 != [] or "2단 생략" not in out or "손익분기" not in out:
            fails.append(("select", "개념 전멸 국가에 기준어만으로 2단을 부른다 · 손익분기 미출력 (P1-1)", out[-300:]))
        rc, out = run([f"{S}/cross_country.py", "compare", "--concepts", "concepts.json",
                       "--metrics", "kr=all_kr.json", "--skipped", "us", "--queried-at", "2026-09-28",
                       "-o", "cc_skip.csv"], tmp)
        if rc or not any(r["country"] == "us" and "2단 생략" in r["yoy_status"] for r in read(tmp / "cc_skip.csv")):
            fails.append(("compare", "2단 생략 국가를 데이터 공백 행으로 남기지 않음 (P1-1)", out[-300:]))
        # P2: 통과한 그룹의 표기변형은 표현 부재 후보로 세지 않는다
        rc, out = run([f"{S}/cross_country.py", "select", "--concepts", "concepts.json",
                       "--screen", "us=screen_us.json", "--gate", "1200", "-o", "stage2_us_only"], tmp)
        if rc or "탈락 동의어 1개" not in out:   # cordless stick vacuum(응답에 없음)만
            fails.append(("select", "통과 그룹의 표기변형을 표현 부재 후보로 셌다 (P2)", out[-300:]))
        # P2·P1-3 문서: 고정 지연 서술 금지
        doc = (HERE.parent / "SKILL.md").read_text(encoding="utf-8")
        if "약 2개월" in doc or "약 2개월" in (HERE.parent / "evals" / "evals.json").read_text(encoding="utf-8"):
            fails.append(("SKILL.md", "고정 지연 서술(약 2개월)이 남아 있다", ""))

        # ── 2026-09-30 선크림 실측 회귀 (v1.3) ──
        # 기준어 표기변형 2개(치환으로 같은 canonical): 마스터 첫 표기(日やけ止め)는 응답에 없고 둘째(日焼け止め)만 있다
        v13 = {"category": "t", "rules": {"jp": {"변형치환": {"日やけ止め": "日焼け止め"}}},
               "anchor": {"jp": ["日やけ止め", "日焼け止め"]},
               "concepts": [{"id": "stick", "label": "스틱", "terms": {"jp": [{"kw": "日焼け止めスティック", "match": "x"}]}}]}
        (tmp / "c13.json").write_text(json.dumps(v13, ensure_ascii=False), encoding="utf-8")
        up = row("日焼け止め", 20000, "2026-07", split=False, growth=0.2)
        dn = row("日やけ止め", 15000, "2026-07", split=False, growth=-0.2)
        st = row("日焼け止め スティック", 5000, "2026-07", split=False)
        (tmp / "s13.json").write_text(json.dumps({"data": [{"keyword": r["keyword"], "ads_metrics": r["ads_metrics"]}
                                                            for r in (up, st)]}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([f"{S}/cross_country.py", "select", "--concepts", "c13.json", "--screen", "jp=s13.json",
                       "-o", "st13"], tmp)
        j2 = json.load(open(tmp / "st13" / "stage2_jp.json", encoding="utf-8")) if not rc else []
        if "日焼け止め" not in j2 or "日やけ止め" in j2:
            fails.append(("select", f"1단 응답에 없는 표기변형을 2단 요청어로 골랐다 (v1.3): {j2}", out[-300:]))
        if "N = 3개" not in out or "canonical 2개" not in out:
            fails.append(("select", "1단 N을 canonical 수로 셌다 — 요청 원문 수가 과금 단위 (v1.3)", out[-300:]))
        rep13 = read(tmp / "st13" / "screen_report.csv") if not rc else []
        if not any(r["keyword"] == "日やけ止め" and r["reason"].startswith("요청 표기 미반환") for r in rep13) \
                or "요청한 표기가 응답에 없다" not in out:
            fails.append(("select", "요청한 표기변형이 응답에 없는데 공백으로 보고하지 않음 (v1.3)", out[-300:]))
        # 표기변형끼리 전년 대비 부호가 갈리면 경고
        (tmp / "a13.json").write_text(json.dumps({"data": [up, dn, st]}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([f"{S}/cross_country.py", "compare", "--concepts", "c13.json", "--metrics", "jp=a13.json",
                       "--queried-at", "2026-09-28", "-o", "cc13.csv"], tmp)
        g13 = {r["concept_id"]: r for r in read(tmp / "cc13.csv")} if not rc else {}
        an = g13.get("__anchor__", {})
        if "日やけ止め" not in an.get("variant_yoy_split", "") or "추세 부호 갈림" not in an.get("bucket_warning", "") \
                or "부호가 갈린다" not in out or an.get("yoy_label") != "[추정]":
            fails.append(("compare", "표기변형 간 yoy 부호 갈림을 경고하지 않음 (v1.3)", str(an)[-300:]))

        # STEP 5 예시
        rc, out = run([f"{S}/trend_yoy.py", "all_us.json", "-o", "trend_us.csv"], tmp)
        if rc:
            fails.append(("trend_yoy", "비정상 종료", out))
        else:
            need = {"yoy", "yoy_ex_last_month", "missing_months_in_window", "last_month_flag"}
            miss = need - set(read(tmp / "trend_us.csv")[0].keys())
            if miss:
                fails.append(("trend_yoy", f"명세 컬럼 누락: {sorted(miss)}", ""))

    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {detail.strip()[-300:]}")
        print("\n문서의 예시와 스크립트가 어긋나 있다. 배포 전에 고칠 것.")
        return 1
    print("통과 — check_refmonth · cross_country(select·compare) · trend_yoy 계약 검증 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
