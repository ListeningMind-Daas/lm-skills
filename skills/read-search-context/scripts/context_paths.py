#!/usr/bin/env python3
"""path_finder 응답 → 시드 앞뒤 이음 · 갈래 구조 · 대표 경로 · SERP 대상 (이 스킬 고유 스크립트).

path_finder 는 경로 배열만 돌려준다. 앞뒤 검색 · 갈래 · 줄기 수는 전부 여기서 **로컬로** 만든다.
API 호출은 하지 않는다. 만드는 과정에서 막는 것:

  ① 표기변형 이중 계상(T5) — `캠핑 의자`→`캠핑의자`처럼 표기만 바뀐 인접은 앞뒤 검색으로 세지 않고
     건수만 적는다. 집계는 canonical 묶음 기준, 화면에는 그 묶음에서 가장 많이 나온 표기를 쓴다.
  ② 절단 오독(T7) — 받은 경로 수가 요청 limit 과 같으면 «전체 경로»가 아니다. truncated 로 적는다.
  ③ 세는 단위 — 받은 경로는 앞 몇 걸음을 공유하고 끝만 갈라지는 경우가 있다. 이음마다 경로 수와
     서로 다른 줄기(앞 --stem-len 걸음) 수를 함께 적는다. 줄기가 1개인 이음은 «가장 흔한»이 아니다.
  ④ 시드 표기 변경 — 응답이 시드를 다른 표기로 돌려주면(선크림 → 썬크림) 시드가 0경로에서 발견된다.
     한 글자만 다른 표기가 받은 경로 절반 이상에 있으면 멈추고(종료 코드 3) 치환 규칙을 제안한다.
  ⑤ 검색량 없음 — 0 으로 바꾸지 않고 비워 둔다.

    python3 scripts/context_paths.py --paths raw/path.json --seed 캠핑의자 --metrics raw/metrics.json \\
        --limit 100 --out-dir out [--rules '{"썬":"선"}'] [--context assets/context.json] \\
        [--branches 5] [--rep-paths 3] [--stem-len 3] [--pick "캠핑의자 > 경량 캠핑의자 > 헬리녹스 체어원"]
"""
import argparse, collections, csv, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, load_rules  # noqa: E402  (계약 T5)

MAP_DEPTH = 4            # [가정] 시드에서 네 걸음까지만 갈래 구조에 넣는다
EDGE_COLS = ["position", "depth", "from_canonical", "to_canonical", "from_example", "to_example",
             "path_count", "stem_count"]
NODE_COLS = ["canonical", "example", "variants", "volume_avg", "volume_avg_upper", "volume_label",
             "google_avg", "naver_avg", "google_share", "brand",
             "paths_with_keyword", "stems_with_keyword", "pre_seed_paths", "post_seed_paths", "min_depth"]
REP_COLS = ["path_id", "branch", "path", "length", "branch_path_count", "branch_stem_count", "reason"]
TARGET_COLS = ["code", "keyword", "canonical", "reason"]
TREND_COLS = ["code", "keyword", "canonical", "reason"]


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load(path):
    d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return d if isinstance(d, dict) else {"data": d}


def collapse(seq):
    """연속으로 같은 묶음(표기변형 인접)은 하나로 접는다 → (접은 목록, 접힌 인접 수)."""
    out, loops = [], 0
    for c in seq:
        if out and out[-1] == c:
            loops += 1
        else:
            out.append(c)
    return out, loops


def one_char_variants(seed_c, path_canons):
    """시드와 길이가 같고 한 글자만 다른 표기 → {표기: 나온 경로 수}."""
    hit = collections.Counter()
    for cs in path_canons:
        for c in set(cs):
            if len(c) == len(seed_c) >= 3 and sum(a != b for a, b in zip(c, seed_c)) == 1:
                hit[c] += 1
    return hit


def trie(seqs_with_stems):
    """[(걸음 목록, 줄기)] → 갈래 구조. n = 경로 수, s = 서로 다른 줄기 수."""
    def grow(items, d):
        if d >= MAP_DEPTH:
            return []
        groups = collections.OrderedDict()
        for seq, stem in items:
            if len(seq) > d:
                groups.setdefault(seq[d], []).append((seq, stem))
        kids = []
        for k, its in sorted(groups.items(), key=lambda kv: (-len(kv[1]), -len({s for _, s in kv[1]}), kv[0])):
            node = {"k": k, "n": len(its), "s": len({s for _, s in its})}
            sub = grow(its, d + 1)
            if sub:
                node["c"] = sub
            kids.append(node)
        return kids
    return grow(seqs_with_stems, 0)


def brand_lists(ctx_path, rules):
    """맥락 파일의 우리 · 경쟁 브랜드 — 빈 칸(⬜)은 건너뛴다."""
    if not ctx_path or not pathlib.Path(ctx_path).is_file():
        return [], []
    b = (json.loads(pathlib.Path(ctx_path).read_text(encoding="utf-8")).get("brands") or {})
    clean = lambda xs: [canon(x, rules) for x in xs or [] if isinstance(x, str) and x.strip() and "⬜" not in x]
    return clean(b.get("ours")), clean(b.get("competitors"))


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", required=True, help="path_finder 원본 응답")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--metrics", help="keyword_info(ads_metrics) 원본 응답 — 없으면 검색량은 모두 비운다")
    ap.add_argument("--limit", type=int, help="요청한 경로 수 — 없으면 응답의 request_detail.limit")
    ap.add_argument("--rules", help="변형치환 규칙 — 인라인 JSON 또는 파일")
    ap.add_argument("--context", help="맥락 파일 — 있으면 우리 · 경쟁 브랜드를 표시한다")
    ap.add_argument("--branches", type=int, default=5, help="[가정] SERP 를 볼 시드 뒤 갈래 수")
    ap.add_argument("--rep-paths", type=int, default=3, help="[가정] 대표 경로 수")
    ap.add_argument("--trend-branches", type=int, default=5, help="[가정] 전년 비교에 넣을 앞 · 뒤 갈래 수(각각)")
    ap.add_argument("--stem-len", type=int, default=3, help="[가정] 줄기로 보는 앞 걸음 수")
    ap.add_argument("--pick", action="append", default=[], help="대표 경로를 직접 지정 — \"a > b > c\" (반복 가능)")
    ap.add_argument("--allow-seed-variant", action="store_true")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()

    rules = load_rules(a.rules)
    resp = load(a.paths)
    raw_paths = [p for p in resp.get("data") or [] if isinstance(p, list)]
    limit = a.limit or (resp.get("request_detail") or {}).get("limit")
    seed_c = canon(a.seed, rules)

    surface = collections.defaultdict(collections.Counter)      # 묶음 → 표기별 등장 수
    canons = []
    for p in raw_paths:
        kws = [k.strip() for k in p if isinstance(k, str) and k.strip()]
        cs = [canon(k, rules) for k in kws]
        for k, c in zip(kws, cs):
            surface[c][k] += 1
        canons.append(cs)
    usable = [cs for cs in canons if len(cs) >= 2]
    with_seed = [cs for cs in usable if seed_c in cs]

    if not with_seed:
        var = one_char_variants(seed_c, usable)
        top = var.most_common(1)
        if top and top[0][1] * 2 >= max(1, len(usable)) and not a.allow_seed_variant:
            v = top[0][0]
            diff = next((x, y) for x, y in zip(v, seed_c) if x != y)
            print(f"✗ 시드 «{a.seed}»가 받은 경로 어디에도 없다 — 한 글자 다른 표기 «{surface[v].most_common(1)[0][0]}»가 "
                  f"{top[0][1]}/{len(usable)} 경로에 있다.\n  치환 규칙을 넣고 다시 돌린다(0크레딧): --rules '{json.dumps({diff[0]: diff[1]}, ensure_ascii=False)}'\n"
                  f"  정말 다른 검색이면 --allow-seed-variant", file=sys.stderr)
            return 3
        if not a.allow_seed_variant:
            print(f"✗ 시드 «{a.seed}»를 지나는 경로가 없다 [데이터 공백] — 받은 경로 {len(raw_paths)}개", file=sys.stderr)
            return 2

    ex = lambda c: surface[c].most_common(1)[0][0] if surface.get(c) else c
    edges = collections.defaultdict(lambda: [0, set()])          # (위치, 깊이, from, to) → [경로 수, 줄기 집합]
    node_paths = collections.defaultdict(set)
    node_stems = collections.defaultdict(set)
    pre_n, post_n, min_depth = collections.Counter(), collections.Counter(), {}
    pre_seqs, post_seqs, loops_total, full = [], [], 0, []
    for i, cs in enumerate(with_seed):
        seq, loops = collapse(cs)
        loops_total += loops
        stem = " > ".join(seq[:a.stem_len])
        at = seq.index(seed_c)
        pre, post = list(reversed(seq[:at])), seq[at + 1:]
        pre_seqs.append((pre, stem)); post_seqs.append((post, stem)); full.append((seq, stem, at))
        for c in set(seq):
            node_paths[c].add(i); node_stems[c].add(stem)
        for side, steps, cnt in (("pre_seed", pre, pre_n), ("post_seed", post, post_n)):
            prev = seed_c
            for d, c in enumerate(steps, 1):
                if d <= MAP_DEPTH:
                    key = (side, d, c, prev) if side == "pre_seed" else (side, d, prev, c)
                    edges[key][0] += 1; edges[key][1].add(stem)
                min_depth[c] = min(min_depth.get(c, 99), d)
                prev = c
            for c in set(steps):
                cnt[c] += 1

    # 검색량 — 묶음 하한(가장 큰 값) · 상한(합). 없으면 비운다.
    vols = collections.defaultdict(list)
    metric_names = collections.defaultdict(set)   # 묶음 → 검색량 응답에 온 표기(요청하지 않은 변형 포함)
    engines = {}                 # 묶음 → 하한 행(가장 큰 volume_avg)의 (구글, 네이버) — 한국 검색량은 구글+네이버 합산이다
    metrics_gl = None
    if a.metrics and pathlib.Path(a.metrics).is_file():
        seen, mresp = set(), load(a.metrics)
        metrics_gl = (mresp.get("request_detail") or {}).get("gl")
        for r in mresp.get("data") or []:
            if not isinstance(r, dict) or not r.get("keyword") or r["keyword"] in seen:
                continue
            seen.add(r["keyword"])
            am = r.get("ads_metrics") or {}
            v = am.get("volume_avg")
            if isinstance(v, (int, float)):
                c = canon(r["keyword"], rules)
                vols[c].append(v)
                metric_names[c].add(r["keyword"])
                g, n = am.get("gg_volume_avg"), am.get("nv_volume_avg")
                if v >= max(vols[c]) and isinstance(g, (int, float)) and isinstance(n, (int, float)):
                    engines[c] = (g, n)
    split = (metrics_gl or (resp.get("request_detail") or {}).get("gl")) == "kr"   # 네이버 분해는 한국 시장에서만 뜻이 있다
    ours, comps = brand_lists(a.context, rules)

    def brand_of(c):
        if any(b and b in c for b in ours):
            return "ours"
        return "competitor" if any(b and b in c for b in comps) else ""

    out = pathlib.Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    def write(name, cols, rows):
        with open(out / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

    edge_rows = [{"position": k[0], "depth": k[1], "from_canonical": k[2], "to_canonical": k[3],
                  "from_example": ex(k[2]), "to_example": ex(k[3]), "path_count": v[0], "stem_count": len(v[1])}
                 for k, v in edges.items()]
    edge_rows.sort(key=lambda r: (r["position"], r["depth"], -r["path_count"], -r["stem_count"], r["to_canonical"]))
    write("context_edges.csv", EDGE_COLS, edge_rows)

    node_rows = []
    for c in sorted(node_paths, key=lambda c: (-len(node_paths[c]), c)):
        vs = vols.get(c) or []
        label = "[데이터 공백]" if not vs else ("[추정]" if len(surface[c]) > 1 else "[실제 데이터]")
        node_rows.append({"canonical": c, "example": ex(c), "variants": " | ".join(list(dict.fromkeys([k for k, _ in surface[c].most_common()] + sorted(metric_names.get(c, ()))))),
                          "volume_avg": int(max(vs)) if vs else "", "volume_avg_upper": int(sum(vs)) if vs else "",
                          "volume_label": label,
                          "google_avg": int(engines[c][0]) if split and c in engines else "",
                          "naver_avg": int(engines[c][1]) if split and c in engines else "",
                          "google_share": round(engines[c][0] / sum(engines[c]) * 100, 1) if split and c in engines and sum(engines[c]) else "",
                          "brand": brand_of(c), "paths_with_keyword": len(node_paths[c]),
                          "stems_with_keyword": len(node_stems[c]), "pre_seed_paths": pre_n.get(c, 0),
                          "post_seed_paths": post_n.get(c, 0), "min_depth": 0 if c == seed_c else min_depth.get(c, "")})
    write("context_nodes.csv", NODE_COLS, node_rows)

    pre_tree, post_tree = trie(pre_seqs), trie(post_seqs)

    # 대표 경로 — 시드 뒤 상위 갈래에서 조건이 가장 많이 붙은 경로 하나씩 [가정]. 뒤가 없으면 앞 갈래에서.
    side_tree, side_seqs, side = (post_tree, post_seqs, "post") if post_tree else (pre_tree, pre_seqs, "pre")
    reps = []
    for b in side_tree[:a.rep_paths]:
        cand = [seq[:MAP_DEPTH] for seq, _ in side_seqs if seq and seq[0] == b["k"]]
        share = collections.Counter(tuple(s[:2]) for s in cand)
        best = sorted(cand, key=lambda s: (-len(s), -share[tuple(s[:2])], s))[0]
        path = [seed_c] + best if side == "post" else list(reversed(best)) + [seed_c]
        reps.append({"branch": ex(b["k"]), "canon": path, "branch_path_count": b["n"], "branch_stem_count": b["s"],
                     "reason": "상위 갈래에서 조건이 가장 많이 붙은 경로"})
    for raw in a.pick:
        cs = [canon(x, rules) for x in raw.split(">") if x.strip()]
        miss = [c for c in cs if c not in node_paths]
        if miss:
            print(f"✗ --pick 의 검색어가 받은 경로에 없다: {miss}", file=sys.stderr)
            return 2
        reps.append({"branch": ex(cs[1] if len(cs) > 1 else cs[0]), "canon": cs, "branch_path_count": "",
                     "branch_stem_count": "", "reason": "사람이 지정"})
    rep_rows = [{"path_id": f"P{i}", "branch": r["branch"], "path": " > ".join(ex(c) for c in r["canon"]),
                 "length": len(r["canon"]), "branch_path_count": r["branch_path_count"],
                 "branch_stem_count": r["branch_stem_count"], "reason": r["reason"]} for i, r in enumerate(reps, 1)]
    write("representative_paths.csv", REP_COLS, rep_rows)

    targets, seen = [], set()

    def add(c, reason):
        if c not in seen:
            seen.add(c); targets.append({"code": f"E{len(targets) + 1}", "keyword": ex(c), "canonical": c, "reason": reason})
    add(seed_c, "seed")
    for b in post_tree[:a.branches]:
        add(b["k"], "branch")
    for i, r in enumerate(reps, 1):
        for c in r["canon"]:
            add(c, f"path:P{i}")
    write("serp_targets.csv", TARGET_COLS, targets)

    # 전년 비교 대상 — 시드 + 뒤 갈래 상위 + 앞 갈래 상위 [가정]. 화면 표기로 조회한다
    trend, tseen = [], set()
    for c, why in [(seed_c, "seed")] + [(b["k"], "post") for b in post_tree[:a.trend_branches]] + [(b["k"], "pre") for b in pre_tree[:a.trend_branches]]:
        if c not in tseen:
            tseen.add(c); trend.append({"code": f"T{len(trend) + 1}", "keyword": ex(c), "canonical": c, "reason": why})
    write("trend_targets.csv", TREND_COLS, trend)

    uniq_kw = {k for p in raw_paths for k in p if isinstance(k, str) and k.strip()}
    cost = (resp.get("cost_detail") or {}).get("total_cost")
    truncated = None if not limit else len(raw_paths) >= limit
    summary = {
        "seed": a.seed, "seed_canonical": seed_c, "requested_limit": limit, "returned_paths": len(raw_paths),
        "usable_paths": len(usable), "seed_paths": len(with_seed), "off_seed_paths": len(usable) - len(with_seed),
        "truncated": truncated, "unique_keywords": len(uniq_kw), "unique_groups": len(surface),
        "variant_loops": loops_total, "stem_len": a.stem_len, "map_depth": MAP_DEPTH,
        "actual_cost": cost, "formula_cost": 50 + 40 * len(uniq_kw),
        "volume_missing": sum(1 for r in node_rows if r["volume_avg"] == ""),
        "seed_volume": next((r["volume_avg"] for r in node_rows if r["canonical"] == seed_c), ""),
        "time_point": (resp.get("request_detail") or {}).get("time_point") or "curr",
        "gl": (resp.get("request_detail") or {}).get("gl"), "rules": rules,
    }
    (out / "context_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    label = lambda t: [dict(n, label=ex(n["k"]), **({"c": label(n["c"])} if n.get("c") else {})) for n in t]
    (out / "context_tree.json").write_text(json.dumps(
        {"seed": ex(seed_c) if surface.get(seed_c) else a.seed, "pre": label(pre_tree), "post": label(post_tree)},
        ensure_ascii=False), encoding="utf-8")

    print(f"받은 경로 {len(raw_paths)}개 · 시드를 지나는 경로 {len(with_seed)}개 · 고유 검색어 {len(uniq_kw)}개"
          f"(표기 묶음 {len(surface)}개) [실제 데이터]")
    print(f"표기변형 인접 {loops_total}건은 앞뒤 검색으로 세지 않았다(T5)")
    if truncated is None:
        print("절단: 판정 불가 — 요청 limit 을 모른다(--limit 로 준다)")
    elif truncated:
        print(f"절단: 받은 경로 수가 요청 limit {limit} 과 같다 — «전체 경로»가 아니라 «받은 경로 기준»으로 쓴다(T7)")
    else:
        print(f"절단 아님: 요청 limit {limit} · 받은 경로 {len(raw_paths)}")
    if cost is not None and cost != summary["formula_cost"]:
        print(f"⚠ 비용 대조: 실측 {cost} ≠ 식 50 + 40 × {len(uniq_kw)} = {summary['formula_cost']}")
    one = [r for r in edge_rows if r["depth"] == 1 and r["stem_count"] == 1 and r["path_count"] > 1]
    if one:
        print(f"줄기가 1개뿐인 바로 앞뒤 이음 {len(one)}건 — 경로 수가 커도 «가장 흔한»으로 쓰지 않는다 [추정]")
    print(f"검색량 없는 묶음 {summary['volume_missing']}개 [데이터 공백] · 대표 경로 {len(rep_rows)}개 · SERP 대상 {len(targets)}개 · 전년 비교 대상 {len(trend)}개 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
