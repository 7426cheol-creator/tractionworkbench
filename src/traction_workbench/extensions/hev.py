"""Hybrid (HEV / PHEV) electric drive system on one DC bus (OEW/HEV addendum section 6, P1-H).

One topology family at a time, from its declared connection graph - the position labels P0..P4 are shorthand only:

* machines EM_k (each a single-VSI ``DriveModel``; an OEW machine is analysed by ``extensions.oew``) on ONE common
  DC bus with one source (battery, optionally behind a boost converter);
* mechanical coupling through declared fixed ratios or one simple planetary set (Willis equation), torques
  positive INTO the gear set;
* joint capability K = {(T_1, T_2, ...): every component AND the common constraints hold together} - never the
  Cartesian box of the separate maxima; branch stress (each machine's DC current, losses) is kept next to the net
  source power (a small net does not hide an 80 / 70 kW branch pair);
* engine cranking as a time-domain replay of a declared crank-angle load (compression peak, initial angle, Vdc sag,
  traction reserve) - "cranking requirement met" is not "engine start guaranteed" (combustion needs engine evidence);
* load rejection: a generator that keeps feeding the bus after a traction trip; the common capacitor energy is
  counted once.

Every result is a model claim for the declared mode, initial state and policy; sampled sweeps are coverage evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..models.flux import _finite
from ..scenario import DcSourceLimits, Scenario
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

TWO_PI = 2.0 * math.pi
UNLIMITED = DcSourceLimits(math.inf, math.inf, math.inf, math.inf)


# --------------------------------------------------------------------------------------------- mechanical graph

def planetary_speeds(Ns: int, Nr: int, sun=None, ring=None, carrier=None) -> dict:
    """Willis: Ns w_s + Nr w_r = (Ns + Nr) w_c; exactly one of the three speeds unknown (None)."""
    if Ns <= 0 or Nr <= 0 or Nr <= Ns:
        raise InputValidationError("simple planetary needs 0 < Ns < Nr (teeth)", field="planetary")
    known = [x is not None for x in (sun, ring, carrier)]
    if sum(known) != 2:
        raise InputValidationError("give exactly two of the three planetary speeds", field="planetary")
    if sun is None:
        sun = ((Ns + Nr) * carrier - Nr * ring) / Ns
    elif ring is None:
        ring = ((Ns + Nr) * carrier - Ns * sun) / Nr
    else:
        carrier = (Ns * sun + Nr * ring) / (Ns + Nr)
    return {"sun": float(sun), "ring": float(ring), "carrier": float(carrier)}


def planetary_torques(Ns: int, Nr: int, port: str, torque_Nm: float) -> dict:
    """Ideal (massless, lossless) planetary, torques INTO the gear set: (Ts, Tr, Tc) = lambda (Ns, Nr, -(Ns + Nr))."""
    n = {"sun": Ns, "ring": Nr, "carrier": -(Ns + Nr)}
    if port not in n:
        raise InputValidationError("port must be sun, ring or carrier", field="port")
    lam = torque_Nm / n[port]
    return {k: lam * v for k, v in n.items()}


def planetary_check(Ns: int, Nr: int, speeds_rpm: dict, torques_Nm: dict, limits_rpm: dict | None = None,
                    tol: float = 1e-6) -> dict:
    """H-04: kinematic (Willis) residual, torque-ratio residual and power residual of a proposed operating state.

    Speed and power balance alone do not fix the torque split: a distribution that closes the power balance but not
    the torque ratio is rejected (the gear set cannot hold it without acceleration or losses).
    """
    ws, wr, wc = (speeds_rpm[k] for k in ("sun", "ring", "carrier"))
    kin = Ns * ws + Nr * wr - (Ns + Nr) * wc
    Ts, Tr, Tc = (torques_Nm[k] for k in ("sun", "ring", "carrier"))
    lam = Ts / Ns
    ratio_res = max(abs(Tr - lam * Nr), abs(Tc + lam * (Ns + Nr)))
    w = {k: v * TWO_PI / 60 for k, v in speeds_rpm.items()}
    pwr = Ts * w["sun"] + Tr * w["ring"] + Tc * w["carrier"]
    scale_w = max(1.0, *(abs(v) for v in speeds_rpm.values()))
    scale_t = max(1.0, *(abs(v) for v in torques_Nm.values()))
    over = []
    for k, lim in (limits_rpm or {}).items():
        if k in speeds_rpm and abs(speeds_rpm[k]) > lim:
            over.append(f"{k}: |{speeds_rpm[k]:.0f}| rpm > {lim:.0f} rpm")
    ok_kin = abs(kin) <= tol * scale_w * (Ns + Nr)
    ok_tq = ratio_res <= tol * scale_t * (Ns + Nr)
    if not ok_kin or not ok_tq or over:
        st = Status.INFEASIBLE
        why = ([] if ok_kin else [f"Willis residual {kin:.4g} rpm*teeth"]) + \
              ([] if ok_tq else [f"torque-ratio residual {ratio_res:.4g} N*m (a speed/power-consistent split is not "
                                 f"enough)"]) + over
    else:
        st, why = Status.FEASIBLE, ["kinematics, ideal torque ratio and power balance close"]
    return {"status": st.value, "willis_residual": kin, "torque_ratio_residual_Nm": ratio_res,
            "power_residual_W": pwr, "detail": "; ".join(why),
            "assumptions": "massless, lossless simple planetary; torques positive into the gear set"}


# --------------------------------------------------------------------------------------------- DC source

@dataclass(frozen=True)
class Battery:
    ocv_V: float
    R_int_ohm: float
    limits: DcSourceLimits
    uv_min_V: float | None = None
    basis: str = ""

    def terminal(self, P_W: float) -> tuple[float | None, float | None]:
        """Terminal voltage and current for a terminal power P (discharge +): V = (OCV + sqrt(OCV^2 - 4 R P)) / 2."""
        if self.R_int_ohm == 0:
            return self.ocv_V, P_W / self.ocv_V
        disc = self.ocv_V ** 2 - 4.0 * self.R_int_ohm * P_W
        if disc < 0:
            return None, None
        v = 0.5 * (self.ocv_V + math.sqrt(disc))
        return v, P_W / v


@dataclass(frozen=True)
class BoostStage:
    """HV boost between battery and bus.  V_dc = V_b / (1 - D) (ideal CCM) is a sanity relation, the limits are the
    declared duty, inductor current, direction and a loss P = a0 + a2 I_L^2 with its basis."""

    D_max: float
    I_L_max_A: float
    a0_W: float = 0.0
    a2_W_per_A2: float = 0.0
    bidirectional: bool = True
    basis: str = ""

    def __post_init__(self):
        if not (0.0 <= _finite("D_max", self.D_max) < 1.0):
            raise InputValidationError("boost duty limit must be in [0, 1)", field="D_max")
        if _finite("I_L_max_A", self.I_L_max_A) <= 0:
            raise InputValidationError("inductor current limit must be > 0", field="I_L_max_A")

    def battery_side(self, P_bus_W: float, V_bus_V: float, V_bat_V: float) -> dict:
        D = 1.0 - V_bat_V / V_bus_V
        out = {"duty": D, "problems": []}
        if D < -1e-12:
            out["problems"].append(f"V_bus {V_bus_V:g} V below the battery voltage {V_bat_V:g} V (boost cannot buck)")
        if D > self.D_max:
            out["problems"].append(f"duty {D:.4f} > D_max {self.D_max:g}")
        if P_bus_W < 0 and not self.bidirectional:
            out["problems"].append("regenerative bus power but the boost is not bidirectional")
        a2, a0 = self.a2_W_per_A2, self.a0_W
        if a2 > 0:
            disc = V_bat_V ** 2 - 4 * a2 * (P_bus_W + a0)
            if disc < 0:
                out["problems"].append("boost loss model cannot deliver this bus power")
                out.update({"I_L_A": None, "P_bat_W": None, "P_loss_W": None})
                return out
            iL = (V_bat_V - math.sqrt(disc)) / (2 * a2)
        else:
            iL = (P_bus_W + a0) / V_bat_V
        loss = a0 + a2 * iL * iL
        if abs(iL) > self.I_L_max_A:
            out["problems"].append(f"|I_L| {abs(iL):.4g} A > {self.I_L_max_A:g} A")
        out.update({"I_L_A": iL, "P_bat_W": P_bus_W + loss, "P_loss_W": loss})
        return out


def _source_check(P_W: float, V_V: float, lim: DcSourceLimits) -> tuple[Status, str]:
    I = P_W / V_V
    rows = []
    if P_W > 0:
        rows = [("discharge power", lim.discharge_power_max_W, P_W), ("discharge current", lim.discharge_current_max_A, I)]
    elif P_W < 0:
        rows = [("charge power", lim.charge_power_max_W, -P_W), ("charge current", lim.charge_current_max_A, -I)]
    miss = [r[0] for r in rows if r[1] is None]
    bad = [f"{r[0]} {r[2]:.4g} > {r[1]:.4g}" for r in rows if r[1] is not None and not math.isinf(r[1])
           and r[2] > r[1] * (1 + 1e-9) + 1e-9]
    if bad:
        return Status.INFEASIBLE, "; ".join(bad)
    if miss:
        return Status.UNKNOWN, "undeclared limit(s) that can bind: " + ", ".join(miss)
    return Status.FEASIBLE, "within the declared source limits"


# --------------------------------------------------------------------------------------------- joint capability

@dataclass(frozen=True)
class BusMachine:
    name: str
    drive: DriveModel
    speed_rpm: float
    role: str = ""               # a name only: 'generator' does not guarantee generation


def _machine_table(m: BusMachine, Vdc: float, torques) -> list[dict]:
    ev = PolicyEvaluator(m.drive, Scenario(m.name, m.speed_rpm, Vdc, UNLIMITED))
    rows = []
    for T in torques:
        sol = ev.solve(float(T))
        pt = sol.point
        st = sol.policy_claim.status
        rows.append({"T_Nm": float(T), "status": st.value,
                     "P_dc_W": None if pt is None else pt.Pdc_W, "I_peak_A": None if pt is None else pt.i_peak_A,
                     "loss_W": None if (pt is None or pt.Pdc_W is None or pt.Pshaft_W is None) else pt.Pdc_W - pt.Pshaft_W,
                     "detail": sol.policy_claim.detail})
    return rows


def machine_range(m: BusMachine, Vdc: float) -> tuple[float | None, float | None]:
    ev = PolicyEvaluator(m.drive, Scenario(m.name, m.speed_rpm, Vdc, UNLIMITED))
    hi = policy_capability(ev, +1)
    lo = policy_capability(ev, -1)
    return (lo.value_Nm if lo.accepted else None), (hi.value_Nm if hi.accepted else None)


def joint_torque_set(machines: list, Vdc_V: float, battery: Battery, aux_W: float = 0.0, bus_loss_W: float = 0.0,
                     boost: BoostStage | None = None, n_levels: int = 21, request: tuple | None = None,
                     cooling_heat_max_W: float | None = None) -> dict:
    """Jointly feasible torque pairs of two machines on one bus (H-01, H-02).

    Each machine runs its minimum-current policy at its own speed (a fixed policy: an INFEASIBLE cell is a policy
    counterexample, not a proof over every control); the common constraints are the one source (battery, through the
    boost when declared) and an optional shared coolant heat budget.  The same source power is never allocated to
    both branches.
    """
    if len(machines) != 2:
        raise InputValidationError("the joint torque set is computed for two machines on one bus", field="machines")
    m1, m2 = machines
    rng1, rng2 = machine_range(m1, Vdc_V), machine_range(m2, Vdc_V)
    if None in rng1 or None in rng2:
        raise InputValidationError("machine capability not established at this speed / Vdc (check the model issues)",
                                   field="machines")
    t1 = np.linspace(rng1[0], rng1[1], n_levels)
    t2 = np.linspace(rng2[0], rng2[1], n_levels)
    if request is not None:
        t1 = np.unique(np.append(t1, request[0]))
        t2 = np.unique(np.append(t2, request[1]))
    tab1, tab2 = _machine_table(m1, Vdc_V, t1), _machine_table(m2, Vdc_V, t2)
    status = np.empty((t1.size, t2.size), dtype=object)
    psrc = np.full((t1.size, t2.size), np.nan)
    reason = np.empty((t1.size, t2.size), dtype=object)
    for i, r1 in enumerate(tab1):
        for j, r2 in enumerate(tab2):
            if r1["status"] == "INFEASIBLE" or r2["status"] == "INFEASIBLE":
                status[i, j], reason[i, j] = "INFEASIBLE", "component"
                continue
            if r1["status"] != "FEASIBLE" or r2["status"] != "FEASIBLE" or r1["P_dc_W"] is None or r2["P_dc_W"] is None:
                status[i, j], reason[i, j] = "UNKNOWN", "component unresolved"
                continue
            p_bus = r1["P_dc_W"] + r2["P_dc_W"] + aux_W + bus_loss_W
            probs = []
            if boost is not None:
                bs = boost.battery_side(p_bus, Vdc_V, battery.ocv_V)
                probs += bs["problems"]
                p_bat = bs["P_bat_W"]
            else:
                p_bat = p_bus
            if p_bat is None:
                status[i, j], reason[i, j] = "INFEASIBLE", "boost"
                continue
            psrc[i, j] = p_bat
            vt, _ = battery.terminal(p_bat)
            if vt is None:
                probs.append("battery cannot deliver this power (terminal voltage collapse)")
            else:
                st, why = _source_check(p_bat, vt, battery.limits)
                if st is Status.INFEASIBLE:
                    probs.append(why)
                elif st is Status.UNKNOWN:
                    status[i, j], reason[i, j] = "UNKNOWN", why
                    continue
                if boost is None and battery.uv_min_V is not None and vt < battery.uv_min_V:
                    probs.append(f"bus {vt:.4g} V < UV {battery.uv_min_V:g} V")
            if cooling_heat_max_W is not None and r1["loss_W"] is not None and r2["loss_W"] is not None:
                if r1["loss_W"] + r2["loss_W"] > cooling_heat_max_W:
                    probs.append("shared coolant heat budget exceeded")
            if probs:
                status[i, j], reason[i, j] = "INFEASIBLE", "; ".join(probs)
            else:
                status[i, j], reason[i, j] = "FEASIBLE", ""
    # conditional envelope: for every T1 the feasible T2 segments (a set, possibly disconnected)
    envelope = []
    for i in range(t1.size):
        segs, cur = [], None
        for j in range(t2.size):
            if status[i, j] == "FEASIBLE":
                cur = [float(t2[j]), float(t2[j])] if cur is None else [cur[0], float(t2[j])]
            elif cur is not None:
                segs.append(cur)
                cur = None
        if cur is not None:
            segs.append(cur)
        envelope.append({"T1_Nm": float(t1[i]), "T2_segments_Nm": segs})
    alone1 = [r["T_Nm"] for r in tab1 if r["status"] == "FEASIBLE"]
    alone2 = [r["T_Nm"] for r in tab2 if r["status"] == "FEASIBLE"]
    box = {"T1": [min(alone1), max(alone1)] if alone1 else None, "T2": [min(alone2), max(alone2)] if alone2 else None}
    box_cells = sum(1 for i in range(t1.size) for j in range(t2.size)
                    if tab1[i]["status"] == "FEASIBLE" and tab2[j]["status"] == "FEASIBLE")
    joint_cells = int(np.sum(status == "FEASIBLE"))
    out = {"machines": [{"name": m.name, "speed_rpm": m.speed_rpm, "role": m.role, "drive_id": m.drive.drive_id,
                         "range_Nm": list(r)} for m, r in ((m1, rng1), (m2, rng2))],
           "T1_Nm": t1.tolist(), "T2_Nm": t2.tolist(), "status": status.tolist(), "reason": reason.tolist(),
           "P_source_W": np.where(np.isnan(psrc), None, psrc).tolist(),
           "tables": {m1.name: tab1, m2.name: tab2}, "conditional_envelope": envelope,
           "separate_maxima_box": box, "box_cells_feasible_separately": box_cells, "joint_cells_feasible": joint_cells,
           "Vdc_V": Vdc_V, "aux_W": aux_W, "bus_loss_W": bus_loss_W, "boost": boost is not None,
           "policy": "minimum-current per machine (fixed policy); common source counted once",
           "meaning": "sampled joint set: FEASIBLE cells are witnesses; the rectangle of separate maxima is not an "
                      "available torque set"}
    if request is not None:
        i = int(np.argmin(np.abs(t1 - request[0])))
        j = int(np.argmin(np.abs(t2 - request[1])))
        r1, r2 = tab1[i], tab2[j]
        branch = [r1["P_dc_W"], r2["P_dc_W"]]
        net = None if None in branch else branch[0] + branch[1]
        circ = None if None in branch else (min(abs(branch[0]), abs(branch[1])) if branch[0] * branch[1] < 0 else 0.0)
        out["request"] = {"T1_Nm": float(t1[i]), "T2_Nm": float(t2[j]), "status": status[i, j], "reason": reason[i, j],
                          "branch_P_dc_W": branch, "branch_I_dc_A": [None if b is None else b / Vdc_V for b in branch],
                          "net_machines_W": net, "circulating_W": circ,
                          "P_source_W": None if np.isnan(psrc[i, j]) else float(psrc[i, j]),
                          "note": "branch stress (currents, losses, capacitor ripple) follows the branch powers, not "
                                  "the net"}
    return out


# --------------------------------------------------------------------------------------------- cranking replay

@dataclass(frozen=True)
class CrankLoad:
    """Declared crank-angle resisting torque (signed: compression returns energy after TDC), periodic in the crank
    angle, plus friction T_f = f0 + f1 w (+ f2 w^2), accessory torque and the reflected inertia at the crank."""

    angle_deg: tuple                 # table axis over one period
    torque_Nm: tuple                 # compression / pumping torque (+ resists forward rotation)
    period_deg: float = 180.0        # 4-cylinder four-stroke: one compression event per 180 deg
    f0_Nm: float = 0.0
    f1_Nm_s: float = 0.0
    f2_Nm_s2: float = 0.0
    aux_Nm: float = 0.0
    J_kgm2: float = 0.2
    basis: str = ""

    def __post_init__(self):
        if len(self.angle_deg) != len(self.torque_Nm) or len(self.angle_deg) < 2:
            raise InputValidationError("crank load table needs matching angle / torque rows", field="crank_load")
        if any(b <= a for a, b in zip(self.angle_deg, self.angle_deg[1:])):
            raise InputValidationError("crank angles must increase", field="crank_load.angle_deg")
        if _finite("J_kgm2", self.J_kgm2) <= 0:
            raise InputValidationError("inertia must be > 0", field="J_kgm2")
        if not self.basis.strip():
            raise InputValidationError("the crank load needs its basis (engine test trace / supplier envelope)",
                                       field="crank_load.basis")

    def comp(self, theta_deg: float) -> float:
        x = theta_deg % self.period_deg
        ang = np.asarray(self.angle_deg, dtype=float)
        tq = np.asarray(self.torque_Nm, dtype=float)
        return float(np.interp(x, ang, tq, period=self.period_deg))

    def friction(self, w: float) -> float:
        s = 1.0 if w > 0 else (-1.0 if w < 0 else 0.0)
        return s * self.f0_Nm + self.f1_Nm_s * w + s * self.f2_Nm_s2 * w * w


def _starter_tables(drive: DriveModel, ratio: float, n_crank_max: float, V_floor: float, n_speed: int = 9,
                    n_torque: int = 7) -> dict:
    """Starter capability and DC power at the UV floor on a speed grid (policy solves at grid points)."""
    speeds = np.linspace(0.0, n_crank_max * ratio * 1.1, n_speed)
    cap, grid = [], []
    for n in speeds:
        ev = PolicyEvaluator(drive, Scenario("crank", float(n), V_floor, UNLIMITED))
        c = policy_capability(ev, +1)
        tmax = c.value_Nm if c.accepted else None
        cap.append(tmax)
        row = []
        if tmax is not None:
            for T in np.linspace(0.0, tmax, n_torque):
                sol = ev.solve(float(T))
                pt = sol.point
                row.append((float(T), None if pt is None else pt.Pdc_W, None if pt is None else pt.i_peak_A))
        grid.append(row)
    return {"speeds_rpm": speeds, "Tmax_Nm": cap, "grid": grid}


def _interp_tables(tab: dict, n_em: float, T: float) -> tuple[float, float | None, float | None]:
    """Capability: the lower of the two neighbouring speed samples (screening lower envelope); P_dc and current:
    bilinear on the solved grid."""
    sp = tab["speeds_rpm"]
    n = min(max(n_em, 0.0), sp[-1])
    j = int(np.clip(np.searchsorted(sp, n, side="right") - 1, 0, sp.size - 2))
    caps = [tab["Tmax_Nm"][j], tab["Tmax_Nm"][j + 1]]
    if None in caps:
        return 0.0, None, None
    tcap = min(caps)
    Tc = min(T, tcap)
    vals = []
    for jj in (j, j + 1):
        row = tab["grid"][jj]
        ts = np.array([r[0] for r in row])
        ps = [r[1] for r in row]
        cs = [r[2] for r in row]
        if None in ps or None in cs:
            return Tc, None, None
        vals.append((float(np.interp(Tc, ts, ps)), float(np.interp(Tc, ts, cs))))
    w = (n - sp[j]) / (sp[j + 1] - sp[j])
    return Tc, (1 - w) * vals[0][0] + w * vals[1][0], (1 - w) * vals[0][1] + w * vals[1][1]


def cranking_replay(drive: DriveModel, ratio: float, load: CrankLoad, battery: Battery, T_cmd_Nm: float,
                    n_target_rpm: float, t_max_s: float, V_floor_V: float, theta0_deg=None, traction_reserve_W: float = 0.0,
                    aux_elec_W: float = 0.0, dt_s: float = 5e-4, T_comb=None) -> dict:
    """Engine cranking replay (H-03): J w' = T_EM->e + T_comb - T_comp(theta) - T_f(w) - T_aux, theta' = w.

    ``ratio``: machine speed / crank speed (fixed, declared engaged mode; a slipping clutch needs its own state).
    The starter torque is limited by its capability at the declared UV floor ``V_floor_V`` (the replay checks that
    the bus stays above it); the battery supplies the starter DC power plus ``aux_elec_W`` while holding
    ``traction_reserve_W`` for traction.  Initial crank angles are sampled (coverage evidence).
    """
    for name, v in (("ratio", ratio), ("n_target_rpm", n_target_rpm), ("t_max_s", t_max_s), ("V_floor_V", V_floor_V),
                    ("dt_s", dt_s)):
        if _finite(name, v) <= 0:
            raise InputValidationError(f"{name} must be > 0", field=name)
    thetas = list(theta0_deg) if theta0_deg is not None else [float(x) for x in
                                                            np.linspace(0.0, load.period_deg, 12, endpoint=False)]
    tab = _starter_tables(drive, ratio, n_target_rpm, V_floor_V)
    runs = []
    wt = n_target_rpm * TWO_PI / 60
    for th0 in thetas:
        th, w, t = math.radians(th0), 0.0, 0.0
        tr = {"t_s": [], "n_rpm": [], "T_em_Nm": [], "T_comp_Nm": [], "P_dc_W": [], "V_bus_V": [], "I_peak_A": []}
        reached, fail = None, []
        vmin, pmax, imax, energy = math.inf, -math.inf, 0.0, 0.0
        steps = int(math.ceil(t_max_s / dt_s))
        for _ in range(steps):
            n_em = abs(w) * 60 / TWO_PI * ratio
            Tem, pdc, ipk = _interp_tables(tab, n_em, T_cmd_Nm)
            if pdc is None:
                fail.append(f"starter point not established at {n_em:.0f} rpm")
                break
            p_bat = pdc + aux_elec_W
            v, _i = battery.terminal(p_bat + traction_reserve_W)
            if v is None:
                fail.append("battery cannot supply starter + reserve (terminal voltage collapse)")
                break
            st, why = _source_check(p_bat + traction_reserve_W, v, battery.limits)
            if st is Status.INFEASIBLE:
                fail.append(f"t = {t:.3f} s: {why} (starter {p_bat:.4g} W + reserve {traction_reserve_W:.4g} W)")
                break
            if v < V_floor_V:
                fail.append(f"t = {t:.3f} s: bus {v:.4g} V < floor {V_floor_V:g} V (the starter capability assumed "
                            f"the floor)")
                break
            tcomb = 0.0 if T_comb is None else float(T_comb(math.degrees(th), w))
            Tc = load.comp(math.degrees(th))
            acc = (Tem * ratio + tcomb - Tc - load.friction(w) - load.aux_Nm) / load.J_kgm2
            if w <= 0 and acc < 0:
                acc = 0.0                          # static: the crank does not turn backwards under this model
            w = max(w + acc * dt_s, 0.0)
            th += w * dt_s
            t += dt_s
            energy += p_bat * dt_s
            vmin, pmax, imax = min(vmin, v), max(pmax, p_bat), max(imax, ipk)
            if len(tr["t_s"]) < 4000:
                for key, val in (("t_s", t), ("n_rpm", w * 60 / TWO_PI), ("T_em_Nm", Tem), ("T_comp_Nm", Tc),
                                 ("P_dc_W", pdc), ("V_bus_V", v), ("I_peak_A", ipk)):
                    tr[key].append(val)
            if w >= wt and reached is None:
                reached = t
                break
        ok = reached is not None and not fail
        runs.append({"theta0_deg": th0, "reached_s": reached, "ok": ok, "failures": fail, "V_bus_min_V": vmin,
                     "P_bat_max_W": pmax, "I_peak_max_A": imax, "energy_J": energy, "trace": tr})
    bad = [r for r in runs if not r["ok"]]
    worst = max(runs, key=lambda r: (r["reached_s"] is None, r["reached_s"] or 0.0))
    q = f"crank to {n_target_rpm:g} rpm within {t_max_s:g} s from every sampled initial crank angle"
    scope = "cranking replay with the declared crank-angle load (not an engine-start guarantee)"
    if bad:
        b = bad[0]
        claim = Claim("cranking", Status.INFEASIBLE, q, scope, reasons=(Reason.CONSTRAINT_VIOLATION,),
                      evidence=(Evidence.make(EvidenceKind.NUMERICAL_WITNESS,
                                              f"initial angle {b['theta0_deg']:g} deg: " +
                                              ("; ".join(b["failures"]) or "target speed not reached in time")),),
                      detail=f"{len(bad)} of {len(runs)} sampled initial angles fail (a counterexample under the "
                             f"declared load and battery)")
    else:
        claim = Claim("cranking", Status.UNKNOWN, q, scope, reasons=(Reason.SAMPLED_COVERAGE,),
                      evidence=(Evidence.make(EvidenceKind.SAMPLED, f"{len(runs)} initial angles"),),
                      qualifiers=(f"met at all {len(runs)} sampled initial angles (worst {worst['reached_s']:.3f} s at "
                                  f"{worst['theta0_deg']:g} deg)",),
                      detail="sampled initial angles are coverage evidence, not a proof over the continuum; "
                             "combustion onset needs engine evidence")
    return {"claim": claim.to_dict(), "runs": runs, "worst": {k: worst[k] for k in ("theta0_deg", "reached_s", "ok")},
            "starter_capability": {"speeds_rpm": tab["speeds_rpm"].tolist(), "Tmax_Nm": tab["Tmax_Nm"],
                                   "V_floor_V": V_floor_V},
            "inputs": {"ratio": ratio, "T_cmd_Nm": T_cmd_Nm, "n_target_rpm": n_target_rpm, "t_max_s": t_max_s,
                       "traction_reserve_W": traction_reserve_W, "aux_elec_W": aux_elec_W, "J_kgm2": load.J_kgm2,
                       "period_deg": load.period_deg, "load_basis": load.basis},
            "notes": ["starter capability evaluated at the UV floor and taken as the lower of neighbouring speed samples "
                      "(screening lower envelope); DC power / current interpolated between exact policy solves",
                      "engaged fixed-ratio connection assumed; clutch slip, combustion, NVH are outside this replay",
                      "result named 'cranking requirement', not 'engine start'"]}


# --------------------------------------------------------------------------------------------- load rejection

def load_rejection(C_uF: float, V0_V: float, V_max_V: float, sources_W: list, sinks_W: list, t_react_s: float,
                   t_ramp_s: float = 0.0, horizon_s: float | None = None, n: int = 400) -> dict:
    """Energy ledger after a traction trip (H-05): generators keep feeding the bus until their reaction completes
    (detection + fuel cut / torque removal, then a linear ramp); sinks that still accept power are subtracted once;
    the one common capacitor absorbs the excess: 1/2 C (V^2 - V0^2) = integral of the excess.
    """
    C = _finite("C_uF", C_uF) * 1e-6
    for name, v in (("V0_V", V0_V), ("V_max_V", V_max_V)):
        if _finite(name, v) <= 0:
            raise InputValidationError(f"{name} must be > 0", field=name)
    if V_max_V <= V0_V:
        raise InputValidationError("V_max must exceed V0", field="V_max_V")
    if t_react_s < 0 or t_ramp_s < 0:
        raise InputValidationError("reaction / ramp times must be >= 0", field="t_react_s")
    P_ex = float(sum(sources_W)) - float(sum(sinks_W))
    E_margin = 0.5 * C * (V_max_V ** 2 - V0_V ** 2)
    horizon = horizon_s or max(2.0 * (t_react_s + t_ramp_s), 1e-4)
    t = np.linspace(0.0, horizon, n)
    gen = np.where(t <= t_react_s, 1.0, np.clip(1.0 - (t - t_react_s) / t_ramp_s, 0.0, 1.0) if t_ramp_s > 0 else 0.0)
    src = float(sum(sources_W)) * gen
    ex = src - float(sum(sinks_W))
    E = np.concatenate([[0.0], np.cumsum(0.5 * (ex[1:] + ex[:-1]) * np.diff(t))])
    V = np.sqrt(np.maximum(V0_V ** 2 + 2.0 * E / C, 0.0))
    E_total = max(P_ex, 0.0) * t_react_s + 0.5 * max(P_ex, 0.0) * t_ramp_s if P_ex > 0 else 0.0
    V_peak = math.sqrt(V0_V ** 2 + 2.0 * E_total / C) if E_total > 0 else V0_V
    t_lim = E_margin / P_ex if P_ex > 0 else math.inf
    q = f"DC bus stays below {V_max_V:g} V after the load rejection"
    if P_ex <= 0:
        st, det = Status.FEASIBLE, "the remaining sinks absorb the generated power: no excess energy"
    elif V_peak <= V_max_V:
        st, det = Status.FEASIBLE, (f"peak {V_peak:.4g} V: the excess {P_ex:.4g} W is removed after {t_react_s:g} s "
                                    f"(+{t_ramp_s:g} s ramp) before the {E_margin:.4g} J margin is used")
    else:
        st, det = Status.INFEASIBLE, (f"the {E_margin:.4g} J capacitor margin is used in {t_lim * 1e6:.4g} us at "
                                      f"{P_ex:.4g} W excess, but the generator reaction takes {t_react_s * 1e3:.4g} ms: "
                                      f"peak would be {V_peak:.4g} V")
    claim = Claim("load_rejection", st, q, "lossless energy ledger of the one common capacitor",
                  reasons=() if st is Status.FEASIBLE else (Reason.CONSTRAINT_VIOLATION,),
                  evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND, "1/2 C (V^2 - V0^2) = integral of the excess"),),
                  qualifiers=("screening: declared powers and reaction; ESR, inductance, protection clamps and "
                              "converter dynamics not modelled",), detail=det)
    return {"claim": claim.to_dict(), "P_excess_W": P_ex, "E_margin_J": E_margin, "time_to_limit_s": t_lim,
            "V_peak_V": V_peak, "trace": {"t_s": t.tolist(), "V_V": V.tolist(), "P_excess_W": ex.tolist()},
            "ledger": {"sources_W": list(sources_W), "sinks_W": list(sinks_W), "capacitor_uF": C_uF,
                       "note": "the common capacitor margin is counted once for all fault branches"}}
