#!/usr/bin/env python3
"""이 스킬 고유 스크립트(buyer_share.py) 계약 검증 + 공유 selftest.py 연쇄 실행.

공유 `selftest.py`는 정본 사본이라 이 스킬 고유 스크립트를 모른다(고치면 드리프트가 난다).
그래서 SKILL.md의 예시 명령을 합성 픽스처로 **그대로 실행**하는 검사를 여기 두고,
끝에서 공유 selftest.py도 돌린다. 한 번의 실행으로 둘 다 확인한다.

합성 픽스처만 쓴다 — API 호출·크레딧 없음.

    python3 scripts/selftest_buyers.py     # 스킬 폴더에서 실행
"""
import csv, json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
EXAMPLE = SKILL / "assets" / "intent-themes.cordless-vacuum-kr.example.json"

# SKILL.md «산출물»에 적힌 컬럼과 같아야 한다
COLUMNS = ["keyword", "canonical", "dup_of", "volume_total", "group_volume_lo", "group_volume_hi",
           "is_group_rep", "in_scope", "brand", "intent_class", "tier", "facets", "conflict", "cpc", "competition",
           "lm_i", "lm_n", "lm_c", "lm_t", "lm_reading", "lm_check", "label_metric", "label_group_volume",
           "label_class"]


def kw(k, vol, cpc=0.5, comp="MEDIUM"):
    return {"keyword": k, "ads_metrics": {"volume_total": vol, "volume_avg": (vol or 0) // 12,
            "volume_trend": 0.1, "cpc": cpc, "competition": comp, "competition_index": 50,
            "gg_volume_total": (vol or 0) // 3, "nv_volume_total": (vol or 0) - (vol or 0) // 3}}


def fixture(tmp):
    data = [
        kw("무선청소기", 400000, 0.9, "HIGH"),            # 헤드 → 미분류
        kw("무선 청소기", 400000, 0.9, "HIGH"),           # 표기변형 — 같은 그룹, 이중 계상 금지
        kw("코드리스 청소기", 30000),                     # 변형치환 코드리스→무선 → 위 그룹과 합류
        kw("무선청소기 최저가", 20000, 1.2, "HIGH"),       # 가격·거래 → buyer
        kw("다이슨 무선청소기 쿠팡", 8000, 1.5, "HIGH"),   # 채널 → buyer, 브랜드
        kw("무선청소기 추천 쿠팡", 5000, 1.1, "HIGH"),     # 채널+비교 → 충돌
        kw("무선청소기 추천", 90000, 0.7, "HIGH"),         # 비교 → browser
        kw("차량용 무선청소기", 7000, 0.5, "LOW"),          # category.exclude → 분포에서 제외(P2)
        kw("다이슨", 300000, 0.4, "MEDIUM"),              # 브랜드 지명
        kw("다이슨 as센터", 25000, 0.2, "LOW"),            # 구매 후
        kw("다이슨 중고", 12000, 0.3, "LOW"),              # 중고
        kw("무선청소기 배터리 교체", 6000, 0.3, "LOW"),     # 구매 후
        kw("신규브랜드 청소기", 15000, 0.6, "MEDIUM"),      # 사전 누락 브랜드 → 미분류 상위에 떠야
        kw("무선청소기 거치대", None),                     # 볼륨 결측
        # 하한·상한 순위 뒤집힘: 하한 12,000 < 신규브랜드 15,000 < 상한 21,000
        kw("뉴브랜드 청소기", 12000, 0.6, "MEDIUM"),
        kw("뉴브랜드청소기", 9000, 0.6, "MEDIUM"),
        # 2단 게이트 경계: 하한 700 < 게이트 1,000 ≤ 상한 1,400 — 하한으로 판정하고 상한 추가분을 적어야
        kw("미니브랜드 청소기", 700, 0.4, "LOW"),
        kw("미니브랜드청소기", 700, 0.5, "LOW"),           # CPC가 달라 같은 검색의 중복이 아니다
    ]
    th = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    th.setdefault("category", {})["exclude"] = "차량용|자동차"
    (tmp / "intent_themes.json").write_text(json.dumps(th, ensure_ascii=False), encoding="utf-8")
    # 1단 요청 목록 — 표기가 바뀌어 온 것 1개(코드리스청소기 → 코드리스 청소기), 미반환 1개(P1-3)
    req = [d["keyword"] for d in data if d["keyword"] not in ("무선 청소기", "코드리스 청소기")]
    req += ["코드리스청소기", "무선청소기 물걸레"]
    (tmp / "metrics.json").write_text(json.dumps({"data": data, "request_detail": {"keywords": req},
                                                 "cost_detail": {"total_cost": len(req)}}, ensure_ascii=False), encoding="utf-8")
    alls = [
        {"keyword": "무선청소기", "intents": {"i": 1, "n": 0, "c": 1, "t": 0},
         "monthly_volume": [{"month": "2026-06", "gg": 1, "nv": 1, "total": 2},
                            {"month": "2026-07", "gg": 1, "nv": 1, "total": 2},
                            {"month": "2026-08", "nv_code": 1}]},
        # intents는 0/1 플래그이고 둘이 동시에 1일 수 있다(계약 §1) — P1-2
        {"keyword": "무선청소기 추천 쿠팡", "intents": {"i": 0, "n": 0, "c": 1, "t": 1}},
        {"keyword": "신규브랜드 청소기", "intents": {"i": 0, "n": 0, "c": 0, "t": 1}},
    ]
    # 2단 요청 4개 중 뉴브랜드 청소기는 미반환 — 실제 요청 수·cost_detail로 보고해야 한다(P1-1)
    s2req = ["무선청소기", "신규브랜드 청소기", "뉴브랜드 청소기", "무선청소기 추천 쿠팡"]
    (tmp / "all.json").write_text(json.dumps({"data": alls, "request_detail": {"keywords": s2req},
                                             "cost_detail": {"total_cost": 40}}, ensure_ascii=False), encoding="utf-8")


def run(cmd, tmp):
    p = subprocess.run([sys.executable] + cmd, cwd=tmp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def check_ascii_markers():
    """P1-4 회귀 — 2026-09-28 US 실측 충돌(best⊂bestbuy · or⊂for · deal⊂ideal · vacuumale)."""
    sys.path.insert(0, str(HERE))
    from buyer_match import Marker, brand_marker, canon_bounds
    rules = {"vacuums": "vacuum"}
    cases = [  # (패턴, 키워드, 걸려야 하나)
        ("best", "bestbuy cordless vacuum", False), ("best", "best cordless vacuum", True),
        ("or", "cordless vacuum for dyson", False), ("or(?=dyson|shark)", "dyson or shark vacuum", True),
        ("deal", "ideal cordless vacuum", False), ("deal", "cordless vacuum deals", True),
        ("nearme", "vacuum store near me", True), ("compar[a-z]*", "vacuum comparison", True),
        ("sale", "vacuum sale", True), ("sale", "vacuums sale", True),
        ("쿠팡", "무선청소기쿠팡", True), ("as", "다이슨 as센터", True), ("as", "다이슨 마스터", False),
    ]
    out = []
    for pat, kw, want in cases:
        c, bd = canon_bounds(kw, rules)
        if Marker(pat).hit(c, bd) != want:
            out.append(("buyer_match", f"마커 `{pat}` × «{kw}» → {not want} (기대 {want})", c))
    c, _ = canon_bounds("vacuum sale", rules)
    if c != "vacuumsale":
        out.append(("buyer_match", "변형치환이 공백 제거 뒤에 적용됨(vacuumale)", c))
    bm = brand_marker(["shark", "mi"], rules)
    for kw, want in (("shark cordless vacuum", "shark"), ("sharkninja stick", ""), ("mi 무선청소기", "mi"), ("mini 청소기", "")):
        c, bd = canon_bounds(kw, rules)
        got = next((b for b, m in bm if m.hit(c, bd)), "")
        if got != want:
            out.append(("buyer_match", f"브랜드 토큰 매칭 «{kw}» → «{got}» (기대 «{want}»)", ""))
    return out


def check_us_duplicates(tmp):
    """P0-1 회귀 — 어순 변형이 지표까지 같으면 canonical이 달라도 합치고, 합친 뒤 lo=hi를 «중복 없음»으로 쓰지 않는다."""
    d = tmp / "us"; d.mkdir()
    themes = {"rules": {"변형치환": {"vacuums": "vacuum"}},
              "themes": [{"name": "channel", "tier": "buyer", "pattern": "bestbuy|walmart|amazon"},
                         {"name": "deal", "tier": "buyer", "pattern": "deal|sale|price|buy"},
                         {"name": "compare", "tier": "browser", "pattern": "best|review|vs|or(?=dyson|shark)"}],
              "brands": ["dyson", "shark"], "category": {"exclude": "robot"}, "no_match": {"with_brand": "brand", "without_brand": "head"},
              "no_match_tier": {"with_brand": "brand_nav", "without_brand": "unclassified"}}
    data = [kw("cordless vacuum", 300000, 1.0), kw("cordless vacuum deals", 50000, 1.4),
            kw("deals cordless vacuum", 50000, 1.4),          # 어순만 다르고 지표 완전 일치 → 중복
            kw("cordless vacuum deal", 50000, 1.4),           # 단수형도 지표 완전 일치 → 같은 묶음
            kw("best cordless vacuum", 90000, 0.8), kw("bestbuy cordless vacuum", 8000, 1.6),
            kw("dyson cordless vacuum", 120000, 0.9), kw("vacuum sale", 20000, 1.3),
            kw("robot vacuum", 70000, 2.0), kw("robotic vacuum", 70000, 2.0)]   # 대표가 카테고리 밖 → 중복도 밖
    (d / "m.json").write_text(json.dumps({"data": data}), encoding="utf-8")
    (d / "t.json").write_text(json.dumps(themes), encoding="utf-8")
    rc, out = run([f"{HERE}/build_universe.py", "--metrics", "m.json", "--themes", "t.json", "-o", "u.csv"], d)
    if rc:
        return [("build_universe(US)", "비정상 종료", out[-300:])]
    rc, out = run([f"{HERE}/buyer_share.py", "u.csv", "--themes", "t.json", "-o", "c.csv", "--queried-at", "2026-09-28"], d)
    if rc:
        return [("buyer_share(US)", "비정상 종료", out[-300:])]
    by = {r["keyword"]: r for r in csv.DictReader(open(d / "c.csv", encoding="utf-8-sig"))}
    f = []
    if by["deals cordless vacuum"]["dup_of"] != "cordless vacuum deal" or by["cordless vacuum deals"]["dup_of"] != "cordless vacuum deal":
        f.append(("buyer_share(US)", "어순·복수형 변형의 지표 완전 일치가 한 번만 세어지지 않음(P0-1)",
                  str({k: by[k]["dup_of"] for k in ("cordless vacuum deals", "deals cordless vacuum")})))
    # 거래 = deal 50,000 + bestbuy 8,000 + vacuum sale 20,000 = 78,000 / 588,000 (중복 제외) — 제외 전이면 178,000 / 688,000
    if "거래 의도 검색 비중: 하한 기준 13.3% · 상한 기준 13.3%" not in out or "제외하지 않았다면 거래 의도 비중은 25.9%" not in out:
        f.append(("buyer_share(US)", "중복 제외 후 거래 비중이 틀림(P0-1)", out[:600]))
    if by["best cordless vacuum"]["tier"] != "browser" or by["bestbuy cordless vacuum"]["conflict"]:
        f.append(("buyer_share(US)", "best⊂bestbuy 오탐(P1-4)", ""))
    if by["vacuum sale"]["tier"] != "buyer":
        f.append(("buyer_share(US)", "vacuum sale이 거래로 분류되지 않음(vacuumale · P1-4)", by["vacuum sale"]["canonical"]))
    if by["robotic vacuum"]["in_scope"] == "yes" or "robotic vacuum" in out.split("## 같은 검색으로")[1][:400]:
        f.append(("buyer_share(US)", "대표가 카테고리 밖인 중복 행이 카테고리 안으로 남음", by["robotic vacuum"]["in_scope"]))
    if "«중복이 없다»·«결론이 견고하다»는 뜻이 아니다" not in out:
        f.append(("buyer_share(US)", "합친 뒤 하한=상한을 «중복 없음»처럼 씀", out[out.find("## 상한"):][:300]))
    return f


def main():
    fails = []
    S = str(HERE)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)

        # SKILL.md STEP 3 예시 그대로
        rc, out = run([f"{S}/build_universe.py", "--metrics", "metrics.json",
                       "--themes", "intent_themes.json", "-o", "universe.csv"], tmp)
        if rc:
            fails.append(("build_universe", "STEP 3 예시로 비정상 종료", out[-300:]))
        else:
            # SKILL.md STEP 4 예시 그대로
            rc, out = run([f"{S}/buyer_share.py", "universe.csv", "--themes", "intent_themes.json",
                           "--requested", "metrics.json",
                           "-o", "intent_class.csv", "--report", "buyer_signal.md",
                           "--stage2-out", "stage2.json", "--stage2-gate", "1000", "--stage2-cap", "50",
                           "--queried-at", "2026-09-28", "--category", "무선청소기"], tmp)
            if rc:
                fails.append(("buyer_share", "STEP 4 예시로 비정상 종료", out[-300:]))
            else:
                rows = list(csv.DictReader(open(tmp / "intent_class.csv", encoding="utf-8-sig")))
                hdr = list(rows[0].keys())
                if hdr != COLUMNS:
                    fails.append(("buyer_share", f"컬럼이 문서와 다르다: {hdr}", ""))
                by = {r["keyword"]: r for r in rows}
                head = [r for r in rows if r["canonical"] == "무선청소기"]
                if len(head) != 3 or sum(int(r["is_group_rep"]) for r in head) != 1:
                    fails.append(("buyer_share", "표기변형 3행이 한 그룹·대표 1행으로 묶이지 않음", ""))
                # P0-1: 지표가 완전히 같은 표기변형은 같은 검색의 중복 — 하한·상한 계산 전에 합친다
                if by["무선 청소기"]["dup_of"] != "무선청소기" or by["무선 청소기"]["group_volume_hi"] != "":
                    fails.append(("buyer_share", "지표 완전 일치 행이 dup_of로 합쳐지지 않음(P0-1)",
                                  str({k: by["무선 청소기"][k] for k in ("dup_of", "group_volume_hi")})))
                if "같은 검색으로 합친 행 [추정] — 1묶음 · 1행 · 400,000건" not in out or "무선청소기 ← 무선 청소기" not in out:
                    fails.append(("buyer_share", "합친 행 목록이 리포트에 없음(P0-1)", ""))
                if "제외하지 않았다면 거래 의도 비중은" not in out:
                    fails.append(("buyer_share", "중복 제외 전 비중(대조값)이 없음(P0-1)", ""))
                # P2: 카테고리 경계
                if by["차량용 무선청소기"]["in_scope"] == "yes" or "카테고리 밖(category 경계) 1행 · 7,000건" not in out:
                    fails.append(("buyer_share", "category.exclude 행이 분포에서 빠지지 않음(P2)", ""))
                # P1-3: 요청↔응답 대조 (canonical)
                if ("미반환 1개 [데이터 공백]" not in out or "무선청소기 물걸레" not in out
                        or "표기가 바뀌어 반환 1개: 코드리스청소기 → 코드리스 청소기" not in out):
                    fails.append(("buyer_share", "요청↔응답 대조(미반환·표기 변경)가 canonical로 안 됨(P1-3)",
                                  out[out.find("## 요청"):][:400]))
                if by["무선청소기 최저가"]["tier"] != "buyer" or by["다이슨 무선청소기 쿠팡"]["tier"] != "buyer":
                    fails.append(("buyer_share", "가격·채널 마커가 buyer로 분류되지 않음", ""))
                if not by["무선청소기 추천 쿠팡"]["conflict"]:
                    fails.append(("buyer_share", "채널+비교 동시 매칭인데 conflict 미표시", ""))
                if by["다이슨 as센터"]["tier"] != "owner" or by["다이슨 중고"]["tier"] != "resale":
                    fails.append(("buyer_share", "구매 후·중고가 buyer/브랜드 지명과 분리되지 않음", ""))
                if by["다이슨"]["tier"] != "brand_nav":
                    fails.append(("buyer_share", "브랜드 단독 검색이 brand_nav가 아님", ""))
                # T5 하한·상한: 중복(무선 청소기)을 합친 뒤 헤드 그룹 하한 400,000(최댓값) · 상한 430,000(합)
                hr = next(r for r in head if r["is_group_rep"] == "1")
                if (hr["group_volume_lo"], hr["group_volume_hi"]) != ("400000", "430000"):
                    fails.append(("buyer_share", "헤드 그룹 하한·상한이 최댓값·합이 아님",
                                  f'{hr["group_volume_lo"]} / {hr["group_volume_hi"]}'))
                if any(r["label_group_volume"] != "추정" for r in rows):
                    fails.append(("buyer_share", "그룹 볼륨 라벨이 [추정]이 아님(계약 §4 4라벨)", ""))
                if "[파생]" in out or "파생" in {r["label_class"] for r in rows}:
                    fails.append(("buyer_share", "계약 밖 라벨 [파생]이 남아 있음", ""))
                nb = by["뉴브랜드 청소기"]
                if (nb["group_volume_lo"], nb["group_volume_hi"]) != ("12000", "21000"):
                    fails.append(("buyer_share", "lo≠hi 픽스처(뉴브랜드) 값이 틀림",
                                  f'{nb["group_volume_lo"]} / {nb["group_volume_hi"]}'))
                # 그룹 합 하한 = 400000+20000+8000+5000+90000+300000+25000+12000+6000+15000+12000+700
                #         상한 = 430000+20000+8000+5000+90000+300000+25000+12000+6000+15000+21000+1400
                if "그룹 합 [추정] 하한 893,700 ~ 상한 933,400" not in out or "행 단위 합 1,333,400" not in out:
                    fails.append(("buyer_share", "그룹 합 하한~상한이 행 단위 합과 함께 출력되지 않음", out[:500]))
                # P2: 비중은 하한·상한 각자의 분모로 계산한 두 값 — «~» 범위로 쓰지 않는다
                kl = next((l for l in out.splitlines() if l.startswith("- 거래 의도 검색 비중:")), "")
                if "하한 기준" not in kl or "· 상한 기준" not in kl or "~" in kl:
                    fails.append(("buyer_share", "핵심 비중이 라벨 붙은 두 값(하한 기준·상한 기준)이 아님", kl))
                if "## 티어별 상위 기여 검색어" not in out or "무선청소기 최저가 20,000" not in out:
                    fails.append(("buyer_share", "티어별 상위 기여 검색어 누락(P2)", ""))
                # 순위 뒤집힘: 하한 순 신규브랜드(15,000) > 뉴브랜드(12,000), 상한 순 뉴브랜드(21,000) > 신규브랜드
                sec = out.split("## 미분류 상위")[1] if "## 미분류 상위" in out else ""
                if not (sec.find("신규브랜드 청소기") < sec.find("뉴브랜드 청소기") and sec.find("신규브랜드") >= 0):
                    fails.append(("buyer_share", "미분류 상위가 하한 순이 아님", sec[:300]))
                if "미분류 상위 순서: 상한 기준이면" not in out:
                    fails.append(("buyer_share", "상한에서 순위가 뒤집히는데 표시되지 않음", ""))
                if "상한 기준이면 게이트를 넘는 그룹 1개 추가" not in out:
                    fails.append(("buyer_share", "2단 게이트가 상한에서 뒤집히는데 표시되지 않음", ""))
                if "논브랜드" not in out or "결측 그룹: 1개" not in out:
                    fails.append(("buyer_share", "논브랜드 분포 또는 결측 집계 누락", out[:400]))
                if "신규브랜드 청소기" not in out:
                    fails.append(("buyer_share", "사전 누락 의심 키워드가 미분류 상위에 안 뜸", ""))
                s2 = json.load(open(tmp / "stage2.json", encoding="utf-8"))
                if ("무선청소기 추천 쿠팡" not in s2 or "무선청소기 최저가" in s2
                        or any(k.startswith("미니브랜드") for k in s2)):
                    fails.append(("buyer_share", f"2단 대상 선정이 미분류·충돌·하한 게이트 기준이 아님: {s2}", ""))
                if "미확인" not in out:
                    fails.append(("buyer_share", "2단 응답 없이 기준월을 단정함", ""))
                if "2단 조회 대상 (하한 기준 게이트): 4개" not in out:
                    fails.append(("buyer_share", "2단 대상 수가 게이트·상한 적용 결과와 다름", ""))

            # SKILL.md STEP 5 예시 그대로 — STEP 4와 같은 게이트·상한을 다시 준다(P1-1)
            rc, out = run([f"{S}/buyer_share.py", "universe.csv", "--themes", "intent_themes.json",
                           "--requested", "metrics.json", "--lm-intents", "all.json",
                           "-o", "intent_class.csv", "--report", "buyer_signal.md",
                           "--stage2-out", "stage2.json", "--stage2-gate", "1000", "--stage2-cap", "50",
                           "--queried-at", "2026-09-28", "--category", "무선청소기"], tmp)
            if rc:
                fails.append(("buyer_share", "STEP 5 예시(--lm-intents)로 비정상 종료", out[-300:]))
            else:
                if "2026-07" not in out or "지연 2개월" not in out:
                    fails.append(("buyer_share", "결측 월(nv_code)을 건너뛴 기준월·지연이 안 나옴", out[:300]))
                if "LM intents 대조" not in out:
                    fails.append(("buyer_share", "LM intents 대조 요약 누락", ""))
                # P1-1: 실제 2단 요청 수·크레딧은 응답(request_detail·cost_detail)에서 읽는다
                if "2단 실행(응답 기준): 요청 4개 → 40크레딧(cost_detail) · 미반환 1개 [데이터 공백]: 뉴브랜드 청소기" not in out:
                    fails.append(("buyer_share", "2단 실제 요청 수·크레딧·미반환이 응답 기준으로 안 나옴(P1-1)",
                                  out[out.find("## 2단 실행"):][:300]))
                # P1-2: 미분류·충돌 그룹이 LM 0/1 플래그로 어떻게 읽히는지(두 플래그 동시 1 포함)
                if ("2단 대상(미분류·충돌) 3그룹" not in out or "무선청소기 추천 쿠팡 (buyer · 충돌 browser|buyer) → LM 거래+비교" not in out
                        or "신규브랜드 청소기 (unclassified) → LM 거래" not in out):
                    fails.append(("buyer_share", "2단 대상 intents 플래그 요약 누락(P1-2)",
                                  out[out.find("2단 대상(미분류"):][:400]))

        # P1-4: ASCII 마커·브랜드는 토큰 단위, 변형치환은 공백을 지우기 전에
        fails += check_ascii_markers()

        # P0-1(US형): 어순만 다르고 지표가 완전히 같은 행 — canonical이 달라도 한 번만 센다 · lo=hi 문구
        fails += check_us_duplicates(tmp)

    # 공유 selftest (정본 사본) 연쇄 실행
    p = subprocess.run([sys.executable, str(HERE / "selftest.py")], capture_output=True, text=True)
    shared_out = (p.stdout + p.stderr).strip()
    if p.returncode:
        fails.append(("selftest.py(공유)", "실패", shared_out[-300:]))

    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {detail.strip()[:300]}")
        print("\n문서의 예시와 스크립트가 어긋나 있다. 배포 전에 고칠 것.")
        return 1
    print("통과 — buyer_share.py 예시 3종(STEP 3·4·5) 계약 검증 완료 (API 호출 없음)")
    print(f"공유 selftest: {shared_out.splitlines()[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
