# Reference Engineering Cases — v1.0

작성일: 2026-09-27

## 1. 이 자료가 증명하는 것과 증명하지 않는 것

모든 값은 특정 회사 제품과 무관한 synthetic fixture다. Production 앱은 작성하지 않았다. 직접 수식 계산, 전체 선언된 d축 구간을 검사하는 1D reference calculation, selected necessary bounds, 일부 최대토크의 quadratic upper-bound certificate를 계산했다. 실기 시험, Simulink/PLECS 실행 비교 또는 공급사 맵과의 validation은 수행하지 않았다.

수치의 많은 자릿수는 회귀 검산용이다. 실제 모터 정확도를 의미하지 않는다. JSON은 immutable expected fixture이며 앱이 계산한 출력으로 자동 갱신하지 않는다.

## 2. 기본 synthetic inverter–motor 정의

정의 파일: `synthetic_drive.json`.

| 항목 | 값 / 의미 |
|---|---|
| p | 4 pole pairs |
| Rs | 0.015 Ω per phase, 고정 |
| ψPM | 0.100 Wb |
| Ld / Lq | 0.0002 / 0.0004 H |
| Ipk,max | 600 A, dq norm 기준 |
| id allowed domain | −500…0 A |
| iq data domain | −600…600 A, 전류 원과 동시 적용 |
| 속도 domain | −16,000…16,000 mechanical rpm |
| Vdc | nominal 600 V, stress 450 V |
| SVPWM command budget | 0.95 Vdc/√3, phase peak |
| inverter voltage error | Δv_inv=0, ideal mapping |
| DC discharge | 200 kW 및 400 A average 동시 제한 |
| DC charge | 100 kW 및 200 A average 동시 제한 |
| rotational torque | τrot=bωm, b=0.002 N·m/(rad/s) |
| inverter loss | 200+0.008(id²+iq²) W |
| PWM frequency | 20 kHz는 context일 뿐 손실 주파수 sweep 모델이 아님 |

id 하한은 **합성 사례의 명시적 허용 운전영역**이다. 실제 모터의 감자 경계가 아니다. 최고속도 역시 기계 강도 인증이 아니다. 온도 의존성, 냉각, continuous/10-second rating은 정의하지 않는다.

기본파 voltage mapping과 합성 total-energy loss를 별도로 정의한 근사다. 상세 도통강하·deadtime·switching waveform 재현을 주장하지 않는다.

정의식:

\[
\omega_m=2\pi n/60,\quad\omega_e=4\omega_m
\]
\[
\psi_d=0.1+0.0002i_d,\quad \psi_q=0.0004i_q
\]
\[
v_d=0.015i_d-\omega_e\psi_q,\quad
v_q=0.015i_q+\omega_e\psi_d
\]
\[
T_{em}=6(0.1-0.0002i_d)i_q,
\quad T_{shaft}=T_{em}-0.002\omega_m
\]
\[
P_{cu}=0.0225(i_d^2+i_q^2),\quad P_{rot}=0.002\omega_m^2
\]
\[
P_{dc}=T_{shaft}\omega_m+P_{cu}+P_{rot}+200+0.008(i_d^2+i_q^2)
\]

## 3. Forward goldens

파일: `golden_forward.json`. 아래는 표시용 반올림이며 JSON 값이 정밀 비교 기준이다.

| Case | n rpm | id / iq A peak | Tem / Tshaft N·m | Pac kW | Pdc kW | Vpeak V |
|---|---:|---:|---:|---:|---:|---:|
| F00 정지 | 0 | −200 / 400 | 336 / 336 | 4.500000 | 6.300000 | 6.708204 |
| F01 정회전 구동 | 3,000 | −200 / 400 | 336 / 335.371681 | 110.057513 | 111.857513 | 219.697387 |
| F02 전압·DC 위반 | 6,000 | −200 / 400 | 336 / 334.743363 | 215.615026 | 217.415026 | 434.408181 |
| F03 회생 | 3,000 | −200 / −400 | −336 / −336.628319 | −101.057513 | −99.257513 | 209.868153 |
| F04 역회전 구동 | −3,000 | −200 / −400 | −336 / −335.371681 | 110.057513 | 111.857513 | 219.697387 |

모든 위 점의 Ipk는 447.2135955 A이고 equivalent sinusoidal RMS는 316.2277660 A다. 정지에서 이 RMS를 각 상의 실제 시간 RMS라고 해석하지 않는다. 정지 효율은 N/A이며, 6.3 kW 소비를 누락하지 않는다.

별도 F05 SPMSM fixture는 p=4, ψPM=0.08 Wb, Ld=Lq=0.0003 H, Rs=0.02 Ω, rotational/inverter loss=0이다. id=0, iq=100 A, n=3,000 rpm일 때 Tem=Tshaft=48 N·m, Pcu=300 W, Pac=Pdc=15,379.6447372 W, Vpeak=109.2420331 V다. 전체 lossless라고 부르면 안 된다. copper loss는 남아 있다.

## 4. Inverse operating-point goldens

파일: `golden_inverse.json`. 기본 정책은 전압·전류·운전영역을 만족하는 최소 I² 해이며, 그 해의 DC 수지를 평가한다.

| Case | n rpm | Tshaft 요청 N·m | Vdc V | id / iq A peak | Pdc kW | 판정 범위 |
|---|---:|---:|---:|---:|---:|---|
| I00 | 3,000 | 300 | 600 | −190.57471 / 362.77552 | 99.766878 | static feasible |
| I01 | 6,000 | 300 | 600 | −315.21782 / 307.95107 | 195.408110 | FW, static feasible |
| I02 | 12,000 | 150 | 600 | −367.67596 / 146.47680 | 196.631385 | FW, static feasible |
| I03 | 12,000 | 150 | 450 | 해 없음 | — | stated domain electrical infeasible |
| I04 | 12,000 | 100 | 600 | −265.85886 / 111.54500 | 131.557238 | static feasible |
| I05 | 16,000 | 50 | 600 | −284.53398 / 56.66956 | 92.157729 | static feasible, dynamics 미검증 |
| I06 | 6,000 | 350 | 600 | −420.38775 / 318.03320 | 229.376119 | DC discharge infeasible |
| I07 | 12,000 | −100 | 600 | −249.78390 / −108.34980 | −120.044417 | DC charge infeasible |
| I08 | 12,000 | −80 | 600 | −222.31897 / −89.39579 | −95.421463 | energy-recovering regen feasible |
| I09 | 3,000 | −300 | 600 | −189.70262 / −361.71590 | −88.762211 | regen feasible |
| I10 | 0 | 300 | 600 | −190.13878 / 362.24598 | 5.304935 | static torque feasible; duration unknown |

정적 가능은 모델에 명시된 제약에 한한다. 모든 duration status는 UNKNOWN이다.

### 4.1 Independent reference 계산 방식

토크식으로 다음을 얻는다.

\[
i_q=\frac{(T_{shaft}+b\omega_m)/6}{0.1-0.0002i_d}
\]

선언된 −500≤id≤0에서 분모는 양수다. 이 식을 전류 원과 정확한 Rs 포함 전압식에 넣는다. 분모 제곱을 곱하면 각각 polynomial inequality가 된다. 실제 경계근과 domain 경계를 모두 모아 구간을 분할하고, 각 유효 구간과 경계에서 I² 최소를 비교했다. 이는 production 구현을 특정 solver로 강제하는 지침이 아니라 독립적인 expected-value 생성 경로다.

### 4.2 I03: 최적화 실패와 무관한 불가능 근거

12,000 rpm·150 N·m에 필요한 Tem=152.5132741 N·m다. 허용 id 구간에서 0.1−0.0002id≤0.2이므로 iq≥127.0943951 A다.

Rs·id≤0, iq>0이므로:

\[
|v_d|\ge\omega_eL_qi_q\ge255.5384435\;V
\]

반면 450 V의 command budget은 246.8172401 V다. 따라서 q축 전압까지 고려하기 전부터 모순이다. 이 결론은 명시된 id 허용영역 안의 불가능이며, 다른 모터/운전영역의 일반적 불가능이 아니다.

### 4.3 I06: shaft power만으로 확인하는 불가능

350 N·m·6,000 rpm의 shaft power=219.9114858 kW다. 손실이 0이어도 DC 200 kW로 공급할 수 없다. minimum-current 정책만의 실패가 아니라 이 fixture의 source-bound physical infeasibility다.

### 4.4 I07: 손실을 최대화해도 charging cap 불만족

−100 N·m·12,000 rpm에서 shaft power=−125.6637061 kW다. Ipk≤600 A이므로 이 fixture의 전체 손실 상한은:

\[
P_{loss,max}=0.002\omega_m^2+0.0305(600)^2+200
\]

그러면 Pdc가 가장 덜 음수가 되어도 −111.3254327 kW다. 요구 하한 −100 kW에 못 미친다. 따라서 이 특정 사례는 의도적으로 손실을 늘리는 다른 해까지 허용해도 불가능하다. 일반적인 회생 정책 실패를 이렇게 단정할 수 있다는 뜻은 아니다.

## 5. Capability / source-boundary goldens

파일: `golden_capability.json`.

| n rpm / Vdc V | 경계 Tshaft N·m | Ipk A | Pdc kW | 의미 |
|---|---:|---:|---:|---|
| 6,000 / 600 | 306.8288961 | 451.7416372 | 200 | positive capability |
| 12,000 / 600 | 152.5550589 | 402.2634984 | 200 | positive capability |
| 12,000 / 450 | 134.8888267 | 483.6821825 | 180 | DC 400 A와 voltage가 active |
| 12,000 / 600 | −83.7110716 | 245.3609964 | −100 | minimum-current regen policy boundary |

회생 행은 임의의 고손실 제어까지 포함한 최대 제동 능력이 아니다.

### 5.1 Positive capability의 독립 상한 확인

이 linear synthetic case는 모든 식이 id,iq의 2차식이다. gV=||vdq||²−Vbudget², gP=Pdc−Pcap으로 정의하고:

\[
\mathcal L=T_{shaft}-\lambda_V g_V-\lambda_P g_P
\]

를 만든다. 각 경계점에서 λV,λP≥0이고, L의 gradient가 0이며 Hessian이 negative definite이면 그 점은 이 quadratic Lagrangian의 전역 최대다. feasible point에서는 Tshaft≤L이므로 최대토크에 대한 상한 근거를 얻는다. 나머지 domain/current 조건까지 만족하는 후보가 같은 값을 달성하면 upper/lower bound가 닫힌다.

JSON에는 세 구동 경계의 multiplier, Hessian eigenvalues, stationarity residual을 포함했다. 12,000 rpm·600 V 사례에서 λV≈3.53567e−5, λP≈7.57820e−4이며 eigenvalues는 약 −3.45932e−4와 −1.03886e−4다. 실제 검증은 단위를 유지한 식과 finite-precision bound tolerance를 포함해 다시 계산해야 한다. KKT라는 이름만으로 임의 비선형 문제의 전역 최적성을 주장하면 안 된다. 여기서는 전역 concavity가 추가 근거다.

### 5.2 이 사례에서 얻는 제품 가치

150 N·m 요청점의 Ipk margin은 약 204.221 A이나, 전체 결합 capability의 torque margin은 약 2.555 N·m다. 요청점의 voltage command budget은 이미 active이며, DC margin은 약 3.369 kW다. 전류만 보고 인버터 여유가 충분하다고 결론 내리면 놓치는 내용이다.

## 6. Nonlinear manufactured flux map

파일: `manufactured_flux_map.json`. id/iq 축 각각 −200…200 A, 10 A 간격이며 배열은 row=id, column=iq다. 이는 실제 모터가 아니라 상호성과 cross-saturation 보간을 검사하는 manufactured magnetic potential이다.

\[
F=0.1i_d+\frac{0.0002}{2}i_d^2+\frac{0.0004}{2}i_q^2
-\frac{10^{-10}}4i_d^4-\frac{2\times10^{-10}}4i_q^4
-\frac{10^{-10}}2i_d^2i_q^2
\]

F는 dq-normalized magnetic potential이며 ψ=∇F다.

\[
\psi_d=0.1+0.0002i_d-10^{-10}i_d^3-10^{-10}i_di_q^2
\]
\[
\psi_q=0.0004i_q-2\times10^{-10}i_q^3-10^{-10}i_d^2i_q
\]

id=−100 A, iq=150 A, p=4에서:

| 값 | expected |
|---|---:|
| ψd | 0.080325 Wb |
| ψq | 0.059175 Wb |
| Tem | 107.7975 N·m |
| ∂ψd/∂id | 0.00019475 H |
| ∂ψq/∂iq | 0.00038550 H |
| ∂ψd/∂iq = ∂ψq/∂id | 0.000003 H |

노드 재현, 축 방향, 토크 일관성, 미분 상호성, off-grid refinement, 범위 밖 거절을 검사한다. 이 맵을 main synthetic linear motor의 교체품처럼 섞어 쓰지 않는다. 두 모델은 서로 다른 fixture다.

## 7. 판정 semantics와 미래 확장 fixture

파일: `semantic_boundary_cases.json`.

**회생 손실 범위:** Pshaft=−80 kW, 전체 손실 2…8 kW이면 Pdc=−78…−72 kW다. charging cap=75 kW일 때 일부 경우 통과, 일부 위반이다. nominal 손실 5 kW만 검사해 robust PASS라고 하면 안 된다. 실제 손실값이 미상이면 실제 운전 판정은 UNKNOWN이다. 그러나 2…8 kW 전체가 허용 가능한 불확실성 집합이고 모든 경우의 만족을 요구하면, 2 kW가 확정 반례이므로 robust 요구는 INFEASIBLE이다. 단순 외포락 구간의 끝점이 실제 가능한지는 별도로 확인한다. 최소 손실이 회생 수용성에는 더 불리하다.

**지속시간 누락:** I02에 10초 요구를 추가하면 electrical FEASIBLE은 유지하되 duration UNKNOWN, 전체 10초 요구 UNKNOWN이다.

**입력/수치 오류:** Vdc≤0, nonfinite, 단위 모호성, 맵 축 오류는 validation error다. Map hole, 미확인 사분면, 수치 미수렴은 적절한 UNKNOWN 사유를 남긴다. 부분 coverage를 물리 한계로 바꾸지 않는다.

**미래 reducer fixture — MVP 구현 요구 아님:** ratio=9, η=0.97, motor 9,000 rpm이면 output 1,000 rpm이다. motor +150 N·m 구동 시 output +1,309.5 N·m, motor −150 N·m 회생 시 output −1,391.7525773 N·m다. 전력 전달 방향에 따라 효율식이 바뀌며 항상 passive loss≥0이어야 한다.

**미래 1-node thermal fixture — MVP 구현 요구 아님:** P=1,000 W, Rth=0.02 K/W, Cth=5,000 J/K, 초기/냉각수=65°C면 τ=100 s이고 T(t)=65+20(1−e^(−t/100))다. T(100s)=77.6424112°C, 80°C 도달 시간=138.6294361 s다. 고정 손실 해석 사례이며 온도 피드백이나 실제 냉각을 검증하지 않는다.

## 8. 수치 비교와 변경 관리

허용오차는 handoff의 acceptance 기준을 사용한다. 표시용 표의 반올림보다 JSON을 우선한다. Exact-boundary fixture에서 부동소수점의 작은 양/음 residual로 FAIL/PASS를 바꾸지 않는다. 기준 물리식을 바꿔야 한다면 모델 버전과 fixture 버전을 함께 갱신하고 변경 근거를 남긴다.

최초 생성 검산에서 forward 6개 사례의 전력 항등식 최대 절대 잔차는 2.91e−11 W였다. Inverse 11개 사례 중 해가 있는 10개에서 토크 일치와 전압/전류 경계를 확인했다. 이는 이 fixture의 계산 검산 결과이지 production 제품이나 실기 validation 결과가 아니다.
