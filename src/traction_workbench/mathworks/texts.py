"""Human-readable documents of the transfer package: the porting guide (README.md) and the task sheet for an
optional agent (AGENT_TASKS.md).  Both are generated from the same data as the package, so they cannot drift."""

from __future__ import annotations

from .contract import CONVENTIONS, PARITY_TOLERANCE


def _models_table(info: dict) -> str:
    rows = ["| key | role | family | drive_id | content SHA-256 |", "|---|---|---|---|---|"]
    for k, m in info["models"].items():
        rows.append(f"| `{k}` | {m['role']} | {m['family']} | {m['drive_id']} | `{m['content_sha256'][:16]}…` |")
    return "\n".join(rows)


def readme(info: dict) -> str:
    p = info["project"]
    c = info["case_counts"]
    tol = "\n".join(f"| {k} | {v[0]:g} | {v[1]:g} |" for k, v in PARITY_TOLERANCE["classes"].items())
    conv = "\n".join(f"- `{k}`: {v}" for k, v in CONVENTIONS.items())
    dis = ", ".join(info["oracle_disagreements"]) or "없음"
    return f"""# Traction Workbench → MathWorks 이식 패키지 (`twb-mathworks/1`)

이 폴더는 Python Traction Workbench(**실행 가능한 reference 구현**)에서 검토한 모델·파라미터·시나리오·계산 의미·결과를
MATLAB / Simulink / System Composer에서 **같은 의미로 재현**하기 위한 이식 패키지입니다.
설계: 프로젝트 **{p.label}** (`{p.id}` rev `{p.revision}`, project digest `{p.digest()[:16]}…`).
식별자 전체(구현 SHA·dirty 여부·사용 파일 hash·semantic fingerprint)는 `manifest.json`에 있습니다.

## 1. 세 층 — 무엇을 무엇과 비교하는가

| 층 | 무엇 | 이 패키지에서 |
|---|---|---|
| 1. 요구·수용된 원천 | 요구사항, 시나리오, 수용된 참조 패키지(`traction_workbench_spec_v1`, manifest SHA-256 확인), 계약 수식 | 각 case의 `oracle` 값 (golden · closed_form · definition) |
| 2. Python reference | 식별된 source revision의 Workbench 계산 | 각 case의 `python` 값 |
| 3. MathWorks | 이 패키지의 MATLAB 코드 → Simulink harness → System Composer 후보 | `results/parity_report.json` |

3은 2와(**parity**), 그리고 1과(**oracle**) 같은 case로 동시에 비교됩니다. Python은 절대 기준이 아닙니다 — 2와 3이 같은
결함을 공유하면 parity는 통과해도 oracle 비교에서 드러납니다. 내보낼 때 2 ↔ 1 불일치 case: **{dis}**.

## 2. 실행 (회사 PC, AI·네트워크 없이)

```matlab
addpath(fullfile(pkg, 'matlab'));
report = twb.runAll(pkg);                  % preflight → 패키지 검사 → native evaluator parity → (Simulink 있으면) harness
```

- 배치: `matlab -batch "addpath(fullfile('<pkg>','matlab')); r = twb.runAll('<pkg>'); exit(double(~r.ok))"`
- GNU Octave(언어 호환 proxy): `octave-cli --eval "..."` 동일. Octave 결과는 **MATLAB 실행이 아니며** Simulink·System Composer
  단계는 NOT_RUN으로 남습니다.
- Workbench로 되가져오기: `twb mathworks verify <pkg> --project <현재 프로젝트>` 또는 앱의 프로젝트 페이지
  → *MathWorks 이식 패키지…* → *결과 보고서 가져오기*. 보고서는 이 패키지의 fingerprint와 모든 소비 파일의 SHA-256을
  기록하며, 다른 패키지나 다른 설계 개정의 결과는 현재 근거로 연결되지 않습니다.

## 3. 상태 — 하나의 초록불로 합치지 않습니다

| 단계 | 값 | 증명하지 못하는 것 |
|---|---|---|
| package_check | PASS / FAIL (schema, hash, 참조, 축·형상, 단위) | MATLAB이 실행된다는 것 |
| target_environment | 제품·버전·release (preflight) | Python과의 수학적 동등성 |
| native_evaluator | PASS / FAIL / ERROR, case별 NOT_SUPPORTED | 원 모델의 물리 정확성 |
| simulink_harness | PASS / FAIL / ERROR / NOT_RUN | 동적 거동 (정적 평가 block임) |
| system_composer, dictionary_conflicts | 명시적 단계, 실행 전 NOT_RUN | 물리 검증 |
| physical_validation | NOT_CLAIMED | — 구현 검증(V0–V4)이며 하드웨어 검증(V5–V6)이 아님 |

실행하지 않은 단계는 NOT_RUN이며 PASS로 표시되지 않습니다. ERROR(실행 실패)와 FAIL(의미 불일치)은 구분됩니다.

## 4. 보존되는 의미 (계약)

`contract.json`과 각 모델의 `conventions`는 MATLAB 쪽 `twb.checkContract`가 **문자 그대로** 확인합니다 — 다른 규약을
선언한 패키지는 재해석하지 않고 거부합니다.

{conv}

추가 규칙: 정의되지 않은 값은 null/NaN(0이 아님) · 선언되지 않은 DC 한계는 무제한이 아님(`not_declared` ≠ `unlimited`) ·
위반한 한 점은 진단값이지 INFEASIBLE 증명이 아님 · 모델 유효성 문제가 있으면 수치는 진단값(evidence 아님) ·
FEASIBLE / INFEASIBLE / UNKNOWN은 boolean으로 평탄화하지 않습니다.

## 5. Flux map 규칙

- 배열 `psi[i][j]`: i = id 축, j = iq 축(row = id). `jsondecode`가 `M(i_id, j_iq)`를 주며, 로더는 크기를
  `[numel(id_axis) numel(iq_axis)]`로 확인하고 **전치를 추측하지 않습니다**. 검증용 map(`VF_D2_MAP`)은 6×7 비균일·비대칭 축이라
  전치·인덱스 오류가 드러납니다.
- 유효 cell 안에서만 bilinear. 모서리 위의 점은 인접한 유효 cell이 하나라도 있으면 covered. 그 밖은 평가 불가(UNKNOWN).
- **외삽·clip 없음.** n-D Lookup Table의 기본 extrapolation(Linear)과 Clip 모두 이 계약과 다릅니다. Lookup Table block을 쓸
  경우 명시적 validity 검사가 UNKNOWN을 돌려주어야 하며, `VF.MAP.*` case(구멍·모서리·축 밖)가 이를 확인합니다.
- Bilinear를 spline 등으로 바꾸면 다른 모델입니다. 온도 의존성은 plane에서만 옵니다 — native 온도 계수를 더하면 이중 보정
  (`twb.checkModel`이 거부). 선언된 q-odd 대칭은 export 시 적용된 전체 plane으로 전달됩니다(원 plane hash 보존).
- 정적 사용만: bilinear 보간자의 Jacobian은 cell 경계에서 불연속이므로 동적(전류 상태) 사용은 NOT QUALIFIED입니다.

## 6. 손실 소유권

각 모델의 `loss_ownership`이 손실 항목의 소유자를 적습니다(구리: dq 전압식의 Rs, 회전: shaft의 손실 등가 토크, 인버터:
DC 측 surrogate, 고조파: 기본파 모델 밖). native Simscape/Simulink block이 자체 손실을 가지면
`twb.lossOwnershipConflicts(model, struct('copper', true, ...))`로 **이중 계산**을 확인하십시오.

## 7. 동등성 판정

`|q_target − q_reference| ≤ atol + rtol·max(|q_target|, |q_reference|)` — 클래스별 값:

| class | atol | rtol |
|---|---|---|
{tol}

{PARITY_TOLERANCE['basis']}
Oracle 비교: golden = 참조 패키지 수용 기준(`|x − x_ref| / max(1, |x_ref|) ≤ 1e-10`), closed_form = 위 atol × 10, rtol 1e-11.
상태(evaluable, reason, gate 사유, 제약 상태, 에너지 모드, 위반 그룹, claim)는 같아야 합니다.

## 8. 구성

| 경로 | 내용 |
|---|---|
| `manifest.json` | 식별(구현 SHA·dirty·source hash·project digest), 파일 목록과 SHA-256, semantic fingerprint, 초기 상태 |
| `contract.json` | 규약, 물리량 사전(단위·tolerance class), 상태 어휘, 수치 설정, map·손실 규칙, 세 층 |
| `models/*.json` | 모델 데이터 + 식별(content SHA-256, provenance) + 손실 소유권 |
| `cases/*.json` | forward {c['forward']} · flux_lookup {c['flux_lookup']} · requirement_witness {c['requirement_witness']} case |
| `architecture/` | System Composer/SLDD 후보(stable ID)와 회사 profile 양식 |
| `source/` | 프로젝트(twb-project/1), 교환 패키지(twb-exchange/1: 규약·fixture), 참조 패키지 manifest |
| `gap_report.json` | exported_executable / exported_recipe / exported_candidates / exported_data_only / not_applicable / missing / unsupported |
| `matlab/+twb/` | native MATLAB 코드 |
| `results/` | 실행 결과(보고서·preflight·log) — 패키지 식별에 포함되지 않음 |

모델:

{_models_table(info)}

## 9. Simulink harness와 System Composer

- `twb.buildEvaluationHarness(pkg, modelKey, scenario, outDir)`: MATLAB Function block 하나가 `twb.staticPoint`(해석 실행과
  같은 코드)를 호출하는 **정적 평가 harness** — 동적 plant가 아닙니다. 새 폴더에만 생성하고 기존 모델을 열거나 덮어쓰지
  않습니다. `twb.runHarness`가 forward case로 실행해 해석 결과·Python 값과 비교합니다(`runAll`이 Simulink가 있으면 호출).
- `architecture/candidates.json`: 물리 연결(DC·3상·축·열 — signal bus가 아님), 정보 signal(타입·단위·rate·지연 — 선언되지
  않은 것은 "not declared"), 데이터 항목(SLDD 후보: 값·단위·소유 섹션·provenance), 요구 연결(요구 → case → 근거).
  인버터 전류 한계는 이 설계의 물리 한계이지 소프트웨어 saturation이나 인터페이스 범위가 아닙니다.
- `architecture/profile_template.json`의 `id_map`에 stable ID ↔ 회사 요소 이름을 **명시적으로** 채웁니다. 이름만 같다고 같은
  항목으로 보지 않습니다: `twb.checkDictionary(pkg, sldd, profile)`(읽기 전용)이 NEW / MAPPED_SAME / NAME_MATCH_ONLY /
  CONFLICT_UNIT / CONFLICT_VALUE / CONFLICT_DUPLICATE_TARGET을 보고합니다. `twb.buildArchitectureCandidate`는 새 후보 모델만
  만듭니다. Profile은 회사 환경에 두며 패키지 식별에 포함되지 않습니다.

## 10. 수정과 재생성 — 연결을 유지하는 방법

- 패키지 파일은 생성물입니다. 편집하면 MATLAB 로더와 `twb mathworks check`가 hash 불일치로 거부하고, 다음 export는 편집된
  생성 파일을 **덮어쓰지 않고** 목록과 함께 중단합니다(다른 폴더로 export하거나 변경을 옮기십시오).
- 회사가 소유하는 것(profile, 회사 architecture·dictionary, 상세 Simscape/Simulink 모델)은 패키지 밖에 둡니다. 생성 도구는
  새 폴더에만 쓰고, 패키지에서 사라진 ID를 삭제 명령으로 해석하지 않습니다.
- 설계가 바뀌면(새 프로젝트 개정) 새 패키지를 export합니다. 이전 패키지의 보고서는 fingerprint와 project digest로 그 개정에만
  연결되며 현재 근거가 되지 않습니다(stale).
- 상세 모델(Simulink 동적 모델, Simscape plant)로 발전할 때도 stable ID, 요구 trace, 같은 case 집합을 회귀 기준으로 씁니다.
  같은 입력·초기조건·손실 경계에서 비교하고, 차이는 fidelity 비교(설명된 discrepancy)로 기록합니다 — 기존 ROM을 대체하려면
  holdout 검증·domain·승인된 개정이 필요합니다.

## 11. 한계

정적 기본파 모델의 forward 평가·flux map 평가·요구 witness 재검사만 native로 실행됩니다. 최적화기(MTPA/약계자)·capability
인증서·데이터시트 모듈 손실·열·PWM·anti-jerk·보호·EMI·OEW/HEV는 데이터·계약·근거 수준으로만 전달됩니다(`gap_report.json`).
Simulink/System Composer 코드는 문서화된 API로 작성되었고 해당 제품이 있는 환경에서 실행되기 전까지 NOT_RUN입니다.
"""


def agent_tasks(info: dict) -> str:
    return """# Agent task sheet (Simulink Agentic Toolkit / MCP agents) — optional orchestration

The core path does not need an agent: every step below is a deterministic MATLAB call whose inputs (package
fingerprint) and outputs (`results/*.json`) identify the run.  An agent may orchestrate these calls; its conversation
is never the evidence — the reports are.

## Allowed

1. Read `manifest.json`, `gap_report.json`, `contract.json` and the company profile; summarise what is exported,
   executable, data-only, not applicable, missing or unsupported, and every mismatch with the company environment.
2. Run `report = twb.runAll(pkg)` and summarise `results/parity_report.json` per stage (package check, native
   evaluator, Simulink harness, System Composer, dictionary, physical validation) without merging them.
3. Generate target artefacts only through the shipped recipes into NEW folders:
   `twb.buildEvaluationHarness`, `twb.runHarness`, `twb.buildArchitectureCandidate`.
4. Read company dictionaries only through `twb.checkDictionary` (read only) and report its conflicts.
5. Propose - never apply - changes to company models or dictionaries, with the exact target elements and a diff.
6. Keep a structured log of every call (function, arguments, package fingerprint, output files).

## Not allowed

- Inventing units, maps, masks, temperatures, initial states, fault delays, thermal parameters or tolerances.
- Editing expected values, tolerances, cases, the contract or the models to make a failing comparison pass.
- Replacing the model by a "similar" native block without a new package / parity run (e.g. spline for bilinear,
  a block with its own losses on top of the exported loss closures, extrapolating lookup tables).
- Writing into company-owned areas, deleting elements because an ID is missing from a newer package, or linking a
  report to a design revision other than the one its package names.
- Sending company data or packages to external services.

## Task list

| id | task | command | done when |
|---|---|---|---|
| T1 | environment | `env = twb.preflight()` | `results/preflight.json` names product, release and toolboxes |
| T2 | package check | `pkg = twb.loadPackage(pkgDir)` | no error (schema, hashes, conventions, shapes) |
| T3 | native parity | `report = twb.runAll(pkgDir)` | native_evaluator PASS; FAIL / ERROR cases listed with their checks |
| T4 | Simulink harness | automatic in `runAll` when Simulink exists | simulink_harness PASS or a precise ERROR |
| T5 | architecture candidate | `twb.buildArchitectureCandidate(pkg, profile, outDir)` | new model created; unmapped IDs listed |
| T6 | dictionary conflicts | `twb.checkDictionary(pkg, slddFile, profile)` | conflicts listed; nothing written |
| T7 | hand back | copy `results/` to the Workbench user | `twb mathworks verify` links it to this package |
"""
