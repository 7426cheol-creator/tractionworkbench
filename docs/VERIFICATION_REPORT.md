# Verification Report — Traction Workbench

자동 생성: `python verification/make_report.py` · software 0.5.0 · commit `8e74a15` · 2026-09-29 · Python 3.11.15, NumPy 2.4.6, SciPy 1.17.1

> 범위: 합성(synthetic) 참조 fixture에 대한 검증(verification)입니다. 하드웨어·공급사 데이터·외부 시뮬레이터에 대한 validation은 수행하지 않았습니다. 수치 자릿수는 회귀 검산용이며 실제 제품 정확도가 아닙니다.

## 1. 요약

| 항목 | 결과 |
|---|---|
| 참조 패키지 무결성 (manifest SHA-256, 10 files) | OK |
| 독립 fixture 검산 (production 코드 미사용) | 136/136 pass (27 s) |
| Production vs golden acceptance | 21/21 pass |
| pytest | 909 passed, 2 warnings in 604.68s (0:10:04) (618 s) |
| 데스크톱 앱 self-test (headless, `twb selftest`) | 92/92 pass (160 s) |
| MathWorks 이식 패키지 (`twb mathworks`) | parity PASS — 80 PASS · 0 FAIL · 0 ERROR · 1 NOT_SUPPORTED; GNU Octave 8.4.0 - MATLAB-language proxy; MATLAB / Simulink not run (4 s) |

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
| REQ-RANGE | **PASS** | — | 51.8181 | 저전압 끝점 + 단조성 조건으로 전 구간 입증 (표본만으로는 PASS 아님) |
| REQ-ST-300 | **PASS** | — | 199.4377 | 등가 RMS, 효율 N/A, 지속시간 미확인 |

## 5. 데스크톱 앱 self-test (`twb selftest`)

모든 페이지를 실제 코드 경로로 실행합니다(작업은 동기 실행): 예시 8건의 판정, 운전점 탐색(클릭 정방향 평가), 궤적, 성능 곡선·맵, 설계·병목, 안전 스크리닝 4종, 열 가용성, golden acceptance, PDF 보고서, flux-map 드라이브 판정, 다크 테마. 배포 빌드(`packaging/build.py`, CI Windows job)는 같은 검사를 **동결된 실행 파일**에서 수행합니다.

| check | result | detail |
|---|---|---|
| decision:ts012_600 | PASS | verdict PASS, expected PASS |
| decision:ts012_450 | PASS | verdict FAIL, expected FAIL |
| pdf_report | PASS | 159429 bytes |
| decision:ts012_10s | PASS | verdict UNKNOWN, expected UNKNOWN |
| decision:regen_80 | PASS | verdict PASS, expected PASS |
| decision:regen_100 | PASS | verdict FAIL, expected FAIL |
| decision:dis_350 | PASS | verdict FAIL, expected FAIL |
| decision:range | PASS | verdict PASS, expected PASS |
| decision:stall | PASS | verdict PASS, expected PASS |
| decision:pwm_risk | PASS | EVALUATED |
| decision:battery_ocv | PASS | RESOLVED |
| explorer:forward | PASS |  |
| reading:explorer | PASS | 1 reading(s) |
| trajectory | PASS |  |
| reading:trajectory | PASS | 1 reading(s) |
| performance | PASS |  |
| reading:performance | PASS | 1 reading(s) |
| design | PASS |  |
| reading:design | PASS | 2 reading(s) |
| reading:requirement_set | PASS | 2 reading(s) |
| requirement_set | PASS | {'PASS': 3, 'FAIL': 1, 'UNKNOWN': 1, 'total': 5, 'by_class': {'pass': 3, 'violation': 1, 'not_rated': 0, 'missing_input' |
| requirement_set:candidates | PASS | {'charge 150 kW': (['REQ-B'], []), 'current 250 A': ([], ['REQ-A', 'REQ-C', 'REQ-D', 'REQ-E'])} |
| requirement_set:open | PASS | REQ-B |
| safety | PASS |  |
| reading:safety:ftti | PASS | 1 reading(s) |
| reading:safety:dclink | PASS | 3 reading(s) |
| reading:safety:safe_state | PASS | 1 reading(s) |
| thermal | PASS | 냉각수 입구 65 °C (유량 10 L/min, EG 50%)에서 450 N·m는 약 <b>4.24 s</b> 유지 가능, 이후 <b>426 N·m</b> (연속)<br><span style='font-size:9p |
| thermal:repeated_load | PASS | UNKNOWN |
| reading:thermal | PASS | 2 reading(s) |
| thermal:coolant_flow | PASS | 10 L/min 4.239895719475726 s, 5 L/min 1.4845071749499539 s |
| schematics | PASS |  |
| protection:ov | PASS | INFEASIBLE ['PROT-01', 'PROT-04', 'PROT-02', 'PROT-03', 'PROT-05', 'PROT-06', 'PROT-07', 'PROT-08', 'PROT-09'] |
| protection:ot | PASS |  |
| reading:protection | PASS | 1 reading(s) |
| protection:asc | PASS | screening indicates the requirement(s) ASC-RMS are exceeded - confirm with a qualified nonlinear fault-domain model befo |
| reading:protection:asc | PASS | 1 reading(s) |
| power:module | PASS |  |
| power:ripple | PASS |  |
| power:lifetime | PASS |  |
| reading:power:module | PASS | 1 reading(s) |
| reading:power:ripple | PASS | 1 reading(s) |
| reading:power:lifetime | PASS | 1 reading(s) |
| oew:point | PASS | FEASIBLE |
| oew:geometry | PASS | {'admissible_pairs': 20, 'hull_inradius_V': 400.0} |
| oew:compare | PASS |  |
| hev:joint | PASS |  |
| hev:crank | PASS |  |
| hev:rejection | PASS |  |
| hev:planetary | PASS |  |
| reading:oew | PASS | 2 reading(s) |
| reading:hev | PASS | 4 reading(s) |
| emi:screening | PASS | SCREENING - predicted exceedance up to 68.9 dB at 3.819 MHz (exact over the covered band) |
| emi:oew_cm | PASS |  |
| reading:emi | PASS | 1 reading(s) |
| reading:emi:oew | PASS | 1 reading(s) |
| efficiency:five_boundaries | PASS | {'inverter': 'DEFINED', 'motor': 'DEFINED', 'inverter_motor': 'DEFINED', 'reducer': 'DEFINED', 'edrive': 'DEFINED'} |
| efficiency:maps | PASS |  |
| efficiency:mission | PASS |  |
| efficiency:module_ab | PASS | ['B_LOWER_LOSS', 'B_LOWER_LOSS', 'B_LOWER_LOSS', 'B_LOWER_LOSS'] |
| reading:efficiency | PASS | 3 reading(s) |
| reading:efficiency:module_ab | PASS | 1 reading(s) |
| pwm:policies | PASS | {'fixed 10 kHz': [], 'light-load 8 kHz': [], 'thermal fallback 6 kHz': ['current-loop phase margin 36.33 vs limit 45']} |
| pwm:transition | PASS |  |
| pwm:ripple | PASS |  |
| pwm:sampling_transition | PASS | {'declared': 0.0, 'bumpless (volts, Ki*Ts remapped)': 0.0, 'error-sum integrator, Ki*Ts remapped': 90.93442529058187, 'i |
| antijerk:variants | PASS | {'off': 'INFEASIBLE', 'shaping': 'INFEASIBLE', 'feedback': 'INFEASIBLE', 'combined': 'FEASIBLE'} |
| antijerk:stability | PASS |  |
| reading:pwm | PASS | 4 reading(s) |
| reading:driveline | PASS | 2 reading(s) |
| machine:trade | PASS | {'ref': 'UGO back-EMF', 'N-10%': 'high-speed torque @ min Vdc', 'N+10%': 'UGO back-EMF', 'L+20%': 'UGO back-EMF', 'PM-10 |
| machine:winding | PASS | 0.9330127018922193 |
| machine:k_turns_to_trade | PASS |  |
| machine:sizing | PASS |  |
| reading:machine | PASS | 1 reading(s) |
| reading:machine:winding | PASS | 1 reading(s) |
| reading:machine:sizing | PASS | 1 reading(s) |
| project:identity | PASS | OK |
| project:usage | PASS | ['asc', 'concept_sizing', 'decision', 'decision-env', 'design-dom', 'design-sweep', 'discharge', 'driveline', 'driveline |
| project:switch | PASS | ⚠ <b>입력이 바뀜</b> — 화면의 결과는 바뀌기 전 입력으로 계산됐습니다: <b>EMI</b> (데드타임 1.500 µs → 1.200 µs) · 다시 실행: Ctrl+Enter<br>⚠ <b>프로젝트 데이터가 |
| project:restore | PASS |  |
| datasheet:module | PASS | Example Semiconductor (fictitious) EXM-750-820 datasheet rev 0.1 (synthetic format example) |
| datasheet:entry_forms | PASS | {'motor': True, 'module': True, 'capacitor': True, 'gate_edges': True} |
| datasheet:entry_motor | PASS | EXMOT-200 |
| mathworks:package | PASS | ['no target report yet: every target stage is NOT_RUN'] |
| guide | PASS |  |
| acceptance | PASS | <span style='color:#1a7f37; font-weight:600'>21/21 PASS · manifest OK</span> · 1.34 s · verification against synthetic f |
| exchange:package | PASS |  |
| decision:flux_map | PASS | UNKNOWN |
| progress:engine_steps | PASS | 80 messages, 75 in the capability scan; e.g. 요구 판정: 1/2 판정 · 토크 능력 130/227 (스캔) |
| workspace:roundtrip | PASS | 15 pages, 97 KB |
| no_error_dialogs | PASS |  |

## 6. MathWorks 이식 패키지 (`twb mathworks`)

Python reference(층 2)의 값과 수용된 원천(층 1: golden·계약 수식·map 정의)의 oracle 값을 가진 case를, 패키지에 든 native MATLAB 코드가 다시 계산해 둘 모두와 비교합니다. 대상 보고서는 Python이 원시 값에서 다시 판정합니다. GNU Octave 실행은 MATLAB 언어 호환 proxy이며 MATLAB 본체·Simulink·System Composer 단계는 NOT_RUN입니다.

| 단계 | 결과 |
|---|---|
| 패키지 | fingerprint `be7bdf7581011f7b` · case {'forward': 48, 'flux_lookup': 21, 'requirement_witness': 12} · 층 2 ↔ 층 1 불일치 0 |
| 패키지 검사 | PASS |
| 대상 환경 | GNU Octave 8.4.0 - MATLAB-language proxy; MATLAB / Simulink not run |
| stage `package_check` | PASS |
| stage `native_evaluator` | PASS |
| stage `self_checks` | PASS |
| stage `simulink_harness` | NOT_RUN |
| stage `system_composer` | NOT_RUN |
| stage `dictionary_conflicts` | NOT_RUN |
| stage `physical_validation` | NOT_CLAIMED |
| parity (재계산) | PASS {'PASS': 80, 'FAIL': 0, 'ERROR': 0, 'NOT_SUPPORTED': 1, 'MISSING': 0} |
| 물리 검증 | NOT_CLAIMED (implementation verification V0-V4; not a physical qualification) |

## 7. 재현 방법

```bash
pip install -e '.[gui,test]'
python verification/independent_fixture_check.py   # 독립 검산
twb acceptance                                      # production vs golden
QT_QPA_PLATFORM=offscreen python -m pytest -q      # 전체 테스트
twb selftest out/selftest                           # 데스크톱 앱 자체 검사
twb mathworks export out/mw && twb mathworks run out/mw   # 이식 패키지 + 로컬 MATLAB/Octave 실행
python verification/make_report.py                  # 이 문서 재생성
```
