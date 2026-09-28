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

import math
from dataclasses import dataclass, replace

import numpy as np

from ..errors import InputValidationError
from ..models.components import TemperatureDependence
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Reason, Status
from ..validation import finite as _finite
from .thermal import ThermalModel, _losses

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
    cu = [(dict(nd.loss_share).get("copper", 0.0), nd.node_id) for nd in model.nodes]
    w = max(cu)[1] if any(v > 0 for v, _ in cu) else None
    dev = [nd.node_id for nd in model.nodes if dict(nd.loss_share).get("inverter_hottest_device", 0.0) > 0]
    inv = [(dict(nd.loss_share).get("inverter", 0.0), nd.node_id) for nd in model.nodes]
    j = dev[0] if dev else (max(inv)[1] if any(v > 0 for v, _ in inv) else None)
    return w, j


class _Losses:
    """Operating-point losses of a phase at the current node temperatures (cached on a temperature grid)."""

    def __init__(self, drive, scenario: Scenario, fb: Feedback, settings: NumericalSettings, tol_K: float = 0.5):
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
        if fb.enabled and not self.rs:
            self.notes.append("no R_s temperature law: the copper loss uses R_s as supplied (no winding feedback)")
        if fb.enabled and not self.module:
            self.notes.append("the inverter loss model has no junction-temperature input (no T_j feedback)")
        if not fb.enabled:
            self.notes.append("loss-temperature feedback off: losses at the stated scenario temperatures")
        self.cache = {}

    def at(self, ph: LoadPhase, T_w: float | None, T_j: float | None) -> dict:
        tw = None if (not self.rs or T_w is None) else round(T_w / self.tol) * self.tol
        tj = None if (not self.module or T_j is None) else round(T_j / self.tol) * self.tol
        key = (ph.torque_Nm, ph.speed_rpm, tw, tj)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        sc = self.scenario.with_(speed_rpm=ph.speed_rpm)
        if tw is not None:
            sc = sc.with_(winding_temp_C=tw)
        drv = self.drive if tj is None else replace(self.drive, inverter=replace(self.drive.inverter, module_Tj_C=tj))
        sol = PolicyEvaluator(drv, sc, self.settings).solve(ph.torque_Nm)
        ok = sol.point is not None and sol.policy_claim.status is Status.FEASIBLE
        det = (sol.point.inverter_loss_detail or {}) if sol.point is not None else {}
        if ok and self.module and not det.get("established"):
            ok = False
        out = {"ok": ok, "losses": _losses(sol.point) if ok else None,
               "reason": "" if ok else (sol.policy_claim.detail or "; ".join(det.get("problems", [])) or
                                       sol.policy_claim.status.value),
               "status": sol.policy_claim.status.value, "T_w": tw, "T_j": tj}
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


def _advance(x, r, tau, P, dt):
    a = np.exp(-dt / tau)
    return x * a + r * P * (1.0 - a)


def _crossing(x, r, tau, P, ref, limit, dt) -> float | None:
    """First time in (0, dt] at which ref + sum x(t) reaches the limit for a constant P (bisection on the exact
    trajectory; None when not reached)."""
    f = lambda t: ref + float(np.sum(_advance(x, r, tau, P, t))) - limit     # noqa: E731
    ts = np.linspace(0.0, dt, 17)[1:]
    prev = 0.0
    for t in ts:
        if f(t) >= 0.0:
            lo, hi = prev, t
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if f(mid) >= 0.0:
                    hi = mid
                else:
                    lo = mid
            return hi
        prev = t
    return None


def repeated_load(drive, scenario: Scenario, model: ThermalModel, phases: list, cycles: int = 20,
                  initial: InitialState | None = None, feedback: Feedback | None = None, steps_per_phase: int = 24,
                  settings: NumericalSettings = DEFAULT_SETTINGS,
                  allowed: bool = True) -> dict:
    """The duty cycle ``phases`` repeated ``cycles`` times (or until the periodic cycle is reached) from ``initial``.
    ``phases[0]`` is the pulse (the allowed duration / torque refer to it)."""
    if scenario.coolant_temp_C is None:
        raise InputValidationError("coolant temperature must be stated for a thermal evaluation", field="coolant_temp_C")
    if not phases:
        raise InputValidationError("a duty cycle needs at least one load phase", field="phases")
    cycles = int(cycles)
    if not 1 <= cycles <= 500:
        raise InputValidationError("cycles must be 1..500", field="cycles")
    init = initial or InitialState()
    fb = feedback or Feedback()
    L = _Losses(drive, scenario, fb, settings)
    w_id, j_id = _feedback_nodes(model)
    ids = [nd.node_id for nd in model.nodes]
    iw = ids.index(w_id) if w_id in ids else None
    ij = ids.index(j_id) if j_id in ids else None
    R, tau = _node_arrays(model)
    inlet = float(scenario.coolant_temp_C)

    def refs_for(losses):
        fluid = model.coolant.fluid_temperatures(inlet, losses) if model.coolant is not None else None
        return [model.reference_C(nd, inlet, losses, fluid) for nd in model.nodes]

    # initial state
    pre = None
    if init.kind == "steady_state_at":
        pre_ph = LoadPhase(float(init.torque_Nm), float(init.speed_rpm), 1.0, "preload")
        ev = L.at(pre_ph, None, None)
        if not ev["ok"]:
            raise InputValidationError(f"the preload point is not statically established: {ev['reason']}",
                                       field="initial_state")
        pre = ev["losses"]
        # the preload's own steady temperatures set its R_s(T) / T_j: iterate the fixed point
        for _ in range(30):
            refs0 = refs_for(pre)
            x0 = _initial_states(model, init, pre, refs0)
            temps = [ref + float(np.sum(x)) for ref, x in zip(refs0, x0)]
            ev = L.at(pre_ph, temps[iw] if iw is not None else None, temps[ij] if ij is not None else None)
            if not ev["ok"]:
                raise InputValidationError(f"the preload point is not established at its own steady temperature: "
                                           f"{ev['reason']}", field="initial_state")
            if ev["losses"] == pre:
                break
            pre = ev["losses"]
    first = L.at(phases[0], None, None)
    refs = refs_for(pre if pre is not None else (first["losses"] or {}))
    x = _initial_states(model, init, pre, refs)
    temps0 = [ref + float(np.sum(xx)) for ref, xx in zip(refs, x)]

    # time stepping: one cycle from given Foster states, losses re-evaluated per step
    bounds, losses_seen = [], []
    tr_t, tr_T = [0.0], [list(temps0)]

    def run_cycle(xs, temps, c, t, record):
        xs = [xx.copy() for xx in xs]
        peak, fl, refs = list(temps), None, None
        for k, ph in enumerate(phases):
            dt = ph.duration_s / steps_per_phase
            if record:
                bounds.append((t, t + ph.duration_s, k, c))
            for s_ in range(steps_per_phase):
                ev = L.at(ph, temps[iw] if iw is not None else None, temps[ij] if ij is not None else None)
                if not ev["ok"]:
                    return xs, temps, peak, fl, {"t_s": t, "cycle": c + 1, "phase": k, "reason": ev["reason"],
                                                 "status": ev["status"]}, t, refs
                los = ev["losses"]
                if record and c == 0 and s_ == 0:
                    losses_seen.append({"phase": k, **los})
                refs = refs_for(los)
                Ps = [nd.power(los) for nd in model.nodes]
                bad = [nd.node_id for nd, P in zip(model.nodes, Ps) if math.isnan(P)]
                if bad:
                    return xs, temps, peak, fl, {"t_s": t, "cycle": c + 1, "phase": k, "status": "UNKNOWN",
                                                 "reason": f"heat source of node {bad[0]!r} not available"}, t, refs
                for i, nd in enumerate(model.nodes):
                    if fl is None:
                        tc = _crossing(xs[i], R[i], tau[i], Ps[i], refs[i], nd.limit_C, dt)
                        if tc is not None:
                            fl = {"t_s": t + tc, "cycle": c + 1, "phase": k, "node": nd.node_id}
                    xs[i] = _advance(xs[i], R[i], tau[i], Ps[i], dt)
                t += dt
                temps = [ref + float(np.sum(xx)) for ref, xx in zip(refs, xs)]
                peak = [max(a, b) for a, b in zip(peak, temps)]
                if record:
                    tr_t.append(t)
                    tr_T.append(list(temps))
        return xs, temps, peak, fl, None, t, refs

    per_cycle, first_limit, stop = [], None, None
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
        if c >= 1 and all(abs(temps[i] - start_T[i]) < 0.02 for i in range(len(ids))):
            break                                  # the physical run itself reached the periodic cycle

    # periodic steady cycle: per Foster term the cycle map is affine, x_end = A x_start + b with A = exp(-T/tau),
    # so x* = (x_end - A x_start) / (1 - A); iterate with the losses re-evaluated (feedback) until it holds
    periodic = None
    if stop is None:
        T_cyc = sum(ph.duration_s for ph in phases)
        A = [np.exp(-T_cyc / tt) for tt in tau]
        xs, ts_ = x, temps
        for it in range(1, 41):
            x_end, t_end, peak, _fl, stp, _t, refs_end = run_cycle(xs, ts_, 0, 0.0, False)
            if stp:
                periodic = {"reached": False, "stopped": stp, "iterations": it}
                break
            if it > 1 and all(abs(t_end[i] - ts_[i]) < 0.02 for i in range(len(ids))):
                periodic = {"reached": True, "iterations": it, "peak_C": dict(zip(ids, peak)),
                            "start_C": dict(zip(ids, ts_)), "end_C": dict(zip(ids, t_end))}
                break
            xs = [(xe - a * x0) / (1.0 - a) for xe, a, x0 in zip(x_end, A, xs)]
            ts_ = [ref + float(np.sum(xx)) for ref, xx in zip(refs_end, xs)]
        else:
            periodic = {"reached": False, "iterations": 40, "peak_C": dict(zip(ids, peak)),
                        "note": "the periodic fixed point did not settle in 40 iterations (strong feedback)"}
    limits = {nd.node_id: nd.limit_C for nd in model.nodes}
    if periodic is not None and periodic.get("peak_C"):
        margins = {n: limits[n] - periodic["peak_C"][n] for n in ids}
        periodic.update(margin_K=margins, governing_node=min(margins, key=margins.get),
                        exceeds=any(m < 0 for m in margins.values()))

    # closed-form, losses held at the hotter temperature corner (conservative when losses grow with temperature)
    allowed_out = _allowed(L, model, phases, init, pre, iw, ij, refs_for, x_init=_initial_states(
        model, init, pre, refs_for(pre if pre is not None else (first["losses"] or {})))) if (allowed and stop is None) \
        else None

    stated = {"coolant_temp_C": scenario.coolant_temp_C, "Vdc_V": scenario.Vdc_V,
              "switching_frequency_Hz": scenario.switching_frequency_Hz, **model.stated_conditions()}
    problems = model.qualification(stated, "equilibrium_at_coolant", losses_seen[0] if losses_seen else None)
    problems = [p for p in problems if "initial" not in p]          # the start is modelled here (INITIAL_KINDS)
    q = f"{len(phases)}-phase duty cycle x {len(per_cycle)} (pulse {phases[0].torque_Nm:g} N*m for " \
        f"{phases[0].duration_s:g} s at {phases[0].speed_rpm:g} rpm), start {init.kind}"
    horizon = f"{len(per_cycle)} x {sum(ph.duration_s for ph in phases):g} s duty cycle"
    exceeded = first_limit is not None
    per_note = "" if not (periodic and periodic.get("margin_K")) else (
        f"; periodic cycle: governing node {periodic['governing_node']!r}, margin "
        f"{periodic['margin_K'][periodic['governing_node']]:.3g} K")
    if stop is not None:
        claim = Claim("repeated_load", Status.UNKNOWN if stop["status"] != "INFEASIBLE" else Status.INFEASIBLE, q,
                      "static policy / loss not established during the cycle", time_horizon=horizon, reasons=(Reason.MISSING_INPUT,)
                      if stop["status"] != "INFEASIBLE" else (Reason.CONSTRAINT_VIOLATION,),
                      detail=f"stopped at {stop['t_s']:.4g} s (cycle {stop['cycle']}, phase {stop['phase'] + 1}): "
                             f"{stop['reason']}")
    elif problems:
        claim = Claim("repeated_load", Status.UNKNOWN, q, "thermal screening estimate", time_horizon=horizon,
                      reasons=(Reason.UNVALIDATED_DURATION,),
                      qualifiers=(f"screening estimate: {'a node limit is reached' if exceeded else 'within limits'} "
                                  f"in the {len(per_cycle)} simulated cycle(s){per_note}",),
                      detail="; ".join(problems) + ": the estimate is not a duty-cycle rating")
    else:
        claim = Claim("repeated_load", Status.INFEASIBLE if exceeded else Status.FEASIBLE, q,
                      "qualified thermal model at matching conditions", time_horizon=horizon,
                      reasons=(Reason.CONSTRAINT_VIOLATION,) if exceeded else (),
                      detail=f"{len(per_cycle)} simulated cycle(s){per_note}")
    step = max(1, len(tr_t) // 1500)
    return {"claim": claim.to_dict(), "cycles_run": len(per_cycle), "cycles_requested": cycles,
            "first_limit": first_limit, "stopped": stop,
            "initial": {"kind": init.kind, "temperatures_C": dict(zip(ids, temps0)),
                        "preload": None if init.kind != "steady_state_at" else
                        {"torque_Nm": init.torque_Nm, "speed_rpm": init.speed_rpm, "losses_W": pre}},
            "periodic": periodic,
            "per_cycle": per_cycle, "allowed": allowed_out,
            "feedback": {"winding_node": w_id, "junction_node": j_id, "rs": L.rs, "module": L.module,
                         "notes": L.notes, "evaluations": len(L.cache)},
            "limits_C": limits, "qualification_problems": problems,
            "trace": {"t_s": tr_t[::step], "nodes": {n: [row[i] for row in tr_T][::step] for i, n in enumerate(ids)},
                      "phase_bounds": bounds},
            "assumptions": ["exact exponential update of every Foster term per step (constant loss within a step, "
                            f"{steps_per_phase} steps per phase; losses re-evaluated per step at the node "
                            "temperatures, cached on a 0.5 K grid)",
                            "coolant rise follows each step's losses instantly (loop thermal mass not modelled; "
                            "conservative for pulse peaks)",
                            "allowed duration / torque: closed form with the losses at the hotter temperature corner "
                            "(coolant or node limit) - monotone losses in temperature assumed"]}


def _periodic_ends(Rs, taus, Ps, durs) -> list:
    """Periodic steady state of one node (Foster terms) for piecewise-constant powers: the rise at the end of every
    phase.  x0 = sum_j R P_j (1 - a_j) prod_{k > j} a_k / (1 - prod a)."""
    a = [np.exp(-d / taus) for d in durs]
    A = np.prod(a, axis=0)
    x0 = np.zeros_like(Rs)
    for j in range(len(durs)):
        tail = np.prod(a[j + 1:], axis=0) if j + 1 < len(durs) else np.ones_like(Rs)
        x0 = x0 + Rs * Ps[j] * (1.0 - a[j]) * tail
    x0 = x0 / (1.0 - A)
    out, x = [], x0
    for j in range(len(durs)):
        x = x * a[j] + Rs * Ps[j] * (1.0 - a[j])
        out.append(float(np.sum(x)))
    return out


def _allowed(L: _Losses, model: ThermalModel, phases: list, init: InitialState, pre, iw, ij, refs_for, x_init) -> dict:
    """Allowed pulse duration / torque in the periodic cycle and allowed first-pulse torque, closed form with the
    losses at the hotter temperature corner (coolant or node limit)."""
    ids = [nd.node_id for nd in model.nodes]
    lim = [nd.limit_C for nd in model.nodes]

    def hot(ph):
        corners = [(None, None)]
        if iw is not None or ij is not None:
            corners.append((lim[iw] if iw is not None else None, lim[ij] if ij is not None else None))
        best = None
        for tw, tj in corners:
            ev = L.at(ph, tw, tj)
            if not ev["ok"]:
                return None
            best = ev["losses"] if best is None else {k: max(best.get(k, 0.0), v) for k, v in ev["losses"].items()}
        return best

    def powers(ph):
        los = hot(ph)
        if los is None:
            return None, None
        return [nd.power(los) for nd in model.nodes], refs_for(los)

    base = [powers(ph) for ph in phases]
    if any(p is None for p, _r in base):
        return {"status": "UNKNOWN", "reason": "a phase is not established at the temperature corners"}

    def periodic_margin(p0, d0, rest_durs=None):
        """Smallest node margin in the periodic cycle with the pulse (powers p0, duration d0) and the other phases
        (their declared durations unless ``rest_durs`` is given)."""
        durs = [d0] + (list(rest_durs) if rest_durs is not None else [ph.duration_s for ph in phases[1:]])
        worst = math.inf
        for i, nd in enumerate(model.nodes):
            Ps = [p0[0][i]] + [b[0][i] for b in base[1:]]
            refs = [p0[1][i]] + [b[1][i] for b in base[1:]]
            ends = _periodic_ends(np.asarray(nd.network.R_K_per_W), np.asarray(nd.network.tau_s), Ps, durs)
            worst = min(worst, min(lim[i] - (r + e) for r, e in zip(refs, ends)))
        return worst

    def first_margin(p0, d0):
        worst = math.inf
        for i, nd in enumerate(model.nodes):
            rise = float(np.sum(_advance(x_init[i], np.asarray(nd.network.R_K_per_W),
                                         np.asarray(nd.network.tau_s), p0[0][i], d0)))
            worst = min(worst, lim[i] - (p0[1][i] + rise))
        return worst

    ph0 = phases[0]
    out = {"basis": "closed form, losses at the hotter temperature corner (coolant / node limit)"}
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
        out.update(_rest_needed(model, phases, base, x_init, lim, periodic_margin))
    out["nodes"] = ids
    return out


def _min_monotone(ok, hi0: float, cap: float = 1e6) -> float:
    """Smallest t >= 0 with ok(t) for a condition that stays true once true (0 if ok(0), inf if never up to cap)."""
    if ok(0.0):
        return 0.0
    hi = hi0
    while not ok(hi):
        hi *= 2.0
        if hi > cap:
            return math.inf
    lo = 0.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _rest_needed(model, phases, base, x_init, lim, periodic_margin) -> dict:
    """Cooling recovery: the rest (phase 2 load) needed after the first pulse before the same pulse can be repeated
    without reaching a limit, and the shortest rest that keeps the periodic cycle within the limits."""
    ph0, ph1 = phases[0], phases[1]
    P0, ref0 = base[0]
    P1 = base[1][0]

    def repeat_ok(t_rest):
        for i, nd in enumerate(model.nodes):
            r, tau = np.asarray(nd.network.R_K_per_W), np.asarray(nd.network.tau_s)
            x = _advance(x_init[i], r, tau, P0[i], ph0.duration_s)
            x = _advance(x, r, tau, P1[i], t_rest) if t_rest > 0 else x
            x = _advance(x, r, tau, P0[i], ph0.duration_s)
            if ref0[i] + float(np.sum(x)) > lim[i]:
                return False
        return True

    def periodic_ok(d1):
        if d1 <= 0:
            return False
        return periodic_margin(base[0], ph0.duration_s, [d1] + [ph.duration_s for ph in phases[2:]]) >= 0
    first_ok = True
    for i, nd in enumerate(model.nodes):
        r, tau = np.asarray(nd.network.R_K_per_W), np.asarray(nd.network.tau_s)
        if ref0[i] + float(np.sum(_advance(x_init[i], r, tau, P0[i], ph0.duration_s))) > lim[i]:
            first_ok = False
    return {"rest_before_repeat_s": _min_monotone(repeat_ok, max(ph1.duration_s, 1.0)) if first_ok else None,
            "rest_before_repeat_note": "" if first_ok else "the first pulse itself reaches a limit",
            "periodic_min_rest_s": _min_monotone(periodic_ok, max(ph1.duration_s, 1.0)),
            "rest_load": {"torque_Nm": ph1.torque_Nm, "speed_rpm": ph1.speed_rpm}}
