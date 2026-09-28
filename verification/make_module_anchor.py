#!/usr/bin/env python3
"""Write the regression anchors of the core decision path with the DATASHEET MODULE loss model active.

The golden acceptance cases and the independent fixture check exercise the built-in drive, which carries the
quadratic loss surrogate; with a datasheet module model attached, the same core (forward evaluation, policy solve,
DC claims, capability, sizing) had no fixed reference values.  These anchors pin that path so that a change of the
module loss model shows up wherever it moves a decision, not only on the power-electronics pages.

They are IMPLEMENTATION regression anchors (V0-V2 bookkeeping), not an independent reference: regenerate them only
for an intended model change and state the reason in the commit.

    python verification/make_module_anchor.py
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "tests" / "fixtures" / "module_core_anchor.json"

FORWARD = [(3000.0, 600.0, -100.0, 300.0), (8000.0, 600.0, -250.0, 150.0), (0.0, 600.0, 0.0, 300.0),
           (5000.0, 600.0, -150.0, -200.0)]
REQUESTS = [(2000.0, 250.0, 600.0), (6000.0, 150.0, 600.0), (11000.0, 40.0, 600.0), (4000.0, -120.0, 600.0),
            (12000.0, 150.0, 600.0)]
CAPABILITY = [(3000.0, 600.0, +1), (10000.0, 600.0, +1), (10000.0, 600.0, -1)]
SIZING = (12000.0, 150.0, "Vdc_V", (450.0, 800.0))


SCALED = {"exponent": 1.3, "valid_V": [400.0, 800.0],
          "basis": "anchor variant: synthetic E ~ Vdc^1.3 switching-energy law (declared, not a datasheet claim)"}


def module_drive(scaled: bool = False):
    """The built-in drive with the example datasheet module as its inverter loss model (optionally with a declared
    Vdc scaling law of the switching energies)."""
    from traction_workbench import api
    from traction_workbench import service as S
    d = S.resolve_drive(None)
    spec = {**api.EXAMPLE_MODULE, **({"vdc_scaling": SCALED} if scaled else {})}
    model = api.module_model_from_dict(spec)
    tj = float(api.EXAMPLE_MODULE["Tj_eval_C"])
    return replace(d, inverter=replace(d.inverter, loss=None, module_loss=model, module_Tj_C=tj)), tj


def compute() -> dict:
    from traction_workbench import api
    from traction_workbench.analysis.sizing import size_parameter
    from traction_workbench.physics import forward_evaluation
    from traction_workbench.scenario import Scenario
    from traction_workbench.solvers.capability import policy_capability
    from traction_workbench.solvers.policy import PolicyEvaluator
    md, tj = module_drive()
    lim = api._limits({})
    out = {"forward": [], "policy": [], "capability": [], "sizing": None}
    for n, vdc, idv, iqv in FORWARD:
        fr = forward_evaluation(md, Scenario("anchor", n, vdc, lim), idv, iqv)
        p = fr.point
        det = (p.inverter_loss_detail or {}) if p is not None else {}
        out["forward"].append({"speed_rpm": n, "Vdc_V": vdc, "id_A": idv, "iq_A": iqv, "evaluable": fr.evaluable,
                               "Pinv_W": None if p is None else p.Pinv_W, "Pdc_W": None if p is None else p.Pdc_W,
                               "hottest_position_W": det.get("hottest_position_W"),
                               "established": det.get("established")})
    for n, T, vdc in REQUESTS:
        sol = PolicyEvaluator(md, Scenario("anchor", n, vdc, lim)).solve(T)
        p = sol.point
        out["policy"].append({"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc,
                              "policy_claim": sol.policy_claim.status.value,
                              "claims": {c.name: c.status.value for c in sol.claims},
                              "id_A": None if p is None else p.id_A, "iq_A": None if p is None else p.iq_A,
                              "Pdc_W": None if p is None else p.Pdc_W, "Pinv_W": None if p is None else p.Pinv_W})
    for n, vdc, sgn in CAPABILITY:
        cap = policy_capability(PolicyEvaluator(md, Scenario("anchor", n, vdc, lim)), sgn)
        out["capability"].append({"speed_rpm": n, "Vdc_V": vdc, "direction": sgn, "value_Nm": cap.value_Nm,
                                  "accepted": cap.accepted})
    # outside the switching test voltage without a declared scaling law: UNKNOWN for a stated domain reason
    sol = PolicyEvaluator(md, Scenario("anchor", 12000.0, 700.0, lim)).solve(150.0)
    out["unscaled_700V"] = {"claims": {c.name: c.status.value for c in sol.claims},
                            "reasons": {c.name: [r.value for r in c.reasons] for c in sol.claims}}
    n, T, par, rng = SIZING
    ms, _ = module_drive(scaled=True)
    sz = size_parameter(ms, Scenario("anchor", n, 600.0, lim), T, par, rng, 41).to_dict()
    out["sizing"] = {"module_vdc_scaling": SCALED, "speed_rpm": n, "torque_Nm": T, "parameter": par,
                     "range": list(rng),
                     "minimal_feasible_value": sz.get("minimal_feasible_value"),
                     "regions": [r["status"] for r in sz.get("regions", [])]}
    return {"module": api.EXAMPLE_MODULE["name"], "Tj_eval_C": tj, **out}


def main() -> int:
    from traction_workbench import __version__
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    doc = {"kind": "implementation regression anchor (not an independent reference)",
           "generated": {"software": __version__, "commit": commit,
                         "date_utc": _dt.datetime.now(_dt.timezone.utc).date().isoformat()},
           "tolerance": {"forward_rel": 1e-9, "solver_rel": 1e-6, "solver_abs_A": 1e-4},
           "values": compute()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, default=float) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
