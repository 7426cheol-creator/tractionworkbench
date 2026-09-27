# Traction Workbench

**Traction Engineering Feasibility & System Analysis Workbench** — 단일 3상 2-level 인버터 + PMSM/IPMSM의
정상상태 기본파 모델로 고객 요구를 판정하고, 그 근거를 재현 가능한 **Engineering Decision Record**로 남기는 도구입니다.

> “현재 조건에서 고객 요구를 만족시킬 수 있는가? 무엇이 막고 있으며, 어떤 변경이나 추가 자료가 의사결정을 바꾸는가?”

설계 기준선: `reference/traction_workbench_spec_v1/` (Blueprint · Implementation Handoff · Reference Cases · golden JSON, SHA-256 고정).

## 무엇이 다른가

- **판정 항목을 섞지 않습니다.** 전기적 해의 존재 / 최소전류 정책의 정적 달성(DC 한계 포함) / DC 소스 한계 / 임의 제어로의 가능성(진단) / 지속시간 / 요구 전체(AND 집계)를 각각 FEASIBLE·INFEASIBLE·UNKNOWN과 근거(evidence)로 보고합니다.
- **INFEASIBLE은 증명이 있을 때만.** 상수 모델은 제약 다항식 근 전수 열거(exact enumeration), flux map은 셀 구간 경계(branch & bound), 공통으로 해석적 필요조건(예: 축 출력 > 방전 한계, 최대 손실로도 충전 한계 미달, d축 전압 하한 > 예산)을 사용합니다. Solver가 해를 못 찾은 것은 UNKNOWN(NUMERICAL_UNRESOLVED)입니다.
- **Capability는 달성값과 증명된 반대쪽 상한을 분리합니다.** 구동 capability는 Lagrangian 오목 상한(상수 모델) 또는 셀 경계(flux map)로 certified, 회생 경계는 최소전류(에너지 회수) 정책 경계로 표본 증거와 함께 보고하며 의도적 손실 증가 운전은 채택하지 않습니다.
- **입력을 조용히 채우지 않습니다.** 단위·정의(peak/RMS, 상/선간, 기계/전기 속도, per-phase/line-to-line, Ke/Kt convention)가 모호하면 계산 전에 INVALID_INPUT, 누락된 손실/온도/지속시간 근거는 UNKNOWN으로 남깁니다. 모든 변환은 기록됩니다.
- **요구를 바꾸지 않습니다.** 원문 보존, 토크 clip 없음, Vdc 범위 요구는 표본점 통과만으로 PASS가 아니며(SAMPLED_COVERAGE), 지속시간이 없으면 정적 항목으로만 해석합니다.

## 빠른 시작

```bash
pip install -e '.[test]'           # numpy, scipy (+ pytest)

twb demo                           # 대표 질문 8개를 합성 드라이브로 판정
twb serve --open                   # 웹 UI (http://127.0.0.1:8765)
twb evaluate examples/cases/req_ts_012_450V_sizing.json --out out/   # 의사결정 기록 JSON + Markdown
twb solve --n 12000 --torque 150 --vdc 600
twb capability --n 12000 --vdc 600 --direction -1
twb curve --vdc 450
twb acceptance                     # production 결과 vs golden JSON
twb export-static --out out/traction_workbench.html   # 서버 없이 열리는 UI 스냅샷

python verification/independent_fixture_check.py      # production 코드를 쓰지 않는 독립 검산
python -m pytest -q
```

`python -m traction_workbench <command>`도 동일하게 동작합니다.

## 대표 결과 (합성 fixture, `twb demo`)

| 요구 | 판정 | 핵심 근거 |
|---|---|---|
| 12,000 rpm · 150 N·m · 600 V | **PASS** (정적) | 전류 여유 204 A지만 토크 capability 여유는 **2.555 N·m** — 한계는 전압·DC 방전 전력 (certified 152.555 N·m) |
| 같은 요구 · 450 V | **FAIL** | 필요조건 증명: \|v_d\| ≥ 255.54 V > 예산 246.82 V, 축 출력 188.5 kW > 450 V×400 A. 인버터 전류를 1200 A로 키워도 해결 불가, **Vdc ≥ 497.7 V** 필요 (전압 + DC 전류 공동 병목) |
| 같은 요구 · 10초 유지 | **UNKNOWN** | 전기적으로 가능하나 검증된 10초 rating/열모델 없음 (UNVALIDATED_DURATION) |
| 12,000 rpm · −80 N·m 회생 | **PASS** | 에너지 회수 회생, 충전 한계 내 |
| 12,000 rpm · −100 N·m 회생 | **FAIL** | 최대 손실(600 A)로도 P_dc ≤ −111.3 kW < −100 kW: 배터리 수용 한계 |
| 6,000 rpm · 350 N·m | **FAIL** | 손실 0이어도 축 출력 219.9 kW > 방전 200 kW |
| Vdc 550–650 V 전 구간 · 100 N·m | **UNKNOWN** | 표본 5점 모두 가능하지만 연속 구간 보장은 아님 (SAMPLED_COVERAGE) |
| 정지 · 300 N·m | **PASS** (정적) | 등가 정현파 RMS, 효율 N/A, 정지 열 지속시간 추론 없음 |

## 웹 UI

판정 결과를 먼저 보여주고(PASS/FAIL/UNKNOWN + 사유 + 범위), **요구 → 조건 → id/iq → 전력 수지 → 제약 → 결론**을 클릭으로 추적합니다.

| 탭 | 내용 |
|---|---|
| 요구 판정 | 예시 프리셋, 요구 입력(원문 보존), claim 카드와 근거, 운전점·전압 예산, 부호 있는 전력 수지, 고유 단위 제약 여유, T–n 영역 + 동일 축척 id–iq 제약 지도, 제한 요인·다음 조치, 추가 분석, 기록 다운로드(JSON/MD, SHA-256) |
| 성능 곡선 | 여러 Vdc의 정책(DC 포함) vs 전기적 한계 T–n 곡선과 제한 요인 표 |
| 설계·병목 | 1-파라미터 역설계(변경 종류 표기: boundary/hardware/design/diagnostic), 제약 1% 완화 재계산 dominance, 공동 병목 |
| 안전 스크리닝 | FTTI 체인(중복 예산 자동 검출, Gantt), 능동 방전·회생 중 배터리 차단 과전압, ASC/Freewheel 정상상태 비교 + 프로젝트 규칙 |
| 열·지속시간 | Foster 열망 → 지속시간별 가용 토크, “냉각수 65 °C에서 X N·m는 t초 유지, 이후 Y N·m” 형식 (미검증 모델은 UNKNOWN 유지) |
| 검증(V&V) | golden 대비 acceptance 표, manifest 해시, 알려진 한계 |

항상 보이는 배지로 모델 ID·fidelity(D1/D2)·데이터 출처(synthetic)·“하드웨어 미검증”을 표시합니다. 한국어/영어, 라이트/다크, 모바일 레이아웃을 지원하며 외부 라이브러리 없이 동작합니다.

## 저장소 구조

```
reference/traction_workbench_spec_v1/   불변 설계 기준선 + golden JSON (manifest SHA-256)
src/traction_workbench/
  models/        상수 dq · flux map(유효 마스크, 선언된 대칭, 온도 plane) · 손실 · 인버터 · 운전영역 · provenance
  physics.py     DriveKernel, 정방향 평가, 고유 단위 제약, 전력 항등식, 에너지 모드/효율
  solvers/       exact(다항식 경계 열거) · sampled(곡선 추적) · bounds(셀 B&B) · screens(필요조건)
                 certificate(Lagrangian 상한) · policy(최소전류 정책 + claim) · capability
  analysis/      rating(지속시간 envelope) · loss_interval · uncertainty(구간 입력) · sizing · dominance
                 compare · supplied_policy · variation
  extensions/    timing(FTTI) · dclink(방전/과전압) · safe_state(ASC/6SO) · thermal(열→토크)  ← 스크리닝 전용
  decision.py    Engineering Decision Record (AND 집계, 입력 스냅샷 SHA-256), report.py (Markdown)
  io.py, units.py  단위·정의가 명시된 JSON 입력 형식 (UC00 변환 기록)
  service.py, web/, cli.py
verification/   independent_fixture_check.py (production 비의존), make_report.py
tests/          골든·의미론·검증·확장·앱 계층 테스트 (186개)
examples/       case 파일, 단위가 선언된 drive 정의
docs/           RELEASE_NOTES_v0.1.0.md (모델 계약·한계·추적표), VERIFICATION_REPORT.md
```

## 검증 상태

- 참조 패키지 10개 파일 SHA-256 일치, 독립 검산 136/136, production vs golden 21/21, pytest 186 통과 — 상세: [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md)
- **합성 fixture에 대한 verification입니다.** 하드웨어·공급사 데이터·외부 시뮬레이터 validation은 수행하지 않았습니다(V4–V5 미수행).

## 문서

- [`docs/RELEASE_NOTES_v0.1.0.md`](docs/RELEASE_NOTES_v0.1.0.md) — 실행 방법, model contract, 제약 목록, 판정 의미론, 수치 방법, 알려진 한계, 미구현 항목, data provenance, 재현 조건, 스펙 조항 ↔ 구현 ↔ 테스트 추적표
- [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md) — 자동 생성 검증 보고서
