"""Repeated load and hot starts (engineering review 6198099, priority 1).

A duty cycle of load phases (torque, speed, duration) is repeated from a stated initial thermal state; each thermal
node integrates its Foster terms EXACTLY between steps for the loss of the step (x_i <- x_i a + R_i P (1 - a),
a = exp(-dt / tau_i)), and the losses are re-evaluated as the temperatures change:

* winding: R_s(T_winding) through the drive's declared R_s temperature law (the scenario winding temperature is the
  winding node's temperature);
* inverter: a datasheet module model at T_j = the junction node's temperature (outside its table the loss is not
  established and the run stops - never extrapolated).

Initial states that a Foster network can represent: equilibrium at the coolant, or the steady state of a declared
preload operating point (x_i = R_i P).  Measured layer temperatures are physical node temperatures: only a Cauer
ladder has them (its modal coordinates give the Foster states exactly); a Foster network's inner states are not
layer temperatures, so such a start on a Foster node is refused.

Outputs: the first pulse from the initial state (time to the first limit), the periodic steady cycle (peak per
node, the governing node; solved as the fixed point of the exact cycle map, not by running cycles until a slow node
settles) and - with the losses held at the hotter temperature corner (coolant or node limit; losses monotone in
temperature assumed) - the allowed pulse duration and pulse torque in the periodic cycle, the allowed first-pulse
torque, the rest needed after the first pulse before the same pulse can be repeated and the shortest rest that
keeps the periodic cycle within the limits (closed form).  The coolant rise
follows each step's losses instantly (the loop's own thermal mass is not modelled: conservative for pulse peaks).
Like every thermal result here, it is a screening estimate unless the thermal model is qualified.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace

import numpy as np
from scipy.optimize import brentq

from ..errors import InputValidationError
from ..models.components import TemperatureDependence
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Reason, Status
from ..validation import finite as _finite
from .thermal import ThermalModel, _losses, _no_band_note, band_status

INITIAL_KINDS = ("equilibrium_at_coolant", "steady_state_at", "node_temperatures")


@dataclass(frozen=True)
class LoadPhase:
    torque_Nm: float
    speed_rpm: float
    duration_s: float
    name: str = ""

    def __post_init__(self):
        for k in ("torque_Nm", "speed_rpm", "duration_s"):
            object.__setattr__(self, k, _finite(k, getattr(self, k)))
        if self.duration_s <= 0:
            raise InputValidationError("each load phase needs a duration > 0 s", field="duration_s")


@dataclass(frozen=True)
class InitialState:
    kind: str = "equilibrium_at_coolant"
    torque_Nm: float | None = None               # steady_state_at: the preload operating point
    speed_rpm: float | None = None
    node_temperatures_C: tuple = ()              # node_temperatures: ((node_id, (T_1 .. T_n junction first)), ...)

    def __post_init__(self):
        if self.kind not in INITIAL_KINDS:
            raise InputValidationError(f"initial state must be one of {INITIAL_KINDS}", field="initial_state")
        if self.kind == "steady_state_at" and (self.torque_Nm is None or self.speed_rpm is None):
            raise InputValidationError("a steady-state start needs the preload torque and speed", field="initial_state")
        if self.kind == "node_temperatures" and not self.node_temperatures_C:
            raise InputValidationError("a start from node temperatures needs them per node", field="initial_state")


@dataclass(frozen=True)
class Feedback:
    """Loss-temperature coupling.  ``rs_law``: an R_s(T) law declared for this analysis when the drive has none
    (coefficient, validity, basis) with the temperature the supplied R_s refers to."""

    enabled: bool = True
    rs_law: TemperatureDependence | None = None
    rs_reference_C: float | None = None


def _feedback_nodes(model: ThermalModel) -> tuple:
    """(winding, junction, magnet) node ids.  A node's declared ``represents`` decides; without a declaration the
    winding is the node with the largest copper share and the junction the device node (else the largest inverter
    share).  The magnet temperature is never inferred from a loss share: only a declared node feeds it."""
    role = {nd.represents: nd.node_id for nd in model.nodes if nd.represents}
    cu = [(dict(nd.loss_share).get("copper", 0.0), nd.node_id) for nd in model.nodes]
    w = role.get("winding") or (max(cu)[1] if any(v > 0 for v, _ in cu) else None)
    dev = [nd.node_id for nd in model.nodes if dict(nd.loss_share).get("inverter_hottest_device", 0.0) > 0]
    inv = [(dict(nd.loss_share).get("inverter", 0.0), nd.node_id) for nd in model.nodes]
    j = role.get("junction") or (dev[0] if dev else (max(inv)[1] if any(v > 0 for v, _ in inv) else None))
    return w, j, role.get("magnet")


def _magnet_dependent(motor) -> bool:
    """The motor's flux depends on the magnet temperature: a declared psi_PM(T) law, or map planes at >= 2 magnet
    temperatures."""
    planes = getattr(motor.flux, "planes", None) or ()
    return motor.psi_temperature is not None or len({p.magnet_temp_C for p in planes
                                                     if p.magnet_temp_C is not None}) >= 2


class _Losses:
    """Operating-point losses of a phase at the current node temperatures (cached on a temperature grid)."""

    def __init__(self, drive, scenario: Scenario, fb: Feedback, settings: NumericalSettings, tol_K: float = 0.5,
                 magnet_node: str | None = None):
        self.scenario, self.settings, self.tol = scenario, settings, tol_K
        m = drive.motor
        self.notes = []
        if fb.enabled and m.rs_temperature is None and fb.rs_law is not None:
            if fb.rs_reference_C is None:
                raise InputValidationError("a declared R_s(T) law needs the temperature the supplied R_s refers to",
                                           field="feedback.rs_reference_C")
            drive = replace(drive, motor=replace(m, rs_temperature=fb.rs_law,
                                                 reference_winding_temp_C=float(fb.rs_reference_C)))
            self.notes.append(f"R_s(T) law declared for this analysis: {fb.rs_law.coeff_per_K:g} /K from "
                              f"{fb.rs_reference_C:g} degC, valid {list(fb.rs_law.valid_C)} ({fb.rs_law.basis})")
        self.drive = drive
        self.rs = fb.enabled and drive.motor.rs_temperature is not None and \
            drive.motor.reference_winding_temp_C is not None
        self.module = fb.enabled and drive.inverter.module_loss is not None
        dep = _magnet_dependent(drive.motor)
        self.mag = fb.enabled and dep and magnet_node is not None
        if fb.enabled and not self.rs:
            self.notes.append("no R_s temperature law: the copper loss uses R_s as supplied (no winding feedback)")
        if fb.enabled and not self.module:
            self.notes.append("the inverter loss model has no junction-temperature input (no T_j feedback)")
        if fb.enabled and dep and magnet_node is None:
            self.notes.append("the motor flux depends on the magnet temperature but no thermal node is declared as the "
                              "magnet temperature: the flux stays at the scenario's magnet temperature")
        if fb.enabled and magnet_node is not None and not dep:
            self.notes.append("a magnet node is declared but the motor has no magnet-temperature dependence (psi_PM(T) "
                              "law or map planes): the magnet node is not fed back")
        if not fb.enabled:
            self.notes.append("loss-temperature feedback off: losses at the stated scenario temperatures")
        self.active = self.rs or self.module or self.mag
        self.cache = {}

    def at(self, ph: LoadPhase, T_w: float | None, T_j: float | None, T_m: float | None = None) -> dict:
        tw = None if (not self.rs or T_w is None) else round(T_w / self.tol) * self.tol
        tj = None if (not self.module or T_j is None) else round(T_j / self.tol) * self.tol
        tm = None if (not self.mag or T_m is None) else round(T_m / self.tol) * self.tol
        key = (ph.torque_Nm, ph.speed_rpm, tw, tj, tm)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        sc = self.scenario.with_(speed_rpm=ph.speed_rpm)
        if tw is not None:
            sc = sc.with_(winding_temp_C=tw)
        if tm is not None:
            sc = sc.with_(magnet_temp_C=tm)
        drv = self.drive if tj is None else replace(self.drive, inverter=replace(self.drive.inverter, module_Tj_C=tj))
        sol = PolicyEvaluator(drv, sc, self.settings).solve(ph.torque_Nm)
        ok = sol.point is not None and sol.policy_claim.status is Status.FEASIBLE
        det = (sol.point.inverter_loss_detail or {}) if sol.point is not None else {}
        if ok and self.module and not det.get("established"):
            ok = False
        out = {"ok": ok, "losses": _losses(sol.point) if ok else None,
               "reason": "" if ok else (sol.policy_claim.detail or "; ".join(det.get("problems", [])) or
                                       sol.policy_claim.status.value),
               "status": sol.policy_claim.status.value, "T_w": tw, "T_j": tj, "T_m": tm}
        self.cache[key] = out
        return out


def _node_arrays(model: ThermalModel):
    R = [np.asarray(nd.network.R_K_per_W, float) for nd in model.nodes]
    tau = [np.asarray(nd.network.tau_s, float) for nd in model.nodes]
    return R, tau


def _initial_states(model: ThermalModel, init: InitialState, losses_pre: dict | None, refs: list) -> list:
    R, _tau = _node_arrays(model)
    if init.kind == "equilibrium_at_coolant":
        return [np.zeros_like(r) for r in R]
    if init.kind == "steady_state_at":
        out = []
        for nd, r in zip(model.nodes, R):
            p = nd.power(losses_pre)
            if math.isnan(p):
                raise InputValidationError(f"node {nd.node_id!r}: a heat source of the preload point is not available",
                                           field="initial_state")
            out.append(r * p)
        return out
    given = dict((str(k), tuple(v)) for k, v in init.node_temperatures_C)
    out = []
    for nd, ref, r in zip(model.nodes, refs, R):
        if nd.node_id not in given:
            raise InputValidationError(f"node temperatures missing for {nd.node_id!r}", field="initial_state")
        if nd.cauer is None:
            raise InputValidationError(
                f"node {nd.node_id!r} is a Foster network: its inner states are not physical layer temperatures - "
                f"enter the network as Cauer (layer R, C) to start from measured node temperatures, or start from "
                f"the steady state of a preload point", field="initial_state")
        rise = np.asarray(given[nd.node_id], float) - ref
        out.append(nd.cauer.foster_state(rise))
    return out


PERIODIC_TOL_K = 0.02          # largest change of any Foster term over one cycle at the periodic state


def _state_residual(xa, xb) -> float:
    """Largest |change| of any Foster term between two node-state lists.  The node temperature is a SUM of terms:
    a fast term still rising and a slow one still falling can cancel in it, so the periodic cycle is judged on every
    term, not on the node temperatures (review of 63a2b61, 3.2)."""
    return max((float(np.max(np.abs(np.asarray(a) - np.asarray(b)))) for a, b in zip(xa, xb)), default=0.0)


def _advance(x, r, tau, P, dt):
    a = np.exp(-dt / tau)
    return x * a + r * P * (1.0 - a)


def _expsum_zeros(c, lam, d: float) -> list:
    """Every zero of h(t) = sum_k c_k exp(-lam_k t) (lam_k > 0) in the open interval (0, d), by Rolle isolation.

    With the rates sorted, H(t) = exp(lam_1 t) h(t) = c_1 + sum_k c_k exp(-(lam_k - lam_1) t) has the zeros of h,
    and H' is an exponential sum with one term less: between two consecutive zeros of H' (found the same way) H is
    monotone, so it has a zero there exactly when its values at the two ends differ in sign.  The recursion ends at
    one term (no isolated zero).  Descartes' rule for exponential sums (at most as many zeros as sign changes of
    the coefficients in rate order) ends it earlier: no sign change, no zero.  No sampling grid - a zero cannot fall
    between samples (review of 63a2b61, 2.3).  A zero of even multiplicity, where h touches 0 without changing sign,
    may be skipped: it is not an extremum of the integral of h."""
    c, lam = np.asarray(c, float), np.asarray(lam, float)
    order = np.argsort(lam, kind="stable")
    cs, ls = [], []
    for ci, li in zip(c[order], lam[order]):            # equal rates are one term
        if ls and li == ls[-1]:
            cs[-1] += ci
        else:
            cs.append(float(ci))
            ls.append(float(li))
    keep = [k for k, ci in enumerate(cs) if ci != 0.0]
    cs, ls = np.array([cs[k] for k in keep]), np.array([ls[k] for k in keep])
    if len(cs) <= 1 or not d > 0:
        return []
    sign = np.sign(cs)
    if not np.any(sign[1:] != sign[:-1]):
        return []
    mu = ls[1:] - ls[0]

    def H(t):
        return cs[0] + float(np.sum(cs[1:] * np.exp(-mu * t)))
    pts = [0.0] + _expsum_zeros(-mu * cs[1:], mu, d) + [float(d)]
    out = []
    for a, b in zip(pts[:-1], pts[1:]):
        ha, hb = H(a), H(b)
        if ha == 0.0 and a > 0.0:
            out.append(a)
        elif ha * hb < 0.0:
            out.append(float(brentq(H, a, b, xtol=1e-15 * b, rtol=8.9e-16, maxiter=500)))
    return sorted(set(out))


def _stationary_times(x, r, tau, P, d: float) -> list:
    """Times in (0, d) where sum_i x_i(t) has a zero derivative on the exact trajectory
    x_i(t) = R_i P + (x_i - R_i P) exp(-t / tau_i): the zeros of sum_i -(x_i - R_i P) / tau_i exp(-t / tau_i)."""
    x, r, tau = (np.asarray(v, float) for v in (x, r, tau))
    e = x - r * P
    return _expsum_zeros(-e / tau, 1.0 / tau, d)


def _phase_max(x, r, tau, P, d, with_time: bool = False):
    """max over t in [0, d] of sum_i x_i(t) on the exact trajectory x_i(t) = c_i + (x_i - c_i) exp(-t / tau_i),
    c_i = R_i P.  When every term moves the same way the extremes are the ends; when terms move in opposite
    directions (a fast term heating while a slower one cools, e.g. after a hot soak or from measured node
    temperatures) the peak can lie inside the phase.  The candidates are the two ends and EVERY stationary point,
    isolated exactly (``_expsum_zeros``), so the maximum is exact to rounding however fast a thermal mode is against
    the phase length.  ``with_time``: (max, the time it is reached)."""
    x, r, tau = (np.asarray(v, float) for v in (x, r, tau))
    e = x - r * P

    def f(t):
        return float(np.sum(r * P + e * np.exp(-t / tau)))
    cands = [0.0] + (_stationary_times(x, r, tau, P, d) if d > 0 else []) + ([float(d)] if d > 0 else [])
    vals = [f(t) for t in cands]
    k = int(np.argmax(vals))
    return (vals[k], cands[k]) if with_time else vals[k]


def _crossing(x, r, tau, P, ref, limit, dt) -> float | None:
    """First time in [0, dt] at which ref + sum x(t) reaches the limit for a constant P (None when not reached).
    Between consecutive stationary points (isolated exactly) the trajectory is monotone, so the first piece whose
    end reaches the limit holds the first crossing, and only one: it is solved on that piece.  0.0 when the step
    starts at or above the limit."""
    x, r, tau = (np.asarray(v, float) for v in (x, r, tau))
    e = x - r * P

    def g(t):
        return ref + float(np.sum(r * P + e * np.exp(-t / tau))) - limit
    if g(0.0) >= 0.0:
        return 0.0
    pts = [0.0] + _stationary_times(x, r, tau, P, dt) + [float(dt)]
    for a, b in zip(pts[:-1], pts[1:]):
        if g(b) >= 0.0:
            return float(brentq(g, a, b, xtol=1e-15 * b, rtol=8.9e-16, maxiter=500))
    return None


def repeated_load(drive, scenario: Scenario, model: ThermalModel, phases: list, cycles: int = 20,
                  initial: InitialState | None = None, feedback: Feedback | None = None, steps_per_phase: int = 24,
                  settings: NumericalSettings = DEFAULT_SETTINGS,
                  allowed: bool = True, cache_K: float = 0.5, resolution_check: bool = True) -> dict:
    """The duty cycle ``phases`` repeated ``cycles`` times (or until the periodic cycle is reached) from ``initial``.
    ``phases[0]`` is the pulse (the allowed duration / torque refer to it).  ``resolution_check``: the run is repeated
    with twice the steps per phase and half the loss-cache grid; the changes are reported and a verdict that changes
    with the resolution is not a verdict (review of 63a2b61, 3.2)."""
    if scenario.coolant_temp_C is None:
        raise InputValidationError("coolant temperature must be stated for a thermal evaluation", field="coolant_temp_C")
    if not phases:
        raise InputValidationError("a duty cycle needs at least one load phase", field="phases")
    cycles = int(cycles)
    if not 1 <= cycles <= 500:
        raise InputValidationError("cycles must be 1..500", field="cycles")
    init = initial or InitialState()
    fb = feedback or Feedback()
    if not (_finite("cache_K", cache_K) > 0):
        raise InputValidationError("the loss-cache grid must be > 0 K", field="cache_K")
    w_id, j_id, m_id = _feedback_nodes(model)
    L = _Losses(drive, scenario, fb, settings, cache_K, magnet_node=m_id)
    ids = [nd.node_id for nd in model.nodes]
    iw = ids.index(w_id) if w_id in ids else None
    ij = ids.index(j_id) if j_id in ids else None
    im = ids.index(m_id) if m_id in ids else None
    R, tau = _node_arrays(model)
    inlet = float(scenario.coolant_temp_C)

    def fbT(temps) -> tuple:
        """(winding, junction, magnet) temperatures of the feedback nodes."""
        return tuple(None if i is None else temps[i] for i in (iw, ij, im))
    t_start = (None, None, inlet if im is not None else None)        # before any node temperature is known

    def refs_for(losses):
        fluid = model.coolant.fluid_temperatures(inlet, losses) if model.coolant is not None else None
        return [model.reference_C(nd, inlet, losses, fluid) for nd in model.nodes]

    # initial state
    pre = None
    if init.kind == "steady_state_at":
        pre_ph = LoadPhase(float(init.torque_Nm), float(init.speed_rpm), 1.0, "preload")
        ev = L.at(pre_ph, *t_start)
        if not ev["ok"]:
            raise InputValidationError(f"the preload point is not statically established: {ev['reason']}",
                                       field="initial_state")
        pre = ev["losses"]
        # the preload's own steady temperatures set its R_s(T) / T_j: iterate the fixed point
        for _ in range(30):
            refs0 = refs_for(pre)
            x0 = _initial_states(model, init, pre, refs0)
            temps = [ref + float(np.sum(x)) for ref, x in zip(refs0, x0)]
            ev = L.at(pre_ph, *fbT(temps))
            if not ev["ok"]:
                raise InputValidationError(f"the preload point is not established at its own steady temperature: "
                                           f"{ev['reason']}", field="initial_state")
            if ev["losses"] == pre:
                break
            pre = ev["losses"]
    first = L.at(phases[0], *t_start)
    refs = refs_for(pre if pre is not None else (first["losses"] or {}))
    x = _initial_states(model, init, pre, refs)
    temps0 = [ref + float(np.sum(xx)) for ref, xx in zip(refs, x)]

    # time stepping: one cycle from given Foster states, losses re-evaluated per step
    bounds, losses_seen, losses_max = [], [], {}
    tr_t, tr_T = [0.0], [list(temps0)]

    def run_cycle(xs, temps, c, t, record):
        xs = [xx.copy() for xx in xs]
        peak, fl, refs = list(temps), None, None
        for k, ph in enumerate(phases):
            dt = ph.duration_s / steps_per_phase
            if record:
                bounds.append((t, t + ph.duration_s, k, c))
            for s_ in range(steps_per_phase):
                ev = L.at(ph, *fbT(temps))
                if not ev["ok"]:
                    return xs, temps, peak, fl, {"t_s": t, "cycle": c + 1, "phase": k, "reason": ev["reason"],
                                                 "status": ev["status"], "T_w": ev["T_w"], "T_j": ev["T_j"],
                                                 "T_m": ev["T_m"]}, t, refs
                los = ev["losses"]
                if L.active:
                    # predictor-corrector (Heun): the losses at the start of the step lag the heating, so the first
                    # limit came late (engineering review 2 of 63a2b61, F-11) - predict the end-of-step temperatures
                    # with the start losses, evaluate the losses there and advance with the mean of both
                    Pp = [nd.power(los) for nd in model.nodes]
                    if not any(math.isnan(P) for P in Pp):
                        rp = refs_for(los)
                        tp = [ref + float(np.sum(_advance(xs[i], R[i], tau[i], Pp[i], dt))) for i, ref in enumerate(rp)]
                        ev2 = L.at(ph, *fbT(tp))
                        if ev2["ok"]:
                            los = {kk: 0.5 * (vv + ev2["losses"].get(kk, vv)) for kk, vv in los.items()}
                if record:
                    for kk, vv in los.items():
                        if isinstance(vv, (int, float)) and not math.isnan(vv):
                            losses_max[kk] = max(losses_max.get(kk, 0.0), float(vv))
                if record and c == 0 and s_ == 0:
                    losses_seen.append({"phase": k, **los})
                refs = refs_for(los)
                Ps = [nd.power(los) for nd in model.nodes]
                bad = [nd.node_id for nd, P, rf in zip(model.nodes, Ps, refs) if math.isnan(P) or math.isnan(rf)]
                if bad:
                    return xs, temps, peak, fl, {"t_s": t, "cycle": c + 1, "phase": k, "status": "UNKNOWN",
                                                 "reason": f"heat source of node {bad[0]!r} not available"}, t, refs
                if fl is None:
                    # every node's first crossing inside this step, then the earliest: the node order of the model
                    # never decides which limit is reached first (review of 63a2b61, 2.2)
                    hits = [(tc, nd.node_id) for i, nd in enumerate(model.nodes)
                            if (tc := _crossing(xs[i], R[i], tau[i], Ps[i], refs[i], nd.limit_C, dt)) is not None]
                    if hits:
                        t0, node = min(hits)
                        fl = {"t_s": t + t0, "cycle": c + 1, "phase": k, "node": node,
                              "nodes_at_limit": sorted(n for tc, n in hits if tc <= t0 + 1e-12 * dt)}
                for i, nd in enumerate(model.nodes):
                    peak[i] = max(peak[i], refs[i] + _phase_max(xs[i], R[i], tau[i], Ps[i], dt))
                    xs[i] = _advance(xs[i], R[i], tau[i], Ps[i], dt)
                t += dt
                temps = [ref + float(np.sum(xx)) for ref, xx in zip(refs, xs)]
                if record:
                    tr_t.append(t)
                    tr_T.append(list(temps))
        return xs, temps, peak, fl, None, t, refs

    # periodic steady cycle, computed FIRST from the initial state: per Foster term the cycle map is affine,
    # x_end = A x_start + b with A = exp(-T/tau), so x* = (x_end - A x_start) / (1 - A); iterate with the losses
    # re-evaluated (feedback) until it holds.  The physical run below then covers the REQUESTED horizon: it may only
    # stop early once it has reached this state (the remaining cycles repeat the periodic cycle), never on a small
    # change per cycle - a slow node can still creep many kelvin (engineering review 2 of 63a2b61, F-06)
    T_cyc = sum(ph.duration_s for ph in phases)
    A = [np.exp(-T_cyc / tt) for tt in tau]
    periodic, x_star = None, None
    xs, ts_ = [xx.copy() for xx in x], list(temps0)
    for it in range(1, 41):
        x_end, t_end, peak, _fl, stp, _t, refs_end = run_cycle(xs, ts_, 0, 0.0, False)
        if stp:
            periodic = {"reached": False, "stopped": stp, "iterations": it}
            break
        res = _state_residual(x_end, xs)
        if it > 1 and res < PERIODIC_TOL_K:
            periodic = {"reached": True, "iterations": it, "peak_C": dict(zip(ids, peak)),
                        "start_C": dict(zip(ids, ts_)), "end_C": dict(zip(ids, t_end)),
                        "state_residual_K": res,
                        "tolerance_K": PERIODIC_TOL_K * max(len(tt) for tt in tau)}
            x_star = xs
            break
        xs = [(xe - a * x0) / (1.0 - a) for xe, a, x0 in zip(x_end, A, xs)]
        ts_ = [ref + float(np.sum(xx)) for ref, xx in zip(refs_end, xs)]
    else:
        periodic = {"reached": False, "iterations": 40, "peak_C": dict(zip(ids, peak)),
                    "note": "the periodic fixed point did not settle in 40 iterations (strong feedback)"}

    per_cycle, first_limit, stop, repeats = [], None, None, 0
    t, temps = 0.0, list(temps0)
    for c in range(cycles):
        start_T = list(temps)
        x_new, temps, peak, fl, stop, t, _refs = run_cycle(x, temps, c, t, True)
        if fl is not None and first_limit is None:
            first_limit = fl
        if stop:
            break
        per_cycle.append({"cycle": c + 1, "peak_C": dict(zip(ids, peak)), "start_C": dict(zip(ids, start_T)),
                          "end_C": dict(zip(ids, temps))})
        x = x_new
        if x_star is not None and c + 1 < cycles and _state_residual(x, x_star) < PERIODIC_TOL_K:
            repeats = cycles - (c + 1)      # the rest of the horizon repeats the periodic cycle (within the tolerance)
            break
    limits = {nd.node_id: nd.limit_C for nd in model.nodes}
    if periodic is not None and periodic.get("peak_C"):
        margins = {n: limits[n] - periodic["peak_C"][n] for n in ids}
        periodic.update(margin_K=margins, governing_node=min(margins, key=margins.get),
                        exceeds=any(m < 0 for m in margins.values()))

    # closed form, losses bounded over the node-temperature box (see _allowed); the start state of a trial pulse is
    # referred to that pulse's own fluid reference (a start from node temperatures means those temperatures at
    # t = 0+, whatever the trial torque)
    allowed_out = _allowed(L, model, phases, init, pre, (iw, ij, im), refs_for,
                           x_init_for=lambda refs: _initial_states(model, init, pre, refs), inlet=inlet) \
        if (allowed and stop is None) else None

    # the same run at twice the steps and half the loss-cache grid: the losses are held per step (explicit in the
    # feedback), so the step and the grid are numerical choices whose effect is measured, not assumed small
    resolution = None
    if resolution_check and stop is None:
        fine = repeated_load(drive, scenario, model, phases, cycles, initial, feedback, 2 * steps_per_phase, settings,
                             allowed=allowed, cache_K=cache_K / 2.0, resolution_check=False)
        resolution = _resolution_compare(steps_per_phase, cache_K, first_limit, periodic, per_cycle, fine, ids,
                                         allowed_out)

    # qualification per phase (every phase's speed and torque against the validity domain) and with the heat sources
    # of EVERY phase and step (the largest value of each loss seen) - a source that heats no node in a later phase
    # is not dropped on the floor (engineering review 2 of 63a2b61, F-08)
    stated = {"coolant_temp_C": scenario.coolant_temp_C, "Vdc_V": scenario.Vdc_V,
              "switching_frequency_Hz": scenario.switching_frequency_Hz, **model.stated_conditions()}
    union = dict(losses_max) if losses_max else (losses_seen[0] if losses_seen else None)
    problems = []
    for ph in phases:
        for p_ in model.qualification({**stated, "speed_rpm": ph.speed_rpm, "torque_Nm": ph.torque_Nm},
                                      "equilibrium_at_coolant", union):
            if "initial" not in p_ and p_ not in problems:     # the start is modelled here (INITIAL_KINDS)
                problems.append(p_)
    q = f"{len(phases)}-phase duty cycle x {cycles} (pulse {phases[0].torque_Nm:g} N*m for " \
        f"{phases[0].duration_s:g} s at {phases[0].speed_rpm:g} rpm), start {init.kind}"
    horizon = f"{cycles} x {T_cyc:g} s duty cycle"
    exceeded = first_limit is not None
    per_note = "" if not (periodic and periodic.get("margin_K")) else (
        f"; periodic cycle: governing node {periodic['governing_node']!r}, margin "
        f"{periodic['margin_K'][periodic['governing_node']]:.3g} K")
    ran = (f"{len(per_cycle)} simulated cycle(s)" + (f" + {repeats} repeating the periodic cycle (the state reached it "
                                                     f"within {PERIODIC_TOL_K:g} K per Foster term)" if repeats else ""))
    # the requested horizon's smallest margin (limit - highest temperature reached); repeated periodic cycles peak at
    # the periodic peak
    hi_T = {n: max(pc["peak_C"][n] for pc in per_cycle) for n in ids} if per_cycle else {}
    if repeats and periodic and periodic.get("peak_C"):
        hi_T = {n: max(hi_T[n], periodic["peak_C"][n]) for n in ids}
    margin_h = min(limits[n] - hi_T[n] for n in ids) if hi_T else None
    periodic_q = ()
    if periodic and periodic.get("exceeds") and not exceeded:
        g = periodic["governing_node"]
        periodic_q = (f"the periodic cycle (the duty cycle repeated indefinitely) exceeds the {g!r} limit by "
                      f"{-periodic['margin_K'][g]:.2g} K: within the limits for the stated {cycles} cycle(s) only",)
    if stop is not None:
        # a stop is a static fact only at t = 0 from a stated start (equilibrium at the coolant, measured node
        # temperatures) or with a qualified model; later, the temperature that makes the point fail is the
        # unqualified model's estimate - never a proven violation (engineering review 2 of 63a2b61, F-07)
        infeasible = stop["status"] == "INFEASIBLE"
        decisive = infeasible and (not problems or (stop["t_s"] == 0.0 and init.kind != "steady_state_at"))
        temps_at = ", ".join(f"{k} {v:.4g} degC" for k, v in (("winding", stop.get("T_w")), ("junction", stop.get("T_j")),
                                                              ("magnet", stop.get("T_m"))) if v is not None)
        where = f"stopped at {stop['t_s']:.4g} s (cycle {stop['cycle']}, phase {stop['phase'] + 1})" + (
            f" at the estimated {temps_at}" if temps_at else "")
        if decisive:
            claim = Claim("repeated_load", Status.INFEASIBLE, q, "static policy / loss not established during the "
                          "cycle", time_horizon=horizon, reasons=(Reason.CONSTRAINT_VIOLATION,),
                          detail=f"{where}: {stop['reason']}")
        elif infeasible:
            claim = Claim("repeated_load", Status.UNKNOWN, q, "thermal screening estimate", time_horizon=horizon,
                          reasons=(Reason.UNVALIDATED_DURATION,),
                          qualifiers=(f"the static point fails at a temperature estimated by an unqualified thermal "
                                      f"model: not a proven violation ({'; '.join(problems)})",),
                          detail=f"{where}: {stop['reason']}")
        else:
            claim = Claim("repeated_load", Status.UNKNOWN, q, "static policy / loss not established during the cycle",
                          time_horizon=horizon, reasons=(Reason.MISSING_INPUT,), detail=f"{where}: {stop['reason']}")
    elif problems:
        claim = Claim("repeated_load", Status.UNKNOWN, q, "thermal screening estimate", time_horizon=horizon,
                      reasons=(Reason.UNVALIDATED_DURATION,),
                      qualifiers=(f"screening estimate: {'a node limit is reached' if exceeded else 'within limits'} "
                                  f"in the {cycles} cycle(s){per_note}",) + periodic_q,
                      detail="; ".join(problems) + ": the estimate is not a duty-cycle rating")
    elif resolution is not None and not resolution["stable"]:
        claim = Claim("repeated_load", Status.UNKNOWN, q, "qualified thermal model at matching conditions",
                      time_horizon=horizon, reasons=(Reason.NUMERICAL_UNRESOLVED,),
                      detail=f"the verdict changes with the time step / loss-cache grid: {resolution['note']}")
    else:
        st, why, band_note = band_status(margin_h, model)
        claim = Claim("repeated_load", st, q, "qualified thermal model at matching conditions", time_horizon=horizon,
                      reasons=why, qualifiers=periodic_q + ((band_note,) if band_note else ())
                      + _no_band_note(model, margin_h),
                      detail=f"{ran}{per_note}")
    step = max(1, len(tr_t) // 1500)
    return {"claim": claim.to_dict(), "cycles_run": len(per_cycle), "cycles_requested": cycles,
            "cycles_repeating_periodic": repeats, "horizon_margin_K": margin_h,
            "first_limit": first_limit, "stopped": stop,
            "initial": {"kind": init.kind, "temperatures_C": dict(zip(ids, temps0)),
                        "preload": None if init.kind != "steady_state_at" else
                        {"torque_Nm": init.torque_Nm, "speed_rpm": init.speed_rpm, "losses_W": pre}},
            "periodic": periodic,
            "per_cycle": per_cycle, "allowed": allowed_out,
            "feedback": {"winding_node": w_id, "junction_node": j_id, "magnet_node": m_id, "rs": L.rs,
                         "module": L.module, "magnet": L.mag, "notes": L.notes, "evaluations": len(L.cache)},
            "resolution_check": resolution,
            "limits_C": limits, "qualification_problems": problems,
            "trace": {"t_s": tr_t[::step], "nodes": {n: [row[i] for row in tr_T][::step] for i, n in enumerate(ids)},
                      "phase_bounds": bounds},
            "assumptions": ["exact exponential update of every Foster term per step (constant loss within a step, "
                            f"{steps_per_phase} steps per phase; losses re-evaluated per step at the node "
                            f"temperatures - with feedback the mean of the start- and end-of-step losses "
                            f"(predictor-corrector) - cached on a {cache_K:g} K grid); the peak and the first limit crossing "
                            "inside every step and phase are exact at every stationary point (exponential-sum root "
                            "isolation, no sampling grid)",
                            "coolant rise follows each step's losses instantly (loop thermal mass not modelled; "
                            "conservative for pulse peaks)",
                            "allowed duration / torque: closed form with every loss at its largest value over the "
                            "corners of the node-temperature box [coolant inlet, node limit] - a bound when each loss "
                            "is monotone in each fed-back temperature (either direction), checked at interior samples "
                            "(the loss bound check)"]}


ALLOWED_VALUES = {"pulse_duration_s": "the allowed pulse duration", "pulse_torque_Nm": "the allowed pulse torque",
                  "first_pulse_torque_Nm": "the allowed first-pulse torque",
                  "rest_before_repeat_s": "the rest before repeating", "periodic_min_rest_s": "the minimum periodic rest"}


def _allowed_change(a: dict | None, b: dict | None) -> tuple[dict, list]:
    """Allowed values at the two resolutions -> ({key: relative change}, [keys that exist - finite - at one
    resolution only]).  The allowed values are closed form in time, so only the loss-cache grid can move them."""
    if not a or not b:
        return {}, []
    changes, flips = {}, []
    for k in ALLOWED_VALUES:
        x, y = a.get(k), b.get(k)
        fx = isinstance(x, (int, float)) and math.isfinite(x)
        fy = isinstance(y, (int, float)) and math.isfinite(y)
        if fx and fy:
            changes[k] = abs(x - y) / max(abs(x), abs(y), 1e-12)
        elif fx != fy or (x is None) != (y is None):
            flips.append(k)
    return changes, flips


def _resolution_compare(steps: int, cache_K: float, first_limit, periodic, per_cycle, fine: dict, ids: list,
                        allowed_out: dict | None = None) -> dict:
    """The run against the same run at 2x the steps and half the loss-cache grid: changes of the first limit time,
    the simulated and the periodic peaks and the allowed values; ``stable`` when no decision changes (a first limit
    reached or not, a periodic margin's sign, an allowed value existing at one resolution only, the run
    stopping)."""
    out = {"steps_per_phase": [steps, 2 * steps], "cache_K": [cache_K, cache_K / 2.0]}
    fl_f = fine.get("first_limit")
    out["first_limit_s"] = [None if first_limit is None else first_limit["t_s"], None if fl_f is None else fl_f["t_s"]]
    same_fl = (first_limit is None) == (fl_f is None) and (first_limit is None or first_limit["node"] == fl_f["node"])
    out["first_limit_change_s"] = (abs(first_limit["t_s"] - fl_f["t_s"]) if (first_limit and fl_f) else None)
    pk = [abs(a["peak_C"][n] - b["peak_C"][n]) for a, b in zip(per_cycle, fine.get("per_cycle") or []) for n in ids]
    out["cycle_peak_change_K"] = max(pk, default=None)
    pf = fine.get("periodic") or {}
    same_per = True
    if periodic and periodic.get("peak_C") and pf.get("peak_C"):
        out["periodic_peak_change_K"] = max(abs(periodic["peak_C"][n] - pf["peak_C"][n]) for n in ids)
        same_per = all((periodic["margin_K"][n] < 0) == (pf["margin_K"][n] < 0) for n in ids)
    else:
        out["periodic_peak_change_K"] = None
        same_per = bool(periodic and periodic.get("peak_C")) == bool(pf.get("peak_C"))
    stopped = fine.get("stopped") is not None
    rel, flips = _allowed_change(allowed_out, fine.get("allowed"))
    out["allowed_change_rel"] = rel
    out["allowed_change_max_rel"] = max(rel.values(), default=None)
    out["stable"] = same_fl and same_per and not stopped and not flips
    parts = []
    if not same_fl:
        parts.append("the first limit " + ("is reached only at one resolution" if (first_limit is None) != (fl_f is None)
                                           else "is reached at another node"))
    if not same_per:
        parts.append("a periodic margin changes sign")
    if stopped:
        parts.append(f"the finer run stops ({fine['stopped']['reason']})")
    for k in flips:
        parts.append(f"{ALLOWED_VALUES[k]} is bounded at one resolution only")
    out["note"] = "; ".join(parts) or (
        "no decision changes; changes: first limit "
        + ("-" if out["first_limit_change_s"] is None else f"{out['first_limit_change_s']:.3g} s")
        + ", peaks " + ("-" if out["cycle_peak_change_K"] is None else f"{out['cycle_peak_change_K']:.3g} K")
        + ", periodic peaks " + ("-" if out["periodic_peak_change_K"] is None else f"{out['periodic_peak_change_K']:.3g} K")
        + ("" if out["allowed_change_max_rel"] is None else
           f", allowed values {100 * out['allowed_change_max_rel']:.3g} %"))
    return out


def _periodic_ends(Rs, taus, Ps, durs, peaks: bool = False):
    """Periodic steady state of one node (Foster terms) for piecewise-constant powers: the rise at the end of every
    phase (and with ``peaks`` the highest rise inside every phase).
    x0 = sum_j R P_j (1 - a_j) prod_{k > j} a_k / (1 - prod a)."""
    a = [np.exp(-d / taus) for d in durs]
    A = np.prod(a, axis=0)
    x0 = np.zeros_like(Rs)
    for j in range(len(durs)):
        tail = np.prod(a[j + 1:], axis=0) if j + 1 < len(durs) else np.ones_like(Rs)
        x0 = x0 + Rs * Ps[j] * (1.0 - a[j]) * tail
    x0 = x0 / (1.0 - A)
    out, mx, x = [], [], x0
    for j in range(len(durs)):
        if peaks:
            mx.append(_phase_max(x, Rs, taus, Ps[j], durs[j]))
        x = x * a[j] + Rs * Ps[j] * (1.0 - a[j])
        out.append(float(np.sum(x)))
    return (out, mx) if peaks else out


def _allowed(L: _Losses, model: ThermalModel, phases: list, init: InitialState, pre, fb_idx: tuple, refs_for,
             x_init_for, inlet: float) -> dict:
    """Allowed pulse duration / torque in the periodic cycle and allowed first-pulse torque, closed form with every
    loss at its largest value over the corners of the box of the fed-back node temperatures (winding, junction,
    magnet: each between the coolant inlet - no node is colder with non-negative heat - and its limit).

    A corner is the maximum of a loss that is monotone in each temperature, in EITHER direction (a module's
    on-state voltage can fall with T_j at low current; a weaker magnet can lower the field-weakening current): no
    'losses grow with temperature' assumption.  Whether each loss is monotone is checked at interior samples of the
    declared phases (each edge midpoint and the centre); a sample above its corners means the corner values are not
    a bound and the allowed values are labelled estimates (review of 63a2b61, 3.2)."""
    ids = [nd.node_id for nd in model.nodes]
    lim = [nd.limit_C for nd in model.nodes]
    roles = ("winding", "junction", "magnet")
    active = (L.rs, L.module, L.mag)
    dims = [(role, i, min(inlet, lim[i]), max(inlet, lim[i])) for role, i, on in zip(roles, fb_idx, active)
            if i is not None and on]

    def temps_at(choice) -> tuple:
        T = {role: lo + s * (hi - lo) for (role, _i, lo, hi), s in zip(dims, choice)}
        return tuple(T.get(role) for role in roles)
    corners = list(itertools.product((0.0, 1.0), repeat=len(dims)))

    def bound(ph):
        best = None
        for ch in corners:
            ev = L.at(ph, *temps_at(ch))
            if not ev["ok"]:
                return None
            best = ev["losses"] if best is None else {k: max(best.get(k, 0.0), v) for k, v in ev["losses"].items()}
        return best

    def powers(ph):
        los = bound(ph)
        if los is None:
            return None, None
        return [nd.power(los) for nd in model.nodes], refs_for(los)

    base = [powers(ph) for ph in phases]
    if any(p is None for p, _r in base):
        return {"status": "UNKNOWN", "reason": "a phase is not established at the temperature corners"}
    # sampled monotonicity check of the corner bound on the declared phases
    samples = [tuple(0.5 if k == d else s for k, s in enumerate(c)) for d in range(len(dims))
               for c in itertools.product((0.0, 1.0), repeat=len(dims))]
    samples = sorted(set(samples)) + ([tuple(0.5 for _ in dims)] if len(dims) > 1 else [])
    violations, unverified = [], []
    for ph in phases:
        cmax = bound(ph)
        for ch in samples:
            ev = L.at(ph, *temps_at(ch))
            T = {role: round(v, 3) for role, v in zip(roles, temps_at(ch)) if v is not None}
            if not ev["ok"]:
                unverified.append({"phase": ph.name or f"{ph.torque_Nm:g} N*m", "temperatures_C": T,
                                   "reason": ev["reason"]})
                continue
            for k, v in ev["losses"].items():
                c = cmax.get(k, 0.0)
                if v > c + 1e-9 * max(abs(c), 1.0) + 1e-6:
                    violations.append({"phase": ph.name or f"{ph.torque_Nm:g} N*m", "loss": k, "W": v,
                                       "corner_max_W": c, "temperatures_C": T})
    bounded = not violations and not unverified
    lb = {"temperatures_C": {role: [lo, hi] for role, _i, lo, hi in dims}, "corners": len(corners),
          "interior_samples": len(samples) * len(phases) if dims else 0,
          "check": ("no fed-back temperature: losses at the scenario temperatures" if not dims else
                    "passed: no interior sample above its corners" if bounded else
                    "FAILED: a loss exceeds its corner values inside the box" if violations else
                    "not verified: a phase is not established at an interior sample"),
          "violations": violations[:12], "unverified": unverified[:12], "is_bound": bounded}
    out = {"basis": ("closed form, every loss at its largest value over the corners of the node-temperature box "
                     "(coolant inlet .. node limit); the highest temperature inside every phase, not only at its end"
                     + ("" if bounded else " - ESTIMATE: the corner losses are not shown to bound the losses inside "
                                            "the box (the loss bound check)")),
           "loss_bound": lb}
    def periodic_margin(p0, d0, rest_durs=None):
        """Smallest node margin in the periodic cycle with the pulse (powers p0, duration d0) and the other phases
        (their declared durations unless ``rest_durs`` is given)."""
        durs = [d0] + (list(rest_durs) if rest_durs is not None else [ph.duration_s for ph in phases[1:]])
        worst = math.inf
        for i, nd in enumerate(model.nodes):
            Ps = [p0[0][i]] + [b[0][i] for b in base[1:]]
            refs = [p0[1][i]] + [b[1][i] for b in base[1:]]
            _ends, mx = _periodic_ends(np.asarray(nd.network.R_K_per_W), np.asarray(nd.network.tau_s), Ps, durs, True)
            worst = min(worst, min(lim[i] - (r + m) for r, m in zip(refs, mx)))
        return worst

    def first_margin(p0, d0):
        worst, x_init = math.inf, x_init_for(p0[1])
        for i, nd in enumerate(model.nodes):
            rise = _phase_max(x_init[i], np.asarray(nd.network.R_K_per_W), np.asarray(nd.network.tau_s), p0[0][i], d0)
            worst = min(worst, lim[i] - (p0[1][i] + rise))
        return worst

    ph0 = phases[0]
    # allowed pulse duration in the periodic cycle (the other phases as declared)
    if len(phases) > 1:
        m_at = lambda d: periodic_margin(base[0], d)        # noqa: E731
        if m_at(1e-6) < 0:
            out["pulse_duration_s"] = 0.0
            out["pulse_duration_note"] = "the rest phases alone exceed a limit"
        else:
            hi = ph0.duration_s
            while m_at(hi) >= 0 and hi < 1e6:
                hi *= 2.0
            if hi >= 1e6:
                out["pulse_duration_s"] = math.inf
            else:
                lo = hi / 2.0 if m_at(hi / 2.0) >= 0 else 1e-6
                for _ in range(60):
                    mid = 0.5 * (lo + hi)
                    if m_at(mid) >= 0:
                        lo = mid
                    else:
                        hi = mid
                out["pulse_duration_s"] = lo
    # allowed pulse torque (same sign as the pulse) in the periodic cycle and for the first pulse
    sgn = 1.0 if ph0.torque_Nm >= 0 else -1.0

    def torque_limit(margin_fn, d0):
        """Largest pulse torque magnitude (sign of the declared pulse) with a non-negative margin; the static
        policy stops the search where the torque is not established (a None margin)."""
        def m_at(T):
            p = powers(LoadPhase(sgn * T, ph0.speed_rpm, d0))
            return None if p[0] is None else margin_fn(p, d0)
        T0 = abs(ph0.torque_Nm) or 1.0
        m0 = m_at(T0)
        if m0 is None:
            return None, "the pulse torque is not statically established"
        if m0 >= 0:
            lo, hi = T0, 2.0 * T0
            while True:
                m = m_at(hi)
                if m is None or m < 0:
                    break
                lo, hi = hi, 2.0 * hi
                if hi > 1e5:
                    return math.inf, "no node limit and no static limit up to 1e5 N*m"
        else:
            mz = m_at(0.0)
            if mz is None or mz < 0:
                return 0.0, "even a zero pulse torque exceeds a limit (the other phases)"
            lo, hi = 0.0, T0
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            m = m_at(mid)
            if m is not None and m >= 0:
                lo = mid
            else:
                hi = mid
        return sgn * lo, "limited by the static policy" if m_at(hi) is None else "limited by a node temperature"

    if len(phases) > 1:
        out["pulse_torque_Nm"], out["pulse_torque_note"] = torque_limit(periodic_margin, ph0.duration_s)
    out["first_pulse_torque_Nm"], out["first_pulse_torque_note"] = torque_limit(first_margin, ph0.duration_s)
    if len(phases) > 1:
        out.update(_rest_needed(model, phases, base, x_init_for(base[0][1]), lim, periodic_margin))
    out["nodes"] = ids
    return out


def _min_monotone(ok, hi0: float, cap: float = 1e6) -> float:
    """Smallest t >= 0 with ok(t): the first transition on a geometric scan up to ``cap``, then bisection (0 if
    ok(0), inf if never).  The condition need not stay true afterwards (see the rest window)."""
    if ok(0.0):
        return 0.0
    prev = 0.0
    # ratio ~1.05 per sample: a window narrower than ~5 % of its start can still fall between two samples
    for t in np.geomspace(min(hi0, 1.0) * 1e-3, cap, 421):
        t = float(t)
        if ok(t):
            lo, hi = prev, t
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if ok(mid):
                    hi = mid
                else:
                    lo = mid
            return hi
        prev = t
    return math.inf


def _window_end(ok, t_ok: float, t_bad: float) -> float:
    """The end of an interval where ok holds: ok(t_ok) and not ok(t_bad), bisected."""
    lo, hi = t_ok, t_bad
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _rest_needed(model, phases, base, x_init, lim, periodic_margin) -> dict:
    """Cooling recovery: the rest (phase 2 load) needed after the first pulse before the same pulse can be repeated
    without reaching a limit, and the shortest rest that keeps the periodic cycle within the limits."""
    ph0, ph1 = phases[0], phases[1]
    P0, ref0 = base[0]
    P1, ref1 = base[1]

    def repeat_ok(t_rest):
        """The rest (its own peak included) and the repeated pulse stay within every limit."""
        for i, nd in enumerate(model.nodes):
            r, tau = np.asarray(nd.network.R_K_per_W), np.asarray(nd.network.tau_s)
            x = _advance(x_init[i], r, tau, P0[i], ph0.duration_s)
            if t_rest > 0:
                if ref1[i] + _phase_max(x, r, tau, P1[i], t_rest) > lim[i]:
                    return False
                x = _advance(x, r, tau, P1[i], t_rest)
            if ref0[i] + _phase_max(x, r, tau, P0[i], ph0.duration_s) > lim[i]:
                return False
        return True

    def periodic_ok(d1):                        # d1 = 0: the pulse repeated back to back
        return periodic_margin(base[0], ph0.duration_s, [d1] + [ph.duration_s for ph in phases[2:]]) >= 0
    first_ok = True
    for i, nd in enumerate(model.nodes):
        r, tau = np.asarray(nd.network.R_K_per_W), np.asarray(nd.network.tau_s)
        if ref0[i] + _phase_max(x_init[i], r, tau, P0[i], ph0.duration_s) > lim[i]:
            first_ok = False
    # a rest this long settles every Foster term (the rest load's steady state): a condition that holds for some
    # rest but fails here holds only inside a window - the rest load heats the slower nodes again
    long_rest = 40.0 * max(float(np.max(np.asarray(nd.network.tau_s))) for nd in model.nodes)
    out = {"rest_load": {"torque_Nm": ph1.torque_Nm, "speed_rpm": ph1.speed_rpm}}
    for key, ok, active, why_not in (("rest_before_repeat", repeat_ok, first_ok, "the first pulse itself reaches a limit"),
                                     ("periodic_min_rest", periodic_ok, True, "")):
        t0 = _min_monotone(ok, max(ph1.duration_s, 1.0)) if active else None
        note = "" if active else why_not
        if t0 is not None and math.isfinite(t0) and t0 < long_rest and not ok(long_rest):
            end = _window_end(ok, t0, long_rest)
            out[f"{key}_window_end_s"] = end
            note = (f"allowed only for a rest between {t0:.4g} and {end:.4g} s: a longer rest at this load heats the "
                    f"slower nodes again")
        out[f"{key}_s"] = t0
        out[f"{key}_note"] = note
    return out
