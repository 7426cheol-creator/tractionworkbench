# 요구 추적표 (Traceability) — 0.3.0

이 문서는 독립 엔지니어링 리뷰(handoff), 감사 증거 패키지(dc7b338)의 재현 스크립트, 그리고 세 추가 명세
(OEW/HEV, 파워모듈별 손실·단계별 효율, 가변 PWM·anti-jerk)의 각 항목이 **어디에 구현되었고 무엇으로 확인했는지**를
정리합니다. 상태 표기는 가변 PWM 명세 §1.1의 분류를 따릅니다.

| 상태 | 의미 |
|---|---|
| **implemented** | 계약대로 구현, 자동 테스트/자체 검사로 확인 |
| **partial** | 핵심은 구현, 명시한 부분이 빠짐 (빠진 부분은 UNKNOWN/미지원으로 표시되며 PASS로 승격되지 않음) |
| **missing** | 미구현 (앱은 해당 claim을 하지 않음) |
| **evidence_missing** | 계산은 가능하나 물리 검증 증거(공급사·시험·HIL·차량)가 없음 — 합성 예시는 끝까지 합성으로 표시 |

검증 단계(V0–V6)는 handoff §13.1의 정의입니다. 이 저장소의 모든 수치 fixture는 **구현 검증(V0–V3)** 이며
하드웨어 정확도(V5–V6)가 아닙니다. `twb exchange`는 MathWorks 이식·도구 간 parity용 규약·fixture 패키지를 씁니다.

## 1. 독립 리뷰 P0-A 정확성 (F01–F13)

| ID | 내용 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| F01 | 모든 경로에 동일 validity gate, 진단 수치와 accepted witness 분리 | `solvers/gate.py` (`check_witness`) | `tests/test_review_p0a.py` F01 | implemented |
| F02 | witness를 원 요청으로 재검증 (거짓 witness 금지) | 공통 gate | F02 | implemented |
| F03/F03b | control domain ≠ data domain, 경계·퇴화 입력 처리 | coverage 판정, 명시 오류 | F03 | implemented |
| F04/F04b | 유한/연속 정격 typed semantics, 순서 불변, band의 같은 witness | `requirement.py` duration/band | F04 | implemented |
| F05 | 선택된 경로가 물리적 safe endpoint까지 연결 | `extensions/timing.py` | F05 | implemented |
| F06 | missing ≠ unlimited, NOT_EVALUATED ≠ pass, 수치 residual ≠ 토크 정확도 | policy/DC claim, 불확실성 분석 | F06, 감사 재현 | implemented |
| F07/F07b | thermal evidence 실체·필수 노드·초기 상태, 비단조 feasible set 보존 | `extensions/thermal.py` | F07 | implemented |
| F08/F08b | 입력·시간·온도 gate, 무부하 역기전력은 정류 위험(방전 하한 아님) | `extensions/dclink.py`, ASC | F08 | implemented |
| F09 | Kt 단위 명시 변환·거부 | `io.py` KT_UNITS | F09 | implemented |
| F10 | node reciprocity(정적) ≠ interpolant 동적 보수성 | `models/flux.py` qualification | F10, 감사 재현(비대칭) | implemented |
| F11/F12 | 수학/모델/요구/qualification 층 분리 | claim layers | F11/F12 | implemented (UI 표시는 §8 참조) |
| F13 | UNKNOWN 경계를 최소 sizing으로, 미해결 이득을 not limiting으로 표시 금지 | `analysis/sizing.py`, dominance | F13 | implemented |

## 2. 감사 증거 패키지 (dc7b338) 재현 폐쇄

| 항목 | 구현 | 확인 | 상태 |
|---|---|---|---|
| solver F06: 조건부 빈 곡선 → UNKNOWN | `solvers/policy.py` `_physical_dc` | `test_review_p0a.py` audit | implemented |
| DV-03: 고정 calibration 행도 witness gate, 토크 정확도는 수치 residual과 별도 | `analysis/uncertainty.py` | DV-03 ×2 | implemented |
| DV-05: 정격 승인은 provenance에서 | `analysis/rating.py` `approval` | DV-05 | implemented |
| FDTI/FRTI 분할 미상 → 예산 ok = None (UNKNOWN) | `extensions/timing.py` | audit | implemented |
| 보간자 내부 비대칭 | `models/flux.py` `max_interior_asymmetry` | audit | implemented |

## 3. Handoff P0-B / P0-C / P1 / §10–§14

| 절 | 내용 | 구현 | 상태 |
|---|---|---|---|
| P0-B | 단위·축 순서·provenance·정적/동적 qualification, data audit | `io.py`, `models/`, `service.data_audit` | implemented (UI 노출: §8) |
| P0-C | 실제 모터–인버터 한 조합의 정적 release evidence | — | **evidence_missing** (공급사·시험 데이터 필요; 합성 suite를 qualified baseline이라 부르지 않음) |
| §8.8 P1-A | 데이터시트 모듈 손실 (소자별 도통·스위칭, 소유권, 외삽 금지) → P_dc·열·claim | `extensions/module_loss.py` | implemented / evidence_missing (DPT·공급사 도구) |
| §8.9 P1-A | DC-link 리플·커패시터 전류·ESR·수명 게이트 | `extensions/dclink_ripple.py` | implemented / evidence_missing |
| §12 | 모듈 열 사이클 rainflow·조건부 손상 | `extensions/lifetime.py` | implemented (screening) |
| §9 P1-B | 보호 임계값·derating·fault 검증 | `extensions/protection.py` | implemented / evidence_missing (HIL) |
| §9.13 | ASC 두 시간영역 전류 요구 | `extensions/asc_transient.py` | implemented (단일 VSI) |
| §11 P1-C | 전도 EMI: 요구 프로파일 완결성, source→path→receiver, 측정 trace 판정 | `extensions/emi.py`, EMI 페이지 | implemented (screening은 PASS 아님) / evidence_missing (보정) |
| §10 | 모터 설계: 검증된 기준의 일관 스케일링·계보·무효화 데이터, 결합 요구 여유 트레이드, 권선(star of slots), 개념 사이징 | `analysis/machine_design.py`, 모터 설계 페이지 | implemented; FEA/CAD는 외부 (non-goal) |
| §10.2 | qualified machine-data package 체크리스트 | `service.data_audit` | partial (체크리스트 UI 노출은 §8) |
| §14 | 디스커넥터 연결·분리·동기화 | — | missing (P2로 명시 보류) |

## 4. OEW / HEV 추가 명세

| ID | 내용 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| O-01 | 64 상태쌍, u0=0 부분집합(20쌍/7벡터), 보장 반경 V / (VA+VB)/√3 | `extensions/oew.py` | `tests/test_oew.py` | implemented |
| O-02 | dq0 전력·동손·상반성 | `winding_power_dq0` | O-02 | implemented |
| O-03 | 공통 bus vs 절연 전원의 귀환 경로 | 토폴로지 식별 | O-03 | implemented |
| O-04 | B=000은 부유 성형 결선이 아님 | `b_clamp_vs_floating_star` | O-04 | implemented |
| O-05 | 포트 회계 (공통 전원 한도는 한 번) | `oew_point` ports | O-05 | implemented |
| O-06 | 스위칭 i0 리플 | `zero_sequence_switching_ripple` | O-06 | implemented |
| O-07 | dq norm ≠ 상 peak | 파형 지표 | O-07 | implemented |
| R-01 | 쌍 안전 상태 (A111/B000, 6SO 등) | `paired_state_table` | R-01 | implemented |
| R-02 | OEW ASC 두 시간창 전류, 소자별 peak·장시간 RMS | 쌍 상태 정상상태 스크리닝만 | — | **partial** (OEW 과도 ASC 미구현 → demag/SOA claim UNKNOWN) |
| R-03 | 전원 개방·브리지 trip·게이트 전원 상실·전환 skew | — | — | **missing** |
| C-01 | 두 브리지 DC 전류 교차 스펙트럼 S_AA+S_BB+2Re S_AB | `emi.oew_common_mode` | `test_emi.py` | implemented |
| C-02 | 권선 u0 억제 ≠ 섀시 CM 억제 | 같은 함수 | C-02 | implemented |
| H-01 | 가지 전력 vs 순전력 | `extensions/hev.py` | `tests/test_hev.py` | implemented |
| H-02 | 개별 가능·공동 불가 (결합 토크 집합) | `joint_torque_set` | H-02 | implemented |
| H-03 | 크랭킹 replay (크랭크각 부하, 초기각) | `cranking_replay` | H-03 | implemented |
| H-04 | 유성기어 Willis·토크비·전력 잔차 | `planetary_check` | H-04 | implemented |
| H-05 | 부하 차단 에너지 원장 | `load_rejection` | H-05 | implemented |
| X-01 | 누락 map·부적합 파라미터·solver 실패 → reason-code UNKNOWN/거부, 단일 VSI solver는 토폴로지를 재해석하지 않음 | `io.py` SINGLE_VSI_NAMES, OEW 입력 검증 | X-01 | implemented |

## 5. 파워모듈별 손실·단계별 효율 추가 명세

| 항목 | 내용 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| F-E01 | 미상 손실을 0으로 합산해 '총 손실' 표시 | `viz/sweeps.point_fields`: 전 항 확정 시에만 총 손실, 알려진 소계 별도 | `test_efficiency.py` E-04 | implemented (수정) |
| F-E02 | 개별 효율을 전체 energy mode에 종속 | 경계별 독립 판정 (정지 시 DC→AC 단자 비율 유지) | E-03/E-04 | implemented (수정) |
| F-E03 | 회생 지도에 정방향 수식 라벨 | 방향별 정의 라벨, UNKNOWN 칸 빗금 | 그림 smoke | implemented (수정) |
| §2.3 | 모듈 손실에 옛 2차 certificate 적용 금지 | 두 손실 모델 배타 + I² 경로 gate (구조적 폐쇄) | leak detector(양성 대조 포함), solver·capability·sizing·지도가 모듈 P_dc를 따름 | implemented |
| §3 | 모듈 데이터 식별·시험 조건·소유권·외삽 금지 | P1-A 모듈 모델 재사용 (중복 계산기 없음) | `test_module_loss.py` | implemented / evidence_missing |
| §3.3 | 전류→소자 손실→Tj→손실→P_dc 결합 | A/B 비교의 전기열 고정점 (드라이브 자체 평가 경유, 정지 시 최악각) | `test_efficiency.py` | implemented (정상상태 점; 미션 과도 Tj는 수명 체인) |
| §4.1–4.2 | 다섯 경계 η, 방향·분모·N/A vs UNKNOWN, 망원 항등식은 같은 점·방향에서만 | `analysis/efficiency.py` | E-01, E-02 | implemented |
| §4.2 | 감속기: 방향별 손실 모델, 데이터 없으면 eDrive η UNKNOWN (100% 금지) | `ReducerModel` (드래그+방향별 η 또는 손실 map, 오일 온도 도메인) | reducer 테스트 | implemented / evidence_missing |
| §4.3 | 보조 전력은 공급 포트에서 한 번, 회수 지표 분리 | `AuxLoad`, aux_metrics | API 테스트 | implemented |
| §4.3 | 측정 파형 ⟨v·i⟩ (공분산) 전력 | — | — | missing (평균 모델만; 측정 파형 import 미지원) |
| §5 | 혼합 흐름·무출력·0 근처 분모·η>1 무clamp | `boundary_eta` | E-05, no-clamp 테스트 | implemented |
| §5 | 과도 저장에너지 포함 창 수지 | `mission_energy(storage_change_J)` 입력 | — | partial (선언값만; 저장에너지 자동 계산 없음) |
| §6.1 | 경계별 지도, feasible 마스크 보존 | `api.efficiency_map` (INFEASIBLE 칸 비움, UNKNOWN 빗금) | API 테스트 | implemented |
| §6.2 | 미션 E±, 방향별 에너지 비, 순비율은 효율 아님, 미수행 궤적 순위 금지 | `mission_energy`, `_mission_compare` | E-06 | implemented |
| §6.3 | OEW ηA·ηB 곱 금지, HEV 시스템 η 자동 생성 금지 | OEW 포트 원장·순환 전력, HEV 가지 전력 | OEW/HEV 테스트 | implemented |
| §7 | SiC vs IGBT: 고정 정책 vs 설계별 정책, Tj는 결과, 같은 요구 수행 후에만 순위, 오차 예산 초과 시에만 우열 | `compare_modules`, 효율·모듈 비교 페이지 | protocol 테스트 | implemented |
| §8.3 | DPT·열량계·동력계 상관 | — | — | evidence_missing |

## 6. 가변 PWM (P1-PWM)

| 항목 | 내용 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| §3.1 | 캐리어·이벤트율·샘플/갱신율·전기 주파수·펄스 비 분리 (정지 시 N/A) | `extensions/pwm_policy.py` | — | implemented |
| §4.1 | 운전점 스케줄: 히스테리시스·dwell·fallback·보호 선점, 인과 측정만 | `FswSchedule` | 스케줄 테스트 | implemented |
| §4.1 | 동기 PWM (전기각 동기 패턴) | — | — | missing (P2) |
| §4.1 | spread spectrum / random PWM | — | — | missing (P2) |
| §4.2 | 스케줄 fsw의 모듈 손실 (이벤트 합과 평균식 일치) | 모듈 모델 fsw 치환 + 결합 Tj | 평균 vs 이벤트 합 (1% 이내) | implemented |
| §4.2 | 모터 고조파: R_ac(f) 동손, 철손은 선언 상한 → 총합은 구간 비교, 없으면 UNKNOWN | `HarmonicLossData`, `_versus` | 테스트 | implemented / evidence_missing |
| §4.3 | 전류 리플·펄스 과전류 (RL 기준, 시간 적분 = 엣지 합 스펙트럼) | `phase_ripple` | 시간/FFT RMS 1e-3 | implemented |
| §4.3 | DC-link 커패시터 전류 (fsw별 재계산) | `dclink_ripple.ripple_analysis` 재사용 | 정책 테스트 | implemented |
| §4.3 | EMC/NVH 결합 | EMI 페이지 (캐리어 위상 포함) | — | partial (정책 비교에 자동 연동 안 함 → 설계별 정책 비교에서 '미평가'로 명시) |
| §4.4 | 원자적 reload vs 즉시 기록, runt/누락/데드타임 | `counter_pwm`, `transition_check`, `check_gate_events` | 전환 테스트 | implemented |
| §4.4 | 지연 원장·deadline (보드 상수 금지) | `TimingConfig`, `delay_ledger` | 지연 예시 (1.104°→2.208°, 54°→108°) | implemented |
| §4.4 | 이득 매핑 (연속 Ki vs 고정 이산 KiΔt), 전류 루프 위상 여유 | `CurrentLoop` | 테스트 | implemented |
| §4.4 | 샘플 유효창(shunt)·stale 샘플·전환 과도 | — | — | missing |
| §4.5 | 필수 위반은 효율로 상쇄 금지, Pareto, '평가 후보 중 최선' | `evaluate_policies` | 정책 테스트 | implemented |
| — | 저 펄스 비·과변조·six-step | — | — | 미지원 (명시; 선형 SVPWM만) |

## 7. Anti-jerk·능동 감쇠 (P1-DAMP)

| 항목 | 내용 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| §5.2 | 2관성 ROM, 고정 g 환산, 에너지 불변식 | `extensions/driveline.py` (행렬지수 정확 적분) | D-01, D-05 (출력 좌표 독립 ODE) | implemented |
| §5.3 | 성형 (rate·prefilter·ZV), 피드백 (상대속도·모터속도 HPF), 전력 항 Tad·ωm | `Shaper`, `Damping` | D-02 | implemented |
| §5.4 | 동적 토크 여유: capability 창 (양/음) | `api.driveline` window | API 테스트 | partial (차동 인덕턴스 기반 전압 여유 궤적은 미구현, 표기) |
| §5.5 | 중재 후 클리핑 (한도 뒤 재가산 금지), 긴급 감소는 comfort 필터 우회 | `simulate` | 클리핑·긴급 테스트, D-04 | implemented |
| §5.6 | 샘플링 루프 안정성 (분수 지연 반올림 금지), 연속 지연 교차 | `sampled_eigenvalues`, `delay_crossings`, `rhp_roots_at` | D-03 | implemented |
| §2 | 백래시 통과 → UNKNOWN | `evaluate_variants` | 테스트 | implemented |
| §5.3 | 센서 dropout·timestamp skew·wheel slip | 양자화만 | — | partial |
| §6 | off/shaping/feedback/combined 같은 조작·요구 비교, 늦은 가속은 jerk 개선만으로 표시 금지, 손실은 전달 일과 함께 | `evaluate_variants` | API 테스트 | implemented |
| — | 다관성·HEV 다축 협조, FRF 식별 | — | — | missing (P2) / evidence_missing |

## 8. 남은 UI 노출 항목 (engine은 구현됨)

P0-A/B 엔진 결과를 화면에 더 드러내는 항목입니다. 현재 결과는 API/기록/보고서에 들어 있으며, 아래는 화면 표시 보강입니다.
판정 페이지 claim 층 표시, 탐색 페이지의 gate 메시지, 모델 페이지 data audit 표, 열 편집기의 검증 증거·초기 상태,
FTTI endpoint 입력, 안전 페이지의 정류 위험 주석.

## 9. 비목표 (handoff §15, 추가 명세 비목표)

generic motor CAD/FEA 복제, 정적 ASC로 demag/SOA 승인, 일반 IGBT 식으로 SiC 수명 보증, 드라이버 typical delay로 ASIL 승인,
class 번호로 EMC 합격률, 평균 dq로 NVH/베어링/MHz 임피던스, 생산 anti-jerk 제어기 자동 납품, 보편 안정성 인증서,
IGBT+SiC 혼합 소자 토폴로지, 인증·공급사 승인 자동 생성.
