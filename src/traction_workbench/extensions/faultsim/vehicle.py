"""Longitudinal vehicle for the vehicle-level consequences of an unintended torque (distance, acceleration, speed).

Driveline.  The simulated machine drives the wheels through the transmission (ratio i, efficiency eta on the driving
side, wheel radius r) - a P2 position, always coupled; in a P1 position its torque reaches the wheels only while the
disconnect clutch K0 is closed (an open K0 leaves the vehicle at rest whatever P1 does - an engine start at a
standstill is not a vehicle motion).  Equivalent mass m_eq = m + J_wheels / r^2 + J_machine (i / r)^2.

Motion.  m_eq dv/dt = F_drive - F_road - F_brake with F_drive = T i eta / r (T > 0 driving; a braking torque is
reflected without eta), F_road = m g c_rr (opposing the motion; at rest it holds the vehicle up to its magnitude -
no roll-back from the road load) + 1/2 rho c_d A v^2, F_brake = m a_brake from the driver's reaction instant on (the
onset of the unintended torque plus the reaction delay), holding the vehicle once it is at rest.

Torque.  Over the electrical run the shaft torque of the trajectory (the engine's plant truth); after its end a
declared CONTINUATION of the state the run ended in: ``final_mean`` (the mean of the last part of the run - a drive
still tracking a wrong command), ``zero`` (six-switch-off below the rectification onset), ``asc`` (the active
short circuit's steady torque at the current speed, from the dq model), ``constant`` (a declared value).  The
continuation is a model statement and is reported with the result.  The electrical run itself sees the vehicle as
the machine's inertia (J_eq = m_eq (r / i)^2) without road load (conservative for an unintended acceleration).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ...errors import InputValidationError

G = 9.81
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


@dataclass(frozen=True)
class VehicleSpec:
    mass_kg: float
    wheel_radius_m: float
    ratio: float
    efficiency: float = 1.0
    c_rr: float = 0.0
    cdA_m2: float = 0.0
    rho: float = 1.2
    J_machine_kgm2: float = 0.0
    J_wheels_kgm2: float = 0.0
    position: str = "P2"               # P2: always coupled; P1: coupled only with K0 closed
    k0_closed: bool = True

    def __post_init__(self):
        for k in ("mass_kg", "wheel_radius_m", "ratio"):
            v = getattr(self, k)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise InputValidationError(f"{k} must be > 0", field=f"vehicle.{k}")
        if not (0 < self.efficiency <= 1):
            raise InputValidationError("efficiency must be in (0, 1]", field="vehicle.efficiency")
        if self.position not in ("P1", "P2"):
            raise InputValidationError("position must be P1 or P2", field="vehicle.position")

    @property
    def m_eq(self) -> float:
        r, i = self.wheel_radius_m, self.ratio
        return self.mass_kg + self.J_wheels_kgm2 / r ** 2 + self.J_machine_kgm2 * (i / r) ** 2

    @property
    def coupled(self) -> bool:
        return self.position == "P2" or self.k0_closed

    def J_eq_machine(self) -> float:
        """The vehicle seen at the machine shaft (kg m^2): what the electrical run integrates the speed with."""
        if not self.coupled:
            return self.J_machine_kgm2 or 0.05
        return self.m_eq * (self.wheel_radius_m / self.ratio) ** 2

    def machine_rpm(self, v_mps: float) -> float:
        return v_mps / self.wheel_radius_m * self.ratio * 60.0 / (2 * math.pi) if self.coupled else 0.0


def vehicle_from_dict(d: dict | None) -> VehicleSpec | None:
    if not d:
        return None
    return VehicleSpec(float(d["mass_kg"]), float(d["wheel_radius_m"]), float(d["ratio"]),
                       float(d.get("efficiency", 1.0)), float(d.get("c_rr", 0.0)), float(d.get("cdA_m2", 0.0)),
                       float(d.get("rho", 1.2)), float(d.get("J_machine_kgm2", 0.0)),
                       float(d.get("J_wheels_kgm2", 0.0)), str(d.get("position", "P2")),
                       bool(d.get("k0_closed", True)))


def _asc_torque(machine: dict, speed_rpm: float) -> float:
    from .strategy import asc_steady_point
    p = int(machine["p"])
    w_e = p * speed_rpm * 2 * math.pi / 60.0
    i_d, i_q = asc_steady_point(w_e, machine["Ld"], machine["Lq"], machine["psi"], machine["Rs"])
    return 1.5 * p * (machine["psi"] * i_q + (machine["Ld"] - machine["Lq"]) * i_d * i_q)


def continue_vehicle(result, vs: VehicleSpec, *, v0_mps: float = 0.0, onset_s: float | None = None,
                     t_react_s: float = 1.0, a_brake_mps2: float = 5.0, continuation: str = "final_mean",
                     tail_s: float = 0.005, T_const_Nm: float = 0.0, machine: dict | None = None,
                     t_end_s: float = 20.0, dt_s: float = 1e-3) -> dict:
    """Integrate the vehicle from the start of the run until it is at rest again after braking (or ``t_end_s``);
    ``onset_s``: the onset of the unintended torque (default: the first primary fault of the run)."""
    tr = result["trace"] if isinstance(result, dict) else result.trace
    echo = result["setup_echo"] if isinstance(result, dict) and "setup_echo" in result else (
        result.get("setup") if isinstance(result, dict) else result.setup_echo)
    t_tr = np.asarray(tr["t"], dtype=float)
    T_tr = np.asarray(tr["T_shaft"], dtype=float)
    if onset_s is None:
        fl = [f["t_s"] for f in (echo or {}).get("faults", []) if f["kind"] not in
              ("mechanism_disabled", "path_lost", "contactor_stuck")]
        onset_s = min(fl) if fl else 0.0
    t_run_end = float(t_tr[-1]) if len(t_tr) else 0.0
    tail = t_tr >= t_run_end - tail_s
    T_tail = float(np.mean(T_tr[tail])) if tail.any() else 0.0
    if continuation not in ("final_mean", "zero", "asc", "constant"):
        raise InputValidationError("continuation must be final_mean, zero, asc or constant", field="continuation")
    if continuation == "asc" and not machine:
        raise InputValidationError("the ASC continuation needs the machine parameters", field="continuation")
    t_brake = onset_s + t_react_s
    m, r, i, eta = vs.mass_kg, vs.wheel_radius_m, vs.ratio, vs.efficiency
    meq = vs.m_eq
    v, x, t = float(v0_mps), 0.0, 0.0
    ts, vs_, xs, a_s, Ts = [], [], [], [], []
    stopped_at, moved = None, v0_mps > 1e-6
    while t <= t_end_s + 1e-12:
        if t <= t_run_end:
            T = float(np.interp(t, t_tr, T_tr))
        elif continuation == "final_mean":
            T = T_tail
        elif continuation == "zero":
            T = 0.0
        elif continuation == "constant":
            T = T_const_Nm
        else:
            T = _asc_torque(machine, vs.machine_rpm(v))
        if not vs.coupled:
            T = 0.0
        F_drive = T * i / r * (eta if T * (v if abs(v) > 1e-9 else 1.0) >= 0 else 1.0)
        F_aero = 0.5 * vs.rho * vs.cdA_m2 * v * abs(v)
        F_rr = m * G * vs.c_rr
        braking = t >= t_brake - 1e-12
        F_b = m * a_brake_mps2 if braking else 0.0
        if abs(v) < 1e-9:
            # at rest: the road load and the brake hold the vehicle up to their magnitude
            net = F_drive - F_aero
            hold = F_rr + F_b
            a = 0.0 if abs(net) <= hold else (net - math.copysign(hold, net)) / meq
            if braking and moved and abs(net) <= hold:
                stopped_at = t
        else:
            a = (F_drive - F_aero - math.copysign(F_rr + F_b, v)) / meq
        ts.append(t)
        vs_.append(v)
        xs.append(x)
        a_s.append(a)
        Ts.append(T)
        if stopped_at is not None:
            break
        v_new = v + a * dt_s
        if braking and v != 0.0 and v_new * v <= 0.0:
            # the brake brings the vehicle to rest within this step
            frac = abs(v) / max(abs(v - v_new), 1e-12)
            x += v * frac * dt_s * 0.5
            t += frac * dt_s
            v = 0.0
            stopped_at = t
            ts.append(t)
            vs_.append(0.0)
            xs.append(x)
            a_s.append(a)
            Ts.append(T)
            break
        x += 0.5 * (v + v_new) * dt_s
        v = v_new
        if abs(v) > 1e-6:
            moved = True
        t += dt_s
    return {"t": np.asarray(ts), "v_mps": np.asarray(vs_), "x_m": np.asarray(xs), "a_mps2": np.asarray(a_s),
            "T_Nm": np.asarray(Ts), "distance_m": float(xs[-1]) if xs else 0.0, "stopped_at_s": stopped_at,
            "moved": moved, "v_max_mps": float(np.max(np.abs(vs_))) if vs_ else 0.0,
            "a_max_mps2": float(np.max(a_s)) if a_s else 0.0, "onset_s": onset_s, "t_brake_s": t_brake,
            "run_end_s": t_run_end, "continuation": continuation,
            "continuation_torque_Nm": T_tail if continuation == "final_mean" else (
                0.0 if continuation == "zero" else (T_const_Nm if continuation == "constant" else None)),
            "basis": (f"vehicle m {vs.mass_kg:g} kg (m_eq {meq:.0f} kg), r {r:g} m, i {i:g}, eta {eta:g}, c_rr "
                      f"{vs.c_rr:g}, cdA {vs.cdA_m2:g} m^2, {vs.position}{'' if vs.coupled else ' (K0 open)'}; "
                      f"electrical run to {t_run_end * 1e3:.4g} ms, then '{continuation}' continuation; brake "
                      f"{a_brake_mps2:g} m/s^2 from {t_brake:.4g} s")}


def judge_distance(veh: dict, limit_m: float) -> dict:
    d = veh["distance_m"]
    if veh["stopped_at_s"] is None and veh["moved"]:
        return {"verdict": UNKNOWN, "distance_m": d, "reason": "the vehicle was not at rest again within the "
                                                               "integration time"}
    ok = d <= limit_m + 1e-9
    return {"verdict": PASS if ok else FAIL, "distance_m": d, "limit_m": limit_m,
            "reason": f"standstill to standstill {d:.3g} m {'<=' if ok else '>'} {limit_m:g} m"}


def judge_acceleration(veh: dict, curve: list | None, open_name: str | None = None) -> dict:
    """The acceleration against a maximum curve a_max(v) [(v_kph, a_mps2), ...] (linear); an OPEN curve: UNKNOWN
    with the measured maximum."""
    a, v = veh["a_mps2"], np.abs(veh["v_mps"]) * 3.6
    k = int(np.argmax(a)) if len(a) else 0
    if not curve:
        return {"verdict": UNKNOWN, "a_max_mps2": float(a[k]) if len(a) else 0.0,
                "at_kph": float(v[k]) if len(v) else 0.0,
                "reason": f"the acceleration target curve {open_name or ''} is not declared (OPEN)"}
    cv, ca = np.array([p[0] for p in curve], float), np.array([p[1] for p in curve], float)
    lim = np.interp(v, cv, ca)
    ex = a - lim
    j = int(np.argmax(ex))
    ok = ex[j] <= 1e-9
    return {"verdict": PASS if ok else FAIL, "a_max_mps2": float(a[k]), "worst_excess_mps2": float(ex[j]),
            "at_kph": float(v[j]), "reason": f"worst {'margin' if ok else 'excess'} {abs(ex[j]):.3g} m/s^2 at "
                                             f"{v[j]:.3g} km/h"}
