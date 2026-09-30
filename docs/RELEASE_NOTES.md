# Traction Workbench v0.5.0 — Release / Handback Notes

기준선: `reference/traction_workbench_spec_v1` (Blueprint, Implementation Handoff, Reference Cases, golden JSON; manifest SHA-256 일치 확인).
이 문서는 Handoff H12가 요구한 실행 방법, model contract, 제약 목록, 알려진 한계, 검증 실행 결과, 실패/미구현 항목, data provenance, 재현 조건을 담습니다.

## 0.5.0 변경 사항 (v0.4.0 대비)

**공학 리뷰 2(63a2b61) 반영** — 본 보고서(F-01…F-19, P3)와 DC 전원·DC-link, 열 하위 보고서의 지적을 현재 헤드에서 다시 재현하고
판단해 반영했습니다(상세: `docs/LOGIC_REVIEW.html` §19, [`TRACEABILITY.md`](TRACEABILITY.md) §16, 회귀 시험
`tests/test_engineering_review_63a2b61.py`). 바뀐 판정은 모두 "증명할 수 없는 것을 결론으로 쓰지 않는다"는 방향입니다.

1. **DC 전원 결합** — 배터리 OCV + Thevenin의 단자 전압을 수동성 상한 V_hi에서 시작하는 단조 반복으로 풂. 회생이 풀리고(−203.72
   N·m: 거짓 FAIL → 604.04 / 611.99 V에서 PASS), 구동에서 전원이 감당할 수 없는 요구는 증명된 FAIL(350 mΩ). 해가 없을 때 드라이브
   쪽은 V_hi에서 보여 주고 거기서의 위반은 결정적이지 않음.
2. **판정 엔진** — 자속맵 정책을 결과로 인증(최소전류 구간 양 끝이 같은 판정이면 구간 전체; UNKNOWN 띠 제거). 온도를 말하지 않은 요구는
   선언 법칙의 모든 온도에서 판정(구동은 최대 R_s 인증서, 그 외 표본) — 판정 폼에 권선 온도. DC 한계로 정해진 여유에 손실 예산과
   파라미터 손익분기를 자동 표시(판정 불변). 조건을 밝히지 않은 rating envelope은 판정에 쓰지 않음(APPLICABILITY_UNCONFIRMED).
   구간 분석의 꼭짓점 인증서(모든 모서리 통과 → 연속 박스 전체 FEASIBLE, 공통 운전점이면 적응형도). 충전 한계 경계 위의 제동
   능력 witness를 잃지 않음. 효율 결과는 "모델 효율"로 표시.
3. **열** — 온도 법칙·자석 되먹임이 있으면 지속시간을 반복 부하 엔진으로 결합(400 N·m: 158.5 °C FEASIBLE → INFEASIBLE), 반복
   부하는 지평 전체 계산과 주기 초과를 claim에(500주기 사례: 449주기에서 INFEASIBLE), 검증 안 된 모델의 되먹임 정지는 결정적이지 않음,
   열원 감시는 모든 단계, Heun 되먹임 스텝, 선언된 불확도 대역(`uncertainty_K`) 안은 UNKNOWN, 냉각수 단순화 보고.
4. **DC-link·안전** — 차단 과전압의 반응 경로(freewheel / 미지정 / ASC)와 전압 한계 유입 전력 상한, 음의 허용 시간 대신 증명된 위반,
   ESR 게이트를 에너지 기준으로(결과가 FFT 길이와 무관, 10–30 kHz FEASIBLE), 커패시터 수명은 요구 수명이 있을 때만 판정.
5. **표시·변조** — 물리량 종류별 표시 자릿수(모델 한정 추정값 3자리)와 커패시터 손실·hotspot의 샘플링 분해능. DPWM1을 모든 펄스
   패턴 소비자(EMI 소스, 상전류 리플, 샘플링 창, PWM 정책)가 받음.

**로직 검토 의견(63a2b61) 반영** — `docs/LOGIC_REVIEW.html`에 대한 검토 의견을 재현하고 판단해 반영했습니다(상세: 문서 §18,
[`TRACEABILITY.md`](TRACEABILITY.md) §15, 회귀 시험 `tests/test_review_63a2b61.py`).

1. **결함 4건 수정** — (2.1) 가변 PWM Pareto에서 같은 에너지 불확도 구간이 정책을 지우던 규칙: 구간은 분리될 때만 지배.
   (2.2) 반복 부하의 첫 한계가 노드 목록 순서에 따라 달라지던 것: 모든 노드의 최솟값. (2.3) 단계 내부 최고 온도와 한계 교차가 빠른
   열 모드의 피크를 놓치던 것: 지수합 정류점의 정확한 근 분리(격자 없음). (2.4) Thevenin 결합의 제곱근 정의역 오류: 증명과 근이
   같은 경계 대역을 쓰고, 대역 안은 UNKNOWN(BOUNDARY_WITHIN_TOLERANCE).
2. **열 반복 부하** — 노드가 대표하는 온도를 선언(`temperature_of`: winding / junction / magnet)하고 자석 노드 온도가 운전점 자속을
   정함. 허용값의 손실 상한은 온도 상자의 모든 모서리 최댓값(좌표별 단조면 방향 무관)이며 모서리 중점·중심으로 점검(실패 시 추정).
   주기 수렴은 모든 Foster 항의 잔차로, 매 실행에 해상도 점검(스텝 2배·캐시 ½ — 첫 한계·피크·주기 피크·허용값)을 하고
   판정이 바뀌면 UNKNOWN.
3. **가변 PWM** — 비정수 f_sw/f_e는 요청 주파수를 사이에 둔 두 동기 캐리어를 모두 평가하고 두 답이 같을 때만 판정(민감도 괄호).
   커패시터 ESR 손실을 에너지 비교 경계 안으로 선언할 수 있음(판정 화면 체크 박스).
4. **판정의 견고성 층** — 선언한 오차 예산(모델 불일치 / 입력·측정 / 공급 데이터의 수치 오차; 물리량 토크·상전류·DC 전력·DC
   전류·명령 전압; 절대값 또는 모델값의 %; 근거·적용 범위 필수)을 물리량마다 최악 조합으로 합쳐 "모델상 가능"과 "선언 오차를 고려해도
   가능"을 구분(ROBUST / WITHIN_ERROR / NOT_ESTABLISHED / NOT_ASSESSED). 토크는 능력 여유(충족은 찾은 능력치, 불충족은 인증 상한),
   한계는 요구를 만족하는 운전점에서 y + Δ ≤ y_max(약계자점의 전압 한계는 정책이 대응하므로 토크 쪽으로). 모델 판정은 바뀌지
   않음. case 파일의 `error_budget`(예제 `examples/cases/req_ts_012_600V_error_budget.json`), 판정 화면의 "오차 예산" 입력(행마다
   물리량·단위 선택), 판정 배너·해석·Markdown·PDF 기록.
5. **한정자** — 판정 폼이 토크 해석(achieve / band ±)을 직접 받고 "판정할 질문"을 ∃(대역)·∀(Vdc 범위, 다평면 자석 온도)로 보여 줌.
   결과의 "판정한 질문"과 기록의 `quantifiers`에도 같은 의미.
6. **약계자 철손 범위** — witness가 전압 한계에 걸린 운전점이면 자속비 |ψ|/|ψ₀|, 토크·DC 여유, 이 속도의 1 kW ↔ N·m 환산과 오차
   방향을 기록·해석에 표시(합성 기준 모델: 12,000 rpm·150 N·m에서 철손이 없는 회전 손실, 약 3.2 kW면 토크 여유가 사라짐).
   커널의 손실 형태는 바꾸지 않음(증명 구조의 전제), 자속 의존 철손은 다음 단계.


**MathWorks 이식 패키지 `twb-mathworks/1`** — Python Workbench를 실행 가능한 reference 구현으로 보고, 그 요구·모델·파라미터·
시나리오·계산 의미·결과를 MATLAB / Simulink / System Composer로 재현 가능하게 옮깁니다. 새 verification framework가 아니라
기존 구조(twb-project/1, twb-exchange/1, 결정 기록의 claim·evidence, provenance·content hash)를 그대로 싣고 이식에 필요한 것만
더했습니다. 세 층을 분리합니다: (1) 요구·수용된 원천, (2) Python reference, (3) MathWorks — 대상은 (2)와 (1) 모두와 비교됩니다.

1. **무엇을 옮기는가** — `manifest.json`(schema, 구현 SHA·dirty·의미 소스 파일 hash, project digest, 파일별 SHA-256, semantic
   fingerprint: canonical ASCII JSON이라 byte 동일 ⇔ 의미 동일), `contract.json`(검사 가능한 규약 열거값, 물리량 사전의 단위·tolerance
   class, 제약·에너지 모드·사유·claim 어휘, 수치 설정, map·손실 규칙, 세 층), `models/*.json`(제품 드라이브 + 참조·검증 fixture:
   파라미터·온도 법칙·flux plane(행 = id)·mask·대칭·온도 보간·손실 closure·**손실 소유권**·content SHA-256·provenance·자기 qualification),
   `cases/*.json`(forward 48, flux lookup 21, 요구 witness 12 — 예제 case 파일마다 1건, 각 case에 Python 값과 oracle 값·tolerance), `architecture/`
   (System Composer/SLDD 후보와 회사 profile 양식), `source/`(프로젝트, 교환 패키지, 참조 패키지 manifest), `gap_report.json`,
   `matlab/+twb/`, 사람용 이식 안내(`README.md`)와 agent 작업표(`AGENT_TASKS.md`).
2. **MathWorks에서 무엇이 재현되는가** — native MATLAB: 상수 dq(D1)와 flux map(D2)의 정상상태 forward 평가(전압·토크·전력·손실·
   전압 예산·제약 상태·에너지 모드·항등식·evidence gate), plane 선택·선언된 온도 보간, 요구 witness 재검사(FEASIBLE claim의 재현;
   INFEASIBLE/UNKNOWN은 층-2 근거로 전달), 패키지 guard(전치 거부, 이중 온도 보정 거부, 규약 불일치 거부, 손실 이중 계산 검출,
   사전 충돌 상태). Simulink 정적 평가 harness(`twb.buildEvaluationHarness`/`twb.runHarness` — MATLAB Function block이 같은
   `twb.staticPoint`를 호출, 동적 plant 아님), System Composer 후보 모델(`twb.buildArchitectureCandidate`), SLDD 충돌 검사
   (`twb.checkDictionary`, 읽기 전용)는 문서화된 API로 작성했고 해당 제품이 있는 환경에서 실행될 때까지 **NOT_RUN**입니다.
   최적화기(MTPA/약계자)·capability 인증서·데이터시트 모듈 손실·열·PWM·anti-jerk·보호·EMI·OEW/HEV는 데이터·계약·근거 수준으로만
   전달합니다(`gap_report.json`).
3. **같은 의미인지 어떻게 확인하는가** — 물리량 class별 `|q_t − q_r| ≤ atol + rtol·max(|q_t|,|q_r|)`(IEEE double, 같은 연산 순서:
   V·A·N·m 1e-9/1e-12, W 1e-6/1e-12, Wb 1e-15/1e-12)와 상태(evaluable·사유·gate 사유·제약 상태·에너지 모드·위반 그룹·claim)의
   완전 일치. Oracle은 엔진을 import하지 않는 모듈이 참조 패키지(golden_forward, manufactured potential — manifest SHA-256 확인)와
   계약 수식, map의 집합 정의로 계산합니다. 내보낼 때 (2)↔(1) 불일치는 보고되고 숨겨지지 않습니다(현재 0건). 대상 보고서는 Python이
   **원시 값에서 다시 계산**해 판정합니다. 비교에 이빨이 있는지 CI가 확인합니다: MATLAB 코드에 심은 결함 — 토크 계수, 모서리 규칙
   제거, UNKNOWN 대신 clip, 결측 DC 한계 = 무제한, ACTIVE = 위반, map 전치, power-invariant Park, 온도 보간 제거, Rs 법칙 무시 — 이
   모두 그 결함을 겨냥한 case에서 FAIL/ERROR가 됩니다. 이 환경과 CI에서 GNU Octave 8.4(MATLAB 언어 호환 proxy)로 **80 PASS · 0 FAIL ·
   0 ERROR · 1 NOT_SUPPORTED**(witness가 없는 INFEASIBLE 요구), guard 6/6. MATLAB 자체·Simulink·System Composer는 실행하지 않았습니다.
4. **작업이 System Composer → Simulink → Simscape로 발전해도 연결을 어떻게 유지하는가** — 보고서는 semantic fingerprint와 소비한 모든
   파일의 SHA-256을 기록하고, 재수입은 그 패키지에만(다른 패키지 → FOREIGN_REPORT), 그 설계 개정에만(project digest가 다르면 현재
   근거가 아님) 연결합니다. stable ID(부품·연결·signal·데이터 항목·요구·case)와 회사 profile의 명시적 `id_map`으로 이름이 바뀌어도
   추적하며, 이름만 같은 항목은 같은 것으로 보지 않습니다(NAME_MATCH_ONLY). 생성 파일을 편집하면 재생성이 멈추고(편집 목록 보고),
   생성기는 새 폴더에만 쓰며 사라진 ID를 삭제 명령으로 해석하지 않습니다. 같은 case 집합이 상세 모델의 회귀 기준이 되며, 상세 모델의
   차이는 fidelity 비교(같은 입력·초기조건·손실 경계, 설명된 discrepancy)로 기록합니다.

사용: 프로젝트 페이지 → *MathWorks 이식 패키지…*(만들기 · 로컬 실행(MATLAB `-batch` 또는 Octave) · 보고서 가져오기 — 패키지 검사 /
대상 환경 / 모델 생성 / parity / guard / 물리 검증 / 현재 근거를 따로 표시), CLI `twb mathworks export|check|run|verify`, 회사 PC에서는
`addpath(fullfile(pkg,'matlab')); twb.runAll(pkg)` — AI·네트워크 불필요. Agentic Toolkit은 같은 결정적 entry point를 호출하는 선택적
orchestration입니다(`AGENT_TASKS.md`: 단위·map·초기상태·tolerance를 지어내거나 기대값을 고쳐 통과시키는 것 금지).

이식 fixture가 찾은 엔진 결함: 여러 온도 plane을 가진 flux map에서 자석 온도를 주지 않거나 plane 밖 온도를 주면(plane 선택 불가)
policy·physical capability와 T–n 곡선이 torque scale을 없는 plane에서 계산하다 `AttributeError`로 죽었습니다. 이제 모델의 plane들로
scale을 잡고, 결과는 사유(자석 온도 필요)를 밝힌 비증거(UNKNOWN)입니다 —
`test_scenario_that_selects_no_plane_is_unknown_not_a_crash`.

**엔지니어링 분석과 사용성 (UX 검토 반영, [`UX_REVIEW.md`](UX_REVIEW.md) "반영 현황")** — 결과가 있는 15개 페이지의 41가지
결과에 첫 탭 *엔지니어링 분석*을 더했습니다: 결론 한 줄, 한계를 만드는 메커니즘, 전력·손실·시간·에너지가 어디로 가는지, 여유가 가장 작은
항목, 무엇을 바꾸면 답이 바뀌는지(예: 과전압을 흡수할 C ≥ 2E/(V_lim² − V₁²)), 미확정의 원인과 필요한 자료. 새 패키지
`traction_workbench.insight`(표현 계층)는 결과를 읽기만 합니다: 모든 수치는 결과의 값이거나 결과 수치의 항등식(항을 함께 표시), 새
임계값 없음, 판정은 엔진 판정 그대로, 결과를 바꾸지 않음, 식별자 대신 표시 이름, 한국어 화면에서는 엔진 문장(고정 240개·패턴 87개)을
한국어로(기록·보고서의 원문은 영어 그대로). 판정 페이지는 Markdown·PDF 보고서에도 같은 해석을 싣습니다.
실행과 입력: 모든 페이지 머리 막대의 실행 버튼(Ctrl+Enter, 실행 중에는 그 페이지만 취소 — Esc, 상태 표시줄은 *모두 취소*); 취소하면
페이지를 되돌림(P0: 판정·요구 묶음의 실행 버튼이 꺼진 채 남던 결함); 결과는 계산 당시 입력을 기억해 바뀐 입력(이전 → 지금)을 띠와
배너에 표시하고 되돌리면 지우며, 판정 기록·보고서 저장 전에 확인(P0: 예시를 "저전압 450 V"로 바꿔도 PASS가 남던 것); 작은 창
(1366×700 · 1280×720 · 1100×700)의 열 모델 편집기 겹침 해소(P0); 보이지 않는 탭의 그래프는 열 때 그림(GUI 최대 정지: 판정
1,430 → 104–154 ms, 성능 곡선·맵 884 → 211–319 ms, 3회); R_eq 행은 배터리 OCV일 때만; 창 크기·위치·마지막 페이지 기억; 결과 표의 판정 항목·사유
코드는 표시 이름(코드는 툴팁); 편집 표의 머리글·값은 잘리지 않음(좁으면 가로 스크롤); 그림의 글자를 캔버스에 맞춤(제목 줄바꿈,
판정 '전력·제약'·열 그림의 부제목·범례·주석을 축 폭에 맞춰 — 1280×720에서 16–59 px로 눌리던 그래프 폭이 132–209 px).
테스트: `test_insight.py`, `test_insight_pages.py`(페이지 실제 경로의 37가지 결과를 두 언어로 — 식별자 없음, 영어에 한국어 없음, 결과
불변, 수치 대조), `test_desktop_run_control.py`(취소 후 복원·재실행, 입력 변경 표시·해제·저장 확인, R_eq 행, 창 세션),
`test_figure_words_fit_a_small_window`(작은 캔버스에서 그래프 폭·부제목·제목), 자체 점검
90개(페이지별 해석 점검 포함; 한국어 1600×1000과 영어 1280×720에서 통과).

**작업 공간·작은 화면·긴 계산 (UX 검토 J1·E2·A5, A3 마무리)** — *작업 공간 파일*(파일 메뉴 → 작업 공간 열기·저장·다른 이름으로
저장, `*.twb-workspace.json`, 스키마 `twb-workspace/1`)이 15개 페이지의 모든 입력, 위젯 없이 엔진에 들어가는 페이지 데이터(측정 EMI
트레이스·교정 연결, 모듈 곡선 세트, 후보 계보, A/B 모듈 파일), 열린 탭과 프로젝트를 담습니다. 입력은 화면 위치가 아니라 페이지의 속성
이름으로 저장해 언어나 폼 순서가 바뀌어도 다른 칸에 들어가지 않고, 복원할 수 없는 값(범위 밖, 없는 선택, 열이 다른 표)은 추측하지 않고
이름을 들어 알립니다. 결과는 담지 않습니다(다시 실행하면 같은 입력으로 계산). 사람의 세션은 60 s마다와 종료 때 복구 파일을 쓰고 다음
시작 때 복원할지 묻습니다. 저장하지 않은 작업 공간 파일은 닫을 때 묻습니다. 판정 배너는 결론·요구·사유만 보이고 요구 원문·범위·기록
정보는 [자세히]로 접힙니다. 요약 탭의 표는 모든 행을 보입니다(1280×720: 배너 217–246 → 79–159 px, 판정 층·핵심 수치 0행 → 전체).
긴 계산은 어디까지 왔는지 말하고 바로 멈춥니다. 엔진의 반복(토크 능력 스캔과 경계 이분법, 운전 조건, 역설계, 병목 기여도, 요구 완화,
시나리오 비교, 설계 곡선, 트레이드 스터디, OEW 비교, PWM 선 스펙트럼)이 단계마다 새 기반 모듈 `traction_workbench.progress`로 위치를
알리고 취소를 확인합니다. 상태 표시줄 예: "요구 판정: 1/2 판정 · 토크 능력 130/350 (스캔) · 21 s". 취소 신호는 `BaseException`이라
엔진의 넓은 예외 처리(수치 실패 → UNKNOWN)가 판정이나 결과로 바꾸지 못하고, 끝까지 간 계산이라도 취소된 결과는 버립니다. 자속 지도
판정에서 [취소]는 이전에 판정 계산이 끝나야 반영됐고(3 s에 누르면 26 s 뒤), 이제 스캔 중 0.01–0.06 s 안에 반영됩니다. 요구 판정은 판정을 운전점 그래프와 함께 먼저 보여 주고 PWM 영향·추가
분석·설계 곡선은 뒤이어 채웁니다(저장은 모두 끝난 뒤). 판정 뒤 단계를 취소하거나 그 단계가 실패하면 판정은 남기고 계산되지 않은 항목을
적습니다(예시 8개: 판정이 보이기까지 0.4–1.8 s, 전체 0.5–9.0 s; 먼저 그리는 비용은 실행 간 편차 안). 숨은 탭의 그래프 자리 안내 문구는
그 탭을 열 때 그립니다(계산 중에 그리면 matplotlib 글자 배치가 계산 스레드와 GIL을 다툼; 시작 직후 숨은 그래프 67개를 그리던 약 1 s도 없어짐). PWM 선 스펙트럼은 선 × 에지 행렬 전체(정지 상태에서
24,000 × 2,400)를 한 번에 만들지 않고 블록 단위로 계산합니다(결과는 비트 단위로 같음): 정지 상태 판정 전체 19–28 s → 7 s, 최대 메모리
3.2 GB → 15 MB. 결과 캐시와 남은 시간 예측은 넣지 않았습니다(이유:
`UX_REVIEW.md` 반영 현황 A5). 테스트: `test_workspace.py`(빠진 입력 없음, 새 창에 복원하면 모든 페이지가 엔진에 같은 요청, 복원 문제
보고, 복구·닫기 흐름), `test_progress.py`(리스너가 결과를 바꾸지 않음 — 판정 예시 8개, 뒤로 가지 않는 진행률, 넓은 예외 처리를 지나는
취소, 블록 계산 = 행렬 계산), `test_desktop_progress.py`(취소된 결과는 버림, 부분 결과의 순서, 상태 표시줄 문구와 경과 시간, 판정 먼저·
저장 대기·판정 뒤 취소·실패), 자체 점검 `workspace:roundtrip`·`progress:engine_steps`.

**앱 전체 검토·UX** — 63개 화면을 검토해 고친 것: PDF 보고서가 밝은 plot theme을 데스크톱에 남기던 것(`style.using`으로 호출자의
theme 복원 + 회귀 테스트), self-test가 사용자 theme 설정을 저장하던 것, 요구 판정 페이지의 요약 열이 결과 위젯을 지우던 것, dict의
Python repr 표시, 0이 아닌 '미선언/없음/미지정' 표시, 쓰지 않는 보호 plant 행 숨김, 잘린 라벨·범례·표. 탐색을 공학 질문별 그룹으로
나누고, *도움말 → 화면 안내*, 입력 패널이 잘리지 않는 콤보·체크박스·스크롤 영역(1600×1000 · 1280×760 · 1100×700에서 잘림 0),
placeholder에서 비활성인 그림 버튼, 삼키지 않는 내보내기 오류, 파라미터 표시 이름.

**Anti-jerk** — 속도 high-pass 피드백은 요청 토크의 정상 성분 일부를 지웠습니다(폐형식 −Kd(T−T_L)/(ω_c J + Kd), 이제 결과에 표시).
2차 washout(`hpf_order: 2`)이 정상 결손을 없애며 예시가 이를 씁니다. 샘플링 루프 고유값 분석이 추가 상태를 포함하고, 안정성 판정이
시뮬레이션 진동과 일치하는지 테스트합니다.

**데이터시트 가져오기 (로드맵 P1-A/B의 데이터시트 단계)** — 디지타이즈된 모듈 곡선(long CSV 또는 WebPlotDigitizer), 커패시터 ESR 표,
게이트 dv/dt → 공급사 provenance(qualified 아님)의 프로젝트 섹션. 모든 온도가 덮는 전류 구간에서만 재표본(외삽 없음, 0 A 기준점은
선언 시에만), 데이터시트 tr/tf(전류 천이)는 전압 에지로 거부, 발견 사항(findings) 기록. CLI `twb datasheet`, 미리보기 대화상자,
전력변환 페이지의 모듈 편집을 프로젝트에 반영. 측정 데이터 단계(DPT 에지 family, 부품 임피던스, FEA dq 정규화)는 로드맵에 남습니다.

**공학 리뷰 반영 (기준 6198099)** — 리뷰가 제시한 반례를 실제 코드에서 재현(회귀 테스트 7건 중 5건 실패)한 뒤 고쳤습니다.
(F1) 지속시간 정격은 증거의 **방향**을 가집니다: 같은 지속시간의 정격은 양방향으로 답하지만, 더 긴(또는 연속) 정격은 긍정 증거일
뿐이라 그 안의 토크는 짧은 시간에도 허용되고, 그 위의 토크는 짧은 시간의 한계를 말해 주지 않습니다(30 s/100 N·m 정격이 10 s/150 N·m를
배제하지 않음 — 결론 없음, UNKNOWN). 양립하는 10 s/160 N·m와 30 s/100 N·m 정격은 더 이상 충돌로 처리되지 않습니다. 같은
지속시간에서도 표가 완전한 정격 한계(`rated_limit`: 초과 = RATING_NOT_MET)인지 입증 영역(`demonstrated_region`: 초과 = 결론
없음)인지 선언합니다. (F2) `linear_declared` 표는 선언된 선형 한계와 직접 비교합니다(1000 rpm 200 N·m, 2000 rpm 100 N·m → 1500 rpm
에서 150 N·m, 175 N·m는 정격 밖). (F3) 열 가용 토크 집합은 정적 segment마다 따로 만들어 서로 다른 정적 구간이나 UNKNOWN 표본을
잇지 않으며 `열 가능 집합 ⊆ 정적 가능 집합`을 검사합니다. (G1) 정격은 적용 제품(`applies_to`: drive id·개정·content SHA-256),
제어 방식, 무관하다고 선언한 조건을 가질 수 있습니다: 선언된 결속이 평가 드라이브와 다르면 적용하지 않고, 결속이 없거나 필수 조건(냉각수·
Vdc, 유한 정격은 초기 상태)을 다루지 않으면 초기 검토에는 쓰되 모든 claim과 요구 층에 APPLICABILITY_UNCONFIRMED로 표시합니다 —
자동으로 제품 근거로 승격되지 않습니다.

**요구 묶음·후보 (공학 리뷰 사용자 기능 1–5)** — 새 페이지 *요구 묶음·후보*: 요구 여러 건(CSV 가져오기·템플릿·붙여넣기)을 같은
제품 데이터·조건·근거로 한 번에 판정하고, 요구마다 판정·여유·제한 원인·UNKNOWN 원인 분류(입력 결측 / 적용성 미확인 / 연속 범위
미입증 / 모델 범위 밖 / 수치 / 근거 충돌 / 정책 한계)·바꿀 수 있는 항목·다음 자료를 보여 줍니다. 묶음 전체의 다음 자료 우선순위는
작업 종류(선언 < 문서 < 해석 < 새 자료)와 확정되는 요구 수로 정렬합니다. 계산 전 *해석 확인*으로 축 토크·기계/전기 속도·인버터 DC
단자·Vdc for-all·연산 의미(band는 존재성이지 제어 정확도가 아님)를 확인하고, 모터 모델 없이 고객 수치만으로 되는 필요조건(같은
운전점의 T·ω vs DC 방전 한계 — 위반이면 어떤 드라이브로도 불가능)을 봅니다. 모델이 UNKNOWN으로 남긴 요구도 이 조건이 위반이면
FAIL로 결정합니다. 후보(설계 변경안)는 모든 요구에 대해 다시 판정하며 개선과 악화를 그대로 나열합니다 — 가중 점수·비용 최적 없음.
판정 페이지 배너에도 UNKNOWN/FAIL의 원인 분류와 그 분류를 닫는 작업이 나옵니다.

**반복 부하·고온 시작 (리뷰 우선순위 1)** — 열 페이지의 *반복 부하*: 펄스–휴지 사이클을 냉각수 평형, 예부하 정상상태, 또는
측정한 노드 온도(Cauer 노드만 — Foster의 내부 상태는 물리 온도가 아님)에서 시작해 반복합니다. 손실은 노드 온도에서 다시 계산되고
(권선 → R_s(T), 접합 → 모듈 손실 T_j; 모듈 표 밖은 외삽하지 않고 멈춤), 주기 정상상태는 주기 사상의 고정점으로 정확히 구합니다(시간
상수가 긴 노드도 폐형식과 일치). 결과: 첫 한계 시각, 주기 정상상태 최고온도와 지배 노드, 허용 펄스 시간·토크, 첫 펄스 허용 토크,
같은 펄스를 반복하기 전 필요한 휴지와 주기를 유지하는 최소 휴지. 허용값은 각 구간의 끝이 아니라 **구간 안의 최고온도**로 판단합니다
— 빠른 항이 오르고 느린 항이 식을 때(고온 침지 후, 측정 노드 온도 시작) 최고점이 펄스 중간에 생기고, 끝만 보면 허용 토크가
과대평가됩니다(예: 약 70 N·m). 시작 노드 온도는 시험하는 펄스 자신의 기준 온도로 환산하며, 휴지 부하가 느린 노드를 다시
데우면 반복이 허용되는 휴지 **구간**(시작·끝)을 표시합니다.

**전원 임피던스 선택 결합 (우선순위 2)** — 판정 페이지에서 Vdc를 *배터리 OCV*로 지정하고 Thevenin R_eq를 선언하면, 단자 전압
V_inv = V_oc − R_eq·P_dc/V_inv를 운전점과 함께 풀어 그 전압에서 판정합니다(회생은 OCV보다 높아짐). 축 출력만으로 V_oc²/4R_eq를
넘으면 어떤 드라이브로도 불가능(증명), 소스 모델의 유효 범위 밖이나 고정점이 없으면 UNKNOWN입니다. 인버터 단자 전압으로 준 요구는
그대로(강하를 다시 빼지 않음).

**같은 운전점의 PWM 위험 (우선순위 3)** — 판정의 운전점에서 같은 변조·캐리어로 기본파 전류(정책의 한계)와 보수 순간 피크 상한(선언된
피크 한계와만 비교), 추가 RMS, 주요 선, DC-link 커패시터 부담, 모터 PWM 손실 구간을 한 표로 보여 줍니다.

**철손 범위와 손실 민감도 (우선순위 5)** — 회전·철손 항은 속도만의 등가 손실 토크임을 원장에 적고(부하·약계자·고조파를 따르지 않음,
별도 철손 맵을 겹쳐 더하지 않음), 각 확정 손실이 10 % 클 때의 효율 변화를 보여 줍니다. Vdc 인증서는 이 전제를 조건으로 명시합니다.

**PWM 고조파 손실의 의미 (PWM 인계 P0)** — 모터 PWM 동손은 R_ac 자료가 없어도 R_dc 하한(3·R_s(T)·ΣI², 평가한 선 스펙트럼)으로
보이고, R_ac/R_dc(f) 표가 유의 고조파를 모두 덮을 때만 정확값이 됩니다(커버리지 표시, 외삽 없음). 선언된 Fe+PM HF 자기 손실은
상한(`magnetic_hf_loss_bound_W`, 예전 `iron_bound_W`)으로만 쓰여 합산되지 않고 비교 구간의 끝이 됩니다. 정책 비교의 에너지는
[확정, 확정 + 열린 부분] 구간이며 겹치면 UNDECIDED, 한쪽이 열려 있으면 UNKNOWN — Pareto의 에너지 축도 구간이 분리될 때만 결정합니다.
'리플 포함 피크'는 보수 상한(I_fund,pk + max|Δi|)으로 이름을 바꿨고, 파형 모델이 쓰는 동기 캐리어 fsw와 요청 fsw의 차이를 구간마다
표시합니다. 결과에 에너지 제어 체적(커패시터 ESR·LV 전력은 별도), 열 범위(공급된 NTC 궤적 — 폐루프 아님), EMI/NVH 미평가를
적습니다. 효율 페이지의 손실 원장에도 PWM 동손(정확값 또는 하한)과 Fe+PM 상한이 나오고, 모터 측 경계에 PWM 고조파를 포함한
효율 구간이 붙습니다(기본파 η는 그대로).

**Vdc 범위 단조성 인증서 (리뷰 우선순위 4)** — 정적 순구동, 최소전류 정책, Vdc에 무관한 비음수 계수 `a0 + a2 I²` 손실(유효
범위가 요구 전압 범위 전체), 고정 소스 한계가 성립하면 전압이 오를수록 가능 집합이 줄지 않으므로 저전압 끝점의 FEASIBLE이 범위
전체의 정적 판정을 입증합니다(수학 층 CERTIFIED). 회생·Vdc 의존 손실·지속시간에는 일반화하지 않습니다(회생 반례를 테스트로 보임).

**온도 plane이 여러 개인 flux map** — 자석 온도를 말하지 않은 요구는 모델의 모든 plane 온도에서 for-all로 판정합니다(선언된 보간이
있으면 plane 사이는 표본 검사, 반례가 있으면 그 온도를 표시). T–n 곡선은 온도를 지정하지 않으면 plane마다 하나씩 그리고, 한 운전점
페이지(탐색·궤적·설계)는 자석 온도 입력을 가집니다(첫 plane 온도로 미리 설정). 이전에는 'magnet temperature required' UNKNOWN이거나
id–iq 지도에서 오류였습니다.

**데이터시트 대표값 직접 입력** — 곡선을 디지타이즈하지 않고 특성표의 대표값을 사람이 입력합니다(*데이터시트 값 입력…*: 프로젝트
페이지·파일 메뉴·모델 페이지·전력변환 페이지). 모터·파워 모듈·DC-link 커패시터·게이트 dv/dt 네 양식이 파일 가져오기와 **같은 spec**을
만들고(`twb datasheet`로 같은 결과, 사양 저장·불러오기), 입력하는 동안 프로젝트에 들어갈 모델·기록·단위 변환을 바로 보여줍니다.
값 하나는 점 하나이므로 곡선·모델은 **선언된 구성 규칙**으로 만들고 규칙마다 기록을 남깁니다:
도통 = 임계 V0 + I_nom 전압 / V0 + 기울기 / 두 점 / R_DS(on)의 직선을 [0, I_max]에(한 개의 V_CE(sat)만으로는 거부),
스위칭 에너지 = E_ref (I/I_ref)^k (k = 1은 직선, 그 밖은 기하 간격 절점과 명시된 선형 보간 오차 상한, E_rr = 0은 '무시 가능' 명시),
I_max 위는 손실 미확립(UNKNOWN); 커패시터 ESR 한 값 = 선언한 대역에서만 성립(대역 밖 고조파가 있으면 ESR 손실 UNKNOWN), EMI용
ESR은 대표값임을 표시; 모터 = 극수 → 극쌍, Ke(전압 정의·기준 속도)·Kt(id = 0 전자기 토크 정의 확인)·ψ_PM, 선간/상 저항, mH, 기준 온도를
드라이브 파서가 변환하고 모든 변환을 기록, 무부하 손실 한 점 → 점성 계수 b = P0/ω0²(모양은 선언), 인버터·운전 영역은 프로젝트
드라이브에서 유지. 값을 √2·√3·2배 바꾸는 규약(전압·저항·전류·에너지 기준)은 기본값 없이 선택해야 하고, 비운 필수 값은 0이 아니라
'미입력'으로 거부됩니다. 모터 미리보기는 입력값만으로 무부하 역기전력 대 Vdc(약계자 시작·비제어 발전 속도)와 MTPA·특성 전류를 그립니다.
합성 기준 기계를 데이터시트 형식(8극, Ke 선간 RMS/1000 rpm, 선간 R, mH, 6000 rpm 무부하 손실)으로 입력하면 기준 판정이 수치까지
같습니다(`test_motor_datasheet_values_reproduce_the_reference_machine`).

기존 테스트 결함 수정: 데이터시트 모듈 가져오기 테스트가 `api.module_losses(ex)`로 모듈 사양을 요청 본문 자리에 넘겨 가져온 곡선 대신
내장 예시 모듈을 평가하고 있었습니다(페이지는 올바른 `{"module": …}` 형식을 씀). 이제 가져온 모듈이 평가되고 내장 모듈과 다름을 확인합니다.

## 0.4.0 변경 사항 (v0.3.0 대비)

두 번째 독립 리뷰(기준 main f6f166b)의 결함 45건을 모두 재현하고 고쳤으며, 권고 R2(프로젝트 데이터 패키지)를 이행했습니다.
항목별 수정·확인 테스트는 [`TRACEABILITY.md`](TRACEABILITY.md) 10절, R2 이행은 [`SYSTEM_REVIEW.md`](SYSTEM_REVIEW.md) 8절에 있습니다.
모든 수치 fixture는 여전히 구현 검증(V0–V3)입니다.

**정확성 (리뷰 R2)**
- 코어·결정 기록: 배제 증명은 증명한 영역에만, 폭 0 제어 집합의 커버리지, DC 대역과 gate의 한 허용오차, 비수동 온도 법칙 거부, typed 정격 승인,
  모든 필드의 content hash, band 요구의 accepted witness, 데이터 경계를 최소 sizing으로 표시하지 않음, 0 cap 유지.
- 전력·열: SiC 물리 다이 소유권, 정지 시 실제 듀티, 커패시터의 한 온도 결합해(발산을 수렴으로 보고하지 않음, 종료 종류 명시), ESR(T)와
  전류 분배의 동시 해, 리플 위치 매핑·수동성 경계, 다이별 열 사이클, 가열 구간 t_on, 감속기 맞물림 동력 분기, A/B의 공통 변조.
- 보호·ASC: 경계 유효성, 같은 궤적의 경고 선행시간, 지평 안의 사건, 전각 포괄 하나(상·소자 증거 공통), 부호 있는 기계 동역학, 안전 평형 containment,
  실제 궤적의 샘플.
- 제어: 열린 필수 검사는 UNKNOWN(승인 아님), 비조정 공유 DC 버스 전압을 기계와 동시 해, 요청 목표 기준 anti-jerk 응답, 격자와 무관한 안전 반응 시각,
  실제 샘플 전류 루프의 안정성, OEW 고조파 표현 불변.
- **EMI**: 연속 수신 대역의 정확 열거(창 변화점·한계 꼭짓점 — 표시 격자와 무관, 조밀 스윕과 1e-6 dB 일치), claim 영역 = 대역 ∩ 보정 구간 ∩
  망 유효 ∩ RBW ∩ 한계(승인된 공백만 제외), **보정 기록**의 완전성(근거·holdout·취득·오차 모델·유한 한계·구간·set-up·망 식별자·소스 범위)과
  매 실행 적용성 재검사, 방법·단위 게이트, 하한 초과 증인만 INFEASIBLE, 측정 trace의 자체 메타데이터·읽음값 사이 손실·검출기 순위·적합/여유
  분리, 데드타임 턴온 지연·다이오드 클램프의 **스위칭 순서 소스**(독립 스위칭 시뮬레이션과 일치; 요청/평가 fsw 표시).
- **모터 설계**: k_N 인계는 두 권선 배치 모두 유효하고 극쌍수가 기계와 같을 때만; 선택적 `WindingDefinition`으로 기준 권선을 선언하면 계보가 파생
  기계로 이어지고, 선언이 없으면 '일반 k_N 사고 실험'으로 표시. 입력한 0은 기본값 1로 바뀌지 않고, 정수는 절삭 없이 검증.
- 함께 찾은 결함: PWM 게이트 이벤트 검사가 관측 창이 자른 펄스를 최소 펄스 위반으로 판정하던 것.

**프로젝트 데이터 패키지 (R2)**
- `twb-project/1`: 한 제품의 제품 데이터를 섹션(드라이브·DC 전원·모듈·대안·DC-link·제어기·열망·감속기·안전·EMI set-up)으로 한 번만 보관,
  섹션별 parser 검증·digest·provenance, 섹션 간 일관성 규칙(PRJ-01…12), 개정과 영향 분석.
- 내장 합성 프로젝트가 **모든 페이지 예시의 제품 데이터 출처**입니다(`api.example(name, project)`). 통합하며 데드타임(EMI·PWM 전환 1.0 µs ↔
  손실 1.5 µs)과 EMI 소스의 최소 펄스를 제어기의 한 값으로 맞췄습니다.
- 데스크톱: 프로젝트 페이지(섹션·일관성·이력·개정 비교, 열기·저장·새 개정), 헤더 배지, 결과가 있는 페이지의 띠 — 프로젝트 데이터 / **로컬 변경** /
  **stale**(결과 뒤에 그 섹션이 바뀜). 프로젝트를 바꾸면 페이지가 제품 입력을 다시 읽습니다. 의사결정 기록(JSON·Markdown·PDF)에 프로젝트 맥락.
- CLI: `twb project show | check | diff | export`.

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

**추가 명세 §7.2 필수 실패 사례**: 전류 샘플 유효창(인라인·레그 션트·DC-link 단일 션트)과 무효 샘플의 나이·오차, 채널 skew,
fsw 전환 과도(이득 매핑·적분기 저장·리셋·포화), 임계 채터, 부하 속도 skew·dropout과 선언 stale 한계의 페이드, 권한 창 미선언 → UNKNOWN,
한쪽 클리핑(회생 여유) 귀속, 안전 반응 별도 판정, 전압 여유 한도의 토크 slew — 어느 것도 조용히 통과로 바뀌지 않습니다.

**시스템 검토** ([`SYSTEM_REVIEW.md`](SYSTEM_REVIEW.md)): 정적 import 그래프·중복 물리·의미론·예시 데이터 교차 검사로 찾은 결함 수정 —
전류 루프를 두 기계 축(운전점의 차동 인덕턴스)으로 판정(단일 평균 L은 d축에서 낙관적), 한 평가 한 변조, 미션 효율의 부분 비율을
UNKNOWN으로, 예시 모듈 열 경로 통일, 변조 법칙 통합(`modulation.py`), 숫자 검증 분리(`validation.py`), 패키지 경계를 넘는 비공개 결합 0.
R1 이행: 데이터시트 모듈 손실 모델을 **드라이브 모델 층**(`models/module_loss.py`, 타입 계약)으로 옮기고, 커널의 **손실 계약**
(`loss_kind`·`i2_dc`·`pointwise_loss`) 한 곳에서 DC 논증을 분기합니다(2차 항등식 7벌 → 1벌). 모듈 모델이 켜진 코어 경로에 회귀 기준을 두었고,
그 과정에서 찾은 두 결함을 고쳤습니다 — 모듈 데이터가 점을 덮지 못할 때 DC claim이 "손실 모델 없음"이라 하던 사유, 모듈 모델에서 id–iq 지도와
격자 envelope가 격자 P_dc(NaN)를 위반으로 비교하던 것(이제 '격자 미평가'와 사유). 층 규칙·비공개 결합·순환은 아키텍처 테스트가 강제합니다.

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
- pytest 628개 통과(리뷰 재현 F01–F13·감사 재현·P0-B, 두 번째 리뷰 R2 수용 테스트, 모듈 코어 anchor·층 구조, 프로젝트 데이터 패키지, P1 모듈, OEW/HEV, EMI, 효율 E-01..E-06, 가변 PWM, 드라이브라인 D-01..D-05,
  모터 설계, 교환 패키지, 그래프 데이터의 물리 일관성, 냉각수·Cauer·유량 보정, 패시브 방전, 회로도, PDF 보고서, 데스크톱 headless smoke 포함).
- 데스크톱 self-test 54/54. Windows CI에서 PyInstaller exe를 빌드하고 **동결된 exe로** acceptance 21/21과 self-test를 통과했습니다
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
- 리뷰 R2의 다음 fidelity 권고(P1-A/B): 측정 DPT 에지 family(전류·Vdc·Tj별 tr/tf), 측정 부품 임피던스·다중 포트 망 import, FEA/공급사 dq 자속·손실
  데이터 정규화와 holdout 비교 — 미구현(현재 판정은 선언 모델 + 보정 기록의 범위 안에서만).
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
| P1: 모듈 손실·DC-link 리플·수명·보호·ASC 과도·전도 EMI | `models/module_loss.py`, `extensions/dclink_ripple.py`·`lifetime.py`·`protection.py`·`asc_transient.py`·`emi.py` | `test_module_loss.py`, `test_dclink_ripple.py`, `test_lifetime.py`, `test_protection.py`, `test_asc_transient.py`, `test_emi.py` |
| 모터 설계 (§10) | `analysis/machine_design.py` | `test_machine_design.py` (권선계수 교과서 값, dq 스케일링 항등식) |
| OEW·HEV | `extensions/oew.py`, `extensions/hev.py` | `test_oew.py`, `test_hev.py` |
| 경계별 효율·감속기·미션·모듈 A/B | `analysis/efficiency.py` | `test_efficiency.py` (E-01..E-06) |
| 가변 PWM | `extensions/pwm_policy.py` | `test_pwm_policy.py` (시간 적분 = 스펙트럼 리플, 이벤트 합 = 평균 손실) |
| Anti-jerk·능동 감쇠 | `extensions/driveline.py` | `test_driveline.py` (D-01..D-05, 출력 좌표 독립 ODE) |
| 교환 패키지 | `exchange.py`, `cli.py exchange` | `test_exchange.py` |
| 항목별 상태 (implemented / partial / missing / evidence_missing) | — | [`TRACEABILITY.md`](TRACEABILITY.md) |
