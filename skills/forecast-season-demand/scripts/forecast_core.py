#!/usr/bin/env python3
"""forecast-season-demand 계산 핵심 — 순수 함수만 둔다 (forecast_segments.py · selftest가 import).

예측법은 **결정적**이다. 같은 입력이면 같은 숫자가 나온다. 모델을 고르거나 학습하지 않는다.

  예측(k번째 달) = 전년 같은 달 실측 × r                        [추정 — 규칙 기반]
  r = 최근 W개월 합 ÷ 전년 같은 W개월 합                         (W 기본 3 · 창 끝 = 기준월 − 1)
      · 전년 W개월 합 < 게이트 → r = 1 (전년 그대로) + «기저 게이트 미달»  (계약 T4)
      · r은 [R_MIN, R_MAX]로 자른다 + «비율 상·하한 적용»          [가정]
  범위 = {r, 다른 모드 r(마지막 달 포함/제외), 12개월 r} 중 최소~최대를 같은 전년 값에 곱한 것  [추정]
  점검 = 기준 시점을 H개월 앞당겨 같은 규칙으로 맞춰 본 합계 오차(1회 홀드아웃)

계절 프로파일은 창 끝에서 거꾸로 자른 12개월 블록 3개로 만든다(달력 해가 아니다).
  지수(달) = 블록마다 (그 달 값 ÷ 블록 월평균)의 평균
  계절형 = 최대 지수 ≥ SEASONAL_PEAK 이고 블록 셋 중 둘 이상이 같은(±1개월) 달에 정점  [가정]

시점 규칙(전부 [가정]): 상승 시작 = 정점에서 거꾸로 지수 ≥ 1.0 이 이어지는 첫 달(정점 혼자면 정점 − 1),
  준비 = 상승 − 2개월 · 출시 = 상승 − 1개월 · 캠페인 = 상승 ~ 정점 · 종료 = 정점 뒤 처음으로 지수 < 1.0 인 달.
"""

R_MIN, R_MAX = 0.5, 2.0          # [가정] 비율 상·하한
SEASONAL_PEAK = 1.3             # [가정] 계절형 최대 지수 하한
RAMP_LEVEL = 1.0                # [가정] 상승 판정 지수
PREP_LEAD, LAUNCH_LEAD = 2, 1   # [가정] 상승 시작보다 몇 개월 앞서 준비 · 출시
QUALITY_ERR = 0.25              # [가정] 점검 오차가 이보다 크면 «참고»
ANTI_PHASE_GAP = 3              # [가정] 카테고리 정점과 이만큼(개월) 이상 떨어지면 역위상
PROFILE_BLOCKS = 3


# ───────────────────────── 월 계산
def month_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7])
    i = y * 12 + (m - 1) + k
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def month_diff(a, b):
    """b − a (개월)."""
    return (int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))


def cal(ym):
    return int(ym[5:7])


def window_months(ref, n):
    """기준월로 끝나는 n개월 (오래된 달 → 최신 달)."""
    return [month_add(ref, -k) for k in range(n - 1, -1, -1)]


def required_months(ref, horizon, window):
    """한 검색어가 계산에 들어가려면 값이 있어야 하는 달 — 마지막 달 포함 모드까지 덮는다.
    프로파일 36개월(창 끝 = 기준월 − 1) + 기준월 = 37개월. 비율 · 점검 · 12개월 범위가 모두 이 안에 든다."""
    need = PROFILE_BLOCKS * 12 + 1
    assert horizon + window + 12 <= need and 24 <= need
    return window_months(ref, need)


# ───────────────────────── 비율과 예측
def ratio(series, end, window, gate):
    """(r, 최근 합, 전년 합, 메모). series: {월: 값}. 결측 달이 있으면 r=None."""
    cur_m = [month_add(end, -k) for k in range(window)]
    prev_m = [month_add(m, -12) for m in cur_m]
    if any(series.get(m) is None for m in cur_m + prev_m):
        return None, None, None, "계산 불가 — 결측 월"
    cur, prev = sum(series[m] for m in cur_m), sum(series[m] for m in prev_m)
    if prev <= 0:
        return 1.0, cur, prev, "계산 불가 — 전년 0 · 비율 미적용(r=1)"
    if prev < gate:
        return 1.0, cur, prev, "기저 게이트 미달 — 비율 미적용(r=1)"
    r = cur / prev
    if r < R_MIN or r > R_MAX:
        return min(R_MAX, max(R_MIN, r)), cur, prev, f"비율 상·하한 적용(원래 r={r:.3f})"
    return r, cur, prev, ""


def forecast(series, ref, horizon, r):
    """[(예측 월, 전년 같은 달, 전년 값, 예측값)]."""
    out = []
    for k in range(1, horizon + 1):
        m = month_add(ref, k)
        ly = month_add(m, -12)
        base = series.get(ly)
        out.append((m, ly, base, None if base is None else base * r))
    return out


def backtest_sums(series, end, horizon, window, gate):
    """창 끝(end)에서 H개월 앞당긴 기준(o = end − H)으로 같은 규칙을 적용해 o+1..end 를 맞춰 본다.
    (예측 합, r=1 순수 계절 나이브 합, 실측 합). 계산 불가면 None."""
    o = month_add(end, -horizon)
    r, _, _, _ = ratio(series, o, window, gate)
    if r is None:
        return None
    fc = forecast(series, o, horizon, r)
    act = [series.get(m) for m, _, _, _ in fc]
    if any(a is None for a in act) or any(f is None for *_, f in fc):
        return None
    return sum(f for *_, f in fc), sum(b for _, _, b, _ in fc), sum(act)


def backtest(series, end, horizon, window, gate):
    """(부호 있는 합계 오차 = 예측 합 ÷ 실측 합 − 1, 같은 오차의 r=1 나이브). 계산 불가면 (None, None)."""
    s = backtest_sums(series, end, horizon, window, gate)
    if not s or s[2] <= 0:
        return None, None
    return s[0] / s[2] - 1, s[1] / s[2] - 1


# ───────────────────────── 계절 프로파일과 시점
def profile(series, end):
    """창 끝(end)에서 거꾸로 12개월 블록 3개. 반환 dict:
    blocks[{label, months, values, index(달력 1~12 → 지수)}], si(달력 1~12 → 평균 지수), peak, trough,
    peak_consistency(블록 정점이 ±1개월 안에 든 블록 수), amplitude, season_type."""
    blocks = []
    for b in range(PROFILE_BLOCKS - 1, -1, -1):
        b_end = month_add(end, -12 * b)
        ms = window_months(b_end, 12)
        vals = [series.get(m) for m in ms]
        if any(v is None for v in vals):
            return None
        mean = sum(vals) / 12
        idx = {cal(m): (v / mean if mean > 0 else None) for m, v in zip(ms, vals)}
        blocks.append({"label": f"{ms[0]}~{ms[-1]}", "months": ms, "values": vals, "index": idx})
    si = {}
    for c in range(1, 13):
        xs = [b["index"][c] for b in blocks if b["index"][c] is not None]
        si[c] = sum(xs) / len(xs) if xs else None
    if any(v is None for v in si.values()):
        return {"blocks": blocks, "si": si, "peak": None, "trough": None, "peak_consistency": 0,
                "amplitude": None, "season_type": "판정 불가"}
    peak = max(range(1, 13), key=lambda c: (si[c], -c))
    trough = min(range(1, 13), key=lambda c: (si[c], c))
    cons = 0
    for b in blocks:
        bp = max(range(1, 13), key=lambda c: ((b["index"][c] or 0), -c))
        if circ(bp, peak) <= 1:
            cons += 1
    amp = si[peak] / max(si[trough], 0.05)
    if si[peak] < SEASONAL_PEAK:
        st = "상시형"
    elif cons >= 2:
        st = "계절형"
    else:
        st = "불규칙"
    return {"blocks": blocks, "si": si, "peak": peak, "trough": trough, "peak_consistency": cons,
            "amplitude": amp, "season_type": st}


def circ(a, b):
    """달력 달 사이 거리(0~6)."""
    d = abs(a - b) % 12
    return min(d, 12 - d)


def ramp_month(si, peak):
    """정점에서 거꾸로 지수 ≥ RAMP_LEVEL 이 이어지는 첫 달. 정점 혼자면 정점 − 1. 1년 내내 ≥ 이면 None."""
    i, steps = peak, 0
    while steps < 11 and si[(i - 2) % 12 + 1] >= RAMP_LEVEL:
        i = (i - 2) % 12 + 1
        steps += 1
    if steps >= 11:
        return None
    return (peak - 2) % 12 + 1 if i == peak else i


def end_month(si, peak):
    for k in range(1, 12):
        j = (peak - 1 + k) % 12 + 1
        if si[j] < RAMP_LEVEL:
            return j
    return None


def next_occurrence(calendar_month, from_ym):
    return month_add(from_ym, (calendar_month - cal(from_ym)) % 12)


def timing(prof, queried_month, horizon_months):
    """계절형(·불규칙은 참고) 세그먼트의 날짜 달린 일정. 상시형 · 판정 불가는 빈 일정."""
    empty = {"ramp_month": None, "prep": None, "launch": None, "campaign_from": None, "peak": None,
             "end": None, "status": "", "peak_in_horizon": ""}
    if not prof or prof["season_type"] not in ("계절형", "불규칙"):
        empty["status"] = "일정 없음 — " + (prof["season_type"] if prof else "판정 불가")
        return empty
    si, peak = prof["si"], prof["peak"]
    rm = ramp_month(si, peak)
    p = next_occurrence(peak, queried_month)
    back = (peak - rm) % 12 if rm else 2
    ramp = month_add(p, -back)
    prep, launch = month_add(ramp, -PREP_LEAD), month_add(ramp, -LAUNCH_LEAD)
    em = end_month(si, peak)
    end = next_occurrence(em, month_add(p, 1)) if em else month_add(p, 2)
    if month_diff(queried_month, prep) >= 0:
        status = "준비 가능"
    elif month_diff(queried_month, launch) >= 0:
        status = "준비 촉박"
    elif month_diff(queried_month, ramp) >= 0:
        status = "캠페인 시작 시점 (출시 시점 지남)"
    else:
        status = "시즌 진행 중"
    if prof["season_type"] == "불규칙":
        status += " (참고 — 정점이 해마다 다름)"
    return {"ramp_month": rm, "prep": prep, "launch": launch, "campaign_from": ramp, "peak": p, "end": end,
            "status": status, "peak_in_horizon": "예" if p in horizon_months else "아니오"}
