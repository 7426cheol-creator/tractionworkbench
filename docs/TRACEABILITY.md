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
| 사용자 기능 4 | 입력 완성도별 진입점: 고객 사양만으로 필요조건, 서로 다른 운전점의 최대 토크×최대 속도 곱 금지, 포트·기계/전기 속도·지속시간 확인 | `spec_check`: 같은 운전점의 P_shaft = T·ω ≥ 0(구동)이면 P_dc ≥ P_shaft → 방전 전력/전류(범위면 최저 Vdc) 한계 초과는 어떤 드라이브로도 불가능; 회생은 손실이 흡수할 수 있어 '모델 필요'; 모델과 같은 허용오차. 모델이 UNKNOWN인 요구를 이 조건이 결정(FAIL, decided_by), 모델 PASS와 모순이면 표시하지 않고 오류 | `test_the_customer_numbers_alone_…`, `test_the_model_never_contradicts_…` | implemented |
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

## 17. 비목표 (handoff §15, 추가 명세 비목표)

generic motor CAD/FEA 복제, 정적 ASC로 demag/SOA 승인, 일반 IGBT 식으로 SiC 수명 보증, 드라이버 typical delay로 ASIL 승인,
class 번호로 EMC 합격률, 평균 dq로 NVH/베어링/MHz 임피던스, 생산 anti-jerk 제어기 자동 납품, 보편 안정성 인증서,
IGBT+SiC 혼합 소자 토폴로지, 인증·공급사 승인 자동 생성.
