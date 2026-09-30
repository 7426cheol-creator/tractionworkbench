"""Safety requirements on the simulated trajectory: SG -> FSR -> TSR, judged on the PLANT TRUTH.

The monitors of the simulation are safety mechanisms (they read sensors); the requirements here are the judge (they
read the true torque, currents and voltages).  A requirement never passes because a mechanism fired, only because the
physical quantity stayed where the requirement allows it.

Requirement structure (project data):

* safety goal (SG): text, ASIL, FTTI, the hazard it addresses (``accel`` / ``decel`` torque, ``overvoltage``, ...)
  and optionally a vehicle-level criterion;
* functional safety requirement (FSR): the SG(s) it serves, the mechanisms allocated to it, the declared FDTI and
  FRTI budgets, and which TSR defines its physical safe condition;
* technical safety requirement (TSR): one criterion on one physical quantity with its allowed range, time origin,
  time window and hold / tolerance condition:

  ``torque_window``      the true shaft torque inside a dynamic window around the vehicle's request: the union
                         of [T - w, T + w] around every request value of the last ``delay_s`` (message period and
                         latency) and around the delayed request's first-order response (``response_tau_s``),
                         w = max(abs, rel |T|); with ``reduction_allowed`` any torque between zero and the request
                         is acceptable (loss of propulsion is an availability matter); side ``accel`` / ``decel`` /
                         ``both`` relative to the direction of motion; a violation is an excursion longer than
                         ``tolerance_s`` or an excess impulse above ``impulse_Nms``;
  ``bound``              a quantity (v_dc, |i_phase|, battery charging current, torque, speed) within [min, max],
                         optionally tolerated for ``tolerance_s``;
  ``safe_state``         after the origin (detection or fault) the conditions (|T| <= x, |i| <= y, v_dc <= z,
                         bridge state in a set) are reached within ``within_s`` and then hold for ``hold_s``;
  ``no_false_reaction``  without a fault, no mechanism detects and nothing reacts (availability / nuisance);
  ``timing``             the FSR's measured FDTI / FRTI / FHTI against its budgets and the SG's FTTI.

Event definitions (one trajectory):

  t_F  the injection time of the first PRIMARY fault (latent faults - a disabled mechanism, a lost path, a welded
       contactor - are injected earlier and wait for a demand);
  t_V  the first violation onset of a hazard-related TSR of the FSR at or after t_F;
  t_D  the first detection at or after t_F by a mechanism ALLOCATED to the FSR (a mechanism's trip, not its
       reaction);
  t_R  the first reaction actuation at or after t_D;
  t_S  the first instant at or after t_D from which the FSR's safe condition holds for its hold time;
  FDTI = t_D - t_F,  FRTI = t_S - t_D,  FHTI = t_S - t_F.

Verdicts carry their scope (``scenario``: this trajectory only), the evidence (times, values, sample resolution) and
the model dependencies.  A violation observed on the valid part of the trajectory is a FAIL even when the run later
leaves the model; the model's end before the window closes, a horizon too short for a hold, or a margin inside the
numerical / model allowance is UNKNOWN - never a PASS.  Inverter-level evidence is not a vehicle safety approval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ...errors import InputValidationError

_trapezoid = getattr(np, "trapezoid", None) or np.trapz      # numpy < 2.0 compatibility
CRITERIA = ("torque_window", "bound", "safe_state", "no_false_reaction", "timing")
QUANTITIES = {"v_dc": "V", "i_phase_abs": "A", "i_bat_charge": "A", "torque": "N*m", "torque_abs": "N*m",
              "speed": "rpm"}                     # torque = shaft torque (air gap minus rotational loss)
LATENT_KINDS = ("mechanism_disabled", "path_lost", "contactor_stuck")
PASS, FAIL, UNKNOWN, NA = "PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"
ORDER = {FAIL: 3, UNKNOWN: 2, PASS: 1, NA: 0}


@dataclass(frozen=True)
class SafetyGoal:
    sg_id: str
    text: str
    asil: str = ""
    ftti_s: float | None = None
    hazard: str = ""
    vehicle: dict | None = None       # {"max_delta_v_mps": ..} judged on the rigid-driveline indicator


@dataclass(frozen=True)
class FSR:
    fsr_id: str
    sg: tuple
    text: str
    mechanisms: tuple = ()
    fdti_budget_s: float | None = None
    frti_budget_s: float | None = None
    safe_state: str | None = None     # the TSR that defines the physical safe condition


@dataclass(frozen=True)
class TSR:
    tsr_id: str
    fsr: str
    text: str
    criterion: dict
    level: str = "inverter"           # inverter | component

    def __post_init__(self):
        c = self.criterion or {}
        if c.get("type") not in CRITERIA:
            raise InputValidationError(f"criterion type must be one of {CRITERIA}", field=f"tsr.{self.tsr_id}.type")
        if c["type"] == "bound" and c.get("quantity") not in QUANTITIES:
            raise InputValidationError(f"bound quantity must be one of {list(QUANTITIES)}",
                                       field=f"tsr.{self.tsr_id}.quantity")
        if c["type"] == "bound" and c.get("max") is None and c.get("min") is None:
            raise InputValidationError("a bound needs max and/or min", field=f"tsr.{self.tsr_id}")
        if c["type"] == "torque_window":
            if c.get("side", "both") not in ("accel", "decel", "both"):
                raise InputValidationError("side must be accel, decel or both", field=f"tsr.{self.tsr_id}.side")
            if c.get("abs_Nm") is None:
                raise InputValidationError("a torque window needs abs_Nm", field=f"tsr.{self.tsr_id}.abs_Nm")
        if c["type"] == "safe_state":
            if not c.get("conditions"):
                raise InputValidationError("a safe state needs its conditions", field=f"tsr.{self.tsr_id}.conditions")
            if c.get("origin", "detection") not in ("detection", "fault"):
                raise InputValidationError("safe-state origin must be detection or fault",
                                           field=f"tsr.{self.tsr_id}.origin")
        if self.level not in ("inverter", "component"):
            raise InputValidationError("level must be inverter or component", field=f"tsr.{self.tsr_id}.level")


@dataclass(frozen=True)
class RequirementSet:
    goals: tuple
    fsrs: tuple
    tsrs: tuple
    basis: str = ""

    def __post_init__(self):
        g = {x.sg_id for x in self.goals}
        f = {x.fsr_id for x in self.fsrs}
        for x in self.fsrs:
            for s in x.sg:
                if s not in g:
                    raise InputValidationError(f"FSR {x.fsr_id} names an undeclared safety goal {s}", field="fsr.sg")
        for x in self.tsrs:
            if x.fsr not in f:
                raise InputValidationError(f"TSR {x.tsr_id} names an undeclared FSR {x.fsr}", field="tsr.fsr")
        t = {x.tsr_id for x in self.tsrs}
        for x in self.fsrs:
            if x.safe_state is not None and x.safe_state not in t:
                raise InputValidationError(f"FSR {x.fsr_id} names an undeclared safe-state TSR {x.safe_state}",
                                           field="fsr.safe_state")

    def goal(self, i):
        return next(x for x in self.goals if x.sg_id == i)

    def fsr(self, i):
        return next(x for x in self.fsrs if x.fsr_id == i)

    def tsr(self, i):
        return next(x for x in self.tsrs if x.tsr_id == i)


def requirements_from_dict(d: dict) -> RequirementSet:
    ms = lambda v: None if v in (None, "") else float(v) * 1e-3          # noqa: E731
    goals = tuple(SafetyGoal(str(g["id"]), str(g.get("text", "")), str(g.get("asil", "")), ms(g.get("ftti_ms")),
                             str(g.get("hazard", "")), g.get("vehicle")) for g in d.get("safety_goals") or [])
    fsrs = tuple(FSR(str(f["id"]), tuple(f.get("sg") or ()), str(f.get("text", "")), tuple(f.get("mechanisms") or ()),
                     ms(f.get("fdti_budget_ms")), ms(f.get("frti_budget_ms")), f.get("safe_state"))
                 for f in d.get("fsr") or [])
    tsrs = []
    for t in d.get("tsr") or []:
        c = dict(t.get("criterion") or {})
        for k in list(c):
            if k.endswith("_ms"):                     # criterion times in ms in the data, s inside
                c[k[:-3] + "_s"] = None if c[k] is None else float(c[k]) * 1e-3
                del c[k]
        tsrs.append(TSR(str(t["id"]), str(t["fsr"]), str(t.get("text", "")), c, str(t.get("level", "inverter"))))
    return RequirementSet(goals, fsrs, tuple(tsrs), str(d.get("basis", "")))


# --------------------------------------------------------------------------------------------------- evaluation

def _worst(*vs):
    return max(vs, key=lambda v: ORDER[v]) if vs else NA


def _intervals(t, g):
    """Contiguous intervals where g > 0 on samples (t, g), with linearly interpolated crossing times."""
    out = []
    n = len(t)
    i = 0
    while i < n:
        if g[i] > 0:
            if i == 0:
                a = t[0]
            else:
                a = t[i - 1] + (t[i] - t[i - 1]) * (0 - g[i - 1]) / (g[i] - g[i - 1]) if g[i] != g[i - 1] else t[i]
            j = i
            while j + 1 < n and g[j + 1] > 0:
                j += 1
            if j + 1 < n:
                b = t[j] + (t[j + 1] - t[j]) * g[j] / (g[j] - g[j + 1]) if g[j] != g[j + 1] else t[j + 1]
                closed = True
            else:
                b, closed = t[j], False
            out.append((a, b, i, j, closed))
            i = j + 1
        else:
            i += 1
    return out


def _lagged(t, r, tau):
    if not tau or tau <= 0:
        return np.array(r, dtype=float)
    y = np.empty(len(r))
    y[0] = r[0]
    for k in range(1, len(r)):
        a = 1.0 - math.exp(-(t[k] - t[k - 1]) / tau)
        y[k] = y[k - 1] + a * (r[k] - y[k - 1])
    return y


def _sampling_allowance(x):
    """Bound on how far a smooth signal can exceed its samples between two samples: max |second difference| / 8
    (exact for a parabola; kinks sit on samples because events are located)."""
    if len(x) < 3:
        return 0.0
    d2 = np.abs(np.diff(np.asarray(x, dtype=float), 2))
    return float(np.max(d2)) / 8.0 if d2.size else 0.0


class Evaluator:
    """Judges one simulation result against a requirement set."""

    def __init__(self, result, reqs: RequirementSet, setup, model_allowance: dict | None = None):
        self.r, self.q, self.s = result, reqs, setup
        tr = result.trace
        self.t = np.asarray(tr["t"], dtype=float)
        self.tr = tr
        self.valid_end = float(self.t[-1]) if len(self.t) else 0.0
        self.completed = result.status == "completed"
        self.allow = dict(model_allowance or {})
        faults = [f for f in result.setup_echo.get("faults", [])]
        prim = [f for f in faults if f["kind"] not in LATENT_KINDS and f["t_s"] <= self.valid_end + 1e-12]
        self.t_F = min((f["t_s"] for f in prim), default=None)
        self.has_fault = bool(prim)
        self.detections = [e for e in result.events if e["kind"] == "detection"]
        self.actuations = [e for e in result.events if e["kind"] == "actuation"]
        self.bridge_changes = [e for e in result.events if e["kind"] == "bridge"]
        self.speed_sign = 1.0 if setup.speed_rpm >= 0 else -1.0
        self.h = float(setup.h_max_s)

    # -- signals -----------------------------------------------------------------------------------------------
    def quantity(self, name):
        tr = self.tr
        if name == "v_dc":
            return np.asarray(tr["v_dc"])
        if name == "i_phase_abs":
            return np.max(np.abs(np.vstack([tr["i_a"], tr["i_b"], tr["i_c"]])), axis=0)
        if name == "i_bat_charge":
            return -np.asarray(tr["i_bat"])
        if name == "torque":
            return np.asarray(tr["T_shaft"])
        if name == "torque_abs":
            return np.abs(np.asarray(tr["T_shaft"]))
        if name == "speed":
            return np.asarray(tr["speed_rpm"])
        raise InputValidationError(f"unknown quantity {name}", field="criterion.quantity")

    def window(self, c):
        """The dynamic allowed window on the samples: bands around every request value of the last ``delay_s``
        (the healthy drive may lag the intent by that pure delay: message period, latency) and around the
        delayed request filtered by the normal first-order response ``response_tau_s``."""
        req = self.s.request
        d = float(c.get("delay_s") or 0.0)
        t = self.t
        r = np.array([req(x) for x in t], dtype=float)
        if d > 0:
            bps = req.breakpoints()
            r_lo, r_hi = r.copy(), r.copy()
            for k, x in enumerate(t):
                vals = [r[k], req(max(0.0, x - d))]
                for b in bps:
                    if x - d < b <= x:
                        vals += [req(b - 1e-12), req(b)]
                r_lo[k], r_hi[k] = min(vals), max(vals)
            rd = np.array([req(max(0.0, x - d)) for x in t], dtype=float)
        else:
            r_lo = r_hi = rd = r
        lag = _lagged(t, rd, c.get("response_tau_s"))
        a, rel = float(c.get("abs_Nm", 0.0)), float(c.get("rel", 0.0))
        w_lo = np.maximum(a, rel * np.abs(r_lo))
        w_hi = np.maximum(a, rel * np.abs(r_hi))
        w_l = np.maximum(a, rel * np.abs(lag))
        lo, hi = np.minimum(r_lo - w_lo, lag - w_l), np.maximum(r_hi + w_hi, lag + w_l)
        if c.get("reduction_allowed", False):
            # any torque between zero and the request is acceptable: less propulsion or less braking than asked is
            # judged by availability requirements, not as unintended acceleration / deceleration
            lo, hi = np.minimum(lo, -a), np.maximum(hi, a)
        return lo, hi

    def origin(self, c, fsr: FSR | None = None):
        o = c.get("origin", "t0")
        if o == "t0":
            return 0.0
        if o == "fault":
            return self.t_F
        if o == "detection":
            return self.t_D(fsr)
        raise InputValidationError(f"unknown origin {o}", field="criterion.origin")

    def t_D(self, fsr: FSR | None):
        t0 = self.t_F if self.t_F is not None else 0.0
        allowed = set(fsr.mechanisms) if fsr is not None and fsr.mechanisms else None
        for e in self.detections:
            if e["t"] >= t0 - 1e-12 and (allowed is None or e["source"] in allowed):
                return e["t"]
        return None

    def t_R(self, after):
        for e in self.actuations:
            if after is not None and e["t"] >= after - 1e-12:
                return e["t"]
        return None

    def _mask(self, a, b):
        return (self.t >= a - 1e-15) & (self.t <= b + 1e-15)

    def _scope(self):
        s = self.s
        faults = "; ".join(f"{f['kind']} at {f['t_s'] * 1e3:.4g} ms {f.get('params') or ''}"
                           for f in self.r.setup_echo.get("faults", [])) or "no fault"
        return {"kind": "scenario",
                "text": f"this trajectory only: {s.speed_rpm:g} rpm, request {s.request.to_dict()['kind']} "
                        f"{s.request(0.0):g} N*m, Vdc(oc) {s.dc.V_oc:g} V, theta0 {math.degrees(s.theta0):.1f} deg, "
                        f"{s.pwm_model} PWM, faults: {faults}"}

    def _end_note(self, b):
        if not self.completed and self.valid_end < b - 1e-12:
            return f"the simulation left the model at {self.valid_end * 1e3:.4g} ms ({self.r.stop_reason})"
        if self.valid_end < b - 1e-12:
            return f"the horizon ends at {self.valid_end * 1e3:.4g} ms"
        return None

    # -- criteria ----------------------------------------------------------------------------------------------
    def eval_tsr(self, tsr: TSR) -> dict:
        c = tsr.criterion
        fsr = self.q.fsr(tsr.fsr)
        typ = c["type"]
        base = {"id": tsr.tsr_id, "fsr": tsr.fsr, "sg": list(fsr.sg), "text": tsr.text, "level": tsr.level,
                "type": typ, "criterion": c, "scope": self._scope()}
        if typ == "torque_window":
            return {**base, **self._torque_window(c)}
        if typ == "bound":
            return {**base, **self._bound(c)}
        if typ == "safe_state":
            return {**base, **self._safe_state(c, fsr)}
        if typ == "no_false_reaction":
            return {**base, **self._no_false_reaction()}
        return {**base, **self._timing(fsr)}

    def _span(self, c, fsr=None):
        o = self.origin(c, fsr)
        if o is None:
            return None, None
        a = o + float(c.get("start_s") or 0.0)
        e = c.get("end_s")
        b = o + float(e) if e is not None else self.s.horizon_s
        return a, b

    def _torque_window(self, c):
        a, b = self._span(c)
        if a is None:
            a, b = 0.0, self.s.horizon_s
        lo, hi = self.window(c)
        T = np.asarray(self.tr["T_shaft"])
        side = c.get("side", "both")
        acc = (T - hi) if self.speed_sign > 0 else (lo - T)
        dec = (lo - T) if self.speed_sign > 0 else (T - hi)
        g = acc if side == "accel" else dec if side == "decel" else np.maximum(acc, dec)
        m = self._mask(a, b)
        tt, gg = self.t[m], g[m]
        tol = float(c.get("tolerance_s") or 0.0)
        imp_lim = c.get("impulse_Nms")
        ivs = _intervals(tt, gg)
        worst = None
        fail_iv = None
        for (ta, tb, i, j, closed) in ivs:
            dur = tb - ta
            imp = float(_trapezoid(np.maximum(gg[i:j + 1], 0.0), tt[i:j + 1])) if j > i else 0.0
            exc = float(np.max(gg[i:j + 1]))
            item = {"start_s": ta, "end_s": tb, "duration_s": dur, "max_excess_Nm": exc, "impulse_Nms": imp,
                    "closed": closed}
            if worst is None or dur > worst["duration_s"]:
                worst = item
            over = dur > tol + 2 * self.h or (imp_lim is not None and imp > float(imp_lim))
            if over and fail_iv is None:
                fail_iv = item
        allowance = _sampling_allowance(T[m]) + float(self.allow.get("torque_Nm", 0.0))
        ev = {"window_s": [a, b], "excursions": len(ivs), "worst": worst, "sample_allowance_Nm": allowance,
              "max_excess_Nm": float(np.max(gg)) if gg.size else None}
        if fail_iv is not None:
            fail_iv["t_hazard_s"] = fail_iv["start_s"] + tol
            return {"verdict": FAIL, "t_violation_s": fail_iv["start_s"], "evidence": ev, "counter": fail_iv,
                    "detail": f"torque outside the allowed window for {fail_iv['duration_s'] * 1e3:.3g} ms from "
                              f"{fail_iv['start_s'] * 1e3:.4g} ms (tolerated {tol * 1e3:g} ms; max excess "
                              f"{fail_iv['max_excess_Nm']:.1f} N*m, impulse {fail_iv['impulse_Nms']:.3g} N*m*s)"}
        end = self._end_note(b)
        # an excursion still open at the end of the valid trace, or margins inside the allowance, stay undecided
        open_iv = [iv for iv in ivs if not iv[4]]
        near = gg.size and float(np.max(gg)) > -allowance
        if end or open_iv:
            why = end or "an excursion is still open at the end of the trace"
            return {"verdict": UNKNOWN, "evidence": ev, "reason": "WINDOW_NOT_OBSERVED",
                    "detail": f"no violation on the observed part, but {why} (window to {b * 1e3:.4g} ms)"}
        if near and ivs:
            pass
        if near and not ivs and float(np.max(gg)) > -allowance:
            return {"verdict": UNKNOWN, "evidence": ev, "reason": "WITHIN_NUMERICAL_ALLOWANCE",
                    "detail": f"the torque comes within {-float(np.max(gg)):.3g} N*m of the window edge, inside the "
                              f"sample / model allowance {allowance:.3g} N*m"}
        return {"verdict": PASS, "evidence": ev,
                "detail": (f"{len(ivs)} excursion(s), longest {worst['duration_s'] * 1e3:.3g} ms <= tolerated "
                           f"{tol * 1e3:g} ms" if ivs else "the torque stays inside the allowed window")}

    def _bound(self, c):
        a, b = self._span(c)
        if a is None:
            a, b = 0.0, self.s.horizon_s
        q = c["quantity"]
        y = self.quantity(q)
        m = self._mask(a, b)
        tt, yy = self.t[m], y[m]
        tol = float(c.get("tolerance_s") or 0.0)
        allowance = _sampling_allowance(yy) + float(self.allow.get(q, 0.0))
        unit = QUANTITIES[q]
        res = []
        for lim_key, sign in (("max", 1.0), ("min", -1.0)):
            lim = c.get(lim_key)
            if lim is None or not yy.size:
                continue
            g = sign * (yy - float(lim))
            ivs = _intervals(tt, g)
            k = int(np.argmax(g))
            peak = {"value": float(yy[k]), "t_s": float(tt[k]), "limit": float(lim), "kind": lim_key}
            bad = [iv for iv in ivs if iv[1] - iv[0] > tol + (2 * self.h if tol > 0 else -1)]
            res.append((lim_key, float(lim), g, ivs, bad, peak))
        ev = {"window_s": [a, b], "sample_allowance": allowance, "unit": unit,
              "extremes": [r[5] for r in res]}
        for lim_key, lim, g, ivs, bad, peak in res:
            if bad and float(np.max(g)) > allowance:
                iv = bad[0]
                return {"verdict": FAIL, "t_violation_s": iv[0], "evidence": ev,
                        "counter": {"start_s": iv[0], "end_s": iv[1], "peak": peak},
                        "detail": f"{q} {'above' if lim_key == 'max' else 'below'} {lim:g} {unit} from "
                                  f"{iv[0] * 1e3:.4g} ms (extreme {peak['value']:.4g} {unit} at "
                                  f"{peak['t_s'] * 1e3:.4g} ms)"}
        for lim_key, lim, g, ivs, bad, peak in res:
            if float(np.max(g)) > -allowance:
                return {"verdict": UNKNOWN, "evidence": ev, "reason": "WITHIN_NUMERICAL_ALLOWANCE",
                        "detail": f"{q} extreme {peak['value']:.5g} {unit} is within the sample / model allowance "
                                  f"{allowance:.3g} {unit} of the limit {lim:g} {unit}"}
        end = self._end_note(b)
        if end:
            return {"verdict": UNKNOWN, "evidence": ev, "reason": "WINDOW_NOT_OBSERVED",
                    "detail": f"inside the bound on the observed part, but {end} (window to {b * 1e3:.4g} ms)"}
        ext = "; ".join(f"{p['kind']} {p['value']:.5g} {unit} (limit {p['limit']:g})" for p in ev["extremes"])
        return {"verdict": PASS, "evidence": ev, "detail": f"inside the bound: {ext}"}

    def safe_condition(self, c):
        """Boolean array: the safe-state conditions hold at each sample."""
        ok = np.ones(len(self.t), dtype=bool)
        for cond in c.get("conditions") or []:
            q = cond["quantity"]
            if q == "bridge_in":
                allowed = {"asc_low": 1, "asc_high": 2, "six_switch_off": 3, "off": 4, "pwm": 0}
                codes = [allowed[v] for v in cond["values"]]
                ok &= np.isin(np.asarray(self.tr["bridge"]).astype(int), codes)
                continue
            y = self.quantity(q)
            if cond.get("max") is not None:
                ok &= y <= float(cond["max"])
            if cond.get("min") is not None:
                ok &= y >= float(cond["min"])
        return ok

    def t_S(self, c, after):
        """First instant >= after from which the safe condition holds for hold_s (None if not in the trace)."""
        hold = float(c.get("hold_s") or 0.0)
        ok = self.safe_condition(c)
        t = self.t
        n = len(t)
        i = int(np.searchsorted(t, after - 1e-15))
        while i < n:
            if ok[i]:
                j = i
                while j + 1 < n and ok[j + 1]:
                    j += 1
                if t[j] - t[i] >= hold - 1e-12 or (j == n - 1 and False):
                    return float(t[i]), float(t[j]), True
                if j == n - 1:
                    return float(t[i]), float(t[j]), False      # holding at the end, hold not yet observed
                i = j + 1
            else:
                i += 1
        return None, None, False

    def _safe_state(self, c, fsr):
        o = self.origin(c, fsr)
        if o is None:
            if not self.has_fault:
                return {"verdict": NA, "detail": "no fault in this scenario: no safe state is demanded"}
            return {"verdict": NA, "reason": "NOT_TRIGGERED",
                    "detail": "no detection by an allocated mechanism: no safe state was demanded (whether one "
                              "was needed is judged by the FSR's timing and hazard requirements)"}
        within = float(c.get("within_s") or math.inf)
        hold = float(c.get("hold_s") or 0.0)
        tS, t_last, held = self.t_S(c, o)
        ev = {"origin_s": o, "within_s": within, "hold_s": hold, "t_safe_s": tS}
        if tS is not None and held and tS - o <= within + 1e-12:
            return {"verdict": PASS, "evidence": ev, "t_safe_s": tS,
                    "detail": f"safe condition from {tS * 1e3:.4g} ms ({(tS - o) * 1e3:.3g} ms after the origin, "
                              f"<= {within * 1e3:g} ms) and held {hold * 1e3:g} ms"}
        deadline = o + within
        observed_to = self.valid_end
        if tS is not None and not held and tS - o <= within + 1e-12:
            return {"verdict": UNKNOWN, "evidence": ev, "reason": "HOLD_NOT_OBSERVED", "t_safe_s": tS,
                    "detail": f"safe condition reached at {tS * 1e3:.4g} ms but the trace ends before the "
                              f"{hold * 1e3:g} ms hold is observed" + (f" ({self.r.stop_reason})"
                                                                      if not self.completed else "")}
        if observed_to + 1e-12 >= deadline + hold:
            first = tS if tS is not None else None
            return {"verdict": FAIL, "evidence": ev, "t_violation_s": deadline,
                    "counter": {"deadline_s": deadline, "first_safe_s": first},
                    "detail": f"the safe condition is not reached and held within {within * 1e3:g} ms of the origin "
                              f"at {o * 1e3:.4g} ms" + (f" (first reached {first * 1e3:.4g} ms)" if first else "")}
        return {"verdict": UNKNOWN, "evidence": ev, "reason": "WINDOW_NOT_OBSERVED",
                "detail": f"not reached on the observed part; {self._end_note(deadline + hold) or 'window open'}"}

    def _no_false_reaction(self):
        if self.has_fault:
            return {"verdict": NA, "detail": "a fault is injected: this availability requirement applies to "
                                             "fault-free scenarios"}
        if self.detections or self.actuations:
            e = (self.detections or self.actuations)[0]
            return {"verdict": FAIL, "t_violation_s": e["t"], "counter": e,
                    "detail": f"false detection without a fault: {e['source']} at {e['t'] * 1e3:.4g} ms - {e['text']}"}
        end = self._end_note(self.s.horizon_s)
        if end:
            return {"verdict": UNKNOWN, "reason": "WINDOW_NOT_OBSERVED", "detail": f"no reaction, but {end}"}
        return {"verdict": PASS, "detail": "no detection and no reaction over the horizon (this trajectory)"}

    def _timing(self, fsr: FSR):
        tl = self.fsr_timeline(fsr)
        out, verdicts = [], []
        for key, budget in (("FDTI", fsr.fdti_budget_s), ("FRTI", fsr.frti_budget_s)):
            v = tl.get(key)
            if budget is None:
                continue
            if v is None:
                verdicts.append(UNKNOWN if tl["need"] else NA)
                out.append(f"{key} not measured ({tl['why']})")
            else:
                ok = v <= budget + 1e-12
                if abs(v - budget) <= 2 * self.h:
                    verdicts.append(UNKNOWN)
                    out.append(f"{key} {v * 1e3:.4g} ms within the sample resolution of the budget {budget * 1e3:g} ms")
                else:
                    verdicts.append(PASS if ok else FAIL)
                    out.append(f"{key} {v * 1e3:.4g} ms {'<=' if ok else '>'} budget {budget * 1e3:g} ms")
        ftti = [self.q.goal(g).ftti_s for g in fsr.sg if self.q.goal(g).ftti_s]
        if ftti:
            F = min(ftti)
            v = tl.get("FHTI")
            if v is None:
                if tl["need"] and tl.get("undetected"):
                    verdicts.append(FAIL)
                    out.append(f"not handled: {tl['why']}")
                else:
                    verdicts.append(UNKNOWN if tl["need"] else NA)
                    out.append(f"FHTI not measured ({tl['why']})")
            else:
                ok = v <= F + 1e-12
                verdicts.append(PASS if ok else FAIL)
                out.append(f"FHTI {v * 1e3:.4g} ms {'<=' if ok else '>'} FTTI {F * 1e3:g} ms")
        v = _worst(*verdicts) if verdicts else NA
        return {"verdict": v, "timeline": tl, "detail": "; ".join(out) or tl["why"]}

    def fsr_timeline(self, fsr: FSR) -> dict:
        """t_F, t_V, t_D, t_R, t_S and FDTI / FRTI / FHTI of one FSR on this trajectory."""
        tl = {"t_F": self.t_F, "t_V": None, "t_D": None, "t_R": None, "t_S": None, "FDTI": None, "FRTI": None,
              "FHTI": None, "need": False, "why": "", "undetected": False}
        if self.t_F is None:
            tl["why"] = "no primary fault in this scenario"
            return tl
        hz = [t for t in self.q.tsrs if t.fsr == fsr.fsr_id and t.criterion["type"] in ("torque_window", "bound")]
        tv = []
        for t in hz:
            r = self.eval_tsr(t)
            if r.get("t_violation_s") is not None:
                tv.append(r["t_violation_s"])
        tl["t_V"] = min(tv) if tv else None
        tD = self.t_D(fsr)
        tl["t_D"] = tD
        tl["need"] = tl["t_V"] is not None or tD is not None
        if tD is None:
            if tl["t_V"] is not None:
                tl["undetected"] = True
                tl["why"] = (f"a hazard-related requirement is violated from {tl['t_V'] * 1e3:.4g} ms and no "
                             f"allocated mechanism ({', '.join(fsr.mechanisms) or 'any'}) detected the fault")
            else:
                tl["why"] = "no violation and no detection: the fault needs no handling by this FSR here"
            return tl
        tl["FDTI"] = max(0.0, tD - self.t_F)            # a detection at the fault instant (rounding) is 0
        tl["t_R"] = self.t_R(tD)
        if fsr.safe_state is None:
            tl["why"] = "no safe-state requirement declared for this FSR (FRTI needs a physical safe condition)"
            return tl
        c = self.q.tsr(fsr.safe_state).criterion
        tS, _last, held = self.t_S(c, tD)
        if tS is not None and held:
            tl["t_S"] = tS
            tl["FRTI"] = max(0.0, tS - tD)
            tl["FHTI"] = max(0.0, tS - self.t_F)
            tl["why"] = "measured"
        else:
            tl["why"] = ("safe condition not reached and held in the trace" + (f" ({self.r.stop_reason})"
                                                                              if not self.completed else ""))
        return tl

    # -- the whole set -----------------------------------------------------------------------------------------
    def evaluate(self) -> dict:
        tsr = [self.eval_tsr(t) for t in self.q.tsrs]
        by_fsr = {}
        for r in tsr:
            by_fsr.setdefault(r["fsr"], []).append(r)
        fsr = []
        for f in self.q.fsrs:
            items = by_fsr.get(f.fsr_id, [])
            tl = self.fsr_timeline(f)
            v = _worst(*[r["verdict"] for r in items]) if items else NA
            fsr.append({"id": f.fsr_id, "sg": list(f.sg), "text": f.text, "mechanisms": list(f.mechanisms),
                        "verdict": v, "timeline": tl, "tsr": [r["id"] for r in items],
                        "budgets": {"FDTI_s": f.fdti_budget_s, "FRTI_s": f.frti_budget_s}})
        sg = []
        for g in self.q.goals:
            fs = [x for x in fsr if g.sg_id in x["sg"]]
            v = _worst(*[x["verdict"] for x in fs]) if fs else NA
            ind = self.vehicle_indicator(g)
            sg.append({"id": g.sg_id, "text": g.text, "asil": g.asil, "ftti_s": g.ftti_s, "hazard": g.hazard,
                       "inverter_evidence": v, "fsr": [x["id"] for x in fs], "vehicle": ind,
                       "statement": "inverter-level evidence on this trajectory - not a vehicle safety approval "
                                    "(vehicle dynamics, driver controllability and the item's other elements are "
                                    "outside this simulation)"})
        return {"tsr": tsr, "fsr": fsr, "sg": sg, "scope": self._scope(),
                "event_definitions": {
                    "t_F": "injection of the first primary fault",
                    "t_V": "first violation onset of a hazard-related TSR of the FSR",
                    "t_D": "first detection by a mechanism allocated to the FSR at or after t_F",
                    "t_R": "first reaction actuation at or after t_D",
                    "t_S": "first instant from which the FSR's safe condition holds for its hold time",
                    "FDTI": "t_D - t_F", "FRTI": "t_S - t_D", "FHTI": "t_S - t_F"},
                "sample_step_s": self.h}

    def vehicle_indicator(self, g: SafetyGoal):
        veh = getattr(self.s, "vehicle", None)
        if not veh or g.hazard not in ("accel", "decel"):
            return {"status": "NOT_EVALUATED",
                    "detail": "no vehicle equivalent declared" if not veh else "not a torque hazard"}
        m_eq, ratio, r = veh["m_eq_kg"], veh["ratio"], veh["wheel_radius_m"]
        err = np.asarray(self.tr["T_shaft"]) - np.asarray(self.tr["T_request"])
        acc = err * ratio / (r * m_eq) * self.speed_sign
        a = acc if g.hazard == "accel" else -acc
        dv = float(_trapezoid(np.maximum(a, 0.0), self.t)) if len(self.t) > 1 else 0.0
        out = {"status": "INDICATOR", "max_accel_error_mps2": float(np.max(a)) if a.size else 0.0,
               "delta_v_mps": dv, "basis": veh.get("basis", ""),
               "detail": "rigid-driveline equivalent (no shaft compliance, tyre slip or vehicle dynamics): an "
                         "indicator, not a vehicle-level verdict"}
        crit = g.vehicle or {}
        if crit.get("max_delta_v_mps") is not None:
            lim = float(crit["max_delta_v_mps"])
            out["criterion"] = f"delta v <= {lim:g} m/s (declared)"
            out["indicator_verdict"] = PASS if dv <= lim else FAIL
        return out


def evaluate(result, reqs: RequirementSet, setup, model_allowance: dict | None = None) -> dict:
    return Evaluator(result, reqs, setup, model_allowance).evaluate()
