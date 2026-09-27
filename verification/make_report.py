#!/usr/bin/env python3
"""Regenerate docs/VERIFICATION_REPORT.md from actual runs.

Runs (1) the independent fixture check, (2) the production-vs-golden
acceptance comparison, (3) the pytest suite, (4) the demo questions and
(5) the headless desktop self-test, and writes the results.  Nothing here
edits the reference fixtures.

    python verification/make_report.py
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def run(cmd):
    t0 = time.perf_counter()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr, time.perf_counter() - t0


def main() -> int:
    import numpy
    import scipy

    from traction_workbench import __version__
    from traction_workbench import service as S
    from traction_workbench.api import PRESETS

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    rc_ind, out_ind, t_ind = run([sys.executable, "verification/independent_fixture_check.py"])
    m = re.search(r"(\d+)/(\d+) independent fixture checks passed", out_ind)
    ind_pass, ind_total = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    mtpa = [ln for ln in out_ind.splitlines() if re.search(r"inverse:I(00|09|10)_\w+:golden ", ln)]
    acc = S.acceptance_summary()
    rc_py, out_py, t_py = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    py_line = next((ln for ln in reversed(out_py.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), out_py[-200:])
    st_dir = ROOT / "build" / "report_selftest"
    rc_st, out_st, t_st = run([sys.executable, "-m", "traction_workbench.cli", "selftest", str(st_dir)])
    try:
        st = json.loads((st_dir / "selftest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {"checks": [], "passed": 0, "total": 0, "ok": False}
    demo = []
    for p in PRESETS:
        r = p["req"]
        case = {"drive": {"builtin": "SYNTH_IPMSM_200KW_REF_V1"},
                "requirement": {"id": r["id"], "text": r["text"],
                                "target": {"value": r["torque_Nm"], "unit": "N*m", "torque": "shaft"},
                                "conditions": {"speed": {"value": r["speed_rpm"], "unit": "rpm", "kind": "mechanical"},
                                               "Vdc": {"value": r["Vdc_V"], "unit": "V"}}}}
        if r.get("duration_s"):
            case["requirement"]["duration"] = {"value": r["duration_s"], "unit": "s"}
        rec = S.evaluate_case(case)
        c = rec["conditions"][0]
        demo.append((r["id"], rec["verdict"]["verdict"], ", ".join(rec["verdict"]["reasons"]) or "—",
                     c["torque_capability_margin_Nm"], p["hint"]["ko"]))

    L = []
    L.append("# Verification Report — Traction Workbench")
    L.append("")
    L.append(f"자동 생성: `python verification/make_report.py` · software {__version__} · commit `{commit}` · "
             f"{time.strftime('%Y-%m-%d')} · Python {platform.python_version()}, NumPy {numpy.__version__}, "
             f"SciPy {scipy.__version__}")
    L.append("")
    L.append("> 범위: 합성(synthetic) 참조 fixture에 대한 검증(verification)입니다. 하드웨어·공급사 데이터·외부 "
             "시뮬레이터에 대한 validation은 수행하지 않았습니다. 수치 자릿수는 회귀 검산용이며 실제 제품 정확도가 아닙니다.")
    L.append("")
    L.append("## 1. 요약")
    L.append("")
    L.append("| 항목 | 결과 |")
    L.append("|---|---|")
    L.append(f"| 참조 패키지 무결성 (manifest SHA-256, 10 files) | {'OK' if acc['manifest_ok'] else 'MISMATCH'} |")
    L.append(f"| 독립 fixture 검산 (production 코드 미사용) | {ind_pass}/{ind_total} pass ({t_ind:.0f} s) |")
    L.append(f"| Production vs golden acceptance | {sum(r['pass'] for r in acc['rows'])}/{len(acc['rows'])} pass |")
    L.append(f"| pytest | {py_line.strip()} ({t_py:.0f} s) |")
    L.append(f"| 데스크톱 앱 self-test (headless, `twb selftest`) | {st['passed']}/{st['total']} pass ({t_st:.0f} s) |")
    L.append("")
    L.append("## 2. 독립 fixture 검산 (`verification/independent_fixture_check.py`)")
    L.append("")
    L.append("Production 패키지를 import하지 않고, production과 다른 방법으로 fixture를 다시 계산했습니다: 정방향은 식 직접 대입, "
             "역문제는 전류각 매개변수화(id = I cos φ, iq = I sin φ)와 조밀 탐색 + 경계 bisection + MTPA 접선 조건, "
             "capability는 KKT 승수·Lagrangian Hessian·오목 상한 재계산, flux map은 해석 포텐셜과 중앙차분 비교입니다.")
    L.append("")
    L.append("관찰: 내부점(MTPA) golden 해 I00/I09/I10은 목적함수가 평탄해 정확한 접선 해와 최대 5.5e-6 A 차이가 있습니다 "
             "(50자리 mpmath로 확인; 허용오차 1e-3 A 이내). expected 값은 수정하지 않았습니다.")
    L.append("")
    L.append("```")
    L.extend(mtpa)
    L.append(next((ln for ln in out_ind.splitlines() if "independent fixture checks passed" in ln), ""))
    L.append("```")
    L.append("")
    L.append("## 3. Production vs golden acceptance (`twb acceptance`)")
    L.append("")
    L.append("허용오차는 Handoff H11: 직접 대입 정규화 오차 ≤ 1e-10, golden id/iq ≤ 1e-3 A (전압 1e-3 V, 전력 0.5 W), "
             "capability bound gap ≤ max(0.1 N·m, 0.0005 × torque scale).")
    L.append("")
    L.append("| group | case | metric | value | tolerance | labels | result |")
    L.append("|---|---|---|---:|---:|---|---|")
    for r in acc["rows"]:
        v = "—" if r["value"] is None else f"{r['value']:.3e}"
        tol = "—" if r["tolerance"] is None else f"{r['tolerance']:.3g}"
        extra = " (certified)" if r.get("certified") else ""
        L.append(f"| {r['group']} | `{r['case']}` | {r['metric']}{extra} | {v} | {tol} | "
                 f"{'OK' if r['labels_ok'] else 'MISMATCH'} | {'PASS' if r['pass'] else 'FAIL'} |")
    L.append("")
    L.append("## 4. 대표 엔지니어링 질문 (`twb demo`)")
    L.append("")
    L.append("| 요구 | 판정 | 사유 | 토크 여유 [N·m] | 의미 |")
    L.append("|---|---|---|---:|---|")
    for rid, v, reasons, margin, hint in demo:
        L.append(f"| {rid} | **{v}** | {reasons} | {'—' if margin is None else f'{margin:.4f}'} | {hint} |")
    L.append("")
    L.append("## 5. 데스크톱 앱 self-test (`twb selftest`)")
    L.append("")
    L.append("모든 페이지를 실제 코드 경로로 실행합니다(작업은 동기 실행): 예시 8건의 판정, 운전점 탐색(클릭 정방향 평가), 궤적, "
             "성능 곡선·맵, 설계·병목, 안전 스크리닝 4종, 열 가용성, golden acceptance, PDF 보고서, flux-map 드라이브 판정, 다크 테마. "
             "배포 빌드(`packaging/build.py`, CI Windows job)는 같은 검사를 **동결된 실행 파일**에서 수행합니다.")
    L.append("")
    L.append("| check | result | detail |")
    L.append("|---|---|---|")
    for c in st["checks"]:
        L.append(f"| {c['check']} | {'PASS' if c['ok'] else 'FAIL'} | {c['detail'][:120].replace('|', '/')} |")
    L.append("")
    L.append("## 6. 재현 방법")
    L.append("")
    L.append("```bash")
    L.append("pip install -e '.[gui,test]'")
    L.append("python verification/independent_fixture_check.py   # 독립 검산")
    L.append("twb acceptance                                      # production vs golden")
    L.append("QT_QPA_PLATFORM=offscreen python -m pytest -q      # 전체 테스트")
    L.append("twb selftest out/selftest                           # 데스크톱 앱 자체 검사")
    L.append("python verification/make_report.py                  # 이 문서 재생성")
    L.append("```")
    L.append("")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "VERIFICATION_REPORT.md").write_text("\n".join(L), encoding="utf-8")
    print(f"independent {ind_pass}/{ind_total}; acceptance {sum(r['pass'] for r in acc['rows'])}/{len(acc['rows'])}; "
          f"pytest: {py_line.strip()}; selftest {st['passed']}/{st['total']}")
    return 0 if (rc_ind == 0 and rc_py == 0 and rc_st == 0 and acc["all_pass"] and acc["manifest_ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())
