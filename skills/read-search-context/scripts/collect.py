#!/usr/bin/env python3
"""수집 층 (이 스킬 고유 스크립트) — 설정 · 견적 · 호출 · 원본 저장 · 호출 원장.

이 스킬에서 네트워크를 쓰는 곳은 이 파일 하나다. 집계 · 리포트 스크립트는 여기서 저장한 원본만 읽는다.

공급원은 둘 중 하나다(설정에 저장하고 실행 기록에 남긴다 — 한 실행에서 섞지 않는다).
  api  이 스크립트가 DaaS API 를 직접 부른다. 서버 주소와 키는 작업 공간에서 읽는다 — 스킬 폴더에 두지 않는다.
  mcp  에이전트가 커넥터 도구로 부르고, 받은 원본 파일을 `ingest` 로 넣는다. 비용은 응답에 있으면 읽고 없으면 `--cost` 로 받는다.

주소 · 키를 찾는 순서: 사용자 설정 폴더(`~/.config/listeningmind/` 의 `api-base` · `api-key` — `setup` 이 주소를 여기 저장한다)
→ 환경변수 `LM_API_BASE` · `LM_API_KEY`(저장한 값이 없을 때만). 저장한 주소와 환경변수 주소의 호스트가 다르면 호출 전에 멈춘다(종료 코드 5) —
사용자가 저장한 주소를 쓰기로 확인하면 `setup --override-env` 로 그 환경변수 호스트를 기록하고, 환경변수 호스트가 바뀌면 다시 멈춘다.
`status` 는 주소 · 키를 어디서 읽었는지 보여 준다. 키는 화면 · 원장 · 원본 파일 어디에도 쓰지 않는다.

    python3 scripts/collect.py setup --source api --base https://api.example.com --gl kr --lang kr --limit 100 --budget 6000
    python3 scripts/collect.py status
    python3 scripts/collect.py quote --limit 100 --serp 18
    python3 scripts/collect.py init --seed 캠핑의자 --gl kr --limit 100 --budget 6000 --request "캠핑의자 앞뒤로 뭘 검색해?"
    python3 scripts/collect.py seed    --run <실행 폴더>
    python3 scripts/collect.py paths   --run <실행 폴더>
    python3 scripts/collect.py volumes --run <실행 폴더>
    python3 scripts/collect.py serp    --run <실행 폴더> --targets <실행 폴더>/out/serp_targets.csv
    python3 scripts/collect.py ingest  --run <실행 폴더> --tool path_finder --file resp.json --params '{"keyword":"캠핑의자","limit":100}' [--cost 1]

모의 실행: 환경변수 `LM_COLLECT_FIXTURES=<폴더>` 를 주면 네트워크 대신 그 폴더의 저장 응답을 읽는다(0크레딧 · selftest 가 쓴다).
"""
import argparse, csv, datetime, json, math, os, pathlib, re, sys, time, urllib.error, urllib.parse, urllib.request

SKILL = "read-search-context"
CONF_DIR = pathlib.Path(os.environ.get("LM_CONFIG_DIR") or pathlib.Path.home() / ".config" / "listeningmind")
# 크레딧 결정식의 상수 — references/lm-data-contract.md §1
PATH_IN, PATH_OUT, KW_ADS, SERP_IN = 50, 40, 1, 30
RATIO_OBSERVED = (0.56, 1.40)        # 경로당 고유 검색어 비율의 관측 범위(상한 · 하한이 아니다)
TOOLS = {"keyword_info": "keyword_info", "path_finder": "path_finder", "serp": "serp",
         "keyword_info_monthly": "keyword_info/monthly_volume"}
MONTHLY_DEFAULT = 11                 # [가정] 전년 비교 대상 — 시드 1 + 뒤 갈래 5 + 앞 갈래 5


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def work_dir():
    return pathlib.Path.cwd() / "work" / SKILL


def settings_path():
    return work_dir() / "settings.json"


def load_settings():
    f = settings_path()
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def first_line(f):
    try:
        return f.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


SAVED, ENV = "사용자 설정 폴더", "환경변수"


def host_of(url):
    return (urllib.parse.urlparse(url).hostname or "").lower() if url else ""


def resolve(env_name, file_name, norm=lambda v: v):
    """저장한 값(사용자 설정 폴더)이 먼저, 환경변수는 저장한 값이 없을 때만 쓴다.

    → {"value", "from", "env"(환경변수 값 · 없으면 ""), "differs"(둘 다 있고 서로 다른가)}
    """
    saved = norm(first_line(CONF_DIR / file_name))
    env = norm(os.environ.get(env_name, "").strip())
    value, where = (saved, SAVED) if saved else ((env, ENV) if env else ("", ""))
    return {"value": value, "from": where, "env": env, "differs": bool(saved and env and saved != env)}


def find_base():
    return resolve("LM_API_BASE", "api-base", lambda v: v.rstrip("/"))


def find_key():
    return resolve("LM_API_KEY", "api-key")


def base_conflict(base, st):
    """저장한 주소와 환경변수 주소의 호스트가 다르면 그 두 호스트를 돌려준다(사용자가 확인한 환경변수 호스트는 제외)."""
    if not base["differs"]:
        return None
    saved_h, env_h = host_of(base["value"]), host_of(base["env"])
    if saved_h == env_h or env_h == (st.get("env_base_ignored") or ""):
        return None
    return saved_h, env_h


def stop_on_conflict(base, st):
    c = base_conflict(base, st)
    if c:
        print(f"✗ 서버 주소가 두 곳에 있고 서로 다르다 — 저장한 주소 호스트 {c[0]} ({SAVED}) · 환경변수 LM_API_BASE 호스트 {c[1]}.\n"
              f"  부르지 않았다. 키가 어느 호스트로 갈지 사용자에게 확인받는다:\n"
              f"  · 저장한 주소를 쓴다 → collect.py setup --source api --base <저장한 주소> --override-env\n"
              f"  · 환경변수 주소를 쓴다 → collect.py setup --source api --base <환경변수 주소>", file=sys.stderr)
        sys.exit(5)


def check_base(url):
    """https 주소만 받는다 — 키가 이 주소로 전송된다."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or not u.hostname:
        sys.exit(f"✗ 서버 주소는 https 로 시작해야 한다 — 받은 값의 형식이 맞지 않는다")
    return u.hostname


# ───────── 설정 (온보딩) ─────────

def cmd_setup(a):
    ignored = ""
    if a.source == "api":
        if not a.base:
            sys.exit("✗ api 공급원은 --base <서버 주소> 가 필요하다 — 사용자가 알려 준 주소를 넣는다")
        host = check_base(a.base).lower()
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        (CONF_DIR / "api-base").write_text(a.base.rstrip("/") + "\n", encoding="utf-8")
        print(f"서버 주소 저장 — 호스트 {host} ({SAVED}). 키는 이 호스트로 전송된다 — 맞는지 사용자에게 확인받는다")
        env_h = host_of(os.environ.get("LM_API_BASE", "").strip())
        if env_h and env_h != host:
            if a.override_env:
                ignored = env_h
                print(f"환경변수 LM_API_BASE 의 호스트 {env_h} 는 쓰지 않는다 — 사용자가 확인함(--override-env). 환경변수 호스트가 바뀌면 다시 멈춘다")
            else:
                print(f"⚠ 환경변수 LM_API_BASE 의 호스트 {env_h} 가 저장한 주소와 다르다 — 이대로면 호출 전에 멈춘다.\n"
                      f"  저장한 주소를 쓰기로 사용자가 확인했으면 --override-env 를 붙여 다시 저장한다")
    st = {"schema": "read-search-context-settings/v1", "source": a.source,
          "defaults": {"gl": a.gl, "lang": a.lang, "limit": a.limit, "budget": a.budget},
          "saved_at": datetime.date.today().isoformat()}
    if ignored:
        st["env_base_ignored"] = ignored
    if a.key_confirmed and a.source == "api":
        st["key_host_confirmed"] = host_of(a.base)
    work_dir().mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"설정 저장 → {settings_path().relative_to(pathlib.Path.cwd())}")
    return cmd_status(a)


def cmd_status(a):
    st = load_settings()
    if not st:
        print("설정 없음 — 온보딩이 필요하다(references/onboarding.md): 공급원 → 서버 주소 → 키 → 기본값 → 맥락(선택)")
        return 2
    print(f"공급원: {st['source']} · 기본값: {json.dumps(st['defaults'], ensure_ascii=False)}")
    if st["source"] == "api":
        base, key = find_base(), find_key()
        host = host_of(base["value"])
        line = f"서버 주소: 호스트 {host} ({base['from']})" if host else "서버 주소: 없음 — setup --base 로 사용자가 알려 준 주소를 넣는다"
        if base["differs"]:
            env_h = host_of(base["env"])
            line += (f" · 환경변수 LM_API_BASE 호스트 {env_h} 는 쓰지 않는다(사용자 확인함)" if env_h == st.get("env_base_ignored")
                     else f" · 환경변수 LM_API_BASE 는 같은 호스트" if env_h == host else f" · 환경변수 LM_API_BASE 호스트 {env_h} 와 다르다")
        print(line)
        kline = f"키: 있음 ({key['from']})" if key["value"] else "키: 없음 — 사용자가 직접 사용자 설정 폴더(api-key) 또는 환경변수 LM_API_KEY 에 넣는다"
        if key["differs"]:
            kline += f" · 환경변수 LM_API_KEY 에도 다른 키가 있다 — {SAVED}의 키를 쓴다"
        print(kline)
        if host and key["value"] and base["from"] != key["from"] and st.get("key_host_confirmed") != host:
            print(f"⚠ 주소는 {base['from']}, 키는 {key['from']}에서 읽었다 — 그 키가 이 호스트용인지 사용자에게 한 번 묻는다. 맞다고 하면 setup … --key-confirmed 로 기록하고(다음부터 묻지 않는다), 아니면 이 호스트용 키를 사용자 설정 폴더에 넣게 한다")
        if base_conflict(base, st):
            c = base_conflict(base, st)
            print(f"✗ 저장한 주소({c[0]})와 환경변수 주소({c[1]})가 다르다 — 호출 전에 멈춘다. 어느 쪽을 쓸지 사용자에게 확인받는다")
            return 3
        if not (host and key["value"]):
            return 2
    ctx = pathlib.Path(__file__).resolve().parent.parent / "assets" / "context.json"
    print(f"맥락 파일: {'있음' if ctx.is_file() else '없음 — 범용으로 돈다(리포트 데이터 범위에 «회사 맥락 미설정»)'}")
    return 0


# ───────── 견적 ─────────

def plan(limit, serp, ratio=1.0, monthly=MONTHLY_DEFAULT):
    u = math.ceil(limit * ratio - 1e-9)
    return {"seed": KW_ADS * 1, "paths": PATH_IN + PATH_OUT * u, "volumes": KW_ADS * u, "monthly": KW_ADS * monthly,
            "serp": SERP_IN * serp, "unique_keywords": u,
            "total": KW_ADS + PATH_IN + PATH_OUT * u + KW_ADS * u + KW_ADS * monthly + SERP_IN * serp}


def cmd_quote(a):
    p = plan(a.limit, a.serp, a.ratio, a.monthly)
    lo, hi = (plan(a.limit, a.serp, r, a.monthly)["total"] for r in RATIO_OBSERVED)
    print(f"견적 [추정] — 경로 수 {a.limit} · SERP 검색어 {a.serp}개 · 경로당 고유 검색어 비율 r = {a.ratio} [가정]")
    print(f"  시드 확인      {KW_ADS} × 1                       = {p['seed']}")
    print(f"  경로 수집      {PATH_IN} + {PATH_OUT} × U  (U ≈ ceil({a.limit} × {a.ratio}) = {p['unique_keywords']}) = {p['paths']}")
    print(f"  검색량         {KW_ADS} × U                       = {p['volumes']}")
    print(f"  월별 검색량    {KW_ADS} × R  (R ≈ 대상 {a.monthly}개 · 변형 행이 더 올 수 있다) = {p['monthly']}")
    print(f"  검색 결과 화면 {SERP_IN} × S  (S = {a.serp} · 저장본 없으면 0)   = {p['serp']}")
    print(f"  합계 계획값    {p['total']} 크레딧")
    print(f"r 은 호출 전에 알 수 없다. 관측된 r {RATIO_OBSERVED[0]} · {RATIO_OBSERVED[1]} 로 다시 계산하면 {lo} · {hi} — 관측일 뿐 상한이 아니다.")
    print(f"serp 단가 {SERP_IN} 은 계약값이다 — 배포 환경마다 다를 수 있고 실제 비용은 호출 원장(cost_detail)에 남는다.")
    if a.budget:
        hi_r = RATIO_OBSERVED[1]
        fit = max((L for L in range(1, 1001) if plan(L, 0, hi_r, a.monthly)["total"] <= a.budget), default=0)
        print(f"상한 {a.budget}: 관측된 r {hi_r} 에서도 시드 확인 · 경로 · 검색량 · 월별 검색량이 상한 안에 드는 경로 수는 최대 {fit}개 [추정]. "
              f"경로 조회 비용은 호출 뒤에야 확정되므로 예산 가드가 그 한 번의 초과는 막지 못한다 — 이 값 이하를 제안한다.")
    return 0


# ───────── 실행 기록 · 원장 ─────────

def cmd_init(a):
    st = load_settings()
    d = st.get("defaults") or {}
    gl, limit, budget = a.gl or d.get("gl"), a.limit or d.get("limit") or 100, a.budget or d.get("budget")
    if gl not in ("kr", "us", "jp"):
        sys.exit("✗ 국가(--gl)는 kr · us · jp 중 하나 — 요청에 없으면 사용자에게 묻는다")
    if not budget:
        sys.exit("✗ 크레딧 상한(--budget)이 없다 — 견적을 보여 주고 사용자에게 받는다")
    safe = re.sub(r"[^\w가-힣ぁ-んァ-ヶ一-龥]+", "_", a.seed).strip("_") or "seed"
    base = work_dir() / f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{safe}"
    run, n = base, 1
    while run.exists():                                          # 같은 초에 두 번 만들어도 앞 실행을 덮지 않는다
        n += 1
        run = base.with_name(f"{base.name}-{n}")
    (run / "raw" / "serp").mkdir(parents=True, exist_ok=True)
    (run / "out").mkdir(exist_ok=True)
    req = {"request": a.request or "", "seed": a.seed, "gl": gl, "lang": a.lang or d.get("lang") or gl, "limit": limit,
           "budget": budget, "time_point": "curr", "source": st.get("source") or "api",
           "queried_at": datetime.date.today().isoformat(), "estimate": plan(limit, a.serp, monthly=a.monthly)["total"]}
    (run / "request.json").write_text(json.dumps(req, ensure_ascii=False, indent=1), encoding="utf-8")
    print(run)
    return 0


def load_run(run):
    run = pathlib.Path(run)
    f = run / "request.json"
    if not f.is_file():
        sys.exit(f"✗ 실행 폴더가 아니다(request.json 없음): {run}")
    return run, json.loads(f.read_text(encoding="utf-8"))


def ledger(run):
    f = run / "calls.jsonl"
    return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()] if f.is_file() else []


def spent(run):
    return sum(c.get("cost") or 0 for c in ledger(run))


def record(run, **row):
    rows = ledger(run)
    row = {"id": f"C{len(rows) + 1}", "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), **row}
    with open(run / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def guard(run, req, planned, what):
    left = req["budget"] - spent(run)
    if planned > left:
        print(f"✗ 예산 가드 — {what} 계획 {planned} > 남은 예산 {left} (상한 {req['budget']} · 누적 실측 {spent(run)}). "
              f"부르지 않았다. 범위를 줄이거나 상한을 사용자에게 다시 받는다", file=sys.stderr)
        sys.exit(4)


def returned_of(body):
    d = body.get("data") if isinstance(body, dict) else body
    return len(d) if isinstance(d, list) else 0


def call(req, tool, payload):
    """→ (응답 본문, 상태). 공급원이 api 가 아니면 부르지 않는다."""
    fx = os.environ.get("LM_COLLECT_FIXTURES")
    if req.get("source") == "api":
        stop_on_conflict(find_base(), load_settings())     # 모의 실행에서도 본다 — 실제 실행 전에 설정 문제를 잡는다
    if fx:
        idx = pathlib.Path(fx) / "serp_index.json"
        serp_map = json.loads(idx.read_text(encoding="utf-8")) if idx.is_file() else {}
        name = {"serp": serp_map.get(payload.get("keyword"), "(없음)"),
                "keyword_info": "seed_metrics.json" if len(payload.get("keywords") or []) == 1 else "metrics.json",
                "path_finder": "path.json", "keyword_info_monthly": "monthly.json"}[tool]
        f = pathlib.Path(fx) / name
        if not f.is_file():
            return {"result": "OK", "data": [], "collected_at": None, "cost_detail": {"total_cost": 0}}, "ok"
        return json.loads(f.read_text(encoding="utf-8")), "ok"
    if req.get("source") != "api":
        sys.exit(f"✗ 이 실행의 공급원은 {req.get('source')} 다 — 에이전트가 커넥터로 부른 뒤 `collect.py ingest` 로 넣는다")
    base, key = find_base()["value"], find_key()["value"]
    if not base or not key:
        sys.exit("✗ 서버 주소 또는 키가 없다 — `collect.py status` 로 확인한다")
    check_base(base)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(3):
        r = urllib.request.Request(f"{base}/{TOOLS[tool]}", data=data, method="POST",
                                   headers={"LM-API-KEY": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8")), "ok"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1)); last = f"HTTP {e.code}"; continue
            try:
                return json.loads(body), f"error {e.code}"
            except ValueError:
                return {"result": "FAILED", "reason": f"HTTP {e.code}"}, f"error {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)[:80]; time.sleep(3 * (attempt + 1))
    return {"result": "FAILED", "reason": last or "연결 실패"}, "error network"


def save(run, rel, body):
    f = run / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")      # 요청 헤더는 저장하지 않는다
    return rel


def do(run, req, tool, payload, rel, step, purpose, params):
    body, status = call(req, tool, payload)
    cd = (body.get("cost_detail") or {}) if isinstance(body, dict) else {}
    cost = cd.get("total_cost")
    if not isinstance(cost, (int, float)):
        cost = 0
    if tool == "serp" and status == "ok" and not returned_of(body):
        status = "no_snapshot"                                   # 저장본 없음 — 과금 0 · 실패가 아니다
    row = record(run, step=step, tool=tool, params=params, purpose=purpose, returned=returned_of(body), cost=cost,
                 raw=save(run, rel, body), status=status, source=req.get("source"),
                 monthly_used=body.get("used_credits") if isinstance(body, dict) else None)
    return body, row


def cmd_seed(a):
    run, req = load_run(a.run)
    guard(run, req, KW_ADS, "시드 확인")
    body, row = do(run, req, "keyword_info", {"keywords": [req["seed"]], "gl": req["gl"], "data_type": "ads_metrics"},
                   "raw/seed_metrics.json", "시드 확인", "시드가 조회되는지 · 응답 표기 확인", f"seed={req['seed']} · {req['gl']} · ads_metrics")
    rows = [r for r in body.get("data") or [] if isinstance(r, dict)]
    names = [r.get("keyword") for r in rows]
    print(f"{row['id']} 시드 확인 — 비용 {row['cost']} · 반환 {len(rows)}행 {names} [실제 데이터]")
    if not rows:
        print("시드가 응답에 없다 [데이터 공백] — 표기를 바꾸거나 진행 여부를 사용자에게 묻는다")
        return 2
    if req["seed"] not in names:
        print(f"⚠ 응답 표기가 요청과 다르다 — 요청 «{req['seed']}» · 응답 {names}. 경로에서도 표기가 바뀔 수 있다(context_paths.py 가 검사한다)")
    others = [n for n in names if n != req["seed"]]
    if req["seed"] in names and others:
        print(f"⚠ 요청하지 않은 표기 행이 함께 왔다 — {others}. 한 글자만 다른 표기(예: 썬 ↔ 선)면 STEP 3 에서 치환 규칙(--rules)으로 묶는다")
    return 0


def cmd_paths(a):
    run, req = load_run(a.run)
    guard(run, req, plan(req["limit"], 0)["paths"], "경로 수집")
    body, row = do(run, req, "path_finder", {"keyword": req["seed"], "gl": req["gl"], "time_point": "curr", "limit": req["limit"]},
                   "raw/path.json", "경로 수집", "시드를 지나는 세션 인접 경로", f"seed={req['seed']} · {req['gl']} · curr · limit {req['limit']}")
    n = row["returned"]
    print(f"{row['id']} 경로 수집 — 비용 {row['cost']} · 받은 경로 {n}개 · 누적 실측 {spent(run)} / 상한 {req['budget']} [실제 데이터]")
    if n >= req["limit"]:
        print(f"절단: 받은 경로 수가 요청 limit {req['limit']} 과 같다 — «받은 경로 기준»으로 쓴다(T7). limit 을 올려 다시 부르지 않는다(사용자 결정)")
    if not n:
        print("빈 응답 [데이터 공백] — 실패로 다시 부르지 않는다")
        return 2
    return 0


def cmd_volumes(a):
    run, req = load_run(a.run)
    paths = json.loads((run / "raw" / "path.json").read_text(encoding="utf-8")).get("data") or []
    kws = list(dict.fromkeys(k.strip() for p in paths if isinstance(p, list) for k in p if isinstance(k, str) and k.strip()))
    guard(run, req, KW_ADS * len(kws), "검색량")
    parts, data = [], []
    for i in range(0, len(kws), 1000):                       # keyword_info 는 1회 1,000개까지
        chunk = kws[i:i + 1000]
        body, row = do(run, req, "keyword_info", {"keywords": chunk, "gl": req["gl"], "data_type": "ads_metrics"},
                       f"raw/metrics_{i // 1000 + 1}.json", "검색량", "경로에 나온 검색어의 검색량", f"{len(chunk)}개 · {req['gl']} · ads_metrics")
        parts.append(body.get("cost_detail") or {"total_cost": row["cost"]}); data += body.get("data") or []
        print(f"{row['id']} 검색량 — 비용 {row['cost']} · 요청 {len(chunk)}개 · 반환 {row['returned']}행 [실제 데이터]")
    save(run, "raw/metrics.json", {"request_detail": {"keywords": kws, "gl": req["gl"], "data_type": "ads_metrics"},
                                   "cost_detail": {"total_cost": sum(p.get("total_cost") or 0 for p in parts), "parts": parts}, "data": data})
    print(f"누적 실측 {spent(run)} / 상한 {req['budget']}")
    return 0


def cmd_monthly(a):
    """전년 동기 비교용 월별 검색량 — keyword_info/monthly_volume 1회(1 × 반환 행). 기간을 주지 않아 제공 범위 전체(최근 4년)를 받는다."""
    run, req = load_run(a.run)
    kws = [t["keyword"] for t in csv.DictReader(open(a.targets, encoding="utf-8-sig"))][:1000]
    if not kws:
        print("월별 검색량 대상이 없다 — 건너뛴다")
        return 0
    guard(run, req, KW_ADS * len(kws), "월별 검색량")
    body, row = do(run, req, "keyword_info_monthly", {"keywords": kws, "gl": req["gl"]}, "raw/monthly.json",
                   "월별 검색량", "시드와 앞뒤 갈래의 전년 동기 비교", f"{len(kws)}개 · {req['gl']} · 기간 전체")
    got = {r.get("keyword") for r in body.get("data") or [] if isinstance(r, dict)}
    want = set(kws)                      # 요청한 검색어의 행만 집계에 넘긴다(T5) — 원본(raw/monthly.json)은 그대로 둔다
    (run / "out").mkdir(exist_ok=True)
    (run / "out" / "monthly_targets.json").write_text(json.dumps(
        {"source": "raw/monthly.json", "data": [r for r in body.get("data") or [] if isinstance(r, dict) and r.get("keyword") in want]},
        ensure_ascii=False), encoding="utf-8")
    print(f"{row['id']} 월별 검색량 — 비용 {row['cost']} · 요청 {len(kws)}개 · 반환 {row['returned']}행 [실제 데이터]")
    miss = [k for k in kws if k not in got]
    extra = sorted(got - set(kws))
    if miss:
        print(f"  돌아오지 않은 검색어 {len(miss)}개 [데이터 공백] — 과금되지 않는다: {miss[:6]}")
    if extra:
        print(f"  요청하지 않은 표기 행 {len(extra)}개 — 과금된다 · 집계에서 더하지 않는다(T5): {extra[:6]}")
    print(f"  집계용(요청한 검색어만) → out/monthly_targets.json — check_refmonth.py · trend_yoy.py 에는 이 파일을 넘긴다")
    print(f"누적 실측 {spent(run)} / 상한 {req['budget']}")
    return 0


def cmd_serp(a):
    run, req = load_run(a.run)
    targets = list(csv.DictReader(open(a.targets, encoding="utf-8-sig")))
    left = req["budget"] - spent(run)
    can = max(0, left // SERP_IN)
    if can < len(targets):
        print(f"⚠ 남은 예산 {left} 으로 SERP {can}/{len(targets)}개만 부른다 — 못 부른 검색어는 리포트에 [데이터 공백]으로 남는다: "
              f"{[t['code'] for t in targets[can:]]}")
    shown_rate, skipped = False, []
    for t in targets[:can]:
        if (run / "raw" / "serp" / f"{t['code']}.json").is_file():
            skipped.append(t["code"]); continue
        body, row = do(run, req, "serp", {"keyword": t["keyword"], "gl": req["gl"], "num": 10},
                       f"raw/serp/{t['code']}.json", "검색 결과 화면", f"{t['code']} 검색 목적 근거", f"{t['code']} · {req['gl']} · num 10")
        note = "저장본 없음 [데이터 공백]" if not row["returned"] else f"수집일 {str(body.get('collected_at') or '')[:10]}"
        print(f"{row['id']} {t['code']} — 비용 {row['cost']} · {note}")
        if row["returned"] and row["cost"] != SERP_IN and not shown_rate:
            print(f"⚠ serp 실측 단가 {row['cost']} ≠ 계약 {SERP_IN} — 이 환경의 단가가 다르다. 견적은 계약값 기준이었다(원장은 실측)")
            shown_rate = True
    if skipped:
        print(f"원본이 이미 있어 부르지 않은 대상 {len(skipped)}개: {skipped} (0크레딧)")
    print(f"누적 실측 {spent(run)} / 상한 {req['budget']}")
    return 0


def cmd_ingest(a):
    """커넥터(mcp)로 받은 원본을 실행 폴더에 넣는다 — 원본을 요약 · 발췌하지 않은 파일이어야 한다."""
    run, req = load_run(a.run)
    try:
        body = json.loads(pathlib.Path(a.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"✗ 파일이 올바른 JSON 이 아니다 — {e}")
    if isinstance(body, list):
        body = {"result": "OK", "data": body}
    cost = (body.get("cost_detail") or {}).get("total_cost")
    if not isinstance(cost, (int, float)):
        if a.cost is None:
            sys.exit("✗ 응답에 cost_detail 이 없다 — 이 호출의 비용을 --cost 로 준다(모르면 사용자에게 묻는다. 0 으로 채우지 않는다)")
        cost = a.cost
    if a.expect is not None and returned_of(body) != a.expect:
        sys.exit(f"✗ 레코드 {returned_of(body)}건 ≠ 기대 {a.expect}건 — 옮기다 빠졌다. 넣지 않았다")
    rel = {"path_finder": "raw/path.json", "keyword_info": "raw/metrics.json",
           "keyword_info_monthly": "raw/monthly.json"}.get(a.tool) or f"raw/serp/{a.code}.json"
    if a.tool == "serp" and not a.code:
        sys.exit("✗ serp 는 --code E1 처럼 대상 코드를 준다")
    if a.tool == "keyword_info" and a.seed_check:
        rel = "raw/seed_metrics.json"
    if a.collected_at and not body.get("collected_at"):
        body["collected_at"] = a.collected_at
    row = record(run, step="넣기", tool=a.tool, params=a.params, purpose="커넥터로 받은 원본 넣기", returned=returned_of(body),
                 cost=cost, raw=save(run, rel, body), status="ok", source="mcp", monthly_used=body.get("used_credits"))
    print(f"{row['id']} 넣기 — {a.tool} · 레코드 {row['returned']}건 · 비용 {cost} → {rel}")
    return 0


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="read-search-context 수집 층")
    s = ap.add_subparsers(dest="cmd", required=True)
    x = s.add_parser("setup"); x.add_argument("--source", choices=["api", "mcp"], required=True); x.add_argument("--base")
    x.add_argument("--override-env", action="store_true", help="환경변수 LM_API_BASE 와 다를 때 저장한 주소를 쓰기로 사용자가 확인함")
    x.add_argument("--key-confirmed", action="store_true", help="주소와 키를 다른 곳에서 읽어도 그 키가 이 호스트용이라고 사용자가 확인함")
    x.add_argument("--gl", choices=["kr", "us", "jp"]); x.add_argument("--lang"); x.add_argument("--limit", type=int, default=100)
    x.add_argument("--budget", type=int)
    s.add_parser("status")
    x = s.add_parser("quote"); x.add_argument("--limit", type=int, default=100); x.add_argument("--serp", type=int, default=18)
    x.add_argument("--monthly", type=int, default=MONTHLY_DEFAULT, help="월별 검색량 대상 수(기본 11)")
    x.add_argument("--budget", type=int, help="크레딧 상한 — 주면 상한 안에 드는 경로 수를 함께 낸다")
    x.add_argument("--ratio", type=float, default=1.0)
    x = s.add_parser("init"); x.add_argument("--seed", required=True); x.add_argument("--gl"); x.add_argument("--lang")
    x.add_argument("--limit", type=int); x.add_argument("--budget", type=int); x.add_argument("--serp", type=int, default=18)
    x.add_argument("--monthly", type=int, default=MONTHLY_DEFAULT); x.add_argument("--request")
    for name in ("seed", "paths", "volumes"):
        s.add_parser(name).add_argument("--run", required=True)
    x = s.add_parser("monthly"); x.add_argument("--run", required=True); x.add_argument("--targets", required=True)
    x = s.add_parser("serp"); x.add_argument("--run", required=True); x.add_argument("--targets", required=True)
    x = s.add_parser("ingest"); x.add_argument("--run", required=True); x.add_argument("--tool", choices=list(TOOLS), required=True)
    x.add_argument("--file", required=True); x.add_argument("--params", required=True); x.add_argument("--cost", type=float); x.add_argument("--expect", type=int)
    x.add_argument("--code"); x.add_argument("--seed-check", action="store_true"); x.add_argument("--collected-at")
    a = ap.parse_args()
    return {"setup": cmd_setup, "status": cmd_status, "quote": cmd_quote, "init": cmd_init, "seed": cmd_seed, "paths": cmd_paths,
            "volumes": cmd_volumes, "monthly": cmd_monthly, "serp": cmd_serp, "ingest": cmd_ingest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
