#!/usr/bin/env python3
"""국가 간 같은 척도 비교 — 동의어 마스터(concepts.json) 기준으로 kr/us/jp를 나란히 놓는다.

검색량 절대값은 국가 간에 비교할 수 없다. 국가마다 집계되는 검색엔진 구성이 다르고
(kr 응답에는 구글·네이버 분해가 붙고, 분해가 없는 응답도 있다), 인구·검색 습관도 다르다.
그래서 이 스크립트는 **국가 안에서 만든 상대 지표**만 국가 간에 나란히 둔다:

  - index_vs_anchor : 개념 검색량 ÷ 같은 나라 기준어(예: 청소기) 검색량 (_lo·_hi)
  - yoy_3m          : 전년 같은 3개월 대비 성장률 (volume_trend 필드는 쓰지 않는다)
  - season_corr_vs_base : 12개월 월별 비중 프로필의 기준국 대비 상관
  - cpc_index       : 개념 CPC ÷ 같은 나라 기준어 CPC (통화 단위를 상쇄한다)

표기변형 그룹(계약 T5): 같은 canonical(띄어쓰기·전각/반각·`변형치환`)에 걸린 행은 한 그룹이다.
그룹 볼륨은 **하한(그룹 내 최댓값 — 변형이 같은 검색의 중복일 때)과 상한(그룹 내 합 — 서로 다른
검색일 때)**을 함께 낸다. 응답은 어느 쪽인지 알려주지 않고 실측에서 둘 다 관찰됐다. 볼륨·지수·
비중은 `_lo`/`_hi` 쌍이고 [추정]이다. 지수의 하한은 하한 분모로, 상한은 상한 분모로 나눈다.
국가 순위(개념별 index 내림차순)는 하한 기준이며, 상한 순위와 다르면 `index_rank_flip`에 적는다.
번역어·음역어(掃除機 / クリーナー)는 표기변형이 아니라 서로 다른 검색어라 별개 그룹으로 더한다.
yoy_3m·시즌 프로필·CPC·경쟁도·성연령은 그룹 대표 행(volume_total 최대)에서 온다 — 하한 기준이며
상한 시계열은 만들지 않는다(`yoy_basis`).

두 하위 명령:

  select  — 1단(ads_metrics) 응답으로 동의어를 검증하고, 2단(all)에 넣을 키워드만 고른다
      python3 scripts/cross_country.py select --concepts concepts.json \\
          --screen kr=screen_kr.json --screen us=screen_us.json --gate 1200 -o stage2

  compare — 2단(all) 응답으로 개념 × 국가 비교표를 만든다
      python3 scripts/cross_country.py compare --concepts concepts.json \\
          --metrics kr=all_kr.json --metrics us=all_us.json \\
          --queried-at 2026-09-28 --yoy-gate 3000 -o cross_country.csv

입력은 keyword_info 응답 JSON 그대로 ({"data": [...]} 또는 [...]).
API를 부르지 않는다.
"""
import argparse, csv, json, math, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon as _canon, dedup_map  # noqa: E402  같은 검색의 중복 판정 (계약 T5 ①)

ANCHOR = "__anchor__"
EXCLUDED = "__excluded__"
UNMAPPED = "__unmapped__"


# ── 공통 ──────────────────────────────────────────────────────────────
def load(path):
    o = json.load(open(path, encoding="utf-8"))
    return o["data"] if isinstance(o, dict) and "data" in o else o


def pairs(specs, flag):
    out = {}
    for s in specs:
        if "=" not in s:
            sys.exit(f"{flag}는 국가=파일 형식이다: {s}")
        gl, path = s.split("=", 1)
        out[gl.strip().lower()] = path
    return out


def canon(kw, subs):
    """NFKC(전각·반각 가나 통일) → 소문자 → 국가별 치환(띄어쓰기를 지우기 전 — 단어 경계 보존) → 공백 제거."""
    return _canon(kw, subs)


class Master:
    """concepts.json — 국가별 동의어·기준어·제외어·치환 규칙."""

    def __init__(self, path):
        cfg = json.load(open(path, encoding="utf-8"))
        self.category = cfg.get("category", "category")
        self.rules = cfg.get("rules", {})
        self.concepts = cfg["concepts"]
        self.anchor = cfg.get("anchor", {})
        self.exclude = cfg.get("exclude", {})

    def subs(self, gl):
        return self.rules.get(gl, {}).get("변형치환", {})

    def raw_terms(self, gl):
        """마스터에 적힌 요청 원문 목록(순서 유지 · 같은 글자 중복 제거) — 1단 과금 단위다.

        canonical 수가 아니다: «선크림»·«썬크림»처럼 같은 canonical의 표기변형을 둘 다 적으면 둘 다 요청·과금된다
        (2026-09-30 선크림 실측: canonical 17 · 실제 요청 19)."""
        out = [(t, ANCHOR, "기준어") for t in self.anchor.get(gl, [])]
        for c in self.concepts:
            for t in c.get("terms", {}).get(gl, []):
                kw, mt = (t["kw"], t.get("match", "")) if isinstance(t, dict) else (t, "")
                out.append((kw, c["id"], mt))
        seen, uniq = set(), []
        for kw, cid, mt in out:
            if kw not in seen:
                seen.add(kw); uniq.append((kw, cid, mt))
        return uniq

    def index(self, gl):
        """canonical → (concept_id, 요청 원문, match_type). 같은 canonical이 여럿이면 **먼저 적은** 원문 —
        기준어·개념 모두 같은 규칙이다(기준어가 개념보다 먼저). 예전에는 기준어만 마지막 원문을 골라
        2단 요청어가 마스터 순서에 따라 달라졌다."""
        sub, idx = self.subs(gl), {}
        for kw, cid, mt in self.raw_terms(gl):
            idx.setdefault(canon(kw, sub), (cid, kw, mt))
        return idx

    def originals(self, gl):
        """canonical → 그 canonical에 걸리는 요청 원문 목록(마스터 순서)."""
        sub, out = self.subs(gl), {}
        for kw, _, _ in self.raw_terms(gl):
            out.setdefault(canon(kw, sub), []).append(kw)
        return out

    def classify(self, gl, kw):
        c = canon(kw, self.subs(gl))
        if any(canon(x, self.subs(gl)) in c for x in self.exclude.get(gl, [])):
            return c, (EXCLUDED, "", "")
        return c, self.index(gl).get(c, (UNMAPPED, "", ""))

    def label(self, cid):
        if cid == ANCHOR:
            return "기준어"
        return next((c.get("label", cid) for c in self.concepts if c["id"] == cid), cid)

    def requested(self, gl):
        return {canon(k, self.subs(gl)) for k, _, _ in self.raw_terms(gl)}


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def window(end, size):
    return [shift(end, i) for i in range(size - 1, -1, -1)]


def series(row):
    """month → total 또는 None(결측). nv_code:1 월은 total 키가 없다 — 0으로 두지 않는다."""
    return {r["month"]: r.get("total") for r in (row.get("monthly_volume") or [])}


def ref_month(rows):
    ms = [r["month"] for d in rows for r in (d.get("monthly_volume") or [])]
    return max(ms) if ms else None   # 배열 순서를 가정하지 않는다


def pearson(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x)); sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


# ── select ────────────────────────────────────────────────────────────
def cmd_select(a):
    m = Master(a.concepts)
    out_dir = pathlib.Path(a.out); out_dir.mkdir(parents=True, exist_ok=True)
    report, total_p, total_n, total_c, skipped, lost_forms = [], 0, 0, 0, [], []
    for gl, path in pairs(a.screen, "--screen").items():
        req = m.requested(gl)
        origs = m.originals(gl)
        rows = load(path)
        # 응답에 실제로 있는 표기(띄어쓰기·전각/반각·대소문자만 무시 — 변형치환은 적용하지 않는다)
        got_forms = {canon(d["keyword"], {}) for d in rows}
        chosen = []
        for d in rows:
            kw = d["keyword"]; am = d.get("ads_metrics") or {}
            c, (cid, _, mt) = m.classify(gl, kw)
            vol = am.get("volume_total")
            if cid in (EXCLUDED, UNMAPPED):
                passed, why = False, "제외어" if cid == EXCLUDED else "마스터에 없음(자동 반환 변형)"
            elif cid == ANCHOR:
                passed, why = True, "기준어는 항상 2단"
            elif vol is None:
                passed, why = False, "볼륨 미반환"
            elif vol < a.gate:
                passed, why = False, f"게이트 {a.gate:,} 미만"
            else:
                passed, why = True, ""
            if passed:
                # 2단에는 마스터의 요청 원문만 넣는다 — 자동 반환된 표기변형까지 넣으면
                # 같은 수요에 10크레딧을 두 번 낸다(T5). 변형은 2단 응답에서 다시 함께 돌아온다.
                # 같은 canonical 원문이 여럿이면 **1단 응답에 실제로 있는** 원문 중 마스터에 먼저 적은 것을 쓴다
                # (2026-09-30: 1단 응답에 없던 «日やけ止め»가 2단 요청어로 뽑혔다). 원문이 하나도 응답에 없으면 첫 원문.
                cands = origs.get(c, [m.index(gl)[c][1]])
                orig = next((k for k in cands if canon(k, {}) in got_forms), cands[0])
                if not any(k in chosen for k in cands):
                    chosen.append(orig)
            report.append({"country": gl, "concept_id": cid, "keyword": kw, "canonical": c,
                           "match_type": mt, "requested": c in req, "volume_total": vol,
                           "passed_gate": passed, "reason": why})
        # 요청했는데 응답에 아예 없는 동의어 — 그 나라에 해당 표현이 거의 없다는 신호
        got = {r["canonical"] for r in report if r["country"] == gl}
        for c in sorted(req - got):
            cid, kw, mt = m.index(gl)[c]
            report.append({"country": gl, "concept_id": cid, "keyword": kw, "canonical": c,
                           "match_type": mt, "requested": True, "volume_total": "",
                           "passed_gate": False, "reason": "응답에 없음"})
        # 요청한 표기가 응답에 없는데 같은 canonical의 다른 표기만 돌아온 경우 — 그룹은 살아 있지만
        # 그 표기의 볼륨은 [데이터 공백]이다(0이 아니다). 표기변형 하한·상한에 들어가지 않는다
        for kw, cid, mt in m.raw_terms(gl):
            c = canon(kw, m.subs(gl))
            if c in got and canon(kw, {}) not in got_forms:
                report.append({"country": gl, "concept_id": cid, "keyword": kw, "canonical": c,
                               "match_type": mt, "requested": True, "volume_total": "",
                               "passed_gate": False, "reason": "요청 표기 미반환(같은 그룹의 다른 표기만 반환)"})
                lost_forms.append(f"{gl}/{kw}")
        # 개념 동의어가 하나도 통과하지 못하면 기준어만으로 2단을 부르지 않는다 — 비교할 개념이 없다
        anchors = {m.index(gl)[c][1] for c in req if m.index(gl)[c][0] == ANCHOR}
        if chosen and all(k in anchors for k in chosen):
            chosen = []
            skipped.append(gl)
            for r in report:
                if r["country"] == gl and r["concept_id"] == ANCHOR:
                    r["passed_gate"], r["reason"] = False, "개념 동의어 전멸 — 2단 생략"
        (out_dir / f"stage2_{gl}.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=1),
                                                   encoding="utf-8")
        total_p += len(chosen); total_n += len(m.raw_terms(gl)); total_c += len(req)
        if gl in skipped:
            print(f"[{gl}] 개념 동의어가 모두 게이트 미달·미반환 — 2단 생략(기준어만 조회하지 않는다) · "
                  f"compare에 --skipped {gl}로 넘겨 [데이터 공백] 행을 남긴다")
        else:
            print(f"[{gl}] 2단 대상 {len(chosen)}개 → all 예상 {len(chosen) * 10:,}크레딧 (10 × {len(chosen)})")
    with open(out_dir / "screen_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(report[0].keys())); w.writeheader(); w.writerows(report)
    # 표현 부재 후보: 통과한 그룹(같은 canonical)의 표기변형은 세지 않는다
    passed_c = {(r["country"], r["canonical"]) for r in report if r["passed_gate"]}
    gaps = [r for r in report if r["requested"] and not r["passed_gate"] and r["concept_id"] != ANCHOR
            and (r["country"], r["canonical"]) not in passed_c]
    print(f"합계 2단 {total_p}개 → {total_p * 10:,}크레딧 · 탈락 동의어 {len(gaps)}개 "
          f"(국가별 표현 부재 후보 — [데이터 공백]으로 보고)")
    print(f"1단 요청 N = {total_n}개(마스터 원문 · 과금 단위) · canonical {total_c}개"
          + (f" — 표기변형 원문 {total_n - total_c}개를 함께 요청했다" if total_n != total_c else ""))
    if lost_forms:
        print(f"⚠️ 요청한 표기가 응답에 없다(같은 그룹의 다른 표기만 반환) {len(lost_forms)}개 [데이터 공백]: "
              + ", ".join(lost_forms) + " — 그 표기의 볼륨은 0이 아니라 미확인이다. 2단 요청어는 응답에 있는 표기로 골랐다")
    two, one = total_n + 10 * total_p, 10 * total_n
    print(f"손익분기: 2단 방식 1×N + 10×P = {total_n} + {10 * total_p:,} = {two:,} vs 전량 all 10×N = {one:,} "
          f"— 2단이 싼 조건은 P < 0.9N (이번 P/N = {total_p / total_n:.0%})" if total_n else "")
    if skipped:
        print(f"2단 생략 국가: {', '.join(skipped)} [데이터 공백]")
    print(f"→ {out_dir}/stage2_<gl>.json · {out_dir}/screen_report.csv")


# ── compare ───────────────────────────────────────────────────────────
def cmd_compare(a):
    m = Master(a.concepts)
    files = pairs(a.metrics, "--metrics")
    base = a.base or next(iter(files))
    data = {gl: [d for d in load(p)] for gl, p in files.items()}

    # T1: 국가별 기준월이 다를 수 있다 — 공통 창으로 맞춘다
    refs = {gl: ref_month(rows) for gl, rows in data.items()}
    if not all(refs.values()):
        sys.exit(f"monthly_volume이 없는 국가가 있다: {refs} — 2단은 data_type=all로 조회할 것")
    common = min(refs.values())
    end = common if a.include_last_month else shift(common, 1)
    qy, qm = int(a.queried_at[:4]), int(a.queried_at[5:7])
    print(f"조회일 {a.queried_at} · 비교 창 끝 {end} "
          f"({'마지막 달 포함' if a.include_last_month else '마지막 달 제외(기본)'})")
    for gl, r in refs.items():
        lag = (qy - int(r[:4])) * 12 + (qm - int(r[5:7]))
        print(f"  [{gl}] 데이터 기준월 {r} / 조회일 {a.queried_at} / 지연 {lag}개월")
    if len(set(refs.values())) > 1:
        print(f"  ⚠️ 국가별 기준월이 다르다 {refs} — 가장 이른 {common}에 맞춰 비교한다")

    cur = window(end, 3)
    prev = [shift(x, 12) for x in cur]
    prof = window(end, 12)

    def win_sum(d):
        """공통 12개월 창 합 — 결측 월이 있으면 None."""
        s = series(d)
        vals = [s.get(x) for x in prof]
        return None if any(v is None for v in vals) else sum(vals)

    kw_rows, agg, cross_dups = [], {}, []
    for gl, rows in data.items():
        # T5 ①: 시계열(또는 지표)이 완전히 같은 행은 같은 검색의 중복 — canonical이 달라도 한 번만 센다.
        # 묶음에서 마스터에 매핑된 행 하나만 센다(대표가 매핑돼 있으면 대표).
        dm = dedup_map(rows, m.subs(gl))
        cls = {d["keyword"]: m.classify(gl, d["keyword"]) for d in rows}
        mapped_kw = {k for k, (_, (cid, _, _)) in cls.items() if cid not in (EXCLUDED, UNMAPPED)}
        clusters = {}
        for k, v in dm.items():
            clusters.setdefault(v["rep"], []).append(k)
        dup_of = {}
        for rep_kw, ks in clusters.items():
            if len(ks) < 2:
                continue
            mk = sorted((k for k in ks if k in mapped_kw), key=lambda k: (k != rep_kw, len(k), k))
            for k in mk[1:]:
                dup_of[k] = mk[0]
                if cls[k][1][0] != cls[mk[0]][1][0]:
                    cross_dups.append(f"[{gl}] {k}({cls[k][1][0]}) = {mk[0]}({cls[mk[0]][1][0]})")
        # T5 ②: 같은 canonical 그룹 — 대표 1행(volume_total 최대)은 성장·시즌·CPC에 쓰고,
        # 볼륨은 하한(그룹 내 최댓값)·상한(그룹 내 합)을 함께 낸다. 행은 모두 남긴다
        groups = {}
        for d in rows:
            c, (cid, _, mt) = cls[d["keyword"]]
            groups.setdefault((cid, c), []).append((d, mt))
        for (cid, c), all_members in groups.items():
            mapped = cid not in (EXCLUDED, UNMAPPED)
            members = [t for t in all_members if t[0]["keyword"] not in dup_of] or all_members[:0]
            rep = max(members, key=lambda t: (t[0].get("ads_metrics") or {}).get("volume_total") or 0) \
                if members else (None,)
            for d, mt in all_members:
                am = d.get("ads_metrics") or {}
                kw_rows.append({"country": gl, "concept_id": cid, "keyword": d["keyword"],
                                "canonical": c, "match_type": mt, "group_size": len(all_members),
                                "volume_total": am.get("volume_total"),
                                "representative": mapped and d is rep[0],
                                "used_in_hi": mapped and d["keyword"] not in dup_of,
                                "dup_of": dup_of.get(d["keyword"], ""),
                                "dup_basis": dm.get(d["keyword"], {}).get("basis", "") if d["keyword"] in dup_of else ""})
            if not mapped or not members:
                continue
            ws = [win_sum(d) for d, _ in members]
            rep_ws = win_sum(rep[0])
            # 표기변형끼리 전년 대비 부호가 갈리면 yoy가 대표 선택(12개월 최대)에 달린다 — 경고만 하고 고르지 않는다
            # (2026-09-30 kr: 썬크림 +17.5% / 선크림 −10.9% — 대표는 12개월 합이 조금 큰 썬크림)
            vy = []
            for d, _ in members:
                sd = series(d)
                a3 = [sd.get(x) for x in cur]; b3 = [sd.get(x) for x in prev]
                if None in a3 or None in b3 or sum(b3) < a.yoy_gate:
                    continue
                vy.append((d["keyword"], sum(a3) / sum(b3) - 1))
            split_yoy = (" / ".join(f"{k} {y:+.1%}" for k, y in vy)
                         if len(vy) > 1 and min(y for _, y in vy) < 0 < max(y for _, y in vy) else "")
            lo = None if rep_ws is None else max(w for w in ws if w is not None)
            hi = None if any(w is None for w in ws) else sum(ws)
            ams = [d.get("ads_metrics") or {} for d, _ in members]
            agg.setdefault((gl, cid), []).append({
                "rep": rep[0], "lo": lo, "hi": hi, "multi": len(members) > 1, "split_yoy": split_yoy,
                "gg_hi": sum(x.get("gg_volume_total") or 0 for x in ams),
                "nv_hi": sum(x.get("nv_volume_total") or 0 for x in ams)})

    def summarize(gl, cid, grp):
        reps = [g["rep"] for g in grp]
        mv = [series(d) for d in reps]

        def wsum(months):
            tot = miss = 0
            for s in mv:
                for x in months:
                    v = s.get(x)
                    if v is None:
                        miss += 1
                    else:
                        tot += v
            return tot, miss
        a3, m1 = wsum(cur); b3, m2 = wsum(prev)
        if m1 + m2:
            yoy, status = "", "계산 불가(결측 월)"
        elif b3 < a.yoy_gate:
            yoy, status = "", f"게이트 미달(전년 창 {b3:,} < {a.yoy_gate:,})"
        else:
            yoy, status = round(a3 / b3 - 1, 4), "ok"
        p, pm = [], 0
        for x in prof:
            t, mm = wsum([x]); p.append(t); pm += mm
        profile = None if pm or not sum(p) else [v / sum(p) for v in p]
        # 12개월 규모도 공통 창에서 monthly_volume으로 다시 합산한다. ads_metrics.volume_total은
        # 국가마다 자기 기준월에서 끝나므로 기준월이 다르면 창이 어긋난다. 결측이 있으면 계산 불가.
        # 하한 = 그룹별 최댓값의 합 · 상한 = 그룹별 합의 합 (그룹끼리는 서로 다른 검색어라 더한다)
        vol_lo = "" if any(g["lo"] is None for g in grp) else sum(g["lo"] for g in grp)
        vol_hi = "" if any(g["hi"] is None for g in grp) else sum(g["hi"] for g in grp)
        gg_hi = sum(g["gg_hi"] for g in grp); nv_hi = sum(g["nv_hi"] for g in grp)
        wcpc = [((d.get("ads_metrics") or {}).get("cpc"), (d.get("ads_metrics") or {}).get("volume_total") or 0)
                for d in reps]
        wcpc = [(c, v) for c, v in wcpc if c is not None and v]
        cpc = sum(c * v for c, v in wcpc) / sum(v for _, v in wcpc) if wcpc else None
        wci = [((d.get("ads_metrics") or {}).get("competition_index"), (d.get("ads_metrics") or {}).get("volume_total") or 0)
               for d in reps]
        wci = [(c, v) for c, v in wci if c is not None and v]
        ci = round(sum(c * v for c, v in wci) / sum(v for _, v in wci), 1) if wci else ""
        gg = sum((d.get("ads_metrics") or {}).get("gg_volume_total") or 0 for d in reps)
        nv = sum((d.get("ads_metrics") or {}).get("nv_volume_total") or 0 for d in reps)
        split = "gg+nv" if nv else ("gg" if gg else "total만(엔진 분해 미반환)")
        demo = [(d.get("demography"), (d.get("ads_metrics") or {}).get("volume_total") or 0)
                for d in reps if d.get("demography")]
        if demo:
            fw = [(x.get("f_gender_ratio"), v) for x, v in demo if x.get("f_gender_ratio") is not None and v]
            f_ratio = round(sum(r * v for r, v in fw) / sum(v for _, v in fw), 1) if fw else ""
            bands = {}
            for x, v in demo:
                for k, r in x.items():
                    if k.startswith("a") and k.endswith("_ratio") and r is not None:
                        bands[k[:-6]] = bands.get(k[:-6], 0) + r * v
            top = max(bands, key=bands.get) if bands else ""
            demo_status = "반환"
        else:
            f_ratio, top = "", ""
            demo_status = "반환" if gl == "kr" else "미반환(gl 사양 — 공백 아님)"
            if gl == "kr":
                demo_status = "미반환(kr인데 없음 — 데이터 공백)"
        peak = prof[p.index(max(p))][5:7] if profile else ""
        # 버킷 반올림(P1-3): 엔진 분해가 없는 응답(us·jp)은 구글 버킷 값이라 전년 대비가 계단식이다
        warn = []
        if split != "gg+nv":
            warn.append("구글 버킷 반올림 값(엔진 분해 없음)")
        vals = [s_.get(x) for s_ in mv for x in cur + prev]
        if vals and None not in vals and len(set(vals)) == 1:
            warn.append("비교 창 월값 모두 동일(평탄)")
        if yoy == 0.0 and yoy != "":
            warn.append("전년 대비 정확히 0.0%(같은 버킷)")
        vsplit = " · ".join(g["split_yoy"] for g in grp if g["split_yoy"])
        if vsplit:
            warn.append("표기변형 간 추세 부호 갈림(대표 선택에 따라 yoy 부호가 바뀜)")
        # 비수기 창(P1-4): 비교 창 3개월이 12개월 볼륨에서 차지하는 비중
        wshare = round(sum(p[-3:]) / sum(p), 3) if profile else ""
        off = "비수기 창" if wshare != "" and wshare < a.offseason_share else ""
        return {"vol_lo": vol_lo, "vol_hi": vol_hi, "multi": sum(1 for g in grp if g["multi"]), "cpc": cpc, "profile": profile, "yoy": yoy, "status": status,
                "prev": b3, "miss": m1 + m2, "miss12": pm, "ci": ci, "split": split,
                "nv_share_lo": round(nv / (gg + nv), 3) if (gg + nv) and nv else "",
                "nv_share_hi": round(nv_hi / (gg_hi + nv_hi), 3) if (gg_hi + nv_hi) and nv_hi else "",
                "f_ratio": f_ratio, "top": top, "demo": demo_status, "peak": peak,
                "peak_share": round(max(profile), 3) if profile else "", "n": len(reps),
                "label": ("[추정]" if warn else "[실제 데이터]") if yoy != "" else "",
                "warn": " · ".join(warn) if yoy != "" else "", "wshare": wshare, "off": off, "vsplit": vsplit}

    S = {k: summarize(k[0], k[1], v) for k, v in agg.items()}

    def reading(cid, idx):
        """지수 > 1 이면 기준어가 개념을 포괄하지 않는다 — 비중이 아니라 배율로 읽는다 (P1-5)."""
        if cid == ANCHOR:
            return "기준어"
        if idx == "":
            return ""
        return "배율(지수>1 — 기준어가 개념을 포괄하지 않음)" if idx > 1 else "비중(≤1)"
    out = []
    for (gl, cid), s in sorted(S.items(), key=lambda kv: (kv[0][1] != ANCHOR, kv[0][1], kv[0][0])):
        anc = S.get((gl, ANCHOR))

        def ratio(k):   # 하한은 하한 분모로, 상한은 상한 분모로
            return round(s[k] / anc[k], 4) if anc and anc[k] and s[k] != "" else ""
        cpc_idx = round(s["cpc"] / anc["cpc"], 3) if anc and s["cpc"] and anc["cpc"] else ""
        b = S.get((base, cid))
        corr = ""
        if b and b["profile"] and s["profile"]:
            r = pearson(s["profile"], b["profile"])
            corr = round(r, 3) if r is not None else ""
        out.append({
            "country": gl, "concept_id": cid, "concept_label": m.label(cid),
            "groups_used": s["n"], "multi_variant_groups": s["multi"],
            "volume_12m_lo": s["vol_lo"], "volume_12m_hi": s["vol_hi"],
            "anchor_volume_12m_lo": anc["vol_lo"] if anc else "",
            "anchor_volume_12m_hi": anc["vol_hi"] if anc else "",
            "index_vs_anchor_lo": ratio("vol_lo"), "index_vs_anchor_hi": ratio("vol_hi"),
            "index_reading": reading(cid, ratio("vol_lo")),
            "index_rank_lo": "", "index_rank_hi": "", "index_rank_flip": "",
            "yoy_3m": s["yoy"], "yoy_basis": "그룹 대표 행(하한 기준)", "yoy_status": s["status"],
            "yoy_label": s["label"], "bucket_warning": s["warn"], "variant_yoy_split": s["vsplit"],
            "window_share_12m": s["wshare"], "offseason_flag": s["off"],
            "prev_year_window_volume": s["prev"], "missing_in_window": s["miss"],
            "missing_in_12m": s["miss12"],
            "peak_month": s["peak"], "peak_share": s["peak_share"],
            "base_country": base, "season_corr_vs_base": corr,
            "cpc_index": cpc_idx, "competition_index_avg": s["ci"],
            "engine_split": s["split"], "nv_share_lo": s["nv_share_lo"], "nv_share_hi": s["nv_share_hi"],
            "demography_status": s["demo"], "f_gender_ratio": s["f_ratio"], "top_age_band": s["top"],
            "data_ref_month": refs[gl], "window_end": end,
        })
    # 동의어가 한 나라에서 하나도 안 잡힌 개념 — 행을 남겨 [데이터 공백]임을 보인다
    for c in m.concepts:
        for gl in data:
            if (gl, c["id"]) not in S:
                out.append({k: "" for k in out[0]} | {
                    "country": gl, "concept_id": c["id"], "concept_label": m.label(c["id"]),
                    "groups_used": 0, "multi_variant_groups": 0,
                    "yoy_status": "데이터 공백(해당 국가 동의어 미반환)",
                    "base_country": base, "data_ref_month": refs[gl], "window_end": end})
        for gl in (a.skipped or []):   # select가 2단을 생략한 국가 — 개념 동의어 전멸
            out.append({k: "" for k in out[0]} | {
                "country": gl, "concept_id": c["id"], "concept_label": m.label(c["id"]),
                "groups_used": 0, "multi_variant_groups": 0,
                "yoy_status": "데이터 공백(1단에서 개념 동의어 전멸 — 2단 생략)",
                "base_country": base, "window_end": end})

    # 개념별 국가 순위 — 하한 지수 기준. 상한 지수 순위와 다르면 뒤집힘을 적는다(한쪽을 고르지 않는다)
    flips = []
    for cid in dict.fromkeys(r["concept_id"] for r in out if r["concept_id"] != ANCHOR):
        rs = [r for r in out if r["concept_id"] == cid
              and r["index_vs_anchor_lo"] != "" and r["index_vs_anchor_hi"] != ""]
        for k, col in (("index_vs_anchor_lo", "index_rank_lo"), ("index_vs_anchor_hi", "index_rank_hi")):
            vals = sorted({r[k] for r in rs}, reverse=True)
            for r in rs:
                r[col] = vals.index(r[k]) + 1
        for r in rs:
            if r["index_rank_lo"] != r["index_rank_hi"]:
                r["index_rank_flip"] = "뒤집힘"
                flips.append(f"{cid}/{r['country']} {r['index_rank_lo']}→{r['index_rank_hi']}")

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    kpath = a.keywords_out or str(pathlib.Path(a.out).with_name(pathlib.Path(a.out).stem + "_keywords.csv"))
    with open(kpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(kw_rows[0].keys())); w.writeheader(); w.writerows(kw_rows)

    dup = sum(1 for r in kw_rows if r["group_size"] > 1 and not r["representative"] and r["used_in_hi"])
    unm = sum(1 for r in kw_rows if r["concept_id"] == UNMAPPED)
    multi = sum(r["multi_variant_groups"] or 0 for r in out)
    print(f"비교 창 {cur[0]}~{cur[-1]} vs {prev[0]}~{prev[-1]} · 시즌 프로필 {prof[0]}~{prof[-1]}")
    print(f"표기변형 그룹 {multi}개(변형 행 {dup}개) — 볼륨·지수는 하한(그룹 최댓값) ~ 상한(그룹 합) [추정] · "
          f"마스터 밖 반환 {unm}행(합산 안 함)")
    for r in out:
        if r["concept_id"] != ANCHOR and r["index_vs_anchor_lo"] != "" and r["multi_variant_groups"]:
            print(f"  [{r['country']}] {r['concept_id']} 지수 하한 {r['index_vs_anchor_lo']} ~ 상한 {r['index_vs_anchor_hi']}")
    if flips:
        print(f"⚠️ 상한 기준에서 국가 순위가 뒤집힌다 — {' · '.join(flips)} (순위는 하한 기준 · 한쪽으로 단정하지 않는다)")
    print("yoy_3m·시즌·CPC·성연령은 그룹 대표 행 기준(하한 기준) — 상한 시계열은 만들지 않는다")
    ndup = sum(1 for r in kw_rows if r["dup_of"])
    print(f"같은 검색의 중복(시계열·지표 일치) {ndup}행은 한 번만 셌다 [추정]")
    if cross_dups:
        print(f"⚠️ 서로 다른 개념·기준어에 같은 검색이 걸렸다 — {' · '.join(cross_dups)} (앞 개념에만 셈)")
    bk = [f"{r['country']}/{r['concept_id']}" for r in out if r.get("bucket_warning")]
    if bk:
        print(f"⚠️ 버킷 반올림·평탄 경고 {len(bk)}칸 — yoy_3m은 [추정]: {', '.join(bk)}")
    vs = [f"{r['country']}/{r['concept_id']}({r['variant_yoy_split']})" for r in out if r.get("variant_yoy_split")]
    if vs:
        print(f"⚠️ 표기변형끼리 전년 대비 부호가 갈린다 — yoy_3m 부호가 대표 행 선택(12개월 최대)에 달려 있다 [추정]: "
              + ", ".join(vs) + " — 성장 방향을 단정하지 말고 변형별 값을 함께 적는다")
    offs = [f"{r['country']}/{r['concept_id']} {r['window_share_12m']:.0%}" for r in out if r.get("offseason_flag")]
    if offs:
        print(f"⚠️ 비수기 창(비교 3개월이 연 볼륨의 {a.offseason_share:.0%} 미만) — 성장률로 국가를 줄 세우지 않는다: "
              + ", ".join(offs))
    mult = [f"{r['country']}/{r['concept_id']} {r['index_vs_anchor_lo']}" for r in out
            if str(r.get("index_reading", "")).startswith("배율")]
    if mult:
        print(f"⚠️ 지수 > 1 — 기준어가 개념을 포괄하지 않는다(비중이 아니라 배율): {', '.join(mult)}")
    print("국가 순위는 기준어 선택에 의존한다 [가정] — 의심되면 기준어를 바꿔 다시 돌린다")
    print(f"계산 불가(결측) {sum(1 for r in out if str(r['yoy_status']).startswith('계산 불가'))} · "
          f"게이트 미달 {sum(1 for r in out if str(r['yoy_status']).startswith('게이트'))} · "
          f"데이터 공백 {sum(1 for r in out if str(r['yoy_status']).startswith('데이터 공백'))}")
    print("volume_trend 필드는 읽지 않는다 — 성장은 전년 동기 비교(yoy_3m)만 쓴다")
    print(f"→ {a.out} · {kpath}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("select", help="1단 응답으로 2단 대상 선별")
    s.add_argument("--concepts", required=True)
    s.add_argument("--screen", action="append", required=True, help="국가=ads_metrics 응답.json")
    s.add_argument("--gate", type=int, default=1200, help="2단 진입 최소 volume_total(12개월 합, 기본 1200)")
    s.add_argument("-o", "--out", default="stage2")
    c = sp.add_parser("compare", help="2단 응답으로 국가 비교표 작성")
    c.add_argument("--concepts", required=True)
    c.add_argument("--metrics", action="append", required=True, help="국가=all 응답.json")
    c.add_argument("--base", help="시즌 상관의 기준국 (기본: 첫 --metrics)")
    c.add_argument("--queried-at", required=True, help="조회일 YYYY-MM-DD")
    c.add_argument("--yoy-gate", type=int, default=3000, help="전년 같은 3개월 합 최소값 (기본 3000)")
    c.add_argument("--include-last-month", action="store_true", help="마지막 달 포함(기본 제외)")
    c.add_argument("--offseason-share", type=float, default=0.18,
                   help="비교 창 3개월의 연 볼륨 비중이 이 미만이면 비수기 창 (기본 0.18 — 균등 25%%의 약 70%%) [가정]")
    c.add_argument("--skipped", action="append", default=[], help="select가 2단을 생략한 국가 (데이터 공백 행)")
    c.add_argument("-o", "--out", default="cross_country.csv")
    c.add_argument("--keywords-out", help="키워드 단위 CSV (기본: <out>_keywords.csv)")
    a = ap.parse_args()
    (cmd_select if a.cmd == "select" else cmd_compare)(a)


if __name__ == "__main__":
    main()
