"""System budgets (system view, item 11): a requirement split over its contributors and checked bottom-up.

A budget is a limit on one quantity (torque error, time, energy, ...) and the contributors that use it.  Each
contributor has a magnitude in the budget's unit (None = not established - the budget is then UNKNOWN, never filled
with zero), a kind and a basis:

  systematic  - the same sign and size in every unit of a population at that condition: added linearly,
  random      - independent spread between units or events: added as a root sum of squares.

Three stacks are always shown side by side and the declared one decides: ``worst_case`` (every contributor linearly,
no probability attached - as everywhere in this tool), ``rss`` (all root sum of squares; it presumes independence and
is never a bound) and ``mixed`` (systematic linearly + random as RSS).  Per contributor: its share of the stack, the
room it has before the stack reaches the limit with the others unchanged (break-even growth), and - top-down - an
allocation (equal shares, shares proportional to the present values, or declared) with its own PASS / FAIL.

Templates.  ``torque_accuracy``: the shaft-torque error of the minimum-current policy point from declared sensing,
position and model errors, each COMPUTED from the machine model at every operating point (current-sensor gain: the
loop regulates the measured current, the true current is i_ref / (1 +- g); offset: a DC error of one phase rotates in
the dq frame - its peak over a revolution; resolver offset: the true current is the reference rotated by +-d_theta;
magnet temperature: psi (1 + alpha dT) at the reference current; model tolerance: the corners of psi, L_d, L_q;
estimator: declared).  Against the requirement max(abs, rel |T|) at each point, and - for the functional-safety view,
worst case throughout - against the project's torque window (TSR) and the torque monitor's threshold (SM):

  window             normal operation: the error stack stays inside the safety window,
  false trip         what the monitor sees in normal operation (its declared mismatch + the control errors it can see)
                     stays below its threshold,
  undetected         a fault the monitor just does not detect (its threshold) + its mismatch + the errors it cannot
                     see stays inside the safety window - the classic allocation of a window between detection
                     threshold and measurement error.

Which errors the monitor sees follows from the architecture's sensor roles: a monitor that reads the control's own
current sensors or angle cannot see their errors (no false trip from them - but they move the shaft torque unseen).
``ftti``: the worst-case path of the project's FTTI chain (the same items the timing analysis sums).
``cycle_losses``: the drive cycle's loss energy per component against declared allocations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from .. import progress
from ..errors import InputValidationError
from ..validation import finite as _finite

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
COMBINATIONS = ("worst_case", "rss", "mixed")
KINDS = ("systematic", "random")
ALLOCATIONS = ("equal", "proportional", "declared")


@dataclass(frozen=True)
class Contributor:
    id: str
    title: str
    value: float | None                        # magnitude in the budget unit (None = not established)
    kind: str = "systematic"
    allocation: float | None = None            # declared allocation (top-down)
    basis: str = ""
    owner: str = ""
    seen_by_monitor: bool | None = None
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise InputValidationError(f"contributor kind must be one of {KINDS}", field=f"contributor.{self.id}.kind")
        if self.value is not None:
            object.__setattr__(self, "value", abs(_finite(f"contributor {self.id}", self.value)))


def contributor_from_dict(d: dict) -> Contributor:
    return Contributor(str(d["id"]), str(d.get("title") or d["id"]), None if d.get("value") is None else float(d["value"]),
                       str(d.get("kind", "systematic")), None if d.get("allocation") is None else float(d["allocation"]),
                       str(d.get("basis", "")), str(d.get("owner", "")), d.get("seen_by_monitor"))


def _stack(vals_kinds: list, combination: str) -> float:
    if combination == "worst_case":
        return sum(v for v, _k in vals_kinds)
    if combination == "rss":
        return math.sqrt(sum(v * v for v, _k in vals_kinds))
    sys_ = sum(v for v, k in vals_kinds if k == "systematic")
    rnd = math.sqrt(sum(v * v for v, k in vals_kinds if k == "random"))
    return sys_ + rnd


def _equal_divisor(contributors: list, combination: str) -> float:
    """n such that n equal shares a = limit / n stack to exactly the limit under the combination."""
    n = len(contributors)
    if combination == "worst_case":
        return float(n)
    if combination == "rss":
        return math.sqrt(n)
    n_sys = sum(1 for c in contributors if c.kind == "systematic")
    return n_sys + math.sqrt(n - n_sys)


def evaluate_budget(title: str, unit: str, limit: float | None, contributors: list, combination: str = "mixed",
                    allocation: str | None = None) -> dict:
    """Stacks, verdict, shares, break-even growth and (optionally) a top-down allocation of one budget."""
    if combination not in COMBINATIONS:
        raise InputValidationError(f"combination must be one of {COMBINATIONS}", field="combination")
    if allocation is not None and allocation not in ALLOCATIONS:
        raise InputValidationError(f"allocation must be one of {ALLOCATIONS}", field="allocation")
    known = [(c.value, c.kind) for c in contributors if c.value is not None]
    missing = [c.id for c in contributors if c.value is None]
    stacks = {k: _stack(known, k) for k in COMBINATIONS}
    total = stacks[combination]
    complete = not missing
    if limit is None:
        status, margin = UNKNOWN, None
    else:
        margin = limit - total
        status = (FAIL if total > limit else (PASS if complete else UNKNOWN))   # a partial stack can already fail
    rows = []
    sys_sum = sum(v for v, k in known if k == "systematic")
    rnd_ss = sum(v * v for v, k in known if k == "random")
    all_ss = sum(v * v for v, _k in known)
    for c in contributors:
        r = {"id": c.id, "title": c.title, "value": c.value, "kind": c.kind, "basis": c.basis, "owner": c.owner,
             "seen_by_monitor": c.seen_by_monitor, "detail": c.detail}
        if c.value is not None and total > 0:
            v = c.value
            if combination == "worst_case" or (combination == "mixed" and c.kind == "systematic"):
                share = v / total
            elif combination == "rss":
                share = v * v / all_ss if all_ss > 0 else 0.0
            else:                                         # mixed, random: its part of the RSS term
                rnd = math.sqrt(rnd_ss)
                share = (rnd / total) * (v * v / rnd_ss) if rnd_ss > 0 else 0.0
            r["share"] = share
            if limit is not None and complete:
                # how much this item may grow (negative: must shrink) with the others unchanged until the stack is
                # exactly the limit; None with alone_insufficient when even zero would not bring the stack inside
                if combination == "worst_case" or (combination == "mixed" and c.kind == "systematic"):
                    g = limit - total
                elif combination == "rss":
                    rest = all_ss - v * v
                    g = (math.sqrt(limit * limit - rest) - v) if limit * limit >= rest else None
                else:
                    room = limit - sys_sum
                    rest = rnd_ss - v * v
                    g = (math.sqrt(room * room - rest) - v) if room >= 0 and room * room >= rest else None
                if g is not None and g < -v:
                    g = None
                r["break_even_growth"] = g
                r["alone_insufficient"] = g is None
        rows.append(r)
    if allocation and limit is not None:
        for c, r in zip(contributors, rows):
            if allocation == "declared":
                a = c.allocation
            elif allocation == "equal":
                a = limit / _equal_divisor(contributors, combination)
            else:                                         # proportional to the present values, scaled to the limit
                a = None if (c.value is None or total <= 0) else c.value * limit / total
            r["allocation"] = a
            r["allocation_status"] = (UNKNOWN if (a is None or c.value is None) else (PASS if c.value <= a else FAIL))
    return {"title": title, "unit": unit, "limit": limit, "combination": combination, "stacks": stacks,
            "total": total, "margin": margin, "status": status, "complete": complete, "missing": missing,
            "contributors": rows, "allocation_method": allocation,
            "meaning": "worst_case: linear sum of the bounds (no probability); rss: root sum of squares (assumes "
                       "independence, not a bound); mixed: systematic linearly + random as RSS"}


# --------------------------------------------------------------------------------------------- torque accuracy

TQ_ITEMS = ("current_gain", "current_offset", "resolver_offset", "magnet_temperature", "model_tolerance", "estimator",
            "monitor_mismatch")
# default kinds: the sensors' errors and the machine's parameter spread vary from unit to unit (random); the magnet
# temperature estimate and the torque estimator err the same way in every unit at one condition (systematic)
TQ_DEFAULT_KIND = {"current_gain": "random", "current_offset": "random", "resolver_offset": "random",
                   "magnet_temperature": "systematic", "model_tolerance": "random", "estimator": "systematic",
                   "monitor_mismatch": "random"}


def _torque_fn(drive, scenario, psi_scale=1.0, Ld_scale=1.0, Lq_scale=1.0):
    from ..models.flux import ConstantFluxModel
    from ..physics import DriveKernel
    d = drive
    if (psi_scale, Ld_scale, Lq_scale) != (1.0, 1.0, 1.0):
        fl = drive.motor.flux
        if not isinstance(fl, ConstantFluxModel):
            return None
        d = replace(drive, motor=replace(drive.motor, flux=replace(fl, psi_pm_Wb=fl.psi_pm_Wb * psi_scale,
                                                                   Ld_H=fl.Ld_H * Ld_scale, Lq_H=fl.Lq_H * Lq_scale)))
    k = DriveKernel(d, scenario)
    if not k.evaluable:
        return None
    return lambda i_d, i_q: float(k.evaluate(np.array([i_d]), np.array([i_q]))["tem"][0])


def _psi_coeff(drive, errors):
    pt = drive.motor.psi_temperature
    if pt is not None:
        return pt.coeff_per_K, f"declared PM flux temperature law ({pt.basis})"
    c = errors.get("magnet_coeff_per_K")
    if c is not None:
        return float(c), str(errors.get("magnet_coeff_basis") or "declared coefficient")
    return None, "no PM flux temperature coefficient (motor law or magnet_coeff_per_K)"


def torque_contributions(drive, scenario, i_d: float, i_q: float, T_ref: float, errors: dict) -> dict:
    """Shaft-torque error (N*m, magnitude) of each declared error at one reference current (see the module text)."""
    T = _torque_fn(drive, scenario)
    if T is None:
        return {k: None for k in TQ_ITEMS}
    T0 = T(i_d, i_q)
    out, det = {}, {}
    g = errors.get("current_gain_pct")
    if g is None:
        out["current_gain"] = None
    else:
        g = abs(float(g)) / 100.0
        vals = [abs(T(i_d / (1 + s * g), i_q / (1 + s * g)) - T0) for s in (1, -1) if 1 + s * g > 0]
        out["current_gain"] = max(vals)
    o = errors.get("current_offset_A")
    if o is None:
        out["current_offset"] = None
    else:
        da = 2.0 * abs(float(o)) / 3.0                 # Clarke (amplitude invariant) of a phase-a offset
        th = np.linspace(0.0, 2 * math.pi, 145)[:-1]
        dd, dq = da * np.cos(th), -da * np.sin(th)
        out["current_offset"] = max(abs(T(i_d - a, i_q - b) - T0) for a, b in zip(dd, dq))
        det["current_offset"] = "peak over one electrical revolution (a torque ripple at the electrical frequency)"
    r = errors.get("resolver_offset_deg_e")
    if r is None:
        out["resolver_offset"] = None
    else:
        dl = math.radians(abs(float(r)))
        vals = []
        for s in (1, -1):
            c, sn = math.cos(s * dl), math.sin(s * dl)
            vals.append(abs(T(i_d * c - i_q * sn, i_d * sn + i_q * c) - T0))
        out["resolver_offset"] = max(vals)
    dT = errors.get("magnet_temp_dev_K")
    if dT is None:
        out["magnet_temperature"] = None
    else:
        a, why = _psi_coeff(drive, errors)
        if a is None:
            out["magnet_temperature"] = None
            det["magnet_temperature"] = why
        else:
            vals = []
            for s in (1, -1):
                Tf = _torque_fn(drive, scenario, psi_scale=1 + a * s * abs(float(dT)))
                vals.append(None if Tf is None else abs(Tf(i_d, i_q) - T0))
            out["magnet_temperature"] = None if any(v is None for v in vals) else max(vals)
            det["magnet_temperature"] = why
    tol = [errors.get(k) for k in ("psi_tol_pct", "Ld_tol_pct", "Lq_tol_pct")]
    if all(t is None for t in tol):
        out["model_tolerance"] = None
    else:
        tp, tdd, tqq = (abs(float(t or 0.0)) / 100.0 for t in tol)
        vals = []
        for sp in (1, -1):
            for sd in (1, -1):
                for sq in (1, -1):
                    Tf = _torque_fn(drive, scenario, 1 + sp * tp, 1 + sd * tdd, 1 + sq * tqq)
                    vals.append(None if Tf is None else abs(Tf(i_d, i_q) - T0))
        out["model_tolerance"] = None if any(v is None for v in vals) else max(vals)
        if out["model_tolerance"] is None:
            det["model_tolerance"] = "parametric tolerance not defined for a flux-map model: declare the estimator item"
    ea, ep = errors.get("estimator_abs_Nm"), errors.get("estimator_pct")
    out["estimator"] = None if (ea is None and ep is None) else max(abs(float(ea or 0.0)),
                                                                    abs(float(ep or 0.0)) / 100.0 * abs(T_ref))
    ma, mp = errors.get("monitor_mismatch_abs_Nm"), errors.get("monitor_mismatch_pct")
    out["monitor_mismatch"] = None if (ma is None and mp is None) else max(abs(float(ma or 0.0)),
                                                                           abs(float(mp or 0.0)) / 100.0 * abs(T_ref))
    out["_detail"] = det
    return out


ERROR_KEYS = ("current_gain_pct", "current_offset_A", "resolver_offset_deg_e", "magnet_temp_dev_K", "magnet_coeff_per_K",
              "psi_tol_pct", "Ld_tol_pct", "Lq_tol_pct", "estimator_abs_Nm", "estimator_pct", "monitor_mismatch_abs_Nm",
              "monitor_mismatch_pct")


def validate_torque_errors(d: dict) -> None:
    """The project's ``torque_errors`` section: magnitudes are finite numbers (null = not declared)."""
    for k in ERROR_KEYS:
        v = d.get(k)
        if v is not None:
            x = _finite(f"torque_errors.{k}", v)
            if k != "magnet_coeff_per_K" and x < 0:
                raise InputValidationError(f"torque_errors.{k} is a magnitude (>= 0)", field=f"torque_errors.{k}")
    unknown = set(d) - set(ERROR_KEYS) - {"basis", "magnet_coeff_basis", "source"}
    if unknown:
        raise InputValidationError(f"unknown torque error keys {sorted(unknown)}", field="torque_errors")


def _window(spec: dict | None, T: float) -> float | None:
    if not spec:
        return None
    return max(float(spec.get("abs_Nm") or 0.0), float(spec.get("rel") or 0.0) * abs(T))


def default_points(drive, Vdc, limits, temps, speeds=(1000.0, 3000.0, 6000.0, 9000.0, 12000.0),
                   fractions=(-1.0, -0.5, -0.2, 0.2, 0.5, 1.0)) -> list:
    """Operating points spread over the policy envelope: fractions of the capability at each speed."""
    from ..scenario import Scenario
    from ..solvers.capability import policy_capability
    from ..solvers.policy import PolicyEvaluator
    pts = []
    for n in speeds:
        ev = PolicyEvaluator(drive, Scenario("budget", float(n), Vdc, limits, **temps))
        cap = {s: policy_capability(ev, s, certify=False) for s in (1, -1)}
        for f in fractions:
            c = cap[1 if f > 0 else -1].value_Nm
            if c is not None:                      # the capability carries the sign (braking < 0)
                pts.append((float(n), round(abs(f) * c, 3)))
    return pts


def torque_accuracy(drive, *, Vdc: float, limits, points: list, errors: dict, requirement: dict | None,
                    combination: str = "mixed", kinds: dict | None = None, temps: dict | None = None,
                    fusa: dict | None = None) -> dict:
    """The torque-accuracy budget at every operating point (see the module text)."""
    from ..scenario import Scenario
    from ..solvers.policy import PolicyEvaluator
    if combination not in COMBINATIONS:
        raise InputValidationError(f"combination must be one of {COMBINATIONS}", field="combination")
    temps = {k: v for k, v in (temps or {}).items() if v is not None}
    kinds = {**TQ_DEFAULT_KIND, **(kinds or {})}
    basis = errors.get("basis") or {}
    sees = set((fusa or {}).get("monitor_sees") or ())
    seen_by = (lambda k: k in sees) if fusa else (lambda k: None)          # noqa: E731
    rows = []
    with progress.span(len(points), "torque accuracy") as sp:
        for (n, T) in points:
            sp.step()
            sc = Scenario("budget", float(n), Vdc, limits, **temps)
            sol = PolicyEvaluator(drive, sc).solve(float(T))
            st = sol.policy_claim.status.value
            if sol.point is None or st != "FEASIBLE":
                rows.append({"speed_rpm": n, "torque_Nm": T, "status": "NOT_EVALUATED",
                             "reason": f"policy {st}: {sol.policy_claim.detail}"})
                continue
            pt = sol.point
            con = torque_contributions(drive, sc, pt.id_A, pt.iq_A, float(T), errors)
            det = con.pop("_detail", {})
            cs = [Contributor(k, k.replace("_", " "), con[k], kinds[k], None, str(basis.get(k, "")),
                              seen_by_monitor=seen_by(k), detail={"note": det.get(k, "")})
                  for k in TQ_ITEMS if k != "monitor_mismatch" and (k in con) and
                  (con[k] is not None or _declared(errors, k))]
            lim = _window(requirement, T)
            b = evaluate_budget("torque accuracy", "N*m", lim, cs, combination)
            row = {"speed_rpm": n, "torque_Nm": T, "id_A": pt.id_A, "iq_A": pt.iq_A, "contributions_Nm": con,
                   "stacks_Nm": b["stacks"], "total_Nm": b["total"], "limit_Nm": lim, "margin_Nm": b["margin"],
                   "status": b["status"], "missing": b["missing"]}
            if fusa:
                row.update(_fusa_row(fusa, float(T), con, [c.id for c in cs], b["complete"]))
            rows.append(row)
    ev = [r for r in rows if "total_Nm" in r]
    worst = min(ev, key=lambda r: (r["margin_Nm"] if r["margin_Nm"] is not None else math.inf)) if ev else None
    sts = [r["status"] for r in ev]
    overall = FAIL if FAIL in sts else (UNKNOWN if (UNKNOWN in sts or not ev) else PASS)
    per_item = {k: max((r["contributions_Nm"].get(k) or 0.0) for r in ev) if ev else None for k in TQ_ITEMS}
    out = {"combination": combination, "requirement": requirement, "errors": errors, "kinds": kinds,
           "not_declared": [k for k in TQ_ITEMS if not _declared(errors, k)],
           "points": rows, "status": overall, "worst_point": worst,
           "max_contribution_Nm": per_item,
           "counts": {s: sts.count(s) for s in (PASS, FAIL, UNKNOWN)},
           "not_evaluated": sum(1 for r in rows if r.get("status") == "NOT_EVALUATED"),
           "notes": ["static (steady-state) error at the minimum-current policy point; the voltage-limited region's "
                     "current-regulator saturation and the dynamic tracking error are not in it",
                     "a current-sensor offset produces a torque ripple at the electrical frequency: its peak is "
                     "counted", "errors the torque monitor cannot see (it reads the same sensors, or computes with "
                     "the same machine model) move the shaft torque without moving its estimate: no false trip from "
                     "them, but they consume the safety window"]}
    if worst is not None:
        cs = [Contributor(k, k.replace("_", " "), worst["contributions_Nm"].get(k), kinds[k], None,
                          str(basis.get(k, "")), seen_by_monitor=seen_by(k))
              for k in TQ_ITEMS if k != "monitor_mismatch" and (worst["contributions_Nm"].get(k) is not None
                                                                or _declared(errors, k))]
        out["worst_point_budget"] = evaluate_budget("torque accuracy at the worst point", "N*m", worst["limit_Nm"], cs,
                                                    combination, "proportional")
    if fusa:
        und = [r for r in ev if r.get("undetected_margin_Nm") is not None]
        out["fusa"] = {"torque_window": fusa.get("torque_window"), "monitor": fusa.get("monitor"),
                       "monitor_sees": list(fusa.get("monitor_sees") or ()),
                       "monitor_sees_basis": fusa.get("monitor_sees_basis", ""),
                       "window_status": _agg([r.get("fusa_window_status") for r in ev]),
                       "false_trip_status": _agg([r.get("false_trip_status") for r in ev]),
                       "undetected_status": _agg([r.get("undetected_status") for r in ev]),
                       "worst_undetected_point": (min(und, key=lambda r: r["undetected_margin_Nm"]) if und else None),
                       "source": fusa.get("source", ""),
                       "meaning": "window: the normal-operation error (worst case) stays inside the safety window; "
                                  "false trip: what the monitor sees in normal operation (its mismatch + the errors "
                                  "it can see) stays below its threshold; undetected deviation: a fault the monitor "
                                  "just does not detect (its threshold) plus its mismatch plus the errors it cannot "
                                  "see stays inside the safety window"}
    return out


def _judge(value, limit, complete: bool) -> str:
    if value is None or limit is None:
        return UNKNOWN
    if value > limit:
        return FAIL                                   # a partial sum can already fail
    return PASS if complete else UNKNOWN


def _fusa_row(fusa: dict, T: float, con: dict, items: list, complete: bool) -> dict:
    """The functional-safety view at one point - worst case throughout (no probability in a safety argument)."""
    sees = set(fusa.get("monitor_sees") or ())
    w = _window(fusa.get("torque_window"), T)
    thr = _window(fusa.get("monitor"), T)
    seen = sum(con[k] for k in items if k in sees and con[k] is not None)
    unseen = sum(con[k] for k in items if k not in sees and con[k] is not None)
    mm = con.get("monitor_mismatch")
    normal = seen + unseen
    mon_normal = seen + (mm or 0.0)
    undetected = None if thr is None else thr + (mm or 0.0) + unseen
    with_mm = complete and mm is not None
    return {"fusa_window_Nm": w, "fusa_error_Nm": normal, "fusa_window_status": _judge(normal, w, complete),
            "monitor_threshold_Nm": thr, "monitor_seen_Nm": mon_normal, "monitor_unseen_Nm": unseen,
            "monitor_mismatch_Nm": mm, "false_trip_status": _judge(mon_normal, thr, with_mm),
            "undetected_Nm": undetected, "undetected_status": _judge(undetected, w, with_mm),
            "undetected_margin_Nm": None if (undetected is None or w is None) else w - undetected}


def _declared(errors: dict, k: str) -> bool:
    keys = {"current_gain": ("current_gain_pct",), "current_offset": ("current_offset_A",),
            "resolver_offset": ("resolver_offset_deg_e",), "magnet_temperature": ("magnet_temp_dev_K",),
            "model_tolerance": ("psi_tol_pct", "Ld_tol_pct", "Lq_tol_pct"),
            "estimator": ("estimator_abs_Nm", "estimator_pct"),
            "monitor_mismatch": ("monitor_mismatch_abs_Nm", "monitor_mismatch_pct")}[k]
    return any(errors.get(x) is not None for x in keys)


def _agg(sts: list) -> str:
    sts = [s for s in sts if s]
    return FAIL if FAIL in sts else (UNKNOWN if (UNKNOWN in sts or not sts) else PASS)


def fusa_torque_limits(fault_sim: dict | None) -> dict | None:
    """The project's torque window (the TSR torque_window criterion) and torque monitor threshold, if declared."""
    if not fault_sim:
        return None
    win = mon = None
    src = []
    for t in (fault_sim.get("requirements") or {}).get("tsr") or []:
        c = t.get("criterion") or {}
        if c.get("type") == "torque_window":
            win = {"abs_Nm": c.get("abs_Nm"), "rel": c.get("rel")}
            src.append(f"{t.get('id')} torque window")
            break
    for m in fault_sim.get("mechanisms") or []:
        if m.get("kind") == "torque_monitor":
            p = m.get("params") or {}
            mon = {"abs_Nm": p.get("abs_Nm"), "rel": p.get("rel")}
            src.append(f"{m.get('id')} threshold")
            break
    if win is None and mon is None:
        return None
    sees, why = monitor_view(fault_sim)
    return {"torque_window": win, "monitor": mon, "source": ", ".join(src), "monitor_sees": sees,
            "monitor_sees_basis": why}


def monitor_view(fault_sim: dict) -> tuple:
    """Which control-path errors the torque monitor sees, from the sensor roles of the project's architecture: a
    monitor that reads the control's own current sensors (or angle) cannot see their errors; one with its own sensors
    sees the true current (its own sensors' errors are then its mismatch).  The model items are never seen - the
    monitor computes torque with the control's machine model."""
    from .faultsim.engine import ROLE_DEFAULTS
    roles = fault_sim.get("roles") or {}
    res = lambda r: roles.get(r) or roles.get(ROLE_DEFAULTS.get(r, ""), "")         # noqa: E731
    cur_same = all(res(f"current_mon_{x}") == res(f"current_{x}") for x in "ab")
    pos_same = res("position_monitor") == res("position_control")
    sees = ([] if cur_same else ["current_gain", "current_offset"]) + ([] if pos_same else ["resolver_offset"])
    why = ("monitor currents from " + ("the control's sensors" if cur_same else "its own sensors")
           + f" ({res('current_mon_a')}, {res('current_mon_b')}); monitor angle from "
           + ("the control's sensor" if pos_same else "its own sensor") + f" ({res('position_monitor')}); "
           "machine model shared with the control")
    return sees, why


# --------------------------------------------------------------------------------------------- other templates

def ftti_budget(timing_result: dict, combination: str = "worst_case") -> dict:
    """The FTTI chain as a budget: the counted items of the worst path (the timing analysis' own sum) vs the FTTI."""
    cs = [Contributor(it["id"], f"{it['from']} → {it['to']}", None if it.get("worst_s") is None else it["worst_s"] * 1e3,
                      "systematic", None, owner=str(it.get("owner", "")))
          for it in timing_result.get("items", []) if it.get("counted")]
    b = evaluate_budget(f"FTTI {timing_result.get('chain_id', '')}", "ms",
                        None if timing_result.get("ftti_s") is None else timing_result["ftti_s"] * 1e3, cs, combination,
                        "proportional")
    b["timing_worst_ms"] = None if timing_result.get("worst_s") is None else timing_result["worst_s"] * 1e3
    return b


def cycle_loss_budget(cycle_result: dict, allocations_Wh_per_km: dict, limit_Wh_per_km: float | None) -> dict:
    """The drive cycle's loss energy per component (Wh/km) against declared allocations and a total limit."""
    km = (cycle_result.get("cycle") or {}).get("distance_km") or 0.0
    L = cycle_result.get("losses_kWh") or {}
    cs = [Contributor(k, k.replace("_", " "), (v * 1e3 / km) if km > 0 and cycle_result.get("complete") else None,
                      "systematic", allocations_Wh_per_km.get(k)) for k, v in L.items()]
    return evaluate_budget(f"losses on {(cycle_result.get('cycle') or {}).get('name', '')}", "Wh/km", limit_Wh_per_km,
                           cs, "worst_case", "declared" if allocations_Wh_per_km else None)
