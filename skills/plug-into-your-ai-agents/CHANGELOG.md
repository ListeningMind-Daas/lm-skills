# plug-into-your-ai-agents — 변경 이력

판 번호의 정본은 SKILL.md frontmatter `metadata.version` 이다. 새 판을 낼 때 두 곳을 같이 고친다.
결함은 무엇이 어떻게 틀렸고 어떻게 고쳤는지, 재발을 막는 검사와 함께 적는다 — 실데이터 실행에서 관측한 수치(검색량 · 건수 · 비율 · 순위)는 적지 않는다.

## v0.4.3 (2026-11-25 · lm-skills 1.9.0)

운영 명세에 새로 들어온 월별 검색량 엔드포인트를 도구로 켜고, 키를 보낼 주소를 검사한다.

- 새 도구 `lm_keyword_info_monthly_volume` (`POST /keyword_info/monthly_volume`) — 키워드 1~1,000개(`keywords` 배열)의 월별 시계열만 받는다. 기간 지정(`date_from`·`date_to` · `YYYY-MM` · 최대 48개월)은 이 도구에만 있다.
  과금은 **반환된 행 1개당 1**이다 — 데이터가 없는 키워드는 과금되지 않고, 요청하지 않은 표기 변형 행이 함께 오면 그 행도 과금된다. 결정식 대조는 응답의 `cost_detail.input_count`(과금된 행 수)를 쓰고(`billed_inputs`), 호출 전 추정은 요청 키워드 수 기준이라 변형 행만큼 실제가 더 클 수 있다
- `is_skills` · `skill_desc` · `response_format`은 잠금 파일에 담되 도구에는 노출하지 않는다(`hidden_params`)
- 키를 보낼 주소 검사가 없었다 — 셸에 남은 `LM_API_BASE`를 확인 없이 썼고 https가 아닌 주소도 받았다(같은 키트의 `read-search-context`를 만들다 사용자가 입력한 주소 대신 셸의 다른 서버 주소가 쓰인 것을 보고 점검했다).
  `HttpTransport` · `make_runtime_from_env`는 https 주소만 받고, `expect_host`를 주면 호스트가 다를 때 전송을 만들지 않는다. `expect_host` 없이 만들면 키를 보낼 호스트를 표준 오류에 한 번 알린다. 인자를 더했을 뿐 기존 호출은 그대로 돈다
- `lm_tools.py host [--expect <호스트>]` 추가 — 키를 보낼 호스트와 읽은 곳을 보여 준다(네트워크 없음 · 키를 출력하지 않음)
- STEP 6 · 연결 안내에 «호스트를 사용자에게 확인받고 `expect_host`로 고정»을 넣었다 · `selftest_tools.py`에 월별 검색량 도구 · https 거부 · 호스트 불일치 · `host` 명령 시험을 더했다
- 나머지 엔드포인트의 인자·필수값·단가는 바뀌지 않았다

## v0.4.2 (2026-11-23 · lm-skills 1.8.0)

- 계약 v1.3 사본 — `ads_info` 금지를 풀었다. 시계열만 필요하면 `ads_info`를 쓸 수 있고, 단가는 첫 호출 `cost_detail`로 확인한다(명세 1/키워드 · 운영 환경에서도 1로 확인).
- `lm_keyword_info`가 `ads_info`를 노출한다(`tool-policy.json` `enum_only`). `ads_info` 응답에도 `all`과 같은 시계열 주석(`data_ref_month` · `yoy_ex_last_month` · 결측 월)을 붙이고, 2단 조회 강제(`two_stage`)도 똑같이 건다.
- `selftest_tools.py` — «`ads_info`가 노출되면 실패»를 «노출되지 않으면 실패»로 뒤집었다.
- 공개 전 정리: 실데이터 실행에서 관측한 수치 기록(검색량 · 건수 · 비율 · 순위 · 실행 날짜)을 걷어냈다 — 규칙 · 결함 서술 · 합성 픽스처는 그대로다.

## v0.4.1 (2026-10-13 · lm-skills 1.7.0)

공유 스크립트 사본 갱신 — 분석 절차·산출 컬럼은 바뀌지 않았다.

- 계약 v1.2 사본 — T4 기저 게이트를 묶음·그룹 순위 문장에도 적용 · T6 여러 창(연도)을 나란히 비교하는 합계는 모든 창에 값이 있는 검색어로 모집단 고정(실습 리허설 실행 평가에서 나온 결함).
- `selftest.py` — SKILL.md가 비었거나 골격 자리표시자(⬜로 표시된 미작성 칸 등)가 남아 있으면 실패한다. 이전에는 스크립트만 돌려 빈 SKILL.md에서도 «통과»를 냈다(워크숍 사전 실행, lm-skill-creator 0.2.4)
- `check_refmonth.py`·`series_dedup.py`·`trend_yoy.py`·`selftest.py` — Windows cp949 콘솔에서 `—`·`⚠` 출력 시 UnicodeEncodeError로 죽던 것을 막는다(표준 출력을 UTF-8로). 이전 판은 cp949 콘솔 모사에서 출력 중 죽었다(`trend_yoy.py --help`·`check_refmonth.py` 실행 등)

## v0.4.0 (2026-09-30 · lm-skills 1.7.0)

카탈로그 예시 실행(dry-run만 · API 호출 0)에서 나온 결함 수정. 예시 디스패처가 스킬 밖에서
하위 클래스로 덧붙여야 했던 두 가드를 런타임으로 올렸다.

- **호출 1건 상한 `per_call_cap`** (런타임 인자 · `--per-call-cap` · 시나리오 `config`) — 런타임에 세션 예산만
  있어서 기본 limit의 cluster 한 번(추정 `150+50×101=5,200`)·path 한 번(추정 `50+40×60=2,450`)이 예산 5,000의
  대부분을 한 호출에 쓸 수 있었다. 예산 가드와 같은 사전 추정으로 막고, 원장 `block_reason=per_call_cap`,
  응답에 `max_limit`(상한 안에 드는 가장 큰 limit — 1,000이면 cluster 16 · path 7)을 준다. 기본은 꺼짐(None ·
  이전 동작 유지) · 권장 1,000 `[가정]`
- **시장 고정 `market`** (`"kr"` 같은 값 또는 `"auto"` = 첫 전송 호출의 gl · `--market`) — 본문은 «세션당 시장
  하나»인데 런타임은 gl=us·jp를 그대로 보냈다(예시 22단계 중 14번 gl=us를 예시 디스패처가 막았다). 다른 gl은
  `market_lock`으로 막는다. gl 인자가 없는 도구(usage)는 보지 않는다
- **intent_finder 단가 근거 정합** — 본문 표는 «명세 = 실측»인데 판단 파일이 `cost_unverified`라 도구 설명에
  «명세값 · 실측 대조 전 [가정]»이 붙었다. 선크림(kr) 분석의 intent_finder 호출 **전부**에서
  `cost_detail.total_cost = 30×입력 + 2×반환`이라 표시를 걷고 근거를
  `cost_verified_note`에 적었다. keyword_list 2종은 그대로 «실측 대조 전»이다(cluster keyword_list는 일치 사례가
  하나뿐이라 올리지 않았다)
- **trend 절단 문구** — limit 20에 매칭 9 · 반환 7인 응답을 «보류 — limit에 닿음»으로 적었다(원인은 매칭 >
  반환). `lm_meta.truncation_cause`(`limit` · `total` · `both`)를 더하고 문구를 «매칭 9건 중 7건만 반환 (limit
  20 미만 …)»으로 가른다. 원장 요약의 절단 줄도 «limit에 닿음 · 또는 매칭 total > 반환»으로 고쳤다
- **integer 인자 경계** — 명세에서 온 integer 인자의 minimum·maximum·default 18곳(도구 6종)이 `1.0`·`10000.0`
  같은 실수로 도구 스키마에 나갔다. 명세 정규화와 스키마 생성 둘 다에서 정수로 낸다(잠금 파일은 `sync --write`
  때 바뀐다 · 값 비교는 같아 `sync`가 변경으로 보지 않는다)
- **모의 keyword_info가 요청 키워드를 따른다** — 합성 픽스처는 `data_type`으로만 골라져 어떤 키워드를
  요청해도 같은 5행이 왔다. 픽스처 항목 `echo_keywords: true`면 행을 요청 이름에 맞추고(없는 이름은 남는 합성
  행을 이름만 바꿔 채우고 `_mock_renamed_from`에 적는다) `request_detail`·`cost_detail`을 요청 수로 맞춘다.
  번들 keyword_info 합성 픽스처 2항목에 켰다 — 번들 시나리오는 픽스처와 같은 키워드라 결과가 같다
- `sync --check-lock` — 네트워크 없이 잠금 파일만 점검(모양 · 필수 인자 · 정수 경계 · 단가 · 판단 파일과의 짝 ·
  0 정상 · 1 문제)
- 시스템 규칙 6을 `per_call_cap`·`market_lock`까지 넓혔다(10줄 유지). `selftest_tools.py`에 회귀 추가 —
  1회 상한(5,200·2,450 차단 · max_limit 16·7 · 기본 꺼짐) · 시장 고정(값 · auto · gl 없는 도구) · CLI config ·
  trend 절단 원인 두 갈래 · 정수 경계 · 모의 키워드 · check-lock · intent 설명

## v0.3.2 (2026-09-30 · lm-skills 1.6.2)

공유 스크립트 결함 수정 — 선크림(KR) 예시 실행에서 보고된 것. 저장된 분석 폴더로 0크레딧 재현 후 고쳤다.

- `trend_yoy.py` — «게이트 미만·계산 불가·같은 검색 중복 N행 제외» 한 줄을 사유별 세 수(계산 불가 · 같은 검색의 중복 · 게이트 미만)로 나눴다(benchmark-competitors 실행에서 세 사유가 한 수로 섞여 보고됐다). `--ref-month`가 비교 창의 끝 달이며 본문 분석 창에 맞출 때 준다는 것을 도움말에 적었다. 같은 keyword의 완전 중복 행도 합친다.
- `series_dedup.py` — `collapse_identical`(같은 keyword의 완전 중복 행 합치기 · 내용이 다르면 첫 행만 쓰고 경고)을 추가했다. `build_universe`·`coverage_curve`·`trend_yoy`가 먼저 이것을 거친다.
- `selftest.py` — 위 결함 여섯 가지의 회귀 검사를 추가했다(고치기 전 스크립트에서는 전부 실패함을 확인).
- 계약 v1.1 — keyword_info 1회 1,000키워드 상한과 나눠 호출한 응답의 병합법(`data`·`request_detail`·`cost_detail`·`used_credits`)을 적고, T5에 완전 중복 행 합치기(⓪)를 넣었다.

## v0.3.1 (2026-09-29 · lm-skills 1.6.0)

- 변경 이력을 SKILL.md 에서 이 파일로 옮기고, 판 번호를 frontmatter `metadata.version` 으로 옮겼다. 분석 절차·산출물은 바뀌지 않았다.
- description 을 1,091자 → 934자로 줄였다 — 에이전트 스킬 규격의 description 상한(1,024자)을 넘으면 인식을 거부하는 호스트가 있다. 빠진 것은 엔드포인트 이름 나열뿐이고 본문 «도구와 크레딧» 표에 그대로 있다.
- 본문 속 판 이력 서술을 걷어냈다 — 규칙의 근거 수치는 본문에 남기고, 이력 문장은 아래 «본문에서 옮긴 기록»으로 옮겼다.

## 이전 기록

- v0.1 (2026-09-28) — 레거시 카탈로그 카드 12를 계약 기반으로 재작성. 레거시에서 뺀 것:
  cluster_finder 단가를 «관계 수 × 50»으로 적은 식(→ 고유 노드 수 × 50으로 교체) · 호출당·
  워크플로우당 크레딧을 관측 범위로 적은 표기 · `total_cost`를 `used_credits`로 출력한 예시
  (→ 소진과 월간 누적을 분리) · 코드에 박힌 API 주소(→ `LM_API_BASE`) · 근거 없는 사용량
  대시보드 수치와 검증되지 않은 오류 코드·과금 주장(관계 0건 무과금 등)
- v0.1.1 (2026-09-28) — 계약 T5 개정 반영: `lm_keyword_info`의 `variant_groups`가 표기 목록에서
  그룹 객체(`keywords` · `volume_field` · `volume_lo` · `volume_hi` · `volume_missing`)로 바뀌었다.
  하한(최댓값)·상한(합)을 병기하고(당시 라벨은 «파생» — v0.2에서 계약의 4-Label인 `[추정]`으로 교체), 시스템 규칙 4번을 «합산»에서 «하한·상한 병기 ·
  순위는 하한 기준»으로 고쳤다. 볼륨이 없는 intent의 `variant_groups`는 표기 목록 그대로 둔다
- v0.2 (2026-09-28) — STEP 5 실측 평가 수정. ① `cluster_nodes()`가 실측 두 모양(기본
  `{"communities": {id: [kw…]}}` · `data_type=all`의 `rels` 쌍 목록)에서 노드 0을 세던 결함(P0) 수정 —
  실측 재생(선크림 · 캠핑의자 all)에서 노드를 0으로 세어 결정식이 cost_detail보다 작고 절단도 놓쳤다. 고친 뒤
  결정식 = cost_detail. 합성 cluster 픽스처를 실측 응답 원문으로 교체하고
  `assets/dryrun/real-shapes/` 회귀 추가. 절단 규칙을 «관계 행 ≥ limit 또는 노드 > limit», rels 없으면 null로.
  도구 스키마에 `data_type`·`time_point` 추가 ② T5에 `series_dedup` 연결 — `dup_of`·`dup_basis`, 그룹에
  `duplicates`, 치환 규칙(`--rules` · `variant_rules`), `not_returned`·`unrequested`. 계약상 라벨이 넷뿐이라
  «파생»을 `[추정]`으로 바꿨다. T5 상태를 ✅에서 «부분»으로 낮췄다(치환 규칙 없이는 선/썬이 안 묶인다)
  ③ 호출 전 상한: cluster `150+50×limit` → `150+50×(limit+1)`(+ `structural` 옵션), path 고정 20 →
  ⌈limit×3⌉(`--path-kw-cap` → `--path-kw-mult`) — 재생에서 옛 추정이 실제 과금보다 작았던 것을 덮는다 ④ 도구 JSON에서 뺀 커넥터를 런타임이 막는다(`tools` · `tool_not_enabled`) ⑤ 생략된
  인자를 스키마 기본값으로 채워 보낸다 · 2단 허용 집합에 함께 온 변형 포함 · 픽스처 `match`·`request_detail`로
  결정식 대조가 호출 순서에 기대지 않는다 · 도구 설명을 기본 응답 모양에 맞췄다 · «약 2개월 지연» 문구를
  «응답에서 읽는다»로. 합성 keyword_info 픽스처 월을 1개월 당겨 기준월 2026-08로 맞췄다
- v0.3 (2026-09-29) — **명세 기반 도구**로 전환. 4개 커넥터를 손으로 적던 구조를 운영 명세(v0.0.60 · 9개
  엔드포인트)에서 도구를 만드는 구조로 바꿨다. ① `assets/api/api-lock.json`(명세 정규화 — 인자·필수·단가) +
  `assets/api/tool-policy.json`(사람의 판단) 두 층 ② `sync`(대조·`--write` 반영 · 종료 코드 0/2/3)·`list` 추가.
  명세에만 있는 새 엔드포인트는 도구로 자동 추가되되 검토 전·기본 꺼짐·최악 기준 추정 ③ 도구 5종 추가 —
  `lm_cluster_keyword_list` · `lm_path_keyword_list` · `lm_serp` · `lm_trend_finder` · `lm_usage_summary`
  (주석: serp 수집 시각 · trend 빈 결과 0크레딧·total 기반 절단 · keyword_list 절단 판정 불가 · usage 계정
  집계) ④ 결정식을 «명세 단가 × 입력 + 명세 단가 × 출력»으로 일반화하고 단가를 도구 설명에 명세 버전과 함께
  자동으로 넣는다 ⑤ 스키마 밖 인자·열거값 밖 값을 `invalid_args`로, 숨긴 인자(`user_query`)를 막는다 ⑥ 원장에 `endpoint`·`spec_version` 추가 · 시스템 규칙 7 → 10줄
  ⑦ trend 기본 limit 50(API 기본 1,000 → 추정 2,050 방지). 시나리오 7 → 13단계, 예산 실례 1,125 → 1,315

## 본문에서 옮긴 기록

```text
- 이전 판(v0.1.1)의 cluster `50 × limit`과 path 고정 상한 20은 실측 과금이 추정보다 커서
  **추정이 실제보다 작았다** — v0.2에서 고쳤다
```
