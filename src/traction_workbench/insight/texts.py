"""The engines' recurring English sentences in the reading's language (numbers and names kept verbatim).

The records keep the engines' English text (traceability); a reading shows these sentences in Korean when the UI is
Korean.  Only fixed sentences and templates the engines emit are translated; anything else is shown unchanged.
"""

from __future__ import annotations

import re

from ..i18n import language

_FIXED = {
    "duration: not stated in the requirement -> static item only (duration aspect undetermined)":
        "지속시간: 요구에 없음 → 정적 항목으로만 판정 (지속 측면은 미확정)",
    "PWM ripple, instantaneous peak current, semiconductor SOA and OC overshoot":
        "PWM 리플, 순간 피크 전류, 반도체 SOA, 과전류 오버슈트",
    "current-control dynamics, stability and voltage headroom for transients":
        "전류 제어 동특성·안정성과 과도용 전압 헤드룸",
    "thermal duration unless a matching validated rating envelope is supplied":
        "열 지속시간 (조건이 맞는 검증된 정격 포락선이 주어지지 않으면)",
    "demagnetisation, rotor strength and insulation (declared domains are not certifications)":
        "감자, 회전자 강도, 절연 (선언된 운전 영역은 인증이 아님)",
    "fault transitions (ASC/6SO) and functional-safety approval":
        "고장 전이(ASC/6SO)와 기능안전 승인",
    "constant-parameter model inside the declared id range only (not a general statement about the motor)":
        "상수 파라미터 모델·선언된 id 범위 안에서만 성립 (모터 일반에 대한 진술이 아님)",
    "independent of motor model, control policy and loss values (losses are passive)":
        "모터 모델·제어 정책·손실값과 무관 (손실은 수동적)",
    "independent of control policy and magnetic model; uses the declared loss models and current limit":
        "제어 정책·자기 모델과 무관; 선언된 손실 모델과 전류 한계를 사용",
    # design-page notes
    "regions: FEASIBLE = witnessed, INFEASIBLE = proven excluded at the samples, UNKNOWN = unresolved; between samples "
    "every statement is sampled, not a continuous proof":
        "구간: 가능 = 근거점 확인, 불가능 = 표본에서 증명된 배제, 미확정 = 미해결 — 표본 사이는 표본 진술일 뿐 연속 증명이 아닙니다",
    "the synthetic inverter-loss surrogate has no Vdc dependence; switching-loss change with Vdc needs loss data before "
    "this becomes a hardware proposal":
        "합성 인버터 손실 대체 모델은 Vdc에 의존하지 않습니다 — Vdc에 따른 스위칭 손실 변화는 손실 데이터가 있어야 하드웨어 "
        "제안이 됩니다",
    "searched relaxations up to +50% (26 steps + bisection); diagnostic only":
        "+50 %까지 완화 탐색 (26단계 + 이분법) — 진단용",
    "a realisable change (e.g. Vdc) moves several limits at once: use one-parameter sizing for it":
        "실현 가능한 변경(예: Vdc)은 여러 한계를 함께 움직입니다 — 그런 변경은 1-파라미터 역설계로 보세요",
    "no single relaxation suffices: the listed pairs are joint bottlenecks":
        "단일 완화로는 부족합니다 — 나열된 쌍이 공동 병목입니다",
    # thermal
    "start from equilibrium at the coolant": "냉각수 온도 평형에서 시작",
    "constant losses at the minimum-current point": "최소전류 운전점의 손실을 일정하게 유지",
    "no loss-temperature feedback": "손실–온도 피드백 없음",
    "thermal model not declared validated": "열 모델이 검증된 것으로 선언되지 않음",
    "declared loss shares per node": "노드별 손실 분담은 선언값",
    "coolant rise along the declared loop (m_dot*c_p)": "선언된 냉각 루프를 따라 냉각수 온도 상승 (ṁ·c_p)",
    "sampled: narrow features between samples can be missed; the set may be disconnected":
        "표본 검사: 표본 사이의 좁은 변화는 놓칠 수 있고, 가능한 토크 집합이 끊겨 있을 수 있습니다",
    "screening estimate: not a duration rating (model not qualified for this question)":
        "스크리닝 추정 — 지속시간 정격이 아닙니다 (이 질문에 적격한 모델이 아님)",
    "static capability": "정적 capability",
    "limited by a node temperature": "노드 온도가 제한",
    "the first pulse itself reaches a limit": "첫 펄스만으로 한계에 도달",
    "the inverter loss model has no junction-temperature input (no T_j feedback)":
        "인버터 손실 모델에 접합 온도 입력이 없음 (T_j 피드백 없음)",
    "coolant rise follows each step's losses instantly (loop thermal mass not modelled; conservative for pulse peaks)":
        "냉각수 상승은 매 단계 손실을 즉시 따름 (루프 열용량 미모델 — 펄스 피크에 보수적)",
    # power stage
    "datasheet typical values are not guaranteed upper bounds": "데이터시트 typical 값은 보장 상한이 아닙니다",
    "loss computed; no allowed loss stated to judge against": "손실은 계산됨 — 판정할 허용 손실이 선언되지 않음",
    "no supplier life data for this capacitor series (no generic '10 K halves the life' rule is applied)":
        "이 커패시터 시리즈의 공급사 수명 데이터 없음 (일반적인 '10 K마다 수명 절반' 규칙은 적용하지 않음)",
    "rainflow counts reversals of the given history; sampling below the thermal bandwidth hides cycles":
        "rainflow는 주어진 이력의 반전을 셉니다 — 열 대역폭보다 성긴 표본은 사이클을 숨깁니다",
    "a Tj history from a screening thermal model gives screening damage only":
        "스크리닝 열모델의 T_j 이력으로는 스크리닝 손상만 얻습니다",
    "current ripple": "전류 리플", "dead-time voltage error": "데드타임 전압 오차",
    "minimum pulse / pulse dropping": "최소 펄스·펄스 누락", "ringing": "링잉", "phase-current ripple": "상전류 리플",
    "dead time": "데드타임", "device edges / ringing": "소자 에지·링잉", "overmodulation": "과변조",
    "C / ESL / ESR tolerances": "C·ESL·ESR 공차",
    "busbar impedance between capacitor and inverter terminals": "커패시터–인버터 단자 사이 버스바 임피던스",
    "asynchronous-carrier sidebands (synchronous ratio used)": "비동기 캐리어 측파대 (동기 비 사용)",
    "die-level sharing inside a module": "모듈 안 다이 간 전류 분담",
    "C_oss hard-commutation at zero current": "0 전류에서 C_oss 하드 커뮤테이션",
    "low-frequency junction ripple within the fundamental period (average model)": "기본파 주기 안의 저주파 접합 온도 리플 (평균 모델)",
    # requirement_set.classify hints
    "state the missing input named in the actions (a declaration usually costs nothing)":
        "다음 조치에 적힌 누락 입력을 선언하세요 (선언은 대개 비용이 들지 않습니다)",
    "bind the evidence to this product and its conditions, or supply evidence for this exact question (e.g. a rating "
    "of the requirement's own duration)":
        "근거를 이 제품과 조건에 묶거나, 이 질문에 맞는 근거를 제공하세요 (예: 요구 지속시간 그대로의 정격)",
    "prove the range (a monotonicity certificate whose conditions hold) or narrow it; a denser grid is not a proof":
        "범위를 증명하세요 (조건이 성립하는 단조성 인증서) 또는 범위를 좁히세요 — 더 조밀한 격자는 증명이 아닙니다",
    "extend the model data (coverage, temperature planes, domain) or state conditions inside it":
        "모델 데이터(범위·온도 plane·영역)를 넓히거나, 그 안의 조건으로 요구를 적으세요",
    "the answer sits within the numerical tolerance of a boundary: tighten the tolerance or examine the boundary":
        "답이 경계의 수치 허용오차 안에 있습니다 — 허용오차를 줄이거나 경계를 검토하세요",
    "declare which evidence is authoritative (priority); the list order never decides":
        "어느 근거가 우선인지 선언하세요 — 목록 순서로는 정하지 않습니다",
    "the minimum-current policy limits here; another control policy needs its own evaluation":
        "여기서는 최소전류 정책이 한계입니다 — 다른 제어 정책은 따로 평가해야 합니다",
    "a rating covering the request (or a qualified thermal model) would be needed":
        "요구를 덮는 정격(또는 적격한 열 모델)이 필요합니다",
    "a design or requirement change is needed (see the limiting cause)":
        "설계나 요구를 바꿔야 합니다 (제한 원인 참조)",
    "bracketed between an insufficient and a sufficient relaxation (sampled)":
        "불충분·충분 완화 사이에서 이분법으로 좁힌 경계 (표본)",
    "the Vdc range was examined at sampled points only: a PASS over the continuous range needs a monotonicity "
    "argument or denser analysis":
        "Vdc 범위를 표본점에서만 검사했습니다 — 연속 구간 전체의 PASS에는 단조성 논거나 더 조밀한 분석이 필요합니다",
    # safety: FTTI
    "periodic items add one period of sampling delay to their worst case": "주기 항목은 최악값에 샘플링 지연 한 주기를 더함",
    "every contiguous item path is a sound bound; the tightest one is reported; items off the chosen path are not summed":
        "이어지는 항목 경로는 모두 유효한 상한 — 가장 촘촘한 경로를 보고하며, 선택 경로 밖 항목은 합산하지 않음",
    "FHTI = FDTI + FRTI holds on one trace; for independent worst cases only sup(D+R) <= sup D + sup R":
        "FHTI = FDTI + FRTI는 한 트레이스에서 성립 — 독립적인 최악값끼리는 sup(D+R) ≤ sup D + sup R만 성립",
    "assumes the declared latencies are correct upper bounds": "선언된 지연값이 올바른 상한이라고 가정",
    "the chain declares that the item maxima occur together in one trace": "항목 최댓값들이 한 트레이스에서 함께 일어난다고 선언됨",
    "sum of independent maxima: an upper bound, not a failure witness": "독립 최댓값의 합 — 상한일 뿐 실패 반례가 아님",
    "no contiguous chain of budget items connects the fault to the safe state (items overlap but never join)":
        "고장에서 안전 상태까지 이어지는 예산 항목 체인이 없음 (항목이 겹치지만 이어지지 않음)",
    "no detection event declared: the split is undefined": "검출 이벤트가 선언되지 않아 FDTI/FRTI 분할이 정의되지 않음",
    # safety: discharge, overvoltage
    "screening: ideal RC, battery disconnected": "스크리닝: 이상적 RC, 배터리 분리",
    "screening: ideal RC, battery disconnected, bleeder always connected": "스크리닝: 이상적 RC, 배터리 분리, 블리더 상시 연결",
    "battery disconnected": "배터리 분리", "ideal capacitor (no ESR)": "이상적 커패시터 (ESR 없음)",
    "constant resistance": "저항값 일정", "no other DC loads or sources": "다른 DC 부하·소스 없음",
    "motor back-EMF below Vf (checked if speed given)": "모터 역기전력 < V_f (속도가 주어지면 확인)",
    "bleeder permanently across the DC link (no switch)": "블리더가 DC 링크에 상시 연결 (스위치 없음)",
    "battery disconnected during the discharge": "방전 중 배터리 분리",
    "constant resistance (no temperature coefficient)": "저항값 일정 (온도 계수 없음)",
    "continuous loss P = V^2/R_p whenever the link is energised (contactors closed)":
        "링크가 충전된 동안(릴레이 닫힘) 상시 손실 P = V²/R_p",
    "resistor derating, voltage rating of the resistor chain and creepage are not checked":
        "저항 디레이팅, 저항 체인의 전압 정격, 연면 거리는 확인하지 않음",
    "screening: declared power profile and reaction time": "스크리닝: 선언된 전력 프로파일·반응 시간",
    "reaction time not given: the result is the maximum allowed reaction time": "반응 시간 미지정 — 결과는 허용되는 최대 반응 시간",
    "lossless energy balance 1/2 C (V2^2 - V1^2) = E_in": "무손실 에너지 수지 ½·C·(V₂² − V₁²) = E_in",
    "no other DC loads, ESR or stray L": "다른 DC 부하·ESR·표유 인덕턴스 없음",
    "flux at zero current not covered by the map": "자속 지도가 0 전류의 자속을 포함하지 않음",
    "regenerated power profile: constant from P_in to 0 over the reaction time":
        "회생 전력 프로파일: 일정 (반응 시간까지 P_in 유지 후 0)",
    "regenerated power profile: linear_ramp_down from P_in to 0 over the reaction time":
        "회생 전력 프로파일: 선형 감소 (반응 시간 동안 P_in에서 0으로)",
    "regenerated power profile: delay_then_ramp from P_in to 0 over the reaction time":
        "회생 전력 프로파일: 지연 후 선형 감소 (반응 시간 동안 P_in 유지 후 ramp로 0)",
    # safety: safe state
    "closed form (constant-parameter model)": "닫힌 해 (상수 파라미터 모델)",
    "numerical solve of v = 0 inside the covered flux-map data": "자속 지도 범위 안에서 v = 0 수치 해",
    "transients, UCG current magnitude, SOA, detection and FuSa approval are not evaluated; the screening informs, it "
    "does not select a safe state":
        "과도, UCG 전류 크기, SOA, 검출, 기능안전 승인은 평가하지 않음 — 스크리닝은 정보를 줄 뿐 안전 상태를 고르지 않음",
    "low speed: ASC gives large braking torque; high speed: ASC current tends to psi/Ld with small torque while "
    "freewheel risks uncontrolled rectification":
        "저속: ASC 제동 토크가 큼 · 고속: ASC 전류는 ψ/L_d에 가까워지고 토크는 작음, freewheel은 비제어 정류 위험",
    "controlled zero torque or normal high-speed operation is not the same as 6SO safety":
        "제어된 0 토크나 정상 고속 운전은 6SO 안전과 같지 않음",
    "standstill without resistance: ASC current undefined": "저항 없는 정지 상태: ASC 전류 정의 불가",
    "ASC operating point not found inside the covered flux-map data (no extrapolation)":
        "자속 지도 범위 안에서 ASC 운전점을 찾지 못함 (외삽 없음)",
    "back-EMF exceeds the device rating": "역기전력이 소자 정격을 넘음",
    "link can be driven above the limit": "링크가 한계 위로 올라갈 수 있음", "back-EMF below the link limit": "역기전력이 링크 한계 아래",
    # protection review
    "DC-link capacitor voltage": "DC 링크 커패시터 전압", "junction temperature": "접합 온도",
    "the sensed samples do not fall below the release threshold in the horizon":
        "계산 구간 안에서 측정 샘플이 복귀 임계 아래로 내려가지 않음",
    "declared architecture: not declared - conditional on the declaration; not functional-safety evidence":
        "선언된 구조: 미선언 — 선언을 전제로 한 조건부이며 기능안전 근거가 아님",
    "needs the project's priority table and viable reaction paths per fault combination":
        "프로젝트의 우선순위 표와 고장 조합별 가능한 반응 경로가 필요",
    "scalar plant with exact piecewise solution; device ringing, SOA, fast OC/SC and sensor saturation are outside this "
    "model (switched simulation / DPT / HIL needed)":
        "스칼라 플랜트의 정확한 구간별 해 — 소자 링잉, SOA, 빠른 OC/SC, 센서 포화는 이 모델 밖 (스위칭 시뮬레이션·DPT·HIL 필요)",
    "no normal operating/transient trajectory declared: nuisance behaviour not covered":
        "정상 운전·과도 궤적이 선언되지 않음 — 오동작 여부 미평가",
    "no warning threshold / required intervention time declared": "경고 임계·필요 개입 시간이 선언되지 않음",
    "normal maximum not declared: the nuisance side of the window is open": "정상 최대값 미선언 — 창의 오동작 쪽 경계가 열려 있음",
    "rows 01-06 are computed on causal trajectories / bounds; 07-09 state what this native model cannot establish":
        "01–06은 인과 궤적·bound로 계산했고, 07–09는 이 모델이 확립할 수 없는 것을 적습니다",
    "treating the whole delay as an immediate ramp would understate E_after (optimistic)":
        "지연 전체를 즉시 시작하는 ramp로 보면 E_after를 과소평가 (낙관적)",
    # ASC transient
    "no validated demagnetisation envelope imported (the declared id domain is not a demag safe-domain)":
        "검증된 감자 envelope 없음 (선언된 id 영역은 감자 안전 영역이 아님)",
    "no supplier pulse / SOA / I^2 t envelope for this waveform, voltage, temperature and gate condition (a short-circuit "
    "withstand time is not an ASC permission)":
        "이 파형·전압·온도·게이트 조건의 공급사 펄스·SOA·I²t envelope 없음 (단락 내량 시간은 ASC 허용이 아님)",
    "SCREENING (constant-parameter linear magnetic model; saturation, cross-coupling and temperature change not "
    "represented)": "스크리닝 (상수 파라미터 선형 자기 모델 — 포화·교차결합·온도 변화 미반영)",
    "saturation / cross-coupling in the fault domain": "고장 영역의 포화·교차결합",
    "device drops and diode/channel commutation": "소자 전압 강하와 다이오드·채널 전류 전환",
    "phase skew of the short": "단락 시점의 상간 어긋남", "gate-driver UVLO / OC priority over ASC": "게이트 드라이버 UVLO·OC 보호와 ASC의 우선순위",
    "magnet temperature change during the event": "사건 중 자석 온도 변화", "6SO / freewheel rectifier interval": "6SO·freewheel 정류 구간",
    # solver claim details (shared by every page that shows claims)
    "model not available at this scenario": "이 시나리오에서 모델을 쓸 수 없음",
    "a statically feasible electrical solution exists": "정적으로 가능한 전기적 해가 있음",
    "no statically feasible electrical solution in the declared domain (fully covered)":
        "선언된 영역(전 범위 포함)에 정적으로 가능한 전기적 해가 없음",
    "an analytic necessary condition is violated": "해석적 필요조건 위반",
    "no DC source limits declared for this scenario": "이 시나리오에 DC 소스 한계가 선언되지 않음",
    "discharge limit exceeded for any passive inverter loss": "어떤 수동 인버터 손실에서도 방전 한계 초과",
    "average DC power/current within the declared limits at the policy point": "정책 운전점의 평균 DC 전력·전류가 선언된 한계 안",
    "no electrical solution exists for the policy to select": "정책이 고를 전기적 해가 없음",
    "the minimum-current operating point could not be certified": "최소전류 운전점을 인증하지 못함",
    "the policy operating point meets every constraint incl. DC": "정책 운전점이 DC를 포함한 모든 제약을 만족",
    "an analytic bound excludes every control, independent of the optimiser": "해석적 경계가 최적화기와 무관하게 모든 제어를 배제",
    "no electrical solution, hence none with DC limits": "전기적 해가 없으므로 DC 한계를 포함한 해도 없음",
    "the policy point itself is DC-compatible (module losses)": "정책 운전점 자체가 DC 한계와 양립 (모듈 손실)",
    "the policy point itself is DC-compatible": "정책 운전점 자체가 DC 한계와 양립",
    "loss model missing": "손실 모델 없음", "no DC source limit declared": "DC 소스 한계 미선언",
    "no control meets the DC limits at this torque": "이 토크에서 DC 한계를 만족하는 제어가 없음",
    # efficiency
    "speed-only loss-equivalent torque b*omega + c*omega*|omega| (fundamental iron loss included as declared): it does "
    "not follow load, field weakening or PWM harmonics - a separate iron-loss map is never added on top of it (double "
    "counting), and an id/iq-dependent iron loss would change the premises of the speed-only torque curve and of the "
    "I^2-based DC certificates":
        "속도만의 손실 등가 토크 b·ω + c·ω·|ω| (선언대로 기본파 철손 포함) — 부하·약계자·PWM 고조파를 따르지 않으며, 별도 "
        "철손 지도를 더하지 않음 (이중 계산); id/iq에 의존하는 철손은 속도 전용 토크 곡선과 I² 기반 DC 인증서의 전제를 바꿈",
    "speed-only loss-equivalent torque b*omega + c*omega*|omega| (mechanical; the iron loss is NOT declared in it and "
    "is not evaluated elsewhere at the fundamental)":
        "속도만의 손실 등가 토크 b·ω + c·ω·|ω| (기계적 — 철손은 여기에 선언되지 않았고 기본파에서 따로 평가되지도 않음)",
    "declared upper bound (stator/rotor iron + PM eddy current; never an expected value)":
        "선언된 상한 (고정자·회전자 철손 + 자석 와전류 — 기대값이 아님)",
    "no declared bound (never set to 0)": "선언된 상한 없음 (0으로 두지 않음)",
    "not evaluated (no declared L_hf / harmonic data)": "평가 안 함 (선언된 L_hf·고조파 데이터 없음)",
    "gate driver / auxiliary (external LV supply)": "게이트 드라이버·보조 전원 (외부 LV 공급)",
    "DC-link capacitor ESR": "DC 링크 커패시터 ESR", "busbar / terminals": "버스바·단자", "control electronics": "제어 전자부",
    "current ripple / dead-time error": "전류 리플·데드타임 오차",
    "fundamental steady-state model: PWM harmonic losses not included (not evaluated)":
        "기본파 정상상태 모델 — PWM 고조파 손실 미포함 (미평가)",
    "standstill: DC->AC terminal conversion ratio; the motor gives no useful mechanical output (motor / eDrive "
    "efficiency N/A)": "정지: DC→AC 단자 변환 비 — 모터의 유용한 기계 출력이 없음 (모터·eDrive 효율 해당 없음)",
    "no throughput at either port": "어느 포트에도 전력 흐름이 없음",
    "power leaves at a port without an input (a source inside the boundary or a sign / definition error)":
        "입력 없이 포트로 전력이 나감 (경계 안의 소스 또는 부호·정의 오류)",
    "standstill: no power through the reducer (static torque ratio not modelled)":
        "정지: 감속기를 지나는 전력 없음 (정적 토크비는 모델 밖)",
    "outside the forward loss map": "정방향 손실 지도 밖", "outside the reverse loss map": "역방향 손실 지도 밖",
    "mixed-flow region (motor input below the loss, output power negative): the directional loss maps do not define "
    "this direction": "혼합 흐름 영역 (모터 입력 < 손실, 출력 음수) — 방향별 손실 지도가 이 방향을 정의하지 않음",
    "directional efficiencies + drag": "방향별 효율 + 드래그", "directional loss map": "방향별 손실 지도",
    "the electromagnetic conversion power T_em*omega_m is not the shaft power; each boundary is judged on its own "
    "ports; the PWM harmonic items are additional to the fundamental port powers (they appear in "
    "eta_interval_incl_pwm_hf, not in eta)":
        "전자기 변환 전력 T_em·ω_m은 축 출력이 아님; 각 경계는 자기 포트로 판정; PWM 고조파 항목은 기본파 포트 전력에 "
        "더해지는 것이며 η가 아니라 PWM 포함 η 구간에 나타남",
    "values at minimum-current policy points; only FEASIBLE cells are feasible operation, UNKNOWN cells are shown "
    "hatched, INFEASIBLE cells are blank":
        "최소전류 정책 운전점의 값 — '가능' 셀만 실제 운전 가능, '미확정' 셀은 빗금, '불가능' 셀은 빈칸",
    "net output / net DC is not a conversion efficiency (drive and regeneration cancel); direction efficiencies are "
    "energy ratios of their own segments":
        "순 출력 / 순 DC는 변환 효율이 아님 (구동과 회생이 상쇄) — 방향별 효율은 각자 구간의 에너지 비",
    "no declared loss error budget for both candidates: estimate shown, ranking reserved":
        "두 후보 모두의 손실 오차 예산이 선언되지 않음 — 추정만 보이고 순위는 유보",
    "both candidates must deliver the same requirement (FEASIBLE) before any efficiency ranking":
        "효율 순위 전에 두 후보가 같은 요구를 실제로 수행(가능)해야 함",
    "a candidate does not deliver the whole trajectory: lower consumption is not ranked as an improvement":
        "한 후보가 전체 궤적을 수행하지 못함 — 적게 소비한 것을 개선으로 순위 매기지 않음",
    "semiconductor (module) loss only - not an eDrive ranking: motor PWM-harmonic, capacitor and auxiliary losses are "
    "not in it; the eDrive efficiency deltas cover the modelled parts only":
        "반도체(모듈) 손실만의 비교 — eDrive 순위가 아님: 모터 PWM 고조파·커패시터·보조 손실은 빠져 있고, eDrive 효율 차는 "
        "모델링된 부분만 반영",
    "DC-link capacitor losses": "DC 링크 커패시터 손실",
    "SOA / short-circuit withstand / lifetime (efficiency does not approve them)": "SOA·단락 내량·수명 (효율이 승인하지 않음)",
    "EMC / dv/dt / overshoot after a gate or frequency change": "게이트·주파수 변경 뒤 EMC·dv/dt·오버슈트",
    "DC-link ripple and capacitor heating at the new frequency": "새 주파수에서 DC 링크 리플·커패시터 발열",
    "control timing at the new period": "새 주기에서 제어 타이밍",
    "fixed-policy: the module change under one declared modulation and carrier; design-specific: module + declared "
    "policy changes (a comparison of declared policies, not a global optimisation). No technology is better by rule; "
    "typical data do not rank a production population.":
        "고정 정책: 선언된 한 변조·캐리어 아래의 모듈 변경; 설계별: 모듈 + 선언된 정책 변경 (선언된 정책끼리의 비교이지 전역 "
        "최적화가 아님). 규칙으로 더 나은 기술은 없고, typical 데이터로 생산 모집단의 순위를 정하지 않음.",
}

_PATTERNS = [
    (r"voltage is a necessary-condition limit independent of the current rating: a larger current rating cannot fix "
     r"it; examine DC terminal voltage \(see sizing\) or the motor/declared id domain",
     "전압이 필요조건 한계입니다 — 전류 정격과 무관하므로 전류 정격을 키워도 해결되지 않습니다. DC 단자 전압(역설계 참조)이나 "
     "모터·선언된 id 영역을 검토하세요"),
    (r"no electrical solution in the declared domain: examine Vdc, current limit, declared id domain or motor data "
     r"\(see sizing/dominance\)",
     "선언된 영역에 전기적 해가 없습니다 — Vdc, 전류 한계, 선언된 id 영역, 모터 데이터를 검토하세요 (역설계·병목 참조)"),
    (r"DC discharge limit binds \((.*)\): raise the source limit or lower the request",
     r"DC 방전 한계가 걸립니다 (\1) — 소스 한계를 올리거나 요구를 낮추세요"),
    (r"battery charge acceptance binds \((.*)\); deliberately raising losses is not an energy-recovering policy - "
     r"confirm charge limits at the relevant SOC/temperature or split braking with the friction brake \(outside this "
     r"model\)",
     r"배터리 충전 수용 한계가 걸립니다 (\1) — 손실을 일부러 늘리는 것은 에너지 회수 정책이 아닙니다. 해당 SOC·온도의 충전 "
     r"한계를 확인하거나 마찰 브레이크와 제동을 나누세요 (이 모델 밖)"),
    (r"data needed: (.*)", r"필요한 데이터: \1"),
    (r"extend flux-map coverage \(nearest uncovered allowed point \|i\| = (.*) A\) before claiming infeasibility "
     r"beyond the data",
     r"자속 지도의 범위를 넓히세요 (가장 가까운 미포함 허용점 |i| = \1 A) — 데이터 밖에서는 불가능을 주장하지 않습니다"),
    (r"provide a validated (.*) rating envelope at matching conditions \(coolant, initial state, Vdc, switching "
     r"frequency\) or a validated thermal model; the static result is not a duration rating",
     r"조건(냉각수·초기 상태·Vdc·스위칭 주파수)이 맞는 검증된 \1 정격 포락선이나 검증된 열 모델을 제공하세요 — 정적 결과는 "
     r"지속시간 정격이 아닙니다"),
]
_PATTERNS += [
    (r"one-parameter change of (\S+) \((\w+)\); coupled constraints and losses recomputed at every sample; searched only "
     r"inside \[(.*)\] \(no extrapolation\)",
     r"파라미터 하나(\1, \2)만 바꾼 결과 — 매 표본에서 결합 제약·손실을 다시 계산; [\3] 안에서만 탐색 (외삽 없음)"),
    (r"thermal node (.*)", r"열 노드 \1"),
    (r"rotational loss (.*) W heats no node: the heat source is unmonitored",
     r"회전 손실 \1 W가 어느 노드도 가열하지 않습니다 — 감시되지 않는 열원"),
]
_PATTERNS += [
    # safety: FTTI claim details
    (r"guaranteed bound (.*) ms leaves (.*) ms margin", r"보장 상한 \1 ms, 여유 \2 ms"),
    (r"the summed maxima (.*) ms exceed the FTTI by (.*) ms: the declared bounds cannot guarantee the FTTI \(not proven "
     r"to fail - refine with a joint worst-case trace, or declare that the maxima are jointly attainable\)",
     r"최댓값의 합 \1 ms가 FTTI를 \2 ms 넘음 — 선언된 상한으로는 FTTI를 보장할 수 없음 (실패가 증명된 것은 아님: 공동 최악 "
     r"트레이스로 좁히거나 최댓값의 동시 발생을 선언하세요)"),
    (r"attainable worst case (.*) ms exceeds the FTTI by (.*) ms", r"동시 발생 가능한 최악 \1 ms가 FTTI를 \2 ms 넘음"),
    (r"even the summed minima (.*) ms exceed the FTTI: every trace is too slow",
     r"최소값의 합 \1 ms조차 FTTI를 넘음 — 모든 트레이스가 너무 느림"),
    (r"the chain ends at '(.*)', declared as a command, not the physical safe state: add the actuation -> current/torque "
     r"decay -> safe-state interval",
     r"체인이 '\1'(명령)에서 끝남 — 물리적 안전 상태가 아님: 구동 → 전류·토크 감쇠 → 안전 상태 구간을 추가하세요"),
    (r"maximum latency missing on every contiguous path for: (.*)", r"모든 경로에서 최대 지연이 빠진 항목: \1"),
    (r"the chain has unbudgeted intervals: (.*)", r"예산이 없는 구간: \1"),
    (r"(\S+) spans the detection event '(.*)': the FDTI/FRTI split of the chosen path is unknown \(a composite budget "
     r"cannot be split\)", r"\1이 검출 이벤트 '\2'를 가로지름 — 선택 경로의 FDTI/FRTI 분할을 알 수 없음 (복합 예산은 나눌 수 없음)"),
    # safety: discharge / overvoltage details
    (r"reaches (.*) V in (.*) s \(target (.*) s\)", r"\2 s에 \1 V 도달 (목표 \3 s)"),
    (r"needs (.*) s > (.*) s: (R|R_p) must be <= (.*) ohm", r"\1 s 필요 > \2 s — \3 ≤ \4 Ω이어야 함"),
    (r"continuous loss (.*) W at (.*) V", r"상시 손실 \1 W @ \2 V"),
    (r"continuous loss (.*) W at (.*) V exceeds the allowed (.*) W: R_p must be >= (.*) ohm",
     r"상시 손실 \1 W @ \2 V가 허용 \3 W를 넘음 — R_p ≥ \4 Ω이어야 함"),
    (r"no passive-only design: the loss limit needs R_p >= (.*) ohm but the time needs R_p <= (.*) ohm \(active discharge "
     r"or a lower loss-time demand required\)",
     r"블리더만으로는 불가: 손실 한계는 R_p ≥ \1 Ω, 시간은 R_p ≤ \2 Ω을 요구 (능동 방전이나 낮은 손실·시간 요구가 필요)"),
    (r"initial power (.*) W exceeds the resistor peak rating (.*) W", r"초기 전력 \1 W가 저항 peak 정격 \2 W를 넘음"),
    (r"pulse energy (.*) J exceeds the resistor energy rating (.*) J", r"펄스 에너지 \1 J이 저항 에너지 정격 \2 J을 넘음"),
    (r"back-EMF unknown \((.*)\)", r"역기전력 미상 (\1)"),
    (r"rectification risk at (.*) rpm: line-line back-EMF peak (.*) V > (.*) V, so the machine can feed the link through "
     r"the diodes below (.*) V; the discharge is slower than RC and whether (.*) V is reached depends on the machine "
     r"impedance vs R \(coupled source/load model required\)",
     r"\1 rpm에서 정류 위험: 역기전력 선간 peak \2 V > \3 V — 모터가 다이오드를 통해 \4 V 아래까지 링크를 충전할 수 있어 방전이 RC보다 "
     r"느리고, \5 V 도달 여부는 모터 임피던스와 R의 결합 모델이 정함"),
    (r"screening estimate of the link voltage held across R: (.*) V \((above|below) the target, not a bound\)",
     r"R에 걸리는 링크 전압의 스크리닝 추정 \1 V (목표보다 \2 — 상한 아님)"),
    (r"constant model psi_PM = (.*) Wb", r"상수 모델 ψ_PM = \1 Wb"),
    (r"peak (.*) V vs limit (.*) V \(allowed reaction (.*) ms\)", r"최고 \1 V vs 한계 \2 V (허용 반응 \3 ms)"),
    (r"regenerated power profile: (\S+) from P_in to 0 over the reaction time",
     r"회생 전력 프로파일: \1 (반응 시간 동안 P_in에서 0으로)"),
    (r"if the inverter opens \(freewheel\) at (.*) rpm the rectified back-EMF \((.*) V line-line peak\) can drive the "
     r"isolated link above (.*) V: freewheel is not a sufficient reaction by itself at this speed",
     r"\1 rpm에서 인버터를 끄면(freewheel) 정류된 역기전력(선간 peak \2 V)이 고립된 링크를 \3 V 위로 올릴 수 있음 — 이 속도에서는 "
     r"freewheel만으로는 충분한 반응이 아님"),
    (r"back-EMF (.*) V below the device rating (.*) V", r"역기전력 \1 V < 소자 정격 \2 V"),
    # protection review rows
    (r"(\d+) declared normal trajectories never confirm in (\d+) sampled phases \(coverage limited to the declared set and "
     r"sampled phases\)", r"선언된 정상 궤적 \1개가 \2개 샘플 위상에서 한 번도 확인(트립)되지 않음 (선언된 집합·샘플 위상에 한정)"),
    (r"peak (.*) < limit (.*) in all (\d+) sampled phases \(latest confirmation (.*) s\); sampled phases, not a proof over "
     r"the continuous fault domain",
     r"\3개 샘플 위상 모두 최고 \1 < 한계 \2 (가장 늦은 확인 \4 s) — 표본 위상 결과이며 연속 고장 영역의 증명이 아님"),
    (r"minimum lead (.*) ms over (\d+) sampled realisations \(needed (.*) ms; witness: (.*)\)",
     r"\2개 표본 실현에서 최소 선행 시간 \1 ms (필요 \3 ms; 반례: \4)"),
    (r"after the action at (.*) s the variable is bounded by (.*) < limit (.*?) \((.*)\)",
     r"반응(\1 s) 뒤 물리량 상한 \2 < 한계 \3 (\4)"),
    (r"window \((.*), (.*)\]: width (.*) \(before model/measurement reserves\)", r"창 (\1, \2]: 폭 \3 (모델·측정 예비 전)"),
    # ASC transient
    (r"screening indicates the requirement\(s\) (.*) are exceeded - confirm with a qualified nonlinear fault-domain model "
     r"before redesign", r"스크리닝상 요구 \1 초과 — 재설계 전에 적격한 비선형 고장 영역 모델로 확인하세요"),
]
_PATTERNS += [
    (r"violated at the policy operating point: (.*)", r"정책 운전점에서 위반: \1"),
    (r"(.*) not established \(a missing loss or port power is never set to zero\)",
     r"\1 미확정 (빠진 손실·포트 전력을 0으로 두지 않음)"),
    (r"mixed flow: power enters at both (\S+) and (\S+) and is dissipated inside the boundary",
     r"혼합 흐름: \1과 \2 양쪽으로 전력이 들어와 경계 안에서 소산"),
    (r"fundamental steady-state model; the PWM harmonic motor loss \[(.*), (.*)\] W is not in eta - see "
     r"eta_interval_incl_pwm_hf", r"기본파 정상상태 모델 — PWM 고조파 모터 손실 [\1, \2] W는 η에 없음 (PWM 포함 η 구간 참조)"),
    (r"R_ac\(f\) table \((.*)\)", r"R_ac(f) 표 (\1)"),
    (r"R_dc lower bound \((.*)\)", r"R_dc 하한 (\1)"),
    (r"\|dL\| (\S+) (W|J) vs combined module error budget (\S+) (W|J) \(typical datasheet values: not a "
     r"production-population winner\)",
     r"|ΔL| \1 \2 > 두 모듈 오차 예산의 합 \3 \4 (typical 데이터 — 생산 모집단의 승자는 아님)"),
    (r"\|dL\| (\S+) (W|J) vs combined module error budget (\S+) (W|J): within the budget",
     r"|ΔL| \1 \2 ≤ 두 모듈 오차 예산의 합 \3 \4 — 예산 안 (순위 유보)"),
    (r"\|dL\| (\S+) (W|J) vs combined module error budget (\S+) (W|J) \(motor points differ: motor-loss errors do not "
     r"cancel and are not in this budget\)",
     r"|ΔL| \1 \2 vs 두 모듈 오차 예산의 합 \3 \4 (모터 운전점이 달라 모터 손실 오차가 상쇄되지 않음 — 순위 유보)"),
    (r"\|dL\| (\S+) (W|J) vs combined module error budget (\S+) (W|J)", r"|ΔL| \1 \2 > 두 모듈 오차 예산의 합 \3 \4"),
    (r"motor PWM-harmonic losses: not evaluated and not asserted to cancel \((.*)\)",
     r"모터 PWM 고조파 손실: 평가하지 않았고 상쇄된다고 보지 않음 (\1)"),
    (r"module loss not established: (.*)", r"모듈 손실 미확정: \1"),
]
_TAG = re.compile(r"^\[(Vdc=[^\],]+)(?:, magnet ([^\]]+) degC)?\]\s*")


def engine_text(s: str) -> str:
    """A next action / not-evaluated item / limiting factor of the engines, in the reading's language."""
    if language() != "ko" or not s:
        return s
    tag = ""
    m = _TAG.match(s)
    if m:
        tag = f"[{m.group(1)}" + (f", 자석 {m.group(2)} °C" if m.group(2) else "") + "] "
        s = s[m.end():]
    if s in _FIXED:
        return tag + _FIXED[s]
    for pat, rep in _PATTERNS:
        if re.fullmatch(pat, s):
            return tag + _codes(re.sub(pat, rep, s))
    return tag + s


def engine_parts(s: str) -> str:
    """A '; '-joined engine message in the reading's language: the whole sentence when it is known, else clause by
    clause (each clause translated when known, kept verbatim otherwise)."""
    if language() != "ko" or not s:
        return s
    whole = engine_text(s)
    if whole != s or "; " not in s:
        return whole
    return "; ".join(engine_text(p) for p in s.split("; ") if p)


def _codes(s: str) -> str:
    """Constraint identifiers inside a translated sentence -> their display names."""
    from ..plots.labels import constraint_label
    return re.sub(r"\b(DC_(?:DIS)?CHARGE_(?:POWER|CURRENT)|VOLTAGE|CURRENT)\b", lambda m: constraint_label(m.group(1)), s)
