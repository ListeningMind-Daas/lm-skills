# track-search-share — 변경 이력

판 번호의 정본은 SKILL.md frontmatter `metadata.version` 이다. 새 판을 낼 때 두 곳을 같이 고친다.
결함은 무엇이 어떻게 틀렸고 어떻게 고쳤는지, 재발을 막는 검사와 함께 적는다 — 실데이터 실행에서 관측한 수치(검색량 · 건수 · 비율 · 순위)는 적지 않는다.

## v0.3.4 (2026-11-23 · lm-skills 1.8.0)

- 계약 v1.3 사본 — `ads_info` 금지를 풀었다. 시계열만 필요하면 `ads_info`를 쓸 수 있고, 단가는 첫 호출 `cost_detail`로 확인한다(명세 1/키워드 · 운영 환경에서도 1로 확인).
- SKILL.md의 «`ads_info`는 쓰지 않는다» 문장을 새 규칙으로 바꿨다 — 분석 절차·산출 컬럼은 바뀌지 않았다.
- 공개 전 정리: 실데이터 실행에서 관측한 수치 기록(검색량 · 건수 · 비율 · 순위 · 실행 날짜)을 걷어냈다 — 규칙 · 결함 서술 · 합성 픽스처는 그대로다.

## v0.3.3 (2026-10-13 · lm-skills 1.7.0)

공유 스크립트 사본 갱신 — 분석 절차·산출 컬럼은 바뀌지 않았다.

- 계약 v1.2 사본 — T4 기저 게이트를 묶음·그룹 순위 문장에도 적용 · T6 여러 창(연도)을 나란히 비교하는 합계는 모든 창에 값이 있는 검색어로 모집단 고정(실습 리허설 실행 평가에서 나온 결함).
- `selftest.py` — SKILL.md가 비었거나 골격 자리표시자(⬜로 표시된 미작성 칸 등)가 남아 있으면 실패한다. 이전에는 스크립트만 돌려 빈 SKILL.md에서도 «통과»를 냈다(워크숍 사전 실행, lm-skill-creator 0.2.4)
- `build_universe.py`·`check_refmonth.py`·`coverage_curve.py`·`series_dedup.py`·`trend_yoy.py`·`selftest.py` — Windows cp949 콘솔에서 `—`·`⚠` 출력 시 UnicodeEncodeError로 죽던 것을 막는다(표준 출력을 UTF-8로). 이전 판은 cp949 콘솔 모사에서 출력 중 죽었다(`trend_yoy.py --help`·`check_refmonth.py` 실행 등)

## v0.3.2 (2026-09-30 · lm-skills 1.6.2)

공유 스크립트 결함 수정 — 선크림(KR) 예시 실행에서 보고된 것. 저장된 분석 폴더로 0크레딧 재현 후 고쳤다.

- `build_universe.py` — keyword_info 응답 안에서 같은 keyword·같은 내용의 행이 두 번 오면 두 행 모두 대표(`dup_of` 빈칸)가 되어 그룹 상한(합)에 이중으로 들어갔다. 완전히 같은 행은 하나로 합치고 «완전 중복 행 N행 합침»을 출력한다. 이 결함은 그룹 상한만 부풀리고 하한은 건드리지 않는다.
- `coverage_curve.py` — ① 판정이 «보류»인데 «논브랜드 기여율 N% — 전체 곡선과 함께 수렴»이 함께 찍히던 문구 모순을 고쳤다(전체가 보류면 논브랜드도 판정하지 않는다 · 전체·논브랜드 네 경우를 따로 쓴다). ② 시드별 호출을 `--round` 여러 개로 넘기면 마지막 호출 한 건으로 판정해 호출 순서에 결과가 달렸다(validate-product-ideas 실행: 같은 호출 묶음을 순서만 바꾸면 «도달»과 «한 라운드 더»가 갈렸다). 라벨 첫 `-` 앞이 같은 연속 라운드(`R1-a`·`R1-b`)는 한 판정 라운드로 묶고, intent_finder 응답의 `request_detail`로 같은 확장 단계가 따로 넘겨진 것을 감지하면 «보류»를 낸다 — 그 사례는 묶음 판정으로 «한 라운드 더»가 된다. ③ 누적 볼륨이 유니버스 합보다 작은 이유(라운드 파일에 없는 canonical 그룹 — 요청하지 않은 변형 행 등)를 차이 값과 함께 출력한다.
- `trend_yoy.py` — «게이트 미만·계산 불가·같은 검색 중복 N행 제외» 한 줄을 사유별 세 수(계산 불가 · 같은 검색의 중복 · 게이트 미만)로 나눴다(benchmark-competitors 실행에서 세 사유가 한 수로 섞여 보고됐다). `--ref-month`가 비교 창의 끝 달이며 본문 분석 창에 맞출 때 준다는 것을 도움말에 적었다. 같은 keyword의 완전 중복 행도 합친다.
- `series_dedup.py` — `collapse_identical`(같은 keyword의 완전 중복 행 합치기 · 내용이 다르면 첫 행만 쓰고 경고)을 추가했다. `build_universe`·`coverage_curve`·`trend_yoy`가 먼저 이것을 거친다.
- `selftest.py` — 위 결함 여섯 가지의 회귀 검사를 추가했다(고치기 전 스크립트에서는 전부 실패함을 확인).
- 계약 v1.1 — keyword_info 1회 1,000키워드 상한과 나눠 호출한 응답의 병합법(`data`·`request_detail`·`cost_detail`·`used_credits`)을 적고, T5에 완전 중복 행 합치기(⓪)를 넣었다.
- SKILL.md — `--round`를 나눠 넘길 때 라벨의 첫 `-` 앞을 같게 두는 규칙을 적었다.

## v0.3.1 (2026-09-29 · lm-skills 1.6.0)

- 변경 이력을 SKILL.md 에서 이 파일로 옮기고, 판 번호를 frontmatter `metadata.version` 으로 옮겼다. 절차·스크립트·산출물은 바뀌지 않았다.

## 이전 판 요약 (옮기기 전 SKILL.md 머리 메모)

> **v0.3** · 레거시 카탈로그 06 «진짜 시장 점유율 추적»을 데이터 계약 기준으로 재생성했다.

## 이전 기록

- v0.1 — 레거시 카탈로그 06을 `lm-skill-creator` 골격으로 재생성. 레거시 대비:
  «진짜/정확한 시장 점유율» 표현 삭제(검색량 기준 근사로 교체) · 정의 없는 `trend_3m` 컬럼을 전년 동기 창 점유 변화로 교체 ·
  근거 없는 `cluster_finder` 중복 자동 식별 삭제(결정 규칙 균등 분배로 교체) · 관측 불가한 «신규 브랜드 30일 미만» 공백 기준 삭제 ·
  크레딧 범위 표기를 결정식 + 2단 조회로 교체 · 카테고리 밖 브랜드 단독 검색 분리 추가 · 익명 예시를 커피머신 KR 예시로 교체
- v0.2 (2026-09-28) — 계약 T5 개정 반영: 표기변형 그룹 볼륨·점유율을 하한(최댓값)·상한(합)으로 병기
  (`counted`·`group_volume_rowsum`·`volume_12m`·`volume_12m_rowsum`·`sov_pct`·`sov_pct_branded_only`·`multi_brand_volume` →
  `group_rep`·`group_volume_lo/hi`·`volume_12m_lo/hi`·`sov_pct_lo/hi`·`sov_pct_branded_only_lo/hi`·`multi_brand_volume_lo/hi`) ·
  `--group-agg` 단일 선택 삭제 · 순위 `rank_lo/hi`·`rank_flip` 추가 · 점유 변화 기준 `series_basis`(대표 행·하한 기준) 추가
- v0.3 (2026-09-28) — STEP 5 실데이터 평가(KR 캡슐 커피머신 · US 스틱청소기) 수정 라운드.
  **P0** 같은 검색의 중복(어순·복수형·동의어 변형의 지표·시계열 완전 일치)을 `series_dedup.dedup_map`으로 먼저 걸러
  한 번만 센다 — 중복을 두 번 세면 US 분모와 브랜드 SoV가 종전 하한~상한 범위 밖으로 움직였다. `dup_of`·`dup_basis` 컬럼 추가 ·
  canonical을 `series_dedup.canon`(치환 후 공백 제거)으로 통일.
  **P1** `--probe-terms`(브랜드 단독 N + 비교 N(N−1)/2)와 크레딧 식 갱신, 수집 안 한 버킷은 0 대신 «미수집»/«미반환»
  (`brand_only_volume`·`outside_comparison_volume`) · `brand_models` 전용 짧은 브랜드(`일리`) KeyError 수정(독립 버킷) ·
  모델명만 걸린 검색은 `model_only`로 분리(`model_only_volume_*`, `--include-model-only`) — 판정 순서 exclude → 카테고리 → 모델 ·
  `--round` 쉼표 나열·호출 단위 절단(공유 `coverage_curve.py`와 같은 문법).
  **P2** 마지막 달 두 모드 부호 불일치 자동 «잠정»(`share_change_pp_other_mode`·`share_change_provisional`) · 잘린 라운드가
  공급한 버킷 전부 절단 표시(`truncated_rounds`) · `--requested` 요청↔응답 대조(미반환·표기 변경·요청하지 않은 행) ·
  "과금은 행 단위" → 요청 키워드 수 단위로 정정 · 예시 exclude가 `캡슐 호환 커피머신`을 자르던 것 수정 ·
  비율을 «하한 기준 X · 상한 기준 Y» 두 값으로 표기 · 지연 문구를 «응답에서 읽는다»로 교체
