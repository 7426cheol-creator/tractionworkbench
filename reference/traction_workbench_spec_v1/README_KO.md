# Traction Workbench — 설계 및 구현 인계 패키지

버전: 1.0 / 2026-09-27

이 패키지는 제품 정의, 물리적/수치적 계약, 검증 fixture를 제공한다. Production Python 코드는 포함하지 않는다.

## 읽는 순서

1. `01_Engineering_Blueprint_KO.md` — 제품 가치, use cases, MVP, 모델, 판정, V&V, 확장 순서.
2. `02_Implementation_Handoff_KO.md` — 구현해야 할 behavior, equations, conventions, invariants, errors, acceptance.
3. `03_Reference_Cases_KO.md` — synthetic reference의 계산 정의·결과·독립 검산 근거.

JSON 자료는 `synthetic_drive.json`, `golden_forward.json`, `golden_inverse.json`, `golden_capability.json`, `manufactured_flux_map.json`, `semantic_boundary_cases.json`이다. JSON 파일 구조를 production 앱의 데이터 구조로 그대로 채택할 필요는 없다. 물리적 의미와 expected behavior를 보존하면 된다.

## 구현자에게 전달할 요청

> Blueprint를 intended use 기준으로 읽고 Handoff의 physics, semantics, invariants, error behavior, acceptance를 보존하여 구현하라. 구현 구조와 라이브러리는 코드베이스에 맞춰 결정하라. 먼저 synthetic fixture를 독립 검증하되, 앱의 출력으로 expected 값을 덮어쓰지 마라. 모델/입력 범위 밖의 결과와 지속시간 불확실성을 PASS로 승격하지 마라. 완성 후 실행·검증 결과와 남은 한계를 제출하라.

## 검증 상태

수식 직접 계산, polynomial-boundary를 사용한 별도 1D inverse reference, selected infeasibility bounds 및 positive-capability quadratic certificates를 확인했다. 하드웨어, 외부 simulator, 공급사 데이터와의 validation은 수행하지 않았다. 수치 자릿수는 회귀검산용이지 실제 제품 정확도가 아니다.

참고문헌은 Blueprint의 마지막 절에 있다. Acceptance 수치는 규격 요구가 아니라 이 프로젝트의 제안 기준이다. 특정 OEM 또는 공급사 기밀자료는 사용하지 않았다.
