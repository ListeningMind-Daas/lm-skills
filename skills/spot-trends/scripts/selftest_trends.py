#!/usr/bin/env python3
"""이 스킬 고유 회귀 검사 — trend_signals.py · write_alert.py (합성 픽스처 · API 호출 없음).

2026-09-28 실데이터 평가 결함을 다시 막는다.
  D1 한국어 입력에서 확산 폭이 전 행 공백이 되지 않는다
  D2 성장률 ≤ 0인 키워드는 등급을 받지 않는다(절대 하한)
  D3 직전 창 기저가 게이트 미만이면 가속도를 비운다
  D4 판정 풀이 작으면 경고하고 --window 12를 권한다
  두 모드 비교 — 마지막 달에 따라 부호가 뒤집히는 행은 «잠정»
  P0 판정 풀이 작으면(기본 10행 미만) 백분위 대신 절대 기준 · 알림은 판정 행 전체와 작은 풀 경고를 싣는다
  같은 검색의 중복(시계열 동일)은 한 번만 · gated_out 사유 분리 · SKILL.md 컬럼 목록 = 실제 출력
  V16 (2026-09-30 선크림 실측) 알림이 마지막 달 완결성 의심 건수(check_refmonth)를 싣고, 없으면 멈춘다 ·
      «잠정» 근거(다른 모드 등급·성장률 growth_other_mode)를 CSV·알림에 싣는다 ·
      같은 표기 그룹 안의 성장률 부호 갈림(group_growth_split)을 CSV·알림에 경고한다 · 작은 풀이면 확산 폭 무의미 경고

    python3 scripts/selftest_trends.py        # 스킬 폴더에서 실행
"""
import csv, json, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL_MD = HERE.parent / "SKILL.md"
MONTHS = [f"{y}-{m:02d}" for y in range(2022, 2027) for m in range(1, 13)]
MONTHS = MONTHS[MONTHS.index("2022-09"): MONTHS.index("2026-08") + 1]   # 48개월 · 기준월 2026-08


def series(fn):
    return [{"month": m, "gg": fn(m) // 3, "nv": fn(m) - fn(m) // 3, "total": fn(m)} for m in MONTHS]


def grow(base, rate, last=None):
    """2025-09 이후 rate배. last를 주면 마지막 달(2026-08)만 그 값."""
    def f(m):
        if last is not None and m == "2026-08":
            return last
        return int(base * (rate if m >= "2025-09" else 1.0))
    return f


def row(kw, fn):
    return {"keyword": kw, "ads_metrics": {"volume_total": 1, "cpc": 0.1, "competition_index": 1},
            "monthly_volume": series(fn)}


def fixture():
    rows = []
    # 브랜드 × 제형 — 머리 토큰(선스틱)과 브랜드 토큰을 함께 공유한다
    for b, r in (("토코보", 1.35), ("다이소", 1.25), ("아토팜", 1.15), ("라운드랩", 0.9), ("닥터지", 0.8)):
        rows.append(row(f"{b} 선스틱", grow(4000, r)))
        rows.append(row(f"{b} 선크림", grow(3500, r * 0.95)))
    rows.append(row("선스틱", grow(20000, 1.1)))
    rows.append(row("썬스틱", grow(9000, 1.05)))                       # 표기변형(--rules 썬→선)
    rows.append(row("선스틱 추천", grow(6000, 0.7)))                    # 하락 — 등급 금지 (D2)
    rows.append(row("추천 선스틱", grow(6000, 0.7)))                    # 시계열 동일 → 같은 검색의 중복
    # D3 — 직전 창(2026-02~04)의 전년 기저가 작다: 2025-02~04만 50
    rows.append(row("톤업 선스틱", lambda m: 50 if "2025-02" <= m <= "2025-04" else 5000))
    # 두 모드 — 최근 3개월 가속 후 마지막 달 0: 제외 모드 +50% · 포함 모드 0%로 뒤집힌다
    rows.append(row("노세범 선스틱", lambda m: 0 if m == "2026-08" else 6000 if m >= "2026-05" else 4000))
    return rows


def run(args, cwd):
    p = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def doc_columns():
    lines = SKILL_MD.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "trend_signals_{category}.csv":
            cols = []
            for nxt in lines[i + 1:]:
                if not re.match(r"^\s+[a-z_]", nxt):
                    break
                cols += [c.strip() for c in nxt.split(",") if c.strip()]
            return cols
    return None


def main():
    fails = []
    S = str(HERE / "trend_signals.py")
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "all.json").write_text(json.dumps({"data": fixture()}, ensure_ascii=False), encoding="utf-8")
        (tmp / "req.json").write_text(json.dumps(["토코보 선스틱", "없는 키워드"], ensure_ascii=False), encoding="utf-8")
        rc, out = run([S, "all.json", "-o", "sig.csv", "--gate", "3000", "--rules", '{"썬":"선"}',
                       "--requested", "req.json"], tmp)
        if rc:
            fails.append(("run", "비정상 종료", out[-300:]))
            R = []
        else:
            with open(tmp / "sig.csv", encoding="utf-8-sig") as f:
                rd = csv.DictReader(f); hdr, R = rd.fieldnames, list(rd)
            doc = doc_columns()
            if doc is None or set(doc) != set(hdr):
                fails.append(("columns", "SKILL.md 컬럼 목록 ≠ 실제 출력",
                              f"문서만 {sorted(set(doc or []) - set(hdr))} · 코드만 {sorted(set(hdr) - set(doc or []))}"))
        by = {r["keyword"]: r for r in R}
        if R:
            live = [r for r in R if not r["gated_out"]]
            filled = [r for r in live if r["diffusion"] != ""]
            if len(filled) < len(live) * 0.8:
                fails.append(("D1", f"확산 폭이 대부분 공백 ({len(filled)}/{len(live)})", ""))
            if len({r["diffusion"] for r in filled}) < 2:
                fails.append(("D1", "확산 폭이 한 값으로 뭉개졌다(머리 토큰만으로 이음)", ""))
            bad = [r["keyword"] for r in R if r["grade"] and float(r["growth"]) <= 0]
            if bad:
                fails.append(("D2", f"성장률 ≤ 0인데 등급: {bad}", ""))
            if by.get("선스틱 추천", {}).get("grade_floor") != "하한 미달":
                fails.append(("D2", "하락 키워드에 grade_floor 표시가 없다", ""))
            t = by.get("톤업 선스틱", {})
            if t.get("accel") != "" or t.get("accel_note") != "직전 창 게이트 미달":
                fails.append(("D3", "직전 창 기저 미달인데 가속도가 계산됐다", f"{t.get('accel')} / {t.get('accel_note')}"))
            if by.get("추천 선스틱", {}).get("gated_out") != "같은 검색의 중복" and \
                    by.get("선스틱 추천", {}).get("gated_out") != "같은 검색의 중복":
                fails.append(("T5", "시계열이 같은 어순 변형을 중복으로 묶지 못했다", ""))
            if by.get("썬스틱", {}).get("canonical") != by.get("선스틱", {}).get("canonical"):
                fails.append(("T5", "--rules 치환이 canonical에 반영되지 않았다", ""))
            n = by.get("노세범 선스틱", {})
            if n.get("provisional") != "잠정":
                fails.append(("두 모드", "마지막 달로 부호가 뒤집히는 행에 «잠정»이 없다",
                              f"{n.get('growth')} · {n.get('grade')} / {n.get('grade_other_mode')}"))
            if "두 모드 비교" not in out or "미반환" not in out and "응답에 없는" not in out:
                fails.append(("출력", "두 모드 비교 또는 미반환 목록이 출력되지 않았다", out[-300:]))
            if "[파생]" in out or "%p" not in out:
                fails.append(("출력", "옛 라벨 [파생] 잔존 또는 가속도 %p 표기 누락", ""))

        # D4 — 게이트를 높여 풀을 작게 만든다
        rc, out = run([S, "all.json", "-o", "small.csv", "--gate", "15000"], tmp)
        if rc or "판정 풀이 작다" not in out or "--window 12" not in out:
            fails.append(("D4", "작은 풀에서 경고·--window 12 권고가 없다", out[-300:]))
        with open(tmp / "small.csv", encoding="utf-8-sig") as f:
            reasons = {r["gated_out"] for r in csv.DictReader(f)}
        if "게이트 미달" not in reasons or "제외" in reasons:
            fails.append(("P2", "gated_out 사유가 분리되지 않았다", str(reasons)))

        # P0 (2026-09-29) — 판정 1~3행이면 백분위 최고 점수가 0.15라 +51% 상승도 등급을 못 받았다 → 절대 기준
        if R and not all(r["grade_basis"] == "백분위" for r in R if not r["gated_out"]):
            fails.append(("P0", "큰 풀인데 백분위가 아닌 기준으로 등급을 매겼다", ""))
        small = [row("가벼운 청소기", grow(5000, 1.4)), row("무중력 의자", grow(30000, 0.95))]
        (tmp / "p0.json").write_text(json.dumps({"data": small}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([S, "p0.json", "-o", "p0.csv"], tmp)
        with open(tmp / "p0.csv", encoding="utf-8-sig") as f:
            p0 = {r["keyword"]: r for r in csv.DictReader(f)}
        g = p0.get("가벼운 청소기", {})
        if rc or g.get("grade") != "A" or not g.get("grade_basis", "").startswith("절대") or "절대 기준" not in out:
            fails.append(("P0", "작은 풀에서 +40%·지속 11개월 키워드가 절대 기준 A를 받지 못했다",
                          f"{g.get('grade')} · {g.get('grade_basis')} · {g.get('signal_score')}"))
        rc, out = run([str(HERE / "write_alert.py"), "p0.csv", "--category", "합성 작은 풀", "--queried-at", "2026-09-29",
                       "--no-refmonth-check", "-o", "p0.md"], tmp)
        md = (tmp / "p0.md").read_text(encoding="utf-8") if not rc else ""
        if "판정 행 전체 (2행" not in md or "| 무중력 의자 |" not in md or "하한 미달" not in md \
                or "판정 풀이 작다" not in md or "절대 기준" not in md:
            fails.append(("P0", "알림이 판정 행 전체(등급 없는 행 포함)·작은 풀 경고·절대 기준을 싣지 않았다", md[:300]))

        # write_alert — CSV 값만으로 알림 작성 (STEP 4 완결성 점검 출력을 함께 준다)
        rc, rm = run([str(HERE / "check_refmonth.py"), "all.json", "--queried-at", "2026-09-28"], tmp)
        (tmp / "refmonth.txt").write_text(rm, encoding="utf-8")
        rc, out = run([str(HERE / "write_alert.py"), "sig.csv", "--category", "합성", "--queried-at", "2026-09-28",
                       "--refmonth-check", "refmonth.txt", "-o", "alert.md"], tmp)
        if rc:
            fails.append(("write_alert", "비정상 종료", out[-300:]))
        else:
            md = (tmp / "alert.md").read_text(encoding="utf-8")
            if "2026-08" not in md or "지연 **1개월**" not in md or "잠정" not in md:
                fails.append(("write_alert", "기준월·지연·잠정이 알림에 없다", md[:300]))
            if not re.search(r"완결성: \d+행 검사 중 의심 \d+행", md):
                fails.append(("V16", "알림에 마지막 달 완결성 의심 건수가 없다", md[:400]))
            if not re.search(r"잠정 \(마지막 달 포함 모드: 등급 \S+ · 성장률 [+-]", md):
                fails.append(("V16", "«잠정» 근거(다른 모드 등급·성장률)가 알림 비고에 없다", ""))
        rc, out = run([str(HERE / "write_alert.py"), "sig.csv", "--category", "합성", "--queried-at", "2026-09-28",
                       "-o", "noref.md"], tmp)
        if rc == 0 or "--refmonth-check" not in out:
            fails.append(("V16", "완결성 점검 출력 없이 알림을 썼다", out[-200:]))
        # V16 — 같은 표기 그룹 안의 성장률 부호 갈림 (썬크림 ↑ / 선크림 ↓)
        sp = [row("썬크림", grow(20000, 1.2)), row("선크림", grow(20000, 0.85)), row("톤업 선크림", grow(9000, 1.1))]
        (tmp / "sp.json").write_text(json.dumps({"data": sp}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([S, "sp.json", "-o", "sp.csv", "--rules", '{"썬":"선"}'], tmp)
        with open(tmp / "sp.csv", encoding="utf-8-sig") as f:
            spr = {r["keyword"]: r for r in csv.DictReader(f)}
        if rc or "썬크림 +" not in spr.get("선크림", {}).get("group_growth_split", "") \
                or spr.get("톤업 선크림", {}).get("group_growth_split") or "부호가 갈린다" not in out \
                or spr.get("썬크림", {}).get("growth_other_mode") in ("", None):
            fails.append(("V16", "그룹 내 성장률 부호 갈림·다른 모드 성장률이 CSV에 없다", str(spr.get("선크림"))[:200]))
        rc, out = run([str(HERE / "write_alert.py"), "sp.csv", "--category", "합성 갈림", "--queried-at", "2026-09-28",
                       "--no-refmonth-check", "-o", "sp.md"], tmp)
        smd = (tmp / "sp.md").read_text(encoding="utf-8") if not rc else ""
        if "표기 그룹 안에서 성장률 부호가 갈린다" not in smd or "확산 폭은 이번에 의미가 없다" not in smd \
                or "미점검 [데이터 공백]" not in smd:
            fails.append(("V16", "알림에 부호 갈림·작은 풀 확산 무의미·미점검 표시가 없다", smd[:300]))
        rc, out = run([str(HERE / "write_alert.py"), "sig.csv", "--category", "합성", "--queried-at", "2026-09-28",
                       "--include-last-month", "--no-refmonth-check", "-o", "x.md"], tmp)
        if rc == 0:
            fails.append(("write_alert", "모드 표시가 CSV와 어긋나는데 멈추지 않았다", ""))

    if fails:
        print(f"실패 {len(fails)}건")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {str(detail)[:200]}")
        return 1
    print("통과 — trend_signals.py · write_alert.py 회귀 검사 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
