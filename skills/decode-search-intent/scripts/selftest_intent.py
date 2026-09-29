#!/usr/bin/env python3
"""decode-search-intent 고유 자가검증 — SKILL.md의 STEP 4~6 예시 명령을 그대로 돌린다.

`selftest.py`는 공유 정본의 사본이라(해시 검사 대상) 이 스킬 고유 스크립트(`intent_layers.py`)를
모른다. 그래서 고유 스크립트 검증은 이 파일이 맡는다. 두 파일을 함께 돌려야 STEP 3이 끝난다.

검사하는 것:
  ① 문서의 명령 순서(build_universe → coverage_curve → pick → report)가 실제로 돈다
  ② intent_layers.csv 헤더 = SKILL.md «산출물» 블록의 컬럼 목록 (집합 일치 — 많아도 적어도 실패)
  ③ T5 — 표기변형(런닝화/러닝화)이 한 canonical 그룹이 되고 group_volume_lo = 그룹 최댓값,
         group_volume_hi = 그룹 합이다(lo≠hi). 요약에 «하한 X ~ 상한 Y»가 찍힌다
  ③' 상한 기준에서 층 순위가 뒤집히는 픽스처(족저근막염 운동/족저근막염운동)가 ⚠️로 보고된다
  ④ T7 — 반환 수 = limit인 호출이 «절단»으로 보고된다
  ⑤ T8 — 요약에 논브랜드 분포 컬럼이 있고, 브랜드 검색어가 L5로 분류된다
  ⑥ 사전에 안 걸린 검색어는 «미분류»로 남는다(억지 분류 금지)
  ⑦ 데이터 기준월이 2단 응답의 monthly_volume에서 읽혀 헤더에 찍힌다
  회귀(2026-09-28 실데이터 평가 결함):
  P0   지표가 완전히 같은 어순 변형(여성 러닝화 추천/러닝화 추천 여성)은 canonical이 달라도 한 번만 센다
  P1-1 coverage_curve 한 라운드 쉼표 문법 — 절단 호출이 섞이면 «보류»(«도달» 금지), --rules에 사전 파일
  P1-2 짧은 브랜드(«려»)는 단어 일치만 — «두피 가려움»이 L5로 가지 않는다
  P1-3 intents 플래그 — 구매 단계 + {c:1,t:1}은 일치(최댓값 동률 처리로 c를 고르지 않는다)
  P1-4 pick 층별 표본은 시드 밖 층 기준 — 주 층 L3인 «무릎 통증 러닝화»가 L1 표본으로 뽑힌다
  P1-5 upstream 판정 — 절단 + 시드 밖 L1·L2 그룹 < 10이면 «발동»
  P2   경계 밖(exclude) 행 제외 · 일치율 표본 경고 · 2단 미반환 표기 · 문서에 «약 2개월»·[파생] 없음

합성 픽스처만 쓴다 — API 호출·크레딧 없음.

    python3 scripts/selftest_intent.py        # 스킬 폴더에서 실행
"""
import csv, json, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent


def ki(kw, vol, brandish=False):
    return {"keyword": kw, "ads_metrics": {"volume_total": vol, "volume_avg": vol // 12,
            "volume_trend": 0.1, "cpc": 0.3, "competition": "LOW", "competition_index": 10,
            "gg_volume_total": vol // 3, "nv_volume_total": vol - vol // 3}}


def fixture(tmp):
    kws = [("러닝화", 120000), ("런닝화", 118000), ("나이키 페가수스 41", 40000),
           ("평발 러닝화 추천", 9000), ("족저근막염 운동", 30000), ("족저근막염운동", 25000), ("러닝화 최저가", 7000),
           ("하프마라톤 준비", 5000), ("등산양말", 3000),
           ("여성 러닝화 추천", 6000), ("러닝화 추천 여성", 6000),     # 지표 동일 어순 변형 → 같은 검색(P0)
           ("무릎 통증 러닝화", 2000),                               # 주 층 L3 · 시드 밖 L1 (P1-4)
           ("등산화 추천", 50000)]                                  # 경계 밖(exclude) — 상위 볼륨이어도 제외
    (tmp / "ki_metrics.json").write_text(json.dumps({"data": [ki(k, v) for k, v in kws]},
                                         ensure_ascii=False), encoding="utf-8")
    (tmp / "seeds.json").write_text(json.dumps(["러닝화", "평발 러닝화"], ensure_ascii=False), encoding="utf-8")
    # 호출 1: limit 5에 반환 5 → 절단 / 호출 2: limit 100에 반환 3 → 미절단
    (tmp / "if_러닝화.json").write_text(json.dumps({"data": ["런닝화", "나이키 페가수스 41",
        "러닝화 최저가", "하프마라톤 준비", "등산양말"]}, ensure_ascii=False), encoding="utf-8")
    # L1 족저근막염 그룹: 하한 30,000 < L5 40,000 이지만 상한 55,000 > L5 → 층 순위 뒤집힘
    (tmp / "if_평발.json").write_text(json.dumps({"data": ["평발 러닝화 추천", "족저근막염 운동",
        "족저근막염운동", "러닝화", "여성 러닝화 추천", "러닝화 추천 여성", "무릎 통증 러닝화", "등산화 추천"]},
        ensure_ascii=False), encoding="utf-8")
    mv = [{"month": f"2026-{m:02d}", "gg": 10, "nv": 20, "total": 30} for m in range(1, 8)]
    mv.append({"month": "2026-08", "nv_code": 1})           # total 없는 행은 기준월로 세지 않는다
    # intents는 0/1 플래그 — 두 개가 동시에 1일 수 있다(러닝화 최저가: c·t)
    alls = [{"keyword": "러닝화", "intents": {"i": 0, "n": 0, "c": 1, "t": 0}, "monthly_volume": mv},
            {"keyword": "평발 러닝화 추천", "intents": {"i": 0, "n": 0, "c": 1, "t": 0}, "monthly_volume": mv},
            {"keyword": "러닝화 최저가", "intents": {"i": 0, "n": 0, "c": 1, "t": 1}, "monthly_volume": mv},
            {"keyword": "나이키 페가수스 41", "intents": {"i": 0, "n": 1, "c": 0, "t": 0}, "monthly_volume": mv}]
    (tmp / "ki_all.json").write_text(json.dumps({"data": alls}, ensure_ascii=False), encoding="utf-8")
    # STEP 3: 예시 사전을 복사해 쓴다
    shutil.copyfile(SKILL / "assets" / "intent_dict.example.json", tmp / "intent_dict.json")


def run(cmd, tmp):
    p = subprocess.run([sys.executable] + cmd, cwd=tmp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def documented_columns():
    """SKILL.md «산출물»의 intent_layers CSV 블록에서 컬럼 목록을 읽는다."""
    doc = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"intent_layers_\{category\}\.csv\n(.*?)\n\n", doc, re.S)
    if not m:
        return None
    return {c.strip() for c in m.group(1).replace("\n", ",").split(",") if c.strip()}


def main():
    fails = []
    S = str(HERE)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)

        rc, out = run([f"{S}/build_universe.py", "--metrics", "ki_metrics.json",
                       "--themes", "intent_dict.json", "--seeds", "seeds.json",
                       "--round", "S·시드=seeds.json", "--round", "R1·러닝화=if_러닝화.json",
                       "--round", "R1·평발 러닝화=if_평발.json", "-o", "keyword_universe.csv"], tmp)
        if rc:
            fails.append(("build_universe", "사전을 --themes로 넘기면 비정상 종료", out[-300:]))

        # P1-1 — 시드별 호출은 **한 라운드**로 쉼표 나열한다. 순차 라운드로 넘기면 절단 뒤 라운드의
        # 작은 기여율이 «도달»로 읽힌다. --rules는 사전 파일(rules.변형치환)을 그대로 받는다(P2-1)
        rc, out = run([f"{S}/coverage_curve.py", "--metrics", "ki_metrics.json",
                       "--rules", "intent_dict.json", "--themes", "intent_dict.json",
                       "--round", "S·시드=seeds.json",
                       "--round", "R1=if_러닝화.json@5,if_평발.json@100"], tmp)
        if rc or "절단된 라운드" not in out:
            fails.append(("coverage_curve", "limit에 붙은 호출을 절단으로 표시하지 않음", out[-300:]))
        elif "보류" not in out or "커버리지 도달" in out:
            fails.append(("P1-1", "한 라운드 쉼표 문법에서 절단 호출이 있는데 «보류»가 아니다", out[-300:]))
        rc, out = run([f"{S}/intent_layers.py", "upstream", "--universe", "keyword_universe.csv",
                       "--dict", "intent_dict.json", "--seeds", "seeds.json",
                       "--call", "R1=if_러닝화.json@5,if_평발.json@100"], tmp)
        if rc or "**발동**" not in out or "족저근막염 운동" not in out:
            fails.append(("P1-5", "절단 + 시드 밖 L1·L2 3그룹(<10)인데 upstream이 «발동»·후보를 내지 않음", out[-300:]))

        if not fails:
            rc, out = run([f"{S}/intent_layers.py", "pick", "--universe", "keyword_universe.csv",
                           "--dict", "intent_dict.json", "--seeds", "seeds.json",
                           "--top", "3", "--per-layer", "1", "-o", "gated.json"], tmp)
            if rc:
                fails.append(("intent_layers pick", "비정상 종료", out[-300:]))
            else:
                gated = json.loads((tmp / "gated.json").read_text(encoding="utf-8"))
                if "런닝화" in gated and "러닝화" in gated:
                    fails.append(("intent_layers pick", "표기변형 두 개를 모두 2단 대상으로 뽑았다(10크레딧 이중 지출)", ""))
                if "무릎 통증 러닝화" not in gated:
                    fails.append(("P1-4", "주 층 L3·시드 밖 L1인 검색어가 L1 표본으로 뽑히지 않음", str(gated)))
                if "등산화 추천" in gated or "여성 러닝화 추천" in gated:
                    fails.append(("P0/P2", "경계 밖 행이나 같은 검색의 중복 행을 2단 대상으로 뽑았다", str(gated)))
                if not re.search(r"10 × \d+ = ", out):
                    fails.append(("intent_layers pick", "2단 크레딧 결정식이 출력되지 않음", out[-200:]))

            rc, out = run([f"{S}/intent_layers.py", "report", "--universe", "keyword_universe.csv",
                           "--dict", "intent_dict.json", "--seeds", "seeds.json", "--intents", "ki_all.json",
                           "--gated", "gated.json",
                           "--call", "러닝화=if_러닝화.json@5", "--call", "평발 러닝화=if_평발.json@100",
                           "--queried-at", "2026-09-28", "--title", "러닝화",
                           "-o", "intent_layers.csv", "--report", "intent_summary.md"], tmp)
            if rc:
                fails.append(("intent_layers report", "문서 예시로 비정상 종료", out[-300:]))
            else:
                rows = list(csv.DictReader(open(tmp / "intent_layers.csv", encoding="utf-8-sig")))
                header = set(rows[0].keys())
                doc = documented_columns()
                if doc is None:
                    fails.append(("SKILL.md", "산출물 블록(intent_layers_{category}.csv)을 찾지 못함", ""))
                elif doc != header:
                    fails.append(("컬럼 계약", f"문서에만: {sorted(doc - header)} · 코드에만: {sorted(header - doc)}", ""))
                by = {r["keyword"]: r for r in rows}
                a, b = by.get("러닝화"), by.get("런닝화")
                if not a or not b or a["canonical"] != b["canonical"]:
                    fails.append(("T5", "런닝화/러닝화가 한 그룹으로 묶이지 않음", ""))
                elif (a["group_volume_lo"], a["group_volume_hi"], b["group_volume_lo"], b["group_volume_hi"]) \
                        != ("120000", "238000", "120000", "238000"):
                    fails.append(("T5", "group_volume_lo/hi가 그룹 최댓값/그룹 합이 아님",
                                  f"{a['group_volume_lo']}/{a['group_volume_hi']}"))
                if (by.get("등산양말", {}).get("group_volume_lo"), by.get("등산양말", {}).get("group_volume_hi")) != ("3000", "3000"):
                    fails.append(("T5", "변형 없는 그룹은 하한 = 상한이어야 한다", ""))
                if by.get("나이키 페가수스 41", {}).get("layer") != "L5 브랜드":
                    fails.append(("T8", "브랜드 검색어가 L5로 분류되지 않음", ""))
                if by.get("등산양말", {}).get("layer") != "미분류":
                    fails.append(("미분류", "사전에 없는 검색어가 층에 억지로 들어감", ""))
                if by.get("평발 러닝화 추천", {}).get("stage_api_agree") != "Y":
                    fails.append(("교차확인", "비교 단계 vs API c 최댓값이 일치로 잡히지 않음", ""))
                md = (tmp / "intent_summary.md").read_text(encoding="utf-8")
                if by.get("여성 러닝화 추천", {}).get("dup_of") != "러닝화 추천 여성":
                    fails.append(("P0", "지표가 같은 어순 변형이 같은 검색의 중복으로 묶이지 않음", ""))
                if "같은 검색의 중복 1행 병합" not in md:
                    fails.append(("P0", "요약 헤더에 병합 행 수가 없다", md[:400]))
                if by.get("등산화 추천", {}).get("boundary_excluded") != "Y" or "경계 밖 1행 제외" not in md:
                    fails.append(("P2", "exclude 패턴에 걸린 행이 경계 밖으로 제외되지 않음", ""))
                if by.get("러닝화 최저가", {}).get("stage_api_agree") != "Y" \
                        or by.get("러닝화 최저가", {}).get("api_intent_flags") != "c|t":
                    fails.append(("P1-3", "구매 단계 + 플래그 c·t가 일치로 잡히지 않음(최댓값 동률 처리)",
                                  json.dumps(by.get("러닝화 최저가", {}), ensure_ascii=False)[:200]))
                if "표본이 작아" not in md:
                    fails.append(("P2", "대조 그룹이 20 미만인데 일치율 표본 경고가 없다", ""))
                if "미반환 [데이터 공백]" not in md:
                    fails.append(("P2", "2단 요청 중 응답에 없는 키워드를 미반환으로 적지 않음", ""))
                if "하한 222,000 ~ 상한 365,000" not in md:
                    fails.append(("T5", "요약 헤더에 그룹 볼륨 «하한 ~ 상한» 범위가 없다", md[:300]))
                if "«L1 니즈» 3→2위" not in md or "순위 뒤집힘(전체)" not in md:
                    fails.append(("T5", "상한 기준 층 순위 뒤집힘(L1↔L5)이 보고되지 않음", ""))
                if "순위 뒤집힘" not in out:
                    fails.append(("T5", "콘솔 출력에 순위 뒤집힘 요약이 없다", out[-200:]))
                if "절단 — " not in md:
                    fails.append(("T7", "limit=반환 호출이 요약에서 절단으로 보고되지 않음", ""))
                if "논브랜드 비중 하한 기준" not in md or "논브랜드 비중 상한 기준" not in md:
                    fails.append(("T8", "요약에 논브랜드 분포가 없음", ""))
                if "데이터 기준월: 2026-07" not in md:
                    fails.append(("기준월", "total 있는 최신 월(2026-07)이 헤더에 찍히지 않음", md[:200]))

        # P1-2 — 짧은 브랜드 가드 (별도 미니 픽스처)
        mini = tmp / "mini"; mini.mkdir()
        (mini / "m.json").write_text(json.dumps({"data": [ki("두피 가려움", 1000), ki("려 샴푸", 500)]},
                                     ensure_ascii=False), encoding="utf-8")
        (mini / "d.json").write_text(json.dumps({"themes": [{"name": "L1 니즈", "pattern": "가려움"}],
            "brands": ["려"], "stages": [{"name": "비교", "pattern": "추천"}],
            "no_match": {"with_brand": "L5 브랜드", "without_brand": "미분류"}}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([f"{S}/build_universe.py", "--metrics", "m.json", "--themes", "d.json", "-o", "u.csv"], mini)
        rc2, out2 = run([f"{S}/intent_layers.py", "report", "--universe", "u.csv", "--dict", "d.json",
                         "-o", "l.csv", "--report", "s.md"], mini)
        if rc or rc2:
            fails.append(("P1-2", "미니 픽스처 실행 실패", (out + out2)[-300:]))
        else:
            mb = {r["keyword"]: r for r in csv.DictReader(open(mini / "l.csv", encoding="utf-8-sig"))}
            if mb["두피 가려움"]["layer"] != "L1 니즈" or mb["려 샴푸"]["layer"] != "L5 브랜드":
                fails.append(("P1-2", "짧은 브랜드 «려»가 «가려움»에 부분일치했거나 단어 일치를 놓쳤다",
                              f"{mb['두피 가려움']['layer']} / {mb['려 샴푸']['layer']}"))
            if "브랜드 가드" not in out2:
                fails.append(("P1-2", "가드로 막은 행 수를 출력하지 않음", out2[-200:]))

        # P2 — 문서 규율: 고정 지연 문구·[파생] 라벨 금지
        for f in [SKILL / "SKILL.md", HERE / "intent_layers.py"]:
            txt = f.read_text(encoding="utf-8")
            if "약 2개월" in txt or "[파생]" in txt:
                fails.append(("P2", f"{f.name}에 «약 2개월» 또는 [파생] 라벨이 남았다 — 기준월은 응답에서 읽고 라벨은 [추정]", ""))

    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {detail.strip()[:200]}")
        print("\n문서의 예시와 스크립트가 어긋나 있다. 배포 전에 고칠 것.")
        return 1
    print("통과 — intent_layers.py 계약 검증 완료 (문서 예시 명령 · 컬럼 집합 · T5·T7·T8 · API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
