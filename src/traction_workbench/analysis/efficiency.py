"""Efficiency by control volume (module-efficiency addendum): five boundaries, loss ledger, reducer, mission energy
and the SiC / IGBT module comparison protocol.

Ports carry signed power, electrical -> mechanical positive:

* ``P_dc`` - input at the DECLARED inverter HV DC terminal,
* ``P_ac`` - input at the motor AC terminals,
* ``P_m``  - output at the motor shaft (T_shaft * omega_m; T_em * omega_m is conversion power, not shaft power),
* ``P_o``  - output at the DECLARED reducer / eDrive output (gearbox output, differential output, sum of the
  half-shafts and the tyre contact are different boundaries - the name "eDrive" does not choose one).

For one single-path point without storage change P_dc = P_ac + L_inv, P_ac = P_m + L_m, P_m = P_o + L_r, L >= 0.
Every boundary is judged on its own two ports (a standstill or a missing inverter loss does not erase the others):
positive transfer eta = P_out / P_in, negative transfer eta = |P_in| / |P_out|; mixed flow, no useful output and a
denominator that is zero within the numerical tolerance / declared uncertainty are N/A or UNKNOWN with the reason,
never an epsilon ratio; eta > 1 (negative loss) is reported raw as an accounting inconsistency, never clamped.
The telescoping identities eta_im = eta_inv eta_m and eta_ed = eta_inv eta_m eta_r hold only for the same point,
direction and ports - efficiencies of different points / temperatures are never multiplied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from ..errors import InputValidationError
from ..models.module_loss import ModuleLossModel
from ..validation import finite as _finite, interval as _interval

BOUNDARIES = (
    ("inverter", "P_dc", "P_ac", "HV DC terminal <-> motor AC terminal"),
    ("motor", "P_ac", "P_m", "motor AC terminal <-> motor shaft"),
    ("inverter_motor", "P_dc", "P_m", "HV DC terminal <-> motor shaft"),
    ("reducer", "P_m", "P_o", "motor shaft <-> declared output"),
    ("edrive", "P_dc", "P_o", "HV DC terminal <-> declared output"),
)
DEFINED, NA, UNKNOWN, INCONSISTENT = "DEFINED", "N/A", "UNKNOWN", "INCONSISTENT"


# --------------------------------------------------------------------------------------------- one boundary

def _sign(p: float, tol: float, unc: float | None):
    if abs(p) <= tol:
        return 0
    if unc is not None and abs(p) <= unc:
        return "?"                                     # the sign is not established within the declared uncertainty
    return 1 if p > 0 else -1


def boundary_eta(p_in: float | None, p_out: float | None, tol_W: float, in_name: str = "P_in",
                 out_name: str = "P_out", unc_in_W: float | None = None, unc_out_W: float | None = None) -> dict:
    """Efficiency of one control volume from its two signed port powers (both measured electrical -> mechanical).

    ``p_in`` is the power entering at the upstream port, ``p_out`` the power leaving at the downstream port.
    """
    base = {"P_in_W": p_in, "P_out_W": p_out, "eta": None, "direction": None, "definition": None,
            "loss_W": None if (p_in is None or p_out is None) else p_in - p_out}
    if p_in is None or p_out is None:
        miss = [n for n, v in ((in_name, p_in), (out_name, p_out)) if v is None]
        return {**base, "status": UNKNOWN, "reason": f"{', '.join(miss)} not established (a missing loss or port power "
                                                     f"is never set to zero)"}
    s_in, s_out = _sign(p_in, tol_W, unc_in_W), _sign(p_out, tol_W, unc_out_W)
    if "?" in (s_in, s_out):
        return {**base, "status": UNKNOWN, "reason": "direction not established: a port power is within its declared "
                                                     "uncertainty of zero (no epsilon ratio is formed)"}
    if s_in == 1 and s_out == 1:
        eta, d, df = p_out / p_in, "forward", f"{out_name}/{in_name}"
    elif s_in == -1 and s_out == -1:
        eta, d, df = abs(p_in) / abs(p_out), "reverse", f"|{in_name}|/|{out_name}|"
    elif s_in == 0 and s_out == 0:
        return {**base, "status": NA, "reason": "no throughput at either port"}
    elif s_in == 1 and s_out == 0:
        return {**base, "status": NA, "reason": f"input dissipated with no useful output at {out_name} (e.g. standstill "
                                                f"holding torque); losses are reported"}
    elif s_in == 0 and s_out == -1:
        return {**base, "status": NA, "reason": f"power enters at {out_name} and is dissipated, none delivered at "
                                                f"{in_name} (braking without recovery)"}
    elif s_in == 1 and s_out == -1:
        return {**base, "status": NA, "reason": f"mixed flow: power enters at both {in_name} and {out_name} and is "
                                                f"dissipated inside the boundary"}
    else:
        return {**base, "status": INCONSISTENT, "reason": "power leaves at a port without an input (a source inside the "
                                                          "boundary or a sign / definition error)"}
    out = {**base, "eta": eta, "direction": d, "definition": df}
    if eta > 1.0 + 1e-12:
        return {**out, "status": INCONSISTENT, "reason": f"eta = {eta:.9g} > 1 (negative loss): accounting / storage / "
                                                         f"measurement to be investigated; not clamped"}
    return {**out, "status": DEFINED, "reason": ""}


def five_boundaries(P_dc, P_ac, P_m, P_o, tol_W: float = 1e-6, unc: dict | None = None) -> dict:
    """All five boundaries of one point plus the telescoping identity checks (same point, same direction only)."""
    ports = {"P_dc": P_dc, "P_ac": P_ac, "P_m": P_m, "P_o": P_o}
    u = unc or {}
    res = {}
    for name, a, b, label in BOUNDARIES:
        r = boundary_eta(ports[a], ports[b], tol_W, a, b, u.get(a), u.get(b))
        r["label"] = label
        res[name] = r
    ident = {}
    inv, mot, im, red, ed = (res[k] for k in ("inverter", "motor", "inverter_motor", "reducer", "edrive"))
    if all(x["status"] == DEFINED for x in (inv, mot, im)) and inv["direction"] == mot["direction"] == im["direction"]:
        ident["inverter_motor"] = im["eta"] - inv["eta"] * mot["eta"]
    if all(x["status"] == DEFINED for x in (inv, mot, red, ed)) and \
            inv["direction"] == mot["direction"] == red["direction"] == ed["direction"]:
        ident["edrive"] = ed["eta"] - inv["eta"] * mot["eta"] * red["eta"]
    res["telescoping_residuals"] = ident
    return res


def efficiency_change(eta_a: float, eta_b: float, basis: str = "same input power") -> dict:
    """An efficiency difference in percentage points AND as a loss change (the two are not the same number).

    With the same input power the loss fraction goes from (1 - eta_a) to (1 - eta_b); with the same output power the
    loss per unit output goes from (1/eta_a - 1) to (1/eta_b - 1)."""
    if basis == "same input power":
        la, lb = 1 - eta_a, 1 - eta_b
    elif basis == "same output power":
        la, lb = 1 / eta_a - 1, 1 / eta_b - 1
    else:
        raise InputValidationError("basis must be 'same input power' or 'same output power'", field="basis")
    return {"delta_percentage_points": 100 * (eta_b - eta_a), "loss_fraction_a": la, "loss_fraction_b": lb,
            "relative_loss_change": (lb - la) / la if la > 0 else None, "basis": basis}


# --------------------------------------------------------------------------------------------- reducer

@dataclass(frozen=True)
class LossMap:
    """Directional reducer loss table at the motor side: loss_W[speed, |torque|] (bilinear, no extrapolation)."""

    speeds_rpm: tuple
    torques_Nm: tuple
    loss_W: tuple

    def __post_init__(self):
        s = np.asarray(self.speeds_rpm, float)
        t = np.asarray(self.torques_Nm, float)
        v = np.asarray(self.loss_W, float)
        if s.ndim != 1 or t.ndim != 1 or s.size < 2 or t.size < 2 or np.any(np.diff(s) <= 0) or np.any(np.diff(t) <= 0):
            raise InputValidationError("loss map axes must be strictly increasing with >= 2 points", field="loss_map")
        if v.shape != (s.size, t.size) or not np.all(np.isfinite(v)) or np.any(v < 0):
            raise InputValidationError("loss map values must be finite, >= 0, shape (speeds, torques)", field="loss_map")

    def at(self, n_rpm: float, T_abs: float) -> float | None:
        s = np.asarray(self.speeds_rpm, float)
        t = np.asarray(self.torques_Nm, float)
        v = np.asarray(self.loss_W, float)
        if not (s[0] <= n_rpm <= s[-1] and t[0] <= T_abs <= t[-1]):
            return None
        i = min(max(int(np.searchsorted(s, n_rpm, side="right")) - 1, 0), s.size - 2)
        j = min(max(int(np.searchsorted(t, T_abs, side="right")) - 1, 0), t.size - 2)
        a = (n_rpm - s[i]) / (s[i + 1] - s[i])
        b = (T_abs - t[j]) / (t[j + 1] - t[j])
        return float((1 - a) * (1 - b) * v[i, j] + a * (1 - b) * v[i + 1, j] + (1 - a) * b * v[i, j + 1]
                     + a * b * v[i + 1, j + 1])


@dataclass(frozen=True)
class ReducerModel:
    """Single fixed-ratio reducer, calibrated PER DIRECTION (motoring efficiency is never inverted for regen).

    Parametric form (drag at the motor side, mesh efficiency on the power THROUGH the mesh, review R2 PT-08):
      mesh input  Q = P_m - P_drag
      Q >= 0 (forward through the mesh):  P_o = eta_forward * Q
      Q <  0 (reverse through the mesh):  P_o = Q / eta_reverse
      P_drag = (c0 + c1 |w| + c2 w^2) |w|   (w = motor-side rad/s; zero at standstill - no P/omega divergence)
    The branch follows Q, not the sign of P_m: a motor supplying less than the drag (P_m > 0, Q < 0) is fed from the
    output, and the inverse P_m = P_o / eta_forward + P_drag (P_o >= 0), P_m = eta_reverse P_o + P_drag (P_o < 0) is
    exact on both branches.  Directional loss maps do not define the mixed-flow region (forward map with P_o < 0):
    UNKNOWN there.  Outside the declared speed / torque / oil-temperature domain: UNKNOWN.
    """

    ratio: float                                   # g = omega_motor / omega_output > 0
    output_boundary: str                           # e.g. "gearbox output shaft (differential input)"
    speed_rpm: tuple                               # motor-side speed domain (|n|)
    torque_Nm: tuple                               # motor-side |torque| domain
    oil_temp_C: tuple                              # validated oil-temperature range
    eta_forward: float | None = None
    eta_reverse: float | None = None
    drag_coeffs: tuple = (0.0, 0.0, 0.0)           # c0 [N m], c1 [N m s/rad], c2 [N m s^2/rad^2]
    map_forward: LossMap | None = None
    map_reverse: LossMap | None = None
    basis: str = ""

    def __post_init__(self):
        g = _finite("ratio", self.ratio)
        if g <= 0:
            raise InputValidationError("reducer ratio must be > 0", field="ratio")
        if not self.output_boundary.strip():
            raise InputValidationError("declare the output boundary (gearbox output, differential output, ...)",
                                       field="output_boundary")
        for name in ("speed_rpm", "torque_Nm", "oil_temp_C"):
            object.__setattr__(self, name, _interval(name, getattr(self, name)))
        param = self.eta_forward is not None or self.eta_reverse is not None
        maps = self.map_forward is not None or self.map_reverse is not None
        if param == maps:
            raise InputValidationError("declare either both directional efficiencies (+ drag) or both directional "
                                       "loss maps", field="eta_forward")
        if param:
            for name in ("eta_forward", "eta_reverse"):
                v = getattr(self, name)
                if v is None or not (0.0 < _finite(name, v) <= 1.0):
                    raise InputValidationError(f"{name} must be declared in (0, 1] (per direction, never the inverse of "
                                               "the other)", field=name)
            if any(_finite("drag", c) < 0 for c in self.drag_coeffs) or len(self.drag_coeffs) != 3:
                raise InputValidationError("drag coefficients must be three values >= 0", field="drag_coeffs")
        elif self.map_forward is None or self.map_reverse is None:
            raise InputValidationError("both directional loss maps are required", field="map_reverse")
        if not self.basis.strip():
            raise InputValidationError("a reducer loss model needs its basis (test / supplier map)", field="basis")

    def _drag_W(self, w: float) -> float:
        c0, c1, c2 = self.drag_coeffs
        aw = abs(w)
        return (c0 + c1 * aw + c2 * aw * aw) * aw

    def _domain(self, n_rpm: float, T_m: float, oil_C: float | None) -> str | None:
        if oil_C is None:
            return "oil temperature not stated (the reducer loss depends on it)"
        if not (self.oil_temp_C[0] <= oil_C <= self.oil_temp_C[1]):
            return f"oil temperature {oil_C:g} degC outside the validated {list(self.oil_temp_C)}"
        if not (self.speed_rpm[0] <= abs(n_rpm) <= self.speed_rpm[1]):
            return f"|n| = {abs(n_rpm):g} rpm outside the validated {list(self.speed_rpm)}"
        if not (self.torque_Nm[0] <= abs(T_m) <= self.torque_Nm[1]):
            return f"|T| = {abs(T_m):g} N m outside the validated {list(self.torque_Nm)}"
        return None

    def output_from_motor(self, n_rpm: float, T_m: float, oil_C: float | None, tol_W: float = 1e-6) -> dict:
        """P_o (and the loss) from the motor-shaft side."""
        w = n_rpm * 2 * math.pi / 60.0
        P_m = T_m * w
        bad = self._domain(n_rpm, T_m, oil_C)
        if bad:
            return {"status": UNKNOWN, "P_o_W": None, "loss_W": None, "reason": bad}
        if abs(w) <= 1e-12:
            return {"status": DEFINED, "P_o_W": 0.0, "loss_W": 0.0, "T_o_Nm": None,
                    "reason": "standstill: no power through the reducer (static torque ratio not modelled)"}
        if self.map_forward is not None:
            if P_m > tol_W:
                L = self.map_forward.at(abs(n_rpm), abs(T_m))
                if L is None:
                    return {"status": UNKNOWN, "P_o_W": None, "loss_W": None, "reason": "outside the forward loss map"}
                P_o = P_m - L
                if P_o < 0:
                    return {"status": UNKNOWN, "P_o_W": None, "loss_W": None,
                            "reason": "mixed-flow region (motor input below the loss, output power negative): the "
                                      "directional loss maps do not define this direction"}
            else:
                # reverse: the map is indexed by the motor-side torque magnitude; |P_o| = |P_m| + L
                L = self.map_reverse.at(abs(n_rpm), abs(T_m))
                if L is None:
                    return {"status": UNKNOWN, "P_o_W": None, "loss_W": None, "reason": "outside the reverse loss map"}
                P_o = P_m - L
            return {"status": DEFINED, "P_o_W": P_o, "loss_W": L, "T_o_Nm": P_o / (w / self.ratio),
                    "reason": "directional loss map"}
        Pd = self._drag_W(w)
        Q = P_m - Pd                                   # power into the mesh from the motor side
        P_o = self.eta_forward * Q if Q >= 0.0 else Q / self.eta_reverse
        return {"status": DEFINED, "P_o_W": P_o, "loss_W": P_m - P_o, "T_o_Nm": P_o / (w / self.ratio),
                "reason": "directional efficiencies + drag", "P_drag_W": Pd, "mesh_power_W": Q,
                "mesh_direction": "forward" if Q >= 0.0 else "reverse"}

    def motor_torque_for_output(self, n_rpm: float, T_o: float, oil_C: float | None) -> dict:
        """Motor-shaft torque that delivers T_o at the output (inverse of ``output_from_motor``; parametric form)."""
        if self.map_forward is not None:
            return {"status": UNKNOWN, "T_m_Nm": None, "reason": "inverse of a loss map is not implemented; state the "
                                                                   "demand at the motor shaft"}
        w = n_rpm * 2 * math.pi / 60.0
        if abs(w) <= 1e-12:
            return {"status": UNKNOWN, "T_m_Nm": None, "reason": "standstill: the static torque ratio is not modelled"}
        Pd = self._drag_W(w)
        P_o = T_o * w / self.ratio
        Q = P_o / self.eta_forward if P_o >= 0 else self.eta_reverse * P_o     # mesh input for that output
        P_m = Q + Pd
        T_m = P_m / w
        bad = self._domain(n_rpm, T_m, oil_C)
        if bad:
            return {"status": UNKNOWN, "T_m_Nm": None, "reason": bad}
        return {"status": DEFINED, "T_m_Nm": T_m, "reason": ""}

    def describe(self) -> dict:
        d = {"ratio": self.ratio, "output_boundary": self.output_boundary, "speed_rpm": list(self.speed_rpm),
             "torque_Nm": list(self.torque_Nm), "oil_temp_C": list(self.oil_temp_C), "basis": self.basis}
        if self.map_forward is None:
            d.update({"form": "P_o = eta_f (P_m - P_drag); |P_m| = eta_r |P_o| - P_drag",
                      "eta_forward": self.eta_forward, "eta_reverse": self.eta_reverse,
                      "drag_coeffs": list(self.drag_coeffs)})
        else:
            d["form"] = "directional loss maps (motor side)"
        return d


# --------------------------------------------------------------------------------------------- point ledger

@dataclass(frozen=True)
class AuxLoad:
    """An auxiliary consumer and where it is supplied from (it is counted once, at its supply port)."""

    name: str
    P_W: float
    supply: str                    # "hv_dc_inside" (already in P_dc) | "lv_external" (a separate external input)
    basis: str = ""

    def __post_init__(self):
        if self.supply not in ("hv_dc_inside", "lv_external"):
            raise InputValidationError("aux supply must be hv_dc_inside or lv_external", field="supply")
        if _finite("P_W", self.P_W) < 0:
            raise InputValidationError("auxiliary power must be >= 0", field="P_W")


def inverter_scope(drive) -> dict:
    inv = drive.inverter
    if inv.module_loss is not None:
        m = inv.module_loss
        return {"model": "datasheet module", "included": ["semiconductor conduction + switching (per device)"] +
                (["gate driver / auxiliary (drawn from HV DC)"] if m.aux_from_hv_dc else []),
                "excluded": (["gate driver / auxiliary (external LV supply)"] if not m.aux_from_hv_dc else []) +
                ["DC-link capacitor ESR", "busbar / terminals", "control electronics", "current ripple / dead-time error"],
                "technology": m.device.technology, "value_kind": m.device.value_kind, "fsw_Hz": m.fsw_Hz}
    if inv.loss is not None:
        return {"model": "quadratic surrogate a0 + a2 I^2", "included": [inv.loss.description or inv.loss.kind],
                "excluded": ["anything outside the surrogate's stated basis"], "value_kind": inv.loss.kind}
    return {"model": None, "included": [], "excluded": ["inverter loss model missing"]}


def hf_eta_interval(r: dict, lo: float | None, hi: float | None) -> list | None:
    """Efficiency of a DEFINED boundary with an additional internal loss in [lo, hi] (upper None = open) at the same
    operating point: forward p_out / (p_in + x), reverse (|p_in| - x) / |p_out|  ->  [eta_low, eta_high]."""
    if r.get("status") != DEFINED or lo is None:
        return None
    a, b = abs(r["P_in_W"]), abs(r["P_out_W"])
    if r["direction"] == "forward":
        return [None if hi is None else b / (a + hi), b / (a + lo)]
    low = None if hi is None else (a - hi) / b
    return [None if (low is not None and low < 0) else low, (a - lo) / b]


def point_ledger(pt, drive, reducer: ReducerModel | None = None, oil_temp_C: float | None = None,
                 aux: tuple = (), tol_W: float = 1e-6, pwm_hf: dict | None = None) -> dict:
    """Five boundaries, the loss ledger (known subtotal + unknown items) and the flow table of one OperatingPoint.

    ``pwm_hf`` (optional, ``pwm_policy.point_hf_losses``): the motor PWM harmonic copper (exact or its R_dc lower
    bound) and the declared Fe+PM HF bound become ledger items with their meaning; the fundamental boundary
    efficiencies stay as they are (qualified) and the motor-side boundaries get an efficiency interval including
    the HF loss.  An unknown is never set to zero; a bound is never added as a value."""
    P_dc, P_ac, P_m = pt.Pdc_W, pt.Pac_W, pt.Pshaft_W
    red = None
    if reducer is not None and P_m is not None and pt.Tshaft_Nm is not None:
        red = reducer.output_from_motor(pt.speed_rpm, pt.Tshaft_Nm, oil_temp_C, tol_W)
        P_o = red["P_o_W"]
    else:
        P_o = None
    b = five_boundaries(P_dc, P_ac, P_m, P_o, tol_W)
    cu = (pwm_hf or {}).get("copper") or {}
    mag = (pwm_hf or {}).get("magnetic_hf_bound_W")
    items = [("inverter", "semiconductor / declared inverter loss", pt.Pinv_W, {}),
             ("motor", "copper (fundamental, 1.5 Rs |i|^2)", pt.Pcu_W, {}),
             ("motor", "rotational / iron (loss-equivalent torque)", pt.Prot_W, {}),
             ("motor", "PWM harmonic copper", cu.get("W"),
              {"lower_bound_W": cu.get("lower_bound_W"), "status": cu.get("status", "NOT_EVALUATED"),
               "basis": cu.get("basis", "not evaluated (no declared L_hf / harmonic data)"),
               "rac_coverage_I2_fraction": cu.get("rac_coverage_I2_fraction")}),
             ("motor", "PWM Fe+PM HF magnetic loss", None,
              {"upper_bound_W": mag, "status": "UPPER_BOUND" if mag is not None else "UNKNOWN",
               "basis": "declared upper bound (stator/rotor iron + PM eddy current; never an expected value)"
                        if mag is not None else "no declared bound (never set to 0)"}),
             ("reducer", "reducer (directional model)", None if red is None else red.get("loss_W"), {})]
    known = sum(v for _, _, v, _e in items if v is not None)
    unknown = [f"{grp}: {name}" for grp, name, v, _e in items if v is None]
    open_items = [e for _g, _n, v, e in items if v is None]
    lo_total = known + sum((e.get("lower_bound_W") or 0.0) for e in open_items)     # losses are never negative
    ups = [e.get("upper_bound_W") for e in open_items]
    hi_total = None if any(u is None for u in ups) else known + sum(ups)
    lv = [a for a in aux if a.supply == "lv_external"]
    P_lv = sum(a.P_W for a in lv)
    extra = {}
    if P_dc is not None and P_o is not None and abs(P_o) > tol_W:
        if P_o > tol_W and P_dc > tol_W:
            extra["useful_output_over_all_inputs"] = P_o / (P_dc + P_lv)
        elif P_o < -tol_W and P_dc < -tol_W:
            extra["hv_recovery_ratio"] = abs(P_dc) / abs(P_o)
            extra["net_recovery_after_lv_aux"] = (abs(P_dc) - P_lv) / abs(P_o)
            extra["recovered_over_all_inputs"] = abs(P_dc) / (abs(P_o) + P_lv)
    standstill = abs(pt.omega_m) <= 1e-9
    if standstill and b["inverter"]["status"] == DEFINED:
        b["inverter"]["qualifier"] = ("standstill: DC->AC terminal conversion ratio; the motor gives no useful "
                                      "mechanical output (motor / eDrive efficiency N/A)")
    hf = (pwm_hf or {}).get("interval_W") or [None, None]
    if pwm_hf is None or hf[0] is None:
        b["motor"]["qualifier"] = "fundamental steady-state model: PWM harmonic losses not included (not evaluated)"
    else:
        for k in ("motor", "inverter_motor", "edrive"):
            iv = hf_eta_interval(b[k], hf[0], hf[1])
            if iv is not None:
                b[k]["eta_interval_incl_pwm_hf"] = iv
        b["motor"]["qualifier"] = (f"fundamental steady-state model; the PWM harmonic motor loss "
                                   f"[{hf[0]:.4g}, {'open' if hf[1] is None else f'{hf[1]:.4g}'}] W is not in eta - "
                                   f"see eta_interval_incl_pwm_hf")
    return {"ports_W": {"P_dc": P_dc, "P_ac": P_ac, "P_m": P_m, "P_o": P_o, "P_em": pt.Te_Nm * pt.omega_m},
            "boundaries": b, "energy_mode_core": pt.energy_mode,
            "loss_items": [{"boundary": g, "item": n, "W": v, "in_port_powers": not n.startswith("PWM"), **e}
                           for g, n, v, e in items],
            "loss_known_subtotal_W": known, "loss_unknown_items": unknown,
            "loss_total_W": known if not unknown else None,
            "loss_interval_W": [lo_total, hi_total],
            "pwm_hf": None if pwm_hf is None else {k: pwm_hf.get(k) for k in (
                "status", "interval_W", "fsw_requested_Hz", "fsw_waveform_used_Hz", "fsw_error_percent", "L_hf_H",
                "modulation_index", "ripple_rms_A", "magnetic_hf_bound_W", "basis", "reason")},
            "inverter_scope": inverter_scope(drive), "reducer": None if reducer is None else {**reducer.describe(),
                                                                                              "evaluation": red},
            "aux": [{"name": a.name, "P_W": a.P_W, "supply": a.supply, "basis": a.basis} for a in aux],
            "aux_metrics": extra,
            "note": "the electromagnetic conversion power T_em*omega_m is not the shaft power; each boundary is judged "
                    "on its own ports; the PWM harmonic items are additional to the fundamental port powers (they "
                    "appear in eta_interval_incl_pwm_hf, not in eta)"}


# --------------------------------------------------------------------------------------------- mission energy

def split_energy(t: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """Exact E+ = int max(p, 0) and E- = int max(-p, 0) of a piecewise-LINEAR trace (zero crossings split)."""
    t = np.asarray(t, float)
    p = np.asarray(p, float)
    Ep = En = 0.0
    for i in range(t.size - 1):
        dt = t[i + 1] - t[i]
        a, b = p[i], p[i + 1]
        if a >= 0 and b >= 0:
            Ep += 0.5 * (a + b) * dt
        elif a <= 0 and b <= 0:
            En -= 0.5 * (a + b) * dt
        else:
            tc = dt * a / (a - b)
            if a > 0:
                Ep += 0.5 * a * tc
                En -= 0.5 * b * (dt - tc)
            else:
                En -= 0.5 * a * tc
                Ep += 0.5 * b * (dt - tc)
    return Ep, En


def mission_energy(segments: list[dict], tol_W: float = 1e-6, storage_change_J: float = 0.0,
                   distance_km: float | None = None) -> dict:
    """Energy ledger of piecewise-constant segments {duration_s, P_dc, P_ac, P_m, P_o} (W; None = unknown).

    Direction efficiencies are energy ratios of the segments of that direction (never an average of eta, never
    the signed net output over net input). The output port is P_o if every segment has it, otherwise P_m."""
    if not segments:
        raise InputValidationError("no mission segments", field="segments")
    out_port = "P_o" if all(s.get("P_o") is not None for s in segments) else "P_m"
    E = {k: [0.0, 0.0] for k in ("P_dc", "P_ac", "P_m", "P_o")}
    unknown = {k: 0.0 for k in E}
    cls = {"traction": {"in": 0.0, "out": 0.0, "t": 0.0}, "regeneration": {"in": 0.0, "out": 0.0, "t": 0.0},
           "dissipative_braking": {"mech_in": 0.0, "dc_in": 0.0, "t": 0.0}, "idle": {"dc_in": 0.0, "t": 0.0},
           "undetermined": {"t": 0.0}}
    per_boundary = {name: {"forward": [0.0, 0.0], "reverse": [0.0, 0.0]} for name, *_ in BOUNDARIES}
    missing_s = {name: 0.0 for name, *_ in BOUNDARIES}                # segments whose port powers are not known
    for s in segments:
        dt = _finite("duration_s", s["duration_s"])
        if dt < 0:
            raise InputValidationError("segment duration must be >= 0", field="duration_s")
        for k in E:
            v = s.get(k)
            if v is None:
                unknown[k] += dt
            else:
                E[k][0 if v > 0 else 1] += abs(v) * dt
        pdc, po = s.get("P_dc"), s.get(out_port)
        if pdc is None or po is None:
            cls["undetermined"]["t"] += dt
        elif po > tol_W and pdc > tol_W:
            cls["traction"]["in"] += pdc * dt
            cls["traction"]["out"] += po * dt
            cls["traction"]["t"] += dt
        elif po < -tol_W and pdc < -tol_W:
            cls["regeneration"]["in"] += -po * dt
            cls["regeneration"]["out"] += -pdc * dt
            cls["regeneration"]["t"] += dt
        elif po < -tol_W:
            cls["dissipative_braking"]["mech_in"] += -po * dt
            cls["dissipative_braking"]["dc_in"] += max(pdc, 0.0) * dt
            cls["dissipative_braking"]["t"] += dt
        elif abs(po) <= tol_W:
            cls["idle"]["dc_in"] += max(pdc, 0.0) * dt
            cls["idle"]["t"] += dt
        else:
            cls["undetermined"]["t"] += dt
        for name, a, b, _lab in BOUNDARIES:
            pa, pb = s.get(a), s.get(b)
            if pa is None or pb is None:
                missing_s[name] += dt
                continue
            if pa > tol_W and pb > tol_W:
                per_boundary[name]["forward"][0] += pa * dt
                per_boundary[name]["forward"][1] += pb * dt
            elif pa < -tol_W and pb < -tol_W:
                per_boundary[name]["reverse"][0] += -pb * dt
                per_boundary[name]["reverse"][1] += -pa * dt
    tr, rg = cls["traction"], cls["regeneration"]
    part_tr = tr["out"] / tr["in"] if tr["in"] > 0 else None
    part_rg = rg["out"] / rg["in"] if rg["in"] > 0 else None
    undet = cls["undetermined"]["t"]
    # a ratio over part of the mission is not the mission's direction efficiency: withheld (UNKNOWN), partial shown
    eta_tr = part_tr if undet <= 0 else None
    eta_rg = part_rg if undet <= 0 else None
    pb_out, pb_partial = {}, {}
    for name, d in per_boundary.items():
        ratios = {dirn: (v[1] / v[0] if v[0] > 0 else None) for dirn, v in d.items()}
        pb_out[name] = ratios if missing_s[name] <= 0 else {dirn: None for dirn in ratios}
        if missing_s[name] > 0:
            pb_partial[name] = {**ratios, "missing_s": missing_s[name]}
    net_dc = E["P_dc"][0] - E["P_dc"][1]
    net_out = E[out_port][0] - E[out_port][1]
    res = {"output_port": out_port, "E_pos_J": {k: v[0] for k, v in E.items()},
           "E_neg_J": {k: v[1] for k, v in E.items()}, "unknown_duration_s": unknown,
           "segments": cls, "eta_traction": eta_tr, "eta_regeneration": eta_rg,
           "boundary_direction_eta": pb_out, "E_dc_net_J": net_dc, "E_out_net_J": net_out,
           "complete": undet <= 0 and not pb_partial,
           "partial": None if (undet <= 0 and not pb_partial) else {
               "undetermined_s": undet, "eta_traction_partial": part_tr, "eta_regeneration_partial": part_rg,
               "boundary_direction_eta_partial": pb_partial,
               "meaning": "segments with a missing port power: the direction efficiencies of the MISSION are UNKNOWN; "
                          "the partial ratios cover the known segments only"},
           "storage_change_J": storage_change_J,
           "note": "net output / net DC is not a conversion efficiency (drive and regeneration cancel); direction "
                   "efficiencies are energy ratios of their own segments"}
    if distance_km:
        res["Wh_per_km_dc_net"] = net_dc / 3600.0 / distance_km
    return res


# --------------------------------------------------------------------------------------------- module comparison

@dataclass(frozen=True)
class ModuleCandidate:
    """One module design in a comparison: its OWN datasheet model, gate / dead time, thermal path and error budget."""

    name: str
    model: ModuleLossModel                     # its own datasheet module model
    Rth_K_per_W: float                         # hottest position junction -> coolant (its own thermal path)
    loss_error_rel: float | None = None        # declared loss error budget (NOT a statistical confidence)
    error_basis: str = ""

    def __post_init__(self):
        if not isinstance(self.model, ModuleLossModel):
            raise InputValidationError("a module candidate needs its datasheet ModuleLossModel", field="model")
        if _finite("Rth_K_per_W", self.Rth_K_per_W) <= 0:
            raise InputValidationError("Rth must be > 0", field="Rth_K_per_W")
        if self.loss_error_rel is not None:
            if not (0.0 <= _finite("loss_error_rel", self.loss_error_rel) < 1.0):
                raise InputValidationError("loss error budget must be in [0, 1)", field="loss_error_rel")
            if not self.error_basis.strip():
                raise InputValidationError("a loss error budget needs its basis (DPT / holdout / tool comparison)",
                                           field="error_basis")


def module_point(base_drive, cand: ModuleCandidate, sc, T: float, coolant_C: float, fsw_Hz: float | None,
                  reducer, oil_C, max_iter: int = 60, tol_K: float = 1e-3) -> dict:
    """Coupled electrothermal point through the drive's OWN module evaluation (one physics for every result):
    policy solve at Tj -> hottest-position loss (worst electrical angle at standstill) -> Tj = T_coolant + Rth P_hot
    -> re-solve (the DC claims see the losses at that Tj).  A non-converging iteration is reported as numerical,
    not as thermal runaway."""
    from ..solvers.policy import PolicyEvaluator
    model = cand.model if fsw_Hz is None else replace(cand.model, fsw_Hz=fsw_Hz)
    Tj = float(coolant_C)
    hist = []
    for _it in range(max_iter):
        drv = replace(base_drive, inverter=replace(base_drive.inverter, loss=None, module_loss=model, module_Tj_C=Tj))
        sol = PolicyEvaluator(drv, sc).solve(T)
        pt = sol.point
        if pt is None:
            return {"status": sol.policy_claim.status.value, "reason": sol.policy_claim.detail, "point": None}
        det = pt.inverter_loss_detail or {}
        if not det.get("established"):
            return {"status": "UNKNOWN", "reason": "module loss not established: " + "; ".join(det.get("problems", [])),
                    "point": None, "Tj_history": hist}
        P_hot = float(det["hottest_position_W"])
        Tj_new = coolant_C + cand.Rth_K_per_W * P_hot
        hist.append((Tj, P_hot, Tj_new))
        if abs(Tj_new - Tj) <= tol_K:
            break
        Tj = Tj + 0.7 * (Tj_new - Tj)
    else:
        return {"status": "UNKNOWN", "reason": "electrothermal fixed point not converged (numerical; not a proof of "
                                               "thermal runaway)", "point": None, "Tj_history": hist}
    led = point_ledger(pt, drv, reducer, oil_C)
    return {"status": sol.policy_claim.status.value, "claims": {c.name: c.status.value for c in sol.claims},
            "reason": sol.policy_claim.detail, "Tj_C": Tj, "P_hot_W": P_hot, "fsw_Hz": model.fsw_Hz,
            "iterations": len(hist),
            "point": {"id_A": pt.id_A, "iq_A": pt.iq_A, "i_peak_A": pt.i_peak_A, "Pinv_W": pt.Pinv_W,
                      "Pdc_W": pt.Pdc_W, "Pac_W": pt.Pac_W, "Pshaft_W": pt.Pshaft_W, "Pcu_W": pt.Pcu_W,
                      "vd_V": pt.vd_V, "vq_V": pt.vq_V, "voltage_budget_V": pt.voltage_budget_V},
            "detail": det, "ledger": led}


def _verdict(La: float, Lb: float, ua: float | None, ub: float | None, value_kinds: set, same_motor_point: bool,
             unit: str = "W") -> dict:
    """Ranking of two inverter losses (or loss energies) against the declared module-specific error budgets.

    Module-specific errors are added (no cancellation assumed); the motor loss cancels only when both candidates
    run the identical motor point (same model, same inputs) - otherwise the ranking is reserved."""
    d = Lb - La
    base = {"delta": d, "unit": unit, "loss_A": La, "loss_B": Lb,
            "relative_change": (d / La) if La else None}
    if ua is None or ub is None:
        return {**base, "verdict": "UNDECIDED", "reason": "no declared loss error budget for both candidates: estimate "
                                                          "shown, ranking reserved"}
    band = ua * La + ub * Lb
    why = f"|dL| {abs(d):.4g} {unit} vs combined module error budget {band:.4g} {unit}"
    if not same_motor_point:
        why += " (motor points differ: motor-loss errors do not cancel and are not in this budget)"
        return {**base, "verdict": "UNDECIDED", "band": band, "reason": why}
    if abs(d) <= band:
        return {**base, "verdict": "UNDECIDED", "band": band, "reason": why + ": within the budget"}
    q = "" if value_kinds == {"max"} else " (typical datasheet values: not a production-population winner)"
    return {**base, "verdict": "A_LOWER_LOSS" if d > 0 else "B_LOWER_LOSS", "band": band, "reason": why + q}


def compare_modules(base_drive, candidates: list, requests: list, limits, coolant_C: float, mode: str = "fixed_policy",
                    common_fsw_Hz: float | None = None, reducer: ReducerModel | None = None,
                    oil_temp_C: float | None = None, mission: list | None = None) -> dict:
    """SiC / IGBT (or any two module designs) on the same delivered requirement.

    ``fixed_policy``: same motor, source, demand, modulation and PWM frequency and coolant - a candidate pair with
    different modulation is rejected, never represented as one policy (review R2 PT-09); each module keeps its own
    data, legal gate / dead time and thermal path (Tj is a RESULT, not forced equal).  ``design_specific``: each
    candidate runs its own declared policy - EMC, ripple and timing constraints must then be re-evaluated for each
    design (not done here), so the result is a loss comparison under declared policies, not a global optimisation
    and not an approval.  Either way the ranking is of the SEMICONDUCTOR (module) loss: the motor PWM-harmonic,
    capacitor and auxiliary losses are not evaluated and are never asserted to cancel between technologies.
    ``requests``: [(speed_rpm, torque_Nm, Vdc_V)], ``mission``: [(duration_s, speed_rpm, torque_Nm, Vdc_V)].
    """
    from ..scenario import Scenario
    if mode not in ("fixed_policy", "design_specific"):
        raise InputValidationError("mode must be fixed_policy or design_specific", field="mode")
    if len(candidates) != 2:
        raise InputValidationError("the A/B protocol compares exactly two candidates", field="candidates")
    if mode == "fixed_policy" and common_fsw_Hz is None:
        raise InputValidationError("the fixed-policy comparison needs the common PWM frequency", field="common_fsw_Hz")
    fsw = common_fsw_Hz if mode == "fixed_policy" else None
    A, B = candidates
    if mode == "fixed_policy" and A.model.modulation != B.model.modulation:
        raise InputValidationError(f"a fixed-policy comparison fixes the modulation: {A.name} uses "
                                   f"{A.model.modulation}, {B.name} uses {B.model.modulation} - declare one modulation "
                                   f"for both, or compare the declared policies design-specifically", field="modulation")
    kinds = {A.model.device.value_kind, B.model.device.value_kind}
    rows = []
    for (n, T, vdc) in requests:
        sc = Scenario("ab", float(n), float(vdc), limits, coolant_temp_C=coolant_C)
        ra = module_point(base_drive, A, sc, float(T), coolant_C, fsw, reducer, oil_temp_C)
        rb = module_point(base_drive, B, sc, float(T), coolant_C, fsw, reducer, oil_temp_C)
        row = {"speed_rpm": n, "torque_Nm": T, "Vdc_V": vdc, "A": ra, "B": rb}
        if ra.get("point") and rb.get("point") and ra["status"] == rb["status"] == "FEASIBLE":
            pa, pb = ra["point"], rb["point"]
            same = abs(pa["id_A"] - pb["id_A"]) < 1e-6 and abs(pa["iq_A"] - pb["iq_A"]) < 1e-6
            row["compare"] = _verdict(pa["Pinv_W"], pb["Pinv_W"], A.loss_error_rel, B.loss_error_rel, kinds, same)
            ea = ra["ledger"]["boundaries"]
            eb = rb["ledger"]["boundaries"]
            row["delta_eta_pp"] = {k: (100 * (eb[k]["eta"] - ea[k]["eta"]) if ea[k]["status"] == eb[k]["status"] == DEFINED
                                       else None) for k in ("inverter", "inverter_motor", "edrive")}
            row["same_motor_point"] = same
        else:
            row["compare"] = {"verdict": "NOT_COMPARABLE",
                              "reason": "both candidates must deliver the same requirement (FEASIBLE) before any "
                                        "efficiency ranking"}
        rows.append(row)
    diffs = [k for k, a, b in (("dead time", A.model.deadtime_s, B.model.deadtime_s),
                               ("switching edges / technology", A.model.device.technology, B.model.device.technology))
             if a != b]
    harm = ("motor PWM-harmonic losses: not evaluated and not asserted to cancel (" +
            ("same modulation and carrier, but " + " and ".join(diffs) + " differ" if (mode == "fixed_policy" and diffs)
             else "same modulation and carrier; no evidence that the edge-dependent part cancels"
             if mode == "fixed_policy" else "the pulse policies differ") + ")")
    out = {"mode": mode, "common_fsw_Hz": fsw, "coolant_C": coolant_C, "rows": rows,
           "common_modulation": A.model.modulation if mode == "fixed_policy" else None,
           "ranking_scope": "semiconductor (module) loss only - not an eDrive ranking: motor PWM-harmonic, capacitor "
                            "and auxiliary losses are not in it; the eDrive efficiency deltas cover the modelled parts "
                            "only",
           "candidates": [{"name": c.name, "technology": c.model.device.technology, "value_kind": c.model.device.value_kind,
                           "modulation": c.model.modulation, "fsw_Hz": fsw or c.model.fsw_Hz,
                           "deadtime_s": c.model.deadtime_s, "Rth_K_per_W": c.Rth_K_per_W,
                           "loss_error_rel": c.loss_error_rel, "error_basis": c.error_basis} for c in candidates],
           "not_evaluated": (["EMC / dv/dt / overshoot after a gate or frequency change", "DC-link ripple and capacitor "
                              "heating at the new frequency", "control timing at the new period"]
                             if mode == "design_specific" else []) +
                            [harm, "DC-link capacitor losses", "SOA / short-circuit withstand / lifetime (efficiency "
                                                               "does not approve them)"],
           "meaning": "fixed-policy: the module change under one declared modulation and carrier; design-specific: "
                      "module + declared policy changes (a comparison of declared policies, not a global "
                      "optimisation). No technology is better by rule; typical data do not rank a production "
                      "population."}
    if mission:
        out["mission"] = _mission_compare(base_drive, candidates, mission, limits, coolant_C, fsw, reducer, oil_temp_C,
                                          kinds)
    return out


def _mission_compare(base_drive, candidates, mission, limits, coolant_C, fsw, reducer, oil_C, kinds) -> dict:
    from ..scenario import Scenario
    res = {}
    for c in candidates:
        segs, delivered, E_inv = [], True, 0.0
        why, mpts = [], []
        for (dt, n, T, vdc) in mission:
            sc = Scenario("mission", float(n), float(vdc), limits, coolant_temp_C=coolant_C)
            r = module_point(base_drive, c, sc, float(T), coolant_C, fsw, reducer, oil_C)
            if r["status"] != "FEASIBLE" or r.get("point") is None:
                delivered = False
                why.append(f"{n:g} rpm / {T:g} N m: {r['status']} ({r.get('reason', '')[:80]})")
                segs.append({"duration_s": dt, "P_dc": None, "P_ac": None, "P_m": None, "P_o": None})
                continue
            p = r["ledger"]["ports_W"]
            segs.append({"duration_s": dt, "P_dc": p["P_dc"], "P_ac": p["P_ac"], "P_m": p["P_m"], "P_o": p["P_o"]})
            E_inv += r["point"]["Pinv_W"] * dt
            mpts += [r["point"]["id_A"], r["point"]["iq_A"]]
        res[c.name] = {"energy": mission_energy(segs), "delivered": delivered, "not_delivered": why,
                       "E_inverter_loss_J": E_inv if delivered else None, "motor_points": mpts}
    a, b = candidates
    ra, rb = res[a.name], res[b.name]
    if ra["delivered"] and rb["delivered"]:
        same = all(abs(x - y) < 1e-6 for x, y in zip(ra["motor_points"], rb["motor_points"]))
        v = _verdict(ra["E_inverter_loss_J"], rb["E_inverter_loss_J"], a.loss_error_rel, b.loss_error_rel, kinds, same,
                     "J")
    else:
        v = {"verdict": "NOT_COMPARABLE", "reason": "a candidate does not deliver the whole trajectory: lower consumption "
                                                    "is not ranked as an improvement"}
    return {"per_candidate": res, "compare": v}


_module_point = module_point          # former private name (compatibility)
