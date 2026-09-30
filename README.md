# Traction Workbench

**Traction Engineering Feasibility & System Analysis Workbench** — 단일 3상 2-level 인버터 + PMSM/IPMSM의
정상상태 기본파 모델로 고객 요구를 판정하고, 그 근거를 그래프와 재현 가능한 **Engineering Decision Record**로 남기는
**독립 실행형 데스크톱 애플리케이션**입니다 (Windows `.exe`, 서버·네트워크·Python 설치 불필요).

> “현재 조건에서 고객 요구를 만족시킬 수 있는가? 무엇이 막고 있으며, 어떤 변경이나 추가 자료가 의사결정을 바꾸는가?”

![요구 판정 화면](docs/screenshots/decision.jpg)

> [!IMPORTANT]
> **새 기능: 기능안전(FuSa) 설계 작업대.** 소프트 ASC 같은 반응 전략, JSON 없이 고치는 설계·요구 편집기,
> 시뮬레이션 전에 모순을 잡는 정적 설계 검토, 전체 시나리오 검증 매트릭스, 심사용 안전 근거 보고서.
> 실제 파형은 [아래 쇼케이스](#기능안전-설계-작업대--고장-시뮬레이션)에서 볼 수 있습니다.

## 기능안전 설계 작업대 — 고장 시뮬레이션

고장 하나를 주입하면 센서 → 제어·감시 → 반응 → 브리지 → 플랜트가 한 시간축에서 인과적으로 돌아가고,
요구(SG → FSR → TSR)는 명령 비트가 아니라 **실제 토크·전류·전압**으로 판정됩니다.
아래 화면은 모두 내장 합성 프로젝트에서 `docs/make_fusa_showcase.py`로 찍은 실제 실행 결과입니다.

### 같은 고장, 반응만 바꿔서 — 하드 ASC vs 소프트 ASC

![반응 후보 비교: 하드 ASC와 전환 전략의 토크·전류·DC 전압](docs/screenshots/fusa_hero_transition.jpg)

전류 센서 오프셋(6000 rpm, 10 ms에 주입), 같은 초기 조건에서 반응만 바꾼 비교입니다.
**하드 ASC**는 최대 1318 A, 제동 토크 637 N·m로 TSR을 위반하고,
**전압 램프 소프트 ASC(`SOFT_ASC_V`)**는 608 A, 121 N·m로 모든 요구를 만족합니다.
`FW2_ASC_LOW`(2 ms 6SO 후 ASC)는 그 중간입니다. 후보마다 위반한 요구 ID가 표에 남습니다.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/fusa_waveform_hard_asc.jpg" alt="하드 ASC 동기 파형"></td>
<td width="50%"><img src="docs/screenshots/fusa_waveform_soft_asc.jpg" alt="소프트 ASC 동기 파형"></td>
</tr>
<tr>
<td><b>하드 ASC 동기 파형</b> — ±600 N·m 토크 진동이 요구 허용 창(초록)을 벗어나고, 안전 조건 도달까지 42 ms(FHTI). 참값·센서·명령·실제 브리지가 한 시간축에</td>
<td><b>소프트 ASC 동기 파형</b> — 전압을 2 ms 동안 0으로 줄인 뒤 ASC를 걸어 토크 진동이 ±120 N·m 안에 머물고, 안전 조건 도달까지 7.8 ms(FHTI)</td>
</tr>
</table>

![사건 타임라인: 고장 → 검출 → 전략 단계 → 안전 조건 도달](docs/screenshots/fusa_timeline_soft_asc.jpg)

**사건 타임라인** — 고장(10.0 ms) → 세 상 합 감시 검출(10.9 ms) → 전략 1단계 전압 램프 → 2단계 ASC.
FSR마다 FDTI·FRTI·FHTI가 FTTI 예산과 같은 축에 그려집니다.

### 설계를 바꾸고, 시뮬레이션 전에 검토하고, 전체로 검증

<table>
<tr>
<td width="50%"><img src="docs/screenshots/fusa_strategy_editor.jpg" alt="반응 전략 편집기"></td>
<td width="50%"><img src="docs/screenshots/fusa_mechanism_editor.jpg" alt="안전 메커니즘 편집기"></td>
</tr>
<tr>
<td><b>반응 전략 편집기</b> — 단계(동작 + 종료 조건 + 최대 시간)를 표로 편집. 템플릿 7종: FW→ASC, 전류 사전조정·전압 램프 소프트 ASC, 상 순차 ASC, 속도별 ASC→6SO, Vdc 히스테리시스, 토크 램프</td>
<td><b>보호 설계 편집기</b> — 디바운스·임계값·경로를 필드로 수정(JSON 직접 입력 없음). 바꾼 값은 설계 변형으로 남아 비교·되돌리기 가능</td>
</tr>
<tr>
<td><img src="docs/screenshots/fusa_design_review.jpg" alt="정적 설계 검토"></td>
<td><img src="docs/screenshots/fusa_verification_matrix.jpg" alt="검증 매트릭스"></td>
</tr>
<tr>
<td><b>정적 설계 검토</b> — 디바운스를 25 ms로 바꾸면 시뮬레이션 없이 “검출 지연 하한 25 ms > FDTI 예산 20 ms” 모순으로 표시</td>
<td><b>검증 매트릭스</b> — 전체 시나리오 × 요구. 요구마다 한 번도 시험되지 않았는지, 최악 FDTI/FRTI가 어느 궤적에서 나왔는지 표시</td>
</tr>
</table>

<table>
<tr>
<td width="50%"><img src="docs/screenshots/fusa_requirement_editor.jpg" alt="안전 요구 편집기"></td>
<td width="50%"><img src="docs/screenshots/fusa_report.jpg" alt="안전 근거 보고서"></td>
</tr>
<tr>
<td><b>안전 요구 편집기</b> — SG·FSR·TSR의 FTTI, FDTI·FRTI 예산, 판정 기준(토크 창, 한계, 안전 상태 유지)을 수치로 직접 조정</td>
<td><b>안전 근거 보고서 (HTML)</b> — 설계 검토, 추적성, 검증 매트릭스, 시뮬레이션 FMEA를 한 파일로. 커밋되지 않은 소스 변경이 있으면 경고</td>
</tr>
</table>

## 실행 (Windows)

1. `TractionWorkbench-<버전>-windows-x64.zip`을 받습니다 — GitHub **Actions → build → Artifacts**
   (`TractionWorkbench-windows-x64`) 또는 `v*` 태그를 올리면 자동 생성되는 **Releases**.
2. 압축을 풀고 `TractionWorkbench\TractionWorkbench.exe`를 실행합니다. 설치·관리자 권한이 필요 없습니다(휴대용 폴더).
3. 코드 서명이 없어서 처음 실행 시 SmartScreen이 “Windows의 PC 보호” 경고를 띄울 수 있습니다: **추가 정보 → 실행**.
   사내 배포 시에는 IT 정책에 따라 서명/화이트리스트를 요청하세요.

같은 폴더의 `twb.exe`는 콘솔 CLI입니다: `twb.exe report case.json --pdf 보고서.pdf`, `twb.exe acceptance`, `twb.exe selftest out`.
모든 계산은 PC 안에서만 수행되며 포트를 열지 않습니다.

## 화면 구성

| 페이지 | 내용 |
|---|---|
| **요구 판정** | 요구 원문·토크·속도·Vdc(단일/범위)·지속시간 입력 → PASS/FAIL/UNKNOWN 배너(사유·범위), **판정 층 표**(수학 · 모델 · 요구 · qualification — 서로 다른 진술을 하나로 합치지 않음), 조건별 claim 트리와 근거, 요구 witness(정적·DC·지속시간이 같은 점), 핵심 수치, 제한 요인·다음 조치, 운전점 그래프(**시스템 개요도**: 배터리–릴레이–DC 링크–인버터–모터에 운전점 값 표시), T–n 상의 위치, 역설계·병목 분석, 의사결정 기록(Markdown) · JSON/MD/**PDF 보고서** 저장. UNKNOWN/FAIL 배너에 **원인 분류**와 그 분류를 닫는 작업. **Vdc 범위 요구**는 조건(정적 순구동, Vdc 무관 a0 + a2·I² 손실이 범위 전체에서 유효, 고정 소스 한계)이 성립하면 저전압 끝점으로 범위 전체를 입증(단조성 인증서 — 회생·지속시간 부분은 제외), **자석 온도 plane이 여러 개인 flux map**에서 자석 온도를 말하지 않은 요구는 모든 plane에서 for-all로 판정(반례 온도 표시). Vdc를 **배터리 OCV**로 지정하면 선언된 Thevenin R_eq로 단자 전압을 풀어 판정(회생은 상승, 공급 불가면 증명된 FAIL). 추가 분석에 **PWM 위험 (같은 운전점)**: 기본파 전류 한계 vs 보수 순간 피크, 추가 RMS, 주요 선, DC-link 부담 |
| **요구 묶음·후보** | 요구 여러 건(표 입력·스프레드시트 붙여넣기·**CSV 가져오기**/템플릿)을 **같은 제품 데이터·조건·근거**로 한 번에 판정. 계산 전 **해석 확인**(축 토크·기계/전기 속도·인버터 DC 단자·Vdc for-all·연산 의미·지속시간)과 모터 모델 없이 고객 수치만으로 되는 **사양 필요조건**(같은 운전점의 T·ω vs DC 한계 — 위반이면 어떤 드라이브로도 불가능, 모델이 말하지 못한 요구도 결정), 요구별 판정·여유·제한 원인·**UNKNOWN 원인 분류**(입력 결측 / 적용성 미확인 / 연속 범위 미입증 / 모델 범위 밖 / 수치 / 근거 충돌 / 정책 한계)·바꿀 수 있는 항목·다음 자료, 묶음 전체의 **다음 자료 우선순위**(작업 종류 × 확정되는 요구 수), **후보 × 요구** 표(후보마다 모든 요구를 다시 판정, 개선 ▲/악화 ▼, 가중 점수·비용 최적 없음), 결과 CSV, 선택한 요구를 판정 페이지에서 열기 |
| **운전점 탐색** | 토크 → 최소전류 정책점, 또는 id/iq 직접 입력(정방향 평가). 결과는 **ACCEPTED / DIAGNOSTIC ONLY / UNKNOWN**으로 구분하고 위반·미평가 제약과 gate 사유를 표시(진단값은 가능한 해가 아님). **id–iq 지도를 클릭**하면 그 전류 벡터를 그대로 평가, 마우스를 올리면 토크·전압·DC 전력 판독. 자석 온도 plane이 여러 개인 flux map이면 **자석 온도** 입력(첫 plane 온도로 미리 설정, 궤적·설계 페이지도 같음) |
| **궤적** | 토크 스윕 @ 속도(MTPA → 약계자 → 한계), 속도 스윕 @ 토크(기저속도·약계자 진입). dq 전류 궤적 + 여러 속도의 전압 타원, 변수 추이, 표 |
| **성능 곡선·맵** | 정책(DC 포함) vs 전기적 T–n 곡선(활성 제약별 색), 비교 Vdc, 효율·손실·전류·변조율·역률·id·iq·P_dc 맵(효율은 방향별 정의로 표기, 미상 손실 칸은 빗금 — 총 손실은 모든 항이 확정될 때만), 기저속도 곡선, 최대 토크 곡선을 따라가는 운전점. 자석 온도 plane이 여러 개인 flux map은 온도를 지정하지 않으면 **plane마다 한 곡선** |
| **설계·병목** | capability vs 파라미터(Vdc, 전류 정격, 예약분, DC 한계, …)와 bisection 역설계, 제약 1% 완화 병목 기여도, 요구 달성 최소 완화·공동 병목 |
| **안전 스크리닝** | FTTI 체인 Gantt(중복 예산 자동 검출, 모든 연속 경로 중 보장 상한 경로, **안전 종점·종점 종류**(명령 발행은 물리적 안전 상태가 아님 → UNKNOWN)·최댓값 동시 발생 선언, FDTI/FRTI 최악값), **회로 개요도** + 능동 방전 V(t), **패시브 방전**(블리더 R_p 설계 창), 방전 결과 표(**정류 위험**, 역기전력 근거, 목표 이하 최고 속도, 정류 링크 전압 스크리닝 추정), 회생 중 배터리 차단 과전압, ASC/Freewheel 회로 비교와 속도별 곡선, 프로젝트 규칙 표(물리와 분리된 계층) |
| **보호·고장** | 임계값·디레이팅·고장 반응을 하나의 인과 궤적에서 검증(이벤트 타임라인, 임계값 창·PROT 표 — 판정은 지평이 아니라 반응과 플랜트 정확해로, 검출 루프 개요도), ASC 고장 과도(표본 사이까지 정밀화한 피크, 모델 전류 영역 이탈 표시)와 고객의 두 전류-시간 요구 |
| **고장 시뮬레이션·FuSa** | 인버터 고장 → 측정·추정 → 제어·감시 → 보호 반응 → 실제 토크·전류·DC-link → 안전 요구 판정의 **한 인과 궤적**(참값·측정·추정·명령·실제를 분리, 감시·반응은 측정만 봄). 스위칭 다리 플랜트(떠 있는 다리·다이오드 도통·6SO 정류·ASC), DC-link·배터리·접촉기·BMS, 센서 고장·이산 FOC·MCU 리셋·재시동, 메커니즘·반응 경로·공유 자원·안전 상태 정책(프로젝트 데이터). SG → FSR → TSR 판정(동적 토크 창·한계·안전 상태·오반응 없음·시간)과 FDTI/FRTI/FHTI, 대표 시나리오, 같은 초기 조건의 **반응 후보 비교**, 캠페인(경계 이분·최악값과 그 실행), **반례** 저장·열기·재실행·입력 변경 표시, 독립 abc 정식화 검증, 독립성(공유 센서·자원) 보기. **설계 편집 탭**(FTTI·TSR 수치·디바운스·임계값·반응 전략·정책을 칸에서, JSON 없음 — 프로젝트 대비 설계 변형으로 실행·저장·반영), **반응 전략**(소프트 ASC 두 방식·상별 순차 ASC·FW → ASC·ASC → 6SO·Vdc 히스테리시스·토크 램프), **안전 근거 탭**(정적 설계 검토, 검출 지연 범위, 검증 매트릭스·시뮬레이션 FMEA, 안전 근거 HTML 보고서) |
| **열·지속시간** | **냉각수**(입구 온도, 유량, 에틸렌글리콜:물 물성, 순환 순서, 기준 유체 온도) → 부품별 냉각수 온도 상승 ΔT = P/(ṁ·c_p). **열 회로망 표 편집**(Foster/Cauer, 4단 템플릿, 데이터시트 붙여넣기, 유량 의존 단), RC 회로도·냉각수 순환도·Z_th(t), 지속시간별 가용 토크와 노드 온도. **초기 열 상태**(미선언·고온 시작은 UNKNOWN)와 열 모델의 **검증 근거**(없으면 “검증”은 증거 없는 선언)를 입력. **반복 부하**: 펄스–휴지 반복, 고온 시작(예부하 정상상태 / Cauer 노드 온도), R_s(T)·모듈 T_j 손실 피드백, 주기 정상상태(고정점), 허용 펄스 시간·토크, 반복 전 필요 휴지 |
| **전력변환·수명** | 데이터시트 모듈 손실(소자별 도통·스위칭, 온도×전류 표, 외삽 금지) → P_dc·열·claim, DC-link 리플·커패시터 전류·ESR 손실·수명 게이트, 모듈 열 사이클 rainflow·조건부 손상 |
| **효율·모듈 비교** | 다섯 제어 체적(인버터 · 모터 · 인버터+모터 · 감속기 · eDrive)의 포트 기준 효율(구동/회생 방향별 정의, N/A · UNKNOWN · INCONSISTENT 구분, clamp 없음), 손실 원장(확정 소계와 미상 항목), 경계별 지도, 미션 E±, 모듈 A/B(IGBT vs SiC: 고정 정책 vs 설계별 정책, Tj는 결과, 선언된 오차 예산을 넘을 때만 우열). 원장에 모터 PWM 동손(정확값 또는 R_dc 하한)과 Fe+PM HF 상한(값 아님), PWM 고조파를 포함한 효율 구간 |
| **가변 PWM·Anti-jerk** | 고정 fsw 기준안과 인과적 fsw 스케줄(히스테리시스·dwell·보호 선점·fallback)을 같은 궤적에서 비교 — 모듈 손실(결합 Tj), RL 리플, DC-link 전류, 지연 원장·전류 루프 위상 여유, 최소 펄스, 카운터 수준 reload 검사, Pareto(에너지는 [확정, 확정 + PWM 동손·Fe+PM 상한] 구간 — 겹치면 UNDECIDED, 요청 fsw ≠ 파형 fsw 표시, 피크 전류는 보수 상한); 2관성 드라이브라인에서 off/성형/피드백/결합 비교(ZOH+분수 지연, 지연 교차, 중재 후 클리핑, 백래시 통과 → UNKNOWN) |
| **OEW·HEV** | OEW 듀얼 인버터(토폴로지·영상분·전력 분배·포트 한도, 최소전류 witness, 두 브리지 손실·포트 회계, 스위칭 상태 기하, 쌍 안전 상태, i0 리플), HEV(두 기계의 결합 토크 집합, 가지 전력 vs 순전력, 크랭킹 replay, 부하 차단 에너지, 유성기어 검사) |
| **EMI (전도성)** | 요구 프로파일(방법·RBW 구간·승인된 공백) + 한도 곡선, 스위칭 순서 소스(데드타임 턴온 지연·다이오드 클램프·최소 펄스, 요청/평가 fsw), 선언된 CM/DM 경로·인공 회로망, CM 귀환 레일 네 모델(중점·에지 부호·HV+·HV−), 수신기 **추정**(직사각 IF·피크 검출기의 포락 최대)과 **상한**(선합·가우시안 IF, 창당 선 수 표시)·반례 분리 — **연속 대역의 정확 열거**(표시 격자와 무관), 대역별 여유·필요 감쇠(추정 기준, 상한 기준 병기; 스크리닝은 PASS가 아님), **보정 기록**(완전성·구성 결속·매 실행 적용성 재검사)이 있을 때만 FEASIBLE / 하한 증인이 있을 때만 INFEASIBLE, 측정 trace는 **trace 자체의 취득 조건**(표현·검출기·RBW·IF 형상·dwell·보정·set-up)과 읽음값 사이 손실까지 판정, OEW 권선 영상분 vs 섀시 공통모드 |
| **모터 설계** | 검증된 기준 모델 주변의 일관 스케일링(턴·병렬 회로·적층·자석, 계보와 무효화되는 데이터 목록) → 같은 결합 요구 여유로 후보 비교, 권선 star of slots(권선계수·평형·병렬 회로; k_N 인계는 두 배치 모두 유효하고 극쌍수가 같을 때만, 선언된 권선이면 계보 결속·아니면 '일반 k_N 사고 실험'), 개념 사이징(T = 2σV_r). 모터 CAD/FEA가 아님 |
| **프로젝트** | 한 제품의 제품 데이터(드라이브·DC 전원·모듈·DC-link·제어기·감속기·열망·안전·EMI set-up)를 ID·개정·섹션 digest와 함께 한 번만 보관 — 모든 페이지가 활성 프로젝트에서 제품 데이터를 가져오고, 모든 결과가 사용한 섹션과 **로컬 변경**을 기록, 프로젝트가 바뀌면 영향받은 결과를 **stale**로 표시. 섹션·provenance(선택한 섹션의 내용 트리), 일관성 검사(INCONSISTENT/WARNING/NOTE), 개정 이력, 파일과의 개정 비교(변경 경로·영향받는 분석), 열기·저장·새 개정. **데이터시트 값 입력**(모터·모듈·커패시터·dv/dt 대표값을 직접 입력 → 선언된 구성 규칙으로 모델, 규칙·가정·단위 변환 기록, 실시간 미리보기) · **데이터시트 파일 가져오기**(디지타이즈 곡선·ESR 표·dv/dt → 공급사 provenance 섹션, 외삽 없음), **MathWorks 이식 패키지**(아래) |
| **모델·데이터** | 내장 드라이브(상수 dq D1 / flux map D2) 선택, 단위가 선언된 드라이브 JSON·case 파일 불러오기, DC 소스 한계(**값 / 미선언(UNKNOWN) / 선언된 무제한(∞)** 구분) — 활성 프로젝트의 drive·dc_source 섹션을 편집(수정된 작업 사본), provenance, **data audit**(용도별 사용 가능 여부와 qualification 공백) |
| **검증 (V&V)** | production vs golden acceptance(오차/허용오차 그래프), 참조 패키지 SHA-256, 알려진 한계, **교환 패키지** 저장(MathWorks 이식·도구 간 parity용 규약·fixture) |

결과가 있는 모든 페이지는 결과 탭의 첫 칸 **엔지니어링 분석**에서 결과를 공학적으로 읽어 줍니다: 결론 한 줄, 한계를 만드는
메커니즘, 전력·손실·시간·에너지가 어디로 가는지, 여유가 가장 작은 항목, 무엇을 바꾸면 답이 바뀌는지, 미확정의 원인과 필요한 자료.
모든 수치는 그 결과에서 나오고(결과 수치로 만든 항등식은 항을 함께 표시), 해석은 판정을 바꾸지 않습니다. 판정 페이지의 Markdown·PDF
보고서에도 같은 해석이 들어갑니다. 페이지 머리 막대의 **▶ 버튼**(Ctrl+Enter)은 보이는 탭의 계산을 실행하고, 실행 중에는 그 페이지의
계산만 취소합니다(Esc). 긴 계산은 상태 표시줄에 어디까지 왔는지(예: "요구 판정: 1/2 판정 · 토크 능력 130/350 (스캔) · 21 s")를
보여 주고, 취소하면 다음 계산 단계에서 바로 멈춥니다. 요구 판정은 판정을 먼저 보여 주고 PWM 영향·추가 분석을 뒤이어 채웁니다. 결과를
만든 뒤 입력을 바꾸면 결과 위 띠와 배너에 바뀐 항목(이전 → 지금)이 표시되고, 되돌리면 사라집니다. **작업 공간 파일**(파일 메뉴 →
작업 공간 저장·열기, `*.twb-workspace.json`)은 모든 페이지의 입력과 프로젝트를 담고, 앱은 작업 중인 입력을 자동으로 남겨 다음 시작 때
복원할지 묻습니다.

모든 그래프는 확대·이동·PNG/SVG/PDF 저장, 데이터는 CSV로 내보낼 수 있습니다. 한국어/영어, 라이트/다크 테마를 지원합니다.
각 페이지의 **ⓘ 개념 설명**을 펼치면 그래프 읽는 법과 핵심 식을 짧게 볼 수 있습니다(전문 내용은 그대로, 처음 쓰는 사람을 위한 보조).
항상 보이는 배지로 활성 프로젝트(ID·개정)·모델 ID·fidelity(D1/D2)·데이터 출처(synthetic)·“하드웨어 미검증”을 표시하고,
결과가 있는 페이지 위에는 그 결과가 어떤 프로젝트 데이터로 계산되었는지(로컬 변경, stale 여부)를 띠로 보여 줍니다.

## 전문가용 그래프

아래 화면은 모두 데스크톱 self-test(`twb selftest`)가 각 페이지를 실제 코드 경로로 실행한 그대로의 캡처입니다(내장 합성 예제 데이터,
`python docs/make_screenshots.py`로 다시 만듭니다). 이미지를 누르면 원본 크기로 볼 수 있습니다.

### 엔지니어링 분석 (결과 해석)

| | |
|---|---|
| ![FAIL 해석](docs/screenshots/reading_decision_fail.jpg) | ![과전압 해석](docs/screenshots/reading_overvoltage.jpg) |
| **요구 판정 FAIL의 해석** (450 V): 결론 한 줄(최대 토크 134.9 N·m, 부족 15.11 N·m), 판정 항목별 근거(요구 축출력 188.5 kW가 손실이 0이어도 유효 방전 한도 180 kW를 넘음 — 어떤 드라이브로도 불가능), 요구를 만족시키려면(전압·DC 방전 전류 한계 공동 +10.6 %, Vdc ≥ 497.7 V; 인버터 전류만으로는 불가), 이 결과가 말하지 않는 것 | **회생 중 배터리 차단의 해석**: 흡수 여유 ½·C·(V_lim² − V₁²) = 90.62 J 대 선언 반응 2 ms 동안의 유입 190.8 J(2.11배) → 최고 전압 √(V₁² + 2E/C) = 1,060 V, 결과를 바꾸는 것(반응 ≤ 0.95 ms, 또는 C ≥ 2E/(V_lim² − V₁²) = 1,053 µF), freewheel만으로 부족한 이유(역기전력 870.6 V > 850 V) |
| ![효율 해석](docs/screenshots/reading_efficiency.jpg) | ![가변 PWM 해석](docs/screenshots/reading_pwm.jpg) |
| **효율 운전점의 해석**: 다섯 경계의 η, 포트 사이 전력 흐름(DC 97.25 kW → 감속기 출력 91.72 kW, 경계마다 손실 차감), 손실 원장(감속기 46 %, 모터 동손 22 %, 인버터 18 %, 회전·철손 14 %), 전자기 변환 T_e·ω와 축 출력의 차 = 회전 손실 | **가변 PWM 정책의 해석**: 허용 2 / 3과 허용되지 않는 정책, 인버터 에너지가 가장 낮은 정책(−5.985 kJ, −6.4 %), 모터+인버터 에너지는 구간이 겹치면 순위를 매기지 않음, 정책별 에너지 ↔ 위상 여유·펄스 비·T_j의 맞바꿈, 정책마다 한계별 여유와 가장 빠듯한 항목(전류 루프 위상 여유 12.1°) |

### 요구 판정·구동 성능

| | |
|---|---|
| ![id–iq 제약 지도](docs/screenshots/idiq_map.jpg) | ![상 파형](docs/screenshots/waveforms.jpg) |
| **id–iq 제약 지도**: 전압 타원(명령 예산·하드웨어 상한), 전류원, 선언 도메인, DC 방전/충전 한계, 등토크선, MTPA, MTPV(참고), 요구 토크 곡선, 최소전류 정책점, 전기적/DC 포함 가능 영역 | **상 파형**: 역 Park로 복원한 상전류, 상/선간 전압과 명령 예산, SVPWM 상 듀티(min-max 영상분, 평균값 모델)와 전압 reserve 대역, 쇄교자속, p(t) = P_ac 확인 |
| ![벡터도](docs/screenshots/phasor_hexagon.jpg) | ![시스템 개요](docs/screenshots/system_overview.jpg) |
| **dq 벡터도 · 공간벡터 육각형**: e₀ = ω_eψ_PM, ω_eL_d·i_d(약계자 전압), −ω_eL_q·i_q, R_s·i, v와 전류각·φ·역률·자석/릴럭턴스 토크 분해 | **시스템 개요도**: 배터리–메인 릴레이(프리차지)–DC 링크–능동 방전–3상 브리지–모터–축, 운전점의 P_dc·I_dc·I_ph·V_LL·토크·효율과 전력 흐름 방향 |
| ![T–n 성능 곡선](docs/screenshots/envelope.jpg) | ![효율 맵](docs/screenshots/efficiency_map.jpg) |
| **T–n 성능 곡선**: 최소전류 정책(DC 한계 포함) vs 전기적 곡선, 활성 제약별 색(전류 · 전압 · DC 방전/충전), 비교 Vdc, 구동·회생 사분면 | **효율·손실 맵**: 최소전류 정책점 기준 η(방향별 정의), 정책 경계·전기적 한계, 기저속도 곡선, DC 한계 위반·미상 손실 칸(빗금), 최고 효율점 |
| ![운전 궤적](docs/screenshots/trajectory.jpg) | ![역설계](docs/screenshots/decision_inverse.jpg) |
| **운전 궤적**: 속도가 오르며 MTPA에서 전압 타원을 따라 약계자로 이동하는 경로, DC 한계 위반점 | **FAIL → 무엇을 바꿔야 하나**: 450 V에서 필요조건으로 불가능이 증명된 요구(인버터를 키워도 해결 안 됨), capability vs Vdc 역설계 — 국소 경계 497.7 V. 인버터 전류 역설계·병목·완화·시나리오 비교 탭 |

### 요구 묶음·후보

| | |
|---|---|
| ![요구 묶음](docs/screenshots/requirement_set.jpg) | ![후보 × 요구](docs/screenshots/requirement_candidates.jpg) |
| **요구 묶음**: 요구 5건을 같은 제품·조건·근거로 한 번에 판정 — 판정, UNKNOWN 원인 분류, 여유, 모델 없이 되는 사양 필요조건, 제한 원인, 다음 자료. 선택한 요구의 상세는 요구 → 조건·데이터 수준 → 결론·여유 → 제한 원인 → 바꿀 항목 → 다음 자료 순서 | **후보 × 요구**: 설계 변경안마다 모든 요구를 다시 판정(개선 ▲ / 악화 ▼ — 충전 한계 150 kW는 REQ-B를 풀고, 전류 250 A는 네 요구를 깸). 가중 점수·비용 최적 없음 |

### 전력·열·효율

| | |
|---|---|
| ![열 → 토크 가용성](docs/screenshots/thermal.jpg) | ![반복 부하](docs/screenshots/thermal_repeated_load.jpg) |
| **열 → 토크 가용성**: 지속시간별 가용 토크와 노드 온도 궤적(노드별 냉각수 기준 온도 표시), 냉각수 입구·유량·부동액 물성. 검증되지 않은 열모델은 스크리닝(UNKNOWN 유지) | **반복 부하·고온 시작**: 450 N·m 8 s / 50 N·m 20 s를 40주기 — 첫 한계 4.24 s, 주기 정상상태(고정점)의 노드 최고온도와 여유, 허용 펄스 시간·토크(구간 안 최고온도 기준), 반복 전 필요 휴지 |
| ![열 회로망](docs/screenshots/thermal_network.jpg) | ![모듈 손실](docs/screenshots/power_module.jpg) |
| **열 회로망**: Foster(병렬 RC 직렬)·Cauer(사다리) 회로도와 냉각수 순환(라디에이터·펌프 → 인버터 냉각판 → 모터 워터재킷, 각 지점 온도) | **데이터시트 모듈 손실**: 소자별 도통·스위칭 손실, 토크에 따른 인버터 손실(모델 비교), 소자별 T_j — 표 밖은 외삽하지 않고 UNKNOWN |
| ![DC-link 리플](docs/screenshots/power_ripple.jpg) | ![열 사이클·수명](docs/screenshots/power_life.jpg) |
| **DC-link 리플**: 스위칭 주기의 커패시터 전류, 전류 스펙트럼과 ESR(f) 손실, 리플 전압, 커패시터 열·수명 게이트 | **열 사이클·수명**: 미션 → 소자별 T_j 이력 → rainflow ΔT_j 분포, 조건부 손상(공급사 사이클 모델이 없으면 UNKNOWN) |
| ![효율 원장](docs/screenshots/efficiency_ledger.jpg) | ![경계별 효율 지도](docs/screenshots/efficiency_boundaries.jpg) |
| **다섯 경계 효율·손실 원장**: 포트 전력 P_dc → P_ac → P_m → P_o, 확정 손실과 미상 항목, 모터 PWM 동손(R_dc 하한)과 Fe+PM HF 상한(점선 — 값이 아니라 구간의 끝), 경계별 η와 PWM을 포함한 η 구간 | **경계별 효율 지도**: 인버터 · 모터 · 인버터+모터 · eDrive η 지도(구동/회생 방향별 정의, 미상 칸 빗금, clamp 없음) |
| ![미션 에너지](docs/screenshots/efficiency_mission.jpg) | ![모듈 A/B](docs/screenshots/module_ab.jpg) |
| **미션 에너지**: 구간별 포트 전력, 포트별 E+ / E−, 방향별 에너지 효율(구동과 회생을 따로 — 순 에너지 비는 효율이 아님) | **모듈 A/B (IGBT vs SiC)**: 고정 정책 vs 설계별 정책, 운전점별 손실과 T_j(T_j는 결과), 선언된 오차 예산을 넘을 때만 우열 |

### 제어·EMC

| | |
|---|---|
| ![가변 PWM](docs/screenshots/pwm_policies.jpg) | ![타이밍·전환](docs/screenshots/pwm_timing.jpg) |
| **가변 PWM 정책 비교**: 같은 궤적에서 고정 10 kHz · 경부하 8 kHz · 열 fallback 6 kHz — fsw 스케줄(× 요청 ≠ 파형 fsw), 에너지 구간 [확정, 확정 + PWM 동손 · Fe+PM 상한](겹치면 UNDECIDED), 필수 제약(T_j · 피크 전류 상한 · 커패시터 전류 · 위상 여유) | **타이밍·전환**: 지연 원장 → 캐리어 주파수별 전류 루프 위상 여유, fsw가 바꾸는 전류 루프·기계 모드 위상, shadow vs 즉시 reload(카운터 수준 검사) |
| ![Anti-jerk](docs/screenshots/antijerk.jpg) | ![전도성 EMI](docs/screenshots/emi_spectrum.jpg) |
| **Anti-jerk (2관성 드라이브라인)**: 20 → 150 N·m tip-in에서 off · 성형 · 피드백 · 결합 비교 — 실제 토크, 차량 가속도, jerk, 축 토크. jerk·정착 요구는 결합만 FEASIBLE | **전도성 EMI**: 스위칭 순서 소스 → CM/DM 경로(CM 귀환 네 모델) → 수신기 추정(직사각 IF 포락)과 상한(선합·가우시안 IF) vs 한도(설계 여유 6 dB), 연속 대역 정확 열거, 대역별 필요 감쇠(CM/DM 지배). 스크리닝은 PASS가 아님 — 여기서는 3.8 MHz에서 최대 63.0 dB 초과 예측(상한 68.9 dB)으로 UNKNOWN |

### 안전·보호

| | |
|---|---|
| ![FTTI](docs/screenshots/ftti.jpg) | ![배터리 차단](docs/screenshots/dclink_overvoltage.jpg) |
| **FTTI 체인**: 고장 → 검출 → 확인 → 반응 → 안전 상태의 Gantt(최소/명목/최대), 중복 예산 검출, 보장 상한 경로와 FTTI 여유, 안전 종점의 종류 | **회생 중 배터리 차단**: 릴레이 개방(빨강)과 회생 전력이 커패시터로만 들어가는 경로, 아래에 V(t)와 허용 반응 시간 |
| ![패시브 방전](docs/screenshots/passive_discharge.jpg) | ![안전 상태](docs/screenshots/safe_state.jpg) |
| **패시브 방전**: 블리더 R_p 설계 창 — 목표 전압까지의 시간 vs 상시 손실, 방전 회로와 V(t) | **안전 상태 (ASC / Freewheel)**: 같은 운전점(12,000 rpm · 600 V)에서 두 회로의 전류 경로, 프로젝트 규칙(물리와 분리된 계층). 속도별 전류·토크·역기전력 곡선과 판정 표 탭 |
| ![보호 타임라인](docs/screenshots/protection_timeline.jpg) | ![ASC 과도](docs/screenshots/asc_transient.jpg) |
| **보호·고장 인과 궤적**: 임계값·디레이팅·고장 반응을 하나의 궤적에서 — DC-link 전압, 경고·고장·차단 임계값, 필터·확인 지연을 거친 반응 시각 | **ASC 고장 과도**: 운전점 → 단락 → ASC 정상상태의 상전류(비선형 자기 모델이 없으면 선형 스크리닝), 고객의 두 전류-시간 요구(피크·RMS)와 판정 |
| ![고장 파형](docs/screenshots/fault_waveforms.jpg) | ![고장 타임라인](docs/screenshots/fault_timeline.jpg) |
| **고장 시뮬레이션 파형**: 고장 주입부터 검출·반응·안전 상태까지 참값(축 토크·상전류·DC-link·배터리 전류)과 측정·감시기 추정, 요구 허용 창과 감시기 창 | **사건 타임라인**: 출처별 사건(고장·검출·반응 명령·실제 브리지 상태)과 FSR별 FDTI·FRTI·FHTI, 예산·FTTI 대비 |
| ![반응 후보 비교](docs/screenshots/fault_candidates.jpg) | ![고장 캠페인](docs/screenshots/fault_campaign.jpg) |
| **반응 후보 비교**: 같은 초기 조건·같은 고장에서 보호 적용/미적용·ASC-low·ASC-high·6SO·토크 0과 선언된 반응 전략의 축 토크·최대 상전류·최저 i_d·제동 토크·V_dc와 종합 판정, 전략이 실제로 밟은 단계 — 모든 요구를 만족하는 후보가 없으면 "실행 가능한 안전 반응 없음" | **캠페인**: 실행별 종합 판정 지도와 판정이 바뀌는 구간(경계 이분), 물리량별 최악값과 그 실행(서로 다른 실행의 합은 상한일 뿐), 탐색 집합의 범위 |
| ![반응 전략 편집](docs/screenshots/fault_strategies.jpg) | ![설계 편집](docs/screenshots/fault_design_editor.jpg) |
| **반응 전략 편집**: 단계(동작 + 종료 조건 + 최대 시간)와 단계 매개변수, 대체 상태, 대표 방법 템플릿(소프트 ASC 두 방식, 상별 순차 ASC, FW → ASC, ASC → 6SO, Vdc 히스테리시스, 토크 램프) — JSON 없음 | **보호 설계·안전 요구 편집**: 메커니즘의 주기·임계값·디바운스·잠재 고장 시험을 표와 칸에서, 프로젝트 대비 변경이 곧 실행되는 설계 변형(바꾸면 결과가 이전 입력이라고 표시) |
| ![검증 매트릭스](docs/screenshots/fault_verification_matrix.jpg) | ![안전 근거](docs/screenshots/fault_safety_case.jpg) |
| **검증 매트릭스**: 대표 시나리오를 한 설계로 실행한 요구 × 시나리오 판정, 요구별 발동·실패 수, FSR별 최악 FDTI/FRTI/FHTI와 그 시나리오 | **안전 근거 (심사)**: 정적 설계 검토(모순·누락·경고)와 검출 지연 범위, 검증 매트릭스의 실패·미발동 요구를 한 해석으로 — 보고서는 HTML 한 파일 |

### 시스템·설계

| | |
|---|---|
| ![OEW](docs/screenshots/oew.jpg) | ![HEV](docs/screenshots/hev.jpg) |
| **OEW 듀얼 인버터**: 두 브리지의 전압 집합(육각형), 공통 bus / 분리 bus 비교, 영상분 제어. 운전점·포트 회계·쌍 안전 상태·i0 리플 탭 | **HEV 결합 토크 집합**: 두 기계(EM1 발전 · EM2 구동)의 동시 토크 가능 영역(공통 bus 전력·부스트 한계), 가지 전력 vs 순전력. 크랭킹 replay·부하 차단·유성기어 탭 |
| ![모터 스케일링](docs/screenshots/machine_trade.jpg) | ![권선](docs/screenshots/machine_winding.jpg) |
| **모터 설계 — 스케일링 트레이드**: 기준 모델 주변의 턴·병렬 회로·적층·자석 스케일링 후보를 같은 요구 여유로 비교(T–n 곡선, 요구별 여유 표), 계보와 무효화되는 데이터 | **권선 star of slots**: 48슬롯 8극(q = 2) — 슬롯 기전력 페이저, 권선계수·고조파, 상 배치(A/B/C), 평형·병렬 회로 |

### 제품 데이터·검증

| | |
|---|---|
| ![프로젝트](docs/screenshots/project.jpg) | ![데이터시트 값 입력](docs/screenshots/datasheet_entry.jpg) |
| **프로젝트 데이터 패키지**: 한 제품의 섹션(drive · dc_source · module · dc_link · 제어기 · 열망 · 안전 · EMI set-up)과 digest·출처·개정, 일관성 검사, 개정 비교와 stale 표시 | **데이터시트 값 입력 (모터)**: 극수 · Ke · R · L 등 대표값 → 선언된 구성 규칙으로 모델(단위 변환·규칙 기록), 실시간 미리보기(무부하 역기전력 vs Vdc, MTPA·특성 전류) |
| ![데이터시트 곡선](docs/screenshots/datasheet_import.jpg) | ![검증](docs/screenshots/verification.jpg) |
| **데이터시트 곡선 가져오기**: 디지타이즈한 모듈 도통·스위칭 에너지 곡선(25 / 150 °C) → 공급사 provenance가 붙은 프로젝트 섹션, 외삽 없음 | **검증 (V&V)**: production vs golden acceptance 21/21(오차 / 허용오차), 참조 패키지 SHA-256, 알려진 한계 |

그래프는 production 모델 값을 그대로 다시 표현한 것입니다(새 물리 없음). 파형·듀티는 스위칭 리플·데드타임이 없는 평균값 모델이며
그림과 보고서에 그렇게 표기됩니다. 테스트가 역변환·전력 항등식·MTPA 접선 조건·기저속도 = 약계자 개시점을 독립적으로 확인합니다.
모든 값은 합성(synthetic) 예제 데이터의 결과이며 하드웨어로 검증된 제품 수치가 아닙니다.

## 무엇이 다른가

- **판정 항목을 섞지 않습니다.** 전기적 해의 존재 / 최소전류 정책의 정적 달성(DC 한계 포함) / DC 소스 한계 / 임의 제어로의 가능성(진단) / 지속시간 / 요구 전체(AND 집계)를 각각 FEASIBLE·INFEASIBLE·UNKNOWN과 근거(evidence)로 보고합니다.
- **INFEASIBLE은 증명이 있을 때만.** 상수 모델은 제약 다항식 근 전수 열거(exact enumeration), flux map은 셀 구간 경계(branch & bound), 공통으로 해석적 필요조건(예: 축 출력 > 방전 한계, 최대 손실로도 충전 한계 미달, d축 전압 하한 > 예산)을 사용합니다. Solver가 해를 못 찾은 것은 UNKNOWN(NUMERICAL_UNRESOLVED)입니다.
- **Capability는 달성값과 증명된 반대쪽 상한을 분리합니다.** 구동 capability는 Lagrangian 오목 상한(상수 모델) 또는 셀 경계(flux map)로 certified, 회생 경계는 최소전류(에너지 회수) 정책 경계로 표본 증거와 함께 보고하며 의도적 손실 증가 운전은 채택하지 않습니다.
- **입력을 조용히 채우지 않습니다.** 단위·정의(peak/RMS, 상/선간, 기계/전기 속도, per-phase/line-to-line, Ke/Kt convention)가 모호하면 계산 전에 INVALID_INPUT, 누락된 손실/온도/지속시간 근거는 UNKNOWN으로 남깁니다. 모든 변환은 기록됩니다.
- **요구를 바꾸지 않습니다.** 원문 보존, 토크 clip 없음, Vdc 범위 요구는 표본점 통과만으로 PASS가 아니며(SAMPLED_COVERAGE — 단조성 인증서의 조건이 성립할 때만 범위 전체 입증), 지속시간이 없으면 정적 항목으로만 해석합니다.
- **결측은 무제한이 아닙니다.** 선언되지 않은 DC 한계·손실·열 증거·초기 상태는 UNKNOWN이고, 무제한은 명시적으로 선언해야 합니다(∞). 스크리닝(EMI, 안전 상태, 합성 데이터)은 PASS로 승격되지 않습니다.
- **모든 witness는 같은 gate를 통과합니다.** 수치 residual·진단값·표본점은 원 요청으로 재검증된 witness가 아니면 증거가 아닙니다. 수학 · 모델 · 요구 · qualification 층은 따로 보고합니다.
- **효율은 경계와 방향을 밝힙니다.** η는 선언된 포트 사이에서만 정의되며, 혼합 흐름은 N/A, 미상 손실은 UNKNOWN, η > 1은 clamp 없이 INCONSISTENT로 남깁니다. 감속기 데이터가 없으면 eDrive η는 100%가 아니라 UNKNOWN입니다.

## 대표 결과 (합성 fixture, 앱의 “예시 질문” / `twb demo`)

| 요구 | 판정 | 핵심 근거 |
|---|---|---|
| 12,000 rpm · 150 N·m · 600 V | **PASS** (정적) | 전류 여유 204 A지만 토크 capability 여유는 **2.555 N·m** — 한계는 전압·DC 방전 전력 (certified 152.555 N·m) |
| 같은 요구 · 450 V | **FAIL** | 필요조건 증명: \|v_d\| ≥ 255.54 V > 예산 246.82 V, 축 출력 188.5 kW > 450 V×400 A. 인버터 전류를 1200 A로 키워도 해결 불가, **Vdc ≥ 497.7 V** 필요 (전압 + DC 전류 공동 병목) |
| 같은 요구 · 10초 유지 | **UNKNOWN** | 전기적으로 가능하나 검증된 10초 rating/열모델 없음 (UNVALIDATED_DURATION) |
| 12,000 rpm · −80 N·m 회생 | **PASS** | 에너지 회수 회생, 충전 한계 내 |
| 12,000 rpm · −100 N·m 회생 | **FAIL** | 최대 손실(600 A)로도 P_dc ≤ −111.3 kW < −100 kW: 배터리 수용 한계 |
| 6,000 rpm · 350 N·m | **FAIL** | 손실 0이어도 축 출력 219.9 kW > 방전 200 kW |
| Vdc 550–650 V 전 구간 · 100 N·m | **PASS** | 저전압 끝점 + **단조성 인증서**(정적 순구동, Vdc 무관 손실, 고정 소스 한계)로 전 구간 입증 — 표본점 통과만으로는 PASS가 아님 |
| 정지 · 300 N·m | **PASS** (정적) | 등가 정현파 RMS, 효율 N/A, 정지 열 지속시간 추론 없음 |

## 개발·빌드

```bash
pip install -e '.[gui,test]'          # numpy, scipy + PySide6-Essentials, matplotlib (+ pytest)

twb gui [--open 작업.twb-workspace.json]   # 데스크톱 앱 (= traction-workbench); case JSON이면 판정, 작업 공간이면 복원
twb evaluate examples/cases/req_ts_012_450V_sizing.json --out out/   # 의사결정 기록 JSON + Markdown
twb report   examples/cases/req_ts_012_450V_sizing.json --pdf out/report.pdf   # 그래프 포함 PDF 보고서
twb demo | solve | forward | capability | curve | acceptance
twb selftest out/selftest             # 모든 페이지 headless 자체 검사 (스크린샷 + selftest.json)
twb exchange out/exchange.json        # MathWorks 이식용 교환 패키지: 규약·식별자·도메인·fixture (구현 검증, 물리 검증 아님)
twb project show|check [P.json]       # 프로젝트 데이터 패키지: 식별(섹션 digest·provenance) / 섹션 간 일관성 (기본: 내장 합성 프로젝트)
twb project diff A.json B.json        # 개정 비교: 바뀐 섹션·경로와 영향받는 분석
twb project export out/project.json   # 내장 합성 프로젝트를 편집용 파일로
twb reqset --template reqs.csv        # 요구 묶음 CSV 템플릿
twb reqset reqs.csv --candidates c.txt --out res.csv [--project P.json] [--exit-code]   # 요구 묶음 판정 + 후보 × 요구
twb datasheet examples/datasheets/module_example.json --out out/p.json --revision B   # 데이터시트 → 프로젝트 섹션
twb datasheet examples/datasheets/motor_example.json --json   # 대표값 사양 (모터·module_representative·capacitor_representative)
twb mathworks export out/mw [--project P.json]   # MathWorks 이식 패키지 (twb-mathworks/1)
twb mathworks run out/mw [--runtime octave]      # 로컬 MATLAB(-batch) / GNU Octave에서 twb.runAll → 보고서 재수입
twb mathworks verify out/mw --project P.json     # 대상 보고서 재수입: 이 패키지·이 설계 개정에만 연결, case 재계산

python verification/independent_fixture_check.py      # production 코드를 쓰지 않는 독립 검산
QT_QPA_PLATFORM=offscreen python -m pytest -q
python verification/local_ci.py --parallel 3          # CI workflow의 세 job을 로컬에서 (커밋의 깨끗한 clone, job별 새 Python 3.12 환경)

# 실행 파일 (Windows: packaging\build_windows.bat)
pip install -e '.[gui,build]'
python packaging/build.py             # dist/TractionWorkbench/ + zip; 동결 앱에서 acceptance와 self-test 실행
```

CI(`.github/workflows/build.yml`): Linux에서 독립 검산 + 전체 테스트, **MathWorks 이식 패키지를 GNU Octave로 실행**(MATLAB 코드의 parity·
심은 결함 검출·guard — MATLAB/Simulink 단계는 NOT_RUN으로 남음), Windows에서 PyInstaller 빌드 → **동결된 exe로 acceptance·self-test** →
zip 아티팩트 업로드(`v*` 태그면 GitHub Release에 첨부).

## MathWorks 이식 (`twb-mathworks/1`)

Python Workbench를 **실행 가능한 reference 구현**으로 보고, 그 모델·파라미터·시나리오·계산 의미·결과를 MATLAB / Simulink /
System Composer에서 같은 의미로 재현하게 하는 패키지입니다(프로젝트 페이지 → *MathWorks 이식 패키지…*, 또는 `twb mathworks`).
세 층을 분리합니다: **(1) 요구·수용된 원천**(참조 패키지 golden, 계약 수식, map 정의) · **(2) Python reference** · **(3) MathWorks**.
각 비교 case는 (2)의 값과 (1)의 oracle 값을 함께 가지며, 대상은 둘 모두와 비교됩니다 — Python을 절대 기준으로 보지 않습니다.

![MathWorks 이식 패키지 — GNU Octave로 실행한 뒤의 단계별 상태](docs/screenshots/mathworks_package.jpg)

- **옮기는 것**: 프로젝트(twb-project/1)와 교환 패키지(twb-exchange/1)를 그대로, 모델 데이터(단위가 키에 있는 파라미터, 온도 법칙, flux
  plane·mask·축, 손실 closure와 **손실 소유권**, 모델 content SHA-256·provenance), 규약(검사 가능한 열거값), 물리량 사전·tolerance class·
  상태 어휘, 비교 case(forward 48 · flux lookup 21 · 요구 witness 12: ±속도·구동/회생·정지·전압/전류/DC/도메인 경계 ±0.5/±2 tol·
  map 구멍·모서리·축 밖·온도 plane·q-odd), gap report, System Composer/SLDD 후보(stable ID), 사람용 이식 안내와 agent 작업표.
- **MathWorks에서 재현되는 것**: native MATLAB `+twb` — 정상상태 dq forward 평가(상수 dq·flux map, 제약 상태, 에너지 모드, 항등식,
  evidence gate), plane 선택·온도 보간, 요구 witness 재검사(FEASIBLE claim), `twb.runAll`(preflight → 패키지 검사 → parity → guard →
  Simulink가 있으면 정적 평가 harness). 최적화기·인증서·열·PWM 등은 데이터·근거 수준으로만 전달(gap report).
- **같은 의미인지 확인하는 방법**: `|q_t − q_r| ≤ atol + rtol·max(|q_t|,|q_r|)`(물리량 class별) + 상태의 완전 일치, 대상 결과를 Python이
  **다시 계산**해서 판정(보고서의 자기 판정을 믿지 않음), 심은 결함(토크 계수·모서리 규칙·clip·결측=무제한·ACTIVE=위반)이 잡히는지 CI가 확인.
- **연결 유지**: semantic fingerprint와 소비 파일 SHA-256으로 보고서를 그 패키지에만, project digest로 그 설계 개정에만 연결(다른 개정은
  stale). 생성 파일을 편집하면 재생성이 멈추고, 회사 소유물(profile·architecture·dictionary·상세 모델)은 패키지 밖에 둡니다.
  자세한 내용: 패키지의 `README.md`, [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md) 0.5.0, [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) 11절.

## 저장소 구조

```
reference/traction_workbench_spec_v1/   불변 설계 기준선 + golden JSON (manifest SHA-256)
src/traction_workbench/
  models/ physics.py solvers/ analysis/ extensions/ decision.py report.py io.py units.py   ← 엔진 (numpy, scipy)
    models/                드라이브 모델: 모터·인버터·자속, 데이터시트 모듈 손실(module_loss.py — 커널이 평가, 2차 surrogate와 배타)
    physics.py             커널: 정방향 평가·제약, 손실 계약(loss_kind · i2_dc · pointwise_loss) — DC 논증의 분기는 여기 한 곳
    solvers/gate.py        공통 witness gate (모든 경로)
    analysis/              역설계·병목·불확실성·정격, efficiency.py(다섯 경계·감속기·미션·모듈 A/B), machine_design.py,
                           source.py(배터리 OCV + Thevenin R_eq → 단자 전압)
    extensions/            timing·dclink·safe_state·thermal·coolant (스크리닝), dclink_ripple·lifetime (P1-A),
                           protection·asc_transient (P1-B), emi (P1-C), oew·hev, pwm_policy (가변 PWM), driveline (anti-jerk),
                           thermal_cycle (반복 부하·고온 시작)
  exchange.py    교환 패키지 twb-exchange/1 (규약·fixture) — 이식 패키지가 그대로 포함
  mathworks/     MathWorks 이식 패키지 twb-mathworks/1: contract(규약·물리량 사전), oracle(엔진 비의존 층-1 값), cases, architecture
                 (System Composer/SLDD 후보), package(export·check·run·verify), matlab/+twb (native MATLAB 코드)
  datasheet.py   데이터시트 가져오기 (디지타이즈 곡선·대표값·ESR·dv/dt·모터 특성값 → 프로젝트 섹션, 구성 규칙은 기록으로)
  project.py     프로젝트 데이터 패키지 (twb-project/1): 섹션 검증·digest·일관성 규칙·개정 비교·결과의 사용 기록
  requirement_set.py  요구 묶음·후보 판정 (CSV, 해석 확인, 사양 필요조건, UNKNOWN 원인 분류, 다음 자료 우선순위)
  examples.py    내장 합성 프로젝트 — 모든 페이지 예시의 제품 데이터 출처 (api.example(name, project)로 합성)
  modulation.py  변조 법칙 하나 (SVPWM·SPWM·DPWM1 듀티·영상분) — 손실·EMI·리플·샘플링·그림이 공유
  validation.py  입력 숫자·구간·축 검증 (모든 층이 공유)
  viz/           그래프 데이터: 파형·벡터도·육각형·전력 흐름 / 스윕·곡선 / 맵·기저속도 / 설계 / 스크리닝·Z_th 곡선
  plots/         matplotlib 그림과 회로 개요도·열 회로도 (앱과 PDF 보고서 공용)
  desktop/       PySide6 앱: main_window, pages/, 열 모델 표 편집기, 백그라운드 작업, self-test
  report_pdf.py  PDF 엔지니어링 보고서
  api.py service.py cli.py
packaging/       PyInstaller spec, launcher(TractionWorkbench.exe + twb.exe), build.py, 아이콘
verification/    independent_fixture_check.py (production 비의존), make_report.py, make_module_anchor.py (모듈 모델 코어 경로 회귀 기준),
                 local_ci.py (GitHub Actions를 쓸 수 없을 때 같은 job을 로컬에서)
tests/           골든·의미론·검증·리뷰 재현(P0-A/B)·확장·P1·OEW/HEV·EMI·효율·PWM·드라이브라인·모터 설계·교환·보고서·데스크톱,
                 아키텍처(층 base < models < kernel < engines < services < presentation, 지연 import 포함·비공개 결합·순환)
examples/        case 파일, 단위가 선언된 drive 정의, datasheets/ (곡선·대표값·모터 spec 예시 — 가상 부품)
docs/            RELEASE_NOTES.md (모델 계약·한계), TRACEABILITY.md (리뷰·추가 명세 추적표), VERIFICATION_REPORT.md,
                 STATUS.md (현재 상태·남은 일·이어서 시작하는 법), screenshots/ (make_screenshots.py로 self-test 화면에서 갱신)
```

## 검증 상태

- 참조 패키지 10개 파일 SHA-256 일치, 독립 검산 136/136, production vs golden 21/21, pytest 1022 통과, 데스크톱 self-test 101/101.
  CI 세 job(Linux 테스트 · MathWorks/Octave · Windows 동결 exe의 acceptance·self-test) 통과 — 상세: [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md)
  (보고서 머리에 생성 commit 표기)
- MathWorks 이식 패키지: 패키지의 MATLAB 코드를 **GNU Octave 8.4**(MATLAB 언어 호환 proxy)로 실행해 80 PASS · 0 FAIL · 0 ERROR ·
  1 NOT_SUPPORTED, guard 6/6, 심은 결함 9종 모두 검출. **MATLAB 본체·Simulink·System Composer는 실행하지 않았습니다**(NOT_RUN).
- **합성 fixture에 대한 verification입니다.** 하드웨어·공급사 데이터·외부 시뮬레이터 validation은 수행하지 않았습니다(V4–V5 미수행).

## 문서

- [`docs/STATUS.md`](docs/STATUS.md) — **현재 상태와 이어서 할 일**: 완료된 것, 남은 것(코드·자료·CI), 이어서 시작하는 법
- [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md) — 변경 사항, 실행 방법, model contract, 제약 목록, 판정 의미론, 수치 방법, 알려진 한계, 미구현 항목, data provenance, 재현 조건, 스펙 조항 ↔ 구현 ↔ 테스트 추적표
- [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) — 독립 리뷰(F01–F13, P0-B/C, P1, §10–§14)·감사 재현·추가 명세(OEW/HEV, 모듈 효율, 가변 PWM·anti-jerk)·공학 리뷰 6198099(§13)·PWM 인계 P0(§14) 항목별 구현·확인·상태(implemented / partial / missing / evidence_missing)
- [`docs/SYSTEM_REVIEW.md`](docs/SYSTEM_REVIEW.md) — 기능 간 의존성·변경 영향·성숙도(V0–V6)·전체 맥락 검토, 교차 모듈 일관성 규칙, 남은 위험과 권고
- [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md) — 자동 생성 검증 보고서
