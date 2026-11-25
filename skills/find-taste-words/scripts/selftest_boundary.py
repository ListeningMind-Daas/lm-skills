#!/usr/bin/env python3
"""find-taste-words 회귀 selftest (v0.1.1) — 낱말 경계 매칭 · 고유명사 exclude · 경계 의심 비율.

v0.1.0은 canonical(공백 제거) 문자열에서 부분일치로 낱말을 찾아, 실데이터 재생에서
기본 사전 적중 상당수가 오탐 또는 극성 오류였다(«대신 나노»→신나 · «최신 기술»→신기 · «있고요»→고요 ·
«냉/온 기능»→온기 · «다글로 시작»→글로시 · «엔씨소프트»→소프트 · «카카오톡»→카카오 · «매트리스»→매트).
이 검사는 그 문장들을 합성 픽스처(`assets/fixtures/serp_경계오탐.json`, 검색량은 지어냄)로 되살려 다시 나오지 않는지 본다.

검사하는 것 (API · 크레딧 없음):
  · scan_detail — 오탐 문장마다 해당 낱말이 걸리지 않는다 · 정탐 문장(«물맛이 깔끔한» · «부드러운 물» · «시원한 물» ·
    «소음 없는») · 일부러 쓴 여러 어절 패턴(«걱정 없는» · «잘 샀어요») · 합성형 패턴(«다크초콜릿»)은 그대로 걸린다
  · 회피 — «소음/진동이 없어» · «소음과 진동이 없는»은 «소음» 회피(v0.1.0은 선호로 셌다)
  · 테마 exclude — 겹치는 출현만 버린다(«매트리스 옆 … 보송한»의 «보송»은 남는다)
  · taste_words.py 끝까지 — 오탐 낱말 0 · 정탐 낱말 극성 · summary 경계 점검 필드 · `boundary_suspect` 컬럼
  · 경계 의심 비율 — exclude를 뺀 사전으로 돌리면 «카카오톡» · «매트리스»가 경계 의심으로 잡히고 경고가 뜬다

    python3 scripts/selftest_boundary.py      # 스킬 폴더에서 (scripts/selftest.py 가 함께 돌린다)
"""
import csv, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from taste_words import load_lexicon, scan_detail  # noqa: E402

ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
HEADS = ["정수기"]

# (원문, 걸리면 안 되는 낱말) — 실데이터 오탐 유형 그대로
FALSE_POS = [
    ("역삼투압 방식 대신 나노나 중공사막 필터를 주로 씁니다.", "즐거움"),      # 신나
    ("최신 기술 및 가격까지 비교했습니다.", "호기심"),                        # 신기
    ("물을 넣을 곳에 맞춰 앞뒤로 움직일 수 있고요.", "차분"),                 # 고요
    ("직수정수기는 말통이 없기때문에 편리하고요.", "차분"),                   # 고요
    ("얼음이나 냉/온 기능이 있기 때문에 따로 물을 끓일 필요가 없습니다.", "포근"),  # 온기
    ("10초만에 다글로 시작하기", "광택"),                                    # 글로시
    ("엔씨소프트(NCSOFT) 음성 기술로 녹음을 글로 변환해서 모읍니다.", "부드러움"),  # 소프트
    ("엔씨소프트(NCSOFT) 음성 기술로 녹음을 글로 변환해서 모읍니다.", "화사"),     # 환해
    ("공유하기 블로그 페이스북 카카오톡.", "초콜릿"),                          # 카카오
    ("정수기, 공기청정기, 비데, 매트리스, 안마의자 렌탈.", "보송"),            # 매트
    ("관리라고 해봐야 4개월에 한 번 필터 교환해 주고 가는 것임.", "화사"),      # 환해
    ("웰스 슈퍼쿨링2 정수기 (냉온정).", "시원"),                              # 쿨링(모델명 속)
]
# (원문, 낱말, 회피 여부)
TRUE_POS = [
    ("물맛이 깔끔한 정수기", "깔끔", False),
    ("부드러운 물", "부드러움", False),
    ("시원한 물 정수기", "시원", False),
    ("소음 없는 정수기", "조용", False),          # 사전 설계: «소음없»은 «조용»(= 소음 회피 표현)
    ("콘센트가 멀어도 쓸 수 있고 소음/진동이 없어 좋습니다.", "소음", True),
    ("소음과 진동이 없는 제품을 찾았습니다.", "소음", True),
    ("오염 걱정 없는 직수 방식.", "안심", False),   # 여러 어절 패턴 «걱정없»
    ("가격 대비 잘 샀어요", "만족", False),         # 여러 어절 패턴 «잘샀»
    ("다크초콜릿 향 원두", "초콜릿", False),        # 합성형을 패턴에 직접 적은 경우
    ("산미 없는 원두", "산미", True),
    ("매트리스 옆에 둬도 보송한 촉감의 커버가 좋습니다.", "보송", False),   # exclude는 겹치는 출현만 버린다
]
UNIVERSE = [  # (keyword, volume_total) — 지어낸 값
    ("정수기", 120000), ("물맛이 깔끔한 정수기", 3600), ("부드러운 물 정수기", 2400), ("시원한 물 정수기", 1800),
    ("소음 없는 정수기", 1200), ("직수 정수기", 30000),
]


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def read(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def main():
    utf8_console()
    fails = []

    def check(cond, name, detail=""):
        if not cond:
            fails.append((name, str(detail)[-400:]))
        return cond

    lex = load_lexicon(ROOT / "assets" / "themes.json")
    for text, word in FALSE_POS:
        hits, _ = scan_detail(text, lex, HEADS)
        got = [h["w"]["word"] for h in hits]
        check(word not in got, f"오탐 재발: «{text}» 에서 «{word}» 가 걸렸다", got)
    for text, word, neg in TRUE_POS:
        hits, _ = scan_detail(text, lex, HEADS)
        got = {(h["w"]["word"], h["negated"]) for h in hits}
        check((word, neg) in got, f"정탐 누락: «{text}» → «{word}» {'회피' if neg else '선호'} 가 아니다", got)
    hits, _ = scan_detail("소음 없는 정수기", lex, HEADS)
    check(("소음", False) not in {(h["w"]["word"], h["negated"]) for h in hits}, "«소음 없는»을 «소음» 선호로 셌다", hits)
    hits, _ = scan_detail("정수기, 공기청정기, 비데, 매트리스, 안마의자 렌탈.", load_lexicon_noex(lex), HEADS)
    check(any(h["w"]["word"] == "보송" and h["suspect"] and h["token"] == "매트리스" for h in hits),
          "exclude 없는 사전에서 «매트리스»→매트 가 경계 의심으로 표시되지 않았다", hits)

    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        with open(t / "u.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh); w.writerow(["keyword", "canonical", "dup_of", "volume_total"])
            for k, v in UNIVERSE:
                w.writerow([k, k.replace(" ", ""), "", v])
        lx = json.loads((ROOT / "assets" / "themes.json").read_text(encoding="utf-8"))
        lx["category_terms"] = ["정수기"]
        (t / "lex.json").write_text(json.dumps(lx, ensure_ascii=False), encoding="utf-8")
        for th in lx["themes"]:
            th.pop("exclude", None)
        (t / "lex_noex.json").write_text(json.dumps(lx, ensure_ascii=False), encoding="utf-8")
        serp = (ROOT / "assets" / "fixtures" / "serp_경계오탐.json").as_posix()

        def run(lexfile, outdir):
            p = subprocess.run([sys.executable, str(HERE / "taste_words.py"), "--universe", "u.csv", "--lexicon", lexfile,
                                "--keyword", "정수기", "--gl", "kr", "--serp", serp, "--outdir", outdir],
                               cwd=t, capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
            return p.returncode, p.stdout + p.stderr

        rc, out = run("lex.json", "o1")
        if check(rc == 0, "taste_words 실패(경계 픽스처)", out):
            W = {r["word"]: r for r in read(t / "o1" / "taste_words_정수기.csv")}
            S = json.loads((t / "o1" / "taste_summary_정수기.json").read_text(encoding="utf-8"))
            bad = sorted({w for _, w in FALSE_POS} & set(W) - {"보송", "부드러움", "시원"})
            check(not bad, "끝까지 돌린 결과에 오탐 낱말이 남았다", bad)
            check(W.get("보송", {}).get("units") == "1", "«보송»은 «보송한» 1단위만이어야 한다(매트리스 제외)", W.get("보송"))
            check("부드러움" in W and W["부드러움"]["units"] == "1", "«부드러움»은 «부드러운 물» 1단위만이어야 한다(엔씨소프트 제외)",
                  W.get("부드러움"))
            for word in ("깔끔", "시원", "조용"):
                check(W.get(word, {}).get("query_hits") == "1", f"검색어 «{word}» 정탐이 빠졌다", W.get(word))
            check(W.get("시원", {}).get("units") == "1", "«시원»에 모델명 «슈퍼쿨링» 문장이 섞였다", W.get("시원"))
            check(W.get("소음", {}).get("avoid_units") == "2" and W.get("소음", {}).get("want_units") == "0",
                  "«소음/진동이 없어» · «소음과 진동이 없는»이 회피 2 · 선호 0 이 아니다", W.get("소음"))
            check(all(k in S for k in ("n_word_hits", "n_boundary_suspect", "boundary_suspect_share",
                                       "boundary_suspect_tokens", "n_boundary_rejected", "n_theme_excluded")),
                  "summary 에 경계 점검 필드가 없다", list(S))
            check(S.get("n_boundary_rejected", 0) >= 9 and S.get("n_theme_excluded", 0) >= 3,
                  "경계 규칙 · exclude 로 버린 수가 summary 에 기록되지 않았다", (S.get("n_boundary_rejected"), S.get("n_theme_excluded")))
            check(S.get("n_boundary_suspect") == 0 and "경계 점검" in out, "기본 사전 결과에 경계 의심이 남았거나 콘솔 점검 줄이 없다", out)
        rc, out = run("lex_noex.json", "o2")
        if check(rc == 0, "taste_words 실패(exclude 뺀 사전)", out):
            W = {r["word"]: r for r in read(t / "o2" / "taste_words_정수기.csv")}
            S = json.loads((t / "o2" / "taste_summary_정수기.json").read_text(encoding="utf-8"))
            check(W.get("초콜릿", {}).get("boundary_suspect") == "1" and S["boundary_suspect_share"] > 0 and "⚠ 경계 의심" in out,
                  "exclude 없이 «카카오톡»이 걸렸는데 경계 의심 · 경고로 드러나지 않았다", (W.get("초콜릿"), S.get("boundary_suspect_tokens"), out))

    if fails:
        print(f"selftest_boundary 실패 {len(fails)}건")
        for n, d in fails:
            print(f"  ✗ {n}")
            if d:
                print("      " + d.strip().replace("\n", "\n      ")[:400])
        return 1
    print(f"selftest_boundary 통과 — 오탐 문장 {len(FALSE_POS)}개 차단 · 정탐 {len(TRUE_POS)}개 유지 · exclude 겹침 · 경계 의심 경고 (API 호출 없음)")
    return 0


def load_lexicon_noex(lex):
    """테마 exclude를 끈 사본 — 경계 의심 지표가 exclude 없이도 고유명사 속 일치를 드러내는지 본다."""
    return {**lex, "words": [dict(w, unit_ex=None) for w in lex["words"]]}


if __name__ == "__main__":
    sys.exit(main())
