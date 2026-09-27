# Traction Workbench v0.3.0 — Release / Handback Notes

기준선: `reference/traction_workbench_spec_v1` (Blueprint, Implementation Handoff, Reference Cases, golden JSON; manifest SHA-256 일치 확인).
이 문서는 Handoff H12가 요구한 실행 방법, model contract, 제약 목록, 알려진 한계, 검증 실행 결과, 실패/미구현 항목, data provenance, 재현 조건을 담습니다.

## 0.3.0 변경 사항 (v0.2.0 대비)

독립 엔지니어링 리뷰(handoff), 감사 증거 패키지(dc7b338)의 재현 스크립트, 세 추가 명세(OEW/HEV, 파워모듈별 손실·단계별 효율,
가변 PWM·anti-jerk)를 반영했습니다. 항목별 구현 위치·확인 테스트·상태(implemented / partial / missing / evidence_missing)는
[`TRACEABILITY.md`](TRACEABILITY.md)에 있습니다. 모든 수치 fixture는 구현 검증(V0–V3)이며 하드웨어 정확도(V5–V6)가 아닙니다.

**정확성 (리뷰 P0-A, F01–F13, 감사 재현)**
- **공통 witness gate** (`solvers/gate.check_witness`): 정책·capability·지정 정책·sizing·불확실성 calibration·탐색 페이지의 모든 witness를
  원 요청으로 다시 평가합니다(모델 유효, 커버리지 내부, 모든 제약 평가·충족, 관련 DC 한계 선언). 통과하지 못한 수치는 진단값입니다.
- **결측 ≠ 무제한**: 선언되지 않은 DC 한계는 UNKNOWN, 무제한은 `math.inf`로 명시 선언. NOT_EVALUATED는 통과가 아니고, 수치 residual은
  토크 정확도가 아닙니다. 커버리지 인지 인증(control domain ≠ data domain), 유한/연속 정격의 typed semantics(순서 불변), 대역 요구는
  같은 witness로 판정합니다.
- **FTTI**: 고장에서 **물리적** 안전 종점까지의 모든 연속 예산 경로를 열거(보장 상한 = 경로 최소). 독립 최댓값의 합이 FTTI를 넘으면
  UNKNOWN(BOUND_INCONCLUSIVE)이고, 최댓값이 한 트레이스에서 함께 일어난다고 선언할 때만 INFEASIBLE. 명령 발행으로 끝나는 체인은 UNKNOWN,
  분할할 수 없는 composite 예산이 있으면 FDTI/FRTI 예산 판정은 UNKNOWN.
- **열 증거**: “검증됨”에는 검증 근거와 유효 영역이 필요하고, 필수 노드·초기 열 상태(미선언·고온 시작 → UNKNOWN)를 확인합니다.
  비단조 feasible set은 보존합니다.
- **정류 위험**: 회전 중 무부하 역기전력은 방전 하한이 아니라 다이오드 정류 위험입니다(RC 시간은 하한, 결합 모델 필요). 상수 파라미터
  모델이면 다이오드 브리지 등가(R_eq = π²/18·R)로 링크 유지 전압을 **스크리닝 추정**(한계 아님)합니다.
- Kt 단위의 명시 변환·거부, 정적 reciprocity ≠ 보간자의 동적 보수성, sizing의 UNKNOWN 구간을 최소 sizing으로 보고하지 않음, 미해결 이득을
  “not limiting”으로 표시하지 않음, **수학 · 모델 · 요구 · qualification claim 층** 분리.
- 감사 재현 폐쇄: 조건부 빈 곡선 → UNKNOWN, 고정 calibration 행의 witness gate, provenance 기반 정격 승인, FDTI/FRTI 분할 미상 → UNKNOWN,
  보간자 내부 비대칭 검출.

**데이터 계약 (P0-B)**: flux map 좌표계를 import 시 고정, q-홀수 이음매 검사, machine-data audit(정적·동적·손실·열·감자·고장 용도별
사용 가능 여부와 qualification 공백 — 이웃 용도로 승격하지 않음).

**P1 확장**
- 데이터시트 **모듈 손실**(§8.8): 소자별 도통·스위칭(온도×전류 표, 외삽 금지, typical ≠ 상한), 변조별 듀티, 데드타임, 병렬 → P_dc·DC claim·
  열에 결합. 2차 surrogate와 배타적이며 I² certificate를 적용하지 않습니다.
- **DC-link 리플**(§8.9): 커패시터 RMS 전류, ESR(f) 손실, 수명 게이트. **열 사이클 수명**(§12): rainflow, 조건부 손상.
- **보호**(§9): 임계값·디레이팅·고장 반응을 하나의 인과 궤적에서 검증. **ASC 과도**(§9.13): 고객의 두 전류-시간 요구.
- **전도 EMI**(§11, P1-C): 요구 프로파일 완결성, source → path → receiver, RBW 선 합 추정, 대역별 필요 감쇠, 측정 trace 판정
  (PASS/FAIL/INDETERMINATE). 스크리닝은 PASS가 아닙니다.
- **모터 설계**(§10): 검증된 기준 모델 주변의 일관 스케일링(ψ = k_N·k_L·k_PM·ψ, L·R은 k_N²와 단부 비율, 전류축 ×1/k_N)과 계보·무효화 데이터,
  같은 결합 요구 여유로 후보 비교, 권선 star of slots(정수 산술 벨트, 3상 MMF 권선계수, 병렬 회로), 개념 사이징(T = 2σV_r).

**추가 명세**
- **OEW 듀얼 인버터 / HEV**: 64 상태쌍·보장 반경, dq0 전력, 공통 bus vs 절연 전원, 포트 회계, i0 리플, 쌍 안전 상태, 두 브리지 CM 교차
  스펙트럼; 결합 토크 집합(가지 vs 순전력), 크랭킹 replay, 유성기어 검사, 부하 차단 에너지 원장.
- **효율·모듈 비교**: 다섯 제어 체적(인버터 · 모터 · 인버터+모터 · 감속기 · eDrive)의 포트 기준 η — 구동 η = 출력/입력, 회생 η = |입력|/|출력|,
  혼합 흐름 N/A, 미상 손실 UNKNOWN, η > 1은 clamp 없이 INCONSISTENT, 망원 항등식은 같은 점·방향에서만. 방향별 감속기 모델(없으면 eDrive η
  UNKNOWN), 보조 전력은 공급 포트에서 한 번, 미션 E±(정확한 구간 선형 분할), 모듈 A/B(고정 정책 vs 설계별 정책, Tj는 드라이브를 거친 결합
  고정점, 선언된 오차 예산을 넘을 때만 우열). 기존 표시 결함 **F-E01**(미상 손실을 0으로 합산한 총 손실), **F-E02**(개별 효율을 전체 energy
  mode에 종속), **F-E03**(회생 지도에 정방향 수식 라벨) 수정.
- **가변 PWM**: 캐리어·이벤트율·샘플/갱신율·전기 주파수·펄스 비 분리, 인과적 fsw 스케줄(첫 일치 규칙, 히스테리시스, dwell, 보호 선점,
  fallback), 지연 원장(필터 + 갱신 + 변조기 비율, deadline 미스는 위반), PI 이득 매핑(연속 vs 고정 이산)과 전류 루프 위상 여유, RL 리플(엣지 사이
  정확 적분 = 엣지 합 스펙트럼), 최소 펄스, 카운터 수준 up-down 타이머(shadow vs 즉시 기록)와 gate event 검사, 고조파 동손 3ΣI²R_ac와
  철손 상한의 구간 비교, Pareto(“평가한 후보 중 최선”).
- **Anti-jerk·능동 감쇠**: 기어비로 환산한 2관성 ROM(이벤트 사이 행렬지수 정확 적분), 성형(rate·prefilter·ZV)과 피드백(상대속도·HPF),
  ZOH + 분수 지연(modified z-transform), 연속 지연 교차(Newton 연속), 중재 후 클리핑, 긴급 감소는 comfort 필터 우회, 백래시 통과 → UNKNOWN.
- **교환 패키지** (`twb exchange`, 검증 페이지): MathWorks 이식·도구 간 parity용 규약(포트 부호, 효율 경계, 손실 소유권, PWM, 드라이브라인 좌표),
  모델 식별자, 예시 입력, 이 구현이 계산한 fixture(E-01..E-06, D-01..D-05, PWM 지연·리플). 구현 검증용이며 물리 검증이 아닙니다.

**데스크톱**
- 새 페이지 7개: 보호·고장, 전력변환·수명, 효율·모듈 비교, 가변 PWM·Anti-jerk, OEW·HEV, EMI(전도성), 모터 설계.
- P0 결과의 화면 노출: 요구 판정의 **claim 층 표**와 요구 witness, 운전점 탐색의 **ACCEPTED / DIAGNOSTIC ONLY / UNKNOWN**과 gate 사유,
  모델·데이터의 DC 한계 **값 / 미선언 / 선언된 무제한** 선택과 **data audit 표**, 열 편집기의 **검증 근거**와 열 페이지의 **초기 열 상태**,
  안전 스크리닝의 **FTTI 종점 입력**(안전 종점 이벤트, 종점 종류, 최댓값 동시 발생)과 보장 상한 경로·FDTI/FRTI 최악값, 방전·과전압 **결과 표**
  (정류 위험과 스크리닝 추정).
- self-test에 효율·PWM·anti-jerk·교환 패키지 검사 추가, desktop smoke가 새 입력 경로를 확인합니다.

## 0.2.0 변경 사항 (v0.1.0 대비)

- **독립 실행형 데스크톱 앱**으로 배포 형태 변경: PySide6(Qt, LGPL) + matplotlib, PyInstaller one-folder 번들.
  `TractionWorkbench.exe`(창 앱)와 `twb.exe`(콘솔 CLI)가 같은 폴더에 들어 있으며 Python 설치·서버·네트워크가 필요 없습니다.
  웹 UI(`twb serve`, `export-static`)는 제거했습니다.
- **전문가용 그래프**(모두 production 모델 값의 재표현, `viz/` + `plots/`): 상전류·상/선간 전압·SVPWM 듀티(평균값 모델)·쇄교자속 파형,
  IPMSM dq 벡터도(e₀, ωL_d·i_d, −ωL_q·i_q, R_s·i)와 공간벡터 육각형, 전력 흐름(DC→축)과 제약 사용률, id–iq 제약 지도(전압 타원·전류원·
  도메인·DC 한계·등토크선·MTPA·MTPV 참고선·정책점, 마우스 판독, 클릭 정방향 평가), 토크/속도 스윕 궤적, 활성 제약별로 색을 나눈 T–n 곡선,
  효율·손실·전류·변조율·역률 맵과 기저속도 곡선, capability vs 파라미터(역설계), 병목·완화, FTTI Gantt, 방전/과전압 V(t),
  ASC·역기전력 vs 속도, 열 가용 토크 vs 지속시간과 노드 온도 궤적. 모든 그림은 PNG/SVG/PDF, 데이터는 CSV로 내보냅니다.
- **PDF 엔지니어링 보고서** (`twb report CASE.json --pdf`, 앱의 "PDF 보고서"): 판정 요약, 조건별 claim, 제한 요인·다음 조치, 그래프, 의사결정 기록 원문.
- **자체 검사**: `twb selftest DIR` / `TractionWorkbench.exe --self-test DIR` — 모든 페이지를 실제 코드 경로로 실행하고 예시 판정, golden acceptance,
  PDF 보고서, flux-map 드라이브, 다크 테마를 확인(18 checks). 빌드 스크립트가 **패키징된 실행 파일에서** 이 검사를 수행합니다.
- CI(GitHub Actions): Linux 테스트 + Windows 실행 파일 빌드·동결 상태 self-test·zip 아티팩트, `v*` 태그 시 릴리스 첨부.
- 참조 패키지 위치 탐색: `TWB_SPEC_DIR` → 번들 내부(`sys._MEIPASS`) → 소스 트리. `reference/**`는 `.gitattributes`로 줄바꿈 변환을
  금지(Windows `core.autocrlf`가 CRLF로 바꾸면 manifest SHA-256이 모두 불일치했음; CI에서 발견·수정).
- **냉각수 모델** (`extensions/coolant.py`): 유량 Q, 에틸렌글리콜:물 농도(기본 50:50 부피)에서 c_p·ρ 일반값 보간(근사, 공급사 값으로 덮어쓰기 가능),
  ṁ = ρQ, ṁ·c_p [W/K], 순환 순서대로 부품 열 P_k를 받아 ΔT_k = P_k/(ṁ·c_p). 노드는 자기 위치(인버터 냉각판/모터 워터재킷)의
  입구·평균·출구 온도를 기준으로 합니다. 냉각수 루프가 없으면 기존처럼 입구 온도 기준(무한 유량 가정, 명시).
- **열 회로망**: Foster(R_i, τ_i)와 Cauer(R_i, C_i) 입력. Cauer는 일반화 고유값 문제 G v = λ C v로 **정확히** Foster로 변환(ΣR 보존,
  사다리 ODE 적분과 1e-6 이내 일치 테스트). 단별 유량 의존 R_i(Q) = R_i,ref·(Q_ref/Q)^n (기본 n = 0.8, 근사로 표기).
  검증된 열모델의 유효 조건에 유량(`coolant_flow_L_per_min`)·농도도 넣을 수 있습니다.
- **열 모델 편집기**(JSON 직접 편집 대체): 노드 탭, 단 표, 4단 템플릿, 데이터시트 붙여넣기(“R τ” 줄 또는 R 행/τ 행), 유효 범위,
  JSON 저장/불러오기. RC 회로도·냉각수 순환도·Z_th(t)(log-log) 즉시 갱신.
- **회로 개요도** (`plots/schematics.py`): 배터리–메인 릴레이(±, 프리차지)–DC 링크–능동 방전–3상 브리지(스위치+역병렬 다이오드)–모터–축.
  시나리오별 상태(릴레이 개방, 방전 스위치, 다이오드 정류, ASC 하단 ON)와 전력 흐름 화살표. 운전점 “시스템 개요” 탭, DC 링크(방전·
  배터리 차단), ASC/Freewheel 비교, PDF 보고서에 사용. 프로젝트 안전 규칙은 표로 입력.
- **패시브 방전** (`extensions/dclink.passive_discharge`): 스위치 없이 상시 연결된 블리더 R_p. V(t) = V₀e^(−t/(R_p C)),
  t = R_p C ln(V₀/V_f), 상시 손실 P = V²/R_p(릴레이 닫힘 동안), 불변량 P·t = C V² ln(V₀/V_f)(R_p와 무관: 빠른 방전 ↔ 상시 손실),
  설계 창 V_max²/P_허용 ≤ R_p ≤ t_req/(C ln(V₀/V_f)), 능동 저항과 병렬(R_a‖R_p), 회전 중 역기전력 하한. 회로도(R_p 상시 연결)와
  V(t)·설계 창 그래프.
- 모든 페이지에 접이식 **개념 설명**(그래프 읽는 법·핵심 식).
- 예시 열 모델을 4단 Foster(인버터 접합, 마지막 단 유량 의존) + 3단 Cauer(권선)와 냉각수 루프(10 L/min, EG 50%)로 교체:
  65 °C·3000 rpm에서 450 N·m 유지 4.24 s(냉각수 상승 반영 전 5.47 s), 연속 426 N·m.

---

## 1. 범위

| 구분 | 내용 |
|---|---|
| 대상 | 균형 3상, 영순분 없음, 단일 2-level VSI, wye 또는 문서화된 wye-equivalent, 정상상태 기본파, linear SVPWM, PMSM/SPMSM/IPMSM |
| 모델 | (a) 상수 파라미터 dq (D1), (b) 유효 마스크가 있는 비선형 dq flux map (D2) — engineering MVP의 두 모델 모두 구현 |
| 경계 | 속도·Vdc·온도는 scenario가 주는 경계 조건. 열 동특성·배터리 전기화학 없음 |
| 필수 query (H2) | forward, 요구 축토크 해, ±capability(정책/물리/전기), 지정 정책 평가, 조건 비교, 1-파라미터 역설계, 구간 입력 분석, 추적 가능한 의사결정 기록, 외부 rating envelope 조회 |
| 추가(요청) | FTTI·DC-link·ASC/6SO·열→토크 스크리닝 확장 (`extensions/`, 7절 참조); 리뷰 P1(모듈 손실, DC-link 리플, 열 사이클 수명, 보호, ASC 과도, 전도 EMI), 모터 설계(§10), 추가 명세(OEW 듀얼 인버터·HEV, 경계별 효율·모듈 A/B, 가변 PWM, anti-jerk) — 단일 VSI solver는 OEW 토폴로지를 재해석하지 않습니다 |

## 2. 실행 방법

**배포본(Windows)**: `TractionWorkbench-<ver>-windows-x64.zip`을 풀고 `TractionWorkbench\TractionWorkbench.exe` 실행.
설치·관리자 권한·Python 불필요. 코드 서명이 없으므로 처음 실행 시 SmartScreen 경고가 뜰 수 있습니다(추가 정보 → 실행).
같은 폴더의 `twb.exe`는 콘솔 CLI입니다(아래 명령과 동일).

```bash
# 개발 환경
pip install -e '.[gui,test]'             # Python ≥ 3.10: numpy, scipy (+ PySide6-Essentials, matplotlib, pytest)
twb gui                                  # 데스크톱 앱 (= traction-workbench, TractionWorkbench.exe)
twb demo                                 # 대표 질문 8개
twb evaluate <case.json> --out out/      # 의사결정 기록(JSON + Markdown). --exit-code: PASS 0 / FAIL 2 / UNKNOWN 3
twb report <case.json> --pdf out.pdf     # 그래프 포함 PDF 보고서 (GUI 불필요)
twb solve | forward | capability | curve # 단일 계산
twb acceptance                           # production vs golden
twb selftest out/selftest                # 데스크톱 앱 headless 자체 검사
python verification/independent_fixture_check.py
python verification/make_report.py       # docs/VERIFICATION_REPORT.md 재생성
python -m pytest -q

# 실행 파일 빌드 (Windows에서: packaging\build_windows.bat)
pip install -e '.[gui,build]'
python packaging/build.py                # dist/TractionWorkbench/ + zip, 동결 앱에서 acceptance·self-test 수행
```

입력 파일 형식: `examples/cases/*.json`, `examples/drives/*.json`. 모든 물리량은 `{"value", "unit"}`와 정의(basis/reference/kind)를 명시해야 하며, 모호하면 계산 전에 거절됩니다(INVALID_INPUT, CLI exit 4).

## 3. Model contract

### 3.1 Convention

- 내부 SI. p = 극쌍 수, ω_m = 2πn/60, ω_e = pω_m (출력에서 기계/전기 속도 구분).
- d축 = PM flux 방향, amplitude-invariant Park, dq 전류·전압 = 기본파 상 peak.
- I_pk = √(i_d² + i_q²), I_phase,rms = I_pk/√2 (정지 시 “equivalent sinusoidal RMS”로 표기, 개별 상 RMS·정지 열부하 추론 없음).
- V_phase,pk = √(v_d² + v_q²), V_LL,rms = √(3/2)·V_phase,pk.
- P_dc > 0: DC 전원 → 인버터, P_ac > 0: 인버터 → 모터, P_shaft > 0: 모터 → 부하. 역회전 motoring(ω<0, T<0)은 P_shaft > 0.

### 3.2 전기·토크 식

```
v_d = R_s i_d − ω_e ψ_q,   v_q = R_s i_q + ω_e ψ_d
T_em = 1.5 p (ψ_d i_q − ψ_q i_d)
상수 모델: ψ_d = ψ_PM + L_d i_d,  ψ_q = L_q i_q        (L_d = L_q인 SPMSM 포함, IPMSM에 L_q > L_d 강제 없음)
flux map: (ψ_d, ψ_q) = Ψ(i_d, i_q; T_PM plane), 유효 셀 내부 쌍선형 보간, 외삽 없음
```

R_s는 per-phase이며 전압식에 포함(전압 한계에서 다시 빼지 않음). 선간 측정값은 wye에서만 /2 변환을 기록하고, raw delta는 거절합니다. Ke/Kt는 convention이 완전할 때만 ψ_PM으로 변환합니다.

### 3.3 손실 closure

```
T_shaft = T_em − τ_rot(ω_m),   τ_rot = bω_m + cω_m|ω_m|  (회전 반대 방향, τ_rot(0) = 0 명시)
P_rot = ω_m τ_rot ≥ 0,   P_cu = 1.5 R_s I_pk²
P_ac = 1.5(v_d i_d + v_q i_q) = T_em ω_m + P_cu = P_shaft + P_cu + P_rot
P_dc = P_ac + P_inv,   P_inv = a0 + a2 I_pk² ≥ 0   (구동/회생 대칭은 명시 선언 필요)
                     또는 P_inv = 데이터시트 모듈 모델(소자별 도통 + 스위칭, V_dc·f_sw·T_j·변조 의존)  — 둘 중 하나만, 합산 금지
```

모듈 모델을 쓰면 2차 surrogate의 I² certificate(오목 상한 등)는 적용되지 않으며 DC claim은 직접 witness에 근거합니다. 데이터 범위(V/I/T)
밖은 외삽하지 않고 UNKNOWN입니다. 효율은 선언된 포트 사이의 제어 체적마다 따로 정의합니다(0.3.0 변경 사항, `analysis/efficiency.py`).

철손을 τ_rot에 포함하면 “loss-equivalent resisting-torque approximation”으로 기록합니다. 손실 모델이 없으면 축/DC claim을 전기적 claim으로부터 승격하지 않습니다(UNKNOWN, MISSING_INPUT; 인버터 손실이 없을 때는 P_dc ≥ P_ac 필요조건만 사용).

### 3.4 인버터 전압 모델

`|v_motor + Δv_inv| ≤ (1 − r_v) V_dc/√3`. r_v는 설계/제어 예약분(수치 허용오차 아님). Δv_inv = 0이면 “ideal mapping(optimistic screening)”, 또는 R_drop·i_dq 저항 강하 모델. 출력은 하드웨어 한계 V_dc/√3, 예약분, 명령 예산, 수요, 잔여 명령 여유를 따로 보고합니다. overmodulation/6-step은 거절(범위 밖).

### 3.5 DC 경계

`I_dc,avg = P_dc/V_dc` (V_dc > 0 필수). 방전 `P_dc ≤ P_dis`, `I_dc ≤ I_dis`, 충전 `P_dc ≥ −P_chg`, `I_dc ≥ −I_chg`를 동시에 적용(유효 한계 = min(P, V_dc·I)). 평균값이며 capacitor ripple RMS·과도 peak가 아닙니다.

### 3.6 에너지 모드·효율

| 조건 | 모드 | 효율 |
|---|---|---|
| P_shaft > 0, P_dc > 0 | MOTORING | P_shaft/P_dc |
| P_shaft < 0, P_dc < 0 | REGENERATING | \|P_dc\|/\|P_shaft\| |
| P_shaft < 0, P_dc ≥ 0 | BRAKING_WITHOUT_NET_DC_RECOVERY | N/A |
| \|ω_m\| ≤ 1e-12 rad/s | STANDSTILL | N/A (손실은 보고) |
| \|P_shaft\| ≤ 1e-6 W | ZERO_SHAFT_POWER | N/A |

0 ≤ η ≤ 1을 벗어나면 clamp하지 않고 ACCOUNTING_INCONSISTENCY로 보고합니다.

### 3.7 온도

Scenario가 권선/자석 온도를 명시하면 모델의 기준 온도와 일치해야 합니다. 불일치 시 근거·유효 범위가 선언된 선형 계수가 있을 때만 R_s/ψ_PM을 조정하고, 그렇지 않으면 claim은 UNKNOWN(OUTSIDE_MODEL_DOMAIN 또는 MISSING_INPUT, “assumed-data conditional”)입니다. 권선 온도를 자석 온도로 치환하지 않습니다. Flux map의 온도 plane 사이 보간은 `temperature_interpolation="linear"`와 근거 문구가 있을 때만 허용합니다.

### 3.8 Flux map 데이터 계약

축(id, iq; 엄격 증가, 중복 금지), row = id / column = iq(명시 선언, 전치 배열 거절), ψ_d·ψ_q 배열, 노드 유효 마스크, 선택적 자석 온도. 셀은 네 모서리가 모두 유효할 때만 사용. 선언된 대칭(`q_odd`)만 사용. 보존적 맵으로 선언되면 내부 노드 중앙차분으로 상호성(∂ψ_d/∂i_q = ∂ψ_q/∂i_d, 상대 허용치 1e-3 기본)과 differential inductance 양정치를 점검합니다(이 점검이 포화 모델 검증은 아님). 맵 데이터 SHA-256이 의사결정 기록 스냅샷에 들어갑니다.

## 4. 제약 목록

| 이름 | group | 방향 | limit | kind |
|---|---|---|---|---|
| VOLTAGE | VOLTAGE | upper | (1−r_v)V_dc/√3 | hard_limit |
| CURRENT | CURRENT | upper | I_pk,max (기본파 상 peak) | hard_limit |
| ID_MIN / ID_MAX | DOMAIN | lower / upper | 선언 id 범위 | allowed_domain 또는 model_validity |
| IQ_MIN / IQ_MAX | DOMAIN | lower / upper | 선언 iq 범위 | 〃 |
| SPEED_MIN / SPEED_MAX | DOMAIN | lower / upper | 선언 속도 범위 | 〃 |
| DC_DISCHARGE_POWER / _CURRENT | DISCHARGE_SOURCE | upper | P_dis / I_dis | hard_limit |
| DC_CHARGE_POWER / _CURRENT | CHARGE_SOURCE | lower | −P_chg / −I_chg | hard_limit |

각 제약은 limit, demand, slack(upper: limit − demand, lower: demand − limit), 단위, tolerance, 상태, 출처를 보고합니다. 상태: `slack < −tol` → VIOLATED, `|slack| ≤ tol` → ACTIVE(허용오차 내 경계, boundary qualifier), 그 외 SATISFIED. `tol = max(절대 하한, 1e-9·|limit|)` (전압 1e-9 V, 전류 1e-9 A, 전력 1e-6 W). 표시 반올림으로 판정하지 않습니다.

속도가 선언 범위 밖이면: `allowed_operating_limit`이면 “선언된 허용 운전영역 내” INFEASIBLE(회전자 강도/감자 인증 아님), `model_validity`이면 UNKNOWN.

## 5. Query 의미론과 수치 방법

| Query | 방법 | 증거 종류 |
|---|---|---|
| Forward (H5.1) | 주어진 id/iq를 그대로 평가, 이동·clip 없음. 위반점은 진단값이며 feasible witness가 아님. 맵 밖은 UNKNOWN | direct_evaluation |
| 최소전류 정책 (H5.3), 상수 모델 | i_q = a/k(i_d) 소거 후 전류·전압·iq 범위·I² 정류점을 다항식 부등식으로 만들고 모든 실근 + 구간 중점을 검사 → 가능 집합 정확 결정. a = 0(영 전자기 토크)과 k = 0(토크 0 수직선)은 원래 식으로 처리 | exact_boundary_enumeration |
| 최소전류 정책, flux map | 행(id)별 T_em 부호 변화로 곡선 추적(맵 노드 포함 격자) → 국소 재풀이·경계 bisection·유계 최소화. 셀 B&B로 I² 하한을 구해 인증(기본 gap 1e-3·I), 커버리지 반경 ≥ I*이면 맵 밖 저전류 해 배제 | sampled + bounded_search |
| DC 판정 | 정책점에서 DC 제약 평가. 맵에서 정책점이 미인증이면 I² 하한으로 방전/충전 쪽을 각각 보수적으로 판단 | direct_evaluation |
| 물리적 존재(DC 포함, 진단) | 토크 곡선 위에서 P_dc는 I²에 단조 → DC 한계 = I² band; 가능 구간의 I² 범위와 교집합. 정책점이 아닌 witness(손실 증가)는 active-loss candidate로 표시만 하고 채택하지 않음 | exact / witness / bounded_search |
| 필요조건 screen | 축 출력 > 유효 방전 한계, 최대 손실로도 충전 한계 미달, 전류 한계 MTPA 토크 < 요구, d축 전압 하한 > 예산 | analytic_necessary_condition |
| 물리/전기 capability | 상수 모델: id별 정확 iq 구간(2차식)에서 최대 토크 → 격자 + 유계 정밀화; Lagrangian 오목 상한(KKT 승수 + 도함수 없는 개선, 상대 오목 여유 요구)으로 반대쪽 상한 인증. 맵: 격자 + 경계 bisection, 셀 B&B 상한 | numerical_witness + certified_bound |
| 정책 capability | 전기적 극값까지 토크 스캔(기본 161점) + 전이 bisection. 불연속 가능 집합은 구간 목록으로 보고. 구동은 물리 capability 인증 상한으로 인증 가능, 회생 정책 경계는 표본 증거 | sampled (+certified) |
| 1-파라미터 역설계 (H5.5) | 선언 범위 내 표본 + 모든 상태 전이 bisection, 외삽 없음, 비단조면 여러 구간 보고. 변경 종류(boundary/hardware/design/diagnostic/data) 표기 | sampled |
| Dominance (H7) | 각 제약 +1% 완화 → 정책 capability 전체 재계산 → 이득. 단독 무효·쌍 유효면 joint bottleneck. 요구 기준 최소 완화도 제공 | 재계산 |
| 구간 입력 (H6) | 모서리(+중심) 조합, adaptive(정책 재풀이) vs fixed calibration(명목 id/iq 고정) 구분. 모두 통과해도 UNKNOWN(SAMPLED_COVERAGE); admissible 집합의 위반은 반례, outer enclosure의 끝점 위반은 반례 아님 | sampled |
| 손실 구간 (S00) | P_dc = P_shaft + [L_min, L_max]; 방전은 최대 손실, 충전은 최소 손실로 검사. 실제/robust/외포락 세 판단 분리 | analytic |
| 지정 정책 (H5.4) | 전류 LUT(토크×속도, 마스크, Vdc 유효 범위, shaft/EM 토크 기준) → forward. hole·범위 밖 → UNKNOWN(POLICY_LIMITATION). 최소전류 결과는 policy gap 비교로만 제시, 대체하지 않음 | direct_evaluation |
| 지속시간 (UC08) | 지속시간·조건(냉각수, 초기 상태, Vdc, fsw 등)이 모두 일치하는 검증된 외부 envelope만 조회, 속도축 외삽 없음. 보수적(이웃 최소) 해석 기본, 보수/낙관 사이면 UNKNOWN | supplier_rated_envelope |

동률 규칙(tie rule): 최소 I², |ΔI²| ≤ 1e-12·I²이면 id가 큰 쪽(약계자 적은 쪽), 다음 |iq|가 작은 쪽, 다음 iq가 작은 쪽.

## 6. 판정 상태·사유·증거

- 상태: FEASIBLE / INFEASIBLE / UNKNOWN (임의 신뢰 점수 없음). 요구 판정 = claim들의 AND: 입증된 위반 하나면 FAIL, 위반 없고 UNKNOWN이 있으면 UNKNOWN, 모두 FEASIBLE이면 명시 범위의 PASS.
- 사유 코드: MISSING_INPUT, OUTSIDE_MODEL_DOMAIN, OUTSIDE_ALLOWED_OPERATING_DOMAIN, UNVALIDATED_DURATION, UNCERTAINTY_OVERLAP, NUMERICAL_UNRESOLVED, INVALID_INPUT, POLICY_LIMITATION, SAMPLED_COVERAGE, BOUNDARY_WITHIN_TOLERANCE, OUT_OF_SCOPE, CONSTRAINT_VIOLATION, NECESSARY_CONDITION_VIOLATED.
- 증거 종류: direct_evaluation, analytic_necessary_condition, exact_boundary_enumeration, numerical_witness, certified_bound, bounded_search, sampled_coverage, supplier_rated_envelope, empirically_validated_domain.
- Qualifier 예: “assumed-data conditional”, “boundary: active within numerical tolerance”, “boundary-qualified: requested torque within the capability bound tolerance”, “feasible at every examined Vdc point”.
- 수치 수용 검사: 해마다 토크 잔차 ≤ max(1e-3 N·m, 1e-6·torque scale), 정규화 hard 위반 ≤ 1e-7, 전력 항등식 잔차 ≤ max(0.01 W, 1e-9·power scale). 실패 시 claim을 NUMERICAL_UNRESOLVED로 강등.

## 7. 스크리닝 확장 (요청에 따라 추가, MVP 범위 밖)

Blueprint는 ASC/6SO 전환·열 지속시간·FuSa를 MVP non-goal로 두었으므로, 다음 모듈은 **축약 스크리닝**으로만 제공하고 판정을 PASS로 승격하지 않습니다.

| 모듈 | 내용 | 판정 규칙 |
|---|---|---|
| `extensions/timing.py` | 이벤트 체인(고장→감지→…→안전상태), min/nom/max, 주기 태스크 샘플링 지연, **중복 예산(같은 구간을 두 담당자가 예산화) 자동 검출**, FDTI/FRTI 분할과 할당 비교 | 물리적 안전 종점까지의 모든 연속 경로 중 보장 상한(최소 합) ≤ FTTI면 FEASIBLE(값 자체는 미검증); 초과는 UNKNOWN(BOUND_INCONCLUSIVE), 최댓값 동시 발생을 선언했거나 최솟값 합도 초과하면 INFEASIBLE; 명령 종점·공백·누락은 UNKNOWN |
| `extensions/dclink.py` | 저항 능동 방전(R_max, I0, P0, E_R, 도달 시간), 패시브 방전(블리더 R_p: 도달 시간, 상시 손실 V²/R_p, P·t 불변량, R_p 설계 창, 능동 병렬), 회전 중 역기전력 > V_f이면 **정류 위험**(RC 시간은 하한, 결합 모델 필요 → UNKNOWN; 목표 이하 최고 속도와 상수 모델의 정류 링크 전압 스크리닝 추정); 회생 중 배터리 차단 시 ½C(V₂²−V₁²) = E_in으로 과전압 도달 시간·허용 반응 시간 | 선언한 전력 프로파일·반응 시간·허용 손실 기준 |
| `extensions/safe_state.py` | Freewheel(6SO): 역기전력 선간 peak vs V_dc(비제어 정류 개시 속도), HV 차단 시 과전압 위험; ASC: v=0 정상상태 전류·제동 토크(상수 모델 해석해, 맵은 커버리지 내 수치해); 소자 정격·경로 가용성·전환 시간(선언값); 프로젝트/고객 규칙 별도 계층 | 항상 UNKNOWN(OUT_OF_SCOPE): 과도 peak, UCG 전류 크기, SOA, 검출, FuSa 미평가 — 안전 상태를 선택하지 않음 |
| `extensions/thermal.py` | Foster Z_th(t), 노드별 손실 배분, 한계 도달 시간, 지속시간별 가용 토크(“X N·m는 t초 유지, 이후 Y N·m”) | 검증된 열모델 + 조건 일치 시에만 FEASIBLE/INFEASIBLE, 그 외 UNKNOWN(UNVALIDATED_DURATION) + 추정치 |

fixture E01(1-node 열) 값 77.6424 °C / 138.6294 s를 재현합니다.

0.3.0에서 추가된 리뷰 P1 모듈(모듈 손실, DC-link 리플, 열 사이클 수명, 보호, ASC 과도, 전도 EMI), 모터 설계, 추가 명세 모듈(OEW·HEV,
경계별 효율·모듈 A/B, 가변 PWM, anti-jerk)의 판정 규칙과 상태는 [`TRACEABILITY.md`](TRACEABILITY.md)에 항목별로 있습니다. 공통 규칙:
스크리닝은 PASS로 승격하지 않고, 선언되지 않은 입력은 UNKNOWN이며, 합성 예시는 끝까지 합성으로 표시됩니다(evidence_missing).

## 8. Data provenance

- `reference/traction_workbench_spec_v1/`: 2026-09-27 v1.0 패키지 원본 그대로(manifest의 SHA-256·크기 일치). expected 값은 수정하지 않았습니다.
- 내장 드라이브 `SYNTH_IPMSM_200KW_REF_V1`: `synthetic_drive.json`에서 변환, provenance = synthetic, validation status = fixture 메타데이터 문구, 파일 SHA-256 기록.
- `MANUFACTURED_FLUX_MAP_TEST_DRIVE`: manufactured 자기 포텐셜 맵 + 합성 드라이브의 R_s·손실·전압 예약분을 빌린 **시험 구성**(두 fixture를 섞은 참조 모터가 아님).
- 예시 rating envelope(`examples/cases/req_ts_012_10s_with_example_rating.json`)과 예시 열망은 기능 시연용 합성 데이터로 명시되어 있습니다.

## 9. 검증 실행 결과 (요약)

`docs/VERIFICATION_REPORT.md` (자동 생성)에 전체 표가 있습니다.

- 참조 패키지 manifest: 10/10 일치.
- 독립 검산(production 비의존, 다른 방법): 136/136.
- Production vs golden: 정방향 6건 정규화 오차 ≤ 2e-16, 역문제 11건 id/iq 최대 오차 5.5e-6 A(경계해는 ~1e-13 A), 라벨 일치, capability 4건 오차 ≤ 3e-7 N·m(구동 3건 certified, 인증 상한 = golden 1e-13 이내), fixture의 Lagrangian 승수·Hessian 고유값 상대오차 < 1e-6.
- pytest 471개 통과(리뷰 재현 F01–F13·감사 재현·P0-B, P1 모듈, OEW/HEV, EMI, 효율 E-01..E-06, 가변 PWM, 드라이브라인 D-01..D-05,
  모터 설계, 교환 패키지, 그래프 데이터의 물리 일관성, 냉각수·Cauer·유량 보정, 패시브 방전, 회로도, PDF 보고서, 데스크톱 headless smoke 포함).
- 데스크톱 self-test 49/49. Windows CI에서 PyInstaller exe를 빌드하고 **동결된 exe로** acceptance 21/21과 self-test를 통과했습니다
  (아티팩트 `TractionWorkbench-windows-x64`).
- 관찰: MTPA 내부점 golden(I00/I09/I10)은 평탄한 목적함수 때문에 정확 해와 최대 5.5e-6 A 차이(50자리 계산으로 확인). 허용오차 1e-3 A 이내이며 expected 값은 그대로 둡니다.

## 10. 알려진 한계

1. 판정의 기준은 정상상태 기본파 모델입니다: 순간 peak, 반도체 SOA, OC overshoot, 과도 전압 headroom은 판정하지 않습니다. PWM 리플·전류 루프 위상 여유(가변 PWM 페이지), ASC 과도(보호 페이지), 드라이브라인 동특성(anti-jerk)은 선언된 축약 모델의 별도 분석이며 기본 판정을 바꾸지 않습니다.
2. 검증은 합성 fixture에 한정: V4(독립 모델 비교)·V5(시험/공급사 데이터) 미수행. 수치 자릿수는 제품 정확도가 아닙니다.
3. 회전 손실 토크는 전류 비의존(τ_rot(ω))만 지원: 전류/자속 의존 철손 토크는 미지원(PWM 고조파 철손은 선언된 상한으로만). 인버터 손실은 대칭 2차 surrogate 또는 데이터시트 모듈 모델(V_dc·f_sw·T_j·변조 의존, 표 범위 밖 외삽 없음); 공급사 손실 맵(측정 P_loss(I, V, T)) 직접 입력과 DPT 상관은 없습니다.
4. Δv_inv는 이상적(0) 또는 저항 강하만 지원: deadtime/소자 강하 모델, 합의된 단자 전압 envelope 함수 미지원.
5. Flux map은 쌍선형 보간(셀 내부)만. 곡선 추적은 행당 한 가지 분기를 가정하고 다중 분기를 감지하면 구간 구조 없이 표본 결과만 보고합니다. 셀 B&B 기본 깊이 7(10 A 셀 → 0.08 A)로 최소전류 인증 gap은 약 0.1 A 수준입니다.
6. 정책 capability의 음(회생) 방향은 표본 증거(연속성 가정)입니다. T–n 곡선은 속도 17점 표본이며 점 사이는 표시용입니다.
7. Vdc 범위 요구는 기본 5점 표본으로만 검사하며 연속 구간 PASS를 주장하지 않습니다(단조성 인증 미구현).
8. 구간 입력 분석은 독립 상자(모서리+중심)만: 상관 파라미터는 공동 시나리오로 표현해야 하며 최악점 인증은 없습니다.
9. 온도: 선형 계수 조정만 지원. 손실–온도 피드백은 모듈 A/B·가변 PWM 비교의 정상상태 전기열 고정점(모듈 T_j)에만 있고, 기본 판정·미션 과도 T_j에는 없습니다. 냉각수 물성 기본값은 일반 EG/물 표의 근사이며, 냉각수 자체의 열용량·수송 지연은
   무시(가열 쪽으로 보수적)하고, 유량에 따른 대류 저항은 사용자가 지정한 단에만 지수 법칙으로 보정합니다(검증된 값이 아님).
10. 스크리닝 확장은 7절과 [`TRACEABILITY.md`](TRACEABILITY.md)의 범위로 제한됩니다(UCG 전류 크기, 소자 SOA, 기능안전 승인, EMC 합격, 수명 보증 없음). OEW 과도 ASC(R-02 partial), 전원 개방·브리지 trip·전환 skew(R-03), 측정 파형 전력, 동기 PWM·spread spectrum, 샘플 유효창, 다관성 드라이브라인은 미구현입니다.
11. 그래프의 파형·듀티는 같은 기본파 값을 역 Park·min-max 영상분 주입으로 다시 표현한 평균값 모델입니다(스위칭 리플, 데드타임, 소자 강하 없음). MTPV는 상수 모델에서 R_s를 무시한 참고선입니다.
12. flux map 드라이브의 T–n 곡선은 계산량 때문에 격자 추정(시각화용, 라벨 표기)이며, 판정 자체는 항상 엄밀 솔버를 사용합니다. 맵·스윕은 표본 사이의 연속성을 보장하지 않습니다.
13. 실행 파일은 코드 서명되지 않았습니다(사내 배포 시 IT 정책에 따라 서명·화이트리스트 필요). 번들 크기는 Qt·SciPy 포함 수백 MB입니다.

## 11. 미구현 / 후속 항목

- **P0-C (evidence_missing)**: 실제 모터–인버터 한 조합의 정적 release evidence — 공급사·시험 데이터가 필요합니다. 합성 suite를 qualified baseline이라 부르지 않습니다.
- Roadmap C–G 본 구현(검증된 lumped thermal과 duty/recovery, DC source 결합(Vdc–Idc), vehicle, 선택 transient) — 보호·ASC 과도·감속기(방향별 손실)는 0.3.0에서 축약 모델로 추가.
- UC11(vehicle 변환)은 fixture E00 산술만 독립 검산에서 확인. 감속기는 효율 경계(P_m ↔ P_o)로만 있고 차량 모델은 없음.
- 공급사 손실 맵 직접 입력, 전류 의존 철손, 비선형 맵의 다중 분기 인증, Vdc 구간 단조성 인증.
- 추가 명세의 missing/partial 항목(OEW R-02/R-03, 측정 파형 ⟨v·i⟩, 저장에너지 자동 계산, 동기 PWM·random PWM, 샘플 유효창·stale 샘플,
  차동 인덕턴스 기반 동적 전압 여유, 센서 dropout·wheel slip, 다관성 협조)과 §14 디스커넥터(P2 보류) — [`TRACEABILITY.md`](TRACEABILITY.md).
- 물리 검증 증거(DPT·열량계·동력계, HIL, EMC 측정 보정, 차량 FRF) 없음: 해당 항목은 evidence_missing으로 표시됩니다.
- 모든 실패 항목: 없음(검증 실행에서 FAIL 0).

## 12. 재현 조건

- Python 3.11.15, NumPy 2.4.6, SciPy 1.17.1(보고서 생성 환경). 결과는 입력·모델·정책·`NumericalSettings`가 같으면 동일합니다(의사결정 기록은 입력 스냅샷 SHA-256으로 식별, 타임스탬프 없음).
- 기본 수치 설정은 `src/traction_workbench/settings.py`(`NumericalSettings`)에 있으며 모든 기록에 포함됩니다.

## 13. 스펙 조항 ↔ 구현 ↔ 테스트 추적표

| 조항 | 구현 | 테스트 |
|---|---|---|
| H3 convention, RMS/peak, 부호 | `physics.py` | `test_golden_forward.py` |
| H4.1 전기 계약, SPMSM, R_s 포함 | `physics.DriveKernel.evaluate` | `test_golden_forward.py::test_spmsm_is_not_lossless`, `test_edge_cases.py::test_spmsm_inverse_no_singularity` |
| H4.2 flux map 계약, 외삽 금지, 대칭, 온도 plane, 상호성 | `models/flux.py` | `test_flux_map.py` |
| H4.3 손실 closure, τ_rot(0)=0 | `models/components.RotationalLossModel` | `test_golden_forward.py::test_power_identities` |
| H4.4 전압 예산, reserve 분리 | `models/components.VoltageModel`, `physics.build_constraints` | `test_golden_inverse.py` |
| H4.5 DC 제약 동시 적용 | `scenario.DcSourceLimits`, `solvers/common.dc_ok` | `test_golden_inverse.py::test_policy_solution_matches_golden` |
| H4.6 에너지 모드·효율 | `physics.classify_energy` | `test_golden_forward.py::test_energy_modes_and_signs` |
| H5.1 forward | `physics.forward_evaluation` | `test_golden_forward.py::test_forward_does_not_move_a_violating_point` |
| H5.2 존재/capability, 커버리지 ≠ 물리 한계 | `solvers/capability.py`, `solvers/bounds.py` | `test_golden_capability.py`, `test_flux_map.py::test_incomplete_coverage_is_not_a_physical_limit` |
| H5.3 최소전류 정책, active-loss 비채택, tie rule | `solvers/policy.py`, `solvers/exact.py`, `solvers/common.better` | `test_golden_inverse.py`, `test_semantics.py::test_active_loss_candidate_is_flagged_not_adopted` |
| H5.4 지정 정책 | `analysis/supplied_policy.py` | `test_semantics.py::test_s07_*` |
| H5.5 역설계 | `analysis/sizing.py` | `test_analyses.py::test_sizing_*`, `test_bigger_inverter_does_not_fix_low_voltage` |
| H6 판정·사유·AND·구간·손실 구간 | `status.py`, `decision.py`, `analysis/uncertainty.py`, `analysis/loss_interval.py` | `test_semantics.py`, `test_decision.py` |
| H7 margin·dominance·joint | `physics.ConstraintResult`, `analysis/dominance.py` | `test_analyses.py::test_dominance_active_vs_limiting`, `test_joint_bottleneck_*` |
| H8 입력·결과·불변 스냅샷 | `io.py`, `units.py`, `decision.py` | `test_validation.py`, `test_decision.py::test_record_is_reproducible` |
| H9 불변식 1–10 | `physics.py`(항등식), 동일 후보점 계산, clip 없음 | `test_golden_forward.py`, `test_golden_inverse.py::test_numerical_acceptance` |
| H10 경계·오류 동작 | 각 모듈 validation, `solvers/policy.py` | `test_semantics.py::test_s02/s03/s05/s06`, `test_edge_cases.py`, `test_validation.py` |
| H11 필수 사례 | 위 전체 | `test_golden_*.py`, `test_semantics.py`, `test_flux_map.py` |
| UC00 단위·정의 | `units.py`, `io.py` | `test_validation.py`, `test_app_layers.py::test_declared_units_drive_matches_builtin_results` |
| UC08 외부 rating | `analysis/rating.py` | `test_decision.py::test_duration_*` |
| 독립 fixture 검증 | `verification/independent_fixture_check.py` | 136 checks |
| 그래프 데이터(파형·벡터도·궤적·맵·기저속도·스크리닝 곡선) | `viz/operating.py`, `viz/sweeps.py`, `viz/maps.py`, `viz/design.py`, `viz/safety.py` | `test_viz.py` (독립 forward Park 역변환, 전력 항등식, MTPA 접선 조건, 기저속도 = 약계자 개시, golden capability) |
| 그림·PDF 보고서·데스크톱 앱 | `plots/`, `report_pdf.py`, `desktop/` | `test_reports_desktop.py`, `twb selftest` |
| 냉각수 루프·Foster/Cauer·유량 의존 열저항 | `extensions/coolant.py`, `extensions/thermal.py`, `api._thermal_model` | `test_thermal_coolant.py` (에너지 수지, Cauer ODE 대조, 유량 보정, 기준 온도) |
| 회로 개요도·열 회로도 | `plots/schematics.py`, `desktop/thermal_editor.py` | `test_reports_desktop.py::test_schematics_render`, desktop smoke |
| 배포 | `packaging/` (PyInstaller spec, 빌드·동결 self-test), `.github/workflows/build.yml` | CI Windows job |
| 리뷰 P0-A F01–F13, 감사 재현 | `solvers/gate.py`, `requirement.py`, `extensions/timing.py`·`thermal.py`·`dclink.py`, `io.py`, `models/flux.py`, `analysis/sizing.py`·`uncertainty.py`·`rating.py`, `decision.py`(claim 층) | `test_review_p0a.py` |
| 리뷰 P0-B 데이터 계약·data audit | `io.py`, `models/`, `service.data_audit` | `test_review_p0b.py` |
| P1: 모듈 손실·DC-link 리플·수명·보호·ASC 과도·전도 EMI | `extensions/module_loss.py`·`dclink_ripple.py`·`lifetime.py`·`protection.py`·`asc_transient.py`·`emi.py` | `test_module_loss.py`, `test_dclink_ripple.py`, `test_lifetime.py`, `test_protection.py`, `test_asc_transient.py`, `test_emi.py` |
| 모터 설계 (§10) | `analysis/machine_design.py` | `test_machine_design.py` (권선계수 교과서 값, dq 스케일링 항등식) |
| OEW·HEV | `extensions/oew.py`, `extensions/hev.py` | `test_oew.py`, `test_hev.py` |
| 경계별 효율·감속기·미션·모듈 A/B | `analysis/efficiency.py` | `test_efficiency.py` (E-01..E-06) |
| 가변 PWM | `extensions/pwm_policy.py` | `test_pwm_policy.py` (시간 적분 = 스펙트럼 리플, 이벤트 합 = 평균 손실) |
| Anti-jerk·능동 감쇠 | `extensions/driveline.py` | `test_driveline.py` (D-01..D-05, 출력 좌표 독립 ODE) |
| 교환 패키지 | `exchange.py`, `cli.py exchange` | `test_exchange.py` |
| 항목별 상태 (implemented / partial / missing / evidence_missing) | — | [`TRACEABILITY.md`](TRACEABILITY.md) |
