"""Optional DC source coupling (engineering review 6198099, priority 2).

The requirement's Vdc is normally the INVERTER DC TERMINAL voltage: a customer's guaranteed minimum terminal voltage
already contains the source drop and nothing is subtracted again.  When only the battery open-circuit voltage (OCV)
is known, the terminal voltage follows from a declared Thevenin source

    V_inv = V_oc - R_eq * I_dc,   I_dc = P_dc / V_inv   ->   V_inv^2 - V_oc V_inv + R_eq P_dc = 0,

with P_dc the policy point's DC power at V_inv (the point depends on the voltage it gets): the upper root
V_inv = (V_oc + sqrt(V_oc^2 - 4 R_eq P_dc)) / 2 is the operating point, solved as a fixed point.  Signs follow the
port convention: motoring P_dc > 0 lowers the terminal voltage, regeneration P_dc < 0 raises it above V_oc.

Model-free bound: the source delivers at most V_oc^2 / (4 R_eq); a motoring request whose shaft power alone exceeds
it cannot be met by any drive on this source (P_dc >= P_shaft).  Outside the declared validity of the source model
(OCV range, DC current range where R_eq was characterised) nothing is resolved.  No electrochemical model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import InputValidationError
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.policy import PolicyEvaluator
from ..validation import finite as _finite

MAX_TRANSFER_REL_TOL = 1e-12       # relative band around V_oc^2 / (4 R_eq): the proof and the root share it


@dataclass(frozen=True)
class TheveninSource:
    R_eq_ohm: float
    basis: str
    valid_current_A: tuple | None = None         # (I_min, I_max) signed DC current (charge < 0) where R_eq holds
    valid_ocv_V: tuple | None = None
    label: str = ""

    def __post_init__(self):
        r = _finite("R_eq_ohm", self.R_eq_ohm)
        if r < 0:
            raise InputValidationError("the source resistance must be >= 0", field="R_eq_ohm")
        object.__setattr__(self, "R_eq_ohm", r)
        if not str(self.basis).strip():
            raise InputValidationError("a source model needs its basis (pack data / measurement at SOC, temperature)",
                                       field="source.basis")
        for name in ("valid_current_A", "valid_ocv_V"):
            v = getattr(self, name)
            if v is not None:
                lo, hi = (_finite(name, x) for x in v)
                if lo > hi:
                    raise InputValidationError(f"{name} must be [low, high]", field=name)
                object.__setattr__(self, name, (lo, hi))

    def max_transfer_W(self, V_oc: float) -> float:
        return math.inf if self.R_eq_ohm == 0 else V_oc ** 2 / (4.0 * self.R_eq_ohm)

    def describe(self) -> dict:
        return {"kind": "thevenin", "R_eq_ohm": self.R_eq_ohm, "basis": self.basis,
                "valid_current_A": self.valid_current_A, "valid_ocv_V": self.valid_ocv_V, "label": self.label}


def resolve_terminal_voltage(drive, scenario: Scenario, source: TheveninSource, T_request: float,
                             settings: NumericalSettings = DEFAULT_SETTINGS, max_iter: int = 60,
                             tol_V: float = 1e-6) -> dict:
    """The inverter terminal voltage of the policy point for ``T_request`` when ``scenario.Vdc_V`` is the source OCV.

    status: RESOLVED | NO_SOLUTION (proven: the shaft power exceeds the source's maximum transfer) |
    NOT_RESOLVED (no fixed point from this start / the point at an iterate is not established) |
    OUTSIDE_SOURCE_MODEL (OCV or the resulting current outside the declared validity)."""
    V_oc = float(scenario.Vdc_V)
    out = {"V_oc_V": V_oc, "R_eq_ohm": source.R_eq_ohm, "source": source.describe(),
           "max_transfer_W": source.max_transfer_W(V_oc)}
    if source.valid_ocv_V is not None and not (source.valid_ocv_V[0] <= V_oc <= source.valid_ocv_V[1]):
        return {**out, "status": "OUTSIDE_SOURCE_MODEL",
                "reason": f"OCV {V_oc:g} V outside the source model's validity {list(source.valid_ocv_V)} V"}
    w = scenario.speed_rpm * 2.0 * math.pi / 60.0
    p_shaft = T_request * w
    p_max = out["max_transfer_W"]
    # one boundary rule for the proof and the root (review of 63a2b61, 2.4): beyond the band the request is proven
    # impossible, inside it neither proven nor resolvable (only a lossless drive at V_oc / 2 could meet it), below
    # it the radicand V_oc^2 - 4 R_eq P_shaft >= 4 R_eq band > 0 and the root exists
    band = MAX_TRANSFER_REL_TOL * p_max if math.isfinite(p_max) else math.inf
    if math.isfinite(p_max) and p_shaft > p_max + band:
        return {**out, "status": "NO_SOLUTION", "P_shaft_W": p_shaft,
                "reason": f"the shaft power {p_shaft:.6g} W alone exceeds the most this source can deliver, "
                          f"V_oc^2 / (4 R_eq) = {p_max:.6g} W (P_dc >= P_shaft when motoring): no "
                          f"drive meets the request on this source"}
    if math.isfinite(p_max) and p_shaft >= p_max - band:
        return {**out, "status": "NOT_RESOLVED", "P_shaft_W": p_shaft, "boundary": True,
                "reason": f"the shaft power {p_shaft:.6g} W equals the source's maximum transfer V_oc^2 / (4 R_eq) = "
                          f"{p_max:.6g} W within the numerical tolerance ({MAX_TRANSFER_REL_TOL:g} relative): only a "
                          f"lossless drive at V_oc / 2 could meet it - neither proven impossible nor resolved"}
    if p_shaft > 0 and source.R_eq_ohm > 0:
        # every motoring point has P_dc >= P_shaft, so its terminal voltage is at most the upper root for P_shaft
        out["V_terminal_max_V"] = 0.5 * (V_oc + math.sqrt(V_oc ** 2 - 4.0 * source.R_eq_ohm * p_shaft))
    V, hist = V_oc, []
    for k in range(1, max_iter + 1):
        sol = PolicyEvaluator(drive, scenario.with_(Vdc_V=V), settings).solve(T_request)
        pt = sol.point
        if pt is None or pt.Pdc_W is None:
            cap = out.get("V_terminal_max_V")
            return {**out, "status": "NOT_RESOLVED", "V_last_V": V, "iterations": k,
                    "reason": f"the source drop takes the terminal voltage to {V:.6g} V, where the policy point is not "
                              f"established ({sol.policy_claim.status.value}: {sol.policy_claim.detail})"
                              + ("" if cap is None else f"; no operating point can see more than {cap:.6g} V at this "
                                                        f"shaft power")}
        disc = V_oc ** 2 - 4.0 * source.R_eq_ohm * pt.Pdc_W
        if disc < 0:
            return {**out, "status": "NOT_RESOLVED", "V_last_V": V, "iterations": k, "P_dc_W": pt.Pdc_W,
                    "reason": f"P_dc {pt.Pdc_W:.6g} W at {V:.6g} V exceeds the source's maximum transfer "
                              f"{out['max_transfer_W']:.6g} W (no terminal voltage for this point)"}
        V_new = 0.5 * (V_oc + math.sqrt(disc))
        hist.append(V_new)
        if abs(V_new - V) <= tol_V:
            V = V_new
            break
        V = V_new
    else:
        return {**out, "status": "NOT_RESOLVED", "V_last_V": V, "iterations": max_iter,
                "reason": "the terminal-voltage fixed point did not converge"}
    # the point AT the resolved voltage (the one the requirement is judged at); at a capability edge it can be lost
    sol = PolicyEvaluator(drive, scenario.with_(Vdc_V=V), settings).solve(T_request)
    pt = sol.point
    if pt is None or pt.Pdc_W is None:
        return {**out, "status": "NOT_RESOLVED", "V_last_V": V, "iterations": len(hist),
                "reason": f"at the fixed point {V:.6g} V the policy point is not established "
                          f"({sol.policy_claim.status.value}: {sol.policy_claim.detail})"}
    I = pt.Pdc_W / V
    res = {**out, "status": "RESOLVED", "V_terminal_V": V, "I_dc_A": I, "P_dc_W": pt.Pdc_W,
           "sag_V": V_oc - V, "iterations": len(hist),
           "direction": "discharge" if pt.Pdc_W > 0 else "charge" if pt.Pdc_W < 0 else "none"}
    if source.valid_current_A is not None and not (source.valid_current_A[0] <= I <= source.valid_current_A[1]):
        return {**res, "status": "OUTSIDE_SOURCE_MODEL",
                "reason": f"I_dc {I:.6g} A outside the current range {list(source.valid_current_A)} A where R_eq "
                          f"was characterised"}
    return res


def source_from_dict(d: dict | None) -> TheveninSource | None:
    if not d:
        return None
    kind = str(d.get("kind", "thevenin")).lower()
    if kind != "thevenin":
        raise InputValidationError("source model kind must be 'thevenin' (no electrochemical model)",
                                   field="source_model.kind")
    if d.get("R_eq_mohm") in (None, "") and d.get("R_eq_ohm") in (None, ""):
        raise InputValidationError("the Thevenin source needs R_eq (R_eq_mohm or R_eq_ohm)", field="source_model")
    R = float(d["R_eq_ohm"]) if d.get("R_eq_ohm") not in (None, "") else float(d["R_eq_mohm"]) * 1e-3
    rng = lambda key: None if not d.get(key) else tuple(float(x) for x in d[key])     # noqa: E731
    return TheveninSource(R, str(d.get("basis", "")), rng("valid_current_A"), rng("valid_ocv_V"),
                          str(d.get("label", "")))
