#!/usr/bin/env python3
"""숨은 하위 시장 판정 — cluster_finder 군집을 하위 시장 후보로 집계한다 (SKILL.md STEP 0·2·5·7).

cluster_finder는 군집(communities)과 관계(rels)만 준다. 볼륨도, 허브 표시도, 군집 이름도 없다.
그래서 이 스크립트가 하는 일은 세 가지다.

  budget 호출 전 예산 상한 — 보수 상한과 관측 기반 상한 [가정]을 함께 낸다.
  nodes  응답에서 고유 키워드를 뽑고, 관계 수가 limit에 붙었는지(절단)·한 허브에 몰렸는지(별 모양
         퇴화) 검사한다. 고유 키워드 수는 곧 이 호출의 과금 단위이자 다음 keyword_info 호출의 입력이다.
  score  build_universe.py 산출(키워드별 볼륨·브랜드·테마·canonical)을 군집 단위로 합쳐
         하위 시장 유형을 판정하고, 2단 조회(all) 대상을 고른다.

사용법:
  python3 submarkets.py budget --limit 500
  python3 submarkets.py nodes cluster.json --limit 500 --seed 캠핑의자 -o nodes.json
  python3 submarkets.py score cluster.json --universe universe.csv --seed 캠핑의자 --limit 500 \\
      --seed-all seed_all.json -o submarkets.csv --keywords-out submarket_keywords.csv \\
      --pick-out stage2_keywords.json
  python3 submarkets.py score ... --seed-all seed_all.json --trend trend.csv   # STEP 7 (대상 재선정 없음)

rels 전처리: 응답에는 같은 쌍이 방향만 바꿔 두 번 들어 있는 경우가 있다(실측 3건 중 2건에서 1쌍).
degree·허브·시드 거리는 **무방향 고유 간선**으로 계산한다. 절단 판정은 반환된 행 수 그대로 limit과 비교한다.

표기변형 그룹 볼륨(계약 T5): ① 같은 검색의 중복(universe.csv의 dup_of — 시계열·지표 일치)은 canonical이
달라도 한 번만 센다. ② 나머지 canonical 그룹은 하한(그룹 내 최댓값)과 상한(그룹 내 합)을 둘 다 낸다
[추정 — 규칙 기반 집계]. 판정·순위는 하한 기준이고, 상한으로 바꾸면 판정이 달라지는 군집은 verdict_flip,
같은 유형 안의 논브랜드 순위가 달라지는 군집은 rank_flip에 «예»로 표시한다.

판정 규칙(모두 [가정] — 리포트에 값을 함께 적는다). 위에서부터 처음 걸리는 것 하나:
  시드 코어      시드 키워드가 속한 군집
  지표 부족      볼륨이 없는 키워드가 군집의 절반 이상
  브랜드 지배    논브랜드 볼륨 비중이 --brand-max 미만  (크기와 무관 — 한 키워드짜리 브랜드 군집도 여기)
  소형·단일      canonical 그룹이 --min-groups 미만
  규모 미달      논브랜드 볼륨(하한)이 --size-gate 미만
  인접 하위 시장        위를 통과하고 시드와 직접 연결(거리 1)
  숨은 하위 시장 후보   위를 통과하고 시드까지의 최단 거리가 2 이상(또는 연결 없음)
brand_dominated 컬럼은 유형과 별개로 모든 군집에 붙는다 — 시드 코어가 브랜드 지배여도 드러난다.
"""
import argparse, csv, json, statistics, sys, unicodedata
from collections import Counter, defaultdict, deque

TYPE_ORDER = ["숨은 하위 시장 후보", "인접 하위 시장", "시드 코어", "규모 미달",
              "소형·단일", "브랜드 지배", "지표 부족"]
COLUMNS = ["community_id", "submarket_type", "hub_keyword", "hub_degree", "n_keywords",
           "n_canonical", "seed_distance", "seed_links", "volume_lo", "volume_hi",
           "nonbrand_volume_lo", "nonbrand_volume_hi", "nonbrand_share_lo", "nonbrand_share_hi",
           "brand_dominated",
           "submarket_type_hi", "verdict_flip", "rank_in_type_lo", "rank_in_type_hi", "rank_flip",
           "theme_top", "theme_top_share_lo", "theme_top_share_hi", "top_keywords", "missing_metrics",
           "dup_rows", "trend_status",
           "yoy_ex_last_median", "yoy_n", "yoy_gated_n", "yoy_uncomputable_n", "last_month_flag_n",
           "data_ref_month", "truncated", "star_degenerate"]
KW_COLUMNS = ["keyword", "canonical", "community_id", "degree", "volume_total", "brand",
              "theme_primary", "dup_of", "is_group_rep", "group_volume_lo", "group_volume_hi"]

STAR_SHARE = 0.8        # [가정] 한 노드가 고유 간선의 이 비율 이상에 닿으면 별 모양 퇴화
TIGHT_OBS = "2026-09-28 관측 4건 모두 노드 ≤ limit+1 (예: limit 50 → 노드 50·44·50)"


def basic(kw):
    return unicodedata.normalize("NFKC", kw).lower().replace(" ", "")


def load_cluster(path):
    """(communities, 무방향 고유 간선, 반환 행 수, 역방향 중복 수, cost)."""
    o = json.load(open(path, encoding="utf-8"))
    d = o.get("data", o) if isinstance(o, dict) else {}
    comms = d.get("communities") or {}
    raw = [tuple(r) for r in (d.get("rels") or []) if len(r) == 2]
    seen, rels = set(), []
    for a, b in raw:
        key = frozenset((a, b))
        if a == b or key in seen:
            continue
        seen.add(key); rels.append((a, b))
    cost = (o.get("cost_detail") or {}).get("total_cost") if isinstance(o, dict) else None
    return comms, rels, len(raw), len(raw) - len(rels), cost


def all_nodes(comms, rels):
    s = set()
    for kws in comms.values():
        s.update(kws)
    for a, b in rels:
        s.add(a); s.add(b)
    return s


def degrees(rels):
    deg = Counter()
    for a, b in rels:
        deg[a] += 1; deg[b] += 1
    return deg


def star_check(rels):
    """(허브, 허브가 닿은 고유 간선 비율). 간선이 없으면 ("", 0)."""
    if not rels:
        return "", 0.0
    hub, d = degrees(rels).most_common(1)[0]
    return hub, d / len(rels)


def check_seed(seed):
    if seed is None or not seed.strip():
        print("✗ 시드가 비었다 — --seed에 시드 키워드를 넣을 것 (빈 시드로는 거리·유형을 판정할 수 없다)")
        return False
    return True


def truncation_lines(n_raw, limit):
    lines = [f"⚠️ 절단: 관계 {n_raw:,}행 = limit {limit:,}. 그래프가 잘렸다."]
    lines.append("  limit은 관계 행을 반환 순서대로 자른다 — 어느 쪽이 잘릴지 응답은 알려주지 않는다.")
    lines.append("  실측(limit 50, 3건 중 2건)에서는 50행 전부가 허브 한 개의 인접 목록이었고, 시드의 직접 이웃까지 잘렸다.")
    lines.append("  «숨은 하위 시장 없음»으로 결론 내지 않는다. limit을 올려 재호출할지 사용자에게 묻는다.")
    return lines


def star_lines(hub, share, rels, seed_nodes=None):
    lines = [f"⚠️ 별 모양 퇴화: 고유 간선 {len(rels):,}개 중 {share:.0%}가 «{hub}» 한 노드에 붙었다 "
             f"(기준 {STAR_SHARE:.0%} [가정])."]
    if seed_nodes:
        adj = sum(1 for a, b in rels if a in seed_nodes or b in seed_nodes)
        lines.append(f"  시드의 직접 이웃 {adj}개만 남았다 — 군집 구조는 허브 주변 목록이지 하위 시장 지도가 아니다.")
    lines.append("  단일 키워드 군집이 많다면 절단 전 그래프에서 매긴 군집의 잔재일 수 있다 [추정]. 판정은 «잠정».")
    return lines


def cmd_budget(a):
    cons_n, tight_n = 2 * a.limit, a.limit + 1
    base = 10 + 150
    print(f"사전 예산 (limit {a.limit:,}) — 호출 전에는 고유 키워드 수 N을 모른다")
    print(f"  총액 = 10 (STEP 1 시드 all) + 150 + 50 × N (cluster_finder) + 1 × N (ads_metrics) + 10 × S (2단 all)")
    print(f"  보수 상한  N ≤ 2 × limit = {cons_n:,} → {base + 51 * cons_n:,} + 10 × S")
    print(f"  관측 상한  N ≤ limit + 1 = {tight_n:,} → {base + 51 * tight_n:,} + 10 × S  "
          f"[가정 — {TIGHT_OBS}. 보장값 아님]")
    print("  S = 2단 조회 대상 수(STEP 5가 고른다). 사용자에게는 두 상한을 함께 알리고 동의를 받는다.")
    return 0


def cmd_nodes(a):
    comms, rels, n_raw, n_rev, cost = load_cluster(a.cluster)
    nodes = all_nodes(comms, rels)
    if not rels and not comms:
        print("[데이터 공백] 관계·군집이 모두 비었다 — 시드의 공동검색 네트워크가 임계 아래다.")
        print("  검색량 0이라는 뜻이 아니다. 상위 표현(더 넓은 시드)으로 바꿔 다시 호출할 것.")
        return 2
    deg = degrees(rels)
    ordered = sorted(nodes, key=lambda k: (-deg.get(k, 0), k))
    json.dump(ordered, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    n = len(nodes)
    print(f"관계 {n_raw:,}행 (무방향 고유 간선 {len(rels):,}개 · 역방향 중복 {n_rev}쌍) · "
          f"군집 {len(comms)}개 · 고유 키워드 {n:,}개")
    expect = 150 + n * 50
    line = f"cluster_finder 결정식 150 + 50 × {n:,} = {expect:,}"
    if cost is not None:
        line += f" · 응답 cost_detail.total_cost = {cost:,}"
        if cost != expect:
            line += "  ⚠️ 불일치 — 소진량은 cost_detail을 기준으로 적는다"
    print(line)
    seed_nodes = {k for k in nodes if a.seed and basic(k) == basic(a.seed)} if a.seed else set()
    if a.seed and not seed_nodes:
        print(f"⚠️ 시드 «{a.seed}»가 그래프에 없다 — STEP 5(score)는 이 상태에서 판정하지 않는다")
    if a.limit and n_raw >= a.limit:
        for ln in truncation_lines(n_raw, a.limit):
            print(ln)
    elif a.limit:
        print(f"절단 아님: 관계 {n_raw:,}행 < limit {a.limit:,} — 이 그래프의 실제 크기다.")
    hub, share = star_check(rels)
    if share >= STAR_SHARE and len(rels) >= 5:
        for ln in star_lines(hub, share, rels, seed_nodes):
            print(ln)
    print(f"다음 keyword_info(ads_metrics) 예상: {n:,} × 1 = {n:,} (요청 수 과금 — 미반환 키워드도 과금된다)")
    print(f"→ {a.out}")
    return 0


def read_universe(path, graph=()):
    """키워드 → universe 행. 응답 표기가 요청과 다르면(캠핑의자 → 캠핑 의자) 공백·대소문자 무시로 잇는다."""
    rows, by_basic = {}, {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        rows[r["keyword"]] = r
        by_basic.setdefault(basic(r["keyword"]), r)
    for k in graph:
        if k not in rows and basic(k) in by_basic:
            rows[k] = by_basic[basic(k)]
    return rows


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def read_trend(path):
    rows = {}
    if not path:
        return rows, None
    reader = csv.DictReader(open(path, encoding="utf-8-sig"))
    prev_col = next((c for c in reader.fieldnames if c.startswith("sum_") and c.endswith("_prev_year")), None)
    for r in reader:
        rows[r["keyword"]] = r
    return rows, prev_col


def ref_month_of(path):
    """keyword_info all 응답의 기준월 = monthly_volume month 최댓값(배열 순서 가정 없음)."""
    if not path:
        return ""
    o = json.load(open(path, encoding="utf-8"))
    data = o.get("data", o) if isinstance(o, dict) else o
    months = [m.get("month") for d in data if isinstance(d, dict)
              for m in (d.get("monthly_volume") or []) if isinstance(m, dict) and m.get("month")]
    return max(months) if months else ""


def cmd_score(a):
    if not check_seed(a.seed):
        return 2
    comms, rels, n_raw, n_rev, _ = load_cluster(a.cluster)
    if not comms:
        print("[데이터 공백] communities가 없다 — data_type=all(또는 communities)로 호출했는지 확인할 것.")
        return 2
    graph = all_nodes(comms, rels)
    uni = read_universe(a.universe, graph)
    trend, prev_col = read_trend(a.trend)
    deg = degrees(rels)
    truncated = bool(a.limit and n_raw >= a.limit)

    canon_of = lambda k: (uni[k]["canonical"] if k in uni and uni[k].get("canonical") else basic(k))
    seed_c = basic(a.seed)
    seed_nodes = {k for k in graph if basic(k) == seed_c or canon_of(k) == canon_of(a.seed)}
    if not seed_nodes:
        print(f"✗ 시드 «{a.seed}»가 그래프에 없다 — 시드 거리를 잴 수 없어 «숨은/인접» 판정을 낼 수 없다.")
        print("  cluster_finder에 넘긴 시드 문자열을 그대로 --seed에 넣었는지 확인할 것. 판정하지 않고 멈춘다.")
        return 2

    # 기준월 — STEP 1의 시드 all 응답(또는 2단 all 응답)에서 읽는다. 없으면 헤더를 쓸 수 없다.
    ref_months = {m for m in (ref_month_of(a.seed_all),) if m}
    ref_months |= {t["data_ref_month"] for t in trend.values() if t.get("data_ref_month")}
    ref_month = max(ref_months) if ref_months else ""

    # 같은 검색의 중복(dup_of) — 대표 행이 그래프에 있으면 대표에서만 센다. 없으면 묶음의 첫 노드에서.
    counted_once = {}
    for k in sorted(graph):
        rep = (uni.get(k) or {}).get("dup_of") or k
        if rep != k and rep in graph:
            continue
        counted_once.setdefault(rep, k)
    is_counted = lambda k: counted_once.get((uni.get(k) or {}).get("dup_of") or k) == k

    # 시드로부터의 최단 거리(무방향 BFS)
    adj = defaultdict(set)
    for x, y in rels:
        adj[x].add(y); adj[y].add(x)
    dist = {s: 0 for s in seed_nodes}
    q = deque(seed_nodes)
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1; q.append(v)

    hub_g, star_share = star_check(rels)
    star = star_share >= STAR_SHARE and len(rels) >= 5

    out, kw_rows, picks, pick_gated = [], [], [], []
    g_lo, g_nb_lo = 0, 0
    for cid, members in comms.items():
        groups = {}   # canonical -> (rep_keyword, volume_max, brand, theme, volume_avg)
        gsum = Counter()   # canonical -> 그룹 내 합 (상한)
        missing = dups = 0
        for k in members:
            r = uni.get(k)
            v = to_int(r.get("volume_total")) if r else None
            if v is None:
                missing += 1
            if not is_counted(k):
                dups += 1
                continue       # 같은 검색의 중복 — 한 번만 센다 (계약 T5 ①)
            c = canon_of(k)
            brand = (r or {}).get("brand", "")
            theme = (r or {}).get("theme_primary", "")
            vavg = to_int((r or {}).get("volume_avg"))
            gsum[c] += v or 0
            cur = groups.get(c)
            # 대표 표기 = 그룹 내 최댓값(하한). 합(상한)은 gsum에 따로 둔다 — T5 ②
            if cur is None or (v or 0) > (cur[1] or 0):
                groups[c] = (k, v, brand or (cur[2] if cur else ""), theme, vavg)
            elif brand and not cur[2]:
                groups[c] = (cur[0], cur[1], brand, cur[3], cur[4])
        total = sum(g[1] or 0 for g in groups.values())
        total_hi = sum(gsum.values())
        nb = sum(g[1] or 0 for g in groups.values() if not g[2])
        nb_hi = sum(gsum[c] for c, g in groups.items() if not g[2])
        g_lo += total; g_nb_lo += nb
        nb_share = round(nb / total, 3) if total else ""
        nb_share_hi = round(nb_hi / total_hi, 3) if total_hi else ""
        tv, tv_hi = Counter(), Counter()
        for c, g in groups.items():
            tv[g[3] or "(미분류)"] += g[1] or 0
            tv_hi[g[3] or "(미분류)"] += gsum[c]
        theme_top, theme_v = (tv.most_common(1)[0] if tv else ("", 0))
        reps = sorted(groups.values(), key=lambda g: -(g[1] or 0))
        hub = max(members, key=lambda k: (deg.get(k, 0), k)) if members else ""
        dists = [dist[k] for k in members if k in dist]
        sdist = min(dists) if dists else ""
        links = sum(1 for x, y in rels
                    if (x in seed_nodes and y in members and y not in seed_nodes)
                    or (y in seed_nodes and x in members and x not in seed_nodes))

        # yoy — 2단 조회(all) 대상만 값이 있다. 게이트 미달·결측 창은 따로 센다
        ys, unc, gated, flags, queried = [], 0, 0, 0, 0
        for k in members:
            t = trend.get(k)
            if not t:
                continue
            queried += 1
            if t.get("last_month_flag"):
                flags += 1
            if t.get("yoy_ex_last_month", "") == "" or to_int(t.get("missing_months_in_window")):
                unc += 1
                continue
            base = to_int(t.get(prev_col)) if prev_col else None
            if base is not None and base < a.yoy_gate:
                gated += 1
                continue
            ys.append(float(t["yoy_ex_last_month"]))
        if not trend or not queried:
            tstatus = "추세 미조회" if trend else ""
        elif ys:
            tstatus = "판정"
        elif gated and not unc:
            tstatus = "게이트 미달"
        elif unc and not gated:
            tstatus = "계산 불가"
        else:
            tstatus = "게이트 미달·계산 불가"

        brand_dom = nb_share != "" and nb_share < a.brand_max   # 볼륨 0 군집은 비중을 정의할 수 없다 → 규모 미달 쪽

        def classify(share, vol):
            if members and any(k in seed_nodes for k in members):
                return "시드 코어"
            if missing * 2 >= len(members):
                return "지표 부족"
            if share != "" and share < a.brand_max:
                return "브랜드 지배"
            if len(groups) < a.min_groups:
                return "소형·단일"
            if vol < a.size_gate:
                return "규모 미달"
            return "인접 하위 시장" if sdist == 1 else "숨은 하위 시장 후보"

        typ = classify(nb_share, nb)            # 판정은 하한 기준
        typ_hi = classify(nb_share_hi, nb_hi)   # 상한이면 무엇이 되는가 — 다르면 verdict_flip

        out.append({
            "community_id": cid, "submarket_type": typ, "hub_keyword": hub,
            "hub_degree": deg.get(hub, 0), "n_keywords": len(members), "n_canonical": len(groups),
            "seed_distance": sdist, "seed_links": links, "volume_lo": total, "volume_hi": total_hi,
            "nonbrand_volume_lo": nb, "nonbrand_volume_hi": nb_hi,
            "nonbrand_share_lo": nb_share, "nonbrand_share_hi": nb_share_hi,
            "brand_dominated": "예" if brand_dom else "",
            "submarket_type_hi": typ_hi, "verdict_flip": "예" if typ_hi != typ else "",
            "theme_top": theme_top,
            "theme_top_share_lo": round(theme_v / total, 3) if total else "",
            "theme_top_share_hi": round(tv_hi[theme_top] / total_hi, 3) if total_hi and theme_top else "",
            "top_keywords": "|".join(g[0] for g in reps[:5]), "missing_metrics": missing,
            "dup_rows": dups, "trend_status": tstatus,
            "yoy_ex_last_median": round(statistics.median(ys), 4) if ys else "",
            "yoy_n": len(ys), "yoy_gated_n": gated, "yoy_uncomputable_n": unc, "last_month_flag_n": flags,
            "data_ref_month": ref_month, "truncated": "예" if truncated else "",
            "star_degenerate": "예" if star else "",
        })
        rep_set = {g[0] for g in groups.values()}
        for k in members:
            r = uni.get(k) or {}
            c = canon_of(k)
            kw_rows.append({"keyword": k, "canonical": c, "community_id": cid,
                            "degree": deg.get(k, 0), "volume_total": r.get("volume_total", ""),
                            "brand": r.get("brand", ""), "theme_primary": r.get("theme_primary", ""),
                            "dup_of": r.get("dup_of", ""),
                            "is_group_rep": "1" if k in rep_set else "",
                            "group_volume_lo": (groups[c][1] or 0) if c in groups else "",
                            "group_volume_hi": gsum[c] if c in groups else ""})
        if typ in ("숨은 하위 시장 후보", "인접 하위 시장"):
            # 2단 대상은 yoy_gate를 넘을 가능성이 있는 것만 — 최신 3개월 합(volume_avg × 3)으로 미리 거른다 [가정]
            for g in reps:
                if g[2] or not g[1]:
                    continue
                if g[4] is not None and g[4] * 3 < a.yoy_gate:
                    pick_gated.append(g[0]); continue
                if sum(1 for p in picks if p in members) < a.pick:
                    picks.append(g[0])

    # 같은 유형 안의 논브랜드 순위 — 하한이 기준, 상한 순위와 다르면 rank_flip
    by_type = defaultdict(list)
    for r in out:
        by_type[r["submarket_type"]].append(r)
    for rs in by_type.values():
        for key, col in (("nonbrand_volume_lo", "rank_in_type_lo"), ("nonbrand_volume_hi", "rank_in_type_hi")):
            for i, r in enumerate(sorted(rs, key=lambda r: (-r[key], r["community_id"])), 1):
                r[col] = i
        for r in rs:
            r["rank_flip"] = "예" if r["rank_in_type_lo"] != r["rank_in_type_hi"] else ""
    out.sort(key=lambda r: (TYPE_ORDER.index(r["submarket_type"]), r["rank_in_type_lo"]))
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS); w.writeheader(); w.writerows(out)
    with open(a.keywords_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=KW_COLUMNS); w.writeheader(); w.writerows(kw_rows)
    stage7 = bool(a.trend)
    if not stage7:
        json.dump(picks, open(a.pick_out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    print(f"군집 {len(out)}개 · 관계 {n_raw:,}행(고유 간선 {len(rels):,} · 역방향 중복 {n_rev}쌍) · "
          f"시드 노드 {len(seed_nodes)}개")
    print(f"데이터 기준월 {ref_month}" if ref_month else
          "⚠️ 데이터 기준월 없음 — --seed-all에 STEP 1의 시드 all 응답을 넘길 것. 헤더를 비워 둔 채 리포트를 쓰지 않는다.")
    print(f"[가정] size_gate {a.size_gate:,} · brand_max {a.brand_max} · min_groups {a.min_groups}"
          f" · yoy_gate {a.yoy_gate:,}")
    if truncated:
        print(f"⚠️ 절단: 관계 {n_raw:,}행 = limit {a.limit:,} — 어느 쪽이 잘렸는지 응답은 알려주지 않는다. "
              "«숨은 하위 시장» 판정은 잠정이다.")
    if star:
        for ln in star_lines(hub_g, star_share, rels, seed_nodes):
            print(ln)
    seed_brand = any((uni.get(k) or {}).get("brand") for k in seed_nodes)
    graph_nb = g_nb_lo / g_lo if g_lo else None
    if seed_brand or (graph_nb is not None and graph_nb < a.brand_max):
        why = "시드가 브랜드 사전에 걸린다" if seed_brand else ""
        if graph_nb is not None and graph_nb < a.brand_max:
            why += (" · " if why else "") + f"그래프 전체 논브랜드 비중 하한 {graph_nb:.1%} < {a.brand_max}"
        print(f"⚠️ 브랜드 시드: {why}. 결과는 하위 시장 지도가 아니라 그 브랜드의 제품 라인 지도다 — "
              "카테고리 대표어 시드로 다시 돌릴지 사용자에게 묻는다.")
    t_lo = sum(r["volume_lo"] for r in out); t_hi = sum(r["volume_hi"] for r in out)
    n_dup = sum(r["dup_rows"] for r in out)
    print(f"군집 볼륨 합: 하한 {t_lo:,} ~ 상한 {t_hi:,}  ※ 같은 검색의 중복 {n_dup}행은 한 번만 · "
          "하한=표기변형 그룹 최댓값 · 상한=그룹 합 [추정 — 규칙 기반 집계] — 판정·순위는 하한 기준")
    miss_kw = sorted(k for k in graph if to_int((uni.get(k) or {}).get("volume_total")) is None)
    if miss_kw:
        print(f"[데이터 공백] 지표 미반환 {len(miss_kw)}개 (과금은 요청 수 기준): "
              + ", ".join(miss_kw[:10]) + (" …" if len(miss_kw) > 10 else ""))
    cnt = Counter(r["submarket_type"] for r in out)
    for t in TYPE_ORDER:
        if cnt[t]:
            print(f"  {t:<14}{cnt[t]:>4}")
    n_bd = sum(1 for r in out if r["brand_dominated"])
    if n_bd:
        print(f"  (brand_dominated «예» {n_bd}개 — 유형과 별개로 센다. 시드 코어·소형 군집도 포함될 수 있다)")
    for r in out:
        if r["submarket_type"] in ("숨은 하위 시장 후보", "인접 하위 시장"):
            yoy = (f"{r['yoy_ex_last_median']:+.0%}" if r["yoy_ex_last_median"] != ""
                   else (r["trend_status"] or "추세 미조회"))
            print(f"  [{r['community_id']}] {r['submarket_type']} · 허브 {r['hub_keyword']} · "
                  f"논브랜드 하한 {r['nonbrand_volume_lo']:,} ~ 상한 {r['nonbrand_volume_hi']:,} "
                  f"({r['nonbrand_share_lo']:.0%}~{r['nonbrand_share_hi']:.0%}) · 거리 {r['seed_distance']} · {yoy}")
    vf = [r for r in out if r["verdict_flip"]]
    rf = [r for r in out if r["rank_flip"]]
    if vf:
        print(f"⚠️ 판정 뒤집힘 {len(vf)}개 — 상한(변형 합) 기준이면 유형이 달라진다. 판정은 하한 기준으로 두고 리포트에 함께 적는다:")
        for r in vf:
            print(f"  [{r['community_id']}] {r['submarket_type']} → 상한 기준 {r['submarket_type_hi']} · 허브 {r['hub_keyword']}")
    if rf:
        print(f"⚠️ 순위 뒤집힘 {len(rf)}개 — 같은 유형 안의 논브랜드 순위가 상한 기준에서 달라진다: "
              + ", ".join(f"[{r['community_id']}] {r['rank_in_type_lo']}→{r['rank_in_type_hi']}위" for r in rf))
    if not vf and not rf:
        print("판정·순위 뒤집힘 없음 — 하한·상한 어느 쪽이든 같은 결론 [추정]")
    if stage7:
        print("추세 반영 완료 — 2단 대상은 다시 고르지 않는다(stage2 파일 유지 · 추가 크레딧 없음)")
        print(f"→ {a.out} · {a.keywords_out}")
    else:
        if pick_gated:
            print(f"2단 대상에서 뺀 키워드 {len(pick_gated)}개 — 최신 3개월 합(volume_avg × 3) < yoy_gate "
                  f"{a.yoy_gate:,} [가정]: " + ", ".join(pick_gated[:5]) + (" …" if len(pick_gated) > 5 else ""))
        print(f"2단 조회(all) 대상 {len(picks)}개 → 10 × {len(picks)} = {10 * len(picks):,} 크레딧")
        print(f"→ {a.out} · {a.keywords_out} · {a.pick_out}")
    return 0 if ref_month else 3


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("budget")
    b.add_argument("--limit", type=int, required=True, help="cluster_finder 요청 limit — 관계(rels) 행 수 상한")
    n = sub.add_parser("nodes")
    n.add_argument("cluster"); n.add_argument("--limit", type=int, required=True,
                                              help="cluster_finder 요청 limit — 관계(rels) 행 수 상한")
    n.add_argument("--seed", help="시드 — 주면 그래프 안 시드 존재·직접 이웃 수를 함께 본다")
    n.add_argument("-o", "--out", default="nodes.json")
    s = sub.add_parser("score")
    s.add_argument("cluster")
    s.add_argument("--universe", required=True, help="build_universe.py 산출 CSV")
    s.add_argument("--seed", required=True)
    s.add_argument("--limit", type=int, required=True)
    s.add_argument("--seed-all", help="STEP 1 시드 keyword_info(all) 응답 — 데이터 기준월 원천")
    s.add_argument("--trend", help="trend_yoy.py 산출 CSV (2단 조회 후 · STEP 7). 주면 2단 대상을 다시 쓰지 않는다")
    s.add_argument("--size-gate", type=int, default=10000, help="논브랜드 볼륨 최소 (12개월 합)")
    s.add_argument("--brand-max", type=float, default=0.5, help="논브랜드 비중 최소")
    s.add_argument("--min-groups", type=int, default=3, help="canonical 그룹 최소 수")
    s.add_argument("--yoy-gate", type=int, default=3000, help="전년 동기 3개월 볼륨 최소 — 기저 0 폭발 차단")
    s.add_argument("--pick", type=int, default=3, help="후보 군집당 2단 조회 키워드 수")
    s.add_argument("-o", "--out", default="submarkets.csv")
    s.add_argument("--keywords-out", default="submarket_keywords.csv")
    s.add_argument("--pick-out", default="stage2_keywords.json")
    a = ap.parse_args()
    return {"budget": cmd_budget, "nodes": cmd_nodes, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
