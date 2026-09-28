# 요구 추적표 (Traceability) — 0.5.0

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
| F11/F12 | 수학/모델/요구/qualification 층 분리 | claim layers, 요구 판정 페이지의 층 표 | F11/F12, desktop smoke | implemented |
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
| P0-B | 단위·축 순서·provenance·정적/동적 qualification, data audit | `io.py`, `models/`, `service.data_audit`, 모델·데이터 페이지 감사 표 | implemented |
| P0-C | 실제 모터–인버터 한 조합의 정적 release evidence | — | **evidence_missing** (공급사·시험 데이터 필요; 합성 suite를 qualified baseline이라 부르지 않음) |
| §8.8 P1-A | 데이터시트 모듈 손실 (소자별 도통·스위칭, 소유권, 외삽 금지) → P_dc·열·claim | `models/module_loss.py` (드라이브 모델의 일부; 구 경로 재수출) | implemented / evidence_missing (DPT·공급사 도구) |
| §8.9 P1-A | DC-link 리플·커패시터 전류·ESR·수명 게이트 | `extensions/dclink_ripple.py` | implemented / evidence_missing |
| §12 | 모듈 열 사이클 rainflow·조건부 손상 | `extensions/lifetime.py` | implemented (screening) |
| §9 P1-B | 보호 임계값·derating·fault 검증 | `extensions/protection.py` | implemented / evidence_missing (HIL) |
| §9.13 | ASC 두 시간영역 전류 요구 | `extensions/asc_transient.py` | implemented (단일 VSI) |
| §11 P1-C | 전도 EMI: 요구 프로파일 완결성, source→path→receiver, 측정 trace 판정 | `extensions/emi.py`, EMI 페이지 | implemented (screening은 PASS 아님) / evidence_missing (보정) |
| §10 | 모터 설계: 검증된 기준의 일관 스케일링·계보·무효화 데이터, 결합 요구 여유 트레이드, 권선(star of slots), 개념 사이징 | `analysis/machine_design.py`, 모터 설계 페이지 | implemented; FEA/CAD는 외부 (non-goal) |
| §10.2 | qualified machine-data package 체크리스트 | `service.data_audit`, 모델·데이터 페이지 | partial (용도별 상태·qualification 공백 표시; 공급사 패키지 서명·개정 관리는 없음) |
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
| §4.4 | 샘플 유효창 (인라인 / 레그 션트 / DC-link 단일 션트: settle·aperture·데드타임·엣지 잡음), 무효 샘플의 유지·예측 나이와 오차 한계, 채널 skew | `SensingConfig`, `sampling_validity` (정지 부근은 무효 각이 지속 → 나이 무한) | 단일 션트 창 = SVPWM 활성 벡터 시간 폐형식 (2e-20 s) | implemented (선언된 타이밍 기반 screening) |
| §4.4 | 전환 과도: 이득 매핑·적분기 저장(전압 / 오차 합)·리셋·포화, 선언 지연 | `transition_transient`, 정책 비교의 각 fsw 변경 재생 | 점프 = (Ts_to/Ts_from − 1)·v_ss, 리셋 = −v_ss 정확 | implemented (한 축, 일정 운전점) |
| §4.4 | 임계 채터: 측정 잡음 p-p ≥ 히스테리시스 → 위반, 잡음 미선언 → 미검증 | `chatter_risk` | 채터 테스트 | implemented |
| §4.5 | 필수 위반은 효율로 상쇄 금지, Pareto, '평가 후보 중 최선'; 요구 미확립 구간은 UNKNOWN (위반과 구분) | `evaluate_policies` (status ADMISSIBLE / VIOLATION / UNKNOWN) | 정책 테스트 (미선언 DC 한계 → UNKNOWN) | implemented |
| — | 저 펄스 비·과변조·six-step | — | — | 미지원 (명시; 선형 SVPWM만) |

## 7. Anti-jerk·능동 감쇠 (P1-DAMP)

| 항목 | 내용 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| §5.2 | 2관성 ROM, 고정 g 환산, 에너지 불변식 | `extensions/driveline.py` (행렬지수 정확 적분) | D-01, D-05 (출력 좌표 독립 ODE) | implemented |
| §5.3 | 성형 (rate·prefilter·ZV), 피드백 (상대속도·모터속도 HPF), 전력 항 Tad·ωm | `Shaper`, `Damping` | D-02 | implemented |
| §5.4 | 동적 토크 여유: capability 창 (양/음), 전압 여유 한도의 토크 slew (차동 L_q, 모델 k_t, 전압 상한까지의 q축 여유) | `api.driveline` window, `electrical_slew_limits` | k_t·L_q = 상수 모델 폐형식, 고속 FW에서 slew 초과 → UNKNOWN | partial (순간 q축 여유 screening; d축 결합·상태/진폭/주파수별 도달 가능 토크 궤적 집합은 미구현) |
| §5.5 | 중재 후 클리핑 (한도 뒤 재가산 금지), 긴급 감소는 comfort 필터 우회 | `simulate` | 클리핑·긴급 테스트, D-04 | implemented |
| §5.6 | 샘플링 루프 안정성 (분수 지연 반올림 금지), 연속 지연 교차 | `sampled_eigenvalues`, `delay_crossings`, `rhp_roots_at` | D-03 | implemented |
| §2 | 백래시 통과 → UNKNOWN | `evaluate_variants` | 테스트 | implemented |
| §5.5 | 권한 창 미선언 → UNKNOWN; 한쪽 클리핑(음 = 회생 여유 소진) 귀속; 안전 반응은 comfort와 별도 판정 (comfort 지표는 안전 요청 전 구간) | `evaluate_variants`, `_safety_reaction`, `response_metrics` | 테스트 | implemented |
| §7.2 | 필수 실패 사례 (무효 샘플 창, 전류/전압 포화, 채널 skew, stale/dropout, 임계 채터, gain-state jump, 회생 여유 부족, 안전 중단, 백래시, solver/domain 실패) → 어느 것도 조용히 PASS로 대체되지 않음 | 위 항목 전체 | `test_pwm_policy.py`, `test_driveline.py` 실패 사례 테스트 | implemented |
| §5.3 | 센서 dropout (유지값의 나이, 선언 stale 한계 → 페이드 아웃/인), 부하 속도 timestamp skew (거짓 상대속도), 양자화 | `Damping` sensing 필드, `simulate` 측정 이벤트 | skew 오차 = w_l(t) − w_l(t − skew) (조밀 출력 대조), dropout 페이드 시간 | implemented / wheel slip은 missing (무슬립 플랜트, slip 사례를 판정하지 않음) |
| §6 | off/shaping/feedback/combined 같은 조작·요구 비교, 늦은 가속은 jerk 개선만으로 표시 금지, 손실은 전달 일과 함께 | `evaluate_variants` | API 테스트 | implemented |
| — | 다관성·HEV 다축 협조, FRF 식별 | — | — | missing (P2) / evidence_missing |

## 8. P0-A/B 결과의 화면 노출

엔진 결과(API·기록·보고서)를 화면에서도 그대로 보이게 한 항목입니다. 모두 desktop smoke(`tests/test_reports_desktop.py`)가 확인합니다.

| 항목 | 화면 | 상태 |
|---|---|---|
| claim 층 (수학 · 모델 · 요구 · qualification), 요구 witness | 요구 판정: 층 표(서로 다른 진술, 합치지 않음), 핵심 수치의 요구 witness(정적·DC·지속시간이 같은 점) | implemented |
| witness gate 메시지 | 운전점 탐색: 정방향 평가를 ACCEPTED / DIAGNOSTIC ONLY / UNKNOWN으로 표시, 위반·미평가 제약과 gate 사유, 제목에 '진단용' | implemented |
| 결측 ≠ 무제한 | 모델·데이터: DC 한계마다 값 / 미선언(UNKNOWN) / 선언된 무제한(∞) 선택 | implemented |
| data audit | 모델·데이터: 용도별 상태·근거와 qualification 공백 (모듈 손실 모델 사용 시 그 식별 정보) | implemented |
| 열 모델 증거·초기 상태 | 열 편집기의 검증 근거 칸(없으면 '검증'은 증거 없는 선언), 열 페이지의 초기 열 상태(미선언·고온 시작 → UNKNOWN) | implemented |
| FTTI endpoint | 안전 스크리닝: 안전 종점 이벤트, 종점 종류(물리적 안전 상태 / 명령 발행 → UNKNOWN), 최댓값 동시 발생 선언, 보장 상한 경로·FDTI/FRTI 최악값 | implemented |
| 정류 위험 | 안전 스크리닝: 능동·패시브 방전 결과 표(정류 위험, 역기전력 근거, 목표 이하 최고 속도, 정류 링크 전압 스크리닝 추정과 방법), 과전압 결과 표 | implemented |

## 9. 시스템 검토 (기능 간 의존성·영향·성숙도)

기능 사이의 의존성, 변경 영향, 성숙도, 교차 모듈 일관성은 [`SYSTEM_REVIEW.md`](SYSTEM_REVIEW.md)에 있습니다. 검토에서 찾아 고친 결함:
전류 루프의 축별 판정(기계 차동 인덕턴스), 한 평가 한 변조, 미션 효율 부분 비율의 UNKNOWN 처리, 예시 모듈 열 경로 통일, 변조 법칙 통합.

| 권고 | 내용 | 상태 |
|---|---|---|
| R1-1 | 모듈 모델이 켜진 코어 경로의 회귀 기준(`module_core_anchor`); 모듈 데이터가 점을 덮지 못할 때의 DC claim 사유(OUTSIDE_MODEL_DOMAIN) | implemented |
| R1-2 | `module_loss`를 모델 층으로(타입 계약), 커널 손실 계약(`loss_kind`·`i2_dc`·`pointwise_loss`)으로 DC 논증 분기 일원화; 격자 DC를 '미평가'로(모듈 모델에서 위반으로 보이던 지도·envelope) | implemented |
| 아키텍처 | 층 규칙·패키지 경계 비공개 이름 0·모듈 수준 순환 0을 테스트로 강제 (`tests/test_architecture.py`) | implemented |
| R2 | 프로젝트 데이터 패키지: `project.py`(twb-project/1 — 섹션별 parser 검증·digest·provenance, 섹션 간 일관성 규칙 PRJ-01…12, 개정 비교와 영향 분석, 결과의 사용 기록·stale 판정), 내장 합성 프로젝트(`examples.py`)가 모든 페이지 예시의 제품 데이터 출처(`api.example(name, project)`, 내장 예시와 동일함을 테스트), 데스크톱 프로젝트 페이지·배지·페이지별 결과 띠(프로젝트 데이터 / 로컬 변경 / stale), 페이지가 활성 프로젝트에서 제품 입력을 다시 읽음, 기록(JSON·Markdown·PDF)에 프로젝트 맥락, `twb project show/check/diff/export` | implemented (`tests/test_project.py`, 데스크톱 smoke·self-test의 프로젝트 전환) |

## 10. 두 번째 독립 리뷰 R2 (기준 main f6f166b)

리뷰의 반례를 모두 재현한 뒤, 각 계약의 **올바른** 동작을 `tests/test_review_r2.py`에 수용 테스트로 고정했습니다
(리뷰 증거 스크립트는 관찰된 결함을 단언하므로 테스트로 병합하지 않음). 수치는 리뷰 인계서의 값입니다.

| ID | 결함 (요지) | 수정 | 확인 (`test_review_r2.py`) | 상태 |
|---|---|---|---|---|
| C01 | covered 영역의 DC 배제를 전체 control domain의 INFEASIBLE로 승격 | 배제 증명은 증명한 영역에만, 나머지 UNKNOWN | `test_c01_*` | implemented |
| C02 | 1차원 control set을 면적 기준 full coverage로 오인 | 폭 0 집합·경계선·단일 점의 커버리지 | `test_c02_*` | implemented |
| C03 | DC I² 대역과 witness gate의 수치 허용오차 불일치 | 하나의 acceptance 집합, 수치 allowance 별도 보고 | `test_c03_*` | implemented |
| C04 | 온도 법칙이 음의 Rs를 만들어도 FEASIBLE | 비수동 법칙 입력 거부, gate의 에너지 상태 검사 | `test_c04_*` | implemented |
| A1.4 | 모듈 손실 physical capability가 policy witness를 잃음 | accepted policy witness 상속 | `test_a1_*` | implemented |
| D-R2-01 | REJECTED 정격을 승인된 공급사 증거로 수용 | typed approval + 근거, NaN 조건 불일치, 부호 사분면 | `test_d_r2_01_*` | implemented |
| D-R2-02 | 다른 모듈 손실·반대 결정이 같은 입력 해시 | 모든 필드의 content hash (`identity.semantic`) | `test_d_r2_02_*` | implemented |
| D-R2-03 | 통과한 band 요구를 실패한 중심점으로 표시·진단 | accepted band witness가 기본 출력 | `test_d_r2_03_*` | implemented |
| D-R2-04 | sizing 차트가 미해결 경계를 최소값으로 재승격 | 데이터 경계는 최소 아님, UNKNOWN 사이 섬은 bracket 없음 | `test_d_r2_04_*` | implemented |
| D-R2-05 | 충전 수용 0이 dominance 진단에서 사라짐 | 0 cap 유지 | `test_d_r2_05_*` | implemented |
| PT-01 | SiC 열을 가상의 두 소자로 분할 | 물리 다이(채널+바디다이오드) 소유권 | `test_pt01_*` | implemented |
| PT-02 | 정지 SiC 도통열 절반 손실, 결측 → 0 | 실제 듀티, 같은 데이터 gate | `test_pt02_*` | implemented |
| PT-03 | 커패시터 열 반복이 발산해도 수렴 보고 | 한 온도 결합해(첫 상향 영점), 독립 잔차, 종료 종류 명시 | `test_pt03_*` | implemented |
| PT-04 | ESR(T)가 전류 분배에 반영 안 됨 | 같은 온도에서 전류 분배·발열 동시 해 | `test_pt04_*` | implemented |
| PT-05 | 리플 위치 매핑·도메인 gating으로 PASS/FAIL 반전 | location×quantity 맵, ESR 표 밖 수동성 경계, 주파수별 KCL, Nyquist, 샘플링 허용치 | `test_pt05_*` | implemented |
| PT-06 | 최대 발열 소자를 하나의 물리 소자로 취급 | 물리 다이별 이력, 유한/주기 미션 | `test_pt06_*` | implemented |
| PT-07 | 승인된 rise_time 입력이 실제로 냉각 시간 | 가열 구간 t_on | `test_pt07_*` | implemented |
| PT-08 | 저제동·양측 소산에서 감속기 역변환 오류 | 맞물림 동력 Q 분기 | `test_pt08_*` | implemented |
| PT-09 | 고정 정책 A/B가 변조를 고정 안 함 | 공통 변조 강제, 범위 표시 | `test_pt09_*` | implemented |
| PD-01 | 적용 불가·부재 OV 경계를 보장 창으로 승격 | 경계 유효성, 필터 지연 경계 | `test_pd01_*` | implemented |
| PD-02 | 경고 선행시간을 다른 샘플링 위상에서 조합 | 같은 궤적에서 측정 | `test_pd02_*` | implemented |
| PD-03 | ASC 연산자와 상/소자 증거 불일치 | 하나의 전각 포괄(sup\|i_phase\| = \|i_dq\|, ∫i² 정확한 최대), 상별 time_above | `test_pd03_*` | implemented |
| PD-04 | 타임라인 검증 없음; ASC 역적분 | 지평 내 사건 | `test_pd04_*` | implemented |
| PD-05 | ASC 기계 동역학의 역속도 대칭 깨짐 | 부호 있는 역학, 에너지 원장 | `test_pd05_*` | implemented |
| PD-06 | 디레이팅 효과를 즉시 냉각으로 오인 | 안전 평형으로의 유계 가열 = containment | `test_pd06_*` | implemented |
| PD-07 | 반환 검출기 샘플이 무반응 궤적의 값 | 실제 궤적의 샘플 | `test_pd07_*` | implemented |
| CT-01 | PWM 미검증 필수 제약을 ADMISSIBLE로 승인 | 필수/해당없음/권고, 열린 필수 검사는 UNKNOWN | `test_ct01_*` | implemented |
| CT-02 | HEV 공유 DC 전압을 동시에 풀지 않음 | 비조정 bus V = OCV − R·I와 기계를 함께 해, 수동성 상한 증명; boost 배터리측 | `test_ct02_*` | implemented |
| CT-03 | anti-jerk 요구량 절반만 전달해도 PASS | 요청 목표 기준 응답 | `test_ct03_*` | implemented |
| CT-04 | 안전 반응 완료를 마지막 위반 샘플로 계산 | 정확한 액추에이터 지수 반응 시각 | `test_ct04_*` | implemented |
| CT-05 | HEV 발전기 ramp 에너지 적분 오류 | 정확한 원장 E_peak = (G−S)td + tr(G−S)²/(2G) | `test_ct05_*` | implemented |
| CT-06 | 연속 여유와 실제 샘플 루프 불일치 | 샘플 루프 스펙트럼 반경·이산 PM이 판정 | `test_ct06_*` | implemented |
| CT-07 | OEW 인증 각도 경계의 부호 오류 | 고조파 표현 정규화, \|a\| 경계, 정확한 상 피크 | `test_ct07_*` | implemented |
| EMC-01 | 표본/보정 EMI를 대역 전체 판정으로 승격 | 연속 수신 대역의 정확 열거(창 변화점·한계 꼭짓점; 표시 격자 무관), claim 영역 = 대역 ∩ 보정 구간 ∩ 망 유효 ∩ RBW ∩ 한계(선언 공백 제외), 완전한 보정 기록(근거·holdout·취득·오차 모델·유한 한계·구간·set-up·망 식별자·소스 범위)과 매 실행 적용성 재검사, 방법·단위 게이트, RBW 구간 | `test_emc01_*` | implemented |
| EMC-02 | 측정 trace coverage가 협대역 피크를 놓쳐도 PASS | trace 자체 메타데이터(표현·검출기·RBW·IF 형상·dwell·보정·set-up), 읽음값 사이 손실 경계, 검출기 순위 peak ≥ QP ≥ AV, 적합·여유 분리, 판정 규칙 저장 | `test_emc02_*` | implemented |
| EMC-03 | 상한 초과를 INFEASIBLE 증인으로 사용 | 하한(E − U−) 초과 주파수만 증인, 중첩은 UNKNOWN | `test_emc03_*` | implemented |
| EMC-04 | 데드타임 펄스 붕괴로 비물리 스펙트럼 | 게이트 명령 + 턴온 지연 데드타임 + 다이오드 클램프의 스위칭 순서, 최소 펄스 정책, 모순 시간 거부, 요청/평가 fsw, 비정수 비동기 캐리어는 유효 범위 밖; 독립 스위칭 시뮬레이션과 일치 | `test_emc04_*` | implemented |
| MD-01 | 권선 가능성·기준 식별이 스케일링 인계를 막지 않음 | 두 배치 모두 유효 + 극쌍수 일치 + (선언 시) 기준 = 선언 권선(`WindingDefinition`); 계보가 파생 기계 권선으로; 미선언은 '일반 k_N 사고 실험' — core·API·UI 같은 gate | `test_md01_*` | implemented |
| MD-02 | 0을 기본값으로, 분수 Q/p 절삭 | 빈칸만 기본값, 0 거부, 정수는 절삭 없이 검증, 상 없는 배치는 구조화된 무효 결과 | `test_md02_*` | implemented |
| P1-A/B | EMI 파라미터·기계 데이터의 검증된 import (다음 fidelity 권고) | — | — | missing (로드맵: 측정 DPT 에지 family, 측정 부품 임피던스·다중 포트 import, FEA/공급사 dq·손실 데이터 정규화와 holdout 비교) |

이 과정에서 함께 찾아 고친 것: 게이트 이벤트 검사가 관측 창이 자른 펄스를 최소 펄스 위반으로 판정하던 결함
(`check_gate_events`, 창 경계 펄스는 폭을 판정하지 않고 open으로 계수 — `test_a_pulse_cut_by_the_observation_window_is_not_a_runt`),
EMI·PWM 예시의 데드타임 불일치(1.0 µs vs 손실 1.5 µs — 프로젝트의 제어기 한 값으로 통일).

## 11. MathWorks 이식·검증 브리지 (twb-mathworks/1)

기준: MathWorks 이식 handoff(2026-09-28)와 Agentic SE 논문의 source-separated acceptance. 세 층 — (1) 요구·수용된 원천,
(2) Python reference, (3) MathWorks — 을 분리하고, 먼저 2 → 3 이식과 parity를 만들되 3을 1에도 대조합니다.
"Octave"는 GNU Octave 8.4를 MATLAB 언어 호환 proxy로 실행한 것이며 MATLAB·Simulink·System Composer 실행이 아닙니다.

| 항목 | 내용 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| §3.1 | 식별: schema, 구현 SHA·dirty·의미 소스 hash, 입력 snapshot(project digest, 모델 content SHA-256, plane data SHA-256), generator·template revision, 수치 설정; byte vs semantic 동일성 | `mathworks/package.py` manifest, semantic fingerprint | `test_export_is_deterministic` | implemented |
| §3.1 | 누락·NaN/Inf·enum의 의미를 schema에서 정함 (null = 미정의, DC 한계 finite/unlimited/not_declared) | `contract.py`, `cases.limits_spec` | `test_semantic_cases_keep_their_meaning` | implemented |
| §3.2 | 규약(amplitude-invariant, phase peak, d on PM, 기계 rpm, pole pairs, 전력 부호, Te/Tshaft, peak/RMS, line/phase)을 검사 가능한 값으로 | `contract.CONVENTIONS`, `+twb/checkContract.m` | guard "Park convention refused", `test_check_refuses_…` | implemented |
| §3.2 | physics와 control policy 분리; native optimiser는 전역 인증서를 상속하지 않음 | 요구 witness case(`layer2_claim`), gap report | `test_requirement_witnesses_carry_the_layer2_claim` | implemented (optimiser 이식은 exported_data_only) |
| §3.3 | map 축·index·mask·cell 규칙·모서리·외삽 금지·온도 plane·대칭 근거, 행/열 순서를 전치로 추측하지 않음 | `contract.map_rules`, `+twb/checkModel.m`, `fluxMapLookup.m`, `selectPlane.m` | VF.MAP.* 21 case, 전치 거부 guard | implemented |
| §3.3 | 이중 온도 보정·손실 이중 계산 검출 | `checkModel.m`, `loss_ownership`, `lossOwnershipConflicts.m` | guard X05 2건 | implemented |
| §3.4 | 동적 모델의 상태·초기값·event 의미 | — | — | not_applicable (동적 모델 미이식, gap report) |
| §3.5 | 요구의 quantity·comparator·조건, claim을 boolean으로 평탄화하지 않음, 출처 유지 | `cases.requirement_cases`, `architecture.requirement_links` | 요구 witness 11 case | implemented / uncertainty set 이식은 missing |
| §4.1-1 | versioned 패키지 생성·검사, target compatibility report | `export_package`, `check_package`, `+twb/preflight.m`, `gap_report.json` | `test_check_refuses_…`, preflight.json | implemented |
| §4.1-2 | 회사 환경 local entry point, 로그·오류 결과 | `+twb/runAll.m`, `run_local` (MATLAB `-batch` / Octave) | Octave 실행 | implemented (MATLAB 실행은 NOT_RUN) |
| §4.1-3 | native constant dq evaluator + nonlinear flux-map evaluator, Python·analytical 대조 | `+twb/staticPoint.m`, `evaluatePoint.m`, `fluxMapLookup.m` | Octave 79 PASS, 심은 결함 9종 검출 | implemented |
| §4.1-4 | Simulink 정적 평가 harness 생성 recipe | `+twb/buildEvaluationHarness.m`, `runHarness.m` | Octave 구문 검사만 | implemented, NOT_RUN (Simulink 없음) |
| §4.1-5 | System Composer/SLDD 후보 mapping과 충돌 검출 | `architecture.py`, `+twb/compareDataItems.m`, `checkDictionary.m`, `buildArchitectureCandidate.m` | guard X09 2건 (Octave), `test_architecture_candidates_…` | candidates·충돌 논리 implemented; 모델 생성·SLDD 읽기 NOT_RUN |
| §4.1-6 | machine-readable 보고서 저장·읽기, 미실행은 NOT_RUN | `runAll.m`, `verify_report` | `test_report_reimport_statuses_and_identity` | implemented |
| §5.3 | 재실행·rename·충돌·사용자 수정: 새 폴더만, 편집된 생성물 덮어쓰기 금지, 사라진 ID ≠ 삭제 | `package._existing_guard`, harness/architecture 생성기 | `test_regeneration_never_overwrites_…` | implemented |
| §6 | Agentic Toolkit 역할 한정, AI 없이 핵심 경로 | `AGENT_TASKS.md`, `twb.runAll` | — | implemented |
| §8.2 | 상태를 하나의 초록불로 합치지 않음 | `desktop/mathworks_dialog.py`, `cli mathworks` | self-test `mathworks:package` | implemented |
| X01 | export → parse → 재export; 모르는 schema·누락 단위·손상 hash | `check_package`, `loadPackage.m` | `test_export_is_deterministic`, `test_check_refuses_…` | implemented |
| X02 | 상수 dq, ±속도, 구동/회생, 정지 | REF.F00–F05, PRODUCT.*, BND.* | golden + closed-form oracle, Octave | implemented |
| X03 | 비정방·비대칭 map, 다른 축 길이, 내부 질의 | VF_D2_MAP (6×7 비균일·비대칭) | 전치 → ERROR/FAIL 검출 | implemented (미분은 정적 사용에 불필요: 동적 사용 NOT QUALIFIED로 전달) |
| X04 | 구멍·외곽·온도 경계·잘못된 입력 | VF.MAP.* hole/edge/outside/above_planes/no_temperature | clip 심은 결함 검출 | implemented |
| X05 | 온도 plane·Rs 보정·native loss on/off | VF_D1_TEMP, guard X05 | Rs 법칙 무시 결함 검출 | implemented |
| X06 | 정확 경계·경계 안팎·미해결 최적화 | BND.* (±0.5, ±2 tol), 요구 witness의 evidence kind | ACTIVE=위반 결함 검출 | implemented |
| X07 / X08 | 동적 모델·물리 포트 보존 | — | — | not_applicable (gap report) |
| X09 | 재생성·rename·이름 충돌·사용자 수정 | `_existing_guard`, `compareDataItems.m` | guard X09, `test_regeneration_…` | implemented |
| X10 | toolbox 부재·MATLAB 미실행·미지원 대상 | `preflight.m`, NOT_RUN 단계 | `test_report_reimport_…`, Octave run | implemented |
| X11 | OEW/HEV topology | 제품 드라이브는 단일 VSI, OEW/HEV를 단일 VSI로 축소하지 않음 | gap report | not_applicable |
| X12 | 잘못된 input/model/profile hash의 보고서 | `verify_report` (fingerprint·소비 파일 hash·project digest) | FOREIGN_REPORT, stale 테스트 | implemented (profile은 패키지 식별 밖 — 회사 환경에 둠) |

## 12. 앱 검토·UX·anti-jerk·데이터시트 (0.5.0)

| 항목 | 내용 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| 검토 B1 | PDF 보고서가 plot theme을 누설 | `plots/style.using`, `report_pdf.build_pdf` | `test_pdf_report_gives_the_callers_plot_theme_back` | implemented |
| 검토 B2 | self-test가 theme 설정을 저장 | `set_theme(persist=False)` | 데스크톱 테스트 | implemented |
| 검토 B3 | 요약 열이 결과 위젯을 지움 | `pages/decision.py` | 데스크톱 스모크 | implemented |
| UX | 그룹 탐색, 화면 안내, 잘리지 않는 입력 패널, 특수값 표시, 오류 표시 | `main_window.py`, `widgets.tidy_inputs` | self-test `guide`, 폭 측정(잘림 0) | implemented |
| P1-DAMP | 정상 토크 결손 폐형식, 2차 washout, 확장 상태의 샘플 루프 | `extensions/driveline.py` | `test_speed_highpass_steady_deficit_…`, `test_washout_sampled_stability_…` | implemented |
| P1-A/B | 데이터시트 가져오기 (모듈 곡선·ESR·dv/dt, 외삽 없음, 공급사 provenance) | `datasheet.py`, `desktop/datasheet_dialog.py`, `cli datasheet` | `test_datasheet.py` 8건, self-test `datasheet:module` | implemented (측정 데이터 단계는 missing) |
| 엔진 | plane을 고를 수 없는 map 시나리오에서 capability가 죽음 | `physics.torque_scale`, `solvers/capability.py` | `test_scenario_that_selects_no_plane_…` | implemented |

## 13. 비목표 (handoff §15, 추가 명세 비목표)

generic motor CAD/FEA 복제, 정적 ASC로 demag/SOA 승인, 일반 IGBT 식으로 SiC 수명 보증, 드라이버 typical delay로 ASIL 승인,
class 번호로 EMC 합격률, 평균 dq로 NVH/베어링/MHz 임피던스, 생산 anti-jerk 제어기 자동 납품, 보편 안정성 인증서,
IGBT+SiC 혼합 소자 토폴로지, 인증·공급사 승인 자동 생성.
