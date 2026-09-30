# plug-into-your-ai-agents — 변경 이력

판 번호의 정본은 SKILL.md frontmatter `metadata.version` 이다. 새 판을 낼 때 두 곳을 같이 고친다.
결함은 «무엇이 몇 % 틀렸다»처럼 근거 수치와 함께 적는다.

## v0.3.2 (2026-09-30 · lm-skills 1.6.2)

공유 스크립트 결함 수정 — 2026-09-30 선크림(KR) 예시 실행에서 보고된 것. 저장된 분석 폴더로 0크레딧 재현 후 고쳤다.

- `trend_yoy.py` — «게이트 미만·계산 불가·같은 검색 중복 N행 제외» 한 줄을 사유별 세 수(계산 불가 · 같은 검색의 중복 · 게이트 미만)로 나눴다(benchmark-competitors 실측: 제외 33행 = 계산 불가 1 + 게이트 미만 32). `--ref-month`가 비교 창의 끝 달이며 본문 분석 창에 맞출 때 준다는 것을 도움말에 적었다. 같은 keyword의 완전 중복 행도 합친다.
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
  실측 재생 선크림 limit 10: 노드 0 → 11 · 결정식 150 → 700(= cost_detail) · 절단 false → true, 캠핑의자
  limit 50(all): 노드 0 → 50 · 150 → 2,650(=). 합성 cluster 픽스처를 실측 응답 원문으로 교체하고
  `assets/dryrun/real-shapes/` 회귀 추가. 절단 규칙을 «관계 행 ≥ limit 또는 노드 > limit», rels 없으면 null로.
  도구 스키마에 `data_type`·`time_point` 추가 ② T5에 `series_dedup` 연결 — `dup_of`·`dup_basis`, 그룹에
  `duplicates`, 치환 규칙(`--rules` · `variant_rules`), `not_returned`·`unrequested`. 계약상 라벨이 넷뿐이라
  «파생»을 `[추정]`으로 바꿨다. T5 상태를 ✅에서 «부분»으로 낮췄다(치환 규칙 없이는 선/썬이 안 묶인다)
  ③ 호출 전 상한: cluster `150+50×limit` → `150+50×(limit+1)`(+ `structural` 옵션), path 고정 20 →
  ⌈limit×3⌉(`--path-kw-cap` → `--path-kw-mult`). 재생: cluster 650 → 700(실제 700), path limit 20 850 →
  2,450(실제 1,050) ④ 도구 JSON에서 뺀 커넥터를 런타임이 막는다(`tools` · `tool_not_enabled`) ⑤ 생략된
  인자를 스키마 기본값으로 채워 보낸다 · 2단 허용 집합에 함께 온 변형 포함 · 픽스처 `match`·`request_detail`로
  결정식 대조가 호출 순서에 기대지 않는다 · 도구 설명을 기본 응답 모양에 맞췄다 · «약 2개월 지연» 문구를
  «응답에서 읽는다(관측 1개월)»로. 합성 keyword_info 픽스처 월을 1개월 당겨 기준월 2026-08로 맞췄다
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
- 이전 판(v0.1.1)의 cluster `50 × limit`과 path 고정 상한 20은 실측 700 > 추정 650, 1,050 > 추정 850으로
  **추정이 실제보다 작았다** — v0.2에서 고쳤다
```
