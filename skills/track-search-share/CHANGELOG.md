# track-search-share — 변경 이력

판 번호의 정본은 SKILL.md frontmatter `metadata.version` 이다. 새 판을 낼 때 두 곳을 같이 고친다.
결함은 «무엇이 몇 % 틀렸다»처럼 근거 수치와 함께 적는다.

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
  한 번만 센다 — US 분모 하한 8.99M → 5.52M, Dyson SoV 32.9% → 28.4%(종전 범위 밖). `dup_of`·`dup_basis` 컬럼 추가 ·
  canonical을 `series_dedup.canon`(치환 후 공백 제거)으로 통일.
  **P1** `--probe-terms`(브랜드 단독 N + 비교 N(N−1)/2)와 크레딧 식 갱신, 수집 안 한 버킷은 0 대신 «미수집»/«미반환»
  (`brand_only_volume`·`outside_comparison_volume`) · `brand_models` 전용 짧은 브랜드(`일리`) KeyError 수정(독립 버킷) ·
  모델명만 걸린 검색은 `model_only`로 분리(`model_only_volume_*`, `--include-model-only`) — 판정 순서 exclude → 카테고리 → 모델 ·
  `--round` 쉼표 나열·호출 단위 절단(공유 `coverage_curve.py`와 같은 문법).
  **P2** 마지막 달 두 모드 부호 불일치 자동 «잠정»(`share_change_pp_other_mode`·`share_change_provisional`) · 잘린 라운드가
  공급한 버킷 전부 절단 표시(`truncated_rounds`) · `--requested` 요청↔응답 대조(미반환·표기 변경·요청하지 않은 행) ·
  "과금은 행 단위" → 요청 키워드 수 단위로 정정 · 예시 exclude가 `캡슐 호환 커피머신`을 자르던 것 수정 ·
  비율을 «하한 기준 X · 상한 기준 Y» 두 값으로 표기 · 지연 문구를 «응답에서 읽는다(관측 1개월)»로 교체
