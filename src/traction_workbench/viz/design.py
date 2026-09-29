"""Design-sensitivity curves: policy capability (and the policy point for a request) vs one parameter."""

from __future__ import annotations

import numpy as np

from .. import progress as P
from ..analysis.variation import PARAMETERS, apply, describe_parameter, get_value
from ..errors import InputValidationError
from ..models.components import DriveModel
from ..scenario import Scenario
from ..service import CURVE_SETTINGS
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator
from .sweeps import OK, UNKNOWN, Progress, _tick, point_fields, policy_point


def capability_vs_parameter(drive: DriveModel, scenario: Scenario, parameter: str, values, T_request: float | None = None,
                            direction: int = 1, progress: Progress = None) -> dict:
    if parameter not in PARAMETERS:
        raise InputValidationError(f"unknown parameter {parameter!r}", field="parameter")
    values = np.asarray(values, float)
    cap = np.full(values.size, np.nan)
    status = np.full(values.size, UNKNOWN, dtype=int)
    i_peak = np.full(values.size, np.nan)
    pdc = np.full(values.size, np.nan)
    v_margin = np.full(values.size, np.nan)
    active: list[list[str]] = [[] for _ in values]
    with P.span(values.size, "parameter values") as steps:
        for i, v in enumerate(values):
            steps.step()
            try:
                d2, s2 = apply(drive, scenario, parameter, float(v))
            except InputValidationError:
                _tick(progress, (i + 1) / values.size, "")
                continue
            ev = PolicyEvaluator(d2, s2, CURVE_SETTINGS)
            pc = policy_capability(ev, direction, certify=False)
            if pc.value_Nm is not None:
                cap[i] = pc.value_Nm
            if T_request is not None:
                st, pt = policy_point(PolicyEvaluator(d2, s2), T_request)
                status[i] = st
                if pt is not None:
                    f = point_fields(pt)
                    i_peak[i] = f["I_peak_A"]
                    pdc[i] = f["Pdc_W"] if f["Pdc_W"] is not None else np.nan
                    v_margin[i] = f["v_margin_V"]
                    active[i] = [c.name for c in pt.active()]
            _tick(progress, (i + 1) / values.size, f"{parameter} = {v:g}")
    return {"parameter": describe_parameter(parameter), "values": values, "capability_Nm": cap,
            "baseline": get_value(drive, scenario, parameter), "T_request_Nm": T_request, "status": status,
            "i_peak_A": i_peak, "Pdc_W": pdc, "v_margin_V": v_margin, "active": active, "direction": direction,
            "speed_rpm": scenario.speed_rpm, "Vdc_V": scenario.Vdc_V, "ok_code": OK}
