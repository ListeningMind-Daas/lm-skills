#!/usr/bin/env python3
"""4지표 초기 트렌드 신호 스코어링 — 성장률·가속도·지속성·확산 폭.

계절 카테고리에서 위양성이 나오지 않는 것이 이 스크립트의 첫 요건이다. 그래서 네 지표를
모두 **전년 동월 대비**로 계산한다. 직전 기간 대비(MoM)로 계산하면 성수기 진입이 곧
'가속'으로, 비수기 진입이 '급락'으로 잡힌다 — 계절이 트렌드로 둔갑한다.

  성장률   최근 W개월 합 / 전년 같은 W개월 합 − 1            (전년 창 볼륨 ≥ --gate)
  가속도   최근 창의 성장률 − 직전 창의 성장률 (%p)          (직전 창의 전년 볼륨도 ≥ --gate)
  지속성   기준월부터 거슬러 «전년 동월 대비 상승»이 이어진 개월 수
  확산 폭  띄어쓰기 토큰을 공유하는 다른 키워드 중 함께 상승한 비율 (같은 표기변형·같은 검색의 중복은 제외)
           · 머리 토큰(행 절반 이상이 공유 [가정])은 다른 토큰으로 이웃을 못 찾을 때만 쓴다

등급은 두 관문을 모두 통과해야 붙는다.
  ① 절대 하한 — 성장률 > --min-growth(기본 0) [가정]. 마지막 달 포함 모드면 «마지막 달 제외» 성장률도 넘어야 한다.
     백분위만 쓰면 후보군 전체가 하락할 때 하락 키워드가 등급을 받는다.
  ② 백분위 점수 — 성장률 35% + 가속도 35% + 지속성 15% + 확산 15%, S ≥ 0.85 · A ≥ 0.70 · B ≥ 0.55 [가정]
     판정 행이 --abs-pool(기본 10) [가정]보다 적으면 백분위가 뜻이 없다(1행이면 최고 점수 0.15). 그때는
     절대 기준으로 매긴다 [가정] — S: 성장률 ≥ 50% · 지속 ≥ 6개월 · 가속도 ≥ 0 / A: 성장률 ≥ 30% · 지속 ≥ 3개월 ·
     가속도 > −20%p(또는 계산 불가) / B: 성장률 ≥ 15% · 지속 ≥ 2개월. CSV grade_basis에 어느 기준인지 남긴다.

기본값은 **마지막 달 제외**다. 마지막 달은 집계가 덜 찬 경우가 있고(관측 사례: 하루 뒤
27,100 → 65,800), 가속도와 지속성이 거기에 가장 민감하다. 스크립트는 **두 모드를 모두 계산**해
등급·성장률 부호가 모드에 따라 달라지는 행에 «잠정»을 단다. --include-last-month는 주 모드만 바꾼다.

    python3 trend_signals.py <keyword_info(all) 응답.json> -o signals.csv [--rules '{"썬":"선"}']
        [--requested candidates.json] [--exclude '머신러닝|e러닝'] [--window 12]
        [--evidence issue_evidence.csv]   # issue_candidates.py 출력 — 이슈 근거를 붙이고 미판정 후보를 따로 낸다

--evidence를 주면 이슈 근거(issue_*)를 canonical로 신호 행에 붙이고, 검색량으로 판정하지 못한 이슈 후보
(미반환·시계열 없음·게이트 미달·계산 불가·범위 밖 제외)를 «이슈 신호만 · 검색량 미확인» CSV로 따로 쓴다.
그 행에는 등급을 주지 않는다 — 이슈는 검색량이 아니다. 조용히 버리지도 않는다.
"""
import argparse, csv, json, pathlib, re, sys, unicodedata
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import apply_rules, canon, dedup_map, load_rules  # noqa: E402  계약 T5

SIGNAL_COLS = ["keyword", "canonical", "dup_of", "volume_prev_year_window", "missing_in_window",
               "group_prev_year_lo", "group_prev_year_hi", "growth", "growth_excl_last", "accel", "accel_note",
               "persistence_months", "diffusion", "signal_score", "grade", "grade_floor", "grade_other_mode",
               "provisional", "gated_out", "data_ref_month", "window_end"]
ISSUE_COLS = ["issue_score", "issue_n_dates", "issue_summary_ai", "issue_source_checked", "issue_sources"]
EXTRA_COLS = ["volume_window", "volume_latest", "first_active_month", "grade_basis"]
ISSUE_ONLY_COLS = ["keyword", "canonical", "reason", "issue_score", "issue_n_dates", "index_date_last",
                   "after_ref_month", "summary_ai", "source_checked", "source_links", "data_ref_month",
                   "volume_avg", "screen"]
ABS_POOL = 10                                   # [가정] 판정 행이 이보다 적으면 절대 기준으로 등급
ABS_TIERS = (("S", 0.50, 6, 0.0), ("A", 0.30, 3, -0.20), ("B", 0.15, 2, None))  # [가정] 등급·성장률·지속·가속도 하한
SMALL_POOL_ABS, SMALL_POOL_SHARE = 20, 0.25   # [가정] 판정 가능 행이 이보다 적으면 풀이 작다
HEAD_SHARE = 0.5                                # [가정] 이 비율 이상의 행이 공유하는 토큰 = 카테고리 머리 토큰


def load(path):
    o = json.load(open(path, encoding="utf-8"))
    return o["data"] if isinstance(o, dict) and "data" in o else o


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def win(end, size):
    return [shift(end, i) for i in range(size - 1, -1, -1)]


def acc(series, months):
    """(합계, 결측월수) — 결측은 0이 아니라 결측. nv_code 월은 total 키가 없다."""
    tot = miss = 0
    for m in months:
        r = series.get(m)
        if r is None or "total" not in r:
            miss += 1
        else:
            tot += r["total"]
    return tot, miss


def yoy(series, end, size):
    """(성장률 또는 None, 전년 창 볼륨, 결측 여부)."""
    a, m1 = acc(series, win(end, size))
    b, m2 = acc(series, [shift(m, 12) for m in win(end, size)])
    if m1 or m2 or not b:
        return None, b, bool(m1 or m2)
    return a / b - 1, b, False


def tokens(kw, rules):
    """띄어쓰기를 살린 채 토큰화한다 — 공백을 지운 canonical은 한국어에서 토큰이 하나뿐이라 확산을 못 잰다."""
    s = apply_rules(unicodedata.normalize("NFKC", kw).lower(), rules)
    return {t for t in re.split(r"[^0-9a-z가-힣]+", s) if len(t) >= 2}


def metrics_at(s, ref, W, gate):
    g, base, miss = yoy(s, ref, W)
    if g is None:
        status = "계산 불가"
    elif base < gate:
        status = "게이트 미달"
    else:
        status = ""
    gp, pbase, pmiss = yoy(s, shift(ref, W), W)
    if g is None or gp is None:
        accel, anote = "", "계산 불가"
    elif pbase < gate:
        accel, anote = "", "직전 창 게이트 미달"
    else:
        accel, anote = round(g - gp, 4), ""
    persist = 0
    for i in range(0, 12):
        m, p = shift(ref, i), shift(shift(ref, i), 12)
        cm, pm = s.get(m), s.get(p)
        if not cm or not pm or "total" not in cm or "total" not in pm or not pm["total"]:
            break
        if cm["total"] / pm["total"] - 1 > 0:
            persist += 1
        else:
            break
    cur = acc(s, win(ref, W))[0]
    return {"growth": round(g, 4) if g is not None else "", "base": base, "cur": cur, "miss": acc(s, win(shift(ref, 12), W))[1],
            "status": status, "accel": accel, "accel_note": anote, "persist": persist}


def abs_grade(m):
    """작은 풀 — 절대 기준 [가정]. 가속도가 빈칸(계산 불가·직전 창 게이트 미달)이면 S는 주지 않는다."""
    a = m["accel"]
    for g, gmin, pmin, amin in ABS_TIERS:
        if m["growth"] < gmin or m["persist"] < pmin:
            continue
        if amin is None:
            return g
        if a == "":
            if g != "S":
                return g
            continue
        if (a >= amin) if g == "S" else (a > amin):
            return g
    return ""


def score_mode(rows, key, min_growth, floor_key=None, abs_pool=ABS_POOL):
    """rows[i][key] = metrics_at 결과. 등급을 매겨 rows[i][key]['score'|'grade']에 넣는다."""
    live = [r for r in rows if not r["_excl"] and not r[key]["status"]]
    # 확산 폭 — 띄어쓰기 토큰을 공유하는 다른 키워드 중 함께 상승한 비율.
    for r in rows:
        # 카테고리 머리 토큰(행의 절반 이상이 공유 — 예: «선스틱»)만으로 이으면 확산이 «카테고리 전체 상승률»로
        # 퇴화한다. 머리 아닌 토큰으로 이은 이웃이 있으면 그것을 쓰고, 없을 때만 머리 토큰까지 넓힌다.
        pool = []
        for toks in (r["_tok"] - r["_head"], r["_tok"]):
            peers = {id(x): x for t in toks for x in r["_by_tok"][t]
                     if x is not r and x["canonical"] != r["canonical"] and x["_rep"] != r["_rep"]}
            pool = [x for x in peers.values() if not x["_excl"] and not x[key]["status"]]
            if pool:
                break
        r[key]["diffusion"] = round(sum(1 for x in pool if x[key]["growth"] > 0) / len(pool), 3) if pool else ""

    def pct_rank(field):
        vals = sorted(r[key][field] for r in live if r[key][field] != "")
        return lambda v: (sum(1 for x in vals if x < v) / len(vals)) if vals else 0
    rk_g, rk_a, rk_d = pct_rank("growth"), pct_rank("accel"), pct_rank("diffusion")
    for r in rows:
        m = r[key]
        m["score"], m["grade"], m["floor"], m["basis"] = "", "", "", ""
        if r not in live:
            continue
        sc = (0.35 * rk_g(m["growth"])
              + 0.35 * (rk_a(m["accel"]) if m["accel"] != "" else 0)
              + 0.15 * min(m["persist"], 6) / 6
              + 0.15 * (rk_d(m["diffusion"]) if m["diffusion"] != "" else 0))
        m["score"] = round(sc, 4)
        m["basis"] = f"절대 기준(판정 {len(live)}행 < {abs_pool})" if len(live) < abs_pool else "백분위"
        ok = m["growth"] > min_growth
        if floor_key:
            fg = r[floor_key]["growth"]
            ok = ok and fg != "" and fg > min_growth
        if not ok:
            m["floor"] = "하한 미달"
            continue
        if len(live) < abs_pool:
            m["grade"] = abs_grade(m)
        else:
            m["grade"] = "S" if sc >= 0.85 else "A" if sc >= 0.70 else "B" if sc >= 0.55 else ""
    return live


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("-o", "--out", default="trend_signals.csv")
    ap.add_argument("--window", type=int, default=3, help="비교 창 개월 수 (기본 3 · 비수기 계절 품목은 12 권장)")
    ap.add_argument("--gate", type=int, default=3000,
                    help="전년 동기 창 최소 볼륨 — 기저 0 폭발 차단 (기본 3000). 가속도의 직전 창에도 적용")
    ap.add_argument("--min-growth", type=float, default=0.0,
                    help="등급을 받을 최소 성장률 (기본 0 = 상승한 키워드만) [가정]")
    ap.add_argument("--include-last-month", action="store_true",
                    help="마지막 달을 포함한 쪽을 주 모드로 쓴다. 미완결 가능성을 리포트에 명시할 것")
    ap.add_argument("--rules", default="", help='표기변형 치환 — 인라인 JSON · 파일 · themes.json')
    ap.add_argument("--requested", help="요청한 후보 목록 JSON — 응답에 없는 키워드를 미반환으로 출력")
    ap.add_argument("--exclude", help="범위 밖 키워드 정규식 — 스코어링 전에 뺀다 (예: '머신러닝|e러닝')")
    ap.add_argument("--evidence", help="issue_candidates.py의 issue_evidence.csv — 이슈 근거 결합 + 미판정 후보 분리")
    ap.add_argument("--issue-only-out", help="«이슈 신호만 · 검색량 미확인» CSV 경로 (기본: <출력>_issue_only.csv)")
    ap.add_argument("--issue-meta", help="issue_candidates.py의 issue_meta.json — 스크린 결과(volume_avg)로 미확인 사유를 가르고, "
                                         "--exclude가 없으면 거기 적힌 범위 밖 정규식을 쓴다")
    ap.add_argument("--abs-pool", type=int, default=ABS_POOL,
                    help=f"판정 행이 이보다 적으면 백분위 대신 절대 기준으로 등급 (기본 {ABS_POOL}) [가정]")
    a = ap.parse_args()
    meta = json.load(open(a.issue_meta, encoding="utf-8")) if a.issue_meta else None
    if meta and not a.exclude and meta.get("exclude"):
        a.exclude = meta["exclude"]
        print(f"범위 밖 정규식을 issue_meta에서 읽음: {a.exclude[:80]}{'…' if len(a.exclude) > 80 else ''}")

    rules = load_rules(a.rules)
    raw = [d for d in load(a.path) if isinstance(d, dict) and d.get("keyword")]
    missing_req = []
    if a.requested:
        req = json.load(open(a.requested, encoding="utf-8"))
        req = [k if isinstance(k, str) else k.get("keyword") for k in req]
        got = {canon(d["keyword"], rules) for d in raw}
        missing_req = [k for k in req if k and canon(k, rules) not in got]
    data = [d for d in raw if d.get("monthly_volume")]
    if not data and a.evidence:
        # 스크린을 통과한 후보가 없어 all을 부르지 않은 경우 — 이슈 후보를 전부 «검색량 미확인»으로 남긴다
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=SIGNAL_COLS + ISSUE_COLS + EXTRA_COLS).writeheader()
        only = join_evidence(a, [], raw, [], rules, "", meta)
        print(f"monthly_volume 없음 — 판정 0행. «이슈 신호만 · 검색량 미확인» {len(only)}건 → {a.issue_only_out}")
        return
    if not data:
        sys.exit("monthly_volume이 없다 — data_type=all로 조회했는지 확인할 것")
    dropped = []
    if a.exclude:
        pat = re.compile(a.exclude)
        dropped = [d["keyword"] for d in data if pat.search(d["keyword"])]
        data = [d for d in data if not pat.search(d["keyword"])]

    data_ref = max(m["month"] for d in data for m in d["monthly_volume"] if m.get("month"))
    ref_ex, ref_in = shift(data_ref, 1), data_ref
    ref = ref_in if a.include_last_month else ref_ex
    W = a.window
    dmap = dedup_map(data, rules)

    rows, by_tok = [], defaultdict(list)
    for d in data:
        s = {r["month"]: r for r in d["monthly_volume"] if r.get("month")}
        rep = dmap.get(d["keyword"], {}).get("rep", d["keyword"])
        act = [m for m in sorted(s) if s[m].get("total")]
        r = {"keyword": d["keyword"], "canonical": canon(d["keyword"], rules),
             "_latest": (s.get(data_ref) or {}).get("total", ""), "_first": act[0] if act else "",
             "dup_of": "" if rep == d["keyword"] else rep, "_rep": rep,
             "_tok": tokens(d["keyword"], rules), "_by_tok": by_tok,
             "ex": metrics_at(s, ref_ex, W, a.gate), "in": metrics_at(s, ref_in, W, a.gate)}
        r["_excl"] = bool(r["dup_of"])     # 같은 검색의 중복 — 대표 행에서 한 번만 판정한다
        rows.append(r)
        for t in r["_tok"]:
            by_tok[t].append(r)

    head = {t for t, xs in by_tok.items() if len(xs) >= HEAD_SHARE * len(rows)}
    for r in rows:
        r["_head"] = head
    main_k, alt_k = ("in", "ex") if a.include_last_month else ("ex", "in")
    live = score_mode(rows, main_k, a.min_growth, floor_key="ex" if main_k == "in" else None, abs_pool=a.abs_pool)
    score_mode(rows, alt_k, a.min_growth, floor_key="ex" if alt_k == "in" else None, abs_pool=a.abs_pool)

    # 표기변형 그룹의 전년 동기 창 볼륨 — 하한(그룹 내 최댓값) ~ 상한(그룹 내 합), 계약 T5.
    # 같은 검색의 중복(dup_of)은 한 번만 센다. 나머지 변형은 같은 검색인지 응답이 알려주지 않는다.
    g_lo, g_hi, g_n = {}, {}, defaultdict(int)
    for r in rows:
        if r["dup_of"]:
            continue
        c, v = r["canonical"], r[main_k]["base"]
        g_lo[c] = max(g_lo.get(c, 0), v); g_hi[c] = g_hi.get(c, 0) + v; g_n[c] += 1

    out = []
    for r in rows:
        m, o = r[main_k], r[alt_k]
        if r["dup_of"]:
            gated = "같은 검색의 중복"
        else:
            gated = m["status"]
        prov = ""
        if (m["grade"] or o["grade"]) and (m["grade"] != o["grade"] or
                                           (m["growth"] != "" and o["growth"] != "" and (m["growth"] > 0) != (o["growth"] > 0))):
            prov = "잠정"
        grp = r["_rep"] if r["dup_of"] else r["canonical"]
        out.append({
            "keyword": r["keyword"], "canonical": r["canonical"], "dup_of": r["dup_of"],
            "volume_prev_year_window": m["base"], "missing_in_window": m["miss"],
            "group_prev_year_lo": g_lo.get(r["canonical"], ""), "group_prev_year_hi": g_hi.get(r["canonical"], ""),
            "growth": m["growth"], "growth_excl_last": r["ex"]["growth"],
            "accel": m["accel"], "accel_note": m["accel_note"],
            "persistence_months": m["persist"], "diffusion": m["diffusion"],
            "signal_score": m["score"], "grade": m["grade"], "grade_floor": m["floor"],
            "grade_other_mode": o["grade"], "provisional": prov,
            "gated_out": gated,
            "data_ref_month": data_ref, "window_end": ref,
            "volume_window": m["cur"], "volume_latest": r["_latest"], "first_active_month": r["_first"],
            "grade_basis": m["basis"],
        })
    for r in out:
        r.update({c: "" for c in ISSUE_COLS})
    issue_only = join_evidence(a, out, raw, dropped, rules, data_ref, meta) if a.evidence else None
    out.sort(key=lambda r: -(r["signal_score"] or 0))
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SIGNAL_COLS + ISSUE_COLS + EXTRA_COLS); w.writeheader(); w.writerows(out)

    n_gate = sum(1 for r in out if r["gated_out"] == "게이트 미달")
    n_unc = sum(1 for r in out if r["gated_out"] == "계산 불가")
    n_dup = sum(1 for r in out if r["gated_out"] == "같은 검색의 중복")
    rising = [r for r in live if r[main_k]["growth"] > 0]
    print(f"데이터 기준월 {data_ref} · 계산 창 {W}개월 · 종료월 {ref}"
          f"{' (마지막 달 포함)' if a.include_last_month else ' (마지막 달 제외)'}")
    if missing_req:
        print(f"[데이터 공백] 요청했지만 응답에 없는 키워드 {len(missing_req)}개 (과금은 요청 수 기준): "
              + ", ".join(missing_req[:10]) + (" …" if len(missing_req) > 10 else ""))
    if dropped:
        print(f"범위 밖으로 뺀 키워드 {len(dropped)}개 (--exclude): " + ", ".join(dropped[:10])
              + (" …" if len(dropped) > 10 else ""))
    print(f"{len(out)}행 → 판정 {len(live)}행 · 게이트 미달 {n_gate} · 계산 불가 {n_unc} · 같은 검색의 중복 {n_dup}"
          + (f" · 상승 {len(rising)}행 ({len(rising)/len(live):.0%})" if live else ""))
    n_ag = sum(1 for r in live if r[main_k]["accel_note"] == "직전 창 게이트 미달")
    if n_ag:
        print(f"가속도 빈칸 {n_ag}행 — 직전 창의 전년 볼륨이 게이트 {a.gate:,} 미만 (기저 폭발 차단)")
    multi = [c for c in g_lo if g_n[c] > 1]
    print(f"canonical {len(g_lo)}그룹 (표기변형 {sum(g_n.values()) - len(g_lo)}행 · 같은 검색의 중복 {n_dup}행은 한 번만) · "
          f"전년 동기 창 볼륨: 하한 {sum(g_lo.values()):,} ~ 상한 {sum(g_hi.values()):,}  "
          "※ 하한=그룹 내 최댓값 · 상한=그룹 내 합 [추정 — 규칙 기반 집계]")
    for c in sorted(multi, key=lambda c: -g_lo[c])[:10]:
        kws = [r["keyword"] for r in out if r["canonical"] == c and not r["dup_of"]]
        print(f"  그룹 {c}: {' / '.join(kws)} — 하한 {g_lo[c]:,} ~ 상한 {g_hi[c]:,}건 [추정]")
    for r in out:
        if r["dup_of"]:
            print(f"  같은 검색의 중복: {r['keyword']} → {r['dup_of']} (한 번만 셈 [추정])")
    n_floor = sum(1 for r in out if r["grade_floor"])
    cnt = lambda g: sum(1 for r in out if r["grade"] == g)
    print(f"등급: S {cnt('S')} · A {cnt('A')} · B {cnt('B')}"
          f"  (성장률 ≤ {a.min_growth:+.0%}라 등급을 주지 않은 행 {n_floor} — 절대 하한 [가정])")
    graded_any = [r for r in out if r["grade"] or r["grade_other_mode"]]
    prov = [r for r in out if r["provisional"]]
    other = "마지막 달 제외" if a.include_last_month else "마지막 달 포함"
    print(f"두 모드 비교 ({other} 모드와): 등급 보유 {len(graded_any)}행 중 {len(prov)}행이 등급·부호가 달라 «잠정»")
    if live and len(live) < a.abs_pool:
        print(f"⚠️ 판정 {len(live)}행 < {a.abs_pool} [가정] — 백분위 대신 절대 기준으로 등급을 매겼다 "
              "(S 성장률≥50%·지속≥6·가속도≥0 / A ≥30%·≥3·가속도>−20%p / B ≥15%·≥2) · grade_basis 컬럼")

    # 작은 풀 — 비수기 계절 품목은 3개월 창의 전년 볼륨이 게이트에 대거 걸린다
    if len(live) < max(SMALL_POOL_ABS, SMALL_POOL_SHARE * len(out)) and n_gate >= n_unc:
        alt = []
        if W < 12:
            for d in data:
                s = {x["month"]: x for x in d["monthly_volume"] if x.get("month")}
                if dmap.get(d["keyword"], {}).get("rep", d["keyword"]) != d["keyword"]:
                    continue
                if not metrics_at(s, ref, 12, a.gate)["status"]:
                    alt.append(d["keyword"])
        print(f"⚠️ 판정 풀이 작다: {len(live)}/{len(out)}행 [가정 기준 {SMALL_POOL_ABS}행 또는 {SMALL_POOL_SHARE:.0%}]. "
              "비수기 계절 품목이면 전년 같은 3개월이 작아 게이트에 걸린다.")
        if W < 12:
            print(f"  → --window 12로 다시 계산하면 판정 {len(alt)}행 (0크레딧 · 12개월 성장률은 계절을 한 바퀴 덮는다). "
                  f"예: python3 trend_signals.py {a.path} --window 12 -o <파일>")

    print("\n상위 신호  (가속도 단위 %p = 성장률 차이)")
    print(f"  {'점수':>5} {'등급':>3} {'성장률':>8} {'가속도':>8} {'지속':>4} {'확산':>5}  키워드")
    fmt = lambda v, s: format(v, s) if isinstance(v, (int, float)) else "—".rjust(len(format(0, s)))
    for r in out[:15]:
        if not r["grade"]:
            continue
        acc_s = f"{r['accel'] * 100:+7.0f}%p" if r["accel"] != "" else "      —"
        print(f"  {r['signal_score']:>5.2f} {r['grade']:>3} {fmt(r['growth'], '>+8.0%')} "
              f"{acc_s} {r['persistence_months']:>4} "
              f"{fmt(r['diffusion'], '>5.2f')}  {r['keyword']}{'  «잠정»' if r['provisional'] else ''}")
    if issue_only is not None:
        print(f"\n«이슈 신호만 · 검색량 미확인» {len(issue_only)}건 — 등급 없음 [추정 요약 · 검색량 데이터 공백]")
        for r in issue_only[:10]:
            print(f"  {r['keyword']} — {r['reason']}{' · 기준월 이후 이슈' if r['after_ref_month'] else ''}")
        n_ev = sum(1 for r in out if r["issue_score"] != "")
        print(f"이슈 근거가 붙은 신호 행 {n_ev} · → {a.issue_only_out}")
    print(f"\n→ {a.out}")


def join_evidence(a, out, raw, dropped, rules, data_ref, meta):
    """이슈 근거를 신호 행에 붙이고, 검색량으로 판정하지 못한 후보를 사유와 함께 돌려준다(버리지 않는다).

    사유: 범위 밖 제외 · 게이트 미달 · 계산 불가 · 시계열 없음 · 스크린 탈락(volume_avg 측정됨) ·
    같은 검색 병합 · 검색량 미반환. 스크린 결과(meta.screen.rows)가 있으면 시드·사용자 후보도 같은 규칙으로 남긴다.
    """
    ev = list(csv.DictReader(open(a.evidence, encoding="utf-8-sig")))
    by_c = defaultdict(list)
    for r in out:
        by_c[r["canonical"]].append(r)
    status = {r["keyword"]: r["gated_out"] for r in out}
    no_series = {canon(d["keyword"], rules) for d in raw if not d.get("monthly_volume")}
    excl = {canon(k, rules) for k in dropped}
    pat = re.compile(a.exclude) if a.exclude else None
    scr = {}
    for sr in ((meta or {}).get("screen") or {}).get("rows", []):
        for k in [sr["keyword"]] + [x for x in sr.get("response_keywords", "").split(" / ") if x]:
            scr.setdefault(canon(k, rules), sr)

    def classify(kws):
        keys = {canon(k, rules) for k in kws}
        rows = [r for c in keys for r in by_c.get(c, [])]
        judged = any(not r["gated_out"] or (r["gated_out"] == "같은 검색의 중복" and not status.get(r["dup_of"]))
                     for r in rows)
        sr = next((scr[c] for c in keys if c in scr), None)
        if judged:
            return rows, None, sr
        if (keys & excl) or (pat and any(pat.search(k) for k in kws)):
            reason = "범위 밖 제외"
        elif rows:
            reason = next((r["gated_out"] for r in rows if r["gated_out"] != "같은 검색의 중복"),
                          status.get(rows[0]["dup_of"]) or "계산 불가")
        elif keys & no_series:
            reason = "시계열 없음"
        elif sr and sr["screen"] == "탈락":
            reason = "스크린 탈락"
        elif sr and sr["screen"] == "병합":
            reason = f"같은 검색 병합 → {sr['merged_into']}"
        else:
            reason = "검색량 미반환"
        return rows, reason, sr

    only, seen = [], set()
    for e in ev:
        kws = [k.strip() for k in [e["keyword"]] + e.get("variants", "").split(" / ") if k.strip()]
        seen.update(canon(k, rules) for k in kws)
        rows, reason, sr = classify(kws)
        for r in rows:
            r.update({"issue_score": e["issue_score_max"], "issue_n_dates": e["n_index_dates"],
                      "issue_summary_ai": e["summary_ai"], "issue_source_checked": e.get("source_checked", ""),
                      "issue_sources": e["source_links"]})
        if reason is None:
            continue
        only.append({"keyword": e["keyword"], "canonical": e["canonical"], "reason": reason,
                     "issue_score": e["issue_score_max"], "issue_n_dates": e["n_index_dates"],
                     "index_date_last": e["index_date_last"],
                     "after_ref_month": "예" if data_ref and e["index_date_last"][:7] > data_ref else "",
                     "summary_ai": e["summary_ai"], "source_checked": e.get("source_checked", ""),
                     "source_links": e["source_links"], "data_ref_month": data_ref,
                     "volume_avg": sr["volume_avg"] if sr else "", "screen": sr["screen"] if sr else ""})
    # 이슈 근거가 없는 후보(시드·사용자 후보) — 판정 행이 없고 스크린에서 멈춘 것만 남긴다
    for sr in ((meta or {}).get("screen") or {}).get("rows", []):
        c = canon(sr["keyword"], rules)
        if c in seen or by_c.get(c):
            continue
        _, reason, _ = classify([sr["keyword"]] + [x for x in sr.get("response_keywords", "").split(" / ") if x])
        if reason:
            only.append({"keyword": sr["keyword"], "canonical": c, "reason": reason, "issue_score": "",
                         "issue_n_dates": "", "index_date_last": "", "after_ref_month": "", "summary_ai": "",
                         "source_checked": "", "source_links": "", "data_ref_month": data_ref,
                         "volume_avg": sr["volume_avg"], "screen": sr["screen"]})
    a.issue_only_out = a.issue_only_out or str(pathlib.Path(a.out).with_name(pathlib.Path(a.out).stem + "_issue_only.csv"))
    with open(a.issue_only_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=ISSUE_ONLY_COLS); w.writeheader(); w.writerows(only)
    return only


if __name__ == "__main__":
    main()
