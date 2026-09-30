"""Optional DC source coupling (engineering review 6198099, priority 2).

The requirement's Vdc is normally the INVERTER DC TERMINAL voltage: a customer's guaranteed minimum terminal voltage
already contains the source drop and nothing is subtracted again.  When only the battery open-circuit voltage (OCV)
is known, the terminal voltage follows from a declared Thevenin source

    V_inv = V_oc - R_eq * I_dc,   I_dc = P_dc / V_inv   ->   V_inv^2 - V_oc V_inv + R_eq P_dc = 0,

with P_dc the policy point's DC power at V_inv (the point depends on the voltage it gets): the upper root
V_inv = (V_oc + sqrt(V_oc^2 - 4 R_eq P_dc)) / 2 is the operating point.  Signs follow the port convention: motoring
P_dc > 0 lowers the terminal voltage, regeneration P_dc < 0 raises it above V_oc.

Model-free bounds: losses are passive, so every operating point has P_dc >= P_shaft.  Hence (i) the source delivers
at most V_oc^2 / (4 R_eq) and a motoring request whose shaft power alone exceeds it cannot be met by any drive on
this source, and (ii) no operating point sees a terminal voltage above V_hi = (V_oc + sqrt(V_oc^2 - 4 R_eq P_shaft))/2
- above the OCV for regeneration (engineering review 2 of 63a2b61, F-04 / F-14).

The fixed point is found by iterating V <- f(V) (the upper root at the policy point's P_dc(V)) DOWNWARD from V_hi.
With the I^2-form loss (quadratic surrogate: on the torque curve P_dc depends on I^2 only) the minimum current can
only fall as the voltage budget grows, so f is non-decreasing and f(V_hi) <= V_hi: the iterates fall monotonically
and never pass below the largest fixed point V* (the stable, highest-voltage operating point).  The iteration either
converges to V*, or reaches an iterate without a policy point - electrical feasibility only shrinks with the voltage
budget, so then no fixed point exists (a proof).  Without the I^2 form (datasheet module loss, Vdc-dependent switching)
the same iteration runs, but leaving the existence set proves nothing.  Electrical infeasibility AT V_hi is a proof
for every loss model.  Outside the declared validity of the source model (OCV range, DC current range where R_eq was
characterised) nothing is resolved.  No electrochemical model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import InputValidationError
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.policy import PolicyEvaluator
from ..status import Status
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


def _upper_root(V_oc: float, R: float, P: float) -> float:
    return 0.5 * (V_oc + math.sqrt(V_oc ** 2 - 4.0 * R * P))


def resolve_terminal_voltage(drive, scenario: Scenario, source: TheveninSource, T_request: float,
                             settings: NumericalSettings = DEFAULT_SETTINGS, max_iter: int = 200,
                             tol_V: float = 1e-6) -> dict:
    """The inverter terminal voltage of the policy point for ``T_request`` when ``scenario.Vdc_V`` is the source OCV.

    status: RESOLVED | NO_SOLUTION (proven: the shaft power exceeds the source's maximum transfer, or no operating
    point exists at any terminal voltage the source can give) | NOT_RESOLVED (neither resolved nor proven absent) |
    OUTSIDE_SOURCE_MODEL (OCV or the resulting current outside the declared validity).  ``V_terminal_max_V`` is the
    model-free ceiling V_hi; ``history_V`` the iterates from it; ``monotone`` whether the I^2 argument holds."""
    V_oc = float(scenario.Vdc_V)
    R = source.R_eq_ohm
    out = {"V_oc_V": V_oc, "R_eq_ohm": R, "source": source.describe(), "max_transfer_W": source.max_transfer_W(V_oc)}
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
                "reason": f"the shaft power {p_shaft:.4g} W alone exceeds the most this source can deliver, "
                          f"V_oc^2 / (4 R_eq) = {p_max:.4g} W (P_dc >= P_shaft when motoring): no "
                          f"drive meets the request on this source"}
    if math.isfinite(p_max) and p_shaft >= p_max - band:
        return {**out, "status": "NOT_RESOLVED", "P_shaft_W": p_shaft, "boundary": True,
                "reason": f"the shaft power {p_shaft:.6g} W equals the source's maximum transfer V_oc^2 / (4 R_eq) = "
                          f"{p_max:.6g} W within the numerical tolerance ({MAX_TRANSFER_REL_TOL:g} relative): only a "
                          f"lossless drive at V_oc / 2 could meet it - neither proven impossible nor resolved"}
    if R == 0.0:
        # an ideal source: the terminal voltage is the OCV whatever the point draws - the drive side decides alone
        pt = PolicyEvaluator(drive, scenario, settings).solve(T_request).point
        pdc = None if pt is None else pt.Pdc_W
        return {**out, "status": "RESOLVED", "V_terminal_V": V_oc, "V_terminal_max_V": V_oc, "sag_V": 0.0,
                "P_dc_W": pdc, "I_dc_A": None if pdc is None else pdc / V_oc, "iterations": 0, "history_V": [V_oc],
                "method": "ideal source (R_eq = 0): no drop",
                "direction": "none" if not pdc else "discharge" if pdc > 0 else "charge"}
    V_hi = _upper_root(V_oc, R, p_shaft)
    out.update({"P_shaft_W": p_shaft, "V_terminal_max_V": V_hi,
                "bound": f"every operating point has P_dc >= P_shaft = {p_shaft:.4g} W (passive losses), so none sees "
                         f"more than V_hi = {V_hi:.4g} V at the terminal"
                         + (" (above the OCV: regeneration)" if V_hi > V_oc else "")})
    ev = PolicyEvaluator(drive, scenario.with_(Vdc_V=V_hi), settings)
    monotone = ev.k.i2_dc is not None and not ev.k.pointwise_loss
    out["monotone"] = monotone
    out["method"] = ("fixed-point iteration downward from V_hi; " + (
        "I^2-form loss: the map is non-decreasing, so the iterates fall to the largest fixed point and an iterate "
        "without a policy point proves that none exists" if monotone else
        f"{ev.k.loss_label}: no I^2 monotonicity - an iterate without a policy point proves nothing"))
    sol = ev.solve(T_request)
    V, hist = V_hi, [V_hi]
    for k in range(max_iter):
        pt = sol.point
        if pt is None or pt.Pdc_W is None:
            elec = sol.electrical.status
            proven = pt is None and elec is Status.INFEASIBLE and (k == 0 or monotone)
            where = (f"at V_hi = {V_hi:.4g} V, the highest terminal voltage any operating point can see on this "
                     f"source" if k == 0 else f"at the iterate {V:.4g} V (iterating down from V_hi = {V_hi:.4g} V)")
            if proven:
                return {**out, "status": "NO_SOLUTION", "V_last_V": V, "iterations": k, "history_V": hist,
                        "reason": f"no electrical solution {where}; the voltage budget only shrinks below it"
                                  + ("" if k == 0 else ", and with the I^2-form loss every fixed point would lie at "
                                                       "or below each iterate")
                                  + f": no operating point exists on this source (no operating point can see more "
                                    f"than {V_hi:.4g} V at this shaft power)"}
            probs = [] if pt is None else list((pt.inverter_loss_detail or {}).get("problems") or [])
            why = (f"P_dc is not established ({ev.k.loss_label}" + (f": {'; '.join(probs)}" if probs else "") + ")"
                   if pt is not None else
                   f"the policy point is not established ({sol.policy_claim.status.value}: "
                   f"{sol.policy_claim.detail})")
            return {**out, "status": "NOT_RESOLVED", "V_last_V": V, "iterations": k, "history_V": hist,
                    "reason": f"{why} {where}"
                              + ("" if k == 0 or monotone else f" - without the I^2 argument ({ev.k.loss_label}) "
                                                                "this does not prove that no fixed point exists")
                              + f"; no operating point can see more than {V_hi:.4g} V at this shaft power"}
        disc = V_oc ** 2 - 4.0 * R * pt.Pdc_W
        if disc < 0:
            status = "NO_SOLUTION" if monotone else "NOT_RESOLVED"
            return {**out, "status": status, "V_last_V": V, "iterations": k, "history_V": hist, "P_dc_W": pt.Pdc_W,
                    "reason": f"P_dc {pt.Pdc_W:.4g} W at {V:.4g} V exceeds the source's maximum transfer "
                              f"{p_max:.4g} W (no terminal voltage for this point)"
                              + (": with the I^2-form loss P_dc only grows as the voltage falls, so no fixed point "
                                 "exists" if monotone else "")}
        V_new = _upper_root(V_oc, R, pt.Pdc_W)
        if monotone and V_new > V + max(tol_V, 1e-9 * V):
            monotone = False          # a numerically non-monotone step (e.g. an uncertified point): no proof from here
            out["monotone"] = False
        hist.append(V_new)
        if abs(V_new - V) <= tol_V:
            V = V_new
            break
        V = V_new
        sol = PolicyEvaluator(drive, scenario.with_(Vdc_V=V), settings).solve(T_request)
    else:
        return {**out, "status": "NOT_RESOLVED", "V_last_V": V, "iterations": max_iter, "history_V": hist,
                "reason": f"the terminal-voltage iteration from V_hi = {V_hi:.4g} V did not converge in {max_iter} "
                          f"steps (last change {abs(hist[-1] - hist[-2]):.3g} V) - close to the source's maximum "
                          f"transfer the map is ill-conditioned"}
    # the point AT the resolved voltage (the one the requirement is judged at); at a capability edge it can be lost
    sol = PolicyEvaluator(drive, scenario.with_(Vdc_V=V), settings).solve(T_request)
    pt = sol.point
    if pt is None or pt.Pdc_W is None:
        return {**out, "status": "NOT_RESOLVED", "V_last_V": V, "iterations": len(hist) - 1, "history_V": hist,
                "reason": f"at the fixed point {V:.6g} V the policy point is not established "
                          f"({sol.policy_claim.status.value}: {sol.policy_claim.detail})"}
    I = pt.Pdc_W / V
    steps = [abs(b - a) for a, b in zip(hist, hist[1:])]
    ratio = next((steps[i + 1] / steps[i] for i in range(len(steps) - 2, -1, -1)
                  if steps[i] > 100 * tol_V and i + 1 < len(steps)), None)
    res = {**out, "status": "RESOLVED", "V_terminal_V": V, "I_dc_A": I, "P_dc_W": pt.Pdc_W,
           "sag_V": V_oc - V, "iterations": len(hist) - 1, "history_V": hist, "contraction": ratio,
           "direction": "discharge" if pt.Pdc_W > 0 else "charge" if pt.Pdc_W < 0 else "none",
           "fixed_point": ("the largest fixed point (approached from above: stable)" if out["monotone"] else
                           "a fixed point (the I^2 argument does not hold: uniqueness not shown)")}
    if source.valid_current_A is not None and not (source.valid_current_A[0] <= I <= source.valid_current_A[1]):
        return {**res, "status": "OUTSIDE_SOURCE_MODEL",
                "reason": f"I_dc {I:.4g} A outside the current range {list(source.valid_current_A)} A where R_eq "
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
