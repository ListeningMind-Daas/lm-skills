#!/usr/bin/env python3
"""map-entry-situations 고유 검증 — 합성 픽스처(assets/fixtures/)로 STEP 1~8 명령을 그대로 돌려 함정 처리와
문서 컬럼을 검사한다. API 호출 · 크레딧 없음. `python3 scripts/selftest.py`가 끝에 함께 돌린다.

픽스처(선크림 KR, 합성): 절단된 intent_finder 호출 · 뜻 고정 제외어(선크림 노래) · 미반환(아기 외출) ·
요청 안 한 같은 검색 중복(선 크림) · 응답 안 완전 중복 행 · 표기변형(썬크림) · 결측 월(골프 선크림) ·
마지막 달 급락(여름 선크림) · 역위상 계절(여름 CEP vs 겨울 CEP) · 브랜드 지배 CEP(골프) · 게이트 미달 CEP ·
시드 표기 변경 경로(썬크림만 온 응답) · volume_trend 0.9(쓰면 안 되는 값).

    python3 scripts/selftest_cep.py     # 스킬 폴더에서
"""
import csv, json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
FX = SKILL / "assets" / "fixtures"
THEMES = SKILL / "assets" / "themes.json"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def run(args, cwd):
    p = subprocess.run([sys.executable] + [str(x) for x in args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    return p.returncode, p.stdout + p.stderr


def header(p):
    return next(csv.reader(open(p, encoding="utf-8-sig")))


def documented():
    """SKILL.md «산출물» 코드 블록의 파일명 → 컬럼 목록."""
    md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    sec = md.split("## 산출물", 1)[1].split("\n## ", 1)[0]
    block = sec.split("```", 2)[1]
    out, cur = {}, None
    for ln in block.splitlines():
        if re.match(r"^\S+\.csv\s*$", ln.strip()) and not ln.startswith(" "):
            cur = ln.strip(); out[cur] = []
        elif cur and ln.startswith("  ") and ln.strip():
            out[cur] += [c.strip() for c in ln.split(",") if c.strip()]
    return out


# ── v0.1.1 회귀 — 앱 캐시 재생 비교에서 나온 P0 두 건. 문자열은 실패 사례 그대로, 볼륨은 지어낸 값이다.
WP_SENSE = {"keyword": "정수기", "gl": "kr", "domain": "물을 정수하는 가전", "anchor_terms": ["정수기"], "seeds": [],
            "exclude": [], "focal_brand": "", "competitors": [], "confirmed_by_user": True}
# v0.1.0 이 공백을 지운 canonical 아무 데서나 건 일치 — (원문, v0.1.0 패턴, 걸린 테마). 이제는 걸리면 안 된다
P0_1_FALSE = [("직수정수기", "수정", "문제:땀·피지·지속"), ("직수 정수기", "수정", "문제:땀·피지·지속"),
              ("얼음정수기미니", "기미", "목표:예방·관리"), ("정수기미세플라스틱", "기미", "목표:예방·관리"),
              ("정수 영어로", "수영", "장소:바다·물놀이"), ("미세플라스틱", "스틱", "비상황:제품 속성"),
              ("미세 플라스틱", "스틱", "비상황:제품 속성"), ("아이콘 정수기", "아이", "사람:아기·아이")]
P0_1_TRUE = [("이사 정수기", "계기:이사·입주·결혼"), ("아기 분유 정수기", "사람:아기·아이"), ("사무실 정수기", "장소:실내·사무실")]
# 배포 사전이 다른 낱말 앞머리에서 걸리면 안 되는 말(재발 검사 — 짧은 패턴을 새로 넣을 때 여기에 걸린다)
COLLISION_WORDS = ["아이콘", "아이스", "아이폰", "스키니", "젤리", "플라스틱", "수정과", "봄버", "유아인"]


def lexicon_boundary_tests(check):
    sys.path.insert(0, str(HERE))
    from cep_lib import Lexicon
    from series_dedup import canon
    lex = Lexicon(json.loads(THEMES.read_text(encoding="utf-8")), WP_SENSE)
    for kw, old, theme in P0_1_FALSE:
        check(old in canon(kw), f"픽스처 오류: «{kw}» canonical 에 v0.1.0 오탐 «{old}»가 없다")
        m = lex.match(kw)
        check(theme not in m["facets"], f"P0-1: «{kw}»가 낱말 경계를 넘어 «{theme}»에 걸린다(v0.1.0 «{old}» 오탐 재발)", str(m))
    for kw, theme in P0_1_TRUE:
        m = lex.match(kw)
        check(m["bucket"] == theme and m["kind"] == "상황" and m["scope"] == "어절",
              f"P0-1: 참 사례 «{kw}»가 «{theme}»(어절 일치)로 분류되지 않는다", str(m))
    m = lex.match("아기분유정수기")
    check(m["bucket"] == "사람:아기·아이" and m["scope"] == "어절 일부",
          "P0-1: 붙여 쓴 합성어의 어절 앞부분 일치를 «어절 일부»로 표시하지 않는다", str(m))
    for w in COLLISION_WORDS:
        m = lex.match(w)
        check(not m["facets"], f"배포 사전 충돌: «{w}»가 다른 낱말 앞머리 패턴에 걸린다", str(m))
    lex2 = Lexicon({"themes": [{"name": "장소:캠핑·야외", "pattern": "간이정수기|휴대용"}]}, WP_SENSE)
    check(lex2.match("간이 정수기")["bucket"] == "장소:캠핑·야외",
          "P0-1: 패턴에 적힌 카테고리어가 마스킹과 같이 바뀌지 않아 «간이정수기» 패턴이 죽는다")


def _months(start, n):
    y, m = int(start[:4]), int(start[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def rank_window_tests(check):
    """P0-2: 기준월(마지막 달) 한 달 폭증이 순위를 움직이면 안 된다. 순위 창 = 추세 창(마지막 달 제외)."""
    months = _months("2023-11", 36)            # 기준월 2026-10
    flat = lambda v, last=None: [{"month": mo, "gg": 0, "nv": v, "total": (last if (last is not None and mo == months[-1]) else v)}
                                 for mo in months]
    series = {"정수기": flat(80000), "아기 분유 정수기": flat(2000), "사무실 정수기": flat(1800, last=20000),
              "이사 정수기": flat(1500)}
    vols = {"직수정수기": 300000, "직수 정수기": 120000, "얼음정수기미니": 5000, "정수기미세플라스틱": 30000,
            "정수 영어로": 5000, "미세플라스틱": 60000, "미세 플라스틱": 20000, "아이콘 정수기": 40000}
    ads = []
    for kw, ser in series.items():
        tot = sum(x["total"] for x in ser[-12:])
        ads.append({"keyword": kw, "ads_metrics": {"volume_total": tot, "volume_trend": 0.9}})
    ads += [{"keyword": k, "ads_metrics": {"volume_total": v, "volume_trend": 0.9}} for k, v in vols.items()]
    kws = [r["keyword"] for r in ads]
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        w = lambda n, o: (t / n).write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        w("sense.json", WP_SENSE)
        (t / "themes.json").write_text(THEMES.read_text(encoding="utf-8"), encoding="utf-8")
        w("if_정수기.json", {"request_detail": {"keywords": ["정수기"], "limit": 100}, "data": kws})
        w("metrics_ads.json", {"request_detail": {"keywords": kws}, "data": ads})
        w("metrics_all.json", {"request_detail": {"keywords": list(series)},
                               "data": [{"keyword": k, "ads_metrics": {"volume_total": sum(x["total"] for x in v[-12:])},
                                         "monthly_volume": v} for k, v in series.items()]})
        rc, out = run([HERE / "situation_pool.py", "--sense", "sense.json", "--themes", "themes.json",
                       "--intent", "if_정수기.json@100", "-o", "pool.csv", "--keywords-out", "pool_keywords.json",
                       "--calls-out", "pool_calls.csv"], t)
        check(rc == 0, "P0 회귀 픽스처: situation_pool 실패", out)
        rc, out = run([HERE / "cep_map.py", "--pool", "pool.csv", "--metrics", "metrics_ads.json", "--monthly", "metrics_all.json",
                       "--sense", "sense.json", "--themes", "themes.json", "--queried-at", "2026-11-20", "--out-dir", "out"], t)
        check(rc == 0, "P0 회귀 픽스처: cep_map 실패", out)
        if rc != 0:
            return
        doc = json.loads((t / "out" / "cep_map.json").read_text(encoding="utf-8"))
        C = {c["cep"]: c for c in doc["ceps"]}
        K = {r["keyword"]: r for r in csv.DictReader(open(t / "out" / "cep_keywords.csv", encoding="utf-8-sig"))}
        # P0-1 — 지도 수준: 오탐 CEP가 생기지 않고 참 사례는 남는다
        for kw, _, theme in P0_1_FALSE:
            check(K[kw]["cep"] != theme, f"P0-1: «{kw}»가 지도에서 오탐 CEP «{theme}»로 집계됐다",
                  str({k: K[k]["cep"] for k in vols}))
        for theme in ("문제:땀·피지·지속", "목표:예방·관리", "장소:바다·물놀이"):
            check(theme not in C, f"P0-1: 해당 검색어가 없는데 지도에 «{theme}» CEP가 생겼다")
        for kw, theme in P0_1_TRUE:
            check(K[kw]["cep"] == theme and C[theme]["priority_rank"] != "", f"P0-1: 참 사례 «{kw}»가 «{theme}» 순위에 없다")
        check(K["직수정수기"]["kind"] == "미분류" and K["직수 정수기"]["cep"] == K["직수정수기"]["cep"],
              "P0-1: «직수정수기» 그룹이 미분류가 아니거나 같은 canonical 표기가 서로 다른 CEP로 갈렸다")
        check(doc["meta"]["partial_token_share_lo"] is not None and "partial_token_share" in C["사람:아기·아이"],
              "P0-1: 정밀도 신호(partial_token_share)가 지도에 기록되지 않는다")
        # P0-2 — 순위 창
        baby, office, move = C["사람:아기·아이"], C["장소:실내·사무실"], C["계기:이사·입주·결혼"]
        check(doc["meta"]["rank_window"] == "2025-10~2026-09" and baby["rank_window"] == "2025-10~2026-09",
              "P0-2: 순위 창이 추세 창(마지막 달 제외)과 다르다", str(doc["meta"].get("rank_window")))
        check(baby["priority_rank"] == 1 and office["priority_rank"] == 2 and move["priority_rank"] == 3,
              "P0-2: 마지막 달 폭증 검색어(사무실 정수기)가 순위를 끌어올렸다",
              str({c["cep"]: (c["priority_rank"], c["window_nonbrand_lo"]) for c in (baby, office, move)}))
        check(office["window_nonbrand_lo"] == 21600 and office["nonbrand_volume_lo"] == 39800
              and office["priority_rank_incl_last"] == 1 and office["rank_shift_incl_last"] == "1→2"
              and office["window_series_share"] == 1.0,
              "P0-2: 마지막 달 포함 옆 열 · 순위 변동 표시가 틀렸다",
              str({k: office[k] for k in ("window_nonbrand_lo", "nonbrand_volume_lo", "priority_rank_incl_last",
                                          "rank_shift_incl_last", "window_series_share")}))
        check(any("장소:실내·사무실" in x for x in doc["meta"]["rank_shifts_incl_last"]),
              "P0-2: 순위 변동 CEP가 지도 머리에 없다")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    fails = []

    def check(cond, what, detail=""):
        if not cond:
            fails.append((what, detail[-300:] if detail else ""))

    S = HERE
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        for f in FX.glob("*.json"):
            (t / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        (t / "themes.json").write_text(THEMES.read_text(encoding="utf-8"), encoding="utf-8")
        (t / "raw").mkdir()

        # STEP 0 — 사용자 확인 전 뜻 고정 파일은 거부
        s = json.loads((t / "sense.json").read_text(encoding="utf-8")); s["confirmed_by_user"] = False
        (t / "sense_unconfirmed.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        rc, out = run([S / "situation_pool.py", "--sense", "sense_unconfirmed.json", "--themes", "themes.json",
                       "--intent", "if_선크림.json@12"], t)
        check(rc != 0 and "confirmed_by_user" in out, "STEP 0: 확인 전 sense.json을 거부하지 않는다", out)

        # STEP 1~2 — 원장 · 파일럿 · 예산 게이트
        L = [S / "ledger.py", "add", "--ledger", "ledger.jsonl"]
        for args in (["--tool", "intent_finder", "--step", "확장", "--raw", "if_선크림.json", "--limit", "12"],
                     ["--tool", "intent_finder", "--step", "확장", "--raw", "if_여름선크림.json", "--limit", "50"],
                     ["--tool", "intent_finder", "--step", "확장", "--raw", "if_아기선크림.json", "--limit", "50"],
                     ["--tool", "path_finder", "--step", "경로", "--raw", "path_pilot.json", "--limit", "3"]):
            rc, out = run(L + args + ["--at", "2026-09-28 10:00"], t)
            check(rc == 0, "ledger add 실패", out)
        rc, out = run([S / "path_gate.py", "--budget", "3000", "--ledger", "ledger.jsonl", "--reserve", "200",
                       "--limit", "10", "--pilot", "path_pilot.json", "--json"], t)
        g = json.loads(out) if rc == 0 else {}
        check(g.get("decision") == "call" and g.get("planned_cost") == 50 + 40 * 24 and g.get("spent") == 460,
              "path_gate: 파일럿 r · 원장 소진으로 계획을 세우지 않는다", out)
        rc, out = run([S / "path_gate.py", "--budget", "1200", "--ledger", "ledger.jsonl", "--limit", "10",
                       "--pilot", "path_pilot.json", "--json"], t)
        g = json.loads(out) if rc == 0 else {}
        check(g.get("decision") == "hold" and g.get("max_limit_in_room", -1) >= 0
              and 50 + 40 * -(-g["max_limit_in_room"] * 7 // 3) <= g["room"],
              "path_gate: 예산 초과를 hold로 막고 예산 안 최대 limit을 내지 않는다", out)
        for args in (["--tool", "path_finder", "--step", "경로", "--raw", "path_main.json", "--limit", "10"],
                     ["--tool", "keyword_info", "--step", "볼륨", "--raw", "metrics_ads.json"],
                     ["--tool", "keyword_info", "--step", "시계열", "--raw", "metrics_all.json"]):
            rc, out = run(L + args, t)
            check(rc == 0, "ledger add 실패", out)
        rc, out = run([S / "ledger.py", "total", "--ledger", "ledger.jsonl"], t)
        check("1,301" in out, "ledger total: cost_detail 합이 맞지 않는다", out)

        # STEP 3 — 후보 풀 · 절단(T7) · 경계 밖 · 시드 표기 변경
        pool_cmd = [S / "situation_pool.py", "--sense", "sense.json", "--themes", "themes.json",
                    "--intent", "if_선크림.json@12", "--intent", "if_여름선크림.json@50", "--intent", "if_아기선크림.json@50",
                    "--paths", "path_main.json@10", "-o", "pool.csv", "--keywords-out", "pool_keywords.json",
                    "--calls-out", "pool_calls.csv"]
        rc, out = run(pool_cmd, t)
        check(rc == 0, "situation_pool 비정상 종료", out)
        if rc == 0:
            calls = list(csv.DictReader(open(t / "pool_calls.csv", encoding="utf-8-sig")))
            check(any(c["truncated"] == "Y" and c["seed"] == "선크림" for c in calls) and "보류" in out,
                  "T7: 반환 = limit 호출을 절단으로 기록하지 않는다", out)
            kws = json.loads((t / "pool_keywords.json").read_text(encoding="utf-8"))
            check("선크림 노래" not in kws and "여름 휴가 준비물" in kws,
                  "뜻 고정: exclude 검색어를 요청 목록에서 빼지 않거나 시드 앞 검색을 놓친다", str(kws))
            check(len(kws) == len(json.loads((t / "metrics_ads.json").read_text(encoding="utf-8"))["request_detail"]["keywords"]),
                  "픽스처 불일치: 요청 목록과 metrics_ads 요청 수가 다르다")
        norule = json.loads(THEMES.read_text(encoding="utf-8")); norule["rules"]["변형치환"].pop("썬", None)
        (t / "themes_norule.json").write_text(json.dumps(norule, ensure_ascii=False), encoding="utf-8")
        rc, out = run([S / "situation_pool.py", "--sense", "sense.json", "--themes", "themes_norule.json",
                       "--paths", "path_variant.json@5", "-o", "v.csv", "--keywords-out", "v.json", "--calls-out", "vc.csv"], t)
        check(rc == 3 and "변형치환" in out, "시드 표기 변경(썬크림만 온 경로)에서 멈추지 않는다", out)
        rc, out = run([S / "situation_pool.py", "--sense", "sense.json", "--themes", "themes.json",
                       "--paths", "path_variant.json@5", "-o", "v.csv", "--keywords-out", "v.json", "--calls-out", "vc.csv"], t)
        check(rc == 0, "변형치환 규칙이 있는데도 시드 표기 변경에서 멈춘다", out)

        # STEP 5 — 2단 대상(규칙) · 크레딧 10 × M
        rc, out = run([S / "select_stage2.py", "--pool", "pool.csv", "--metrics", "metrics_ads.json", "--sense", "sense.json",
                       "--themes", "themes.json", "-o", "stage2_keywords.json"], t)
        check(rc == 0, "select_stage2 비정상 종료", out)
        if rc == 0:
            s2 = json.loads((t / "stage2_keywords.json").read_text(encoding="utf-8"))
            fx = json.loads((t / "metrics_all.json").read_text(encoding="utf-8"))["request_detail"]["keywords"]
            check(s2["credit"] == 10 * len(s2["keywords"]) and sorted(s2["keywords"]) == sorted(fx),
                  "2단 대상이 픽스처 metrics_all 요청과 다르거나 크레딧이 10 × M 이 아니다", out)
            check("바다 여행" not in s2["keywords"] and "선크림 추천" not in s2["keywords"] and "선크림" in s2["keywords"],
                  "2단 대상 규칙: 카테고리어 없는 상황·비상황을 사거나 기준선을 빼먹는다", str(s2["keywords"]))

        # STEP 5.5 — 기준월(T1) · 마지막 달(T2)
        rc, out = run([S / "check_refmonth.py", "metrics_all.json", "--queried-at", "2026-09-28"], t)
        check(rc == 0 and "2026-08" in out and "이상 의심: 1행" in out, "check_refmonth: 기준월·마지막 달 급락 미검출", out)

        # STEP 6 — CEP 지도
        map_cmd = [S / "cep_map.py", "--pool", "pool.csv", "--metrics", "metrics_ads.json", "--monthly", "metrics_all.json",
                   "--sense", "sense.json", "--themes", "themes.json", "--paths", "path_main.json@10",
                   "--calls", "pool_calls.csv", "--stage2", "stage2_keywords.json", "--queried-at", "2026-09-28",
                   "--coverage", "보류(R1 절단)", "--out-dir", "out"]
        rc, out = run(map_cmd, t)
        check(rc == 0, "cep_map 비정상 종료", out)
        if rc == 0:
            doc = json.loads((t / "out" / "cep_map.json").read_text(encoding="utf-8"))
            m = doc["meta"]
            C = {c["cep"]: c for c in doc["ceps"]}
            K = {r["keyword"]: r for r in csv.DictReader(open(t / "out" / "cep_keywords.csv", encoding="utf-8-sig"))}
            check(m["data_ref_month"] == "2026-08" and m["lag_months"] == 1, "T1: 기준월·지연을 응답에서 읽지 않는다", str(m)[:200])
            check(m["window_end"] == "2026-07" and m["last_month_suspect"] >= 1, "T2: 마지막 달을 기본 제외하지 않거나 의심 건수 없음")
            check(abs(float(C["시점:여름"]["yoy_12m"]) - 0.10) < 0.005, "T2: 마지막 달 급락이 여름 CEP 추세에 섞였다",
                  str(C["시점:여름"]["yoy_12m"]))
            check(all(str(c["yoy_12m"]) not in ("0.9", "0.9000") for c in doc["ceps"]), "T3: volume_trend(0.9)가 추세로 쓰였다")
            sk = C["장소:스키·겨울레저"]
            check(sk["gated_out"] == "Y" and sk["priority_rank"] == "" and sk["trend_call"] == "판정 보류",
                  "T4: 게이트 미달 CEP가 순위·추세 판정에 들어갔다", str({k: sk[k] for k in ("gated_out", "priority_rank", "trend_call")}))
            check(K["선 크림"]["dup_of"] == "선크림" and m["merged_identical"] == 1,
                  "T5: 같은 검색 중복(dup_of) · 응답 안 완전 중복 행 합침이 안 된다")
            core = C["카테고리 코어"]
            check(core["volume_12m_lo"] == 840000 and core["volume_12m_hi"] == 1140000,
                  "T5: 표기변형 그룹 하한(최댓값)·상한(합) 병기가 틀렸다", f"{core['volume_12m_lo']} {core['volume_12m_hi']}")
            golf = C["장소:골프·야외운동"]
            check(golf["series_excluded_missing"] == 1 and K["골프 선크림"]["series_status"] == "결측 월",
                  "T6: 결측 월 검색어를 시계열 모집단에서 빼지 않는다")
            check(any("intent_finder 선크림" in x for x in m["truncated_calls"]), "T7: 절단 호출이 지도 머리에 없다")
            check(golf["brand_link_call"] == "경쟁 브랜드 우위" and golf["top_competitor"] == "아넷사"
                  and golf["nonbrand_volume_lo"] < golf["volume_12m_lo"], "T8: 브랜드 지배 CEP를 분리하지 못한다")
            rank = {c["cep"]: c["priority_rank"] for c in doc["ceps"] if c["priority_rank"] != ""}
            check(rank.get("사람:아기·아이") == 1 and rank.get("장소:골프·야외운동") == 4,
                  "T8: 우선순위가 논브랜드 하한 순이 아니다", str(rank))
            check(C["사람:아기·아이"]["brand_link_call"] == "기준 브랜드 우위"
                  and C["장소:바다·물놀이"]["brand_link_call"] == "미점유(브랜드 연결 약함)", "브랜드 연결 판정 규칙 불일치")
            win = C["시점:겨울"]
            check({"12", "1"} <= set(str(win["peak_months"]).split("|")) and "7" in str(C["시점:여름"]["peak_months"]).split("|")
                  and win["yoy_3m"] == "", "역위상 계절: 겨울 CEP 성수기·3개월 게이트 처리가 틀렸다",
                  f"{win['peak_months']} {win['yoy_3m']}")
            check("미분류" in C and C["미분류"]["volume_12m_lo"] == 48000 and K["아기 외출"]["returned"] == "N",
                  "미분류 버킷 · 미반환 행이 없다")
            check(C["장소:바다·물놀이"]["situation_only_volume_lo"] == 480000 and C["장소:바다·물놀이"]["path_pre_paths"] == 1,
                  "시드 앞 검색(상황만)이 따로 집계되지 않는다")
            # 문서 컬럼 = 실제 출력
            doc_cols = documented()
            for fn, path in (("pool.csv", t / "pool.csv"), ("pool_calls.csv", t / "pool_calls.csv"),
                             ("cep_keywords.csv", t / "out" / "cep_keywords.csv"), ("cep_map.csv", t / "out" / "cep_map.csv"),
                             ("cep_brand.csv", t / "out" / "cep_brand.csv")):
                check(doc_cols.get(fn) == header(path), f"SKILL.md 산출물 컬럼 ≠ 실제 출력: {fn}",
                      f"문서 {doc_cols.get(fn)} / 실제 {header(path)}")
            rc, out = run(map_cmd[:-2] + ["--include-last-month", "--out-dir", "out_last"], t)
            d2 = json.loads((t / "out_last" / "cep_map.json").read_text(encoding="utf-8")) if rc == 0 else {"meta": {}}
            check(d2["meta"].get("window_end") == "2026-08" and d2["meta"].get("last_month") == "포함",
                  "--include-last-month 가 창을 바꾸지 않는다", out)

            # STEP 8 — 리포트(사실 · 호출 원장) → 공통 렌더러
            rc, out = run([S / "build_report.py", "--map", "out/cep_map.json", "--ledger", "ledger.jsonl", "-o", "report.json"], t)
            check(rc == 0, "build_report 비정상 종료", out)
            if rc == 0:
                rep = json.loads((t / "report.json").read_text(encoding="utf-8"))
                check(len(rep["provenance"]["calls"]) == 7 and sum(c["cost"] for c in rep["provenance"]["calls"]) == 1301,
                      "provenance.calls 가 원장과 다르다")
                rc, out = run([S / "render_report.py", "report.json", "-o", "report.html"], t)
                check(rc == 0 and (t / "report.calls.csv").is_file(), "report.json 이 렌더링 계약을 어긴다", out)
                bad = dict(rep); bad["lede_html"] = "논브랜드 검색량 64,263건"
                (t / "bad.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
                rc, out = run([S / "render_report.py", "bad.json", "--check"], t)
                check(rc == 1, "사실 목록을 거치지 않은 숫자를 렌더러가 막지 않는다", out)
        rc, out = run([S / "render_report.py", SKILL / "assets" / "report.example.json", "-o", "ex.html"], t)
        check(rc == 0, "assets/report.example.json 이 렌더되지 않는다", out)

    lexicon_boundary_tests(check)
    rank_window_tests(check)

    if fails:
        print(f"selftest_cep 실패 {len(fails)}건")
        for w, d in fails:
            print(f"  ✗ {w}" + (f"\n      {d.strip()[:300]}" if d else ""))
        return 1
    print("selftest_cep 통과 — STEP 0~8 명령 · 함정 T1~T8 · 문서 컬럼 = 실제 출력 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
