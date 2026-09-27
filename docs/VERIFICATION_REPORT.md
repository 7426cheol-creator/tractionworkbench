# Verification Report — Traction Workbench

자동 생성: `python verification/make_report.py` · software 0.2.0 · commit `a02e664` · 2026-09-27 · Python 3.11.15, NumPy 2.4.6, SciPy 1.17.1

> 범위: 합성(synthetic) 참조 fixture에 대한 검증(verification)입니다. 하드웨어·공급사 데이터·외부 시뮬레이터에 대한 validation은 수행하지 않았습니다. 수치 자릿수는 회귀 검산용이며 실제 제품 정확도가 아닙니다.

## 1. 요약

| 항목 | 결과 |
|---|---|
| 참조 패키지 무결성 (manifest SHA-256, 10 files) | OK |
| 독립 fixture 검산 (production 코드 미사용) | 136/136 pass (35 s) |
| Production vs golden acceptance | 21/21 pass |
| pytest | 206 passed in 71.69s (0:01:11) (72 s) |
| 데스크톱 앱 self-test (headless, `twb selftest`) | 18/18 pass (100 s) |

## 2. 독립 fixture 검산 (`verification/independent_fixture_check.py`)

Production 패키지를 import하지 않고, production과 다른 방법으로 fixture를 다시 계산했습니다: 정방향은 식 직접 대입, 역문제는 전류각 매개변수화(id = I cos φ, iq = I sin φ)와 조밀 탐색 + 경계 bisection + MTPA 접선 조건, capability는 KKT 승수·Lagrangian Hessian·오목 상한 재계산, flux map은 해석 포텐셜과 중앙차분 비교입니다.

관찰: 내부점(MTPA) golden 해 I00/I09/I10은 목적함수가 평탄해 정확한 접선 해와 최대 5.5e-6 A 차이가 있습니다 (50자리 mpmath로 확인; 허용오차 1e-3 A 이내). expected 값은 수정하지 않았습니다.

```
PASS  inverse:I00_MTPA:golden                                          |did|=2.82e-06 A |diq|=1.48e-06 A |dV|=9.40e-07 V |dP|=0.00e+00 W
PASS  inverse:I09_LOW_SPEED_REGEN:golden                               |did|=1.63e-07 A |diq|=8.55e-08 A |dV|=5.70e-08 V |dP|=4.37e-11 W
PASS  inverse:I10_STANDSTILL_TORQUE:golden                             |did|=5.54e-06 A |diq|=2.91e-06 A |dV|=8.31e-08 V |dP|=1.36e-12 W
136/136 independent fixture checks passed
```

## 3. Production vs golden acceptance (`twb acceptance`)

허용오차는 Handoff H11: 직접 대입 정규화 오차 ≤ 1e-10, golden id/iq ≤ 1e-3 A (전압 1e-3 V, 전력 0.5 W), capability bound gap ≤ max(0.1 N·m, 0.0005 × torque scale).

| group | case | metric | value | tolerance | labels | result |
|---|---|---|---:|---:|---|---|
| forward | `F00_STANDSTILL` | max normalised discrepancy | 0.000e+00 | 1e-10 | OK | PASS |
| forward | `F01_FORWARD_MOTORING` | max normalised discrepancy | 0.000e+00 | 1e-10 | OK | PASS |
| forward | `F02_VOLTAGE_AND_DC_VIOLATION` | max normalised discrepancy | 0.000e+00 | 1e-10 | OK | PASS |
| forward | `F03_REGENERATION` | max normalised discrepancy | 0.000e+00 | 1e-10 | OK | PASS |
| forward | `F04_REVERSE_MOTORING` | max normalised discrepancy | 0.000e+00 | 1e-10 | OK | PASS |
| forward | `F05_SPMSM_NO_ROTATIONAL_OR_INVERTER_LOSS` | max normalised discrepancy | 0.000e+00 | 1e-10 | OK | PASS |
| inverse | `I00_MTPA` | max |d id|, |d iq| (A) | 2.818e-06 | 0.001 | OK | PASS |
| inverse | `I01_FIELD_WEAKENING` | max |d id|, |d iq| (A) | 2.842e-13 | 0.001 | OK | PASS |
| inverse | `I02_HIGH_SPEED_MATCH` | max |d id|, |d iq| (A) | 2.274e-13 | 0.001 | OK | PASS |
| inverse | `I03_LOW_VOLTAGE_INFEASIBLE` | no solution (proof) | — | — | OK | PASS |
| inverse | `I04_HIGH_SPEED_PART_LOAD` | max |d id|, |d iq| (A) | 2.274e-13 | 0.001 | OK | PASS |
| inverse | `I05_MAX_DECLARED_SPEED` | max |d id|, |d iq| (A) | 5.684e-14 | 0.001 | OK | PASS |
| inverse | `I06_DISCHARGE_FAIL` | max |d id|, |d iq| (A) | 1.705e-13 | 0.001 | OK | PASS |
| inverse | `I07_CHARGE_FAIL` | max |d id|, |d iq| (A) | 2.558e-13 | 0.001 | OK | PASS |
| inverse | `I08_REGEN_FEASIBLE` | max |d id|, |d iq| (A) | 4.832e-13 | 0.001 | OK | PASS |
| inverse | `I09_LOW_SPEED_REGEN` | max |d id|, |d iq| (A) | 1.631e-07 | 0.001 | OK | PASS |
| inverse | `I10_STANDSTILL_TORQUE` | max |d id|, |d iq| (A) | 5.539e-06 | 0.001 | OK | PASS |
| capability | `6000 rpm / 600 V (+)` | |d T| (N*m) (certified) | 2.968e-07 | 0.288 | OK | PASS |
| capability | `12000 rpm / 600 V (+)` | |d T| (N*m) (certified) | 1.515e-07 | 0.288 | OK | PASS |
| capability | `12000 rpm / 450 V (+)` | |d T| (N*m) (certified) | 1.291e-07 | 0.288 | OK | PASS |
| capability | `12000 rpm / 600 V (-)` | |d T| (N*m) | 8.099e-08 | 0.288 | OK | PASS |

## 4. 대표 엔지니어링 질문 (`twb demo`)

| 요구 | 판정 | 사유 | 토크 여유 [N·m] | 의미 |
|---|---|---|---:|---|
| REQ-TS-012 | **PASS** | — | 2.5551 | 정적 가능. 전류 여유 204 A지만 토크 여유는 2.6 N·m |
| REQ-TS-012-LV | **FAIL** | CONSTRAINT_VIOLATION | -15.1112 | 필요조건으로 불가능 증명. 인버터를 키워도 해결 안 됨 |
| REQ-TS-012-10S | **UNKNOWN** | UNVALIDATED_DURATION | 2.5551 | 전기적으로 가능해도 지속시간 근거가 없으면 UNKNOWN |
| REQ-RG-080 | **PASS** | — | 3.7111 | 에너지 회수 회생 가능 |
| REQ-RG-100 | **FAIL** | CONSTRAINT_VIOLATION | -16.2889 | 손실을 최대로 늘려도 충전 한계 초과 (배터리 수용이 병목) |
| REQ-PK-350 | **FAIL** | CONSTRAINT_VIOLATION | -43.1711 | 축 출력만으로 방전 한계 초과 |
| REQ-RANGE | **UNKNOWN** | SAMPLED_COVERAGE | 51.8181 | 표본점 통과만으로 전 구간 PASS 아님 |
| REQ-ST-300 | **PASS** | — | 199.4377 | 등가 RMS, 효율 N/A, 지속시간 미확인 |

## 5. 데스크톱 앱 self-test (`twb selftest`)

모든 페이지를 실제 코드 경로로 실행합니다(작업은 동기 실행): 예시 8건의 판정, 운전점 탐색(클릭 정방향 평가), 궤적, 성능 곡선·맵, 설계·병목, 안전 스크리닝 4종, 열 가용성, golden acceptance, PDF 보고서, flux-map 드라이브 판정, 다크 테마. 배포 빌드(`packaging/build.py`, CI Windows job)는 같은 검사를 **동결된 실행 파일**에서 수행합니다.

| check | result | detail |
|---|---|---|
| decision:ts012_600 | PASS | verdict PASS, expected PASS |
| decision:ts012_450 | PASS | verdict FAIL, expected FAIL |
| pdf_report | PASS | 128093 bytes |
| decision:ts012_10s | PASS | verdict UNKNOWN, expected UNKNOWN |
| decision:regen_80 | PASS | verdict PASS, expected PASS |
| decision:regen_100 | PASS | verdict FAIL, expected FAIL |
| decision:dis_350 | PASS | verdict FAIL, expected FAIL |
| decision:range | PASS | verdict UNKNOWN, expected UNKNOWN |
| decision:stall | PASS | verdict PASS, expected PASS |
| explorer:forward | PASS |  |
| trajectory | PASS |  |
| performance | PASS |  |
| design | PASS |  |
| safety | PASS |  |
| thermal | PASS | 냉각수 65 °C에서 450 N·m는 약 <b>5.47 s</b> 유지 가능, 이후 <b>433.7 N·m</b> (연속)<br><span style='font-size:9pt'>claim: <b>UNKNOWN</b |
| acceptance | PASS | <span style='color:#1a7f37; font-weight:600'>21/21 PASS · manifest OK</span> · 1.39 s · verification against synthetic f |
| decision:flux_map | PASS | UNKNOWN |
| no_error_dialogs | PASS |  |

## 6. 재현 방법

```bash
pip install -e '.[gui,test]'
python verification/independent_fixture_check.py   # 독립 검산
twb acceptance                                      # production vs golden
QT_QPA_PLATFORM=offscreen python -m pytest -q      # 전체 테스트
twb selftest out/selftest                           # 데스크톱 앱 자체 검사
python verification/make_report.py                  # 이 문서 재생성
```
