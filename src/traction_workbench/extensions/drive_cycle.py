"""Drive cycle (system view, item 8): a vehicle on a speed trace -> machine operating points -> energy per component.

Backward (kinematic) model: the trace is followed exactly where the drive can deliver it, and every interval that it
cannot deliver is reported - the trace is never re-planned.  One driven axle, one machine, the declared reducer:

  interval k = [t_k, t_k+1], speed linear inside it: a = (v_k+1 - v_k) / dt at the mid speed v = (v_k + v_k+1) / 2
  F_road  = A + B v + C v^2                                   (form "abc": declared road-load coefficients)
          = m g c_rr cos(alpha) + 1/2 rho CdA v^2             (form "physical")
  F_grade = m g sin(alpha),   alpha = atan(grade)
  F_inert = m_eq a,           m_eq = m + J_wheels / r^2 + J_motor (g_total / r)^2
  F_wheel = F_road + F_grade + F_inert                        (the tractive force the trace demands at the tyres)

The wheel torque F_wheel r reaches the reducer's declared output boundary through the axle (ratio, efficiency per
direction; 1 / 1 = the boundary turns with the wheels and the axle is lossless).  The machine torque follows from the
reducer's own inverse (directional efficiencies + drag; a loss-map reducer is inverted by bisection on its forward
evaluation), and the machine point is the minimum-current policy point at the declared inverter DC voltage - the
same point, losses and DC power as the efficiency page.  The inertia term is exact for linear speed (its sum is the
kinetic-energy change), the road load uses the midpoint rule.

Braking (F_wheel < 0).  The machine takes ``regen.share`` of the braking torque at the output boundary - never less
braking than the drive's own drag at zero shaft torque - down to ``regen.min_speed_kmh``; below it the shaft torque
is zero.  A regeneration point the policy cannot deliver (machine or DC charge limits) is reduced by bisection to
the largest deliverable share; the friction brakes take the rest.  Traction the policy cannot deliver is reduced the
same way and reported as not delivered (the trace is not followed there).

At rest (v = 0 and no acceleration) the vehicle is held by the brakes and the inverter does not switch (declared
idle assumption).  The inverter DC voltage is held at the declared value; the battery current is P / V_dc and the
declared source resistance (battery + harness) dissipates R I^2 - the voltage drop is not fed back into the machine
point (stated).  The energy ledger closes: OCV energy = wheel work + friction + losses + auxiliaries, the residual
is reported.  Results are model energies of the declared parts; they are not a certification test result.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import progress
from ..errors import InputValidationError
from ..validation import finite as _finite

G = 9.80665
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
KMH = 1.0 / 3.6
MPH = 0.44704
UNIT_TO_MPS = {"km/h": KMH, "mph": MPH, "m/s": 1.0}
# per-interval states
IDLE, TRACTION, COAST, REGEN, REGEN_LIMITED, FRICTION, NOT_DELIVERED, UNKNOWN_STEP = (
    "idle", "traction", "drive_drag", "regeneration", "regeneration_limited", "friction_only", "not_delivered",
    "unknown")
LOSS_KEYS = ("battery", "inverter", "motor_copper", "motor_rotational", "reducer", "axle")
_DATA = Path(__file__).resolve().parents[1] / "data" / "drive_cycles.json"
_CYCLES = None


# --------------------------------------------------------------------------------------------- cycles

def builtin_cycles() -> dict:
    """The standard traces shipped with the application (``data/drive_cycles.json``), keyed by name."""
    global _CYCLES
    if _CYCLES is None:
        _CYCLES = json.loads(_DATA.read_text(encoding="utf-8"))["cycles"]
    return _CYCLES


@dataclass(frozen=True)
class Cycle:
    """A speed trace: t (s, strictly increasing, from 0), v (m/s, >= 0), optional grade (rise / run) per sample."""

    name: str
    t_s: tuple
    v_mps: tuple
    grade: tuple = ()
    phases: tuple = ()                 # ({"name", "start_s", "end_s"}, ...)
    source: str = ""
    official: dict = field(default_factory=dict)

    def __post_init__(self):
        t = np.asarray(self.t_s, float)
        v = np.asarray(self.v_mps, float)
        if t.ndim != 1 or t.size < 2 or t.size != v.size:
            raise InputValidationError("a cycle needs >= 2 samples of time and speed of equal length", field="cycle")
        if not (np.all(np.isfinite(t)) and np.all(np.isfinite(v))):
            raise InputValidationError("cycle time and speed must be finite", field="cycle")
        if np.any(np.diff(t) <= 0):
            raise InputValidationError("cycle time must be strictly increasing", field="cycle.t_s")
        if np.any(v < 0):
            raise InputValidationError("cycle speed must be >= 0 (forward driving only)", field="cycle.v")
        if self.grade and len(self.grade) != t.size:
            raise InputValidationError("grade needs one value per sample", field="cycle.grade")
        for p in self.phases:
            if not (t[0] <= float(p["start_s"]) < float(p["end_s"]) <= t[-1]):
                raise InputValidationError(f"phase {p.get('name')} outside the trace", field="cycle.phases")

    @property
    def duration_s(self) -> float:
        return float(self.t_s[-1] - self.t_s[0])

    @property
    def distance_m(self) -> float:
        t, v = np.asarray(self.t_s, float), np.asarray(self.v_mps, float)
        return float(np.sum(0.5 * (v[1:] + v[:-1]) * np.diff(t)))

    def stats(self) -> dict:
        v = np.asarray(self.v_mps, float)
        d = self.distance_m
        return {"duration_s": self.duration_s, "distance_km": d / 1e3, "v_max_kmh": float(v.max()) * 3.6,
                "v_mean_kmh": (d / self.duration_s) * 3.6 if self.duration_s > 0 else None,
                "stop_fraction": float(np.mean(v <= 0.0)), "samples": len(self.t_s)}


def cycle_from_builtin(key: str) -> Cycle:
    cyc = builtin_cycles()
    if key not in cyc:
        raise InputValidationError(f"unknown cycle {key!r}; built-in: {sorted(cyc)}", field="cycle")
    c = cyc[key]
    f = UNIT_TO_MPS[c["unit"]]
    sp = c["speed"]
    dt = float(c.get("dt_s", 1.0))
    return Cycle(key, tuple(i * dt for i in range(len(sp))), tuple(x * f for x in sp), (),
                 tuple(c.get("phases") or ()), f"{c['title']} - {c['reference']}", dict(c.get("official") or {}))


def cycle_from_dict(d: dict) -> Cycle:
    """``{"builtin": key}`` or ``{"name", "t_s": [...], "speed": [...], "unit": "km/h", "grade_pct": [...]}``."""
    if not d:
        raise InputValidationError("no cycle given", field="cycle")
    if d.get("builtin"):
        return cycle_from_builtin(str(d["builtin"]))
    unit = d.get("unit", "km/h")
    if unit not in UNIT_TO_MPS:
        raise InputValidationError(f"speed unit must be one of {sorted(UNIT_TO_MPS)}", field="cycle.unit")
    f = UNIT_TO_MPS[unit]
    sp = [float(x) for x in d["speed"]]
    t = [float(x) for x in d["t_s"]] if d.get("t_s") is not None else [float(i) for i in range(len(sp))]
    gr = tuple(float(x) / 100.0 for x in d["grade_pct"]) if d.get("grade_pct") else ()
    return Cycle(str(d.get("name") or "user trace"), tuple(t), tuple(x * f for x in sp), gr,
                 tuple(d.get("phases") or ()), str(d.get("source") or "user-supplied trace"))


def cycle_from_csv(text: str, name: str = "imported trace") -> Cycle:
    """CSV with a header: time column ``t``/``time``/``t_s``/``s`` and a speed column whose name carries its unit
    (``kmh``, ``km/h``, ``mph``, ``mps``, ``m/s``); optional ``grade_pct``."""
    import csv
    import io
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if r and any(c.strip() for c in r)]
    if len(rows) < 3:
        raise InputValidationError("the CSV needs a header and >= 2 rows", field="cycle.csv")
    head = [h.strip().lower() for h in rows[0]]

    def col(*names):
        for i, h in enumerate(head):
            if h in names:
                return i
        return None
    it = col("t", "time", "t_s", "time_s", "s", "sec", "cycsecs")
    unit, iv = None, None
    for i, h in enumerate(head):
        for key, u in (("kmh", "km/h"), ("km/h", "km/h"), ("kph", "km/h"), ("mph", "mph"), ("mps", "m/s"),
                       ("m/s", "m/s")):
            if key in h.replace(" ", "").replace("[", "").replace("]", ""):
                unit, iv = u, i
                break
        if iv is not None:
            break
    if iv is None:
        raise InputValidationError("no speed column with a unit in its name (kmh, mph, mps)", field="cycle.csv")
    ig = col("grade_pct", "grade%", "grade [%]")
    try:
        data = [[float(x) for x in r] for r in rows[1:]]
    except ValueError as exc:
        raise InputValidationError(f"non-numeric CSV value: {exc}", field="cycle.csv") from None
    sp = [r[iv] for r in data]
    t = [r[it] for r in data] if it is not None else [float(i) for i in range(len(sp))]
    body = {"name": name, "t_s": t, "speed": sp, "unit": unit, "source": "imported CSV"}
    if ig is not None:
        body["grade_pct"] = [r[ig] for r in data]
    return cycle_from_dict(body)


# --------------------------------------------------------------------------------------------- vehicle

@dataclass(frozen=True)
class RoadLoad:
    form: str                            # "abc" | "physical"
    A_N: float = 0.0
    B_N_per_mps: float = 0.0
    C_N_per_mps2: float = 0.0
    c_rr: float = 0.0
    CdA_m2: float = 0.0
    rho_kg_m3: float = 1.2
    basis: str = ""
    includes_edrive_drag: bool = False   # coast-down coefficients that already contain this drive's drag

    def __post_init__(self):
        if self.form not in ("abc", "physical"):
            raise InputValidationError("road load form must be 'abc' or 'physical'", field="road_load.form")
        for k in ("A_N", "B_N_per_mps", "C_N_per_mps2", "c_rr", "CdA_m2", "rho_kg_m3"):
            if _finite(f"road_load.{k}", getattr(self, k)) < 0:
                raise InputValidationError(f"road_load.{k} must be >= 0", field=f"road_load.{k}")
        if not str(self.basis).strip():
            raise InputValidationError("a road load needs its basis (coast-down test, target values, estimate)",
                                       field="road_load.basis")


@dataclass(frozen=True)
class Vehicle:
    mass_kg: float
    wheel_radius_m: float
    road_load: RoadLoad
    J_wheels_kgm2: float = 0.0
    J_motor_kgm2: float = 0.0
    axle_ratio: float = 1.0                      # output boundary speed / wheel speed
    axle_eta_forward: float = 1.0
    axle_eta_reverse: float = 1.0
    regen_share: float = 1.0                     # share of the braking torque the machine is asked for
    regen_min_speed_kmh: float = 0.0
    aux_hv_W: float = 0.0                        # HV auxiliary load at the battery terminals (DC-DC, HVAC, ...)
    usable_energy_kWh: float | None = None
    basis: str = ""

    def __post_init__(self):
        for k in ("mass_kg", "wheel_radius_m", "axle_ratio"):
            if _finite(f"vehicle.{k}", getattr(self, k)) <= 0:
                raise InputValidationError(f"vehicle.{k} must be > 0", field=f"vehicle.{k}")
        for k in ("J_wheels_kgm2", "J_motor_kgm2", "regen_min_speed_kmh", "aux_hv_W"):
            if _finite(f"vehicle.{k}", getattr(self, k)) < 0:
                raise InputValidationError(f"vehicle.{k} must be >= 0", field=f"vehicle.{k}")
        for k in ("axle_eta_forward", "axle_eta_reverse"):
            if not (0 < _finite(f"vehicle.{k}", getattr(self, k)) <= 1):
                raise InputValidationError(f"vehicle.{k} must be in (0, 1]", field=f"vehicle.{k}")
        if not (0 <= _finite("vehicle.regen_share", self.regen_share) <= 1):
            raise InputValidationError("regen share must be in [0, 1]", field="vehicle.regen_share")
        if self.usable_energy_kWh is not None and _finite("usable_energy_kWh", self.usable_energy_kWh) <= 0:
            raise InputValidationError("usable battery energy must be > 0", field="vehicle.usable_energy_kWh")
        if not isinstance(self.road_load, RoadLoad):
            raise InputValidationError("road_load must be a RoadLoad", field="vehicle.road_load")

    def m_eq(self, ratio_total: float) -> float:
        r = self.wheel_radius_m
        return self.mass_kg + self.J_wheels_kgm2 / r ** 2 + self.J_motor_kgm2 * (ratio_total / r) ** 2

    def road_force(self, v: float, alpha: float) -> tuple:
        """(constant, linear, quadratic) parts of the road-load force at v (opposing forward motion, N)."""
        rl = self.road_load
        if rl.form == "abc":
            return rl.A_N, rl.B_N_per_mps * v, rl.C_N_per_mps2 * v * v
        return self.mass_kg * G * rl.c_rr * math.cos(alpha), 0.0, 0.5 * rl.rho_kg_m3 * rl.CdA_m2 * v * v

    def describe(self) -> dict:
        rl = self.road_load
        return {"mass_kg": self.mass_kg, "wheel_radius_m": self.wheel_radius_m, "J_wheels_kgm2": self.J_wheels_kgm2,
                "J_motor_kgm2": self.J_motor_kgm2, "axle_ratio": self.axle_ratio,
                "axle_eta": [self.axle_eta_forward, self.axle_eta_reverse],
                "road_load": {"form": rl.form, **({"A_N": rl.A_N, "B_N_per_mps": rl.B_N_per_mps,
                                                   "C_N_per_mps2": rl.C_N_per_mps2} if rl.form == "abc" else
                                                  {"c_rr": rl.c_rr, "CdA_m2": rl.CdA_m2, "rho_kg_m3": rl.rho_kg_m3}),
                              "basis": rl.basis, "includes_edrive_drag": rl.includes_edrive_drag},
                "regen": {"share": self.regen_share, "min_speed_kmh": self.regen_min_speed_kmh},
                "aux_hv_W": self.aux_hv_W, "usable_energy_kWh": self.usable_energy_kWh, "basis": self.basis}


def vehicle_from_dict(d: dict, J_motor_kgm2: float | None = None) -> Vehicle:
    """The project's ``vehicle`` section (or a request body); the machine inertia defaults to the driveline ROM's."""
    if not d:
        raise InputValidationError("no vehicle data", field="vehicle")
    rl = d.get("road_load") or {}
    road = RoadLoad(str(rl.get("form", "abc")), float(rl.get("A_N", 0.0)), float(rl.get("B_N_per_mps", 0.0)),
                    float(rl.get("C_N_per_mps2", 0.0)), float(rl.get("c_rr", 0.0)), float(rl.get("CdA_m2", 0.0)),
                    float(rl.get("rho_kg_m3", 1.2)), str(rl.get("basis", "")), bool(rl.get("includes_edrive_drag")))
    rg = d.get("regen") or {}
    ax = d.get("axle") or {}
    jm = d.get("J_motor_kgm2")
    return Vehicle(float(d["mass_kg"]), float(d["wheel_radius_m"]), road, float(d.get("J_wheels_kgm2", 0.0)),
                   float(jm if jm is not None else (J_motor_kgm2 or 0.0)), float(ax.get("ratio", 1.0)),
                   float(ax.get("eta_forward", 1.0)), float(ax.get("eta_reverse", 1.0)),
                   float(rg.get("share", 1.0)), float(rg.get("min_speed_kmh", 0.0)), float(d.get("aux_hv_W", 0.0)),
                   None if d.get("usable_energy_kWh") is None else float(d["usable_energy_kWh"]),
                   str(d.get("basis", "")))


def validate_vehicle(d: dict) -> None:
    """Project-section validator (the same parser as a request body)."""
    vehicle_from_dict(d)


# --------------------------------------------------------------------------------------------- the run

@dataclass
class _Acc:
    """Energy accumulators (J)."""

    e: dict = field(default_factory=lambda: {k: 0.0 for k in (
        "ocv", "terminal", "dc_pos", "dc_neg", "aux", "wheel_pos", "wheel_neg", "road_const", "road_lin",
        "road_quad", "grade", "kinetic", "friction", "braking_available", "regen_ocv", "traction_ocv",
        "shortfall", *LOSS_KEYS)})
    t: dict = field(default_factory=lambda: {k: 0.0 for k in (IDLE, TRACTION, COAST, REGEN, REGEN_LIMITED, FRICTION,
                                                              NOT_DELIVERED, UNKNOWN_STEP)})
    dist_m: float = 0.0


class _Machine:
    """The machine side of one run: the policy point for a motor-shaft torque at a speed (evaluators cached)."""

    def __init__(self, drive, Vdc: float, limits, temps: dict):
        from ..scenario import Scenario
        from ..solvers.policy import PolicyEvaluator
        self.drive, self.Vdc, self.limits, self.temps = drive, Vdc, limits, temps
        self._Sc, self._PE = Scenario, PolicyEvaluator
        self._ev = {}

    def evaluator(self, n_rpm: float):
        ev = self._ev.get(n_rpm)
        if ev is None:
            sc = self._Sc("cycle", float(n_rpm), self.Vdc, self.limits, **self.temps)
            ev = self._ev[n_rpm] = self._PE(self.drive, sc)
        return ev

    def solve(self, n_rpm: float, T_m: float):
        sol = self.evaluator(n_rpm).solve(T_m)
        return sol.policy_claim.status.value, sol.point, sol.policy_claim.detail


def _motor_torque_for_output(reducer, n: float, T_o: float, oil_C: float) -> tuple:
    """Motor-shaft torque giving T_o at the reducer output: the reducer's own inverse, or bisection on its forward
    evaluation for a loss-map reducer (the output torque rises with the shaft torque)."""
    inv = reducer.motor_torque_for_output(n, T_o, oil_C)
    if inv["status"] == "DEFINED" or reducer.map_forward is None:
        return inv["T_m_Nm"], inv["reason"]
    lo, hi = -reducer.torque_Nm[1], reducer.torque_Nm[1]
    w_o = n * 2 * math.pi / 60.0 / reducer.ratio

    def out(Tm):
        r = reducer.output_from_motor(n, Tm, oil_C)
        return None if r["P_o_W"] is None else r["P_o_W"] / w_o
    a, b = out(lo), out(hi)
    if a is None or b is None or not (a <= T_o <= b):
        return None, "output torque outside the reducer loss map's range at this speed"
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        m = out(mid)
        if m is None:
            return None, "the reducer loss map does not define the inverse here (mixed-flow region)"
        lo, hi = (mid, hi) if m < T_o else (lo, mid)
        if hi - lo <= 1e-9 * max(1.0, abs(mid)):
            break
    return 0.5 * (lo + hi), "inverse of the directional loss map (bisection)"


def run_cycle(drive, cycle: Cycle, vehicle: Vehicle, reducer, *, Vdc_V: float, limits, oil_temp_C: float,
              source_R_ohm: float = 0.0, temps: dict | None = None, requirements: dict | None = None,
              keep_trace: bool = True) -> dict:
    """Energy and losses of ``drive`` on ``cycle`` in ``vehicle`` (see the module text)."""
    if reducer is None:
        raise InputValidationError("a drive cycle needs the reducer (ratio and losses between machine and wheels)",
                                   field="reducer")
    if _finite("source_R_ohm", source_R_ohm) < 0:
        raise InputValidationError("source resistance must be >= 0", field="source_R_ohm")
    Vdc = _finite("Vdc_V", Vdc_V)
    machine = _Machine(drive, Vdc, limits, {k: v for k, v in (temps or {}).items() if v is not None})
    g_total = reducer.ratio * vehicle.axle_ratio
    m_eq = vehicle.m_eq(g_total)
    r = vehicle.wheel_radius_m
    v_cut = vehicle.regen_min_speed_kmh * KMH
    t = np.asarray(cycle.t_s, float)
    v = np.asarray(cycle.v_mps, float)
    gr = np.asarray(cycle.grade, float) if cycle.grade else np.zeros_like(v)
    phases = list(cycle.phases)
    acc = _Acc()
    ph_acc = [{"name": p["name"], "start_s": float(p["start_s"]), "end_s": float(p["end_s"]), "dist_m": 0.0,
               "ocv_J": 0.0, "dc_J": 0.0} for p in phases]
    tr = {k: [] for k in ("t_mid_s", "v_kmh", "a_mps2", "n_rpm", "T_out_demand_Nm", "T_out_drive_Nm", "T_m_Nm",
                          "P_dc_W", "P_ocv_W", "P_friction_W", "loss_W", "state", "id_A", "iq_A")}
    unknown_reasons, not_delivered = [], []
    n_steps = t.size - 1
    with progress.span(n_steps, "drive cycle") as sp:
        for k in range(n_steps):
            sp.step()
            dt = t[k + 1] - t[k]
            vm = 0.5 * (v[k] + v[k + 1])
            a = (v[k + 1] - v[k]) / dt
            alpha = math.atan(0.5 * (gr[k] + gr[k + 1]))
            fc, fl, fq = vehicle.road_force(vm, alpha) if vm > 0 else (0.0, 0.0, 0.0)
            fg = vehicle.mass_kg * G * math.sin(alpha) if vm > 0 else 0.0
            fi = m_eq * a
            F = fc + fl + fq + fg + fi
            acc.dist_m += vm * dt
            for k_, f_ in (("road_const", fc), ("road_lin", fl), ("road_quad", fq), ("grade", fg), ("kinetic", fi)):
                acc.e[k_] += f_ * vm * dt
            state, P_dc, losses, T_o_drive, T_m, pt_id, pt_iq = IDLE, 0.0, dict.fromkeys(LOSS_KEYS, 0.0), 0.0, 0.0, \
                None, None
            P_fric = 0.0
            n = 0.0
            T_out_dem = 0.0
            if vm <= 0.0 and a <= 0.0:
                pass                                      # at rest: held by the brakes, inverter not switching
            else:
                w_wheel = vm / r
                w_out = w_wheel * vehicle.axle_ratio
                n = w_out * reducer.ratio * 60.0 / (2 * math.pi)
                T_wheel = F * r
                # wheel -> reducer output boundary through the axle (per direction)
                T_out_dem = (T_wheel / (vehicle.axle_ratio * vehicle.axle_eta_forward) if T_wheel >= 0 else
                             T_wheel * vehicle.axle_eta_reverse / vehicle.axle_ratio)
                state, T_o_drive, T_m, P_dc, pt, why = _interval(machine, reducer, oil_temp_C, n, T_out_dem, vm,
                                                                 v_cut, vehicle.regen_share)
                if pt is not None:
                    pt_id, pt_iq = pt.id_A, pt.iq_A
                if state == UNKNOWN_STEP:
                    unknown_reasons.append(f"t = {t[k]:g}..{t[k + 1]:g} s, {n:.0f} rpm, {T_m:.1f} N m: {why}")
                else:
                    if pt is not None:
                        losses["inverter"] = pt.Pinv_W or 0.0
                        losses["motor_copper"] = pt.Pcu_W
                        losses["motor_rotational"] = pt.Prot_W or 0.0
                        red = reducer.output_from_motor(n, pt.Tshaft_Nm, oil_temp_C)
                        losses["reducer"] = red["loss_W"] or 0.0
                    P_o = T_o_drive * w_out
                    P_wheel_drive = (P_o * vehicle.axle_eta_forward if P_o >= 0 else P_o / vehicle.axle_eta_reverse)
                    losses["axle"] = P_o - P_wheel_drive
                    P_wheel_dem = F * vm
                    if F < 0:
                        P_fric = max(0.0, P_wheel_drive - P_wheel_dem)          # the brakes absorb the rest
                        acc.e["braking_available"] += -P_wheel_dem * dt
                    elif state == NOT_DELIVERED:
                        acc.e["shortfall"] += max(0.0, P_wheel_dem - P_wheel_drive) * dt
                        not_delivered.append({"t_s": float(t[k]), "v_kmh": vm * 3.6, "n_rpm": n,
                                              "T_out_demand_Nm": T_out_dem, "T_out_delivered_Nm": T_o_drive,
                                              "reason": why})
            P_term = P_dc + vehicle.aux_hv_W
            I = P_term / Vdc
            losses["battery"] = source_R_ohm * I * I
            P_ocv = P_term + losses["battery"]
            if state != UNKNOWN_STEP:
                acc.e["ocv"] += P_ocv * dt
                acc.e["terminal"] += P_term * dt
                acc.e["dc_pos" if P_dc >= 0 else "dc_neg"] += abs(P_dc) * dt
                acc.e["aux"] += vehicle.aux_hv_W * dt
                acc.e["friction"] += P_fric * dt
                for kk in LOSS_KEYS:
                    acc.e[kk] += losses[kk] * dt
                P_wheel_delivered = (F * vm) if state != NOT_DELIVERED else (
                    (T_o_drive * w_out * vehicle.axle_eta_forward) if T_o_drive >= 0 else 0.0)
                acc.e["wheel_pos" if P_wheel_delivered >= 0 else "wheel_neg"] += abs(P_wheel_delivered) * dt
                if P_ocv < 0:
                    acc.e["regen_ocv"] += -P_ocv * dt
                else:
                    acc.e["traction_ocv"] += P_ocv * dt
            acc.t[state] += dt
            tm = 0.5 * (t[k] + t[k + 1])
            for p in ph_acc:
                if p["start_s"] <= tm < p["end_s"]:
                    p["dist_m"] += vm * dt
                    if state != UNKNOWN_STEP:
                        p["ocv_J"] += P_ocv * dt
                        p["dc_J"] += P_dc * dt
            if keep_trace:
                tr["t_mid_s"].append(tm)
                tr["v_kmh"].append(vm * 3.6)
                tr["a_mps2"].append(a)
                tr["n_rpm"].append(n)
                tr["T_out_demand_Nm"].append(T_out_dem)
                tr["T_out_drive_Nm"].append(T_o_drive)
                tr["T_m_Nm"].append(T_m)
                tr["P_dc_W"].append(P_dc)
                tr["P_ocv_W"].append(P_ocv)
                tr["P_friction_W"].append(P_fric)
                tr["loss_W"].append(sum(losses.values()))
                tr["state"].append(state)
                tr["id_A"].append(pt_id)
                tr["iq_A"].append(pt_iq)
    return _summarise(cycle, vehicle, reducer, acc, ph_acc, tr if keep_trace else None, unknown_reasons,
                      not_delivered, m_eq, g_total, Vdc, source_R_ohm, oil_temp_C, requirements or {})


def _interval(machine, reducer, oil_C, n, T_out_dem, vm, v_cut, share):
    """One moving interval -> (state, T_out by the drive, T_m, P_dc, point, reason)."""
    w_out = n * 2 * math.pi / 60.0 / reducer.ratio
    # output torque at zero shaft torque: the drive's own drag (<= 0)
    zero = reducer.output_from_motor(n, 0.0, oil_C)
    if zero["P_o_W"] is None:
        return UNKNOWN_STEP, 0.0, 0.0, 0.0, None, f"reducer: {zero['reason']}"
    T_o0 = zero["P_o_W"] / w_out if w_out > 0 else 0.0
    braking = T_out_dem < T_o0                         # more braking than the drive's drag alone
    if braking:
        if vm < v_cut or share <= 0.0:
            st, pt, why = machine.solve(n, 0.0)
            if pt is None or st == "UNKNOWN" or pt.Pdc_W is None:
                return UNKNOWN_STEP, 0.0, 0.0, 0.0, None, why or "DC power not defined at zero torque"
            return FRICTION, T_o0, 0.0, pt.Pdc_W, pt, ""
        target = min(share * T_out_dem, T_o0)
    else:
        target = T_out_dem
    T_m, why = _motor_torque_for_output(reducer, n, target, oil_C)
    if T_m is None:
        return UNKNOWN_STEP, 0.0, 0.0, 0.0, None, f"reducer inverse: {why}"
    st, pt, why = machine.solve(n, T_m)
    if st == "FEASIBLE" and pt is not None:
        if pt.Pdc_W is None:
            return UNKNOWN_STEP, 0.0, T_m, 0.0, None, "DC power not defined (inverter or rotational loss model missing)"
        return (REGEN if braking else (TRACTION if T_m > 0 else COAST)), target, T_m, pt.Pdc_W, pt, ""
    if st not in ("INFEASIBLE",):
        return UNKNOWN_STEP, 0.0, T_m, 0.0, None, f"policy {st}: {why}"
    # not deliverable: the largest deliverable share between the drag point (T_o0) and the target
    lo_o, hi_o = T_o0, target            # lo deliverable (zero shaft torque), hi not
    best = None
    st0, pt0, why0 = machine.solve(n, 0.0)
    if st0 != "FEASIBLE" or pt0 is None or pt0.Pdc_W is None:
        return UNKNOWN_STEP, 0.0, 0.0, 0.0, None, f"zero torque not deliverable: {why0}"
    best = (T_o0, 0.0, pt0)
    for _ in range(14):
        mid = 0.5 * (lo_o + hi_o)
        Tm_mid, _w = _motor_torque_for_output(reducer, n, mid, oil_C)
        if Tm_mid is None:
            break
        s2, p2, _w2 = machine.solve(n, Tm_mid)
        if s2 == "FEASIBLE" and p2 is not None:
            lo_o, best = mid, (mid, Tm_mid, p2)
        else:
            hi_o = mid
        if abs(hi_o - lo_o) <= 1e-3 * max(1.0, abs(target)):
            break
    T_o_b, T_m_b, p_b = best
    return (REGEN_LIMITED if braking else NOT_DELIVERED), T_o_b, T_m_b, p_b.Pdc_W, p_b, \
        f"policy INFEASIBLE at {T_m:.1f} N m ({why}); delivered {T_m_b:.1f} N m"


def _summarise(cycle, vehicle, reducer, acc, ph_acc, tr, unknown_reasons, not_delivered, m_eq, g_total, Vdc, R,
               oil_C, req) -> dict:
    e = acc.e
    km = acc.dist_m / 1e3
    complete = acc.t[UNKNOWN_STEP] <= 0.0
    losses = {k: e[k] for k in LOSS_KEYS}
    wheel_net = e["wheel_pos"] - e["wheel_neg"]
    closure = e["ocv"] - (wheel_net + e["friction"] + sum(losses.values()) + e["aux"])
    scale = e["traction_ocv"] + e["regen_ocv"] + e["friction"] + e["aux"] + 1.0
    whkm = (lambda J: None if (not complete or km <= 0) else J / 3600.0 / km)
    cons_ocv = whkm(e["ocv"])
    regen_avail = e["braking_available"]
    res = {
        "cycle": {"name": cycle.name, "source": cycle.source, **cycle.stats(), "official": cycle.official},
        "vehicle": {**vehicle.describe(), "m_eq_kg": m_eq, "ratio_total": g_total},
        "conditions": {"Vdc_V": Vdc, "source_R_ohm": R, "oil_temp_C": oil_C,
                       "reducer": reducer.describe()},
        "complete": complete,
        "time_s": dict(acc.t),
        "energy_kWh": {
            "battery_ocv_net": e["ocv"] / 3.6e6, "battery_terminal_net": e["terminal"] / 3.6e6,
            "battery_traction_out": e["traction_ocv"] / 3.6e6, "battery_regen_in": e["regen_ocv"] / 3.6e6,
            "inverter_dc_motoring": e["dc_pos"] / 3.6e6, "inverter_dc_regeneration": e["dc_neg"] / 3.6e6,
            "wheel_positive": e["wheel_pos"] / 3.6e6, "wheel_negative": e["wheel_neg"] / 3.6e6,
            "friction_brakes": e["friction"] / 3.6e6, "aux_hv": e["aux"] / 3.6e6,
            "braking_available_at_wheels": regen_avail / 3.6e6, "shortfall_at_wheels": e["shortfall"] / 3.6e6},
        "losses_kWh": {k: v / 3.6e6 for k, v in losses.items()},
        "road_work_kWh": {"rolling_constant": e["road_const"] / 3.6e6, "linear": e["road_lin"] / 3.6e6,
                          "aero_quadratic": e["road_quad"] / 3.6e6, "grade": e["grade"] / 3.6e6,
                          "kinetic_net": e["kinetic"] / 3.6e6},
        "consumption_Wh_per_km": {"battery_ocv": cons_ocv, "battery_terminal": whkm(e["terminal"]),
                                  "inverter_dc_net": whkm(e["dc_pos"] - e["dc_neg"])},
        "regeneration": {"recovered_to_battery_kWh": e["regen_ocv"] / 3.6e6,
                         "available_at_wheels_kWh": regen_avail / 3.6e6,
                         "recovery_ratio": (e["regen_ocv"] / regen_avail) if regen_avail > 0 else None,
                         "friction_share": (e["friction"] / regen_avail) if regen_avail > 0 else None,
                         "meaning": "recovery ratio = energy into the battery OCV during regeneration / braking "
                                    "energy the trace demands at the wheels (road load already subtracted)"},
        "closure": {"residual_kWh": closure / 3.6e6, "relative": abs(closure) / scale,
                    "meaning": "OCV energy - (net wheel work + friction + losses + auxiliaries); the kinetic energy "
                               "of a trace that ends where it started is in the wheel work and nets to "
                               f"{e['kinetic'] / 3.6e6:.3g} kWh"},
        "followed": {"status": (UNKNOWN if not complete else (FAIL if not_delivered else PASS)),
                     "not_delivered_s": acc.t[NOT_DELIVERED], "intervals": not_delivered[:200],
                     "count": len(not_delivered),
                     "meaning": "every interval of the trace delivered by the drive at the declared conditions "
                                "(strict: the regulation's speed-tolerance band is not evaluated)"},
        "unknown": {"count": len(unknown_reasons), "reasons": unknown_reasons[:50]},
        "phases": [{"name": p["name"], "start_s": p["start_s"], "end_s": p["end_s"], "distance_km": p["dist_m"] / 1e3,
                    "Wh_per_km_battery_ocv": (p["ocv_J"] / 3.6e3 / (p["dist_m"] / 1e3)
                                              if complete and p["dist_m"] > 0 else None),
                    "Wh_per_km_inverter_dc": (p["dc_J"] / 3.6e3 / (p["dist_m"] / 1e3)
                                              if complete and p["dist_m"] > 0 else None)} for p in ph_acc],
        "notes": _notes(vehicle, R),
    }
    if vehicle.usable_energy_kWh and cons_ocv and cons_ocv > 0:
        res["range_km"] = vehicle.usable_energy_kWh * 1e3 / cons_ocv
    res["verdicts"] = _verdicts(res, req)
    if tr is not None:
        res["trace"] = tr
    return res


def _notes(vehicle, R) -> list:
    out = ["backward (kinematic) model: the trace is followed where the drive delivers it; intervals it cannot "
           "deliver are reported, the trace is not re-planned",
           "machine points: minimum-current policy at the declared inverter DC voltage (model efficiency, not a "
           "loss-optimal control); PWM harmonic motor losses and DC-link capacitor losses are not included",
           "at rest the inverter does not switch (declared idle assumption); the auxiliaries run the whole trace",
           f"battery: the DC voltage is held at the declared value; the source resistance {R * 1e3:g} mohm dissipates "
           f"R I^2 (the voltage drop is not fed back into the machine point)"]
    if vehicle.road_load.includes_edrive_drag:
        out.append("the road-load coefficients are declared to contain this drive's drag (coast-down with the drive "
                   "connected): the reducer and machine drag are then counted twice - consumption is conservative")
    if vehicle.axle_ratio == 1.0 and vehicle.axle_eta_forward == 1.0 and vehicle.axle_eta_reverse == 1.0:
        out.append("axle: the reducer's output boundary turns with the wheels and the differential / half-shafts are "
                   "lossless (declared 1 / 1)")
    return out


def _verdicts(res: dict, req: dict) -> list:
    out = []
    c = res["consumption_Wh_per_km"]["battery_ocv"]
    lim = req.get("consumption_Wh_per_km_max")
    if lim is not None:
        out.append({"id": "consumption", "requirement": f"battery consumption <= {float(lim):g} Wh/km",
                    "value": c, "status": UNKNOWN if c is None else (PASS if c <= float(lim) else FAIL),
                    "margin": None if c is None else float(lim) - c})
    rng = req.get("range_km_min")
    if rng is not None:
        v = res.get("range_km")
        out.append({"id": "range", "requirement": f"range >= {float(rng):g} km (usable energy / consumption)",
                    "value": v, "status": UNKNOWN if v is None else (PASS if v >= float(rng) else FAIL),
                    "margin": None if v is None else v - float(rng)})
    out.append({"id": "followed", "requirement": "the drive delivers the whole trace", "value":
                res["followed"]["not_delivered_s"], "status": res["followed"]["status"], "margin": None})
    rr = req.get("recovery_ratio_min")
    if rr is not None:
        v = res["regeneration"]["recovery_ratio"]
        out.append({"id": "recovery", "requirement": f"recovery ratio >= {float(rr):g}", "value": v,
                    "status": UNKNOWN if (v is None or not res["complete"]) else (PASS if v >= float(rr) else FAIL),
                    "margin": None if v is None else v - float(rr)})
    return out
