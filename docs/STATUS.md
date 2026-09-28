# 현재 상태와 이어서 할 일 (인계 문서)

이 문서는 작업을 중간에 이어받는 사람(또는 다음 세션)이 저장소만 보고 **무엇이 되어 있고, 무엇이 남았고, 어떻게 이어서
시작하는지**를 알 수 있게 정리한 것입니다. 항목별 구현 위치·테스트·상태의 상세 대응표는
[`TRACEABILITY.md`](TRACEABILITY.md), 버전별 변경은 [`RELEASE_NOTES.md`](RELEASE_NOTES.md), 전체 구조와 성숙도는
[`SYSTEM_REVIEW.md`](SYSTEM_REVIEW.md)에 있습니다.

- 브랜치 `claude/attachment-review-completion-qw3ngk`, 초안 PR #4. 패키지 버전 0.5.0.
- 로컬 검증: 전체 `pytest`, 데스크톱 self-test(모든 페이지를 headless로 실행·스크린샷)가 모두 통과합니다. 정확한 개수는 마지막
  커밋 메시지와 PR 본문에 있습니다.
- 데이터: 내장 제품 데이터는 **합성(synthetic) 예제**입니다. 하드웨어·공급사 자료로 검증된 결과는 아직 없습니다(V4–V5 미수행).

## 1. 완료된 것

### 1.1 기반 (이전 작업)
요구 판정 엔진(최소전류 정책, 정확한 경계 열거, 인증서, 4층 판정 — 수학·모델·요구·qualification), T–n·효율 맵, 궤적, 역설계·병목,
안전 스크리닝(FTTI, 방전, 과전압, ASC/Freewheel), 보호·고장, 열·지속시간(냉각수 순환, Foster/Cauer), 데이터시트 모듈 손실·DC-link
리플·수명, 다섯 경계 효율·모듈 A/B, 가변 PWM·anti-jerk, OEW·HEV, 전도성 EMI, 모터 설계, 프로젝트 데이터 패키지(섹션 digest,
provenance, stale 표시), 데이터시트 가져오기·대표값 직접 입력, MathWorks 이식 패키지(GNU Octave로 실행). 리뷰 R1·R2·앱 리뷰의
결함 수정.

### 1.2 이번 작업
| 주제 | 내용 | 문서 |
|---|---|---|
| 온도 plane이 여러 개인 flux map | 자석 온도를 말하지 않은 요구는 모든 plane에서 for-all(선언된 보간 사이는 표본), T–n은 plane마다, 한 운전점 페이지는 자석 온도 입력 | TRACEABILITY §13 |
| 공학 리뷰 6198099 F1–F3, G1 | 정격 증거의 방향, 선언된 선형 한계, 열 집합이 정적 공백을 잇지 않음, 정격의 제품·조건 결속 | §13 |
| 우선순위 4 | Vdc 범위 단조성 인증서(정적 순구동·Vdc 무관 손실·고정 소스 한계일 때 저전압 끝점으로 범위 전체 입증) | §13 |
| 사용자 기능 1–5 | 요구 묶음·후보 페이지와 `twb reqset`: CSV, 해석 확인, 모델 없이 되는 사양 필요조건, UNKNOWN 원인 분류, 다음 자료 우선순위, 후보 × 요구, 리뷰가 정한 결과 순서 | §13 |
| 우선순위 1 | 반복 부하·고온 시작: 펄스–휴지 반복, 예부하 정상상태·Cauer 노드 온도 시작, R_s(T)·T_j 손실 피드백, 주기 정상상태(고정점), 허용 펄스 시간·토크, 필요 휴지 | §13 |
| 우선순위 2 | 배터리 OCV + Thevenin R_eq → 단자 전압을 운전점과 함께 풀어 판정(회생 상승, 공급 불가 증명) | §13 |
| 우선순위 3 | 판정 운전점의 PWM 위험: 기본파 한계 vs 보수 순간 피크, 추가 RMS, 주요 선, DC-link 부담 | §13 |
| 우선순위 5 | 회전·철손 항의 범위 표시, 손실 민감도, 인증서 전제 명시 | §13 |
| PWM 인계 P0 | R_dc 동손 하한·R_ac 커버리지, Fe+PM HF 상한은 구간 끝, 피크 전류 보수 상한, 요청≠파형 fsw, 구간 기반 Pareto·비교, 제어 체적·열 범위 메타데이터, 효율 원장에 PWM 항목 | §14 |

## 2. 남은 것

### 2.1 코드로 할 수 있는 것 (우선순위 순)
1. **PWM 추가 모터 손실의 정식 회계 (PWM 인계 P1)**: 지금은 원장·효율 구간에 노출만 합니다. P_dc와 DC 한계 판정, 미션 에너지에
   같은 의미(하한·상한 구간)로 결합하려면 control volume별 소유권을 먼저 정하고 기존 I² 기반 DC 인증서의 전제를 다시 확인해야
   합니다.
2. **프로젝트 데이터의 `motor_hf` 섹션 (P1)**: L_hf(스칼라/축/임피던스 맵), R_ac/R_dc(f), Fe+PM HF 상한을 provenance와 함께
   프로젝트에 넣기. 지금 효율·판정 페이지의 PWM 입력은 가변 PWM 페이지의 합성 예시를 씁니다(표시됨).
3. **폐루프 열–스케줄러 (PWM 인계 P4, 리뷰 우선순위 1의 확장)**: 손실 → 열망 → NTC 센서 지연 → fsw 스케줄 → 손실. 지금 가변
   PWM의 T_ntc는 공급된 궤적입니다(결과에 명시).
4. **소스 모델 확장 (우선순위 2)**: SOC·온도별 V_oc/R_eq 표, 요구 묶음 CSV의 `Vdc_port = battery_ocv` 지원(지금은 판정 페이지와
   case 파일만), 소스 결합 요구의 범위 인증서.
5. **EMI 자동 재평가 (P1)**: 정책별 fsw로 전도성 EMI를 다시 계산해 필수 제약에 연결. NVH·베어링 전류는 검증된 모델이 없으면
   UNKNOWN 유지.
6. **HF 임피던스 모델 (P2)**, 비동기 캐리어·관측창, DPWM, 과변조 모델, **Fe/PM 보정 ROM (P3)** — 자료(FEA·공급사·시험)가 생길 때.
7. 철손 분해 모델(기계손·기본파 철손·PWM 추가손) — 자료가 있을 때(리뷰 우선순위 5). id/iq 의존 철손을 코어에 넣으면 속도만의
   손실 토크·I² 기반 인증서의 전제를 다시 확인할 것.

### 2.2 자료·검증이 필요한 것 (코드만으로 닫히지 않음)
- 실제 제품 자료(모터 flux map·손실, 모듈 데이터시트, 열망 검증, 배터리 R_eq)와 하드웨어 상관 — 지금은 모든 qualification 층이
  NOT QUALIFIED(합성 자료)입니다.
- MATLAB/Simulink/System Composer 단계는 GNU Octave로 MATLAB 코드만 실행했습니다(Simulink 등은 NOT_RUN).

### 2.3 CI
- 2026-09-28 14:29 UTC 이후 GitHub Actions가 이 저장소의 작업에 **runner를 배정하지 않습니다**(`runner_id 0`, 로그 없음, 3초
  만에 실패). 코드와 무관한 계정 수준 문제(Actions 사용량·지출 한도 등)로 보이며, 저장소 소유자가 GitHub 설정(Billing & plans →
  Actions)에서 확인해야 합니다. 같은 검사는 로컬에서 모두 통과합니다.

## 3. 이어서 시작하는 법

```bash
pip install -e '.[gui,test]'
QT_QPA_PLATFORM=offscreen python -m pytest -q          # 전체 테스트 (약 6분)
twb selftest out/selftest                             # 데스크톱 self-test (약 3분, out/selftest/selftest.json)
python verification/independent_fixture_check.py      # production 코드를 쓰지 않는 독립 검산
twb gui                                               # 앱
```

- 새 판정 규칙은 `decision.py`(요구 판정), 분석 엔진은 `analysis/`·`extensions/`, 요구 묶음은 `requirement_set.py`, 화면은
  `desktop/pages/`에 있습니다. 계층 규칙(base < models < kernel < engines < services < presentation)은
  `tests/test_architecture.py`가 검사합니다.
- 공통 원칙: 모르는 값은 0이 아니라 UNKNOWN, 상한은 값이 아니라 구간의 끝, 표본 통과는 범위 증명이 아님, 선언된 근거가 없는
  승격 없음, 모든 결과는 사용한 프로젝트 데이터(섹션 digest)와 함께 기록.
- 리뷰·인계 문서의 원문은 저장소 밖(대화 첨부)이며, 각 항목의 대응은 `TRACEABILITY.md` §13–§14에 있습니다.
