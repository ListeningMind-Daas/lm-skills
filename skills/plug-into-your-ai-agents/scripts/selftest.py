#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""번들 스크립트 계약 검증 — SKILL.md의 예시 명령이 실제로 도는지 확인한다.

왜 필요한가: 이 계열 스킬은 «명세와 코드 불일치»를 두 번 냈다. 한 번은 문서가 약속한 CSV
컬럼을 스크립트가 만들지 않았고, 한 번은 문서의 `--rules '{"..."}'` 인라인 예시를 스크립트가
파일 경로로 열어 죽었다. 둘 다 사람이 읽어서는 안 잡히고, 문서를 믿고 따라간 쪽에서만 터진다.

스킬은 플래그에 따라 공유 스크립트를 **일부만** 배치받는다. 없는 것은 «건너뜀»으로 보고하되
조용히 넘어가지 않는다.

합성 픽스처만 쓰므로 API 호출도 크레딧도 필요 없다.

    python3 scripts/selftest.py        # 스킬 폴더에서 실행
"""
import csv, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
REF_MONTHS = [f"{y}-{m:02d}" for y in (2023, 2024, 2025, 2026) for m in range(1, 13)][:48]
LAST = "2026-07"


def fixture(tmp: pathlib.Path):
    """ads_info/all 응답 모양의 합성 데이터 — 표기변형·결측 월·마지막 달 급락을 포함한다."""
    def months(base, dip_last=False, missing=()):
        out = []
        for i, m in enumerate(REF_MONTHS[: REF_MONTHS.index(LAST) + 1]):
            if m in missing:
                out.append({"month": m, "gg": 10, "nv_code": 1})   # total 키 없음
                continue
            v = base + (i % 12) * 100
            if dip_last and m == LAST:
                v = int(v * 0.2)
            out.append({"month": m, "gg": v // 3, "nv": v - v // 3, "total": v})
        return out

    data = [
        {"keyword": "선크림", "ads_metrics": {"volume_total": 90000, "volume_avg": 7000,
         "volume_trend": -0.6, "cpc": 0.5, "competition": "HIGH", "competition_index": 90,
         "gg_volume_total": 30000, "nv_volume_total": 60000}, "monthly_volume": months(5000, dip_last=True)},
        {"keyword": "썬크림", "ads_metrics": {"volume_total": 88000, "volume_avg": 6900,
         "volume_trend": 0.1, "cpc": 0.5, "competition": "HIGH", "competition_index": 88,
         "gg_volume_total": 29000, "nv_volume_total": 59000}, "monthly_volume": months(4900)},
        {"keyword": "브랜드에이 선스틱", "ads_metrics": {"volume_total": 40000, "volume_avg": 3000,
         "volume_trend": 0.3, "cpc": 0.4, "competition": "LOW", "competition_index": 20,
         "gg_volume_total": 15000, "nv_volume_total": 25000},
         "monthly_volume": months(2500, missing=("2025-03", "2025-04"))},
    ]
    (tmp / "metrics.json").write_text(json.dumps({"data": data}, ensure_ascii=False), encoding="utf-8")
    (tmp / "seeds.json").write_text(json.dumps(["선크림"], ensure_ascii=False), encoding="utf-8")
    (tmp / "r1.json").write_text(json.dumps({"data": ["썬크림", "브랜드에이 선스틱"]},
                                            ensure_ascii=False), encoding="utf-8")
    (tmp / "rules.json").write_text('{"썬":"선"}', encoding="utf-8")
    (tmp / "themes.json").write_text(json.dumps({
        "rules": {"변형치환": {"썬": "선"}},
        "themes": [{"name": "제형", "pattern": "선스틱|스틱"}],
        "brands": ["브랜드에이"],
        "no_match": {"with_brand": "브랜드·제품명", "without_brand": "코어·일반"},
    }, ensure_ascii=False), encoding="utf-8")


def run(cmd, tmp):
    p = subprocess.run([sys.executable] + cmd, cwd=tmp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def header(path):
    return set(next(csv.reader(open(path, encoding="utf-8-sig"))))


def main():
    fails, skipped = [], []
    S = str(HERE)

    def has(name):
        if (HERE / name).is_file():
            return True
        skipped.append(name)
        return False

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)

        if has("check_refmonth.py"):
            rc, out = run([f"{S}/check_refmonth.py", "metrics.json", "--queried-at", "2026-09-07"], tmp)
            if rc or LAST not in out:
                fails.append(("check_refmonth", "기준월 미출력 또는 비정상 종료", out[-300:]))
            elif "이상 의심: 0행" in out:
                fails.append(("check_refmonth", "마지막 달 급락 픽스처를 감지하지 못함", out[-300:]))

        if has("trend_yoy.py"):
            rc, out = run([f"{S}/trend_yoy.py", "metrics.json", "-o", "trend.csv"], tmp)
            need = {"yoy", "yoy_ex_last_month", "mom_last", "mom_last_prev_year",
                    "last_month_gap_vs_prev_year", "last_month_flag", "missing_months_in_window"}
            if rc:
                fails.append(("trend_yoy", "비정상 종료", out[-300:]))
            else:
                miss = need - header(tmp / "trend.csv")
                if miss:
                    fails.append(("trend_yoy", f"명세 컬럼 누락: {sorted(miss)}", ""))

        if has("build_universe.py"):
            args = [f"{S}/build_universe.py", "--metrics", "metrics.json", "--themes", "themes.json",
                    "--seeds", "seeds.json", "--round", "S1=seeds.json", "--round", "R1=r1.json",
                    "-o", "universe.csv"]
            if (tmp / "trend.csv").is_file():
                args += ["--trend", "trend.csv"]
            rc, out = run(args, tmp)
            if rc:
                fails.append(("build_universe", "비정상 종료", out[-300:]))
            else:
                miss = {"canonical", "brand", "theme_primary"} - header(tmp / "universe.csv")
                if miss:
                    fails.append(("build_universe", f"명세 컬럼 누락: {sorted(miss)}", ""))

        if has("coverage_curve.py"):
            # SKILL.md 예시 그대로 — 인라인 JSON
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json",
                           "--rules", '{"썬":"선"}', "--themes", "themes.json",
                           "--round", "S1=seeds.json", "--round", "R1=r1.json@2"], tmp)
            if rc:
                fails.append(("coverage_curve", "인라인 --rules JSON으로 비정상 종료 (문서 예시가 실패한다)", out[-300:]))
            elif "보류" not in out:
                fails.append(("coverage_curve", "절단(반환=limit)인데 «보류» 판정이 안 나옴", out[-300:]))
            elif "논브랜드" not in out:
                fails.append(("coverage_curve", "--themes를 줬는데 논브랜드 커버리지 미출력", out[-300:]))
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json",
                           "--rules", "rules.json", "--round", "S1=seeds.json"], tmp)
            if rc:
                fails.append(("coverage_curve", "--rules 파일 경로로 비정상 종료", out[-300:]))
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json",
                           "--rules", "themes.json", "--round", "S1=seeds.json"], tmp)
            if rc:
                fails.append(("coverage_curve", "--rules 에 themes.json 을 주면 비정상 종료", out[-300:]))
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json",
                           "--round", "R1=r1.json@2", "--round", "R2=seeds.json"], tmp)
            if rc or "보류" not in out:
                fails.append(("coverage_curve", "앞 라운드의 절단이 뒤 라운드에 가려 «보류»가 안 나온다(순서 의존)", out[-300:]))

        if has("trend_signals.py"):
            rc, out = run([f"{S}/trend_signals.py", "metrics.json", "-o", "sig.csv",
                           "--gate", "0", "--rules", '{"썬":"선"}'], tmp)
            need = {"growth", "accel", "persistence_months", "diffusion", "signal_score",
                    "grade", "gated_out", "canonical", "data_ref_month", "window_end"}
            if rc:
                fails.append(("trend_signals", "문서 예시(인라인 --rules)로 비정상 종료", out[-300:]))
            else:
                miss = need - header(tmp / "sig.csv")
                if miss:
                    fails.append(("trend_signals", f"명세 컬럼 누락: {sorted(miss)}", ""))
                elif "마지막 달" not in out:
                    fails.append(("trend_signals", "마지막 달 처리 방식이 출력에 표시되지 않는다", out[-200:]))

        if has("series_dedup.py"):
            sys.path.insert(0, str(HERE))
            from series_dedup import apply_rules, dedup_map
            same = [{"month": m, "total": 5000} for m in REF_MONTHS[:12]]
            rows = [{"keyword": "stick vacuum cordless", "monthly_volume": same},
                    {"keyword": "cordless stick vacuum", "monthly_volume": same},
                    {"keyword": "robot mop", "monthly_volume": [{"month": m, "total": 4000} for m in REF_MONTHS[:12]]}]
            dm = dedup_map(rows)
            if dm["stick vacuum cordless"]["rep"] != dm["cordless stick vacuum"]["rep"]:
                fails.append(("series_dedup", "시계열이 같은 어순 변형을 중복으로 묶지 못한다", ""))
            if dm["robot mop"]["rep"] != "robot mop":
                fails.append(("series_dedup", "시계열이 다른 키워드를 중복으로 묶었다", ""))
            if apply_rules("vacuum sale", {"vacuums": "vacuum"}) != "vacuum sale":
                fails.append(("series_dedup", "변형치환이 단어 경계를 넘는다", ""))
            if apply_rules("stickvacuum", {"stickvac": "stickvacuum"}) != "stickvacuum":
                fails.append(("series_dedup", "변형치환이 멱등이 아니다(stickvacuumuum)", ""))

    # 스킬 고유 스크립트 검증 — scripts/selftest_*.py 가 있으면 함께 돌린다(중첩 실행 방지)
    if not os.environ.get("LM_SELFTEST_NESTED"):
        env = dict(os.environ, LM_SELFTEST_NESTED="1")
        for extra in sorted(HERE.glob("selftest_*.py")):
            r = subprocess.run([sys.executable, str(extra)], cwd=HERE.parent, env=env,
                               capture_output=True, text=True)
            if r.returncode:
                fails.append((extra.name, "스킬 고유 selftest 실패", (r.stdout + r.stderr)[-300:]))

    if skipped:
        print(f"건너뜀 — 이 스킬에 배치되지 않은 스크립트: {', '.join(skipped)}")
    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {detail.strip()[:200]}")
        print("\n문서의 예시와 스크립트가 어긋나 있다. 배포 전에 고칠 것.")
        return 1
    checked = len([n for n in ("check_refmonth.py", "trend_yoy.py", "build_universe.py",
                               "coverage_curve.py", "trend_signals.py", "series_dedup.py") if (HERE / n).is_file()])
    print(f"통과 — 배치된 스크립트 {checked}종 계약 검증 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
