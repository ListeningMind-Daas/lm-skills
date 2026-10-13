#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""추세층 — monthly_volume에서 전년 동기 비교를 직접 계산 (SKILL.md STEP 6).

`volume_trend` 필드는 쓰지 않는다. 선크림 KR 357키워드 대조에서 이 필드는 검증
YoY와 부호가 38.1% 어긋났고, 하루 간격 재조회에서 5.6%가 값이 변했다. 정의도
monthly_volume으로 재현되지 않는다. 그래서 여기서는 창을 명시해 직접 계산한다.

결측 월 주의: nv_code:1인 달은 total 키 자체가 없다. 0으로 합산하면 조용히
과소집계되므로 결측으로 세고, 비교 창에 결측이 있으면 "계산 불가"로 남긴다.

사용법:
  python3 trend_yoy.py <ads_info.json> -o trend.csv [--gate 3000] [--ref-month 2026-07]

--ref-month는 비교 창의 **끝 달**이다(기본: 데이터의 최신 월). 다른 산출물과 창을 맞춰야 할 때 준다 —
예: 분석 본문이 2026-07에서 끝나는데 응답 최신 월이 2026-08이면 `--ref-month 2026-07`로 주어야
교차확인 값이 본문 창(2026-05~07 vs 2025-05~07)과 같아진다. 마지막 달을 빼고 볼 때도 쓴다.
"""
import argparse, csv, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import collapse_identical, dedup_map, report_collapse  # noqa: E402  같은 검색의 중복은 랭킹에 한 번만 (계약 T5)


def load(path):
    o = json.load(open(path, encoding="utf-8"))
    return o["data"] if isinstance(o, dict) and "data" in o else o


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def window(end, size):
    """end로 끝나는 size개월 창 (오래된 달 → 최신 달)."""
    return [shift(end, i) for i in range(size - 1, -1, -1)]


def acc(series, months):
    """(합계, 결측월수) — 결측은 0이 아니라 결측."""
    tot = miss = 0
    for m in months:
        r = series.get(m)
        if r is None or "total" not in r:
            miss += 1
        else:
            tot += r["total"]
    return tot, miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path"); ap.add_argument("-o", "--out", default="trend.csv")
    ap.add_argument("--ref-month", metavar="YYYY-MM",
                    help="비교 창의 끝 달 (기본: 데이터의 최신 월). 본문 분석 창에 맞출 때 준다 — "
                         "예: 본문이 2026-07에서 끝나면 --ref-month 2026-07")
    ap.add_argument("--window", type=int, default=3, help="비교 창 개월 수 (기본 3)")
    ap.add_argument("--gate", type=int, default=3000,
                    help="랭킹 진입 최소 전년 볼륨 — 기저 0 폭발 차단 (기본 3000)")
    a = ap.parse_args()

    raw = load(a.path)
    n_raw = len(raw)
    raw, n_merged, conflicts = collapse_identical(raw)   # 같은 keyword의 완전 중복 행은 한 행으로
    data = [d for d in raw if d.get("monthly_volume")]
    # 배열 순서를 가정하지 않는다 — 전 행의 달 중 최댓값이 기준월
    ref = a.ref_month or max(m["month"] for d in data for m in d["monthly_volume"] if isinstance(m, dict))
    W = a.window
    cur, prev_y = window(ref, W), [shift(m, 12) for m in window(ref, W)]
    cur_x, prev_x = window(shift(ref, 1), W), [shift(m, 12) for m in window(shift(ref, 1), W)]

    rows = []
    for d in data:
        s = {r["month"]: r for r in d["monthly_volume"]}
        a3, m1 = acc(s, cur); b3, m2 = acc(s, prev_y)
        ax, m3 = acc(s, cur_x); bx, m4 = acc(s, prev_x)
        a12, m5 = acc(s, window(ref, 12)); b12, m6 = acc(s, [shift(m, 12) for m in window(ref, 12)])
        # 마지막 달 미완결 신호: 이번 달이 실제로 떨어졌고, 그 낙폭이 전년 같은 구간보다
        # 30%p 이상 나쁘면 의심한다. 올해도 오른 키워드는 작년보다 덜 올랐을 뿐이라 제외.
        def mom(year):
            cur_m, prev_m = f"{year}-{ref[5:7]}", shift(f"{year}-{ref[5:7]}", 1)
            c, pv = s.get(cur_m), s.get(prev_m)
            if not c or not pv or "total" not in c or "total" not in pv or not pv["total"]:
                return None
            return c["total"] / pv["total"] - 1
        m_cur, m_prev = mom(int(ref[:4])), mom(int(ref[:4]) - 1)
        gap = (m_cur - m_prev) if (m_cur is not None and m_prev is not None) else None
        flag = "이상의심" if (gap is not None and m_cur is not None and m_cur < 0 and gap < -0.30) else ""

        rows.append({
            "keyword": d["keyword"],
            f"sum_{W}m_cur": a3, f"sum_{W}m_prev_year": b3,
            "yoy": round(a3 / b3 - 1, 4) if (b3 and not m1 + m2) else "",
            "yoy_ex_last_month": round(ax / bx - 1, 4) if (bx and not m3 + m4) else "",
            "yoy_12m": round(a12 / b12 - 1, 4) if (b12 and not m5 + m6) else "",
            "mom_last": round(m_cur, 4) if m_cur is not None else "",
            "mom_last_prev_year": round(m_prev, 4) if m_prev is not None else "",
            "last_month_gap_vs_prev_year": round(gap, 4) if gap is not None else "",
            "last_month_flag": flag,
            "missing_months_in_window": m1 + m2,
            "missing_months_total": sum(1 for r in d["monthly_volume"] if "total" not in r),
            "api_volume_trend_do_not_use": (d.get("ads_metrics") or {}).get("volume_trend"),
            "data_ref_month": max(m["month"] for m in d["monthly_volume"]),
        })

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    dups = {k for k, v in dedup_map(data).items() if v["rep"] != k}
    # 제외 사유는 서로 배타적으로 한 번씩만 센다 — 계산 불가 → 같은 검색 중복 → 게이트 미만 순
    no_calc = [r for r in rows if r["yoy"] == ""]
    dup_rows = [r for r in rows if r["yoy"] != "" and r["keyword"] in dups]
    below = [r for r in rows if r["yoy"] != "" and r["keyword"] not in dups and r[f"sum_{W}m_prev_year"] < a.gate]
    pool = [r for r in rows if r["yoy"] != "" and r["keyword"] not in dups and r[f"sum_{W}m_prev_year"] >= a.gate]
    cmp_ = [r for r in rows if r["yoy"] != "" and r["api_volume_trend_do_not_use"] is not None]
    mism = [r for r in cmp_ if (r["yoy"] > 0) != (r["api_volume_trend_do_not_use"] > 0)]

    flagged = [r for r in rows if r["last_month_flag"]]
    print(f"기준월 {ref} · 비교 창 {cur[0]}~{cur[-1]} vs {prev_y[0]}~{prev_y[-1]}")
    print(f"마지막 달 미완결 의심: {len(flagged)}행 ({len(flagged)/len(rows):.1%}) "
          f"— 판정은 yoy_ex_last_month와 교차확인할 것")
    report_collapse(n_merged, conflicts, n_raw)
    print(f"{len(rows)}행 → 랭킹 풀 {len(pool)}행 · 제외 {len(rows) - len(pool)}행")
    print(f"  계산 불가 {len(no_calc)}행 (비교 창에 결측 월 또는 전년 합 0) · "
          f"같은 검색의 중복 {len(dup_rows)}행 (대표 행만 랭킹) · 게이트 미만 {len(below)}행 (전년 {W}개월 합 < {a.gate:,})")
    if cmp_:
        print(f"참고: API volume_trend와 부호 불일치 {len(mism)}/{len(cmp_)}행 "
              f"({len(mism)/len(cmp_):.1%}) — 이 필드를 판정에 쓰면 안 되는 이유")
    for title, rev in (("라이징", True), ("하강", False)):
        print(f"\n[{title}]")
        side = [r for r in pool if (r["yoy"] > 0 if rev else r["yoy"] < 0)]   # 한 키워드가 양쪽에 나오지 않게
        for r in sorted(side, key=lambda r: -r["yoy"] if rev else r["yoy"])[:10]:
            print(f"  {r['yoy']:+8.0%}  {r[f'sum_{W}m_prev_year']:>9,} → {r[f'sum_{W}m_cur']:>9,}  {r['keyword']}")
    print(f"\n→ {a.out}")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):      # Windows cp949 콘솔에서 ⚠·— 출력이 UnicodeEncodeError 로 죽지 않게
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
