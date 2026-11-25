#!/usr/bin/env python3
"""실행 입력 → 노드 분류 사전 leak_dict.json (이 스킬 고유 · 0크레딧).

assets/leak-dict.template.json 의 빈 슬롯(브랜드 · 카테고리어 · 시드 · 시장별 단계어)을 실행 입력으로 채운다.
브랜드는 스킬에 박지 않는다 — 매번 입력으로 받는다.

    python3 scripts/make_leak_dict.py --template assets/leak-dict.template.json --gl kr \\
        --seeds 런닝화 --my "아식스;asics" --competitor "나이키;nike" --competitor "아디다스;adidas" \\
        --category-terms 러닝화,러닝슈즈 --variant 런닝=러닝 -o leak_dict.json

막는 것:
  · 시드가 브랜드 별칭에 걸림 → 종료 코드 4. 브랜드 시드의 경로는 «카테고리 진입 경로»가 아니다
    (이미 그 브랜드 안에서 출발한다). 정말 브랜드 출발 표본을 보려면 --allow-brand-seed
  · 두 브랜드가 같은 별칭을 가짐 → 종료 코드 2 (한 노드가 두 브랜드에 이중 귀속된다)
  · 2글자 미만 별칭 → 종료 코드 2 (부분 일치로 무관한 노드를 브랜드로 만든다)
  · 별칭이 카테고리어 · 단계어와 겹침 → 경고 (그 말이 들어간 모든 논브랜드 노드가 브랜드가 된다)
"""
import argparse, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from leak_lib import LeakDict, utf8_console  # noqa: E402
from series_dedup import canon  # noqa: E402


def parse_brand(spec):
    """'이름;별칭1;별칭2' 또는 '이름,별칭'."""
    parts = [p.strip() for p in re.split(r"[;,|]", spec) if p.strip()]
    if not parts:
        return None
    return {"name": parts[0], "aliases": parts[1:]}


def alt(terms, rules):
    cs = sorted({canon(t, rules) for t in terms if t and canon(t, rules)}, key=lambda x: (-len(x), x))
    return "|".join(re.escape(c) for c in cs)


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="노드 분류 사전 만들기 (0크레딧)")
    ap.add_argument("--template", default=str(pathlib.Path(__file__).resolve().parent.parent
                                              / "assets" / "leak-dict.template.json"))
    ap.add_argument("--gl", required=True, choices=["kr", "jp", "us"])
    ap.add_argument("--seeds", required=True, help="카테고리 진입 시드, 쉼표로 (최대 3개)")
    ap.add_argument("--my", default="", help="내 브랜드 '이름;별칭;…' (없으면 경쟁 지형만 본다)")
    ap.add_argument("--competitor", action="append", default=[], help="경쟁 브랜드 '이름;별칭;…' (반복)")
    ap.add_argument("--category-terms", default="", help="카테고리어(시드 외 같은 품목을 가리키는 말), 쉼표로")
    ap.add_argument("--variant", action="append", default=[], help="변형치환 '왼쪽=오른쪽' (반복)")
    ap.add_argument("--extra-channel", default="", help="채널어 추가, 쉼표로")
    ap.add_argument("--allow-brand-seed", action="store_true", help="시드가 브랜드여도 진행(브랜드 출발 표본)")
    ap.add_argument("-o", "--out", default="leak_dict.json")
    a = ap.parse_args()

    tpl = json.loads(pathlib.Path(a.template).read_text(encoding="utf-8"))
    seeds = [s.strip() for s in a.seeds.split(",") if s.strip()]
    if not 1 <= len(seeds) <= 3:
        sys.exit(f"시드는 1~3개다 — 받은 것 {len(seeds)}개. path_finder 비용이 시드 수만큼 곱해진다")
    rules = dict(tpl.get("rules", {}).get("변형치환") or {})
    for v in a.variant:
        if "=" not in v:
            sys.exit(f"--variant 형식은 '왼쪽=오른쪽': {v}")
        l, r = v.split("=", 1)
        rules[l] = r
    my = parse_brand(a.my) if a.my.strip() else None
    comps = [b for b in (parse_brand(s) for s in a.competitor) if b]
    if not comps:
        sys.exit("경쟁 브랜드가 없다 — 누수(경쟁 브랜드로 넘어가는 지점)를 정의할 수 없다. --competitor 를 하나 이상 준다")

    errs, warns = [], []
    owner = {}
    for b in ([my] if my else []) + comps:
        for al in [b["name"]] + b["aliases"]:
            c = canon(al, rules)
            if len(c) < 2:
                errs.append(f"별칭 «{al}»({b['name']})이 2글자 미만 — 부분 일치로 무관한 노드가 브랜드가 된다")
            if c in owner and owner[c] != b["name"]:
                errs.append(f"별칭 «{al}»이 {owner[c]} · {b['name']} 둘에 있다 — 한 노드가 두 브랜드에 귀속된다")
            owner.setdefault(c, b["name"])
    terms = tpl.get("stage_terms_by_gl", {}).get(a.gl) or {}
    channel = list(terms.get("channel", [])) + [t.strip() for t in a.extra_channel.split(",") if t.strip()]
    cat_terms = [t.strip() for t in a.category_terms.split(",") if t.strip()]
    stop = {canon(t, rules) for t in cat_terms + seeds + channel + terms.get("community", []) + terms.get("comparison", [])}
    for c, name in owner.items():
        hit = [s for s in stop if s and (c == s or (len(c) >= 2 and c in s))]
        if hit:
            warns.append(f"«{name}» 별칭 «{c}»가 카테고리어·단계어 {hit[:3]}와 겹친다 — 그 말이 든 논브랜드 노드가 브랜드로 분류된다")
    if errs:
        print("\n".join(f"✗ {e}" for e in errs))
        return 2

    d = json.loads(json.dumps(tpl))
    d.pop("stage_terms_by_gl", None)
    d["rules"]["변형치환"] = rules
    d["gl"], d["seeds"], d["category_terms"] = a.gl, seeds, cat_terms
    d["brand_sets"] = {"my": my, "competitors": comps}
    d["brands"] = [x for b in ([my] if my else []) + comps for x in [b["name"]] + b["aliases"]]
    d["stage_terms"] = {"channel": channel, "community": terms.get("community", []),
                        "comparison": terms.get("comparison", [])}
    pats = {"channel": alt(channel, rules), "community": alt(terms.get("community", []), rules),
            "comparison": alt(terms.get("comparison", []), rules), "generic": alt(seeds + cat_terms, rules)}
    for t in d["themes"]:
        t["pattern"] = pats.get(t.get("class"), "")

    ld = LeakDict(d)
    brand_seeds = []
    for s in seeds:
        m, cs = ld.brands_in(s)
        if m or cs:
            brand_seeds.append(f"{s} → {([ld.my['name']] if m else []) + cs}")
    if brand_seeds:
        msg = "시드가 브랜드 별칭에 걸린다: " + " · ".join(brand_seeds)
        if not a.allow_brand_seed:
            print(f"✗ {msg}\n  카테고리 진입 경로가 아니라 그 브랜드 안에서 출발한 경로다. 시드를 브랜드 없는 카테고리어로 바꾼다"
                  f"(예: «나이키 런닝화» → «런닝화»). 브랜드 출발 표본이 정말 목적이면 --allow-brand-seed")
            return 4
        d["brand_seed"] = True
        warns.append(msg + " — 브랜드 출발 표본: 시드 자신의 브랜드는 귀속에서 뺀다")
    pathlib.Path(a.out).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    for w in warns:
        print(f"⚠ {w}")
    print(f"사전 → {a.out} · gl={a.gl} · 시드 {len(seeds)} · 내 브랜드 {my['name'] if my else '없음(경쟁 지형 모드)'} · "
          f"경쟁 {len(comps)} · 변형치환 {len(rules)} · 카테고리어 {len(cat_terms)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
