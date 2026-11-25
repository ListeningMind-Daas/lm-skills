#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""같은 검색의 중복 행 판정 + 표기 정규화 (계약 T5).

keyword_info는 요청하지 않은 변형(띄어쓰기·어순·복수형·동의어)을 함께 돌려준다. 그중 일부는
**같은 검색을 중복해서 받은 행**이고(48개월 시계열·CPC까지 동일), 일부는 **서로 다른 검색**이다.
어순·복수형·동의어 변형이 시계열까지 동일하게 온 묶음이 여분 볼륨의 큰 몫을 차지할 수 있다.
canonical 키(띄어쓰기 제거)로는 이 묶음을 잡을 수 없다.

규칙:
  1. 시계열이 완전히 같은 행 → 같은 검색의 중복. 한 번만 센다 (dup_of = 대표 행)
     - 시계열이 없으면(ads_metrics 단독) volume_total·CPC·competition_index가 모두 같을 때만,
       그리고 토큰이 겹칠 때만 중복으로 본다 — 버킷 반올림으로 우연히 같은 값이 나오는 것을 막는다.
  2. 시계열이 다른 표기변형(같은 canonical) → 하한(그룹 최댓값)·상한(그룹 합)을 병기한다.
  [추정] 이 판정은 규칙 기반이다. 근거 범위는 계약 T5에 적는다.

  0. 같은 keyword의 행이 두 번 오면(응답 안에서 행 자체가 반복 — 요청 수보다 많은 행이 오고 초과분이
     전부 완전 중복인 경우가 있다) 완전히 같은 행은 하나로 합친다(`collapse_identical`). dedup_map은
     키워드를 키로 쓰므로 합치지 않으면 두 행이 모두 대표가 되어 그룹 상한에 이중으로 들어간다.

모듈 사용:
  from series_dedup import canon, collapse_identical, dedup_map
  rows, merged, conflicts = collapse_identical(rows)   # 먼저 완전 중복 행을 합친다
  dmap = dedup_map(rows)          # rows: keyword_info 응답의 data 항목들
  dmap[kw] -> {"rep": 대표 키워드, "basis": "시계열 일치" | "지표 일치" | "", "n": 묶음 크기}

CLI:
  python3 series_dedup.py <keyword_info.json> [-o dup_map.csv] [--rules '{"썬":"선"}']
"""
import argparse, csv, json, re, sys, unicodedata

MIN_SERIES_TOTAL = 1000   # [가정] 이보다 작은 시계열은 버킷 반올림으로 우연히 같을 수 있어 토큰 겹침을 추가로 요구


def apply_rules(s, rules):
    """변형치환을 **띄어쓰기를 지우기 전** 문자열에 적용한다.

    공백을 지운 뒤 치환하면 단어 경계를 넘어 오작동한다(`vacuum sale`→`vacuumale`).
    이미 치환 결과가 들어 있는 곳은 다시 치환하지 않는다(`stickvac`→`stickvacuum` 반복 방지).
    """
    for a, b in (rules or {}).items():
        if not a:
            continue
        if b.startswith(a) and len(b) > len(a):
            s = re.sub(re.escape(a) + "(?!" + re.escape(b[len(a):]) + ")", b, s)
        else:
            s = s.replace(a, b)
    return s


def canon(kw, rules=None):
    """NFKC · 소문자 · 변형치환(공백 유지 상태) · 공백 제거."""
    s = unicodedata.normalize("NFKC", kw).lower()
    s = apply_rules(s, rules)
    return re.sub(r"\s+", "", s)


def load_rules(value):
    """인라인 JSON · 치환 사전 파일 · themes.json(rules.변형치환) 모두 받는다."""
    if not value:
        return {}
    obj = json.loads(value) if value.lstrip().startswith("{") else json.load(open(value, encoding="utf-8"))
    if isinstance(obj, dict) and isinstance(obj.get("rules"), dict):
        return obj["rules"].get("변형치환", {})
    if isinstance(obj, dict) and all(isinstance(v, str) for v in obj.values()):
        return obj
    return {}


def collapse_identical(rows):
    """같은 keyword의 **완전히 같은 행**을 하나로 합친다 → (rows, 합친 행 수, 충돌 키워드 목록).

    keyword는 같은데 내용이 다른 행은 합치지 않고 첫 행만 남긴 뒤 충돌 목록으로 돌려준다 —
    어느 쪽이 맞는지 응답만으로는 알 수 없으므로 호출 측이 출력해 사람이 확인하게 한다.
    """
    first, out, merged, conflicts = {}, [], 0, []
    for r in rows:
        kw = r.get("keyword") if isinstance(r, dict) else None
        if not isinstance(kw, str):
            out.append(r); continue
        sig = json.dumps(r, sort_keys=True, ensure_ascii=False)
        if kw not in first:
            first[kw] = sig; out.append(r)
        elif first[kw] == sig:
            merged += 1
        else:
            conflicts.append(kw)
    return out, merged, conflicts


def report_collapse(merged, conflicts, total):
    """collapse_identical 결과를 한 줄로 알린다 (합친 것이 없으면 조용히)."""
    if merged:
        print(f"완전 중복 행 {merged}행 합침 — 응답 {total}행 안에서 같은 keyword·같은 내용이 반복됨 (계약 T5)")
    if conflicts:
        print(f"⚠️ keyword는 같은데 내용이 다른 행 {len(conflicts)}건 — 첫 행만 사용: {', '.join(conflicts[:5])}"
              + (" …" if len(conflicts) > 5 else ""))


def _tokens(kw):
    t = re.split(r"\s+", unicodedata.normalize("NFKC", kw).lower().strip())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in t if w}


def _series_sig(row):
    mv = row.get("monthly_volume")
    if not isinstance(mv, list) or not mv:
        return None, 0
    sig = tuple((m.get("month"), m.get("total"), m.get("gg"), m.get("nv")) for m in mv if isinstance(m, dict))
    total = sum((m.get("total") or 0) for m in mv if isinstance(m, dict))
    if total == 0:
        return None, 0
    return sig, total


def _metric_sig(row):
    m = row.get("ads_metrics") or {}
    v = m.get("volume_total")
    if not v or m.get("cpc") is None or m.get("competition_index") is None:
        return None
    return (v, m.get("cpc"), m.get("competition_index"), m.get("gg_volume_total"), m.get("nv_volume_total"))


def dedup_map(rows, rules=None):
    """키워드 → 대표 키워드. 같은 검색의 중복 묶음만 합친다(표기변형 하한·상한은 호출 측)."""
    rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get("keyword"), str)]
    out = {r["keyword"]: {"rep": r["keyword"], "basis": "", "n": 1} for r in rows}
    buckets = {}
    for r in rows:
        sig, total = _series_sig(r)
        if sig is not None:
            buckets.setdefault(("시계열 일치", sig), []).append((r, total))
        else:
            ms = _metric_sig(r)
            if ms is not None:
                buckets.setdefault(("지표 일치", ms), []).append((r, 0))
    for (basis, _), members in buckets.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda x: (len(x[0]["keyword"]), x[0]["keyword"]))
        # 토큰·canonical 연결 요소로 묶는다 — 우연히 같은 값인 무관 키워드는 합치지 않는다
        groups = []
        for r, total in members:
            kw = r["keyword"]
            placed = False
            for g in groups:
                rep, reptok, repcanon = g[0]
                related = (canon(kw, rules) == repcanon or bool(_tokens(kw) & reptok))
                strong = basis == "시계열 일치" and total >= MIN_SERIES_TOTAL
                if related or strong:
                    g.append((kw, _tokens(kw), canon(kw, rules))); placed = True; break
            if not placed:
                groups.append([(kw, _tokens(kw), canon(kw, rules))])
        for g in groups:
            if len(g) < 2:
                continue
            rep = g[0][0]
            for kw, _, _ in g:
                out[kw] = {"rep": rep, "basis": basis, "n": len(g)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("metrics")
    ap.add_argument("-o", "--out")
    ap.add_argument("--rules")
    a = ap.parse_args()
    obj = json.load(open(a.metrics, encoding="utf-8"))
    rows = obj.get("data", obj) if isinstance(obj, dict) else obj
    n_raw = len(rows)
    rows, merged, conflicts = collapse_identical(rows)
    report_collapse(merged, conflicts, n_raw)
    dm = dedup_map(rows, load_rules(a.rules))
    dup = {k: v for k, v in dm.items() if v["rep"] != k}
    vol = {r["keyword"]: (r.get("ads_metrics") or {}).get("volume_total") or 0 for r in rows if isinstance(r, dict)}
    print(f"{len(dm)}행 중 같은 검색의 중복으로 판정 {len(dup)}행 · 중복 볼륨 {sum(vol.get(k, 0) for k in dup):,} "
          f"(전체 {sum(vol.values()):,}) [추정]")
    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["keyword", "dup_of", "dup_basis", "dup_group_size"])
            for k, v in dm.items():
                w.writerow([k, "" if v["rep"] == k else v["rep"], v["basis"], v["n"]])
        print(f"→ {a.out}")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):      # Windows cp949 콘솔에서 ⚠·— 출력이 UnicodeEncodeError 로 죽지 않게
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
