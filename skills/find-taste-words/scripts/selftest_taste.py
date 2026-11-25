#!/usr/bin/env python3
"""find-taste-words 고유 selftest — SKILL.md의 예시 명령을 **문서에 적힌 그대로** 합성 픽스처로 실행한다.

검사하는 것 (API · 크레딧 없음):
  · 문서의 명령 문자열이 SKILL.md에 그대로 있는가 → 그 문자열을 실행한다(scripts/ 만 절대 경로로 바꾼다)
  · 산출물 컬럼 목록(SKILL.md «산출물») = 실제 CSV 머리
  · T5 — 낱말 수준 변형이 대표 낱말 하나로 · 표기변형 그룹 하한 < 상한 · 같은 검색 중복 제외 ·
          같은 PAA 문장은 SERP 두 곳에 나와도 한 단위 · SERP 대상이 canonical 그룹당 하나
  · T7 — 반환 = limit 인 호출이 있으면 --coverage «수렴»을 덮어쓰고 «보류»
  · 회피(부정 표지) · 오탐 차단(고소장) · 최장일치 · 주입 문구는 데이터로만 · 감각어 희소 판정
  · 원장 — cost_detail 없는 파일 거부 · report.json 의 provenance = 원장 · 렌더 통과 · 견본과 일치
  · 영어 · 일본어 시작 사전의 회피 판정

    python3 scripts/selftest_taste.py      # 스킬 폴더에서 (scripts/selftest.py 가 함께 돌린다)
"""
import csv, json, os, pathlib, re, shlex, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
NOTE = "합성 픽스처 예시(assets/fixtures) — 실제 시장 값이 아니다"   # 견본 부제의 합성 표시
FX = ROOT / "assets" / "fixtures"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

CMDS = {
    "universe": 'python3 scripts/build_universe.py --metrics metrics_ads.json --themes themes.커피원두.json --seeds seeds.json '
                '--round "S1·시드=seeds.json" --round "R1·확장=intent_r1_커피원두.json" --round "R1·확장=intent_r1_커피원두맛.json" '
                '-o keyword_universe.csv',
    "coverage": 'python3 scripts/coverage_curve.py --metrics metrics_ads.json --rules themes.커피원두.json --themes themes.커피원두.json '
                '--round "S1·시드=seeds.json" --round "R1·확장=intent_r1_커피원두.json@20,intent_r1_커피원두맛.json@50"',
    "gate": 'python3 scripts/serp_gate.py --universe keyword_universe.csv --lexicon themes.커피원두.json --keyword 커피원두 '
            '--top 10 --top-taste 5 --max-credits 450 -o serp_targets.json',
    "ledger": 'python3 scripts/taste_ledger.py --ledger calls_ledger.json --step 확장 --tool intent_finder '
              '--raw intent_r1_커피원두.json --purpose "연관 검색어 확장"',
    "words": 'python3 scripts/taste_words.py --universe keyword_universe.csv --lexicon themes.커피원두.json --keyword 커피원두 '
             '--gl kr --serp serp_커피원두.json --serp serp_고소한원두.json --intent-call "R1=intent_r1_커피원두.json@20" '
             '--intent-call "R1=intent_r1_커피원두맛.json@50" --coverage "수렴(R1 기여 12%)" --queried-at 2026-11-19',
    "report": 'python3 scripts/build_report.py --summary taste_summary_커피원두.json --ledger calls_ledger.json -o report.json',
    "render": 'python3 scripts/render_report.py report.json -o report.html',
}


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def run(cmd, cwd):
    args = shlex.split(cmd)
    if args[0] == "python3":
        args[0] = sys.executable
    args = [str(HERE / a[len("scripts/"):]) if a.startswith("scripts/") else a for a in args]
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    return p.returncode, p.stdout + p.stderr


def header(p):
    return next(csv.reader(open(p, encoding="utf-8-sig")))


def read(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def doc_columns(md):
    """SKILL.md «산출물» 코드 블록 → {파일 이름 앞부분: [컬럼…]}."""
    sec = md.split("## 산출물", 1)[1].split("```", 2)[1]
    out, cur = {}, None
    for ln in sec.splitlines():
        m = re.match(r"^(taste_\w+?)_\{kw\}\.csv\s*$", ln.strip())
        if m:
            cur = m.group(1); out[cur] = []
        elif cur and ln.startswith("  ") and ln.strip():
            out[cur] += [c.strip() for c in ln.split(",") if c.strip()]
    return out


def lexicon(dst, cats):
    lx = json.loads((ROOT / "assets" / "themes.json").read_text(encoding="utf-8"))
    lx["category_terms"] = cats
    dst.write_text(json.dumps(lx, ensure_ascii=False), encoding="utf-8")


def main():
    utf8_console()
    fails = []

    def check(cond, name, detail=""):
        if not cond:
            fails.append((name, str(detail)[-400:]))
        return cond

    md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    for k, c in CMDS.items():
        check(c in md, f"SKILL.md 예시 명령({k})이 selftest 와 다르다 — 문서를 믿은 쪽이 다른 명령을 돌린다", c)

    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        for f in FX.iterdir():
            shutil.copy(f, t / f.name)
        lexicon(t / "themes.커피원두.json", ["원두", "커피"])
        lexicon(t / "themes.자동차보험.json", ["자동차보험", "보험"])
        (t / "seeds.json").write_text(json.dumps(["커피원두", "커피원두 맛"], ensure_ascii=False), encoding="utf-8")

        # ── STEP 3 · 4
        rc, out = run(CMDS["universe"], t); check(rc == 0, "build_universe 실패", out)
        rc, out = run(CMDS["coverage"], t)
        check(rc == 0 and "보류" in out, "coverage_curve: 반환 = limit 호출이 있는데 «보류»가 아니다 (T7)", out)
        rc, out = run(CMDS["gate"], t); check(rc == 0, "serp_gate 실패", out)
        gate = json.loads((t / "serp_targets.json").read_text(encoding="utf-8"))
        canons = [x["canonical"] for x in gate["targets"]]
        check(len(canons) == len(set(canons)), "SERP 대상에 같은 canonical 그룹이 두 번 — 표기변형에 30을 두 번 낸다 (T5)", canons)
        check(gate["credits"] == 30 * len(gate["targets"]), "SERP 크레딧이 30 × G 가 아니다", gate["credits"])
        check(any(x["lane"].startswith("B") for x in gate["targets"]), "B·감각 줄이 비었다", gate["targets"])
        rc, out = run(CMDS["gate"].replace("--max-credits 450", "--max-credits 300").replace("serp_targets.json", "g300.json"), t)
        g3 = json.loads((t / "g300.json").read_text(encoding="utf-8"))
        check(rc == 0 and len(g3["targets"]) == 10 and g3["credits"] == 300 and g3["cut_by_budget"]
              and all(x["lane"].startswith("A") for x in g3["targets"]),
              "예산 상한(300 → G ≤ 10)을 지키지 않거나 B 줄부터 자르지 않는다", g3)

        # ── 원장 (STEP 1·2·3·5)
        rc, out = run(CMDS["ledger"], t); check(rc == 0 and "C1" in out, "taste_ledger 실패", out)
        for step, tool, raw in (("확장", "intent_finder", "intent_r1_커피원두맛.json"), ("지표", "keyword_info", "metrics_ads.json"),
                                ("화면", "serp", "serp_커피원두.json"), ("화면", "serp", "serp_고소한원두.json")):
            rc, out = run(f"python3 scripts/taste_ledger.py --ledger calls_ledger.json --step {step} --tool {tool} --raw {raw}", t)
            check(rc == 0, f"taste_ledger 실패 ({raw})", out)
        (t / "nocost.json").write_text(json.dumps({"data": ["a"]}), encoding="utf-8")
        rc, out = run("python3 scripts/taste_ledger.py --ledger calls_ledger.json --step 확장 --tool intent_finder --raw nocost.json", t)
        check(rc != 0, "cost_detail 없는 응답을 원장이 받아들였다 — 비용을 추정해 채우면 안 된다", out)
        rc, out = run(CMDS["ledger"], t)
        check(rc != 0, "같은 원본 파일을 원장에 두 번 적었다 — 소진이 부풀려진다", out)

        # ── STEP 6
        rc, out = run(CMDS["words"], t); check(rc == 0, "taste_words 실패", out)
        doc = doc_columns(md)
        check(set(doc) == {"taste_words", "taste_tribes", "taste_cooc", "taste_evidence", "taste_collection"},
              "SKILL.md 산출물 블록의 파일 목록이 다르다", doc.keys())
        for name, cols in doc.items():
            p = t / f"{name}_커피원두.csv"
            if check(p.is_file(), f"{p.name} 가 만들어지지 않았다"):
                check(header(p) == cols, f"{p.name} 컬럼이 문서와 다르다", f"문서 {cols}\n실제 {header(p)}")
        S = json.loads((t / "taste_summary_커피원두.json").read_text(encoding="utf-8"))
        W = {r["word"]: r for r in read(t / "taste_words_커피원두.csv")}
        T = read(t / "taste_tribes_커피원두.csv")
        C = read(t / "taste_collection_커피원두.csv")
        E = read(t / "taste_evidence_커피원두.csv")

        # T7
        check(S["coverage_status"] == "보류" and "절단" in S["coverage"] and "보류" in out,
              "반환 = limit 호출이 있는데 «수렴»을 그대로 받아들였다 (T7)", S["coverage"])
        check([c["truncated"] for c in C] == ["Y", ""] and C[0]["limit"] == "20" and C[0]["returned"] == "20",
              "호출별 요청 limit · 반환 수 · 절단 기록이 틀렸다 (T7)", C)
        # T5
        g = W.get("고소", {})
        check({"고소한", "고소함", "고소하고"} <= set(g.get("variants", "").split("|")),
              "낱말 수준 변형(고소한·고소함·고소하고)이 대표 낱말 «고소» 하나로 모이지 않았다 (T5)", g)
        check(not any(w.startswith("고소") and w != "고소" for w in W), "변형이 별도 낱말로 갈라졌다 (T5)", list(W))
        check(g.get("query_hits") == "4", "고소 검색어 그룹 수가 4가 아니다 — 같은 검색 중복(고소한원두추천) 또는 «고소장» 오탐이 섞였다", g)
        b = W.get("부드러움", {})
        check(b.get("query_volume_lo") == "5000" and b.get("query_volume_hi") == "5700",
              "표기변형 그룹(부드러운 원두/부드러운원두)의 하한 · 상한이 5000 · 5700 이 아니다 (T5)", b)
        check(S["n_dup_rows"] == 1, "같은 검색 중복(dup_of) 행을 빼지 않았다 (T5)", S)
        check(S["n_serp_sentences_raw"] > S["n_serp_units"] and g.get("serp_keywords") == "2",
              "같은 PAA 문장이 SERP 두 곳에 나왔는데 두 단위로 셌다 (T5)", (S["n_serp_sentences_raw"], S["n_serp_units"]))
        # 회피 · 최장일치 · 카테고리어 가림 · 주입 문구
        sm = W.get("산미", {})
        check(sm.get("avoid_units") == "4" and sm.get("want_units") == "1", "«산미 없는/산미가 없는»을 회피로 세지 않았다", sm)
        check(W.get("쓴맛", {}).get("avoid_units") == "1", "«쓴맛이 덜한»을 회피로 세지 않았다", W.get("쓴맛"))
        check("원두" not in W and "커피" not in W, "카테고리어가 낱말로 세어졌다", list(W))
        inj = [e for e in E if e["url"] == "https://example.net/shop"]
        check(inj and inj[0]["word"] == "단맛" and not any("이전 지시" in e["text"] for e in E),
              "주입 문구가 든 화면 항목이 데이터로만 처리되지 않았다(낱말 · 항목 단위 출처 주소)", inj)
        check(W.get("묵직", {}).get("variants", "").count("바디감") >= 1, "카테고리 전용 감각어(바디감)가 묵직으로 모이지 않았다", W.get("묵직"))
        # 묶음
        top = T[0] if T else {}
        check(top.get("status") == "묶음" and set(top.get("words", "").split(" · ")) == {"고소", "묵직"},
              "공기 묶음이 예상(고소 · 묵직)과 다르다 — 결정적이어야 한다", T[:2])
        check(S["verdict"] == "취향 묶음 판정", "감각어가 충분한데 희소로 판정했다", S["verdict"])
        rc, out2 = run(CMDS["words"], t)
        check(read(t / "taste_tribes_커피원두.csv") == T, "같은 입력에 다른 묶음 — 결정적이지 않다")

        # ── STEP 8
        rc, out = run(CMDS["report"], t); check(rc == 0, "build_report 실패", out)
        rep = json.loads((t / "report.json").read_text(encoding="utf-8"))
        led = json.loads((t / "calls_ledger.json").read_text(encoding="utf-8"))
        check(rep["provenance"]["calls"] == led["calls"], "provenance.calls 가 원장과 다르다 — 손으로 적으면 안 된다")
        check(rep["facts"]["F8"]["value"] == sum(c["cost"] for c in led["calls"]) == 194,
              "소진(F8)이 원장 cost_detail 합(194)과 다르다", rep["facts"]["F8"])
        check(rep["facts"]["F7"]["value"] == 1, "절단 호출 수(F7)가 리포트에 없다 (T7)", rep["facts"].get("F7"))
        rc, out = run(CMDS["render"], t)
        check(rc == 0 and (t / "report.html").is_file() and (t / "report.calls.csv").is_file(), "렌더 실패 — 계약 위반", out)
        ex = json.loads((ROOT / "assets" / "report.example.json").read_text(encoding="utf-8"))
        strip = lambda r: json.dumps({**r, "provenance": {**r["provenance"], "calls": [{k: v for k, v in c.items() if k != "at"}
                                                                                    for c in r["provenance"]["calls"]]}},
                                     ensure_ascii=False, sort_keys=True)   # noqa: E731
        # 견본 = 같은 명령 + `--subtitle-note`(합성 픽스처 표시)
        rc, out = run(CMDS["report"].replace(" -o report.json", f' --subtitle-note "{NOTE}" -o report_ex.json'), t)
        check(rc == 0, "build_report --subtitle-note 실패", out)
        rep_ex = json.loads((t / "report_ex.json").read_text(encoding="utf-8"))
        check(rep_ex["meta"]["subtitle"] == f"{NOTE} · {rep['meta']['subtitle']}", "--subtitle-note 표시가 부제 앞에 붙지 않는다",
              rep_ex["meta"]["subtitle"])
        if os.environ.get("LM_WRITE_EXAMPLE"):        # 견본 재생성: LM_WRITE_EXAMPLE=1 python3 scripts/selftest_taste.py
            (ROOT / "assets" / "report.example.json").write_text(json.dumps(rep_ex, ensure_ascii=False, indent=1), encoding="utf-8")
            ex = rep_ex
        check(strip(rep_ex) == strip(ex), "assets/report.example.json 이 build_report.py 산출과 다르다 — 견본을 다시 만들 것")

        # ── 감각어가 거의 없는 서비스 카테고리
        rc, out = run("python3 scripts/build_universe.py --metrics metrics_ads_자동차보험.json --themes themes.자동차보험.json "
                      "--round R1=intent_r1_자동차보험.json -o universe_ins.csv", t)
        rc, out = run("python3 scripts/taste_words.py --universe universe_ins.csv --lexicon themes.자동차보험.json --keyword 자동차보험 "
                      "--gl kr --serp serp_자동차보험.json --intent-call R1=intent_r1_자동차보험.json@50", t)
        si = json.loads((t / "taste_summary_자동차보험.json").read_text(encoding="utf-8"))
        check(rc == 0 and si["verdict"].startswith("감각어 희소") and si["coverage_status"] == "미판정",
              "감각어가 거의 없는 카테고리를 «희소 — 보류»로 판정하지 않았다", out)
        run("python3 scripts/taste_ledger.py --ledger l2.json --step 화면 --tool serp --raw serp_자동차보험.json", t)
        rc, out = run("python3 scripts/build_report.py --summary taste_summary_자동차보험.json --ledger l2.json -o r2.json", t)
        r2 = json.loads((t / "r2.json").read_text(encoding="utf-8")) if rc == 0 else {}
        check(rc == 0 and "보류" in r2["sections"][0]["title"], "희소 판정인데 리포트 헤딩이 묶음을 결론으로 쓴다", out)
        rc, out = run("python3 scripts/render_report.py r2.json -o r2.html", t)
        check(rc == 0, "희소 판정 리포트가 렌더되지 않는다", out)

        # ── 영어 · 일본어 시작 사전
        for gl, lx, rows, expect in (
                ("us", "themes.us.json", [("non greasy sunscreen", 900), ("fragrance free sunscreen", 800), ("sunscreen not sticky", 700)],
                 {"번들거림": ("0", "1"), "무향": ("1", "0"), "끈적임": ("0", "1")}),
                ("jp", "themes.jp.json", [("べたつかない 日焼け止め", 900), ("日焼け止め しっとり", 800)],
                 {"끈적임": ("0", "1"), "촉촉": ("1", "0")})):
            with open(t / f"u_{gl}.csv", "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh); w.writerow(["keyword", "canonical", "dup_of", "volume_total"])
                for k, v in rows:
                    w.writerow([k, re.sub(r"\s+", "", k.lower()), "", v])
            kw = "sunscreen" if gl == "us" else "日焼け止め"
            rc, out = run(f"python3 scripts/taste_words.py --universe u_{gl}.csv --lexicon {(ROOT / 'assets' / lx).as_posix()} --keyword {kw} "
                          f"--gl {gl} --outdir out_{gl}", t)
            got = {r["word"]: (r["want_units"], r["avoid_units"]) for r in read(next((t / f"out_{gl}").glob("taste_words_*.csv")))} if rc == 0 else {}
            for word, wa in expect.items():
                check(got.get(word) == wa, f"{gl} 사전: «{word}» 선호/회피가 {wa} 가 아니다", got)

    if fails:
        print(f"selftest_taste 실패 {len(fails)}건")
        for n, d in fails:
            print(f"  ✗ {n}")
            if d:
                print("      " + d.strip().replace("\n", "\n      ")[:400])
        return 1
    print(f"selftest_taste 통과 — 예시 명령 {len(CMDS)}개 · 산출 CSV 5종 컬럼 · T5 · T7 · 회피 · 희소 판정 · 원장 · 리포트 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
