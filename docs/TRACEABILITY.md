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
| §3.5 | 요구의 quantity·comparator·조건, claim을 boolean으로 평탄화하지 않음, 출처 유지 | `cases.requirement_cases`, `architecture.requirement_links` | 요구 witness 12 case | implemented / uncertainty set 이식은 missing |
| §4.1-1 | versioned 패키지 생성·검사, target compatibility report | `export_package`, `check_package`, `+twb/preflight.m`, `gap_report.json` | `test_check_refuses_…`, preflight.json | implemented |
| §4.1-2 | 회사 환경 local entry point, 로그·오류 결과 | `+twb/runAll.m`, `run_local` (MATLAB `-batch` / Octave) | Octave 실행 | implemented (MATLAB 실행은 NOT_RUN) |
| §4.1-3 | native constant dq evaluator + nonlinear flux-map evaluator, Python·analytical 대조 | `+twb/staticPoint.m`, `evaluatePoint.m`, `fluxMapLookup.m` | Octave 80 PASS, 심은 결함 9종 검출 | implemented |
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
| P1-A/B | 데이터시트 대표값 직접 입력 (모터·모듈·커패시터·dv/dt): 선언된 구성 규칙, 규칙마다 기록, 규약 선택 강제, 미입력 거부, 파일 spec과 같은 결과 | `datasheet.representative_curves`·`motor_section`·`ESR_representative`, `desktop/datasheet_entry_dialog.py`, `plots/datasheet_figures.fig_datasheet_motor` | `test_datasheet.py` (기준 기계 재현, 곡선 = 선언 모델, I_max 위 UNKNOWN, ESR 대역 밖 UNKNOWN, 거부 사례), self-test `datasheet:entry_forms`·`datasheet:entry_motor` | implemented |
| 엔진 | plane을 고를 수 없는 map 시나리오에서 capability가 죽음 | `physics.torque_scale`, `solvers/capability.py` | `test_scenario_that_selects_no_plane_…` | implemented |

## 13. 공학 리뷰 (기준 6198099)

동료 공학 리뷰(`ENGINEERING_REVIEW.md`, 반례 스크립트, 회귀 테스트 초안)의 지적을 실제 저장소에서 재현한 뒤 고쳤습니다.
리뷰어의 회귀 테스트 7건은 수정 전 5건 실패 → 수정 후 7건 통과이며 그대로 `tests/test_review_6198099.py`에 들어 있습니다.

| 항목 | 지적 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| F1 | 긴 정격(30 s·연속)의 초과를 짧은 요구의 실패로 전이, 양립하는 짧은·긴 정격이 CONFLICTING | `rating.applicability` → 증거 방향(`both` / `positive`), `duration_claim`: 긍정 전용 근거의 초과는 결론 없음(UNKNOWN), 충돌 판정은 결론을 내는 근거끼리만 | `test_longer_rating_cannot_exclude_…`, `test_compatible_short_and_long_…`, `test_a_same_duration_rating_still_…` | implemented |
| F1b | 같은 지속시간이라도 '완전한 상한'과 '입증 영역'을 구분 | `RatingEnvelope.limit_semantics` (`rated_limit` / `demonstrated_region`) | `test_a_demonstrated_region_is_not_a_limit` | implemented |
| F2 | `linear_declared` 표의 보간 한계 초과가 UNKNOWN | `_evaluate_envelope`: 선언된 선형 한계와 직접 비교 (RATING_NOT_MET), `conservative`는 괄호 의미 유지 | `test_declared_linear_rating_fails_…`, `test_conservative_interpolation_keeps_…` | implemented |
| F3 | 열 가용 토크 집합이 정적 가능 구간 사이의 공백을 연결 | `thermal.torque_availability`: 정적 segment마다 따로 scan·bisection, segment 번호 보존, `thermal ⊆ static` 불변식 검사, UNKNOWN 표본은 잇지 않음 | `test_thermal_set_cannot_bridge_…`, `test_thermal_set_is_not_joined_across_an_unknown_sample` | implemented |
| G1 | 정격 증거가 적용 제품·조건과 결속되지 않음 | `RatingEnvelope.applies_to` (drive_id·revision·content SHA-256), `control_policy`, `irrelevant_conditions`; `binding()`: 선언된 결속 불일치 → 적용 안 함, 결속 없음·필수 조건(냉각수·Vdc, 유한 정격은 초기 상태) 미처리 → 사용은 하되 모든 claim에 APPLICABILITY_UNCONFIRMED, 요구 층의 미결 항목; 판정 엔진이 평가 드라이브의 식별자를 넘김; case 파일 파서 | `test_rating_bound_to_another_product_…`, `test_bound_and_complete_rating_…`, `test_required_conditions_…`, `test_case_file_rating_fields_…` | implemented |
| 우선순위 1 | 단일 냉간 펄스에서 반복 부하·고온 시작으로: 펄스–휴지–반복, 초기 노드 상태, R_s(T)·T_j 손실 결합; 첫 펄스·반복 정상주기의 허용 시간·토크, 냉각 회복 시간, 지배 노드; Foster 내부 상태는 물리 층 온도가 아님 | `extensions/thermal_cycle.repeated_load`: Foster 항을 스텝마다 정확히(지수) 적분하고 손실을 노드 온도에서 재평가(권선 노드 → R_s(T), 접합 노드 → 모듈 손실 T_j; 드라이브에 R_s 법칙이 없으면 이 분석용 선언 가능, 모듈 표 밖은 중단·외삽 없음); 시작 상태 = 냉각수 평형 / 예부하 정상상태(x = R·P) / 노드 온도(Cauer 사다리만 — 모달 좌표로 정확 변환, Foster는 거절); 주기 정상상태는 주기 사상(항별 아핀)의 고정점으로 정확히 풂(느린 노드도 폐형식과 일치); 허용 펄스 시간·토크, 첫 펄스 허용 토크, 반복 전 필요 휴지, 주기 유지 최소 휴지(폐형식, 뜨거운 온도 모서리의 손실; 구간 끝이 아니라 구간 안의 최고온도 — 지수합의 도함수 근으로 정확히, 시작 노드 온도는 시험 펄스의 기준 온도로 환산, 휴지 부하가 느린 노드를 다시 데우면 허용 휴지 구간의 끝도 표시); 열 페이지 '반복 부하' 탭·그림 | `tests/test_thermal_cycle.py`(폐형식 일치, 첫 한계 시각, 고온 시작, Cauer 노드 온도, R_s 피드백, 허용값 경계, 필요 휴지, 검증된 모델만 결론, 구간 안 최고점, 고온 침지 시작의 첫 펄스 허용값, 휴지 구간), self-test `thermal:repeated_load` | implemented |
| 우선순위 2 | Vdc 입력의 위치를 유지하며 선택적 전원 임피던스: 보장된 단자 전압에서 다시 강하를 빼지 않음, OCV면 Thevenin V_inv = V_oc − I_dc·R_eq와 I_dc = P_dc/V_inv 결합, 구동·회생 부호, 소스 모델 유효 범위 | `analysis/source`: `TheveninSource`(R_eq, 근거, 전류·OCV 유효 범위), `resolve_terminal_voltage`(상근 고정점; 회생은 V_oc보다 높아짐; 축 출력만으로 V_oc²/4R_eq 초과면 어떤 드라이브로도 불가능 — 증명; 모델 무관 단자 전압 상한); `service.resolve_case_source`: 요구 Vdc 포트 `battery_ocv` + `source_model` → 해석된 단자 전압(범위는 끝점 사상)에서 기존 판정, 소스 claim을 AND로 합산(해석 실패면 드라이브 쪽은 OCV에서 — 낙관적임을 명시 — 보이고 소스 claim이 결정), 요구 층 미결 항목; 단자 전압 요구의 레코드 식별자는 불변; 판정 페이지 'Vdc 의미' 선택과 R_eq 입력 | `tests/test_source.py`, self-test `decision:battery_ocv` | implemented |
| 우선순위 3 | 이미 있는 PWM 리플·선 스펙트럼을 같은 운전점의 위험 판정에 연결; 기본파 전류 한계와 실제 순간 피크 구별 | `pwm_policy.point_pwm_risk` / `api.pwm_risk_at`: 판정 witness 운전점에서 같은 변조·캐리어로 기본파 전류(정책의 dq 노름 한계) vs 보수 순간 피크 상한(선언된 소자/과전류 피크 한계와만 비교, 없으면 UNKNOWN), 추가 RMS, 주요 선(차수), DC-link 커패시터 부담, 모터 PWM 손실 구간, 요청≠파형 fsw; 판정 페이지 '추가 분석'에 'PWM 위험 (같은 운전점)' 탭; NVH·베어링 전류·정확한 피크는 평가하지 않음 | `tests/test_review_priorities.py`, self-test `decision:pwm_risk` | implemented |
| 우선순위 5 | 모터 철손의 범위와 손실 분해; 민감도부터; 중복 합산 금지; id/iq 의존 철손이면 certificate 전제가 바뀜 | 원장 회전·철손 항에 범위(속도만의 등가 손실 토크, 부하·약계자·PWM 고조파를 따르지 않음, 별도 철손 맵을 위에 더하지 않음), 인버터+모터 η의 손실 민감도(각 확정 항 +10 %), Vdc 인증서 조건에 '속도만의 회전·철손' 전제를 명시 | `tests/test_review_priorities.py` | implemented (분해 모델은 자료가 생기면) |
| 우선순위 4 | 전압 범위는 표본으로만 → 항상 UNKNOWN; 조건부 충분조건으로 해소 | `decision.vdc_range_certificate`: 정적 순구동(또는 정지)·최소전류 정책·Vdc 무관 `a0 + a2 I²` 손실(a0, a2 ≥ 0, 유효 전압 범위가 요구 범위 전체를 덮음, 모듈 손실 없음)·고정 소스 한계·명령 전압 예산 (1 − r_v)·Vdc/√3 단조 증가·저전압 끝점 FEASIBLE·P_dc > 0 → 정적 subclaim을 범위 전체에서 입증(수학 층 CERTIFIED, 한정자에 인증서 문장). 회생(높은 Vdc에서 충전 전력이 커짐 — 반례를 테스트로 보임)·Vdc 의존 손실·지속시간(정적 부분만 입증, 지속 부분은 표본)에는 적용하지 않음 | `test_vdc_range_is_certified_from_the_low_endpoint_…`(21점 조밀 격자로 |i|·P_dc 단조성 독립 확인), `test_the_certificate_does_not_cover_regeneration_and_why`, `test_no_certificate_with_a_vdc_dependent_loss_or_a_duration` | implemented |
| 사용자 질문 | 온도 plane이 여러 개인 flux map에서 자석 온도가 없으면 capability·T–n이 UNKNOWN('자석 온도 필요') | 판정: 자석 온도를 말하지 않은 요구는 모델이 가진 **모든 plane 온도에서 for-all**(`decision._magnet_points`) — 선언된 온도 보간이 있으면 plane 사이 표본을 더하고 모두 가능해도 SAMPLED_COVERAGE(보간 구간은 표본뿐), 보간이 없으면 plane이 모델이 아는 전부라 모두 가능이면 FEASIBLE, 한 온도라도 불가능이면 INFEASIBLE + 반례 온도. T–n: `sweeps.envelope_family` — plane마다 그 온도에서 정확한 곡선 하나. 한 운전점 페이지(탐색·궤적·설계): `MagnetTempInput`(첫 plane 온도로 미리 설정·표시, 비우면 이유를 말하고 계산하지 않음, 다른 모델로 바뀌면 미지정으로 복귀) | `test_a_multi_plane_map_without_magnet_temperature_…`, `test_declared_interpolation_is_sampled_…`, `test_envelope_family_…`, `test_desktop_requirement_set_page_and_magnet_temperature_inputs` | implemented |
| 사용자 기능 1 | 요구 묶음의 총괄 판정: CSV, 조건/연산자 확인, 요구별 판정·여유·지배 원인·미확인 근거; band 존재성 ≠ 제어 정확도 | `requirement_set.evaluate_set`(같은 제품·조건·근거, 행 = 단일 판정 기록과 동일한 record id), `parse_requirements_csv`(case 파일 파서, 알 수 없는 열·중복 id·모호한 정의 거절, `speed_kind`, `Vdc_port`), `interpretation`(band: '존재성 — 제어 정확도·전 구간 추종 아님'), 결과 CSV; 페이지 **요구 묶음·후보**(해석 확인, 판정 페이지에서 열기) | `tests/test_requirement_set.py` | implemented |
| 사용자 기능 2 | 후보별로 모든 필수 요구를 재평가, 가중합 없음, 비용 자료 없이 비용 최적 없음 | `evaluate_candidates`: 후보마다 모든 요구 재판정, 개선/악화 목록, 전부 만족 여부, 진단용 파라미터 표시, Vdc는 요구의 조건이라 후보에서 거절 | `test_candidates_are_rejudged_…`, `test_candidate_changes_that_are_not_design_changes_…` | implemented |
| 사용자 기능 3 | UNKNOWN을 다음 결정으로: 원인 분리와 추가 자료 우선순위(측정 난이도·판정 영향) | `classify`(모든 Reason이 정확히 한 분류 또는 위반 사유, 원인이 여럿이면 모두 유지), `next_data_priorities`(작업 종류: 선언 < 문서 < 해석 < 새 자료, 그다음 영향 받는 요구 수; '이것만으로 확정' vs '추가로 필요'), `levers`(바인딩 제약 → 완화하는 파라미터); 판정 페이지 배너에 분류와 닫는 방법 | `test_every_reason_is_classed_…`, `test_classify_keeps_every_cause_…`, `test_next_data_is_prioritised_…` | implemented |
| 사용자 기능 4 | 입력 완성도별 진입점: 요구 사양만으로 필요조건, 서로 다른 운전점의 최대 토크×최대 속도 곱 금지, 포트·기계/전기 속도·지속시간 확인 | `spec_check`: 같은 운전점의 P_shaft = T·ω ≥ 0(구동)이면 P_dc ≥ P_shaft → 방전 전력/전류(범위면 최저 Vdc) 한계 초과는 어떤 드라이브로도 불가능; 회생은 손실이 흡수할 수 있어 '모델 필요'; 모델과 같은 허용오차. 모델이 UNKNOWN인 요구를 이 조건이 결정(FAIL, decided_by), 모델 PASS와 모순이면 표시하지 않고 오류 | `test_the_customer_numbers_alone_…`, `test_the_model_never_contradicts_…` | implemented |
| 사용자 기능 5 | 결과 화면 순서: 요구 → 조건·데이터 수준 → 결론/여유 → 제한 원인 → 바꿀 수 있는 항목 → 다음 자료 → 상세 | 요구 묶음 상세 창이 이 순서(1–7)로 표시, 4층 판정·stale 표시는 판정 페이지에 그대로(선택한 요구를 판정 페이지에서 열기) | `test_desktop_requirement_set_page_…`(순서 검사), self-test `requirement_set:*` | implemented |

## 14. PWM 고조파·가변 PWM 인계 (P0)

인계 문서(`PWM_Harmonic_Variable_PWM_Handoff`)의 P0 항목을 현재 코드와 대조해 구현했습니다. 첫 번째 공학 리뷰(13절)를 우선했고,
PR #5의 코드는 병합하지 않고 이 문서의 의미를 현재 코드 기준으로 다시 구현했습니다. 수용 테스트 A–F는
`tests/test_pwm_handoff.py`에 있습니다.

| 항목 | 인계 요구 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| §3.1 | `iron_bound_W`는 실제 철손이 아니라 선언된 **상한** — 이름·표시 | `HarmonicLossData.magnetic_hf_loss_bound_W`(정식, Fe+PM HF), `iron_bound_W`는 호환 별칭(둘이 다르면 거절), anchor 주파수에서만(보간 없음), 합산하지 않고 구간 끝으로만 | C1, C3 | implemented |
| §4 L0 | R_ac 자료 없이도 PWM 동손 **R_dc 하한** 3·R_s(T)·ΣI²(평가한 선 스펙트럼), 0 W 금지, 대역 밖 전역 하한이라 부르지 않음 | `harmonic_copper_loss`: `lower_bound_W`, `bandwidth_Hz`, `status`(ESTABLISHED / LOWER_BOUND_ONLY), R_s는 시나리오의 R_s(T)(기본파 동손과 같은 값) | B1–B3, `test_rs_follows_…` | implemented |
| §4 L1 | R_ac/R_dc(f) 표, 유의 고조파가 표 밖이면 정확값 UNKNOWN(외삽 금지) + 하한 유지, `rac_coverage_I2_fraction` | 같은 함수: 커버리지(ΣI² 비율), 표 밖 유의 선 → 정확값 None | B2, B3 | implemented |
| §4 L2 | hairpin/근접효과 모델 | 임의 형상 모델을 만들지 않음 | — | deliberately unsupported |
| §8 | 주 효율 원장에 PWM 동손·상한 노출, 전력 항등식 유지, control volume | `point_ledger(pwm_hf=…)`: 'PWM harmonic copper'(정확값 또는 ≥ 하한), 'PWM Fe+PM HF magnetic loss'(≤ 상한, 값 아님), `loss_interval_W`, 포트 전력·기본파 η는 그대로(항목에 `in_port_powers`), motor·inverter+motor·eDrive 경계에 `eta_interval_incl_pwm_hf`(구동: P_out/(P_in + x), 회생: (|P_in| − x)/|P_out|) | D, C1, C4 | implemented (P0 노출) |
| §8 P1 | PWM 추가 모터 손실을 P_dc 회계·DC 한계 판정에 정식 결합, 미션 에너지에 같은 의미 | — | — | remaining (P1) |
| §9 | 'best'는 평가 후보 중 최선, Pareto 에너지 축은 확정 손실만, Fe+PM 상한은 기대값이 아님, 겹치면 UNDECIDED | `policy_energy`(구간 [E_inv + PWM 동손(정확/하한), + Fe+PM 상한], 열린 끝 None), `_pareto`(에너지는 구간 분리로만 지배), `_best_interval`, `compare_intervals`(IMPROVED / WORSE / UNDECIDED / UNKNOWN), `ENERGY_CONTROL_VOLUME`(커패시터 ESR·LV 전력은 별도) | E3–E5, `test_control_volume_…`, `test_interval_comparison` | implemented |
| §10 | '리플 포함 피크'는 정확한 피크가 아닌 보수 상한 | `i_peak_bound_A` / `i_peak_bound_max_A`, `peak_current_meaning`; 요청 본문의 한도 이름 `i_peak_bound_max_A`(저장 필드는 기존 `i_peak_incl_ripple_max_A`, 둘이 다르면 거절) | E6 | implemented |
| §11 | 요청 fsw ≠ 파형 모델 fsw를 숨기지 않음 | 구간마다 `fsw_requested_Hz`, `fsw_waveform_used_Hz`, `fsw_error_percent`, 커패시터 모델의 사용 fsw, 0.5 % 초과 시 advisory, 그림에 × 표시 | A4 | implemented; 비동기 캐리어 파형 엔진은 remaining |
| §12 | 공급된 NTC 궤적 ≠ 폐루프 열 시뮬레이션 | `thermal_scope` 메타데이터 | F | implemented; 폐루프(P4) remaining |
| §14 | EMI/NVH/베어링 전류 영향은 평가하지 않았다고 명시 | `not_evaluated` | F | implemented |
| §15 | 에너지 패널: 확정 스택 + 상한은 구간(해치/whisker), 필수 제약 표의 이름, 상세 표 | 그림(확정 스택, R_dc 하한 해치, 비교 구간 whisker/열린 화살표, 요청≠파형 fsw 표시), 페이지 상세 행(에너지 구간, fsw 오차, 제어 체적, 열 범위, 미평가) | 그림 렌더 테스트 | implemented |
| §16 | 프로젝트 데이터 `motor_hf`(L_hf, R_ac, magnetic bound) | 효율 페이지는 가변 PWM 페이지와 같은 선언 예시(합성, 표시됨)를 사용 | B4 | remaining (P1) |
| §19 A | 시간 적분 RMS ≈ 스펙트럼 RMS, fsw 2배 → 리플 1/2, m = 0 리플 0, 비정수 fsw/fe | 기존 모델 확인 | A1–A4 | implemented |
| §6·§7 | Fe/PM ROM, THD→철손 계수 금지 | THD 계수를 만들지 않음, 보정 ROM은 자료가 생기면(P3) | — | deliberately unsupported (P3) |

## 15. 로직 검토 의견 (기준 63a2b61, `docs/LOGIC_REVIEW.html`)

로직 검토 문서에 대한 검토 의견을 하나씩 재현하고 판단했습니다. 타당한 지적은 반영했고, 더 나은 방법이 있으면 그 방법으로 진행했으며
(표의 "구현"에 이유), 상세 설명은 `docs/LOGIC_REVIEW.html` §18에 있습니다. 회귀 시험은 `tests/test_review_63a2b61.py`에 있습니다.

| 항목 | 의견 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| 2.1 | Pareto: 같은 에너지 불확도 구간이 정책을 지움 | `_pareto`: 구간은 분리될 때만 지배, 같은 구간은 아무것도 정하지 않음(점 구간만 같은 값). 기존 시험 E3·E5 기대값 수정 | `test_equal_energy_intervals_never_remove_a_policy` | fixed |
| 2.2 | 반복 부하: 첫 한계가 노드 순서에 의존 | `repeated_load`: 모든 노드 교차 시각의 최솟값, 같은 시각 노드 목록 | `test_the_first_limit_does_not_depend_on_the_node_order` | fixed |
| 2.3 | 단계 내부 피크·교차를 놓침 | 적응 격자 대신 지수합 정류점의 정확한 근 분리(`_expsum_zeros`, Descartes 규칙, Brent) — 격자 없이 모든 정류점 | `test_exponential_sum_zeros_are_isolated_exactly`, `test_a_peak_microseconds_into_a_long_phase_is_found_exactly`, `test_the_phase_maximum_is_never_below_a_dense_sample_of_the_trajectory` | fixed |
| 2.4 | Thevenin: 제곱근 정의역 오류 | 증명과 근이 같은 경계 대역(`MAX_TRANSFER_REL_TOL`), 대역 안은 UNKNOWN(BOUNDARY_WITHIN_TOLERANCE) | `test_the_maximum_transfer_boundary_is_one_rule`, `test_a_boundary_source_case_is_an_open_answer_at_the_boundary` | fixed |
| 3.1 | 고속 약계자 철손, 단계적 충실도 | 약계자 witness의 \|ψ\|/\|ψ₀\|, 토크·DC 여유, 1 kW ↔ N·m, 방향(비보수/보수/미증명)을 기록·해석에 표시(`iron_loss_scope`), 오차 예산과 연결. 커널은 그대로(증명이 속도만 손실 형태를 전제) | `test_a_field_weakening_witness_states_what_rests_on_the_loss_model`, `test_a_point_below_the_voltage_limit_has_no_field_weakening_statement` | implemented (단계 0); 자속 의존 철손·FEA 맵 remaining |
| 3.2 | 자석 온도 피드백 | 노드의 선언 역할 `temperature_of`(winding/junction/magnet), 자석 노드 온도가 운전점 자속을 정함 | `test_a_declared_magnet_node_feeds_the_flux_of_the_operating_point`, `test_one_node_per_represented_temperature` | implemented |
| 3.2 | hot-corner 단조성 가정 | 온도 상자 2^k 모서리의 최댓값(방향 무관) + 모서리 중점·중심 점검, 실패 시 ESTIMATE | `test_the_allowed_values_say_when_the_corner_losses_are_not_a_bound` | implemented |
| 3.2 | 주기 수렴은 내부 상태로 | 모든 Foster 항의 잔차(`_state_residual`, 0.02 K) | `test_the_periodic_cycle_is_judged_on_every_foster_term` | implemented |
| 3.2 | 시간 스텝·캐시 민감도 | 스텝 2배·캐시 ½ 재계산(`_resolution_compare`: 첫 한계·피크·주기 피크·허용값), 판정이 바뀌면 UNKNOWN(NUMERICAL_UNRESOLVED) | `test_a_verdict_that_changes_with_the_resolution_is_not_a_verdict`, `test_the_default_run_reports_its_resolution_check` | implemented |
| 3.3 | 동기 캐리어 근사 | 요청 주파수를 사이에 둔 두 동기 캐리어(`carrier_brackets`)를 모두 평가, 두 답이 같을 때만 판정(민감도 괄호 — 비동기 드리프트 상한 아님) | `test_the_waveform_models_bracket_a_non_integer_pulse_ratio`, `test_a_limit_between_the_two_bracketing_carriers_is_not_decided` | implemented; 비동기 재평가 remaining |
| 3.3 | 에너지 목표가 커패시터를 뺌 | 선언 시 커패시터 ESR 손실 구간을 에너지 구간에 포함(`capacitor_in_energy_boundary`), 제어 체적 설명이 따라감 | `test_control_volume_…`(test_pwm_handoff) | implemented |
| 3.3 | 폐루프 vs 공급 센서 | 기존 `thermal_scope` 문구 유지 | F | implemented; 폐루프 remaining |
| 4 | 모델 판정 vs 오차 예산에 강건 (y + Δ ≤ y_max, Δ의 출처) | 다섯째 판정 층 `robustness`(`analysis/error_budget.py`, `requirement_robustness`): 물리량별 최악 조합 합; 토크는 능력 여유(충족은 witness·불충족은 인증 상한), 상전류·DC 전력·DC 전류·명령 전압은 운전점의 한계 여유(`constraint_robustness`; 약계자 전압 한계는 토크 쪽으로); 판정 불변; 판정 화면 입력·배너·해석·기록 | `test_a_limit_margin_is_compared_with_its_declared_error_in_its_own_unit`(의견의 500 A·488 A·±20 A 예), `test_the_limits_at_the_witness_join_the_torque_margin_in_one_statement`, `test_the_guarded_rule_on_the_capability_margin`, `test_the_layer_never_changes_the_model_verdict` 외 | implemented; 요구 묶음·열·PWM 한계 확장 remaining |
| 4 | 한정자 의미(대역 ∃, 범위 ∀)를 입력·결과에 | 판정 폼의 토크 해석(achieve/band)과 "판정할 질문", 결과의 "판정한 질문", 기록 `quantifiers` | `test_the_quantifiers_are_written_out_in_the_record_and_the_reading`, `test_the_form_states_the_question_and_sends_the_band_and_the_error_budget` | implemented |

## 16. 공학 리뷰 2 (기준 63a2b61: 본 보고서 F-01…F-19·P3, DC 전원·DC-link 하위 F1…F12, 열 하위 F1…F11)

리뷰는 로직 검토 반영(§15) 이전 판을 보았으므로 모든 지적을 리뷰어 스크립트로 b9fb69c에서 다시 재현한 뒤 판단했습니다. 타당한
지적은 반영했고, 더 나은 방법이 있으면 그 방법으로 진행했으며(표의 "구현"에 이유), 상세 설명은 `docs/LOGIC_REVIEW.html` §19에
있습니다. 회귀 시험은 `tests/test_engineering_review_63a2b61.py`에 있습니다.

| 항목 | 의견 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| F-04 (하위 F1) | 회생 OCV를 OCV에서 판정 → 거짓 FAIL(−203.72 N·m, 10/30 mΩ) | `resolve_terminal_voltage`: 수동성 상한 V_hi에서 시작하는 단조 반복(I² 손실에서 f(V) 단조 → 가장 큰 고정점), 604.04/611.99 V에서 PASS; 해가 없으면 드라이브 쪽은 V_hi에서 표시, 그 위반은 결정적이지 않음(`_not_decisive`) | `test_f04_regeneration_is_resolved_above_the_ocv_where_the_point_exists`, `test_an_unresolved_source_never_turns_a_drive_side_violation_into_a_fail` | fixed |
| F-14 (하위 F2) | 증명 가능한 구동 FAIL이 UNKNOWN(350 mΩ: 455 V < 479.6 V) | 반복점에 정책점이 없거나 판별식 음수면 NO_SOLUTION(단조성이 있을 때만) | `test_f14_a_motoring_request_the_source_cannot_carry_is_a_proven_fail`, `test_without_the_i2_form_leaving_the_existence_set_proves_nothing` | fixed |
| 하위 F3 | Picard에 괄호·이력 없음 | 반복 이력·수축률·고정점 문구 기록 | `test_the_iteration_records_its_history_and_conditioning` | implemented |
| F-01 | 자속맵 정책 UNKNOWN 띠(B&B 절대 간격) | 결과로 인증: [lb, found]의 I² 구간 양 끝이 같은 판정이면 구간 전체(P_dc가 I²에 단조), DC 한계를 가로지르면 UNKNOWN | `test_f01_an_uncertified_map_point_is_decided_by_its_consequence`, `test_f01_the_status_is_monotone_in_torque_and_the_capability_scan_has_no_unknown_swath`, `test_f01_a_bracket_that_straddles_a_dc_limit_stays_unknown` | fixed |
| F-02 | 말하지 않은 온도를 기준 온도에서 판정 | 선언 법칙의 모든 온도(∀): 권선은 구동에서 최대 Rs 인증서(`winding_law_certificate`), 그 외 표본(FEASIBLE → UNKNOWN(SAMPLED_COVERAGE)); 판정 폼 권선 온도, 기록·해석의 한정자 | `test_f02_a_request_that_fails_hot_is_no_longer_a_pass_at_the_reference_temperature`, `test_f02_motoring_is_certified_from_the_largest_rs_and_regen_stays_sampled`, `test_f02_the_worst_end_certificate_holds_numerically`, `test_f02_a_stated_temperature_is_judged_at_that_temperature_only` | fixed |
| F-03 | 손실 제한 여유의 민감도 없음 | `analysis/margin_sensitivity.py`: DC 한계로 정해진 여유에 손실 예산(W)과 파라미터 손익분기(한 스텝 유한차분), 기록·해석·Markdown 표; 판정 불변 | `test_f03_the_flagship_margin_is_stated_as_a_loss_budget_and_break_even_changes`, `test_f03_a_current_limited_margin_carries_no_loss_sensitivity`, `test_f03_the_markdown_record_has_the_sensitivity_table` | implemented |
| F-15 | 조건 없는 rating envelope이 FEASIBLE | `open_conditions`: 필요한 조건이 없거나 무관 선언이 없으면 UNKNOWN(APPLICABILITY_UNCONFIRMED) + 요구 층 미결 항목 | `test_f15_a_condition_less_envelope_is_not_a_duration_pass_at_any_condition`, `test_f15_stated_or_declared_irrelevant_conditions_restore_the_envelope`, `test_case_file_rating_fields_and_the_decision_record` | fixed |
| F-17 | 효율 맵 = 모델 효율 | `MODEL_EFFICIENCY`를 점·지도·미션·A/B 결과, 그림 제목, 해석에 | `test_f17_efficiency_results_are_labelled_model_efficiency` | implemented |
| F-18 | 구간 분석: 모든 모서리 통과여도 UNKNOWN | `vertex_certificate`: 고정점의 게이트 양이 파라미터마다 아핀·볼록·단조 → 꼭짓점이 최악; 고정 캘리브레이션 FEASIBLE, 토크를 움직이는 파라미터가 없으면 공통 운전점으로 적응형도 FEASIBLE | `test_f18_all_corners_passing_is_a_proof_where_the_vertex_certificate_applies`, `test_f18_a_common_witness_settles_the_adaptive_claim_when_the_nominal_calibration_fails`, `test_f18_the_certified_point_holds_inside_the_box` 외 2 | implemented; 토크를 움직이는 파라미터의 적응형 claim remaining |
| F-19 | 충전 한계 제동 능력에 witness 없음(seed 2·3) | `_verify`: id 고정, iq를 정확한 가능 구간 안쪽으로(원점 쪽 당김은 대체) | `test_f19_a_witness_on_a_charge_limit_edge_is_moved_inside_not_lost` | fixed |
| F-05 (열 F1) | 지속시간이 Rs(T) 무시 | 법칙·되먹임이 관련되면 반복 부하 엔진으로 결합(`_coupled_duration`), 가용 토크는 뜨거운 모서리 손실 상한 | `test_f05_a_declared_rs_law_makes_the_duration_claim_self_consistent`, `test_f05_availability_with_the_law_is_bounded_at_the_hot_corner` | fixed |
| F-06 (열 F2) | 반복 부하 조기 종료·지평 단축 | 주기 정상상태 먼저, 지평 전체(도달 후 반복), 주기 초과를 claim에 | `test_f06_the_requested_horizon_is_never_truncated`, `test_f06_a_periodic_exceedance_is_part_of_the_claim`, `test_f06_a_run_that_reaches_the_periodic_state_repeats_it_for_the_rest_of_the_horizon` | fixed |
| F-07 (열 F3) | 검증 안 된 모델이 되먹임으로 INFEASIBLE | 정지는 t = 0 또는 적격 모델일 때만 결정적 | `test_f07_an_unqualified_model_never_proves_a_violation_through_its_own_temperature_estimate` | fixed |
| F-08 (열 F4) | 열원 감시를 첫 단계만 | 모든 단계 손실의 합집합 + 뜨거운 모서리 | `test_f08_heat_source_coverage_is_checked_over_every_phase` | fixed |
| F-11 (열 F6) | 되먹임 스텝 지연 | Heun 예측-수정(2차) | `test_f11_the_feedback_step_is_second_order` | fixed |
| F-13 (열 F7) | 열 판정 허용 대역 | 선언된 `uncertainty_K`(근거 필수) 안은 UNKNOWN(BOUNDARY_WITHIN_TOLERANCE), 없으면 한정어 | `test_f13_a_margin_inside_the_declared_uncertainty_is_not_decided` | implemented |
| F-12 | 교차 탐색 다봉 | 이미 반영(§15의 2.3) | 기존 회귀 시험 | already fixed |
| 열 F5·F10·F11 | 가용 토크 적격 문구, 냉각수 단순화 보고, 기타(None → NaN, 휴지 창) | 손실을 넘겨 같은 적격 규칙, 물성 평가 온도·클램프·스테이션 없는 노드 보고, 421점 탐색 | `test_thermal_f5_…`, `test_thermal_f10_…`, `test_thermal_f11_…` | fixed |
| F-09 (하위 F4) | UCG 위 차단 과전압 FEASIBLE | 반응 경로 입력(freewheel → INFEASIBLE, 미지정 → UNKNOWN(COUPLED_MODEL_REQUIRED), ASC → 수지), 전압 한계 유입 전력으로 V_peak 상한 | `test_f09_above_the_ucg_speed_the_reaction_path_decides`, `test_f09_the_inflow_is_bounded_at_the_voltage_limit` | fixed |
| 하위 F5 | 음의 허용 반응 시간 | 0으로 자르고 램프만의 위반은 증명된 위반, 허용 최대 램프 | `test_f5_a_ramp_longer_than_the_headroom_is_a_proven_violation_not_a_negative_time` | fixed |
| F-10 (하위 F6·F9) | ESR 게이트가 FFT 길이를 잼, 게이트는 I_inv | 모델 대역에 맞춘 샘플 수 + 에너지 기준(표 밖 전류 제곱 ≤ 5 %면 최대 ESR로 실음), 결과가 요청 샘플 수와 무관 | `test_f10_the_esr_gate_follows_energy_not_the_fft_length`, `test_f10_the_result_does_not_depend_on_the_requested_samples_per_carrier`, `test_f10_a_table_that_misses_the_switching_band_still_gates` | fixed |
| F-16 (하위 F7) | 요구 수명 없이 수명 FEASIBLE | 요구 수명 필수(없으면 UNKNOWN(REQUIREMENT_INCOMPLETE)), 전압 점검은 Vdc + 리플 피크 | `test_f16_an_expected_life_is_not_a_pass_without_a_required_life` | fixed |
| 하위 F12, P3 | 거짓 정밀도 | `units.shown`: 물리량 종류별 표시 자릿수; 커패시터 손실·hotspot 샘플링 분해능 | `test_f12_model_limited_quantities_are_shown_at_the_precision_the_model_supports`, `test_f12_the_capacitor_loss_and_hotspot_carry_their_sampling_resolution`, `test_f12_terminal_voltage_text_is_rounded_the_value_is_not` | implemented |
| P3 | DPWM1 지원 불일치 | 모든 펄스 패턴 소비자가 `MODULATIONS`와 `duties`를 공유, 클램프 레그를 레일에 정확히 | `test_p3_dpwm1_is_accepted_by_every_pulse_pattern_consumer_and_matches_a_brute_force_ripple`, `test_p3_the_clamped_leg_sits_exactly_on_its_rail` | fixed |

## 17. 인과 고장 시뮬레이션·기능안전 (FuSa)

요구: 인버터 고장과 기능안전 반응을 고장 → 측정·추정 → 제어·감시 → 보호 → 실제 토크·전류·DC-link → 안전 요구 판정까지
**하나의 인과 궤적**으로 계산하고, SG/FSR/TSR 추적, FDTI/FRTI/FHTI, 캠페인·반례, provenance·stale, 검증, 화면·저장·내보내기까지
갖출 것. 상세 설명은 `docs/LOGIC_REVIEW.html` §20, 회귀 시험은 `tests/test_faultsim.py`·`tests/test_fault_page.py`에 있습니다.

| 항목 | 요구 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| 인과 사슬 | 참값·측정·추정·명령·실제를 나누고, 감시·반응은 측정만 봄 | `extensions/faultsim/engine.py` (`_Sim.apply_fault` · `read` · `update_bridge` · `actual_bridge` · `simulate`): 센서 고장은 측정을 바꾸고 폐루프가 실제 전류·토크를 바꿈, 반응 명령과 실제 브리지 상태를 따로 기록, 판정은 참값 | `test_sensor_fault_acts_through_closed_loop_control_and_is_seen_by_measured_channels_only`, `test_fault_free_operation_keeps_the_kernel_operating_point_and_never_reacts` | implemented |
| 플랜트 | 스위칭 다리, 6SO 정류, ASC, DC-link·배터리·접촉기·BMS·블리더·능동 방전 | `extensions/faultsim/plant.py`: 상보성 다리(떠 있는 다리, 다이오드 도통, 레일 한 걸음 앞 보기), 사건 1 ns 위치, Zeno 구간 고정 스텝, 에너지 수지 매 실행, 평균값 / 스위칭 PWM | `test_plant_against_closed_forms_independent_abc_reference_and_convergence`, `test_a_floating_leg_at_the_rail_conducts_instead_of_floating_beyond_it`, `test_open_circuit_below_the_onset_carries_no_current_and_six_switch_off_above_it_brakes`, `test_asc_is_not_torque_free_at_low_speed_and_six_switch_off_is_not_current_free_at_high_speed` | implemented |
| 센서·제어기 | 고장 모드, 지연, 제어 격자, MCU 리셋·재시동 | `sensors.py`(오프셋·이득·고착·마지막 값·소실·수송 지연, 자기 진단), `control.py`(정책표 전류 지령의 이산 FOC, 각도 지연 보상, 명령 경로, flying / cold 재시동) | `test_flying_restart_recovers_and_cold_restart_ends_in_the_safe_state`, `test_recovery_attempts_with_a_persistent_fault_trip_again_and_latch` | implemented |
| 메커니즘·경로·자원 | 검출·반응 경로·공유 자원·우선순위·안전 상태 선택을 데이터로 | `faultsim/protection.py`(`SafeStatePolicy.decide`: 측정 정보만, 속도 히스테리시스, 실행 불가 ASC 교체), `ResourceBook`(자원 상실 → 센서·메커니즘·경로 동시 상실), `configure.py`(`validate_section`) | `test_protection_path_lost_backup_reacts_later_and_delay_violates_the_rating`, `test_shared_current_sensor_supply_blinds_control_and_the_hardware_comparator`, `test_gate_supply_loss_reported_by_uvlo_selects_the_other_side`, `test_shorted_switch_selects_the_asc_of_its_own_side_and_a_speed_only_policy_does_not`, `test_architecture_is_project_data_and_is_validated`, `test_independence_view_names_the_shared_sensors_and_resources` | implemented |
| 요구·판정 | SG → FSR → TSR, 동적 토크 창, 한계, 안전 상태, 오반응 없음, 시간 | `faultsim/safety.py` `Evaluator`: 판정 다섯 종류, 사건 정의(\(t_F, t_V, t_D, t_R, t_S\)), 관측 위반 FAIL, 창 미관측 UNKNOWN, 안전 상태 마감 규칙, FRTI·FHTI 하한, 분해능·모델 허용 대역, 적용 범위(시나리오 하나), SG는 인버터 근거 + 차량 지표만 | `test_event_definitions_are_explicit`, `test_a_violation_before_leaving_the_model_is_a_fail_and_an_unobserved_window_is_unknown`, `test_a_safe_state_missed_by_its_deadline_is_a_fail_even_if_the_hold_window_is_cut_off`, `test_sg_carries_inverter_evidence_and_a_vehicle_indicator_never_a_vehicle_approval`, `test_tight_monitor_trips_on_a_healthy_torque_step_false_detection`, `test_monitor_without_independent_request_misses_a_stale_command_common_cause` | implemented |
| 대표 시나리오·후보 비교 | 성공·지연·잘못된 반응·경로 상실·공통 원인·오검출·복귀 실패 | `faultsim/study.py`(`explain`, `compare`): 분류는 결과와 맞아야 함(시험이 확인), 같은 초기 조건에서 보호 적용/미적용·ASC-low·ASC-high·6SO·토크 0 | `test_every_representative_scenario_is_declared_with_its_category`, `test_frozen_position_sensor_makes_the_policy_choose_six_switch_off_at_high_speed_wrong_reaction`, `test_candidate_comparison_finds_no_executable_safe_reaction`, `test_candidate_comparison_on_regeneration_disconnect_prefers_asc`, `test_fault_instant_and_initial_angle_change_the_peaks_and_the_timing` | implemented |
| 캠페인·반례 | 탐색, 경계, 최악값, 반례 기록·재실행·stale | `faultsim/campaign.py`: 격자·시드 고정 무작위, 판정 경계 이분, 물리량별 최악값과 그 실행, 반례(시나리오·판정·수치·프로젝트/코드 식별) 저장·열기·재실행, 관련 섹션 변경 시 stale | `test_campaign_brackets_the_failure_boundary_and_keeps_worst_cases_per_run`, `test_campaign_axes_and_scenario_paths`, `test_a_counterexample_is_rerun_saved_opened_loaded_and_marked_stale` | implemented |
| 검증 | 같은 식의 다른 적분만으로는 부족 | `faultsim/reference.py`: 폐형식(대칭 ASC 정상상태, 개방 회로), 독립 abc 정식화(\(L(\theta)\), 컨덕턴스 소자, Radau, 이상화 경향), 행렬 지수, 스텝 수렴, 에너지 수지 — 찾은 부분 단락 결함 수정 | `test_plant_against_closed_forms_independent_abc_reference_and_convergence` | implemented |
| 화면·저장 | 실행·해석·비교·캠페인·검증 화면, 저장·내보내기, 작업 공간 | `desktop/pages/fault_sim.py`(안전·보호 → 고장 시뮬레이션·FuSa), `insight/fault.py`, `plots/fault_figures.py`, 시나리오 파일·반례 파일, CSV/JSON, 작업 공간 복원, self-test 화면 25b–25i | `test_a_representative_scenario_runs_and_reads_on_the_page`, `test_reaction_candidates_run_from_the_same_initial_condition`, `test_scenario_files_round_trip_and_the_page_survives_a_workspace`, `test_insight_pages`, `test_workspace` | implemented |
| 한계 | — | 상수 파라미터 모터(자속 지도 드라이브 거절), 이상 소자(SOA·단락 내량 없음, 다리 단락은 모델 밖), 평균값 PWM 기본, 3상 2-level만(다른 토폴로지 선언은 거절), 강체 구동계·일정 속도, 열 비적분 | `docs/LOGIC_REVIEW.html` §20.10 | remaining |

## 18. 공학 리뷰 3 (남은 영역 수치 감사: 새 지적만 — F-10, F-11, F-22, F-23, F-24, 새 P3)

앞선 검토에서 다룬 지적(§16)은 제외하고, 이번 리뷰에서 새로 나온 지적만 130fb33에서 재현한 뒤 판단했습니다. 상세 설명은
`docs/LOGIC_REVIEW.html` §21, 회귀 시험은 `tests/test_review_r3.py`(리뷰의 적대 사례 A21–A25 포함)에 있습니다.

| 항목 | 의견 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| F-10 (P1) | PROT-04가 사용자 지평으로 결정(지연 2 ms: 지평 0.3 ms FEASIBLE, 0.1 ms INFEASIBLE) | `_prot04`: 지평 안에서 확정되면 반응 시각이 정해지므로 플랜트 정확해로 판정(예정 반응 전 교차는 이분법 교차 시각과 함께 INFEASIBLE, 아니면 작동 후 정확한 상한), 확정이 지평 밖이면 UNKNOWN(결코 INFEASIBLE 아님). PROT-03도 같은 정확해. 해석 지표는 PROT-04를 따름. 리뷰 기대(0.3 ms UNKNOWN)와 달리 INFEASIBLE — 같은 모델의 정확해가 0.375 ms 교차를 증명하고 1·5 ms 지평과 같은 판정·교차 시각 | `test_a21_prot04_is_decided_by_the_reaction_not_by_the_horizon`, `test_a21_an_action_after_the_horizon_is_judged_on_the_exact_solution`, `test_f10_prot03_reads_the_same_exact_solution_beyond_the_horizon`, `test_f10_the_reading_follows_the_prot04_verdict` | fixed |
| F-11 (P1) | RBW 창의 선합은 추정이 아니라 상한(+8.8 / +11.1 dB), 필요 감쇠 과대 | 추정 = 직사각 IF 포락 최대(`envelope_peak`: FFT 과표본 + 표본 극대마다 뉴턴, 전수 탐색 대비 2.7×10⁻¹⁶), 상한 = 선합·가우시안 IF 가중합(`_gaussian_cells`), 반례 = 네 귀환 모델의 최소 포락; 필요 감쇠는 추정 기준(상한 기준 병기, 화면·해석의 최대값도 판정과 같은 대역 정확값), 창당 선 수. 추가로 선합을 반례의 하한으로 쓰던 것을 고침 | `test_a22_the_estimate_does_not_depend_on_the_fundamental_the_line_sum_does`, `test_f11_the_estimate_equals_an_independent_dense_receiver_sweep`, `test_f11_gaussian_if_cells_bound_every_tuning_inside_them`, `test_f11_a_line_sum_exceedance_alone_is_never_a_witness`, `test_envelope_peak_is_an_attained_maximum_never_above_the_line_sum`, `test_f11_the_largest_required_attenuation_is_the_exact_band_figure_on_the_page_and_the_reading` | fixed |
| F-23 (P2) | CM 귀환 ½·½ 고정, 에지 부호 주입과 12.6 MHz +7.7 dB | 네 귀환 모델(`RAIL_MODELS`: 중점·에지 부호·HV+·HV−), 한 번 분해 + Woodbury(`port_transfers`), 추정은 물리적 두 모델, 상한·반례는 네 모델. 예제 3.8 MHz에서 에지 부호가 정적 두 극단보다 7.2 dB 높음(감싸지 못함)을 확인 | `test_f23_the_edge_sign_return_is_not_bracketed_by_the_static_rails`, `test_f23_the_bound_covers_every_cm_return_model` | fixed; LPTV 망은 remaining |
| F-22 (P2) | HEV 캐시가 (이름, T, V) — 같은 이름 두 기계가 한 점 공유(거짓 FEASIBLE) | 캐시 키 = 기계 객체(항목이 객체 보유), 한 버스 기계 이름 유일(결합 집합·비조정 버스) | `test_a23_hev_points_are_cached_per_machine_and_names_are_unique` | fixed |
| F-24 (P2) | ASC 수치 허용치가 격자 오차의 1/18, 궤적이 선언 영역 2.1배 밖 | 정속 해의 피크·전체 dq 피크·최소 i_d를 표본 사이 정확해로 정밀화(`_refine_max`), 허용치는 정밀화 뒤 하한·두 격자 차·표본 곡률 한계 중 최대, 소자 생존 피크 비교에 곡률 한계; 모델 전류 영역 항목(OUTSIDE_DECLARED_DOMAIN 2.12배)을 결과·claim·해석에 | `test_a24_asc_peak_is_refined_and_the_allowance_bounds_the_grid_error`, `test_f24_the_event_peak_and_minimum_id_are_refined_like_the_requirements` | fixed |
| P3 OEW | 공통 버스 off/off 임계에 3고조파 역기전력 누락 | 기본파 + 3고조파의 선언 위상 정확 피크와 위상 무관 상한, 영상분 없으면 UNKNOWN(MISSING_INPUT) | `test_a25_common_bus_off_off_threshold_includes_the_triplen_emf` | fixed |
| P3 드라이브라인 | `a_final` = 마지막 5 % 평균(반 주기 → 1.052) | 창 뒤쪽 정수 개 비틀림 주기 평균, 근거 문구 | `test_driveline_final_level_is_averaged_over_whole_torsional_periods` | fixed |
| P3 A/B | 오차대 선형합의 성격 미표기 | 근거 문구(선언 한계의 최악)와 독립 오차 RSS를 보고, 순위는 선형합 | `test_ab_error_band_is_the_linear_sum_of_the_declared_bounds_and_states_it` | implemented |
| P3 EMI | 설계 예비 공란 → 조용히 0 dB, 공백이 많은데 "모든 수신 주파수" | 공란 메모·플래그·해석 지표, FEASIBLE 문구에 판정하지 않은 선언 공백 구간 | `test_emi_p3_blank_reserve_is_stated_and_declared_gaps_are_named` | fixed |
| P3 EMI | 획득 메타데이터 없는 측정 trace는 INDETERMINATE(FAIL 아님) | 리뷰대로 보수적 — 변경 없음 | 기존 시험 | kept |

## 19. 기능안전 설계 작업 (고장 시뮬레이션·FuSa 사용성·심사 근거)

요구(사용자): FTTI·TSR 요구 수치·검출 디바운스를 조정할 수 있을 것, 반응을 단일 상태가 아니라 소프트 ASC 등 과도를 줄이는 대표
방법들과 "FW 잠깐 → ASC" 같은 단계로 설계할 수 있을 것, 보호 반응 설계 변경을 JSON 직접 입력 없이 할 수 있을 것, FSR·TSR 보강,
Safety 심사관 관점의 근거. 상세 설명은 `docs/LOGIC_REVIEW.html` §22, 회귀 시험은 `tests/test_fusa_design.py`·`tests/test_fault_page.py`에 있습니다.

| 항목 | 요구 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| FTTI·예산·TSR 수치 편집 | 타입이 있는 칸, JSON 없음 | `desktop/fault_design.py` `DesignEditor`: SG(ASIL·FTTI·위험·안전 상태·운전 상황), FSR(ASIL·FDTI/FRTI 예산·할당·안전 상태 TSR·경고·검증 방법), TSR(유형별 판정 기준 칸·안전 상태 조건 표·할당·검증·근거), id 바꾸면 참조도 바뀜, 편집마다 섹션 검증 | `test_the_design_editor_changes_ftti_tsr_limits_debounce_and_strategies_without_json` | implemented |
| 디바운스·필터·임계값 편집 | 메커니즘별 | 메커니즘 표(주기·임계값·디바운스/필터·잠재 고장 시험·커버리지) + 종류별 매개변수 칸(`KIND_PARAMS`), 범위·선택지 검증(`_check_params`) | 위 시험, `test_engineer_entered_values_are_checked_where_they_are_entered` | implemented |
| 설계 변형 | 실행·저장·추적·반영 | `extensions/faultsim/design.py`(`diff_overrides`, `change_rows`: 없는 키·None·빈 컨테이너는 변경 아님), `VariantField`(입력 추적·작업 공간), 시나리오 불러올 때 편집 변경 교체 전 확인, 프로젝트에 반영(`with_section`) | `test_a_design_variant_is_the_difference_and_applies_back`, `test_empty_containers_and_missing_keys_are_not_changes`, `test_removing_and_reordering_items_are_named`, `test_the_edited_design_is_written_into_the_project_and_survives_a_workspace`, `test_scenario_files_round_trip_and_the_page_survives_a_workspace` | implemented |
| 반응 전략 | 단계열, 소프트 ASC 대표 방법, FW → ASC | `extensions/faultsim/strategy.py`(동작 11종·종료 조건 7종·최대 시간·대체 상태·템플릿 7종), `engine.py`(`_start_strategy`·`_enter_step`·`_advance_strategies`·`_strategy_fallback`·`_rank`, 이미 실행 중이면 재시작 안 함), `control.py`(토크·토크 램프·ASC 점 전류 사전 조정·전압 램프 모드), `asc_steady_point` | `test_soft_asc_by_voltage_ramp_removes_the_asc_transient`, `test_current_preconditioning_ends_by_its_maximum_time_when_the_measurement_is_wrong`, `test_freewheel_first_and_phase_sequential_asc`, `test_a_software_strategy_on_a_hardware_path_falls_back_to_its_bridge_state`, `test_asc_while_fast_then_freewheel_below_the_declared_speed`, `test_torque_ramp_down_then_freewheel_takes_the_declared_rate`, `test_a_running_strategy_is_not_restarted_by_a_second_request`, `test_the_policy_selects_a_strategy_through_a_design_variant`, `test_the_asc_steady_point_solves_the_steady_dq_equations`, `test_strategy_declarations_are_validated` | implemented (개념 모델: 상수 파라미터 ASC 점, 개루프 전압 램프; HW 소프트 ASC는 모델 밖) |
| 후보 비교 | 전략을 후보로, 과도 지표 | 후보 체크 목록(기본 반응 + 선언 전략), 강제 반응에 전략, 비교 표에 최저 i_d·최대 제동 토크, 단계 기록(해석·툴팁) | `test_reaction_candidates_run_from_the_same_initial_condition` | implemented |
| FSR·TSR 보강 | 심사 속성 | `safety.py`: SG 안전 상태·운전 상황, FSR ASIL·경고·할당·검증 방법, TSR ASIL·할당·검증 방법·근거, 물리량 i_d·제동 토크, 예제 TSR-08(제동 토크 ≤ 450 N·m)·TSR-09(i_d ≥ −1,000 A), FTTI·예산 > 0, 판정 기준 시간·폭 ≥ 0 | `test_engineer_entered_values_are_checked_where_they_are_entered`, `test_a_representative_scenario_runs_and_reads_on_the_page` | implemented (값은 합성 예제) |
| 정적 설계 검토 | 시뮬레이션 전 일관성 | `extensions/faultsim/review.py`: 모순·누락·경고·참고, ASIL 상속, 예산 합 ≤ FTTI, 검출 지연 범위(최소 초과 = 모순, 최대만 = 경고), 전략 시간 대 FRTI(경고), 잠재 고장 시험, 실행 가능성, 시간 판정 TSR 없는 예산, 공통 원인 | `test_the_example_design_has_no_contradiction_or_gap_and_names_its_weak_points`, `test_the_review_finds_contradictions_between_declared_values`, `test_the_review_finds_gaps_and_unexecutable_strategies`, `test_a_detection_bound_is_a_contradiction_only_when_its_minimum_exceeds_the_budget` | implemented |
| 검증 매트릭스·FMEA | 카탈로그 전체 근거 | `extensions/faultsim/verification.py`: 한 설계로 카탈로그 실행(시나리오 자신의 변형 제거, 중복 제외), 요구 × 시나리오, 발동 수, FSR별 최악 시간(한 궤적), 시뮬레이션 기반 고장 모드 표 | `test_the_catalog_runs_on_one_design_without_its_own_design_variants`, `test_the_verification_matrix_counts_coverage_and_worst_times` | implemented (시나리오 커버리지 — 진단 커버리지 SPFM/LFM 아님) |
| 안전 근거 보고서 | 심사용 한 파일 | `extensions/faultsim/report.py` `safety_case_html`(설계·변형, 요구, 검토, 전략, 매트릭스, 공통 원인, 반례, 한계; 매트릭스는 같은 설계·프로젝트일 때만, 커밋되지 않은 소스 변경을 머리에 경고 — `code_identity`의 `dirty`), `api.fault_report` | `test_the_safety_case_report_is_one_self_contained_page`, `test_the_report_says_when_the_source_had_uncommitted_changes`, `test_the_api_serves_the_editor_schema_review_matrix_and_report`, `test_the_safety_case_tab_reviews_verifies_and_saves_the_report` | implemented |
| 화면·해석 | 편집 탭, 안전 근거 탭, 두 언어 | `desktop/pages/fault_sim.py`(세 탭), `insight/fault.py` `safety_case_insight`(모순·누락 → 경고 → 검출 지연 → 요구별 실패·미발동 → 최악 시간 → 미검출 고장), self-test 화면 25j–25m | `test_every_page_reading_is_made_and_readable_in_both_languages`, self-test | implemented |
| 한계 | — | 센서·자원·배터리·역할은 편집기 밖(프로젝트 파일), 커버리지 주장은 선언(계산 아님), 매트릭스는 카탈로그만 | `docs/LOGIC_REVIEW.html` §22.9 | remaining |

## 20. 기능안전 참고 문서 검증 (FuSa 참고 문서·계층·제안·파형 판독·데이터 커서)

요구(사용자): 기능안전 참고 문서의 항목을 최소한 모두 앱에서 검증 가능하게(PASS/FAIL/UNKNOWN/CONFLICT/MANUAL + 근거),
OPEN 값은 추정하지 않고, DERIVED/RESEARCH는 원문 요구로 내보내지 않으며, 충돌 기록은 변형으로; SG·FSR·TSR이 섞인 문서를
분류해 반영하고 추론해 보완·추가할 것; 고장 파형의 과도 스파이크 크기·시각을 읽을 수 있을 것; MATLAB/Simulink식 데이터 커서;
제어까지 고장 났을 때 HW가 DC 전압으로 FW/ASC를 고르는 로직과 주행 중 순환의 확인; 스크린샷은 영어로. 상세 설명은
`docs/LOGIC_REVIEW.html` §23, 회귀 시험은 `tests/test_reference.py`·`tests/test_reference_page.py`·`tests/test_transient.py`·
`tests/test_cursor.py`·`tests/test_fault_page.py`에 있습니다.

| 항목 | 요구 | 구현 | 확인 | 상태 |
|---|---|---|---|---|
| 참고 패키지·판정 | 항목별 판정과 근거, OPEN 비추정, 충돌은 변형 | `extensions/faultsim/refpkg.py`(`validate_package`, `ReferenceRunner`: 원문 값/예시 프로파일, USER 값, 실행 캐시, 검사 예외는 그 검사만 UNKNOWN), `refcheck.py`(검사 라이브러리), `safestate.py`(C1–C4, 한 시계의 여섯 시각) | `test_open_value_is_unknown_in_customer_and_flagged_in_illustrative`, `test_user_value_replaces_open`, `test_gde_sequences_kept_as_variants`, `test_a_broken_check_never_stops_the_others`, `test_every_check_is_used_by_the_example` | implemented |
| 요구 계층·추적 | SG → TLSR → FSR → TSR → SM → 검증, 문서 근거와 추론 분리 | `extensions/faultsim/refhier.py`(`level_of`, `hierarchy`: 롤업·공백·통계), `level`/`traces_to`/`traces_to_inferred`/`proposed` 검증, HTML·CSV에 계층 | `test_example_hierarchy_levels_traces_rollup_and_gaps`, `test_a_proposal_is_derived_and_never_a_source_requirement`, `test_hierarchy_in_the_html_and_the_csv` | implemented |
| 참고 문서 패키지 (내장) | 문서 전 항목, 분류·추적, 원문 없는 곳 표시 | `examples/reference_packages/customer_inverter/`(`extract.py` → `doc_extract.json` → `build.py`), `extensions/faultsim/packages/customer_inverter_reference.json`(371 항목: 분류·문서 인용 추적·추론 추적·OPEN 자리표시·제안 8건), `api.reference_builtin*` | `test_the_builtin_reference_is_shipped_classified_and_traced`, `test_builtin_packages_and_the_hierarchy`, self-test `reference:builtin_hierarchy` | implemented (검증 대상은 앱의 제품 모델 — 기본 합성) |
| 추론한 추가 요구 (제안) | 시뮬레이션 근거로 보완·추가 | 예제 EX-PROP-01, 내장 패키지 PROP-SG-HV·PROP-01…07(HW Vdc 선택 순환 금지, GDE 시퀀스 ASC 종료, DCFOC 임계, 클럭 감시, 독립 과속, KL15 에지 재허가, 관측 기반 확인) | `test_the_cycling_proposal_is_demonstrated_and_its_fix_holds`, `test_a_proposal_runs_with_evidence_and_the_reports`, self-test `reference:proposal` | implemented (DERIVED: 원문 요구 아님) |
| 용어 | 앱이 쓰는 말은 '원문 요구'(문서가 준 요구) — 프로파일 '원문 값'·'예시 값', 근거 태그 PAST-PROJECT, CSV 열 `source` (패키지 필드 `customer_tags`·프로파일 키 `customer`는 호환용으로 유지, `source`도 받음) | `refhier.py`(`DEFAULT_SOURCE_TAGS`, `SET_NAMES`, `set_name`), `refreport.py`, `desktop/pages/reference.py`, 빌더(`TAG`, `GROUP_SHOWN`) | `test_the_application_speaks_of_source_requirements`, `test_builtin_packages_and_the_hierarchy`(페이지 라벨) | implemented (문서에서 그대로 옮긴 요구 원문은 바꾸지 않음) |
| 제어 상실·주행 중 HW Vdc 선택 | FW ↔ ASC 순환 확인 | `study.py` 프리셋 `hw_vdc_rolling`(GDE 감시·`HW_VDC_SELECT`·`HWGD` 설계 변형), `refexample.py` S-HWVDC(순환/ASC 유지), `insight/fault.py` `bridge_cycling`(해석 헤드라인·절), `event` 검사의 반복 횟수 | `test_the_cycling_proposal_is_demonstrated_and_its_fix_holds`, self-test `fault:hw_vdc_cycling` | implemented |
| 과도 판독 | 스파이크 크기·시각, 확대 | `extensions/faultsim/transient.py`(`transient_metrics`: 극값=샘플·해상도·한계 밖 시간·정착, `transient_window`), `plots/fault_figures.py`(`fig_fault_transient`, 극값 주석, 먼 한계 축 밖 표기, 확대 구간 음영), 고장 페이지 "과도 확대" 탭(그림 + 지표 표), 참고 페이지 증거 그림의 극값 | `test_transient.py` 7건, `test_a_representative_scenario_runs_and_reads_on_the_page`, self-test `fault:transient` | implemented |
| 데이터 커서 | 범례에서 신호 선택, 곡선 추적, 값 읽기 | `desktop/cursor.py` `DataCursor`(샘플 맞춤·계단선·평면 곡선, 쌍축 범례 적중 판정, 시간 커서, 고정 커서 2개 Δx·Δy·기울기, 방향키, 블리팅), `desktop/widgets.py` `PlotPanel`(토글·신호 목록·판독 3줄; 지도 패널은 꺼진 채 시작) | `test_cursor.py` 7건, self-test `plot:data_cursor` | implemented |
| 영어 스크린샷 | 해외 독자 | `docs/make_fusa_showcase.py --lang en`(기본), 새 캡처(과도 확대·데이터 커서·HW Vdc 순환·계층·제안 증거) | 생성 로그 | implemented |
| 한계 | — | 추론 링크는 검토용, OPEN 자리표시는 번호만, 과도 지표는 트레이스 해상도에 묶임, HW 순환 주기는 합성 R·C·역기전력 | `docs/LOGIC_REVIEW.html` §23.8 | remaining |

## 21. 비목표 (handoff §15, 추가 명세 비목표)

generic motor CAD/FEA 복제, 정적 ASC로 demag/SOA 승인, 일반 IGBT 식으로 SiC 수명 보증, 드라이버 typical delay로 ASIL 승인,
class 번호로 EMC 합격률, 평균 dq로 NVH/베어링/MHz 임피던스, 생산 anti-jerk 제어기 자동 납품, 보편 안정성 인증서,
IGBT+SiC 혼합 소자 토폴로지, 인증·공급사 승인 자동 생성.
