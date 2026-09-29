#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""데이터 기준월 확인 + 마지막 달 완결성 점검 (SKILL.md STEP 1.5).

조회일을 시점으로 쓰면 계절 카테고리에서 해석이 뒤집힌다. monthly_volume의
마지막 달을 읽고, 그 달이 아직 채워지는 중인지까지 검사한다.

사용법:
  python3 check_refmonth.py <keyword_info_응답.json> [--queried-at YYYY-MM-DD]

입력: keyword_info(all 또는 ads_info) 응답 JSON — {"data": [...]} 또는 [...] 둘 다 허용.
출력: 기준월·지연·완결성 판정 (사람이 읽는 형태 + --json)
"""
import argparse, json, sys
from datetime import date


def load(path):
    obj = json.load(open(path, encoding="utf-8"))
    return obj["data"] if isinstance(obj, dict) and "data" in obj else obj


def month_total(row):
    """결측 월은 0이 아니라 None. nv_code:1 월은 total 키 자체가 없다."""
    return row.get("total")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--queried-at", default=date.today().isoformat())
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    data = [d for d in load(a.path) if d.get("monthly_volume")]
    if not data:
        sys.exit("monthly_volume이 있는 행이 없다 — data_type=ads_info 또는 all로 조회했는지 확인할 것")

    ref_months = {max(m["month"] for m in d["monthly_volume"]) for d in data}   # 배열 순서 가정 없음
    ref = sorted(ref_months)[-1]
    qy, qm = int(a.queried_at[:4]), int(a.queried_at[5:7])
    ry, rm = int(ref[:4]), int(ref[5:7])
    lag = (qy - ry) * 12 + (qm - rm)

    # 마지막 달 완결성: 최신 월 MoM을 전년 같은 구간의 MoM과 비교한다.
    # 전년에 없던 급락이 올해만 보이면 집계가 아직 덜 찼을 가능성이 크다.
    prev = f"{ry}-{rm-1:02d}" if rm > 1 else f"{ry-1}-12"
    ly, lprev = f"{ry-1}-{rm:02d}", f"{ry-1}-{rm-1:02d}" if rm > 1 else f"{ry-2}-12"
    flagged, checked = [], 0
    for d in data:
        s = {r["month"]: month_total(r) for r in d["monthly_volume"]}
        if not all(s.get(m) for m in (ref, prev, ly, lprev)):
            continue
        checked += 1
        mom = s[ref] / s[prev] - 1
        gap = mom - (s[ly] / s[lprev] - 1)
        # 미완결은 "이번 달이 실제로 떨어졌는데 작년엔 안 떨어졌다"로 나타난다.
        # 올해도 오른 키워드는 작년보다 덜 올랐을 뿐이므로 제외한다.
        if mom < 0 and gap < -0.30:
            flagged.append((d["keyword"], round(gap, 3), s[prev], s[ref]))

    out = {
        "data_ref_month": ref, "queried_at": a.queried_at, "lag_months": lag,
        "rows": len(data), "checked": checked,
        "last_month_suspect": len(flagged),
        "last_month_suspect_pct": round(len(flagged) / checked * 100, 1) if checked else None,
        "mixed_ref_months": sorted(ref_months) if len(ref_months) > 1 else None,
    }
    if a.json:
        print(json.dumps({**out, "flagged": flagged[:50]}, ensure_ascii=False, indent=1)); return

    print(f"데이터 기준월 : {ref}")
    print(f"조회 실행일   : {a.queried_at}")
    print(f"지연          : 약 {lag}개월")
    if out["mixed_ref_months"]:
        print(f"⚠️ 기준월이 섞여 있다: {out['mixed_ref_months']}")
    print(f"\n마지막 달 완결성 점검 ({checked}행 검사)")
    print(f"  이상 의심: {len(flagged)}행 ({out['last_month_suspect_pct']}%)"
          " — 전년 같은 구간보다 MoM이 30%p 이상 나쁨")
    for kw, gap, p, c in flagged[:10]:
        print(f"    {kw}: {p:,} → {c:,} (전년 대비 갭 {gap:+.0%})")
    if flagged:
        print("\n→ 마지막 달을 집계에서 제외하거나, 하루 이상 뒤 재조회해 값 고정을 확인할 것.")
    print(f"\n리포트 헤더에 반드시 적을 것: 데이터 기준월 {ref} / 조회일 {a.queried_at} / 지연 {lag}개월")


if __name__ == "__main__":
    main()
