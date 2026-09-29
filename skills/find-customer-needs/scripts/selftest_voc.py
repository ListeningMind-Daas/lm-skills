#!/usr/bin/env python3
"""이 스킬의 자가검증 — 공유 selftest + 고유 스크립트(voc_axes.py) 계약 검증.

`selftest.py`는 공유 자산 사본이라 이 스킬에서 고칠 수 없다(고치면 드리프트). 그래서
고유 스크립트 검증은 이 파일에 두고, 먼저 공유 selftest를 돌린 뒤 이어서 SKILL.md의
STEP 6~7 예시 명령을 **문서에 적힌 그대로** 합성 픽스처로 실행한다. API·크레딧 불필요.

    python3 scripts/selftest_voc.py        # 스킬 폴더에서 실행

검사하는 것:
  · 문서 예시 명령(build_universe → voc_axes)이 비정상 종료 없이 도는가
  · 두 CSV의 컬럼이 SKILL.md «산출물» 목록과 **정확히** 같은가
  · 표기변형 그룹 볼륨을 하한(최댓값)·상한(합)으로 병기하는가, 상한에서 순위가 뒤집히면 표시하는가 (T5)
  · 브랜드/논브랜드가 따로 집계되는가 (T8)
  · 결측 월을 0으로 채우지 않고 «결측 월»로 남기는가 (T6)
  · 기저 게이트 미달을 추세 계산에서 빼는가 (T4)
  · 마지막 달 처리 방식·기준월·조회일·지연이 출력·md에 표기되는가 (T1·T2)
  · Pain이 Feature보다 먼저 선언돼 '충전 안됨'이 Pain으로 가는가 (배열 순서 규칙)
"""
import csv, json, pathlib, re, shlex, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
MONTHS = [f"{y}-{m:02d}" for y in (2023, 2024, 2025, 2026) for m in range(1, 13)]
LAST = "2026-07"


def series(base, growth=0.0, missing=(), dip_last=False):
    out = []
    for i, m in enumerate(MONTHS[: MONTHS.index(LAST) + 1]):
        if m in missing:
            out.append({"month": m, "gg": 10, "nv_code": 1})
            continue
        v = int(base * (1 + growth) ** (i / 12))
        if dip_last and m == LAST:
            v = int(v * 0.2)
        out.append({"month": m, "gg": v // 3, "nv": v - v // 3, "total": v})
    return out


def row(kw, vol, base=None, **kw_series):
    return {"keyword": kw, "ads_metrics": {"volume_total": vol, "volume_avg": vol // 12,
            "volume_trend": -0.9, "cpc": 0.3, "competition": "LOW", "competition_index": 10,
            "gg_volume_total": vol // 3, "nv_volume_total": vol - vol // 3},
            "monthly_volume": series(base or max(vol // 12, 1), **kw_series)}


SEEDS = ["전동칫솔", "음파칫솔", "전동칫솔 단점", "전동칫솔 고장", "전동칫솔 없는시드"]


def fixture(tmp):
    data = [
        row("전동칫솔", 200000, growth=-0.3, dip_last=True),  # 카테고리 기준선 −30% · 마지막 달 미완결 의심
        row("전동칫솔 충전 안됨", 24000, growth=0.5),          # Pain (Feature '충전'에도 걸림)
        row("전동칫솔 충전안됨", 23000, growth=0.5),           # 표기변형 — 같은 canonical
        row("전동칫솔 잇몸 피", 12000, growth=-0.3),           # Pain · 기준선과 같이 움직임 (그룹 게이트 미달 · 항목 합산 통과)
        row("전동칫솔 소음", 30000, growth=0.1),               # Pain — 하한 30,000 > 충전 24,000, 상한 30,000 < 충전 47,000 → 순위 뒤집힘
        row("전동칫솔 단점", 3000, growth=0.4),                # Pain 긴 꼬리 — M_pain
        row("음파 전동칫솔", 60000, growth=0.3),               # Feature
        row("음파전동칫솔", 60000, growth=0.3),                # 같은 검색의 중복(지표 일치) — 상한을 부풀리면 거짓 뒤집힘
        row("전동칫솔 칫솔모", 80000, growth=0.0),             # Feature
        row("블루투스 앱 연동", 5000),                        # Feature · 카테고리어 없음 → 경계 점검
        row("필립스 음파칫솔", 90000, growth=0.0),             # Feature · 브랜드
        row("휴대용 전동칫솔", 36000, missing=("2025-05",)),   # Attribute · 결측 월
        row("전동칫솔 미니 여행", 13000, base=100),           # Attribute · 게이트 미달
        row("전동칫솔 추천", 150000),                         # 미분류
        row("구강세정기 소음", 50000, growth=0.8),             # 인접 카테고리 — exclude가 없으면 Pain 1위
    ]
    (tmp / "_universe_all.json").write_text(json.dumps({"data": data}, ensure_ascii=False), encoding="utf-8")
    ads = [{k: v for k, v in d.items() if k != "monthly_volume"} for d in data]
    (tmp / "metrics_ads.json").write_text(json.dumps({"data": ads}, ensure_ascii=False), encoding="utf-8")
    (tmp / "seeds.json").write_text(json.dumps(SEEDS, ensure_ascii=False), encoding="utf-8")
    # STEP 1 응답: 요청 표기와 다른 응답(음파 칫솔) · 볼륨 0(고장) · 미반환(없는시드) · 요청 안 한 변형
    sc = [{"keyword": "전동칫솔", "ads_metrics": {"volume_total": 200000}},
          {"keyword": "음파 칫솔", "ads_metrics": {"volume_total": 40000}},
          {"keyword": "전동칫솔 단점", "ads_metrics": {"volume_total": 3000}},
          {"keyword": "전동칫솔 고장", "ads_metrics": {"volume_total": 0}},
          {"keyword": "전동 칫솔 추천", "ads_metrics": {"volume_total": 150000}}]
    (tmp / "seed_check.json").write_text(json.dumps({"data": sc}, ensure_ascii=False), encoding="utf-8")
    kws = [d["keyword"] for d in data]
    (tmp / "intent_r1_전동칫솔.json").write_text(json.dumps({"data": kws[:9]}, ensure_ascii=False), encoding="utf-8")
    (tmp / "intent_r1_전동칫솔단점.json").write_text(json.dumps({"data": kws[9:]}, ensure_ascii=False), encoding="utf-8")
    ex = json.load(open(SKILL / "assets" / "axes.electric-toothbrush-kr.example.json", encoding="utf-8"))
    (tmp / "axes.json").write_text(json.dumps(ex, ensure_ascii=False), encoding="utf-8")


def stage2_response(tmp):
    """STEP 5 keyword_info(all)을 흉내 낸다 — pool_M.json에 든 검색어의 시계열만 metrics_all.json에 싣는다."""
    pool = set(json.load(open(tmp / "pool_M.json", encoding="utf-8")))
    data = json.load(open(tmp / "_universe_all.json", encoding="utf-8"))["data"]
    (tmp / "metrics_all.json").write_text(json.dumps({"data": [d for d in data if d["keyword"] in pool]},
                                                     ensure_ascii=False), encoding="utf-8")


DOC_SCRIPTS = ("seed_check.py", "coverage_curve.py", "build_universe.py", "select_stage2.py",
               "check_refmonth.py", "voc_axes.py", "trend_yoy.py")


def doc_commands():
    """SKILL.md의 bash 블록에서 스크립트별 첫 예시 명령을 뽑는다."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    cmds = {}
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        joined = block.replace("\\\n", " ")
        for ln in joined.splitlines():
            ln = ln.strip()
            for name in DOC_SCRIPTS:
                if ln.startswith(f"python3 scripts/{name}") and name not in cmds:
                    cmds[name] = shlex.split(ln)[1:]
    return cmds, text


def documented_columns(text, fname):
    m = re.search(rf"{re.escape(fname)}\n((?:  [a-z0-9_, ]+\n)+)", text)
    if not m:
        return None
    return [c.strip() for c in m.group(1).replace("\n", ",").split(",") if c.strip()]


def header(p):
    return next(csv.reader(open(p, encoding="utf-8-sig")))


def main():
    fails = []
    p = subprocess.run([sys.executable, str(HERE / "selftest.py")], capture_output=True, text=True)
    print(p.stdout.strip())
    if p.returncode:
        fails.append(("selftest.py(공유)", "공유 스크립트 검증 실패", p.stdout[-300:]))

    cmds, text = doc_commands()
    for name in DOC_SCRIPTS:
        if name not in cmds:
            fails.append((name, "SKILL.md에서 예시 명령을 찾지 못함", ""))
    if fails:
        return report(fails)

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)
        outs = {}

        def run(name, args):
            args = [str(HERE / name) if a == f"scripts/{name}" else a for a in args]
            r = subprocess.run([sys.executable] + args, cwd=tmp, capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr

        for name in DOC_SCRIPTS:                      # 문서 순서 그대로 (STEP 1 → 7)
            code, outs[name] = run(name, cmds[name])
            if code:
                fails.append((name, "문서 예시 명령이 비정상 종료", outs[name][-300:]))
                return report(fails)
            if name == "select_stage2.py":
                stage2_response(tmp)                  # STEP 5 keyword_info(all) — pool_M만 조회한 응답

        fails += check_seed(tmp, outs["seed_check.py"])
        fails += check_coverage(tmp, cmds["coverage_curve.py"], outs["coverage_curve.py"], run)
        fails += check_stage2(tmp, outs["select_stage2.py"])
        fails += check_doc_text(text)

        cat = cmds["voc_axes.py"][cmds["voc_axes.py"].index("--category") + 1]
        f_kw = tmp / f"voc_decomposition_{cat}.csv"
        f_it = tmp / f"roadmap_priority_{cat}.csv"
        f_md = tmp / f"roadmap_priority_{cat}.md"
        for f in (f_kw, f_it, f_md):
            if not f.is_file():
                fails.append(("voc_axes", f"산출 파일 없음: {f.name}", ""))
        if fails:
            return report(fails)

        for f in (f_kw, f_it):
            want = documented_columns(text, f"{f.name.split('_' + cat)[0]}_{{category}}.csv")
            got = header(f)
            if want is None:
                fails.append(("SKILL.md", f"{f.name} 컬럼 목록을 문서에서 찾지 못함", ""))
            elif want != got:
                fails.append(("voc_axes", f"{f.name} 컬럼이 문서와 다름",
                              f"문서만: {sorted(set(want) - set(got))} · 코드만: {sorted(set(got) - set(want))}"))

        kw = {r["keyword"]: r for r in csv.DictReader(open(f_kw, encoding="utf-8-sig"))}
        it = list(csv.DictReader(open(f_it, encoding="utf-8-sig")))
        md = f_md.read_text(encoding="utf-8")
        out = outs["voc_axes.py"]

        if kw["전동칫솔 충전 안됨"]["axis"] != "Pain":
            fails.append(("분류", "'충전 안됨'이 Pain이 아님 — 배열 순서 규칙 위반", kw["전동칫솔 충전 안됨"]["axis"]))
        pain = [r for r in it if r["axis"] == "Pain" and r["need_item"].startswith("충전")]
        if not pain or (pain[0]["volume_12m_lo"], pain[0]["volume_12m_hi"]) != ("24000", "47000"):
            fails.append(("T5", "표기변형 그룹 하한(최댓값 24,000)·상한(합 47,000)이 아님",
                          f'{pain[0]["volume_12m_lo"]} / {pain[0]["volume_12m_hi"]}' if pain else "항목 없음"))
        rep_row = kw["전동칫솔 충전 안됨"]
        if (rep_row["group_volume_lo"], rep_row["group_volume_hi"]) != ("24000", "47000"):
            fails.append(("T5", "키워드 CSV의 그룹 하한·상한이 틀림",
                          f'{rep_row["group_volume_lo"]} / {rep_row["group_volume_hi"]}'))
        noise = [r for r in it if r["axis"] == "Pain" and r["need_item"].startswith("소음")]
        if not (pain and noise and pain[0]["axis_rank"] == "2" and pain[0]["axis_rank_hi"] == "1"
                and noise[0]["axis_rank"] == "1" and noise[0]["axis_rank_hi"] == "2"):
            fails.append(("T5", "순위가 하한 기준이 아니거나 상한 순위가 틀림",
                          [(r["need_item"], r["axis_rank"], r["axis_rank_hi"]) for r in pain + noise]))
        elif "순위 2→1" not in pain[0]["flip_under_hi"] or "순위 1→2" not in noise[0]["flip_under_hi"]:
            fails.append(("T5", "상한에서 순위가 뒤집히는데 flip_under_hi에 표시되지 않음",
                          pain[0]["flip_under_hi"]))
        if "하한 24,000 ~ 상한 47,000" not in md or "뒤집히는 항목" not in md or "⚠ 순위 2→1" not in md:
            fails.append(("T5", "md에 하한~상한 범위·뒤집힘 표시가 없음", md[:300]))
        shares = [r for r in it if r["axis"] == "Pain" and r["brand_scope"] == "논브랜드"]
        for b in ("lo", "hi"):
            tot = sum(float(r[f"volume_share_in_axis_{b}"]) for r in shares)
            if abs(tot - 1) > 0.01:
                fails.append(("T5", f"축 내 비중({b})이 자기 분모로 계산되지 않음 — 합 {tot:.3f}", ""))
        if pain and pain[0]["yoy"] and pain[0]["yoy_series_basis"] != "대표 검색어(하한 기준)":
            fails.append(("T5", "추세가 대표 검색어(하한 기준)임을 표기하지 않음", pain[0]["yoy_series_basis"]))
        scopes = {r["brand_scope"] for r in it if r["axis"] == "Feature"}
        if scopes != {"브랜드", "논브랜드"}:
            fails.append(("T8", "Feature 축에서 브랜드/논브랜드가 분리되지 않음", str(scopes)))
        if kw["휴대용 전동칫솔"]["yoy_status"] != "결측 월":
            fails.append(("T6", "결측 월 키워드가 «결측 월»로 남지 않음", kw["휴대용 전동칫솔"]["yoy_status"]))
        if kw["전동칫솔 미니 여행"]["yoy_status"] != "게이트 미달":
            fails.append(("T4", "기저 게이트 미달이 추세 계산에 들어감", kw["전동칫솔 미니 여행"]["yoy_status"]))
        if "마지막 달" not in out or "마지막 달" not in md:
            fails.append(("T2", "마지막 달 처리 방식이 출력/md에 표기되지 않음", out[-200:]))
        if not all(s in md for s in ("데이터 기준월", "조회일", "지연", LAST)):
            fails.append(("T1", "md 헤더에 기준월·조회일·지연 3종이 없음", md[:200]))
        if "volume_trend" in ",".join(header(f_kw) + header(f_it)):
            fails.append(("T3", "산출물에 volume_trend가 실렸다 — 판정 근거 금지 필드", ""))

        # P1-3 카테고리 경계 — 인접 카테고리(구강세정기)는 집계에서 빠지고, 카테고리어 없는 검색어는 경계 점검
        if kw["구강세정기 소음"]["axis"] != "경계 밖" or not kw["구강세정기 소음"]["boundary"].startswith("경계 밖"):
            fails.append(("P1-3", "exclude 패턴 검색어가 경계 밖으로 빠지지 않음", kw["구강세정기 소음"]["axis"]))
        if any("구강세정기" in r["top_keywords"] for r in it):
            fails.append(("P1-3", "경계 밖 검색어가 니즈 항목 집계에 들어감", ""))
        if kw["블루투스 앱 연동"]["boundary"] != "경계 점검":
            fails.append(("P1-3", "카테고리어 없는 검색어가 경계 점검에 오르지 않음", kw["블루투스 앱 연동"]["boundary"]))
        if "카테고리 경계 점검" not in md or "구강세정기 소음(50,000)" not in md or "블루투스 앱 연동" not in md:
            fails.append(("P1-3", "md에 경계 밖·경계 점검 목록이 없음", ""))

        # P1-4 카테고리 기준선 · 기준선 대비 · Pain 긴 꼬리 항목 합산 추세
        gum = next((r for r in it if r["need_item"].startswith("잇몸")), None)
        if not gum or gum["baseline_yoy"] == "" or abs(float(gum["baseline_yoy"]) + 0.3) > 0.05:
            fails.append(("P1-4", "카테고리 기준선 전년 동기(약 −30%)가 항목 CSV에 없음", gum and gum["baseline_yoy"]))
        elif gum["trend_call"] != "하락" or gum["trend_call_rel"] != "기준선과 같이 움직임":
            fails.append(("P1-4", "카테고리와 같이 줄어든 Pain이 «기준선과 같이 움직임»으로 판정되지 않음",
                          (gum["yoy"], gum["trend_call"], gum["yoy_rel_baseline"], gum["trend_call_rel"])))
        dj = next((r for r in it if r["need_item"].startswith("단점")), None)
        if not dj or dj["yoy"] == "" or dj["yoy_status"] != "계산됨":
            fails.append(("P1-4", "게이트 아래 Pain 긴 꼬리(M_pain)가 항목 합산으로 추세를 얻지 못함",
                          dj and dj["yoy_status"]))
        if "카테고리 기준선(전동칫솔) 전년 동기: **-30%**" not in md:
            fails.append(("P1-4", "md 헤더에 카테고리 기준선 전년 동기가 없음", ""))

        # P2 같은 검색 중복 — 지표까지 같은 변형이 상한을 부풀려 거짓 뒤집힘을 내지 않는다
        dup = [k for k in ("음파 전동칫솔", "음파전동칫솔") if kw[k]["dup_of"]]
        drive = next((r for r in it if r["need_item"] == "구동 방식" and r["brand_scope"] == "논브랜드"), None)
        if len(dup) != 1:
            fails.append(("P2", "지표 일치 중복 행에 dup_of가 붙지 않음", dup))
        if not drive or drive["volume_12m_hi"] != "60000" or drive["flip_under_hi"]:
            fails.append(("P2", "중복 행이 상한을 부풀려 거짓 «상한 시 뒤집힘»", drive and
                          (drive["volume_12m_lo"], drive["volume_12m_hi"], drive["flip_under_hi"])))
        # P2 마지막 달 미완결 의심 건수를 md에 적는다
        if not re.search(r"마지막 달 미완결 의심: [1-9]\d*/", md):
            fails.append(("P2", "md에 마지막 달 미완결 의심 건수가 없음(기준선에 마지막 달 급락을 넣었다)", ""))
    return report(fails)


def check_seed(tmp, out):
    """P1-1 시드 존재 검증은 canonical로 대조하고, 볼륨 0과 미반환을 따로 적는다."""
    f = []
    live = json.load(open(tmp / "seeds_live.json", encoding="utf-8"))
    if "음파칫솔" not in live:
        f.append(("P1-1", "응답 표기가 다른 시드(음파칫솔 → 음파 칫솔)를 죽었다고 판정 — 문자열 대조", live))
    if set(live) != {"전동칫솔", "음파칫솔", "전동칫솔 단점"}:
        f.append(("P1-1", "살아 있는 시드 목록이 틀림", live))
    if not re.search(r"미반환\s+전동칫솔 없는시드", out) or re.search(r"미반환\s+음파칫솔", out):
        f.append(("P1-1", "미반환 시드가 따로 적히지 않음", out[-300:]))
    if not re.search(r"볼륨 0\s+전동칫솔 고장", out):
        f.append(("P1-1", "볼륨 0 시드가 미반환과 구분되지 않음", out[-300:]))
    return f


def check_coverage(tmp, args, out, run):
    """P1-2 절단은 호출 단위로 본다 — 문서 예시가 호출마다 @limit을 달았는가, 한 호출만 잘려도 보류인가."""
    f = []
    rounds = [args[i + 1] for i, a in enumerate(args) if a == "--round"]
    calls = [p for r in rounds[1:] for p in r.split("=", 1)[1].split(",")]
    if len(calls) < 2 or not all("@" in p for p in calls):
        f.append(("P1-2", "문서 예시의 확장 라운드가 호출 단위(파일@limit, 쉼표 나열)가 아님", rounds))
        return f
    if "보류" in out:
        f.append(("P1-2", "잘린 호출이 없는데 보류 판정", out[-200:]))
    n = [len(json.load(open(tmp / p.split("@")[0], encoding="utf-8"))["data"]) for p in calls]
    base = [a for i, a in enumerate(args) if not (a == "--round" or (i and args[i - 1] == "--round"))]
    lab = rounds[1].split("=", 1)[0]
    # 한 호출만 limit에 붙음 → 보류 / 호출마다 limit 미만이지만 합집합은 limit 이상 → 보류 아님
    one_cut = f"{lab}=" + ",".join(f"{p.split('@')[0]}@{n[0] if i == 0 else 100}" for i, p in enumerate(calls))
    union = f"{lab}=" + ",".join(f"{p.split('@')[0]}@{max(n) + 1}" for p in calls)
    _, o1 = run("coverage_curve.py", base + ["--round", rounds[0], "--round", one_cut])
    _, o2 = run("coverage_curve.py", base + ["--round", rounds[0], "--round", union])
    if "판정: **보류**" not in o1:
        f.append(("P1-2", "호출 하나가 limit에 붙었는데 보류가 아님", o1[-200:]))
    if sum(n) >= max(n) + 1 and "판정: **보류**" in o2:
        f.append(("P1-2", "라운드 합집합 길이로 절단을 판정함(호출 단위가 아님)", o2[-200:]))
    return f


def check_stage2(tmp, out):
    """P1-4 2단 대상 = 기준선 + 게이트 통과 대표 + Pain 긴 꼬리, 크레딧 식 출력. P1-3 경계 밖은 사지 않는다."""
    f = []
    pool = json.load(open(tmp / "pool_M.json", encoding="utf-8"))
    for must, why in (("전동칫솔", "카테고리 기준선"), ("전동칫솔 단점", "Pain 긴 꼬리(M_pain)"),
                      ("전동칫솔 소음", "게이트 통과 대표")):
        if must not in pool:
            f.append(("P1-4", f"2단 대상에 {why}가 없음: {must}", pool))
    for never, why in (("구강세정기 소음", "경계 밖"), ("전동칫솔 충전안됨", "표기변형 비대표"),
                       ("전동칫솔 추천", "미분류"), ("블루투스 앱 연동", "게이트 미달 Feature")):
        if never in pool:
            f.append(("P1-4", f"2단 대상에 {why} 검색어가 들어감: {never}", ""))
    if f"크레딧 = 10 × {len(pool)} = {10 * len(pool):,}" not in out:
        f.append(("P1-4", "select_stage2 출력에 크레딧 식이 없음", out[-200:]))
    return f


def check_doc_text(text):
    """P2 문서 서술 — 교차확인 열 · 탐침어 문턱 · 예시 사전의 단점 · 지연 서술."""
    f = []
    s7 = text[text.index("### STEP 7"):text.index("## 산출물")]
    if "yoy_ex_last_month" not in s7:
        f.append(("P2", "STEP 7 교차확인이 yoy_ex_last_month를 가리키지 않음", ""))
    if "volume_threshold=10" not in text[text.index("### STEP 2"):text.index("### STEP 3")]:
        f.append(("P2", "STEP 2에 불편 탐침어 volume_threshold 10이 없음", ""))
    ex = json.load(open(SKILL / "assets" / "axes.electric-toothbrush-kr.example.json", encoding="utf-8"))
    if not any(t["name"].startswith("Pain:") and "단점" in t["pattern"] for t in ex["themes"]):
        f.append(("P2", "예시 사전 Pain에 탐침어 «단점»이 없음", ""))
    if not (ex.get("exclude") and ex.get("category_terms")):
        f.append(("P1-3", "예시 사전에 exclude·category_terms가 없음", ""))
    # 변경 이력은 폐기 사실을 적으므로 검사에서 뺀다 — v1.2.1부터 CHANGELOG.md 로 옮겨 SKILL.md 전체가 검사 대상
    cut = text.find("## 변경 이력")
    body = text if cut < 0 else text[:cut]
    for bad in ("약 2개월", "[파생]"):
        if bad in body:
            f.append(("문서", f"폐기된 서술이 남음: {bad}", ""))
    return f


def report(fails):
    if fails:
        print(f"\n[find-customer-needs] 실패 {len(fails)}건")
        for n, why, d in fails:
            print(f"  ✗ {n}: {why}")
            if d:
                print(f"      {str(d).strip()[:200]}")
        return 1
    print("[find-customer-needs] 통과 — 문서 예시 명령 7종(STEP 1→7)·컬럼·함정 6종·v1.2 회귀(P1-1~4·P2) 검증 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
