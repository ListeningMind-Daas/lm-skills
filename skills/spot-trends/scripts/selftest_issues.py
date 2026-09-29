#!/usr/bin/env python3
"""이 스킬 고유 회귀 검사 — trend_finder 이슈 후보 경로 (합성 픽스처 · API 호출 없음). v1.5.1

  I1 후보 추출 — data[].keyword → candidates.json, series_dedup.canon으로 표기변형 병합
  I2 지속 — 같은 이슈가 여러 index_date에 나오면 n_index_dates로 센다
  I3 빈 결과 — 14일만 비었으면 exit 3(넓혀 재조회) · 14·90일 모두 비면 «이슈 없음»(후보는 시드뿐 — 지어내지 않음)
  I4 크레딧 — 50 × 결과 있는 호출 + 2 × 반환 = cost_detail 합 (빈 결과 호출은 0 — 경고 없음) · limit 절단 경고
  P1-1 --exclude 후보는 «범위 밖 제외»로 남고 «검색량 미반환»으로 새지 않는다
  P1-2 스크린 탈락은 volume_avg와 함께 · 게이트 미달(측정됨)은 알림의 «신생·저기저 급등 후보» 절로
  P1-4 넓힘은 범위 안 이슈 수로 판정 · 띄어쓴 시드 경고
  P1-5 --rules 표기 치환으로 후보 병합 · 스크린에서 지표 일치(같은 검색)를 병합해 all에 한 번만
  I5 근거 결합 — trend_signals.py --evidence가 issue_* 컬럼을 채운다
  I6 «이슈 신호만 · 검색량 미확인» — 미반환·시계열 없음·게이트 미달을 등급 없이 사유와 함께 남긴다
  I7 write_alert.py — 이슈 근거가 있는데 --issue-only가 없으면 멈추고, 있으면 절을 쓴다
  I8 스크린 — volume_avg × window ≥ gate만 all로 · SKILL.md 컬럼 목록 = 실제 출력(근거·미확인 CSV)

    python3 scripts/selftest_issues.py        # 스킬 폴더에서 실행
"""
import csv, json, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE.parent / "assets" / "fixtures"
sys.path.insert(0, str(HERE))
from selftest_trends import grow, row  # noqa: E402  같은 합성 시계열(기준월 2026-08)


def run(args, cwd):
    p = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def read(p):
    with open(p, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        return rd.fieldnames, list(rd)


def doc_columns(block):
    lines = (HERE.parent / "SKILL.md").read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == block:
            cols = []
            for nxt in lines[i + 1:]:
                if not re.match(r"^\s+[a-z_]", nxt):
                    break
                cols += [c.strip() for c in nxt.split(",") if c.strip()]
            return cols
    return None


def regressions_p1(t, IC, TS):
    """2026-09-29 실데이터 평가 결함(P1-1·P1-2·P1-4·P1-5) 재발 방지 — 합성 응답."""
    F = []
    o = json.loads((t / "issue_list_90d.json").read_text(encoding="utf-8"))
    # P1-4 — 넓힘은 범위 안 이슈 수로: 14일 응답이 전부 범위 밖이면 비어 있지 않아도 exit 3
    rc, out = run([IC, "issue_list_90d.json@14", "--exclude", ".", "-o", "p4.json", "--evidence-out", "p4.csv"], t)
    if rc != 3 or "범위 안 이슈 0건" not in out:
        F.append(("P1-4", "범위 밖 이슈만 있을 때 넓힘(exit 3)을 요구하지 않았다", out[-300:]))
    o2 = dict(o, request_detail=dict(o["request_detail"], keyword="캠핑 의자"))
    (t / "spaced.json").write_text(json.dumps(o2, ensure_ascii=False), encoding="utf-8")
    rc, out = run([IC, "spaced.json", "-o", "p4b.json", "--evidence-out", "p4b.csv"], t)
    if rc or "띄어쓴 시드" not in out or "캠핑의자" not in out:
        F.append(("P1-4", "띄어쓴 시드 경고·붙여 쓴 형태 제안이 없다", out[-300:]))

    # P1-1 — --exclude 후보는 «범위 밖 제외» (issue_meta에서 정규식을 읽는다)
    rc, out = run([IC, "issue_list_90d.json", "--exclude", "수납", "-o", "p1.json", "--evidence-out", "p1.csv",
                   "--meta-out", "p1_meta.json"], t)
    cand = json.loads((t / "p1.json").read_text(encoding="utf-8")) if not rc else []
    if rc or "캠핑의자 수납 가방" in cand or "범위 밖 1개" not in out:
        F.append(("P1-1", "--exclude 이슈가 후보에 남았거나 뺀 사실이 출력되지 않았다", out[-300:]))
    rc, out = run([TS, "all.json", "-o", "p1s.csv", "--evidence", "p1.csv", "--issue-meta", "p1_meta.json"], t)
    only = {r["keyword"]: r["reason"] for r in read(t / "p1s_issue_only.csv")[1]} if not rc else {}
    if only.get("캠핑의자 수납 가방") != "범위 밖 제외":
        F.append(("P1-1", "STEP 2에서 뺀 후보가 «범위 밖 제외»가 아니다", str(only)))

    # P1-2 — 스크린 탈락은 «검색량 미반환»이 아니라 volume_avg와 함께
    ads = [{"keyword": k, "ads_metrics": {"volume_avg": v, "volume_total": v * 12, "cpc": 0.1, "competition_index": 1}}
           for k, v in (("캠핑의자", 9000), ("릴렉스 체어", 7000), ("캠핑의자 경량", 5000), ("하이백 캠핑의자", 400))]
    (t / "p2ads.json").write_text(json.dumps({"data": ads}, ensure_ascii=False), encoding="utf-8")
    run([IC, "--screen", "p2ads.json", "--candidates", "p1.json", "-o", "p2all.json", "--meta", "p1_meta.json"], t)
    kw = [row("캠핑의자", grow(20000, 1.1)), row("릴렉스 체어", grow(5000, 1.4)), row("캠핑의자 경량", grow(4000, 1.2))]
    (t / "p2kw.json").write_text(json.dumps({"data": kw}, ensure_ascii=False), encoding="utf-8")
    rc, out = run([TS, "p2kw.json", "-o", "p2s.csv", "--evidence", "p1.csv", "--issue-meta", "p1_meta.json"], t)
    only = {r["keyword"]: r for r in read(t / "p2s_issue_only.csv")[1]} if not rc else {}
    h = only.get("하이백 캠핑의자", {})
    if h.get("reason") != "스크린 탈락" or h.get("volume_avg") != "400":
        F.append(("P1-2", "스크린 탈락 후보가 사유·volume_avg와 함께 남지 않았다", str(h)))
    if only.get("체어 원목 캠핑", {}).get("reason") != "검색량 미반환":
        F.append(("P1-2", "ads_metrics에 행이 없는 후보가 «검색량 미반환»이 아니다", str(only.get("체어 원목 캠핑"))))

    # P1-5 — 표기 치환(--rules)으로 후보 병합 · 스크린의 같은 검색(지표 일치) 병합
    items = [dict(o["data"][0], keyword=k, issue_score=s) for k, s in
             (("나이키 알파플라이 3", 9), ("알파플라이3", 5), ("알파 플라이3", 3), ("경량 체어", 2), ("체어 경량", 1))]
    (t / "p5.json").write_text(json.dumps(dict(o, data=items, total=5), ensure_ascii=False), encoding="utf-8")
    rules = '{"플라이 3":"플라이3","나이키 알파플라이3":"알파플라이3","알파 플라이3":"알파플라이3"}'
    rc, out = run([IC, "p5.json", "--no-seed", "--rules", rules, "-o", "p5c.json", "--evidence-out", "p5.csv",
                   "--meta-out", "p5_meta.json"], t)
    cand = json.loads((t / "p5c.json").read_text(encoding="utf-8")) if not rc else []
    if len(cand) != 3:
        F.append(("P1-5", f"모델명 표기변형 3개가 --rules로 한 후보가 되지 않았다: {cand}", out[-200:]))
    ads = [{"keyword": k, "ads_metrics": {"volume_avg": 2000, "volume_total": 24000, "cpc": 0.3, "competition_index": 40,
                                          "gg_volume_total": 8000, "nv_volume_total": 16000}} for k in ("경량 체어", "체어 경량")]
    ads.append({"keyword": "알파플라이 3", "ads_metrics": {"volume_avg": 1600, "volume_total": 18000, "cpc": 0.2,
                                                              "competition_index": 10}})
    (t / "p5ads.json").write_text(json.dumps({"data": ads}, ensure_ascii=False), encoding="utf-8")
    rc, out = run([IC, "--screen", "p5ads.json", "--candidates", "p5c.json", "--rules", rules, "-o", "p5all.json",
                   "--meta", "p5_meta.json"], t)
    keep = json.loads((t / "p5all.json").read_text(encoding="utf-8")) if not rc else []
    if rc or "같은 검색 병합 1" not in out or len(keep) != 2 or "같은 검색 병합 [추정" not in out:
        F.append(("P1-5", "지표가 같은 두 후보(같은 검색)를 병합해 all에 한 번만 보내지 않았다", out[-300:]))
    return F


def main():
    F = []
    IC, TS, WA = (str(HERE / n) for n in ("issue_candidates.py", "trend_signals.py", "write_alert.py"))
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        for p in FIX.glob("issue_list_*.json"):
            shutil.copy(p, t / p.name)

        # I3 — 14일만 비었다: 넓혀야 한다
        rc, out = run([IC, "issue_list_14d_empty.json", "-o", "c0.json", "--evidence-out", "e0.csv"], t)
        if rc != 3 or "period_days=90" not in out:
            F.append(("I3", "14일 빈 결과에서 넓혀 재조회를 요구하지 않았다", out[-300:]))
        # I3 — 14·90일 모두 비었다: «이슈 없음», 후보 0
        rc, out = run([IC, "issue_list_14d_empty.json", "issue_list_90d_empty.json", "-o", "c1.json",
                       "--evidence-out", "e1.csv"], t)
        if rc != 0 or "이슈 없음" not in out or json.loads((t / "c1.json").read_text(encoding="utf-8")) != ["캠핑의자"] \
                or "= 0 " not in out or "⚠️ 결정식" in out:
            F.append(("I3", "넓혀도 빈 결과인데 «이슈 없음»으로 끝나지 않았다(또는 후보를 지어냈다)", out[-300:]))

        # I1·I2·I4 — 14일 빈 결과 → 90일
        rc, out = run([IC, "issue_list_14d_empty.json", "issue_list_90d.json", "-o", "cand.json",
                       "--evidence-out", "ev.csv"], t)
        if rc:
            F.append(("I1", "비정상 종료", out[-300:]))
        cand = json.loads((t / "cand.json").read_text(encoding="utf-8")) if not rc else []
        hdr, ev = read(t / "ev.csv") if not rc else ([], [])
        byk = {r["keyword"]: r for r in ev}
        if len(cand) != 6 or cand[:1] != ["캠핑의자"] or sum(1 for k in cand if k.replace(" ", "") == "릴렉스체어") != 1:
            F.append(("I1", f"시드 + canonical 병합 후보 5개(=6)가 아니다: {cand}", ""))
        if byk.get("릴렉스 체어", {}).get("variants") != "릴렉스 체어 / 릴렉스체어":
            F.append(("I1", "표기변형이 variants에 남지 않았다", str(byk.get("릴렉스 체어"))))
        if byk.get("캠핑의자 경량", {}).get("n_index_dates") != "2":
            F.append(("I2", "반복 인덱스일이 지속(n_index_dates)으로 세지지 않았다", ""))
        if any(r["source_checked"] for r in ev) or "[추정]" not in out:
            F.append(("I2", "AI 요약이 [추정]으로 안내되지 않았거나 source_checked가 미리 채워졌다", ""))
        if "50×1호출(결과 있음) + 2×7반환 = 64" not in out or "cost_detail 합 64" not in out or "⚠️ 결정식" in out:
            F.append(("I4", "크레딧 결정식(빈 결과 호출 0)이 cost_detail과 대조되지 않았다", out[-400:]))
        if "limit 절단" not in out or "14일 범위 안 이슈 0건 → 90일" not in out:
            F.append(("I4", "절단 경고 또는 넓힘 기록이 없다", out[-300:]))
        if hdr and hdr != doc_columns("issue_evidence.csv"):
            F.append(("I8", "SKILL.md issue_evidence.csv 컬럼 ≠ 실제 출력", f"{hdr} / {doc_columns('issue_evidence.csv')}"))

        # I8 — 스크린
        ads = [{"keyword": k, "ads_metrics": {"volume_avg": v, "volume_total": v * 12, "cpc": 0.1, "competition_index": 1}}
               for k, v in (("캠핑의자", 9000), ("릴렉스 체어", 7000), ("캠핑의자 경량", 5000), ("체어 원목 캠핑", 500),
                            ("하이백 캠핑의자", 2000))]
        (t / "ads.json").write_text(json.dumps({"data": ads}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([IC, "--screen", "ads.json", "--candidates", "cand.json", "--gate", "3000", "-o", "all_c.json",
                       "--meta", "issue_meta.json"], t)
        keep = json.loads((t / "all_c.json").read_text(encoding="utf-8")) if not rc else []
        if rc or len(keep) != 4 or "탈락 1" not in out or "미반환 1" not in out or "10×4 = 40" not in out:
            F.append(("I8", "스크린 결과(통과 4·탈락 1·미반환 1·all 40크레딧)가 다르다", out[-300:]))

        # I5·I6 — keyword_info all 응답(합성): 판정 2 · 게이트 미달 1 · 시계열 없음 1 · 미반환 1
        kw = [row("릴렉스 체어", grow(5000, 1.4)), row("캠핑의자 경량", grow(4000, 1.2)),
              row("캠핑의자", grow(20000, 1.1)), row("캠핑 테이블", grow(6000, 1.05)),
              row("체어 원목 캠핑", lambda m: 300 if m < "2026-06" else 2000),
              {"keyword": "하이백 캠핑의자", "ads_metrics": {"volume_avg": 2000}}]
        (t / "all.json").write_text(json.dumps({"data": kw}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([TS, "all.json", "-o", "sig.csv", "--evidence", "ev.csv", "--issue-meta", "issue_meta.json"], t)
        if rc:
            F.append(("I5", "trend_signals.py --evidence 비정상 종료", out[-300:]))
        else:
            _, sig = read(t / "sig.csv")
            s = {r["keyword"]: r for r in sig}
            if not s.get("릴렉스 체어", {}).get("issue_score") or s.get("캠핑 테이블", {}).get("issue_score"):
                F.append(("I5", "이슈 근거가 canonical로 결합되지 않았다(또는 엉뚱한 행에 붙었다)", ""))
            ohdr, only = read(t / "sig_issue_only.csv")
            o = {r["keyword"]: r for r in only}
            want = {"체어 원목 캠핑": "게이트 미달", "하이백 캠핑의자": "시계열 없음", "캠핑의자 수납 가방": "검색량 미반환"}
            if {k: v["reason"] for k, v in o.items()} != want:
                F.append(("I6", "«검색량 미확인» 목록·사유가 다르다", str({k: v["reason"] for k, v in o.items()})))
            if o.get("체어 원목 캠핑", {}).get("after_ref_month") != "예":
                F.append(("I6", "기준월 이후 이슈(월 그레인 지연)가 표시되지 않았다", ""))
            if any(k in want and r["grade"] for k, r in s.items()):
                F.append(("I6", "검색량 미확인 후보에 등급이 붙었다", ""))
            if ohdr != doc_columns("trend_issue_only_{category}.csv"):
                F.append(("I8", "SKILL.md 미확인 CSV 컬럼 ≠ 실제 출력", str(ohdr)))
            # I7 — 알림
            base = [WA, "sig.csv", "--category", "합성 캠핑의자", "--queried-at", "2026-09-29"]
            rc, out = run(base + ["-o", "a0.md"], t)
            if rc == 0:
                F.append(("I7", "이슈 근거가 있는데 --issue-only 없이 알림을 썼다(미판정 후보가 조용히 빠진다)", ""))
            rc, out = run(base + ["--issue-only", "sig_issue_only.csv", "-o", "a0b.md"], t)
            if rc == 0:
                F.append(("I7", "--issue-meta 없이 알림을 썼다(절단·넓힘·크레딧 사실이 빠진다)", ""))
            rc, out = run(base + ["--issue-only", "sig_issue_only.csv", "--issue-meta", "issue_meta.json", "-o", "a1.md"], t)
            md = (t / "a1.md").read_text(encoding="utf-8") if not rc else ""
            if rc or "이슈 신호만 · 검색량 미확인 (2건)" not in md or "구글 기반" not in md or "[추정" not in md \
                    or "신생·저기저 급등 후보 (1건" not in md or "limit 절단" not in md or "cost_detail 합 64" not in md:
                F.append(("I7", "알림에 미확인 절(2건)·신생·저기저 절(1건)·절단·크레딧 사실·구글 기반 한계·[추정] 라벨이 없다",
                          (out + md)[:3000]))

        # 스크린 통과분이 없어 all을 안 부른 경우 — 전부 미확인으로 남아야 한다
        (t / "none.json").write_text(json.dumps({"data": [{"keyword": "하이백 캠핑의자", "ads_metrics": {}}]},
                                                ensure_ascii=False), encoding="utf-8")
        rc, out = run([TS, "none.json", "-o", "n.csv", "--evidence", "ev.csv"], t)
        if rc or len(read(t / "n_issue_only.csv")[1]) != 5:
            F.append(("I6", "시계열이 하나도 없을 때 이슈 후보 5건이 모두 미확인으로 남지 않았다", out[-300:]))

        F += regressions_p1(t, IC, TS)

    if F:
        print(f"실패 {len(F)}건")
        for name, why, detail in F:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {str(detail)[:300]}")
        return 1
    print("통과 — issue_candidates.py · trend_signals.py --evidence · write_alert.py --issue-only 회귀 검사 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
