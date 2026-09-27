# Traction Engineering Feasibility & System Analysis Workbench
## 제품·물리 모델·판정·검증 설계 기준선 v1.0

작성일: 2026-09-27  
상태: 구현 인계를 위한 제안 기준선. 실기 검증 완료 또는 특정 시스템의 적합성 승인을 의미하지 않는다.  
대상: EV traction System Engineer / System PL  
초기 대상: 단일 3상 2-level inverter + PMSM/IPMSM

## 1. Product Definition

### 1.1 무엇을 만드는가

고객 요구를 특정 운전 조건의 인버터–모터 허용 운전영역과 대조하여, 가능한 운전점, 설계 여유, 제한 요인, 결론의 적용 범위, 추가로 필요한 데이터를 제시하는 engineering decision workbench를 만든다.

핵심 산출물은 그래프나 최대 출력 숫자가 아니라 재현 가능한 **Engineering Decision Record**다. 이 기록에는 원 요구사항, 해석한 물리량/측정 경계, 시나리오, 입력 출처, 가정, 모델과 버전, 해 또는 불가능 근거, 제약 여유, 불확실성, 다음 조치가 연결되어야 한다.

제품 가치는 다음 질문으로 평가한다.

> “현재 조건에서 고객 요구를 만족시킬 수 있는가? 무엇이 막고 있으며, 어떤 변경이나 추가 자료가 의사결정을 바꾸는가?”

단순 토크–속도 곡선, MTPA/FW 경로, id–iq 제약 곡선은 이미 다른 엔지니어링 도구가 제공하는 기능이다.[R3][R4] 이 제품의 차별은 그 계산 자체보다 불완전한 초기 데이터, 고객 요구조건, 구성 변경, 근거 수준을 동일한 판단 과정에 연결하는 데 둔다.

### 1.2 제품을 재정의하는 네 가지 결정

1. 배터리 상세 모델은 나중에 만들되 **DC 단자 전압·방전/충전 전력·평균 전류 제한은 첫 버전부터** 포함한다.
2. 열망은 나중에 만들되 **권선·자석·소자 온도 조건과 전류 허용치의 유효 조건은 첫 버전부터** 포함한다.
3. 고정 Ld/Lq 모델만으로 전체 traction 영역을 대표하지 않는다. 완성된 engineering MVP는 **상수 파라미터 모델과 비선형 flux-map 모델**을 모두 지원한다. 상수 모델만 완성된 단계는 검산 가능한 alpha다.
4. **전기적 해의 존재, 지정 제어 정책의 정적 달성, 지속시간 적합성, 고객 요구 전체의 만족**을 구분한다.

### 1.3 사용 흐름

요구사항 등록 → 물리량/경계/조건 확인 → 가용 데이터 수준 판정 → 시나리오 생성 → 운전점 및 capability 계산 → 조건·제약·모델 범위 확인 → 불확실성/변경 영향 분석 → 의사결정 기록 발행.

LLM은 초기 MVP의 필수 구성요소가 아니다. 향후 자연어 해석이나 설명 생성에 쓰더라도, 승인된 구조화 입력과 deterministic 계산 결과를 변경하지 않는다.

## 2. Engineering Use Cases와 우선순위

아래 우선순위는 이 제품의 설계 제안이다. 산업 전반 사용 빈도를 측정한 통계가 아니다.

| ID | 질문 | 핵심 산출물 | 우선순위 |
|---|---|---|---|
| UC00 | 고객·모터·인버터 데이터의 단위와 정의가 같은가? | RMS/peak, 상/선간, 기계/전기 속도, 축/전자기 토크, 측정 경계 불일치 | P0 |
| UC01 | 지정 속도·축 토크가 실제로 가능한가? | 실행 가능한 id/iq, 전압·전류·DC 전력 여유 또는 불가능 근거 | P0 |
| UC02 | 요구 토크–속도 영역 전체를 커버하는가? | 조건별 ±torque capability, 위반 구간, 최악 조건, 미확인 구간 | P0 |
| UC03 | 이 인버터와 모터가 서로 맞는가? | 전류 여유가 있어도 전압이 부족한 영역, DC 제한과 상전류 제한의 차이 | P0 |
| UC04 | 손실을 포함한 구동·회생 전력이 DC 인터페이스와 맞는가? | signed power balance, 회수 전력, 충전/방전 경계 | P0 |
| UC05 | 부족분을 메우려면 무엇을 얼마나 바꿔야 하는가? | 유효 설계 범위 안의 최소 전압/허용 전류/전력 조건 및 상충관계 | P0, 제한된 역설계 |
| UC06 | 데이터 오차·온도·제어 여유가 결론을 바꾸는가? | margin 범위, 민감도, 결론이 뒤집히는 임계 조건 | P0 |
| UC07 | 현 제어 맵과 물리적 가능 영역 사이에 차이가 있는가? | policy gap, 불필요한 전류, FW 영역, 보정 후보 | P0, 정적 정책만 |
| UC08 | 10초 peak 또는 continuous 요구를 만족하는가? | 검증된 외부 허용 맵의 범위 내 판정; 자료 없으면 UNKNOWN | P0 외부 맵 / P1 열 계산 |
| UC09 | 요구 duty를 반복해도 열적으로 유지되는가? | winding/rotor/junction 온도, 가능한 지속시간·회복시간 | P1 |
| UC10 | DC 전압 sag/rise와 운전점이 상호 정합적인가? | 전원–구동계 결합해, DC 한계 이동 | P1 |
| UC11 | reducer/차량 요구에서 필요한 모터 요구는 무엇인가? | wheel-to-shaft 변환, 등판·최고속·주행 에너지 | P2 |
| UC12 | 특정 transient 또는 보호반응의 전기적 성립 조건은? | 시간 파형·전환 조건·위험 영역 및 추가 검증 범위 | P3, 별도 승인 |

초기에는 주행거리와 완성차 GUI보다 UC00–UC08을 제대로 연결하는 것이 우선이다.

### 초기 제품에서 특히 중요한 system coupling

- 약계자 d축 전류와 토크 q축 전류는 동일한 상전류 예산을 사용한다.
- DC 유효전력과 AC 전류 부담은 동일한 지표가 아니다. 낮은 유효전력도 큰 전류·손실을 동반할 수 있다.
- 같은 속도·토크라도 DC 전압이 달라지면 필요한 id/iq와 손실이 함께 달라진다.
- 자석 자속 변화는 저속 토크와 고속 전압 여유에 서로 다른 영향을 줄 수 있다.
- 회생 시 기계 제동 토크, 실제 DC 회수 전력, 배터리 충전 허용치는 서로 다른 조건이다.
- PWM 주파수의 영향은 손실뿐 아니라 전기주파수 대비 제어/변조 조건과 연결된다. 본 MVP는 주파수 비율을 보고하되 검증 근거 없는 보편적 임계값을 만들지 않는다.[R3][R4][R7]

## 3. MVP Boundary

### 3.1 반드시 구현할 범위

균형 3상, 정상상태 기본파, wye 및 명시적으로 정규화된 wye-equivalent, 단일 2-level VSI, 선형 SVPWM을 기본 범위로 한다. 상수 자속·인덕턴스 모델과 유효영역이 정의된 2D 비선형 dq 자속 맵을 지원한다. 온도는 첫 버전에서 주어진 조건이다. 비선형 맵의 여러 온도 plane을 지원할 수 있으나 온도 사이 보간은 근거가 있을 때만 허용한다.

다음 behavior가 하나의 end-to-end 흐름으로 연결되어야 한다.

- 전진·후진의 구동/제동 운전점 평가, 토크 지령의 정적 해, ±capability.
- 전압·기본파 전류·DC 평균 전류/전력·명시적 운전영역 제약의 동시 평가.
- minimum-current 정책과 공급된 정적 current-policy 평가.
- 손실 및 전력 수지, 손실 불확실성의 구동/회생 전파.
- 조건 비교, 제한적 역설계, 설계 margin과 제약 완화 영향.
- 입력 출처·모델 범위·수치 오차·판정 증거 보존.
- 검증된 외부 peak/continuous 허용 맵을 같은 조건에서 조회하는 기능.

### 3.2 의도적으로 제외할 범위

전체 PWM 스위칭 파형, 전류 제어기 튜닝/안정성 검증, overmodulation/6-step, dual-three-phase, open-end winding, induction machine, 기생회로/EMC/NVH, 절연/부분방전, 칩 내부 온도 분포, 자석 비가역 감자 계산, rotor 강도 계산, detailed battery electrochemistry, 차량 전체 dynamics, ASC/6SO 고장 전환, ISO 26262 적합성 판정은 MVP non-goals다.

원시 delta 권선 데이터를 자동으로 star-equivalent로 간주하지 않는다. 결선·전류·역기전력 상수의 정의가 확인되지 않으면 입력을 거절하거나 해당 분석을 UNKNOWN으로 남긴다.

### 3.3 “전류 600 A 허용”의 의미

입력은 허용 **기본파 전류 진폭**인지, 전체 RMS인지, 단발 pulse인지 명확해야 한다. 기본파 모델만으로 PWM ripple을 포함한 순간 최대전류, 반도체 SOA, OC overshoot를 승인하지 않는다.

열 모델 없이도 온도가 고정된 electrical capability는 계산할 수 있다. 그것을 “10초 peak” 또는 “continuous”라고 바꿔 부를 수는 없다. 외부 허용 맵도 지속시간·초기상태·냉각·PWM 조건 등이 일치할 때만 사용한다.

## 4. Modeling Strategy

### 4.1 입력 데이터에 따라 달라지는 모델 수준

| 수준 | 가용 자료 | 허용 분석 | 허용하지 않는 주장 |
|---|---|---|---|
| D0 | nameplate, 일부 T–n/효율 점 | 요구 간 필수조건 검사, 알려진 영역 대조, 누락 데이터 식별 | 새로운 전압/온도에서의 detailed matching |
| D1 | p, Rs, Ld, Lq, PM flux 및 유효 조건 | 상수 모델의 정적 dq 계산, 예비 설계 비교 | 포화/교차포화 영역 전체의 실기 정합성 |
| D2 | dq flux maps + 손실/허용영역 자료 | 비선형 정적 capability와 matching | 시간응답, 실제 고장 전환, 실측 없는 열 지속시간 |
| D3 | 독립 참조와 검증된 동적/열 모델 | 승인된 범위의 duty/thermal/transient 분석 | 검증 범위를 벗어난 보편적 승인 |

D1→D2는 모델 fidelity 변화다. “측정됨/공급사 자료/FEA/추정/synthetic”은 별도의 데이터 출처 축이다. D2 맵이 있다는 이유만으로 실기 검증 완료라고 표시하지 않는다.[R2][R5][R8]

### 4.2 고정 convention

내부는 SI 단위이며, 속도는 기계 rad/s와 전기 rad/s를 구분한다. p는 극쌍 수다. d축은 PM flux 방향, q축은 아래 토크 부호를 만드는 amplitude-invariant Park convention이다. dq 전류·전압은 기본파 상 peak 기준이다.

\[
\omega_m=2\pi n/60,\qquad \omega_e=p\omega_m
\]
\[
I_{pk}=\sqrt{i_d^2+i_q^2},\quad I_{phase,rms}=I_{pk}/\sqrt2
\]
\[
V_{phase,pk}=\sqrt{v_d^2+v_q^2},\quad V_{LL,rms}=\sqrt{3/2}\,V_{phase,pk}
\]

RMS 변환은 균형 정현파의 전기주기 RMS다. 정지에서는 dq 전류가 실제 각 상의 DC 전류로 분배되므로, 시간 RMS 및 개별 소자 열부하를 이 값으로 단정하지 않는다. 정지 시 출력에는 equivalent sinusoidal RMS임을 명시한다. 전체 동손의 dq 식은 균형·영순분 없음 조건에서 그대로 성립한다.

Pdc > 0은 DC 전원에서 inverter로 유입, Pac > 0은 inverter에서 motor로 유입, Pshaft > 0은 motor에서 외부 축으로 전달이다. 역회전 motoring은 ω<0, Tshaft<0이면서 Pshaft>0이다.[R1][R3]

### 4.3 motor의 수학적 계약

정상상태 기본파에서:

\[
v_d=R_s i_d-\omega_e\psi_q,\qquad
v_q=R_s i_q+\omega_e\psi_d
\]
\[
T_{em}=\frac32p(\psi_d i_q-\psi_q i_d)
\]

상수 모델:

\[
\psi_d=\psi_{PM}+L_d i_d,\qquad \psi_q=L_q i_q
\]

비선형 모델:

\[
(\psi_d,\psi_q)=\Psi(i_d,i_q,T_{PM})
\]

Rs는 권선 온도 조건에서 정해진다. PM 온도와 권선 온도를 같은 온도로 자동 치환하지 않는다. 선형 온도 계수는 자료와 유효 범위가 있을 때만 사용한다.[R1][R2]

비선형 맵의 토크는 위 flux 식으로 계산한다. 별도 torque map은 비교 검증용이다. 별도 맵이 shaft torque인지 electromagnetic torque인지 확인하지 않은 채 혼합하지 않는다. 맵의 hole, 유효영역 밖, 알려지지 않은 사분면, 확인되지 않은 온도 범위는 외삽하지 않는다.

보존적 자기 모델로 해석하는 맵은 교차 미분의 상호성 및 differential-inductance matrix의 타당성을 점검한다. 원시 데이터 노이즈/보간 오차에 대한 허용치는 명시한다. 이 점검만으로 실제 포화 모델이 검증되는 것은 아니다.

### 4.4 손실·shaft torque의 일관된 처리

MVP 기본 closure는 기본파 dq 자기 모델 뒤에 손실 토크를 연결하는 reduced-order 방식이다. 회전 관련 등가 손실 토크 τrot는 회전 방향을 방해하며:

\[
T_{shaft}=T_{em}-\tau_{rot},\qquad
P_{rot}=\omega_m\tau_{rot}\ge0
\]
\[
P_{cu}=\frac32R_s(i_d^2+i_q^2),\qquad
P_{ac}=\frac32(v_di_d+v_qi_q)
\]
\[
P_{ac}=T_{em}\omega_m+P_{cu}
=P_{shaft}+P_{cu}+P_{rot}
\]
\[
P_{dc}=P_{ac}+P_{inv}
\]

P_rot에 철손을 포함하는 경우 이것은 **loss-equivalent resisting-torque approximation**이다. 철손 전류의 dq 분해를 직접 재현하는 고충실도 모델은 아니다. 이러한 토크 차감 방식은 기존 모델링 도구에서도 쓰이지만,[R2] 사용하려는 고속·약계자 영역의 전압/전류 예측 오차는 독립적으로 검증해야 한다.

회전 손실을 토크에서 이미 차감하고 Pac에 또 더하면 이중계상이다. 전체 motor loss map이 이미 copper를 포함한다면 Pcu도 재가산하지 않는다.

P_rot/ω를 ω=0에서 계산하지 않는다. τrot 자체 또는 명시적 영속도 limit가 필요하다. 이 closure로 표현할 수 없는 정지 중 고조파 철손/전기 손실은 별도 전기 손실 모델이 필요하며, 임의의 큰 drag torque로 바꾸지 않는다. 본 MVP의 기본파 모드는 그 현상을 제외했다고 표시한다.

inverter loss는 검증된 total-loss map, 유효 조건의 parameterized surrogate, 또는 손실 범위를 입력받는다. 특정 Eon/Eoff 한 점에 fsw와 6을 곱하여 모든 운전점을 대표하지 않는다. 실제 switching energy는 데이터시트 시험 조건과 동작 조건의 차이를 확인해야 한다.[R6]

HV 이외의 LV 공급으로 소비되는 제어/게이트 전력을 HV Pdc에 자동 합산하지 않는다. 전력 경계는 필요 시 Pdc + Plv = Pshaft + Ploss로 확장한다.

### 4.5 inverter voltage model

기본 linear SVPWM의 이상적인 기본파 상전압 peak 한계는 Vdc/√3이다.[R3] 모터 저항 전압강하는 이미 vd/vq에 포함한다.

사용 가능한 command budget과 실제 하드웨어 한계를 분리한다.

\[
\left\|\mathbf v_{motor}+\Delta\mathbf v_{inv}\right\|
\le (1-r_v)\frac{V_{dc}}{\sqrt3}
\]

r_v는 명시적인 제어/설계 reserve다. Δv_inv는 자료가 있을 때 사용하는 inverter voltage-error/drop model이다. 동일한 저항·deadtime 효과를 Δv_inv와 reserve에서 중복 차감하지 않는다. 데이터가 없으면 Δv_inv=0인 optimistic screening인지, 합의된 terminal-voltage envelope인지 구분한다.

0 V의 잔여 command margin은 FW 최소전류 해에서 정상적으로 나타날 수 있다. 이미 차감한 reserve까지 0이라고 표현하지 않는다. 동적 전압 headroom 및 제어 안정성은 정적 식만으로 증명하지 않는다.

### 4.6 DC 경계

\[
I_{dc,avg}=P_{dc}/V_{dc},\qquad V_{dc}>0
\]
\[
-P_{chg,max}\le P_{dc}\le P_{dis,max}
\]
\[
-I_{chg,max}\le I_{dc,avg}\le I_{dis,max}
\]

이는 DC-link capacitor의 ripple RMS 전류나 케이블 transient peak를 뜻하지 않는다.

회생의 효율은 Pshaft<0과 Pdc<0을 모두 만족할 때 |Pdc|/|Pshaft|다. Pshaft<0, Pdc≥0이면 기계적으로 제동 중이어도 DC 에너지 회수 운전은 아니다. 정지/영전력에서는 효율을 N/A로 보고한다.

## 5. Core Engineering Architecture

### 5.1 물리 구조

DC boundary ↔ inverter ↔ motor ↔ shaft boundary. Thermal boundary는 winding/rotor/junction 등 필요한 곳에 조건 또는 모델로 접속한다.

첫 버전에서는 속도·DC 전압·온도를 scenario가 제공한다. 따라서 주어진 경계에서 coupled algebraic problem을 푼다. 미래에 battery source model이 들어오면 Vdc와 Idc를 함께 풀고, thermal model이 들어오면 loss–temperature feedback을 함께 푼다. 모터 계산 뒤 전원 한계를 사후 clipping하는 방식으로 확장하지 않는다.

### 5.2 최소한의 engineering objects

| 개념 | 책임 |
|---|---|
| Asset/Model Definition | 물리 모델, parameters/maps, validity domain, evidence |
| Requirement | 무엇을 어느 포트·조건·지속시간에 만족해야 하는지 |
| Scenario | 운전점 또는 영역, 환경, 경계, 불확실성, 제어 정책 |
| Operating Solution | 실제 후보 id/iq, 전압, 토크, signed powers, 잔차 |
| Constraint Evidence | limit/slack, 원인 분류, 적용 범위, 증명 또는 탐색 상태 |
| Decision Record | 요구–시나리오–결과 연결, 결론, 다음 조치, 구성 식별자 |

구체적인 Python class 수, 디렉터리 구조, UI framework, DB는 이 문서가 지정하지 않는다.

### 5.3 확장을 지탱하는 abstraction

핵심은 특정 컴포넌트 이름보다 **에너지 포트, 조건과 상태의 구분, 허용영역, 손실 경계, 시간적 의미, 모델 유효영역**이다.

Reducer가 추가되면 shaft torque/speed/power와 손실을 연결한다. 구동과 회생에 동일 방향 효율식을 무조건 적용하지 않는다. Vehicle은 wheel boundary를 요구 조건으로 바꾸고, battery는 DC boundary를 전류 의존 전압 모델로 바꾼다. 이때 requirement의 측정 포트와 부호는 유지한다.

## 6. Input / Output Model

### 6.1 입력 부족을 판단하는 방법

| 분석 | 반드시 필요한 입력 | 있으면 개선 | 추정 허용 | 없으면 불가능한 결론 |
|---|---|---|---|---|
| electrical point | 속도/토크 정의, p, flux 관계, Rs, Vdc, current/voltage/domain limits | 포화/온도 맵, voltage-error 모델 | 유효 범위가 선언된 상수 모델·bound | 범위 밖 실기 적합성 |
| shaft/DC power | 위 입력 + motor/inverter loss 정의·경계 | 사분면별 측정 맵 | nonnegative loss interval | 정확 효율·좁은 power margin |
| high-speed matching | electrical point 입력 + speed/domain/d축 제한 | cold PM data, 제어 허용 맵 | PM/voltage bounds | rotor/감자/제어 안정성 승인 |
| continuous/peak | 유효 조건이 일치하는 허용 맵 또는 검증된 열 모델 | duty·냉각·초기 온도·history | screening envelope만 | 지속시간 만족 |
| robustness | input bounds와 상관/물리적 결합 | 검증된 분포·model discrepancy | 구간·유효 조합 시나리오 | 근거 없는 확률 보장 |

각 값/맵에는 단위, 정의, 참조 온도·전압·주파수, provenance, revision, 유효 영역, uncertainty를 보존한다. 같은 데이터에서 파생된 값의 불확실성을 독립으로 중복 처리하지 않는다.

중요한 누락값을 조용히 0 또는 효율 95%로 채우지 않는다. Zero loss는 명시적인 analytic fixture 또는 optimistic bound일 때만 허용한다.

### 6.2 Requirement의 의미

Requirement는 최소한 ID/revision, 원문, 물리량, 포트, 비교 연산자, 목표/허용오차, 적용 조건, 조건의 quantifier, 시간/초기상태, 제외 조건을 가진다.

예: “REQ-TS-012: inverter DC terminal 600 V, motor shaft 12,000 rpm에서 shaft torque 150 N·m를 유지한다.” 여기서 ‘유지’의 지속시간이 없다면 정적 항목으로만 해석하고, 지속시간 요구는 미정으로 남긴다. 이를 자동으로 continuous로 읽지 않는다.

Scenario는 requirement를 변경하지 않는다. 공급 전압의 범위가 명시되면 대표 전압 한 점만 검사하고 전체를 만족했다고 하지 않는다.

### 6.3 의사결정에 필요한 결과

기본 화면은 판정 범위·결론, 요구 대비 margin, 문제 operating region, 제한 요인, 필요한 변경/추가자료를 우선한다. 상세값은 그 근거로 제공한다.

필수 수치: requested/achieved shaft torque, electromagnetic torque, id/iq, fundamental Ipeak/equivalent RMS, required motor/command voltage, all voltage budgets, signed shaft/AC/DC power, loss breakdown, Idc average, 모든 적용 제약의 여유, model-domain 거리/coverage, numerical residual/error estimate.

시각화는 요구 envelope를 덧씌운 ±T–n 영역, 선택점의 id–iq 제약 지도, DC power/loss balance, parameter–margin 변화, validation/data coverage map을 우선한다. 데이터 없는 영역은 매끈하게 보간해 숨기지 않는다.

## 7. Feasibility & Constraint Strategy

### 7.1 문제의 정의

주어진 scenario s, model parameters θ, controls x에 대해:

\[
\mathcal F(s,\theta)=\{x: h(x,s,\theta)=0,\ g_j(x,s,\theta)\le0,\ x\in D_{valid}\}
\]

토크 일치/요구 band, voltage/current/DC limits, explicit operating-domain constraints를 동시에 적용한다. 메커니즘별 envelope를 독립 계산한 후 min으로 합치거나, MTPA를 계산한 후 id/iq만 clip하지 않는다.

Capability는 모델 범위 내에서 최대/최소 가능한 shaft torque다. 보수적인 feasible witness에서 얻는 lower bound와 상한 추정·증명에서 얻는 upper bound를 구분한다. 부분 map의 바깥을 물리적 금지영역으로 착각하지 않는다.

### 7.2 두 종류의 current selection

**Existence/capability query:** 허용된 control domain 안에서 요구를 만족하는 해가 있는지와 그 범위를 찾는다.

**Operating-policy query:** 기본 정책은 전압·전류·운전영역과 shaft torque를 만족하는 해 중 I² 최소다. 그 후 DC 수지와 source limits를 평가한다. 공급된 current map은 별도 정책으로 평가한다. Loss optimum은 loss data가 충분한 후 별도 정책으로 추가한다.

최소전류 정책의 실패가 모든 물리 해의 부재를 뜻하지 않는다. 반대로 임의의 해가 있다는 이유로 실제 정책이 그 해에 도달한다고 하지 않는다. 특히 charging cap을 맞추기 위해 불필요한 전류를 흘려 손실을 늘린 해는, energy-recovering regen 정책의 성공으로 인정하지 않는다. 능동 손실 증가 운전은 MVP non-goal이다.

### 7.3 판정 상태와 증거

- FEASIBLE: 적용 모델·조건·정책·범위에서 확인된 해가 있고 해당 판정에 필요한 제약이 모두 평가됐다.
- INFEASIBLE: 알려진 hard bound, 필수조건, 또는 신뢰 가능한 capability bound가 요구와 양립할 수 없음을 보인다.
- UNKNOWN: 입력/모델/시간조건/수치 탐색의 부족으로 위 두 결론을 정할 수 없다.

상태와 별도로 evidence를 저장한다: analytic, numerical witness, bounded/global search evidence, sampled coverage, supplier-rated envelope, empirically validated domain 등. 모델 데이터가 잘못됐으면 INVALID_INPUT; solver 내부 문제가 있으면 NUMERICAL_UNRESOLVED를 원인으로 기록한다. 이들은 물리적 FAIL이 아니다.

Nominal feasible, assumed-data conditional, verified robust 등의 qualifier는 판정 범위를 설명하며 임의의 0–100 신뢰점수를 만들지 않는다.

### 7.4 불확실성과 quantifier

설계 변수는 scenario마다 임의 변경할 수 없다. 재조정 가능한 control과 고정 hardware를 구분한다.

이상적으로 조건을 모두 안다는 adaptive feasibility는 ∀u∈U ∃x∈F(u)다. 실제 고정 calibration/센서 정보로 가능한지는 π(y(u))라는 정책을 모든 u에 대해 평가해야 한다. 각 코너를 서로 다른 최적 control로 통과시키는 것은 실제 calibration robustness의 증명이 아니다.

모든 불확실성 조합을 평가했다는 근거가 없으면 “검사한 시나리오에서 feasible”라고 보고한다. Monte Carlo pass 비율이나 일부 코너 검사로 worst-case PASS를 주장하지 않는다. 분포가 없는 입력 범위에 임의의 확률분포를 붙이지 않는다.

실제 파라미터가 아직 알려지지 않은 단일 시스템의 결과가 pass/fail 양쪽에 걸치면 그 실제 결과는 UNKNOWN이다. 반면 요구가 ‘허용 불확실성 집합의 모든 경우에서 만족’이면, 실제로 허용 가능한 한 조건의 확실한 위반만으로 robust 요구는 INFEASIBLE이다. 단순 보수적 외포락의 끝점이 물리적으로 실현 가능한지 모르면 그 끝점만으로 robust FAIL을 증명할 수는 없다.

손실 L∈[Lmin,Lmax]이고 Pdc=Pshaft+L일 때, 구동 source cap에는 큰 손실이 불리하다. 회생 charging cap에는 작은 손실이 더 불리할 수 있다. 회생에도 큰 손실만 적용하면 잘못된 낙관 판정이 생긴다.

### 7.5 margin과 dominant constraint

Upper limit의 native margin은 limit−demand, lower limit은 demand−limit다. normalization은 단위 비교용이며 실제 A/V/N·m/W margin을 항상 유지한다. 숫자 허용오차, 설계 reserve, 입력 uncertainty는 서로 별개다.

‘현재 해에서 active한 제약’, ‘capability를 제한하는 제약’, ‘변경 가치가 큰 design parameter’는 같은 개념이 아니다. 기본 출력은 이 셋을 구분한다.

Dominance는 한 제약을 조금 완화하고 **전체 결합문제를 다시 풀어** capability 또는 요구 margin이 얼마나 바뀌는지로 평가한다. 효과가 두 제약을 함께 완화해야 나타나면 공동 제한으로 보고한다. 선택한 정규화와 perturbation 크기를 저장한다.

실현 불가능한 단일 제약 완화는 원인 진단용이다. 실제 설계 제안은 전압 정격·손실·냉각·제어 등 함께 변하는 항목을 연결해서 재계산한다. Ld, Lq, ψPM은 자유로운 독립 설계 knob라고 단정하지 않는다.

### 7.6 수치 전략

문제 스케일링, 명시적 bounded domain, boundary/branch 확인, 복수 후보 검사, 조건 변화에 대한 refinement를 사용한다. 알고리즘 선택은 구현자에게 맡기되, local solver success를 global optimum으로, local solver failure를 infeasible로 치환하지 않는다.

특히 linear IPMSM의 synthetic benchmark는 torque equation으로 iq를 제거한 1D 문제와 polynomial boundary roots를 이용해 독립 비교할 수 있다. 비선형 맵은 hole/분리된 영역/보간 경계를 고려한다. unresolved 영역은 UNKNOWN이다.

## 8. Validation Strategy

Verification는 정한 모델을 올바르게 계산했는지 확인한다. Validation는 그 모델이 의도된 사용 범위에서 실제 시스템 또는 타당한 독립 참조를 충분히 재현하는지 확인한다. 외부 simulator와 동일 식으로 일치하거나 synthetic case를 통과한 것만으로 실기 검증 완료라고 하지 않는다. 이 구분과 validation domain의 기록은 NASA의 M&S 지침과도 일치한다.[R8]

| 단계 | 검증 내용 | 종료 조건 |
|---|---|---|
| V0 | 단위·부호·변환, 입력 schema, invalid cases | 잘못된 입력이 조용히 계산되지 않음 |
| V1 | 해석식, 직접 운전점, conservation | 기준값·항등식 일치 |
| V2 | inverse solve, capability, 경계/분기 | 독립 기준과 일치; 오분류 없음 |
| V3 | 비선형 manufactured map 및 refinement | 맵 축/단위/토크/보간 오차 확인 |
| V4 | 독립 모델 비교 | 동일 조건/경계로 변환한 출력과 오차 분석 |
| V5 | 독립 공개/공급사/시험 데이터 | intended-use별 error budget 및 검증 영역 승인 |
| V6 | 변경 회귀 검증 | 이전 승인 영역의 변화와 근거 추적 |

### 제안 acceptance criteria

- 직접 algebraic fixture: normalized discrepancy ≤ 1e-10.
- 정적 전력 항등식: |residual| ≤ max(0.01 W, 1e-9 × power scale).
- inverse fixture: torque residual ≤ max(0.001 N·m, 1e-6 × declared torque scale), normalized hard-constraint violation ≤ 1e-7.
- 첨부 golden inverse 수치: id/iq/voltage 오차 각각 0.001 A / 0.001 A / 0.001 V 이내, power 0.5 W 이내. 제공된 model과 policy가 같은 경우에 한한다.
- capability benchmark: bound gap ≤ max(0.1 N·m, 0.0005 × declared torque scale). 경계 요구가 오차 구간과 겹치면 UNKNOWN/boundary-qualified다.
- 맵 보간 오차는 analytic solver 오차와 분리한다. 노드 일치, 중간점 refinement, 맵 범위 오류 처리를 검증한다.
- 물리적 validation 정확도에 범용 ‘1%’를 선언하지 않는다. 판단하려는 margin과 측정 오차에 맞춘 출력별 error budget을 사전 정의한다.

검증 데이터의 계산값은 immutable reference로 관리한다. 앱 자체가 생성한 결과로 expected data를 자동 갱신하지 않는다. 두 LLM이 같은 식을 복사해 같은 결과를 얻은 것은 독립적인 공학 validation이 아니다.

## 9. Reference Engineering Case

구체적인 수학 정의와 기준 결과는 `03_Reference_Cases_KO.md` 및 JSON 파일들에 제공한다. 다음 값들은 특정 OEM/공급사 제품을 재현하지 않는 synthetic fixture다.

p=4; ψPM=0.100 Wb; Ld=0.200 mH; Lq=0.400 mH; Rs=0.015 Ω; Ipk,max=600 A; 명시적 synthetic 운전영역 −500≤id≤0 A; |n|≤16,000 rpm. Vdc=600 V nominal, 450 V stress. r_v=0.05, Δv_inv=0. Pdis,max=200 kW, Pchg,max=100 kW, Idis,max=400 A, Ichg,max=200 A. τrot=0.002ωm, Pinv=200+0.008Ipk² W. 파라미터 온도 의존성, 지속시간 rating, 실제 냉각 성능은 정의하지 않는다. PWM은 20 kHz context이며 loss surrogate로 PWM sweep 정확도를 주장하지 않는다.

| n / 요구 shaft torque / Vdc | 최소전류 정책 결과 | Pdc | 판정 |
|---|---|---|---|
| 3,000 rpm / +300 N·m / 600 V | id≈−190.575, iq≈362.776 A | 99.767 kW | 정적 모델 내 feasible |
| 6,000 rpm / +300 N·m / 600 V | id≈−315.218, iq≈307.951 A | 195.408 kW | voltage budget active |
| 12,000 rpm / +150 N·m / 600 V | id≈−367.676, iq≈146.477 A | 196.631 kW | static feasible; DC margin 3.369 kW |
| 12,000 rpm / +150 N·m / 450 V | 전압 제약을 만족하는 해 없음 | 미산출 | 전기적으로 infeasible in stated domain |
| 6,000 rpm / +350 N·m / 600 V | 전기적 해는 존재 | 229.376 kW | DC 방전 제한 위반 |
| 12,000 rpm / −100 N·m / 600 V | 전기적 해는 존재 | −120.044 kW | DC 충전 제한 위반 |
| 12,000 rpm / −80 N·m / 600 V | id≈−222.319, iq≈−89.396 A | −95.421 kW | energy-recovering regen feasible |

+150 N·m, 12,000 rpm, 600 V에서 minimum-current policy의 source-limited capability는 약 152.555 N·m이다. 전류 여유가 약 204 A라고 해서 torque margin이 큰 것이 아니다. 요구 대비 capability margin은 약 2.555 N·m이다.

같은 조건에 10초 유지 요구를 추가하면, 전기적 결과는 그대로여도 지속시간 판정은 UNKNOWN이다.

역방향·정지·회전/인버터 손실을 제외한 SPMSM·cross-saturation manufactured map·loss-interval 회생 판정·의도적 데이터 오류 사례가 별도 reference contract에 포함된다.

## 10. Development Roadmap

| 단계 | capability | 다음 단계 진입 조건 |
|---|---|---|
| A — Physics alpha | conventions, 상수 motor, forward/inverse, DC 수지, synthetic cases | V0–V2와 error behavior 통과 |
| B — Engineering MVP | nonlinear maps, policy/physical 구분, uncertainty, 역설계, 외부 rating 맵, decision record | V3–V4, 대표 요구에 end-to-end 판정 |
| C — Electrothermal | 검증된 lumped thermal, duration/duty/recovery, temperature feedback | 초기 상태·냉각·loss allocation 검증 |
| D — DC source coupling | Vdc–Idc 관계, SOC/T source limits, sag/rise | 해 존재/다중해/안정성 범위와 power balance 확인 |
| E — Reducer/vehicle | ratio/loss, longitudinal demand, selected duty energy | 포트 부호·구동/회생 손실·cycle 조건 검증 |
| F — Selected transient | flux derivatives, inertia, current control/measurement delay | steady-state cross-check 및 동적 reference 통과 |
| G — Selected protection | 정의된 fault와 reaction의 제한된 feasibility | topology/initial states/fault assumptions 및 독립 V&V 계획 승인 |

C와 D의 순서는 실제 미해결 요구의 빈도에 따라 교환 가능하다. 안전·보호 기능은 단순히 후속 메뉴를 추가하는 작업이 아니라 intended use와 V&V 범위가 달라지는 별도 모델 capability다.

## 11. 제품의 성공을 확인하는 지표

기능 수·그래프 수·계산 속도를 주 지표로 두지 않는다. 대표 고객 요구에서 재사용 가능한 판단을 얻는지, 조건 변경이 어떤 결론을 바꿨는지 설명되는지, 부족한 데이터가 무엇인지 구체화되는지, 독립 검토자가 동일 입력으로 동일 판정을 재현하는지를 본다.

MVP 종료 시 최소 세 종류의 실제 업무형 질문을 처리해야 한다: inverter–motor matching, 저전압 고속 토크, 회생/DC 한계. 최소 한 사례는 “부품을 키우는 것보다 경계 조건/정책/자료 확보가 더 중요하다”는 결론을 근거와 함께 낼 수 있어야 한다. 이는 미리 정한 답을 강요하는 것이 아니라 잘못된 단일 원인 해석을 막기 위한 품질 기준이다.

## 12. 참고 자료

아래 문서는 물리식/기존 모델링 방식/검증 원칙의 참고다. 본 제품의 우선순위와 acceptance 수치는 별도의 설계 제안이다.

- [R1] MathWorks, PMSM — Permanent magnet synchronous motor with sinusoidal flux distribution. `https://www.mathworks.com/help/simscape-electrical/ref/pmsm.html`
- [R2] MathWorks, FEM-Parameterized PMSM — flux linkage, nonlinear parameterization, losses, temperature dependence. `https://www.mathworks.com/help/simscape-electrical/ref/femparameterizedpmsm.html`
- [R3] MathWorks, PMSM Constraint Curves and Their Application. `https://www.mathworks.com/help/mcb/gs/pmsm-constraint-curves-and-their-application.html`
- [R4] MathWorks, LUT based PMSM Control Reference. `https://www.mathworks.com/help/mcb/ref/lutbasedpmsmcontrolreference.html`
- [R5] MathWorks, Field-Weakening Control (with MTPA) of Nonlinear PMSM Using Lookup Table. `https://it.mathworks.com/help/mcb/gs/fwc-with-mtpa-of-non-linear-pmsm-using-lut.html`
- [R6] Infineon, Estimating SiC MOSFET switching losses in applications. `https://community.infineon.com/t5/Knowledge-Base-Articles/Estimating-SiC-MOSFET-switching-losses-in-applications/ta-p/709113`
- [R7] Plexim, Three-Phase Voltage Source Inverter demo model documentation. 이 문서는 PWM/샘플링 fidelity 참고이며 traction motor 실기 validation 자료가 아니다. `https://www.plexim.com/sites/default/files/demo_models_categorized/plecs/three_phase_voltage_source_inverter.pdf`
- [R8] NASA-STD-7009B, Standard for Models and Simulations, 2024, 특히 §4.2.3–4.2.7 및 §4.3.8. 자동차 규정으로 적용하는 것이 아니라 M&S credibility 원칙을 참고한다. `https://standards.nasa.gov/sites/default/files/standards/NASA/B/1/NASA-STD-7009B-Final-3-5-2024.pdf`
