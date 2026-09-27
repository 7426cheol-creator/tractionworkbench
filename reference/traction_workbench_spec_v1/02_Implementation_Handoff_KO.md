# Implementation Handoff — Traction Engineering Workbench
## 구현 담당: Claude Opus 5.5 / 기준선 v1.0

이 문서는 구현되어야 할 engineering behavior와 검증 계약이다. 특정 Python class 구조, UI framework, solver library, test 디렉터리 구조를 요구하지 않는다. 기존 코드베이스가 있다면 먼저 확인하고, 이 계약을 보존하는 최소한의 구조로 구현한다. 프로젝트와 관계없는 저장소 또는 서비스는 이 문서만으로 수정 권한이 주어진 것으로 해석하지 않는다.

`01_Engineering_Blueprint_KO.md`는 제품 의도, `03_Reference_Cases_KO.md`와 JSON은 독립적인 검증 fixture다. 본 문서와 수치 fixture가 충돌하면 조용히 expected 값을 바꾸지 말고 물리식·경계·단위부터 확인한다.

## H1. 성공 행동

입력한 shaft-torque/speed 요구에 대해, 해당 조건에서 사용할 수 있는 motor/inverter/DC-boundary 모델을 선택하고 다음을 반환한다.

1. 실제 계산된 운전점 또는 충분한 불가능 근거, 아니면 판단 불가의 이유.
2. voltage/current/DC power/domain/rating 조건의 여유와 제한 요인.
3. 적용 모델, 입력 출처, 가정, 미평가 영역, 수치·데이터 불확실성.
4. 요구사항을 그대로 보존한 채 가능한 개선 방향 또는 필요한 자료.

단순히 최적화 solver의 success flag를 engineering conclusion으로 반환하는 것은 실패다. 입력 부족을 0으로 메우거나 요청 토크를 내부적으로 줄인 뒤 원 요구가 만족됐다고 표시하는 것도 실패다.

## H2. MVP scope

균형 3상, zero sequence 없음, 단일 2-level inverter, wye 또는 확인된 wye-equivalent, 정상상태 기본파, linear SVPWM, PMSM/SPMSM/IPMSM을 대상으로 한다.

필수 모델은 (a) constant-parameter dq motor, (b) valid-domain이 있는 nonlinear dq flux map이다. 초기 alpha는 (a)만으로 진행할 수 있지만, engineering MVP 완료는 (b)까지 요구한다.

온도와 Vdc, 속도는 주어진 scenario 경계다. thermal dynamics와 battery electrochemistry는 만들지 않는다. 검증된 외부 continuous/finite-duration rating envelope를 조건 일치 시 조회하는 기능은 MVP에 포함한다.

필수 query는 forward evaluation, requested-shaft-torque solve, positive/negative capability, stated operating-policy evaluation, scenario comparison, bounded one-parameter inverse sizing, bounded-input analysis, traceable decision record다.

## H3. conventions와 단위

- 내부 SI. 모든 출력에서 mechanical/electrical speed, phase/line-line, peak/RMS를 명확히 구분한다.
- p는 pole pairs. ωm=2πn/60, ωe=pωm.
- PM flux와 정렬된 d축, 아래 식과 일치하는 q축 및 amplitude-invariant Park convention.
- dq current/voltage는 기본파 phase peak 기준.
- Ipk²=id²+iq². 회전하는 균형 정현파의 Iphase,rms=Ipk/√2.
- Vphase,pk²=vd²+vq². VLL,rms=√(3/2)Vphase,pk.
- Vdc는 inverter DC terminal voltage이며 strictly positive.
- Pdc>0: DC source→inverter. Pac>0: inverter→motor. Pshaft>0: motor→external shaft.
- Tshaft·ωm=Pshaft. 음속도·음토크의 곱은 positive motoring이다.
- Rs는 per-phase resistance다. line-to-line measurement를 그대로 per-phase로 쓰지 않는다.

정지에서는 equivalent sinusoidal RMS와 실제 각 상의 시간 RMS가 다르다. θe가 없으면 개별 phase current/semiconductor heat를 정확히 결정할 수 없다. 전체 copper-loss identity와 conservative fundamental amplitude envelope는 유지하되, stall thermal capability를 추론하지 않는다.

입력 Ke/Kt의 convention이 모호하면 flux로 자동 변환하지 않는다. raw delta 자료도 자동 변환하지 않는다. 변환을 지원할 경우 원 정의와 변환 경로를 출력에 남긴다.

## H4. mathematical / physical contract

### H4.1 motor electrical contract

주어진 id, iq, ωm와 온도 조건에서:

\[
(v_d,v_q)=(R_s i_d-\omega_e\psi_q,\ R_s i_q+\omega_e\psi_d)
\]
\[
T_{em}=1.5p(\psi_d i_q-\psi_q i_d)
\]

constant model:

\[
\psi_d=\psi_{PM}+L_di_d,\quad \psi_q=L_qi_q
\]

nonlinear model:

\[
(\psi_d,\psi_q)=\Psi(i_d,i_q,T_{PM})
\]

반드시 Rs를 포함한 terminal voltage를 사용한다. Rs=0은 명시적인 analytic test case에서 허용한다. 전압식에 포함한 RsI를 voltage limit에서 다시 빼지 않는다.

Rs≥0, Ld>0, Lq>0이며 p는 양의 정수다. IPMSM이라고 무조건 Lq>Ld를 강제하지 않는다. SPMSM을 Ld=Lq인 case로 지원한다. flux-map 모델은 상수 Ld/Lq를 필수 입력으로 요구하지 않는다.

### H4.2 nonlinear data contract

맵 축은 id, iq, optional temperature이며 axis labels, units, source, revision, validity mask를 가진다. 최소 지원 형식은 두 개의 2D flux 배열과 명시적 축이다. 형식의 구체적 serialization은 구현자가 결정할 수 있다.

맵 바깥, hole, 알려지지 않은 사분면, 검증되지 않은 온도로 extrapolate하지 않는다. symmetry를 사용하려면 그 symmetry가 데이터 계약에 선언되어야 한다.

평균 electromagnetic torque는 flux 식에서 유도한다. 별도 torque map을 함께 쓸 경우 정의 일치/오차 비교용으로 취급한다. 비보존적인 모순을 조용히 평균내지 않는다.

보존적 magnetic map이라고 선언된 경우 교차 미분 reciprocity 및 differential inductance의 타당성을 검사한다. 수치 보간/원시 데이터 노이즈의 허용치는 release 문서에 명시한다. 검증된 범위 밖 미분을 만들어 사용하지 않는다. steady-state MVP에서는 dynamic current response를 이 derivative만으로 검증했다고 주장하지 않는다.

### H4.3 rotational loss closure

기본 loss closure는 다음과 같다.

\[
T_{shaft}=T_{em}-\tau_{rot}(i_d,i_q,\omega_m,T),
\quad P_{rot}=\omega_m\tau_{rot}\ge0
\]
\[
P_{cu}=1.5R_s(i_d^2+i_q^2)
\]
\[
P_{ac}=1.5(v_di_d+v_qi_q)=T_{em}\omega_m+P_{cu}
\]
\[
P_{ac}=P_{shaft}+P_{cu}+P_{rot}
\]

τrot는 회전 방향을 방해하는 부호를 가진다. 축 토크 요구를 풀 때 Tshaft를 Tem과 같게 놓지 말고 loss closure를 포함한다.

P_rot에 철손을 포함하면 dq 철손 전류를 직접 푸는 것이 아니라 loss-equivalent torque로 근사하는 방식임을 기록한다. 이 근사의 사용 범위는 독립 참조로 검증해야 한다.

ω=0에서 P_rot/ω를 계산하지 않는다. 손실 입력은 τrot 또는 명시적인 zero-speed limit를 제공해야 한다. 첨부한 τrot=bωm는 정확히 처리한다.

회전 손실로 표현할 수 없는 영속도 전기 손실, PWM harmonic losses, static friction/stiction을 이 closure에 억지로 넣지 않는다. 요구가 이 현상에 의존하면 해당 판단은 UNKNOWN/out-of-scope다.

### H4.4 inverter power / voltage contract

\[
P_{dc}=P_{ac}+P_{inv},\qquad P_{inv}\ge0
\]

P_inv에 사용하는 total-loss map 또는 surrogate의 조건과 범위를 보존한다. 구동·회생에서 손실이 같다고 자동 가정하지 않는다. 첨부 synthetic fixture는 명시적으로 대칭이다.

MVP linear SVPWM:

\[
\|\mathbf v_{motor}+\Delta\mathbf v_{inv}\|
\le(1-r_v)V_{dc}/\sqrt3
\]

0≤r_v<1이다. r_v는 설계/제어 reserve이며 수치오차 허용치가 아니다. Δv_inv=0이면 ideal voltage mapping이라고 기록한다. 자료로 얻은 terminal-voltage envelope를 대신 사용하는 경우 동일한 nonideality를 reserve와 drop에서 중복 차감하지 않는다.

첨부 fixture의 P_inv=200+0.008Ipk²와 Δv_inv=0은 합성 energy-loss surrogate와 ideal command-voltage mapping을 조합한 정의다. 물리 소자의 도통 전압강하·deadtime을 상세 재현하는 모델이 아니다.

별도 LV 전원에서 소비하는 auxiliary power를 Pdc에 혼입하지 않는다. 여러 power port를 사용하면 공급 경계별로 손실을 배분한다.

### H4.5 DC constraints

\[
I_{dc,avg}=P_{dc}/V_{dc}
\]
\[
-P_{chg,max}\le P_{dc}\le P_{dis,max},\quad
-I_{chg,max}\le I_{dc,avg}\le I_{dis,max}
\]

전력과 전류 제한이 모두 있으면 동시에 적용한다. 고정 Vdc에서 방전 전력 상한은 min(Pdis,max,Vdc·Idis,max), 최대 충전 전력의 크기는 min(Pchg,max,Vdc·Ichg,max)다.

이것은 average DC current다. capacitor ripple RMS나 instantaneous peak가 아니다.

### H4.6 energy mode / efficiency

Pshaft>0이고 Pdc>0이면 ηmot=Pshaft/Pdc다. Pshaft<0이고 Pdc<0이면 ηregen=|Pdc|/|Pshaft|다. Pshaft<0이고 Pdc≥0이면 기계적 제동이지만 순 DC 에너지 회수는 없다. 영속도/영전력에서는 efficiency=N/A로 보고하고 손실·전력 값은 유지한다.

효율이 음수이거나 1을 초과하면 clamp로 숨기지 않는다. 정의·부호·loss accounting의 오류 또는 적용 범위 문제를 보고한다. zero 판정의 문턱은 단위를 가진 tolerance로 기록한다.

## H5. Query semantics

### H5.1 Forward evaluation

입력한 id/iq를 그대로 평가한다. 제약 위반이 있어도 운전점을 임의로 이동시키지 않는다. 모델이 계산 가능한 범위 안에 있는 부적합 운전점의 P/V/I를 diagnostic으로 반환할 수는 있지만 feasible witness라고 부르지 않는다.

### H5.2 Physical existence / capability

요구의 operator/band, motor equations, voltage/current/DC constraints, 명시적 physical/allowed control domain을 동시에 만족하는지 평가한다.

최대·최소 torque는 shaft 기준이다. capability query에 허용한 제어 자유도와 제약을 명시한다. 전체 영역의 global optimum이 입증되지 않았으면 확인한 후보의 bound와 탐색 범위·상한 근거를 분리한다.

불완전한 데이터 coverage는 물리적 금지가 아니다. 알려진 맵 밖에 해가 있을 가능성을 배제하지 못하면 모터 전체의 infeasible로 단정하지 않는다. 명시적으로 허용된 운전영역 내의 infeasible이라는 한정된 결론은 가능하다.

### H5.3 Minimum-current operating policy

주어진 shaft torque·속도에서 전압·전류·명시된 운전영역을 만족하는 후보의 I²를 최소화한다. 그 정책의 운전점에 대해 DC 전력·전류를 평가한다. DC source 한계를 위반해도 요구 토크를 clip하여 성공 처리하지 않는다.

이 정책은 charge power를 열로 버리면서 임의의 source-compatible current를 찾는 정책이 아니다. 충전 제한을 맞추기 위해 고손실 해로 이동하는 것을 default 동작으로 허용하지 않는다.

Physical-existence query가 다른 해를 발견해도 그 해를 현재 정책이 사용할 수 있다고 간주하지 않는다. source-compatible active-loss candidate는 정책 밖 결과로 구분하고 자동 채택하지 않는다.

동등한 최적해가 여러 개면 재현 가능한 tie 규칙을 정하고 release에 기록한다. 비선형 모델 전체를 단일 MTPA closed-form branch로 강제하지 않는다. minimum-current와 minimum-total-loss는 다른 목적이다.

### H5.4 Supplied-policy query

주어진 static current map/policy가 산출한 id/iq에 H5.1을 적용한다. policy map hole, 미정의 사분면, 범위 밖 입력은 UNKNOWN이다. 물리 최적점을 대신 넣어서 기존 정책이 요구를 통과한 것처럼 보이게 하지 않는다.

정책의 정적 성립은 closed-loop dynamics, estimation error, delay, stability 또는 transient 달성의 증명이 아니다.

### H5.5 Inverse sizing

MVP는 한 번에 하나의 명시적 design/scenario parameter 또는 소수의 선언된 후보 구성을 변경하여 요구에 필요한 범위를 찾는다. 관련 제약과 손실을 함께 재계산한다.

탐색 범위 밖의 최소 Vdc를 외삽하지 않는다. 비단조 문제에 단순 이분법의 global 최소성을 가정하지 않는다. global minimum을 확인하지 못하면 feasible candidate와 미탐색/불확실 구간을 반환한다.

운전점마다 서로 다른 hardware parameter를 사용해 한 설계가 전체 영역을 만족하는 것처럼 만들지 않는다.

## H6. Feasibility classification / uncertainty

각 claim은 quantity, scope, scenario/domain, policy, time horizon, model validity를 가진다.

- FEASIBLE: 명시된 범위의 해를 확인했고 필요한 제약이 평가되었다.
- INFEASIBLE: 적용 가능한 hard bound, necessary condition 또는 타당한 capability bound가 요구와 모순된다.
- UNKNOWN: 위 두 상태 중 어느 쪽도 충분히 확인되지 않았다.

원인은 MISSING_INPUT, OUTSIDE_MODEL_DOMAIN, UNVALIDATED_DURATION, UNCERTAINTY_OVERLAP, NUMERICAL_UNRESOLVED, INVALID_INPUT, POLICY_LIMITATION을 구분할 수 있어야 한다. 구체적인 enum 이름은 자유다. 잘못된 입력은 계산 전 validation error로 처리할 수 있으며 물리적 FAIL이 아니다.

Solver failure는 physical infeasible이 아니다. Local optimum은 certified capability가 아니다. 일부 grid 통과는 전체 연속 운전영역 통과가 아니다.

고객 요구 전체는 원칙적으로 AND 집계한다. 필요한 한 항목/시나리오라도 입증된 위반이면 FAIL이다. 위반이 없어도 필수 항목에 UNKNOWN이 있으면 전체는 UNKNOWN이다. 필요한 항목 모두가 정해진 coverage/evidence 조건을 만족해야 명시된 범위의 PASS를 반환한다.

'모든 Vdc'와 'nominal Vdc 한 점'을 구분한다. nominal FEASIBLE과 robust requirement PASS는 다르다.

불확실한 파라미터 u에 대한 이상적 adaptive feasibility인 ∀u∃x와, 실제 정책 π(y(u))를 고정한 robustness인 ∀u, g(π(y(u)),u)≤0을 구분한다. 제어기가 알 수 없는 제조 편차를 완전히 알고 있다는 가정을 숨기지 않는다.

실제 미상 파라미터에 대한 결과가 통과/위반 양쪽에 걸치면 실제 운전점 판정은 UNKNOWN이다. 그러나 모든 허용 u에서 만족해야 한다는 robust 요구에는 하나의 실현 가능한 확정 위반이 INFEASIBLE의 근거다. 외포락 구간만으로 만든 실현 불가능한 끝점을 반례로 사용하지 않는다.

입력 범위의 상관·물리적 결합을 유지한다. 근거 있는 분포가 없으면 confidence probability를 만들지 않는다. sampled analysis는 sampled라고 표시한다.

손실 구간은 Pdc=Pshaft+[Lmin,Lmax]로 전파한다. 구동의 상한과 회생의 하한을 각각 검사한다. 최대 손실만 사용하는 것이 양쪽 모두에 보수적이지 않다.

## H7. Margins / dominance

각 제약에 대해 native-unit limit, demand, slack, tolerance, reserve, source를 반환한다. upper constraint의 slack은 limit−demand, lower constraint는 demand−limit다. 음수면 위반이다.

Hardware voltage ceiling, 사용 가능한 command budget, 차감한 reserve, 잔여 margin을 구분한다.

Dominance는 active set의 나열에서 끝내지 않는다. 제약의 작은 완화 → 전체 결합문제 재계산 → capability/margin 개선량을 평가한다. 단독 완화로 개선되지 않고 두 제약을 함께 완화해야 개선되면 joint bottleneck으로 표현한다.

민감도에서 고정한 것과 재최적화한 것을 저장한다. fixed-current, reoptimized operating-policy, full capability 민감도는 서로 다르다.

원인 진단용 constraint relaxation과 실제 구현 가능한 hardware 변경을 구분한다. Vdc 변경은 voltage budget, average-current 조건, 데이터가 있는 경우 switching loss 등 관련 항목을 함께 갱신한다.

## H8. Input / output semantics

입력은 parameter/model ID, unit/convention, value/map, reference conditions, revision, origin, validity domain, optional uncertainty/correlation을 보존한다. 거대한 범용 schema를 먼저 만들 필요는 없다.

Requirement: ID/revision, 원문, quantity, measurement port, operator/target/band, conditions, quantifier, duration/initial state, exclusions.

Scenario: 운전점 또는 영역, DC/thermal/mechanical boundary, control policy, uncertainty, 관련 rating 조건.

Result: 요청값, 계산된 후보점, 전기·기계 전력과 손실, 제약/margin, 모델 적용 범위, 수치 잔차·탐색 근거, 판정 상태, 누락 근거, 다음 조치, 모델·데이터·입력 버전 식별자.

입력과 가정을 immutable snapshot으로 결과에 연결한다. 모델 변경이나 source map 갱신 후 이전 판정을 최신 결과처럼 보여주지 않는다.

## H9. Invariants

1. Pac=1.5(vd·id+vq·iq)=Tem·ωm+Pcu.
2. Pac=Pshaft+Pcu+Prot.
3. Pdc=Pac+Pinv. LV port가 추가되면 경계를 명시하고 식을 확장한다.
4. Passive losses≥0. 유효한 에너지 모드에서 0≤η≤1.
5. Torque/current/voltage는 동일한 후보점에서 계산한다. 서로 다른 최적점의 좋은 숫자를 결합하지 않는다.
6. 임의 current/torque clipping 또는 map extrapolation으로 feasible을 만들지 않는다.
7. Model-domain boundary와 physical limit를 구분한다.
8. 데이터 불확실성, 가정, 수치오차, 설계 reserve를 섞지 않는다.
9. 동일 input/model/policy/numerical settings에 재현 가능한 결과를 반환한다.
10. Surrogate 수치의 자릿수를 실제 시스템의 정확도로 표현하지 않는다.

## H10. Boundary / error behavior

- Vdc≤0, NaN, Inf, unit mismatch, 중복/잘못된 map axes: validation error.
- n=0, nonzero torque: copper/HV loss 평가 가능, Pshaft=0, efficiency=N/A. 지속시간은 별도 판단.
- Tshaft=0, n≠0: loss compensation/FW current가 필요할 수 있으므로 id=iq=0으로 고정하지 않는다.
- Negative speed/torque: 부호로 power mode를 판정한다. 사분면 이름만 뒤집지 않는다.
- Ld=Lq: 분모 특이점 없이 SPMSM case를 처리한다.
- 토크식을 변형한 분모가 0인 branch: 나누기 오류로 제거하지 말고 원래 식으로 검토한다. 특히 zero torque를 주의한다.
- V/I/DC 경계: 원래 정밀도, 명시적 tolerance, boundary qualifier를 유지한다. 표시용 반올림으로 판정하지 않는다.
- Map hole/outside domain: 조용히 보간·외삽하지 않는다.
- Missing losses: 가능한 electrical-only claim을 shaft/DC exact claim으로 승격하지 않는다.
- Missing thermal/time evidence: 10초/continuous를 추정하지 않는다.
- 속도/d축 제한의 출처 부재: electrical possibility를 rotor/demagnetization approval로 승격하지 않는다.
- 제어된 zero torque 또는 정상 고속 운전을 6SO 안전성과 동일시하지 않는다.
- 의도적 손실 증가로만 source-compatible한 후보: default energy-recovery policy의 PASS로 처리하지 않는다.
- Solver 미수렴/탐색 부족: NUMERICAL_UNRESOLVED. 수학적 불가능으로 단정하지 않는다.

## H11. Validation cases / acceptance

첨부 JSON은 고정 reference다. 다음 사례가 필수다.

- SPMSM analytic torque, voltage, copper power.
- Synthetic IPMSM forward: 정지, 정회전 구동, 역회전 구동, 회생, 전압 위반.
- Synthetic IPMSM inverse: MTPA 측, FW 측, DC source/charge 위반.
- 저전압 12,000 rpm·150 N·m의 infeasibility를 필요조건으로 확인.
- 12,000 rpm에서 source cap을 만족하는 minimum-current policy capability.
- Nonlinear coenergy-derived flux fixture와 reciprocity.
- Loss interval에 따른 회생 충전 판정 변화.
- Missing duration→UNKNOWN, 잘못된 units/maps→error, nonconvergence→UNKNOWN.
- 불완전한 map coverage를 physical limit로 간주하지 않는 사례.
- 기존 policy의 FAIL을 다른 policy의 성공으로 덮어쓰지 않는 사례.

### Acceptance numbers

직접 대입한 normalized algebra error≤1e-10.

Power identity residual≤max(0.01 W,1e-9×power scale).

Inverse torque residual≤max(0.001 N·m,1e-6×torque scale), normalized hard-inequality violation≤1e-7.

동일 fixture와 policy의 golden 비교: id/iq 각각 0.001 A, voltage 0.001 V, power 0.5 W 이내.

Capability 비교 및 bound gap≤max(0.1 N·m,0.0005×torque scale). 요구 경계가 계산 오차 구간과 겹치면 robust PASS/FAIL로 승격하지 않는다.

Nonlinear map은 node 일치와 off-grid refinement/error를 나누어 검증한다. 거친 보간값과 analytic off-grid 값이 정확히 같을 것을 요구하지 않지만, 차이와 의사결정 margin에 미치는 영향을 보고한다.

대표적인 결정 가능 fixture를 불필요한 UNKNOWN으로 회피하지 않는다. 반대로 의도적으로 불충분한 자료를 제공한 사례는 반드시 UNKNOWN/error여야 한다. 코드 coverage 비율만으로 acceptance하지 않는다.

독립 참조와 비교할 때 출력 경계, Park convention, loss scope, control policy를 일치시킨다. 같은 production 함수를 테스트에서 다시 호출하는 것은 독립 golden이 아니다. 다른 simulator에서 동일 수식이 일치한 것도 실기 validation의 대체라고 주장하지 않는다.

## H12. Release / handback expectations

실행 방법, model contract, 제약 목록, 알려진 limitation, 검증 실행 결과, 실패/미구현 항목, data provenance, 재현 조건을 제출한다.

사용자가 synthetic case를 실행하고 요구→조건→id/iq→power balance→constraint→conclusion을 추적할 수 있어야 한다. UI 장식보다 이 end-to-end engineering behavior가 우선이다.

Software abstraction, library 선정, UI 구조, test organization, 구현 순서는 구현자가 정한다. 구현을 쉽게 하려고 물리적 의미나 UNKNOWN semantics를 제거하지 않는다. 본질적 모호성이 발견되면 어떤 결론이 달라지는지 구체화한다.

## H13. 명확한 non-goals

PWM waveform 정확도, controller tuning/stability 승인, hardware pulse/SOA certification, thermal duration 추측, demagnetization/rotor stress certification, detailed battery dynamics, EMC/NVH/insulation, dual inverter/open-end winding, fault transition, ASIL/FuSa 승인, AI의 물리계산 대체, 범용 MBSE 플랫폼은 MVP 범위 밖이다.

목표는 거대한 범용 simulation framework가 아니라, 잘못된 확신 없이 반복적으로 유용한 inverter–motor engineering decisions를 제공하는 것이다.
