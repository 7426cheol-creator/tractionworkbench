"""The check library of the reference verification: one named, machine-checkable judgement per requirement aspect.

Every check reads what the runner gives it (simulated trajectories of the declared scenarios, the project's design
data, the package itself) and returns

    {"verdict": PASS | FAIL | UNKNOWN | CONFLICT | NOT_APPLICABLE | MANUAL,
     "measured": {...}, "expected": "...", "reasons": [...], "evidence": {...}}

A check never turns an undeclared (OPEN) value into a number: the quantity is measured and reported, the verdict is
UNKNOWN with the parameter named.  A check that demonstrates a stated counterexample or blind spot (``expect`` says
so) passes when the counterexample is reproduced.  Physical checks judge the plant truth, never a command or a flag.
"""

from __future__ import annotations

import math
import re

import numpy as np

from ...errors import InputValidationError
from .safestate import (FAIL, NA, PASS, UNKNOWN, excess_impulse, fault_timeline, judge_safe_state,
                        time_to_window_hold)

CONFLICT, MANUAL = "CONFLICT", "MANUAL"
ORDER = {FAIL: 5, CONFLICT: 4, UNKNOWN: 3, MANUAL: 3, PASS: 1, NA: 0}
CHECKS: dict = {}


def check(name):
    def deco(fn):
        CHECKS[name] = fn
        return fn
    return deco


def worst(verdicts) -> str:
    vs = [v for v in verdicts if v is not None]
    return max(vs, key=lambda v: ORDER.get(v, 3)) if vs else NA


def _ms(v):
    return None if v is None else float(v) * 1e-3


def _fmt_ms(s):
    return "-" if s is None else f"{s * 1e3:.4g} ms"


class Params:
    """The resolved parameters of one check (a value, or None for an OPEN reference - its name is remembered)."""

    def __init__(self, raw: dict, resolved: dict, open_refs: dict, illustrative: set):
        self.raw, self.v, self.open, self.illustrative = raw, resolved, open_refs, illustrative

    def get(self, key, default=None):
        return self.v.get(key, default) if key in self.v else default

    def opened(self, key) -> str | None:
        return self.open.get(key)

    def has(self, key) -> bool:
        return key in self.raw


def _res(verdict, measured=None, expected="", reasons=None, evidence=None):
    return {"verdict": verdict, "measured": measured or {}, "expected": expected, "reasons": list(reasons or ()),
            "evidence": evidence or {}}


def _budget(label, value_s, limit_s, open_name):
    """(verdict, reason) of one measured time against a budget (None + OPEN name: UNKNOWN)."""
    if value_s is None:
        return FAIL, f"{label}: the instant does not occur in the trajectory"
    if limit_s is None:
        if open_name:
            return UNKNOWN, f"{label} {value_s * 1e3:.4g} ms: passes if {open_name} >= {value_s * 1e3:.4g} ms (OPEN)"
        return None, f"{label} {value_s * 1e3:.4g} ms (no budget)"
    ok = value_s <= limit_s + 1e-12
    return (PASS if ok else FAIL), f"{label} {value_s * 1e3:.4g} ms {'<=' if ok else '>'} {limit_s * 1e3:g} ms"


# ------------------------------------------------------------------------------------------------- origins

def origin_time(run, spec, tl=None) -> float | None:
    """The time origin of a check: fault | criterion | detect | gate | terminal | t0 | <ms> | input:<signal>=<v> |
    event:<kind>[:<source or text>] (an empty kind matches any kind)."""
    o = spec if isinstance(spec, (int, float)) else (spec or "gate")
    if isinstance(o, (int, float)):
        return float(o) * 1e-3
    tl = tl or fault_timeline(run.result)
    if o in ("fault", "criterion", "detect", "gate", "terminal"):
        return {"fault": tl["t_fault"], "criterion": tl["t_criterion"], "detect": tl["t_detect"],
                "gate": tl["t_gate_applied"], "terminal": tl["t_terminal"]}[o]
    if o == "t0":
        return 0.0
    if o.startswith("input:"):
        sig, _, val = o[6:].partition("=")
        for e in run.result.events:
            if e["kind"] == "input" and e["source"] == sig and (not val or str(e.get("value")) in (val, str(val))):
                return e["t"]
        return None
    if o.startswith("event:"):
        kind, _, text = o[6:].partition(":")           # an empty kind: an event of any kind
        for e in run.result.events:
            if (not kind or e["kind"] == kind) and (not text or text in e["text"] or text == e["source"]):
                return e["t"]
        return None
    raise ValueError(f"unknown origin {o!r}")


# ------------------------------------------------------------------------------------------------- physical checks

def _tols(P):
    """The judge's tolerances: a declared value, 0 (the strictest) for an OPEN one, or the default when absent."""
    out, opened = [], []
    for key, default in (("torque_tol_Nm", 5.0), ("power_tol_W", 500.0)):
        if P.opened(key):
            out.append(0.0)
            opened.append(P.opened(key))
        else:
            v = P.get(key, default)
            out.append(float(default if v is None else v))
    return out[0], out[1], opened


@check("safe_state")
def c_safe_state(ctx, spec, P: Params):
    """C1..C4 after the origin.  An OPEN tolerance is judged at zero (the strictest): a PASS then holds for every
    value of it; otherwise the break-even tolerance is reported and the verdict is UNKNOWN - never a guess."""
    run = ctx.run(spec)
    tl = fault_timeline(run.result)
    o = origin_time(run, P.get("origin", "gate"), tl)
    tT, tP, open_tol = _tols(P)
    until = origin_time(run, P.get("to"), tl) if P.get("to") is not None else None
    deadline = _ms(P.get("deadline_ms"))
    if deadline is None and P.get("deadline_us") is not None:           # a hardware budget stated in microseconds
        deadline = float(P.get("deadline_us")) * 1e-6
    kw = dict(t_min_Nm=P.get("tlsr_min_Nm"), t_max_Nm=P.get("tlsr_max_Nm"),
              transition_s=_ms(P.get("transition_ms")) or 0.0, hold_s=_ms(P.get("hold_ms")) or 0.0,
              deadline_s=deadline, deadline_open=P.opened("deadline_ms") or P.opened("deadline_us"), until_s=until)
    if P.get("conditions"):
        # an obligation that is one condition of the safe state only (e.g. no torque by active pulsing: C3)
        kw["conditions"] = tuple(P.get("conditions"))
    j = judge_safe_state(run.result, o, torque_tol_Nm=tT, power_tol_W=tP, **kw)
    reasons = list(j["reasons"])
    for k in ("tlsr_min_Nm", "tlsr_max_Nm", "transition_ms"):
        if k != "transition_ms" and P.get("conditions") and "C4" not in P.get("conditions"):
            continue
        if P.opened(k):
            reasons.append(f"{P.opened(k)} OPEN" + (" (judged without a transition allowance)"
                                                    if k == "transition_ms" else " (C4 not judged)"))
    v = j["verdict"]
    if open_tol:
        if v == PASS:
            reasons.append(f"holds for every value of {', '.join(open_tol)} (judged at zero tolerance)")
        else:
            viol = [i for i in j["conditions"].values() if i.get("violated")]
            T_be = max((i["peak_torque_Nm"] for i in viol), default=0.0)
            P_be = max((i["peak_power_W"] for i in viol), default=0.0)
            relaxed = judge_safe_state(run.result, o, torque_tol_Nm=T_be * (1 + 1e-9) + 1e-9,
                                       power_tol_W=P_be * (1 + 1e-9) + 1e-9, **kw)
            reasons.append(f"at zero tolerance {v}; it passes C1-C3 only with a torque tolerance >= {T_be:.3g} N*m "
                           f"(or a power tolerance >= {P_be:.3g} W for C1 / C2) - {', '.join(open_tol)} OPEN "
                           f"(relaxed: {relaxed['verdict']})")
            v = UNKNOWN
    meas = {"reached_after_ms": None if j["reached_after_s"] is None else j["reached_after_s"] * 1e3,
            "origin_ms": None if o is None else o * 1e3}
    for c, info in j["conditions"].items():
        if "violated" in info:
            meas[f"{c}_violation_ms"] = info["duration_s"] * 1e3
            meas[f"{c}_peak_Nm"] = info["peak_torque_Nm"]
    exp = P.get("expect_reached", True)
    if exp is False:
        # a counterexample demonstration: the stated reaction does NOT reach the safe state here
        failed = [c for c, i in j["conditions"].items() if i.get("verdict") == FAIL]
        if j["verdict"] == FAIL and not open_tol:
            v = PASS
            reasons.insert(0, "counterexample reproduced: " + ", ".join(failed))
        elif j["verdict"] == FAIL:
            v = UNKNOWN
            reasons.insert(0, "counterexample at zero tolerance (" + ", ".join(failed) + "); its size is above")
        else:
            v = FAIL if j["verdict"] == PASS else UNKNOWN
            reasons.insert(0, "the stated counterexample is not reproduced (the safe state is reached)")
    return _res(v, meas, "C1-C4 reached after the origin and held" if exp is not False else
                "the safe state is NOT reached (counterexample)", reasons,
                {"timeline": tl, "conditions": j["conditions"], "run": run.key, "judge": j.get("params"),
                 "origin_s": j.get("origin_s"), "window_from_s": j.get("window_from_s"), "t_safe": j.get("t_safe")})


@check("timeline")
def c_timeline(ctx, spec, P: Params):
    run = ctx.run(spec)
    tT, tP, open_tol = _tols(P)
    tl = fault_timeline(run.result, P.get("mechanisms"), torque_tol_Nm=tT, power_tol_W=tP,
                        t_min_Nm=P.get("tlsr_min_Nm"), t_max_Nm=P.get("tlsr_max_Nm"),
                        hold_s=_ms(P.get("hold_ms")) or 0.0)
    tf, tc, td, tg, ts = tl["t_fault"], tl["t_criterion"], tl["t_detect"], tl["t_gate_applied"], tl["t_physical_safe"]
    meas = {k: (None if tl[k] is None else tl[k] * 1e3) for k in ("t_fault", "t_criterion", "t_detect",
                                                                  "t_reaction_req", "t_gate_applied", "t_terminal",
                                                                  "t_physical_safe")}
    verdicts, reasons = [], []
    d = lambda a, b: None if (a is None or b is None) else b - a          # noqa: E731
    for label, val, key in (("FDTI (fault -> detect)", d(tf, td), "fdti_ms"),
                            ("FRTI (detect -> physical safe)", d(td, ts), "frti_ms"),
                            ("FTTI (fault -> physical safe)", d(tf, ts), "ftti_ms"),
                            ("criterion -> physical safe", d(tc, ts), "shutoff_ms"),
                            ("detect -> gate applied", d(td, tg), "gate_ms")):
        if not P.has(key):
            continue
        v, r = _budget(label, val, _ms(P.get(key)), P.opened(key))
        verdicts.append(v)
        reasons.append(r)
    if P.get("expect_recorded", False):
        missing = [k for k in ("t_fault", "t_criterion", "t_detect", "t_reaction_req", "t_gate_applied",
                               "t_physical_safe") if tl[k] is None]
        verdicts.append(FAIL if missing else PASS)
        reasons.append("all six instants recorded on one clock" if not missing else f"missing: {', '.join(missing)}")
    if tl["safe_state"].get("verdict") == FAIL:
        verdicts.append(UNKNOWN if open_tol else FAIL)
        reasons.append("the physical safe state is not reached" + (f" at zero tolerance ({', '.join(open_tol)} OPEN)"
                                                                   if open_tol else ""))
    elif open_tol:
        reasons.append(f"physical safe state judged at zero tolerance ({', '.join(open_tol)} OPEN)")
    return _res(worst(verdicts) if verdicts else PASS, meas, "fault -> criterion -> detect -> request -> gate -> "
                "physical safe state on one clock", reasons, {"timeline": tl, "run": run.key})


@check("no_detection")
def c_no_detection(ctx, spec, P):
    run = ctx.run(spec)
    ign = tuple(P.get("ignore", ()))
    det = [e for e in run.result.events if e["kind"] in ("detection", "actuation")
           and not str(e["source"]).startswith("SYS:") and e["source"] not in ign]
    return _res(PASS if not det else FAIL, {"detections": len(det)}, "no detection and no reaction",
                [f"{e['kind']} {e['source']} at {e['t'] * 1e3:.4g} ms: {e['text']}" for e in det[:5]] or
                ["no detection, no reaction"], {"run": run.key})


@check("detected")
def c_detected(ctx, spec, P):
    run = ctx.run(spec)
    by = P.get("by")
    by = (by,) if isinstance(by, str) else tuple(by or ())
    tl = fault_timeline(run.result)
    t0 = origin_time(run, P.get("origin", "fault"), tl) or 0.0
    det = [e for e in run.result.events if e["kind"] == "detection" and e["t"] >= t0 - 1e-12
           and (not by or e["source"] in by or e.get("mech_kind") in by)]
    first = det[0] if det else None
    exp = P.get("expect", True)
    meas = {"detected": bool(first), "by": first["source"] if first else None,
            "after_ms": (first["t"] - t0) * 1e3 if first else None}
    if exp is False:
        return _res(PASS if not first else FAIL, meas, f"NOT detected by {', '.join(by) or 'any mechanism'} (a "
                    "stated blind spot)", ["blind spot reproduced: no detection" if not first else
                                           f"detected by {first['source']} at {first['t'] * 1e3:.4g} ms"],
                    {"run": run.key})
    if not first:
        return _res(FAIL, meas, f"detected by {', '.join(by) or 'a mechanism'}", ["no detection"], {"run": run.key})
    v, r = _budget("detection after the origin", first["t"] - t0, _ms(P.get("within_ms")), P.opened("within_ms"))
    return _res(v or PASS, meas, f"detected by {', '.join(by) or 'a mechanism'}", [r, first["text"]],
                {"run": run.key})


_QTY = {"torque": "T_shaft", "torque_em": "T_em", "v_dc": "v_dc", "speed": "speed_rpm", "i_bat": "i_bat"}


def quantity(tr, name):
    if name in _QTY:
        return np.asarray(tr[_QTY[name]], float)
    if name == "i_phase_abs":
        return np.max(np.abs(np.vstack([tr["i_a"], tr["i_b"], tr["i_c"]])), axis=0)
    if name == "torque_abs":
        return np.abs(np.asarray(tr["T_shaft"], float))
    if name == "p_dc":
        return np.asarray(tr["v_dc"], float) * np.nan_to_num(np.asarray(tr["i_dc"], float))
    if name in ("est_error", "est_error_abs"):
        # the monitor's torque estimate against the true shaft (transmission-side) torque
        e = np.asarray(tr["T_est_mon"], float) - np.asarray(tr["T_shaft"], float)
        return np.abs(e) if name == "est_error_abs" else e
    if name in tr:
        return np.asarray(tr[name], float)
    raise KeyError(name)


@check("bound")
def c_bound(ctx, spec, P):
    run = ctx.run(spec)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    q = quantity(tr, P.get("quantity"))
    a = origin_time(run, P.get("from", "t0"))
    b = origin_time(run, P.get("to")) if P.get("to") is not None else float(t[-1])
    if a is None:
        return _res(NA, {}, "", ["the window's origin does not occur"])
    m = (t >= a - 1e-15) & (t <= b + 1e-15)
    qm = q[m]
    lo, hi = P.get("min"), P.get("max")
    meas = {"max": float(np.nanmax(qm)) if qm.size else None, "min": float(np.nanmin(qm)) if qm.size else None}
    verdicts, reasons = [], []
    for lim, name, key, op in ((hi, "max", "max", "le"), (lo, "min", "min", "ge")):
        if not P.has(key):
            continue
        if lim is None:
            if name == "max" and meas[name] is not None and meas[name] <= 1e-9:
                verdicts.append(PASS)
                reasons.append(f"{P.get('quantity')} max {meas[name]:.4g}: within every non-negative "
                               f"{P.opened(key)}")
            else:
                verdicts.append(UNKNOWN)
                reasons.append(f"{P.get('quantity')} {name} {meas[name]:.4g}: passes if {P.opened(key)} "
                               f"{'>=' if name == 'max' else '<='} {meas[name]:.4g} (OPEN)")
            continue
        ok = (meas[name] <= lim + 1e-9) if op == "le" else (meas[name] >= lim - 1e-9)
        verdicts.append(PASS if ok else FAIL)
        reasons.append(f"{P.get('quantity')} {name} {meas[name]:.4g} {'within' if ok else 'beyond'} {lim:g}")
    if P.get("expect_stop"):
        stopped = run.result.status != "completed"
        return _res(PASS if stopped else FAIL, meas, "the run leaves the model (uncontrolled)",
                    [f"left the model: {run.result.stop_reason}" if stopped else "the run stayed in the model"],
                    {"run": run.key})
    if run.result.status != "completed":
        reasons.append(f"the run left the model: {run.result.stop_reason}")
        verdicts.append(UNKNOWN if FAIL not in verdicts else FAIL)
    v = worst(verdicts)
    if P.get("expect_violation"):
        reasons.insert(0, "counterexample reproduced: the bound is violated" if v == FAIL else
                       "the expected violation does not occur" if v == PASS else
                       "whether the bound is violated depends on the OPEN limit")
        v = PASS if v == FAIL else (FAIL if v == PASS else v)
    return _res(v, meas, f"{P.get('quantity')} within [{lo}, {hi}]" + (" violated (counterexample)" if
                P.get("expect_violation") else ""), reasons, {"run": run.key})


@check("hw_timing")
def c_hw_timing(ctx, spec, P):
    """A fast hardware comparator: from the true threshold crossing to the detection, and to the gate state."""
    run = ctx.run(spec)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    q = quantity(tr, P.get("quantity"))
    thr = P.get("threshold")
    if thr is None:
        # the comparator's own threshold in this run (a variant may move it: threshold tolerance)
        m = next((m for m in run.setup.mechanisms if m.mech_id == P.get("mech")), None)
        thr = None if m is None else next((m.params[k] for k in ("threshold_A", "threshold_V") if k in m.params), None)
        if thr is None:
            return _res(UNKNOWN, {}, "", [f"no threshold declared and none found on {P.get('mech')}"], {"run": run.key})
    thr = float(thr)
    k = np.where(q > thr)[0]
    t_cross = float(t[k[0]]) if len(k) else None
    mech = P.get("mech")
    det = next((e for e in run.result.events if e["kind"] == "detection" and e["source"] == mech), None)
    tl = fault_timeline(run.result, [mech] if mech else None)
    verdicts, reasons = [], []
    meas = {"t_cross_ms": None if t_cross is None else t_cross * 1e3,
            "t_detect_ms": det["t"] * 1e3 if det else None,
            "t_gate_ms": None if tl["t_gate_applied"] is None else tl["t_gate_applied"] * 1e3}
    if t_cross is None:
        return _res(NA, meas, "", ["the quantity never crosses the threshold"], {"run": run.key})
    if det is None:
        return _res(FAIL, meas, "detected", [f"{mech} did not detect"], {"run": run.key})
    meas["detect_us"] = (det["t"] - t_cross) * 1e6
    if P.has("detect_max_us"):
        lim = P.get("detect_max_us")
        v, r = _budget("detection after the crossing", det["t"] - t_cross, None if lim is None else lim * 1e-6,
                       P.opened("detect_max_us"))
        verdicts.append(v)
        reasons.append(r.replace(" ms", " ms"))
    if P.has("react_max_us"):
        if P.get("no_reaction_expected"):
            verdicts.append(PASS)
        else:
            lim = P.get("react_max_us")
            tg = tl["t_gate_applied"]
            meas["react_us"] = None if tg is None else (tg - det["t"]) * 1e6
            v, r = _budget("gate applied after the detection", None if tg is None else tg - det["t"],
                           None if lim is None else lim * 1e-6, P.opened("react_max_us"))
            verdicts.append(v)
            reasons.append(r)
    return _res(worst(verdicts), meas, "hardware detection / reaction within the stated us", reasons,
                {"run": run.key, "timeline": tl})


# ------------------------------------------------------------------------------------------------- system checks

def _sys(run):
    return run.result.summary.get("system") or {}


@check("opstate")
def c_opstate(ctx, spec, P):
    run = ctx.run(spec)
    sy = _sys(run)
    tr = sy.get("transitions", [])
    seq = [(x["from"], x["to"]) for x in tr]
    want = [tuple(x) for x in P.get("sequence", ())]
    verdicts, reasons = [], []
    it = iter(seq)
    ok_seq = all(any(w == s for s in it) for w in want)
    if want:
        verdicts.append(PASS if ok_seq else FAIL)
        reasons.append(("transitions in order: " if ok_seq else "expected transitions missing / out of order: ")
                       + " -> ".join(f"{a}->{b}" for a, b in want))
    for a_, b_ in [tuple(x) for x in P.get("absent", ())]:
        hit = next((x for x in tr if x["from"] == a_ and x["to"] == b_), None)
        verdicts.append(PASS if hit is None else FAIL)
        reasons.append(f"{a_} -> {b_} does not occur (as required)" if hit is None else
                       f"{a_} -> {b_} occurs at {hit['t'] * 1e3:.4g} ms although it must not")
    for tc in P.get("timing", ()):
        a = next((x for x in tr if x["from"] == tc["from"] and x["to"] == tc["to"]), None)
        if a is None:
            verdicts.append(FAIL)
            reasons.append(f"{tc['from']} -> {tc['to']} does not occur")
            continue
        ref = origin_time(run, tc.get("origin", "t0"))
        dt = a["t"] - (ref or 0.0)
        if tc.get("min_ms") is not None:
            ok = dt >= tc["min_ms"] * 1e-3 - 1e-9
            verdicts.append(PASS if ok else FAIL)
            reasons.append(f"{tc['from']} -> {tc['to']} at {dt * 1e3:.4g} ms {'>=' if ok else '<'} {tc['min_ms']:g} ms")
        if tc.get("max_ms") is not None:
            ok = dt <= tc["max_ms"] * 1e-3 + 1e-9
            verdicts.append(PASS if ok else FAIL)
            reasons.append(f"{tc['from']} -> {tc['to']} at {dt * 1e3:.4g} ms {'<=' if ok else '>'} {tc['max_ms']:g} ms")
    if P.get("allowlist", True):
        allow_s = _ms(P.get("changeover_allow_ms", 1.0)) or 0.0
        av = [v for v in sy.get("allow_violations", []) if len(v) < 4 or v[3] > allow_s + 1e-12]
        verdicts.append(PASS if not av else FAIL)
        reasons.append(f"every power-stage state allowed by its operating state (changeover allowance "
                       f"{allow_s * 1e3:g} ms)" if not av else
                       f"{len(av)} samples outside the allow-list, first at {av[0][0] * 1e3:.4g} ms: {av[0][2]} in "
                       f"{av[0][1]}")
    return _res(worst(verdicts), {"transitions": [f"{a}->{b}" for a, b in seq]}, "declared transitions and timing",
                reasons, {"run": run.key, "transitions": tr})


def _expr_eval(expr: str, env: dict) -> bool:
    toks = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|[&|~()]", expr)
    py = " ".join({"&": "and", "|": "or", "~": "not"}.get(tk, tk) for tk in toks)
    for tk in toks:
        if re.match(r"[A-Za-z_]", tk) and tk not in env:
            raise ValueError(f"unknown signal {tk} in {expr!r}")
    return bool(eval(py, {"__builtins__": {}}, {k: bool(v) for k, v in env.items()}))  # noqa: S307 - whitelisted


@check("guard_table")
def c_guard_table(ctx, spec, P):
    """The implemented guard of a transition against the requirement's boolean formula, over every combination."""
    from itertools import product as _prod
    from .system import GUARDS
    g = P.get("guard")
    fn, signals = GUARDS[g]
    ref = P.get("formula")
    bad = []
    for combo in _prod((0, 1), repeat=len(signals)):
        env = dict(zip(signals, combo))
        if bool(fn(env)) != _expr_eval(ref, env):
            bad.append(env)
    n = 2 ** len(signals)
    return _res(PASS if not bad else FAIL, {"combinations": n, "mismatches": len(bad)}, f"{g} == {ref}",
                [f"all {n} combinations agree" if not bad else f"mismatch at {bad[0]}"])


@check("permit")
def c_permit(ctx, spec, P):
    """The normal torque permit (and PWM) over a window: blocked, or restored after an event."""
    run = ctx.run(spec)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    perm = np.asarray(tr["sys_permit"], float)
    br = np.asarray(tr["bridge"], float)
    a = origin_time(run, P.get("from", "t0"))
    b = origin_time(run, P.get("to")) if P.get("to") is not None else float(t[-1])
    if a is None or b is None:
        return _res(FAIL, {}, "", [f"the window's {'start' if a is None else 'end'} event does not occur"],
                    {"run": run.key})
    m = (t > a + 1e-12) & (t < b - 1e-12)
    entered = np.where(m & (br != 0))[0]
    after = t >= (t[entered[0]] if len(entered) else b)       # PWM before the reaction's path delay is not a leak
    leak = np.where(m & ((perm > 0.5) | ((br == 0) & after)))[0]
    verdicts, reasons = [], []
    if P.get("blocked", True):
        verdicts.append(PASS if not len(leak) else FAIL)
        reasons.append(f"permit blocked and no PWM from {a * 1e3:.4g} to {b * 1e3:.4g} ms" if not len(leak) else
                       f"permit / PWM at {t[leak[0]] * 1e3:.4g} ms inside the blocked window (premature re-enable)")
    if P.get("restored_within_ms") is not None or P.has("restored_within_ms"):
        k = np.where((t >= b - 1e-12) & (perm > 0.5))[0]
        t_back = float(t[k[0]]) if len(k) else None
        lim = P.get("restored_within_ms")
        v, r = _budget("permit restored after the window", None if t_back is None else t_back - b, _ms(lim),
                       P.opened("restored_within_ms"))
        verdicts.append(v)
        reasons.append(r)
    return _res(worst(verdicts), {"window_ms": [a * 1e3, b * 1e3]}, "no premature re-enable", reasons,
                {"run": run.key})


@check("no_pwm")
def c_no_pwm(ctx, spec, P):
    run = ctx.run(spec)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    br = np.asarray(tr["bridge"], float)
    a = origin_time(run, P.get("from", "t0"))
    b = origin_time(run, P.get("to")) if P.get("to") is not None else float(t[-1])
    if a is None:
        return _res(NA, {}, "", ["the window's origin does not occur"], {"run": run.key})
    note = []
    if b is None:                       # the closing event never comes: the window runs to the end of the trajectory
        b = float(t[-1])
        note = [f"{P.get('to')} does not occur within the horizon: judged to the end ({b * 1e3:.4g} ms)"]
    k = np.where((t > a + 1e-12) & (t <= b + 1e-12) & (br == 0))[0]
    return _res(PASS if not len(k) else FAIL, {"window_ms": [a * 1e3, b * 1e3]}, "no PWM authority in the window",
                ["no PWM" if not len(k) else f"PWM at {t[k[0]] * 1e3:.4g} ms"] + note, {"run": run.key})


@check("equivalent")
def c_equivalent(ctx, spec, P):
    """Two scenarios end in the same physical safe state (e.g. an emergency stop by terminal-30 loss and a low-voltage
    loss)."""
    ra, rb = ctx.run(spec), ctx.run({**spec, "scenario": P.get("other"), "variant": P.get("other_variant")})
    fa, fb = ra.result.summary["final_actual"], rb.result.summary["final_actual"]
    ja = judge_safe_state(ra.result, fault_timeline(ra.result)["t_fault"], torque_tol_Nm=5.0, power_tol_W=500.0,
                          transition_s=_ms(P.get("transition_ms", 20)) or 0.0)
    jb = judge_safe_state(rb.result, fault_timeline(rb.result)["t_fault"], torque_tol_Nm=5.0, power_tol_W=500.0,
                          transition_s=_ms(P.get("transition_ms", 20)) or 0.0)
    same = fa == fb and ja["verdict"] == jb["verdict"]
    return _res(PASS if same else FAIL, {"a": fa, "b": fb}, "the same final safe state",
                [f"final bridge {fa} vs {fb}; C1-C4 {ja['verdict']} vs {jb['verdict']}"],
                {"runs": [ra.key, rb.key]})


@check("rearm")
def c_rearm(ctx, spec, P):
    """The default-error latch is released only after the qualified re-arm event (never before)."""
    run = ctx.run(spec)
    ev = run.result.events
    t_set = next((e["t"] for e in ev if e["kind"] == "supervisor" and "latch set" in e["text"]), None)
    t_rel = next((e["t"] for e in ev if e["kind"] == "supervisor" and "latch released" in e["text"]), None)
    t_lost = next((e["t"] for e in ev if e["kind"] == "supervisor" and ("latch LOST" in e["text"] or
                                                                        "is lost" in e["text"] or
                                                                        "releases the default-error" in e["text"])),
                  None)
    q = origin_time(run, P.get("qualified")) if P.get("qualified") else None
    exp = P.get("expect_release", True)
    meas = {"latch_set_ms": None if t_set is None else t_set * 1e3,
            "released_ms": None if t_rel is None else t_rel * 1e3,
            "lost_ms": None if t_lost is None else t_lost * 1e3,
            "qualified_ms": None if q is None else q * 1e3}
    reasons = []
    if P.get("expect_latch") is False:
        return _res(PASS if t_set is None else FAIL, meas, "no default-error latch (not a default error)",
                    ["no default-error latch" if t_set is None else f"latch set at {t_set * 1e3:.4g} ms"],
                    {"run": run.key})
    if t_set is None:
        return _res(FAIL, meas, "", ["the default-error latch was never set"], {"run": run.key})
    premature = (t_lost is not None) or (t_rel is not None and (q is None or t_rel < q - 1e-12))
    if premature:
        reasons.append("premature release: " + (f"latch lost at {t_lost * 1e3:.4g} ms" if t_lost is not None else
                                                f"released at {t_rel * 1e3:.4g} ms before the qualified event"))
        return _res(FAIL if P.get("expect_premature") is not True else PASS, meas,
                    "released only after the qualified re-arm", reasons, {"run": run.key})
    if P.get("expect_premature") is True:
        return _res(FAIL, meas, "a premature release (non-approved variant) reproduced",
                    ["no premature release occurred"], {"run": run.key})
    if exp and t_rel is None:
        return _res(FAIL, meas, "released after the qualified re-arm", ["never released"], {"run": run.key})
    if not exp and t_rel is not None:
        return _res(FAIL, meas, "kept latched", [f"released at {t_rel * 1e3:.4g} ms"], {"run": run.key})
    reasons.append("latch kept until the qualified event" + (f", released at {t_rel * 1e3:.4g} ms" if t_rel else ""))
    return _res(PASS, meas, "released only after the qualified re-arm", reasons, {"run": run.key})


@check("changeover")
def c_changeover(ctx, spec, P):
    """A controlled changeover: during a state transition the transient stays in its envelope and the new state's
    stage is established within the changeover time."""
    run = ctx.run(spec)
    sy = _sys(run)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    trans = next((x for x in sy.get("transitions", []) if x["from"] == P.get("from") and x["to"] == P.get("to")), None)
    if trans is None:
        return _res(FAIL, {}, "", [f"{P.get('from')} -> {P.get('to')} does not occur"], {"run": run.key})
    t0 = trans["t"]
    tmax = _ms(P.get("max_ms"))
    win_end = t0 + (tmax if tmax is not None else 0.05)
    m = (t >= t0) & (t <= win_end)
    meas = {}
    verdicts, reasons = [], []
    for q, key in (("torque_abs", "torque_abs_max_Nm"), ("i_phase_abs", "i_abs_max_A"), ("v_dc", "v_dc_max_V")):
        val = float(np.max(quantity(tr, q)[m])) if m.any() else None
        meas[key] = val
        if P.has(key):
            lim = P.get(key)
            if lim is None:
                verdicts.append(UNKNOWN)
                reasons.append(f"{key} {val:.4g}: limit {P.opened(key)} OPEN")
            else:
                verdicts.append(PASS if val <= lim else FAIL)
                reasons.append(f"{key} {val:.4g} {'<=' if val <= lim else '>'} {lim:g}")
    br = np.asarray(tr["bridge"], float)
    k = np.where((t >= t0) & (br != 0))[0]
    t_stage = float(t[k[0]]) - t0 if len(k) else None
    meas["stage_after_ms"] = None if t_stage is None else t_stage * 1e3
    if P.has("max_ms"):
        v, r = _budget("the new stage established", t_stage, tmax, P.opened("max_ms"))
        verdicts.append(v)
        reasons.append(r)
    return _res(worst(verdicts), meas, "controlled changeover", reasons, {"run": run.key})


# ------------------------------------------------------------------------------------------------- interface checks

@check("fallback")
def c_fallback(ctx, spec, P):
    run = ctx.run(spec)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    T = np.asarray(tr["T_shaft"], float)
    o = origin_time(run, P.get("origin"))
    if P.has("axle_torque_Nm"):
        # the end-to-end variant: the safe value is an axle torque through the transmission ratio
        ax, ratio = P.get("axle_torque_Nm"), P.get("wheel_ratio")
        lim = None if (ax is None or ratio is None) else float(ax) / float(ratio)
        missing = ", ".join(x for x in (P.opened("axle_torque_Nm"), P.opened("wheel_ratio")) if x)
    else:
        lim = P.get("safe_value_Nm")
        missing = P.opened("safe_value_Nm")
    settle = _ms(P.get("settle_ms", 20.0))
    if o is None:
        return _res(FAIL, {}, "", ["the fallback condition never occurs"], {"run": run.key})
    m = t >= o + settle
    peak = float(np.max(T[m])) if m.any() else None
    meas = {"T_max_after_Nm": peak}
    if lim is None:
        return _res(UNKNOWN, meas, "", [f"safe value {missing} OPEN; measured {peak:.4g} N*m"], {"run": run.key})
    ok = peak <= lim + float(P.get("tol_Nm", 5.0))
    return _res(PASS if ok else FAIL, meas, f"positive torque <= {lim:g} N*m after {settle * 1e3:g} ms",
                [f"max shaft torque {peak:.4g} N*m after the fallback"], {"run": run.key})


@check("arbitration")
def c_arbitration(ctx, spec, P):
    """The signed torque arbitration T_sum = desired + intervention clamped to the envelope by its sign, against the
    formula over a grid of every quadrant."""
    from .system import InterfaceSpec, SystemLayer, SystemSpec
    grid = [float(x) for x in P.get("grid", (-300, -150, -20, 0, 20, 150, 300))]
    bad = []
    n = 0
    for tmax in (200.0, 50.0):
        for tmin in (-200.0, -50.0):
            lay = SystemLayer(SystemSpec(interface=InterfaceSpec(envelope={"max_Nm": tmax, "min_Nm": tmin})))

            class _S:                                           # the minimal simulation view the interface needs
                app_limit_failed = False

                def ev(self, *a, **k):
                    pass
            for d in grid:
                for iv in grid:
                    lay.sig["intervention_Nm"] = iv
                    got = lay.limit_command(_S(), 0.0, d)
                    s = d + iv
                    ref = min(s, tmax) if s > 0 else max(s, tmin)
                    n += 1
                    if abs(got - ref) > 1e-9:
                        bad.append((d, iv, tmax, tmin, got, ref))
    return _res(PASS if not bad else FAIL, {"cases": n, "mismatches": len(bad)},
                "T_sum > 0 -> min(T_sum, max); T_sum < 0 -> max(T_sum, min); signed, every quadrant",
                [f"all {n} cases agree" if not bad else f"mismatch {bad[0]}"])


@check("extended")
def c_extended(ctx, spec, P):
    run = ctx.run(spec)
    ev = [e for e in run.result.events if e["kind"] == "interface" and e["source"] == "extended torque"]
    act = next((e for e in ev if "active" in e["text"]), None)
    exp_ = next((e for e in ev if "expired" in e["text"]), None)
    meas = {"active_ms": act["t"] * 1e3 if act else None, "expired_ms": exp_["t"] * 1e3 if exp_ else None}
    verdicts, reasons = [], []
    if P.get("expect_active", True):
        verdicts.append(PASS if act else FAIL)
        reasons.append(act["text"] if act else "the extension never became active")
        if act and P.has("c_t_imax_ms"):
            lim = P.get("c_t_imax_ms")
            if lim is None:
                verdicts.append(UNKNOWN)
                reasons.append(f"reversion time {P.opened('c_t_imax_ms')} OPEN")
            else:
                dt = None if exp_ is None else exp_["t"] - act["t"]
                ok = dt is not None and dt <= lim * 1e-3 + 2e-3
                verdicts.append(PASS if ok else FAIL)
                reasons.append(f"reverted after {_fmt_ms(dt)} (limit {lim:g} ms)")
    else:
        verdicts.append(PASS if not act else FAIL)
        reasons.append("no extension (as required)" if not act else f"extension active: {act['text']}")
    if P.has("neg_limit_Nm"):
        T = np.asarray(run.result.trace["T_shaft"], float)
        lim = P.get("neg_limit_Nm")
        ok = float(np.min(T)) >= lim - float(P.get("tol_Nm", 5.0))
        verdicts.append(PASS if ok else FAIL)
        reasons.append(f"negative side unchanged: min torque {float(np.min(T)):.4g} N*m vs {lim:g}")
    return _res(worst(verdicts), meas, "positive-side extension only, active flag, timed reversion", reasons,
                {"run": run.key})


@check("e2e")
def c_e2e(ctx, spec, P):
    run = ctx.run(spec)
    ev = run.result.events
    rej = [e for e in ev if e["kind"] == "rx"]
    det = [e for e in ev if e["kind"] == "detection" and e.get("mech_kind") == "rx_monitor"]
    exp = P.get("expect_detection", True)
    meas = {"rejected_frames_logged": len(rej), "detections": len(det),
            "first_detection_ms": det[0]["t"] * 1e3 if det else None}
    if not exp:
        return _res(PASS if not det else FAIL, meas, "no receive fault in normal operation (a task at half the "
                    "receive period sees no new frame every other run - not a repeated message)",
                    ["no false receive fault" if not det else f"false detection: {det[0]['text']}"], {"run": run.key})
    if not det:
        return _res(FAIL, meas, "receive fault detected", ["not detected"], {"run": run.key})
    t0 = origin_time(run, "fault") or 0.0
    v, r = _budget("receive fault detection after the fault", det[0]["t"] - t0, _ms(P.get("within_ms")),
                   P.opened("within_ms"))
    return _res(v or PASS, meas, "receive fault detected", [r, det[0]["text"]], {"run": run.key})


# ------------------------------------------------------------------------------------------------- energy checks

@check("discharge")
def c_discharge(ctx, spec, P):
    run = ctx.run(spec)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    v = np.asarray(tr["v_dc"], float)
    t_req = origin_time(run, "input:discharge_request=1")
    lim = float(P.get("v_limit_V", 60.0))
    if t_req is None:
        return _res(FAIL, {}, "", ["no discharge request in the scenario"], {"run": run.key})
    k = np.where((t >= t_req) & (v < lim))[0]
    t_below = float(t[k[0]]) if len(k) else None
    comp = next((e for e in run.result.events if e["kind"] == "discharge" and "complete indicated" in e["text"]), None)
    meas = {"below_after_ms": None if t_below is None else (t_below - t_req) * 1e3,
            "indicated_after_ms": None if comp is None else (comp["t"] - t_req) * 1e3,
            "v_end_V": float(v[-1])}
    verdicts, reasons = [], []
    if P.has("deadline_ms"):
        vv, r = _budget("true DC link below the limit after the request",
                        None if t_below is None else t_below - t_req, _ms(P.get("deadline_ms")),
                        P.opened("deadline_ms"))
        if t_below is None and float(t[-1]) - t_req < (_ms(P.get("deadline_ms")) or math.inf):
            vv, r = UNKNOWN, f"the horizon ends {(float(t[-1]) - t_req) * 1e3:.4g} ms after the request, before the deadline"
        verdicts.append(vv)
        reasons.append(r)
    if comp is not None:
        kk = int(np.searchsorted(t, comp["t"]))
        v_true = float(v[min(kk, len(v) - 1)])
        false = v_true >= lim
        meas["v_true_at_indication_V"] = v_true
        if P.get("expect_false_complete") is True:
            verdicts.append(PASS if false else FAIL)
            reasons.append(f"false completion reproduced (true {v_true:.1f} V)" if false else "no false completion")
        else:
            verdicts.append(FAIL if false else PASS)
            reasons.append(f"completion indicated at true {v_true:.1f} V {'>=' if false else '<'} {lim:g} V")
    if P.get("expect_recharge") is True:
        blocked = t_below is None
        verdicts.append(PASS if blocked else FAIL)
        reasons.append("the rotating machine keeps the DC link above the limit (counterexample reproduced)"
                       if blocked else "the link discharged although the machine rotates")
    return _res(worst(verdicts), meas, "measured Vdc(t) deadline, no false completion", reasons, {"run": run.key})


@check("selftest")
def c_selftest(ctx, spec, P):
    run = ctx.run(spec)
    ev = run.result.events
    refused = [e for e in ev if e["kind"] == "selftest" and e.get("refused")]
    pulses = [e for e in ev if e["kind"] == "selftest" and "pulse:" in e["text"]]
    verdicts, reasons = [], []
    meas = {"refused": len(refused), "pulses": len(pulses)}
    if P.get("expect_refused") is not None:
        ok = bool(refused) == bool(P.get("expect_refused"))
        verdicts.append(PASS if ok else FAIL)
        reasons.append(refused[0]["text"] if refused else (pulses[0]["text"] if pulses else "no test"))
    if pulses:
        tr = run.result.trace
        t = np.asarray(tr["t"], float)
        a = pulses[0]["t"]
        m = (t >= a) & (t <= a + _ms(P.get("window_ms", 5.0)))
        for q, key in (("torque_abs", "torque_abs_max_Nm"), ("i_phase_abs", "i_abs_max_A"), ("v_dc", "v_dc_max_V")):
            val = float(np.max(quantity(tr, q)[m]))
            meas[key] = val
            if P.has(key):
                lim = P.get(key)
                if lim is None and val <= 1e-6:
                    verdicts.append(PASS)
                    reasons.append(f"{key} {val:.3g}: within every non-negative test envelope {P.opened(key)}")
                elif lim is None:
                    verdicts.append(UNKNOWN)
                    reasons.append(f"{key} {val:.4g}: passes if {P.opened(key)} >= {val:.4g} (OPEN)")
                else:
                    verdicts.append(PASS if val <= lim else FAIL)
                    reasons.append(f"{key} {val:.4g} {'<=' if val <= lim else '>'} {lim:g}")
        br = np.asarray(tr["bridge"], float)
        k = np.where((t > a) & (br == 0))[0]
        verdicts.append(PASS if not len(k) else FAIL)
        reasons.append("no normal PWM after the test" if not len(k) else f"PWM at {t[k[0]] * 1e3:.4g} ms after it")
    return _res(worst(verdicts), meas, "test only under its entry conditions, output envelope, no PWM enable", reasons,
                {"run": run.key})


@check("vehicle")
def c_vehicle(ctx, spec, P):
    from .vehicle import continue_vehicle, judge_acceleration, judge_distance, vehicle_from_dict
    run = ctx.run(spec)
    vs = vehicle_from_dict(run.scenario.get("vehicle"))
    mach = None
    if P.get("continuation") == "asc":
        m = run.setup.machine
        mach = {"p": m.p, "psi": m.psi, "Ld": m.Ld, "Lq": m.Lq, "Rs": m.Rs}
    veh = continue_vehicle(run.result, vs, v0_mps=float(P.get("v0_kph", 0.0)) / 3.6,
                           t_react_s=float(P.get("t_react_s", 1.0)), a_brake_mps2=float(P.get("a_brake_mps2", 5.0)),
                           continuation=P.get("continuation", "final_mean"), machine=mach,
                           T_const_Nm=float(P.get("T_const_Nm", 0.0)))
    verdicts, reasons = [], []
    meas = {"distance_m": veh["distance_m"], "v_max_kph": veh["v_max_mps"] * 3.6, "a_max_mps2": veh["a_max_mps2"],
            "stopped_at_s": veh["stopped_at_s"]}
    if P.has("distance_max_m"):
        lim = P.get("distance_max_m")
        if lim is None:
            verdicts.append(UNKNOWN)
            reasons.append(f"distance {veh['distance_m']:.3g} m: limit {P.opened('distance_max_m')} OPEN")
        else:
            j = judge_distance(veh, float(lim))
            jv = j["verdict"]
            if P.get("expect_violation"):
                jv = PASS if jv == FAIL else (FAIL if jv == PASS else jv)
                reasons.append("counterexample reproduced" if jv == PASS else "the expected violation does not occur")
            verdicts.append(jv)
            reasons.append(j["reason"])
    if P.has("accel_curve"):
        j = judge_acceleration(veh, P.get("accel_curve"), P.opened("accel_curve"))
        verdicts.append(j["verdict"])
        reasons.append(j["reason"])
    if P.get("expect_no_motion"):
        ok = veh["distance_m"] < 1e-3
        verdicts.append(PASS if ok else FAIL)
        reasons.append("the vehicle stays at rest" if ok else f"the vehicle moved {veh['distance_m']:.3g} m")
    reasons.append(veh["basis"])
    return _res(worst(verdicts), meas, "vehicle-level consequence", reasons,
                {"run": run.key, "vehicle": {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in veh.items()}})


# ------------------------------------------------------------------------------------------------- static checks

@check("calibration")
def c_calibration(ctx, spec, P):
    from .calibration import activation_sequence
    data = ctx.design_data()
    active = {"set_id": "active", "values": {}}
    res = activation_sequence(data, active, list(P.get("updates", ())), dict(P.get("target", {})),
                              tuple(P.get("constraints", ())))
    exp = list(P.get("expect", ()))
    got = [s["decision"] for s in res["steps"]]
    ok = got == exp if exp else True
    return _res(PASS if ok else FAIL, {"decisions": got, "active": res["active"]},
                f"decisions {exp}", [f"{s['candidate']}: {s['decision']} "
                                     f"({'; '.join(c['check'] + (' ok' if c['ok'] else ' FAILED') for c in s['checks'] if not c['ok']) or 'all checks ok'})"
                                     for s in res["steps"]], {"steps": res["steps"]})


@check("task_cycle")
def c_task_cycle(ctx, spec, P):
    """T_cycle <= min(min Tx cycles, 1/2 min E2E Rx cycles, the maximum safety task time); a violation of a known
    term is a FAIL even while another term is OPEN."""
    data = ctx.design_data()
    mechs = [m for m in data.get("mechanisms") or [] if m.get("period_ms") and
             (not P.get("mechanisms") or m.get("id") in P.get("mechanisms"))]
    t_cycle = max(float(m["period_ms"]) for m in mechs) if mechs else None
    tx = [float(x) for x in P.get("tx_cycles_ms", ())]
    rx = [float(x) for x in P.get("rx_cycles_ms", ())]
    known = [min(tx)] if tx else []
    if rx:
        known.append(0.5 * min(rx))
    tmax = P.get("max_task_ms")
    meas = {"t_cycle_ms": t_cycle, "bound_known_ms": min(known) if known else None}
    if t_cycle is None:
        return _res(FAIL, meas, "", ["no periodic safety mechanism declared"])
    reasons = [f"safety task period {t_cycle:g} ms (slowest of {', '.join(m['id'] for m in mechs)})"]
    if known and t_cycle > min(known) + 1e-12:
        return _res(FAIL, meas, "T_cycle <= min(Tx, Rx/2, max task time)",
                    reasons + [f"> {min(known):g} ms from the Tx / Rx terms"])
    if tmax is None:
        return _res(UNKNOWN, meas, "", reasons + [f"<= the Tx / Rx terms ({min(known):g} ms)" if known else "",
                                                  f"the maximum safety task time {P.opened('max_task_ms')} OPEN"])
    ok = t_cycle <= float(tmax) + 1e-12
    return _res(PASS if ok else FAIL, meas, "", reasons + [f"{'<=' if ok else '>'} {tmax:g} ms max task time"])


@check("asil_binding")
def c_asil_binding(ctx, spec, P):
    """Every ASIL literal is kept and resolved only through its parameter binding (MAX never silently becomes a
    letter)."""
    pkg = ctx.package
    binding = pkg.get("asil_binding") or {}
    rows, bad = [], []
    for it in pkg.get("items", []):
        lit = it.get("asil_literal")
        if not lit:
            continue
        res = it.get("asil_resolved")
        par = binding.get(lit)
        if par is not None:
            val = ctx.param_value(par)
            if res is not None and res != val:
                bad.append(f"{it['id']}: resolved {res} but {par} is {val}")
            rows.append((it["id"], lit, val if val is not None else f"TBD ({par} OPEN)"))
        elif lit in ("QM", "A", "B", "C", "D", "N/A"):
            rows.append((it["id"], lit, lit))
        else:
            if res is not None:
                bad.append(f"{it['id']}: literal {lit} resolved to {res} without a binding")
            rows.append((it["id"], lit, "TBD (no binding)"))
    tbd = [r for r in rows if str(r[2]).startswith("TBD")]
    v = FAIL if bad else (UNKNOWN if tbd else PASS)
    return _res(v, {"items": len(rows), "tbd": len(tbd)}, "literal kept, resolved only by binding",
                bad or [f"{len(rows)} literals kept; {len(tbd)} resolve to TBD (parameter OPEN)"], {"rows": rows})


@check("provenance")
def c_provenance(ctx, spec, P):
    """The package keeps customer truth and study assumptions apart: OPEN parameters carry no value, DERIVED /
    RESEARCH items are not presented as customer requirements, every CONFLICT has at least two variants."""
    pkg = ctx.package
    bad = []
    for p in pkg.get("parameters", []):
        if p.get("provenance") == "OPEN" and p.get("value") is not None:
            bad.append(f"OPEN parameter {p['id']} carries a value {p['value']!r}")
        if p.get("provenance") == "CONFLICT" and len(p.get("variants") or {}) < 2:
            bad.append(f"CONFLICT parameter {p['id']} has fewer than two variants")
    cust = set(pkg.get("customer_tags") or ("CONFIRMED", "CUSTOMER-PAST", "PROJECT"))
    for it in pkg.get("items", []):
        if it.get("customer_requirement") and it.get("provenance") not in cust:
            bad.append(f"{it['id']} ({it.get('provenance')}) is marked as a customer requirement")
    return _res(PASS if not bad else FAIL, {"violations": len(bad)}, "provenance kept apart",
                bad or ["OPEN values empty, DERIVED / RESEARCH separate, conflicts as variants"])


@check("sm_spec")
def c_sm_spec(ctx, spec, P):
    it = ctx.item(P.get("item") or spec.get("_item"))
    need = ("target_fault", "principle", "reaction", "timing", "asil", "allocation", "verification", "shared")
    miss = [k for k in need if not (it.get("spec") or {}).get(k)]
    demo = P.get("demonstrated_by")
    verdicts = [PASS if not miss else FAIL]
    reasons = ["specification complete" if not miss else f"missing: {', '.join(miss)}"]
    if demo:
        r = ctx.item_result(demo)
        verdicts.append(r["verdict"])
        reasons.append(f"detect -> react demonstrated by {demo}: {r['verdict']}")
    elif P.get("not_simulable"):
        verdicts.append(MANUAL)
        reasons.append(f"not demonstrable in this simulation: {P.get('not_simulable')}")
    return _res(worst(verdicts), {"missing": miss}, "a complete mechanism specification with a demonstrated path",
                reasons)


@check("metrics")
def c_metrics(ctx, spec, P):
    from .metrics import judge_metrics, metric_set
    rows = P.get("rows") or ctx.package.get("fmeda") or []
    out, verdicts, reasons = {}, [], []
    for name, sdef in (P.get("sets") or {}).items():
        m = metric_set(rows, sdef["requirements"], float(P.get("lifetime_h", 10000.0)))
        tg = {k: sdef.get(k) for k in ("SPFM_min", "LFM_min", "PMHF_max_fit")}
        for k in tg:
            if isinstance(sdef.get(k), str) and sdef[k].startswith("$"):
                tg[k] = ctx.param_value(sdef[k][1:])
        j = judge_metrics(m, tg)
        out[name] = {**m, "judgement": j}
        verdicts.append(j["verdict"])
        reasons.append(f"{name}: SPFM {m['SPFM'] if m['SPFM'] is None else round(100 * m['SPFM'], 2)} %, LFM "
                       f"{m['LFM'] if m['LFM'] is None else round(100 * m['LFM'], 2)} %, PMHF {m['PMHF_fit']:.4g} FIT "
                       f"- {'; '.join(j['reasons'])}")
    reasons.append("each set judged on its own; the sets are never added")
    return _res(worst(verdicts), {k: {kk: v[kk] for kk in ("SPFM", "LFM", "PMHF_fit")} for k, v in out.items()},
                "SPFM / LFM / PMHF per metric set", reasons, {"sets": out})


@check("feasibility")
def c_feasibility(ctx, spec, P):
    from .feasibility import feasibility_map
    res = ctx.cached(("feasibility", repr(sorted(P.v.items()))), lambda: feasibility_map(
        ctx.product, P.get("speeds_rpm"), P.get("vdcs_V"), reactions=tuple(P.get("reactions", ("six_switch_off",
                                                                                                 "asc_low"))),
        hv_state=P.get("hv_state", "connected"), torque_tol_Nm=_tols(P)[0], power_tol_W=_tols(P)[1],
        t_min_Nm=P.get("tlsr_min_Nm"), t_max_Nm=P.get("tlsr_max_Nm"),
        horizon_ms=float(P.get("horizon_ms", 40.0)), temperatures=tuple(P.get("temperatures_C") or (None,)),
        progress=None))
    cnt = res["counts"]
    if P.get("research"):
        # a study figure recomputed for this machine (e.g. how many states meet neither reaction): never acceptance
        return _res(NA, cnt, "computed for this machine (research reference, not an acceptance criterion)",
                    [f"{k}: {v}" for k, v in cnt.items()] + [f"reference of the study: {P.get('reference')}"
                                                             if P.get("reference") else ""], {"map": res})
    verdicts, reasons = [], [f"{k}: {v}" for k, v in cnt.items()]
    if P.get("expect_counterexample"):
        c = P.get("expect_counterexample")
        ok = any(cell["reactions"].get(c["reaction"], {}).get("verdict") == FAIL for cell in res["cells"])
        verdicts.append(PASS if ok else FAIL)
        reasons.insert(0, f"{c['reaction']} fails the safe state somewhere in the domain" if ok else
                       f"no cell where {c['reaction']} fails")
    if P.get("expect_all_cells_decided"):
        und = cnt.get("undecided", 0)
        verdicts.append(PASS if und == 0 else UNKNOWN)
    if P.opened("tlsr_min_Nm") or P.opened("tlsr_max_Nm"):
        reasons.append("C4 not judged: the torque limit is OPEN")
    if _tols(P)[2]:
        reasons.append(f"judged at zero tolerance ({', '.join(_tols(P)[2])} OPEN)")
    return _res(worst(verdicts) if verdicts else PASS, cnt, "reaction feasibility over the domain", reasons,
                {"map": res})


@check("research")
def c_research(ctx, spec, P):
    """A RESEARCH-type metric computed for this machine (reference values of another study are not comparable and
    never an acceptance criterion)."""
    run = ctx.run(spec)
    tl = fault_timeline(run.result)
    kind = P.get("metric")
    meas = {}
    if kind == "impulse_before_detection":
        lim = float(P.get("limit_Nm", 0.0))
        meas["impulse_Nms"] = excess_impulse(run.result, tl["t_fault"] or 0.0, tl["t_detect"] or
                                             float(run.result.trace["t"][-1]), lim)
    elif kind == "time_to_window":
        dt = time_to_window_hold(run.result, tl["t_fault"] or 0.0, float(P.get("half_width_Nm", 20.0)),
                                 hold_s=_ms(P.get("hold_ms")) or 0.0)
        meas["time_to_window_ms"] = None if dt is None else dt * 1e3
    elif kind == "gate_vs_terminal":
        dt = time_to_window_hold(run.result, tl["t_gate_applied"] or 0.0, float(P.get("half_width_Nm", 20.0)))
        meas["gate_ms"] = None if tl["t_gate_applied"] is None else tl["t_gate_applied"] * 1e3
        meas["terminal_after_gate_ms"] = None if dt is None else dt * 1e3
    ref = P.get("reference")
    return _res(NA, meas, "computed for this machine (research reference, not an acceptance criterion)",
                [f"reference value of the study (another machine): {ref}" if ref is not None else "",
                 f"computed here: {meas}"], {"run": run.key, "timeline": tl})


@check("trace_channels")
def c_trace_channels(ctx, spec, P):
    run = ctx.run(spec)
    tr = run.result.trace
    ev_kinds = {e["kind"] for e in run.result.events}
    miss = [c for c in P.get("channels", ()) if c not in tr]
    miss += [f"event:{k}" for k in P.get("events", ()) if k not in ev_kinds]
    return _res(PASS if not miss else FAIL, {"missing": miss}, "the module's observations are recorded",
                ["all observations present" if not miss else f"missing {', '.join(miss)}"], {"run": run.key})


@check("manual")
def c_manual(ctx, spec, P):
    it = ctx.item(spec.get("_item"))
    st = (it.get("status") or "OPEN").upper()
    ev = it.get("evidence")
    if st == "DONE" and ev:
        return _res(PASS, {}, "manual evidence recorded", [f"evidence: {ev}"])
    return _res(MANUAL, {}, "manual / organisational evidence", [P.get("reason") or it.get("manual") or
                                                                 "not machine-checkable: record the evidence"])


@check("normal_first")
def c_normal_first(ctx, spec, P):
    """The fault-free application limitation acts before the safety monitor (no monitor reaction), and with the
    application failed the monitor still reacts within its deadline (a second scenario)."""
    run = ctx.run(spec)
    ev = run.result.events
    lim = next((e for e in ev if e["kind"] == "controller" and "limit" in e["text"]), None)
    det = [e for e in ev if e["kind"] == "detection"]
    verdicts = [PASS if not det else FAIL]
    reasons = ["no monitor reaction in the fault-free boundary profile" if not det else
               f"monitor reacted: {det[0]['text']}"]
    if lim is not None:
        reasons.append(f"application limitation first: {lim['text']} at {lim['t'] * 1e3:.4g} ms")
    if P.get("failed_scenario") or P.get("failed_variant"):
        r2 = ctx.run({**spec, "scenario": P.get("failed_scenario") or spec.get("scenario"),
                      "variant": P.get("failed_variant")})
        d2 = next((e for e in r2.result.events if e["kind"] == "detection"), None)
        t0 = fault_timeline(r2.result)["t_fault"] or 0.0
        v, r = (FAIL, "the monitor did not react with the application failed") if d2 is None else \
            _budget("monitor reaction with the application failed", d2["t"] - t0, _ms(P.get("deadline_ms")),
                    P.opened("deadline_ms"))
        verdicts.append(v or PASS)
        reasons.append(r)
    return _res(worst(verdicts), {}, "normal function first, monitor deadline kept", reasons, {"run": run.key})


@check("confirmation")
def c_confirmation(ctx, spec, P):
    """The reported confirmation of the safe-state reaction never claims a safe state the truth does not show
    (requested / applied / confirmed kept apart); ``expect_confirmed``: whether it should be confirmed at the end."""
    from .safestate import condition_masks
    run = ctx.run(spec)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    conf = np.asarray(tr["sys_confirmed"], float) > 0.5
    tT, tP, _open = _tols(P)
    masks = condition_masks(tr, tT, tP, None, None)
    unsafe = masks["C1"] | masks["C2"] | masks["C3"]
    allow = _ms(P.get("transition_ms", 5.0)) or 0.0
    first_conf = float(t[np.argmax(conf)]) if conf.any() else None
    bad = np.where(conf & unsafe & (t >= (first_conf or 0.0) + allow))[0]
    verdicts, reasons = [], []
    verdicts.append(PASS if not len(bad) else FAIL)
    reasons.append("no confirmation while the truth is unsafe" if not len(bad) else
                   f"confirmed at {t[bad[0]] * 1e3:.4g} ms while C1-C3 are violated (false confirmation)")
    if P.has("expect_confirmed"):
        end = bool(conf[-1]) if len(conf) else False
        ok = end == bool(P.get("expect_confirmed"))
        verdicts.append(PASS if ok else FAIL)
        reasons.append(f"confirmed at the end: {end} (expected {bool(P.get('expect_confirmed'))})")
    return _res(worst(verdicts), {"first_confirmed_ms": None if first_conf is None else first_conf * 1e3},
                "confirmation only with sufficient observations", reasons, {"run": run.key})


@check("state_time")
def c_state_time(ctx, spec, P):
    """The time from an origin until the bridge is first commanded into one of the given states (e.g. the ASC after
    the gate-driver enable is withdrawn), against a limit in microseconds."""
    from .engine import BRIDGE_CODES
    run = ctx.run(spec)
    tr = run.result.trace
    t = np.asarray(tr["t"], float)
    br = np.asarray(tr["bridge"], float)
    o = origin_time(run, P.get("origin", "fault"))
    codes = [BRIDGE_CODES[s] for s in P.get("states", ("asc_low", "asc_high", "seq_asc_low", "seq_asc_high"))]
    if o is None:
        return _res(NA, {}, "", ["the origin does not occur"], {"run": run.key})
    k = np.where((t >= o - 1e-15) & np.isin(br, codes))[0]
    dt = None if not len(k) else float(t[k[0]]) - o
    meas = {"after_us": None if dt is None else dt * 1e6}
    lim = P.get("max_us")
    v, r = _budget(f"{'/'.join(P.get('states', ('ASC',)))} after the origin", dt, None if lim is None else lim * 1e-6,
                   P.opened("max_us"))
    return _res(v or PASS, meas, f"in the state within {lim} us", [r.replace(" ms", " ms")], {"run": run.key})


# ------------------------------------------------------------------------------------------------- formula checks

_ARITH_FUNCS = {"max": max, "min": min, "abs": abs}


def _arith(expr: str, env: dict) -> float:
    """A declared arithmetic formula (numbers, the names in ``env``, + - * /, max / min / abs) - nothing else."""
    import ast
    import operator as op
    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.USub: op.neg,
           ast.UAdd: op.pos}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise ValueError(f"unknown name {n.id} in {expr!r}")
            return float(env[n.id])
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _ARITH_FUNCS and not n.keywords:
            return float(_ARITH_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError(f"not an allowed formula element in {expr!r}")
    return ev(ast.parse(str(expr), mode="eval"))


@check("window_semantics")
def c_window_semantics(ctx, spec, P):
    """The implemented signed torque window (the function the monitors run) against the declared formula, over a
    grid of requests of both signs, limit factors, margins, actual torques and tolerances - so it holds for every
    value of an OPEN limit factor or tolerance: the high / low edges, the tolerance inequality (``rule``), the signed
    comparison (a sign flip is a violation - never |T|) and the speed-dependent tolerance (a map over |speed|)."""
    from .protection import signed_window, speed_tolerance, window_excess
    hi_f, lo_f = P.get("high", "max(T*F, T/F) + A"), P.get("low", "min(T*F, T/F) - A")
    rule = P.get("rule", "add")
    grid = [float(x) for x in P.get("requests", (-300, -120, -20, -1, 0, 1, 20, 120, 300))]
    factors = [float(x) for x in P.get("factors", (1.0, 1.05, 1.2, 1.5, 2.0))]
    margins = [float(x) for x in P.get("margins", (0.0, 5.0, 20.0))]
    tols = [float(x) for x in P.get("tolerances", (0.0, 2.5, 10.0))]
    bad, n, flips, flips_seen = [], 0, 0, 0
    for F in factors:
        for A in margins:
            for T in grid:
                Pm = {"limit_source": "factor", "limit_factor": F, "abs_Nm": A, "tol_rule": rule}
                hi, lo, _c = signed_window(T, Pm)
                env = {"T": T, "F": F, "A": A}
                rh, rl = _arith(hi_f, env), _arith(lo_f, env)
                n += 1
                if abs(hi - rh) > 1e-9 or abs(lo - rl) > 1e-9:
                    bad.append(f"edges at T={T:g}, F={F:g}, A={A:g}: [{lo:.4g}, {hi:.4g}] vs declared [{rl:.4g}, "
                               f"{rh:.4g}]")
                for Ta in sorted(set(grid + [T, -T, rh + 0.5, rl - 0.5, rh - 0.5, rl + 0.5])):
                    for tol in tols:
                        n += 1
                        x_hi, x_lo = window_excess(Ta, tol, hi, lo, Pm)
                        if rule == "add":
                            ref_hi, ref_lo = Ta + tol > rh, Ta - tol < rl
                        else:
                            ref_hi, ref_lo = Ta - tol > rh, Ta + tol < rl
                        if (x_hi > 0) != ref_hi or (x_lo > 0) != ref_lo:
                            bad.append(f"violation at T={T:g}, T_act={Ta:g}, tol={tol:g}: implemented "
                                       f"({x_hi > 0}, {x_lo > 0}) vs declared ({ref_hi}, {ref_lo})")
                # the signed comparison: the actual torque of the opposite sign violates wherever the declared
                # window does not contain it (a |T| comparison would accept it)
                if T != 0.0 and not (rl <= -T <= rh):
                    flips += 1
                    x_hi, x_lo = window_excess(-T, 0.0, hi, lo, Pm)
                    flips_seen += int(x_hi > 0 or x_lo > 0)
    tm = P.get("tol_map") or [[0.0, 5.0], [6000.0, 7.5], [12000.0, 10.0]]
    tbad = []
    for sp in (-15000.0, -9000.0, -3000.0, 0.0, 1500.0, 6000.0, 9000.0, 12000.0, 20000.0):
        got = speed_tolerance(sp, {"tol_map": tm})
        ref = float(np.interp(abs(sp), [float(a) for a, _ in tm], [float(b) for _, b in tm]))
        if abs(got - ref) > 1e-9:
            tbad.append(f"tolerance at {sp:g} rpm {got:.4g} vs {ref:.4g}")
    verdict = PASS if not bad and not tbad and flips_seen == flips else FAIL
    reasons = [f"{n} cases: the edges and the {rule} inequality as declared (high = {hi_f}, low = {lo_f})"
               if not bad else bad[0],
               f"sign flip detected in {flips_seen} of {flips} cases (signed comparison, never |T|)",
               "tolerance: linear map over |measured speed|, flat outside" if not tbad else tbad[0],
               "holds for every value of the limit factor, the margin and the tolerance in the grid (their customer "
               "values are not needed for this property)"]
    return _res(verdict, {"cases": n, "mismatches": len(bad) + len(tbad), "sign_flips": flips},
                "the implemented window equals the declared formula", reasons)


@check("sweep")
def c_sweep(ctx, spec, P):
    """One or more OPEN (or any) parameters swept over declared values (every combination): the inner check at each.
    While a swept parameter has no value the verdict is UNKNOWN with the combinations it passes for - the
    break-even, never a guess; once every one has a value (declared, entered, or illustrative in that profile) the
    verdict is the inner check's at those values."""
    import itertools
    pids = P.raw.get("param")
    pids = [pids] if isinstance(pids, str) else list(pids or ())
    inner = P.raw.get("inner")
    ip = dict(P.raw.get("params") or {})
    vals = P.raw.get("values") or ()
    grids = [list(vals)] if len(pids) == 1 else [list(v) for v in vals]
    if inner not in CHECKS or inner == "sweep" or not pids or len(grids) != len(pids) or not all(grids):
        return _res(UNKNOWN, {}, "", ["a sweep needs parameters, values for each and an inner check"])
    missing = object()
    saved = {pid: ctx.values.get(pid, missing) for pid in pids}

    def restore():
        for pid, v in saved.items():
            if v is missing:
                ctx.values.pop(pid, None)
            else:
                ctx.values[pid] = v

    def one(combo):
        for pid, v in zip(pids, combo):
            ctx.values[pid] = v
        opened: dict = {}
        resolved = ctx.resolve(ip, open_refs=opened)
        try:
            return CHECKS[inner](ctx, spec, Params(ip, resolved, opened, set()))
        except InputValidationError as exc:
            return _res(UNKNOWN, {}, "", [f"input: {exc}"])

    def label(combo):
        return ", ".join(f"{pid} = {v}" for pid, v in zip(pids, combo))
    per = []
    try:
        for combo in itertools.product(*grids):
            r = one(combo)
            per.append({"values": list(combo), "verdict": r["verdict"],
                        "reasons": [x for x in r["reasons"] if x][:2], "measured": r.get("measured", {})})
    finally:
        restore()
    declared = [ctx.param_value(pid) for pid in pids]     # entered, declared, or illustrative in that profile
    rows = [f"{label(x['values'])}: {x['verdict']} ({'; '.join(x['reasons'])[:110]})" for x in per]
    if all(v is not None for v in declared):
        try:
            r = one(declared)
        finally:
            restore()
        return _res(r["verdict"], {"at": dict(zip(pids, declared)), "sweep": per}, r.get("expected", ""),
                    [f"at {label(declared)}: {r['verdict']}"] + [x for x in r["reasons"] if x][:2] + rows,
                    r.get("evidence", {}))
    ok = [label(x["values"]) for x in per if x["verdict"] == PASS]
    no = [label(x["values"]) for x in per if x["verdict"] == FAIL]
    opens = [pid for pid, v in zip(pids, declared) if v is None]
    summary = (f"passes for {len(ok)} of {len(per)} combinations" + (f" ({'; '.join(ok[:4])}"
               f"{'; ...' if len(ok) > 4 else ''})" if ok else "") + (f"; fails for {len(no)}" if no else ""))
    return _res(UNKNOWN, {"sweep": per}, f"{inner} over {', '.join(pids)}",
                [summary + f" - {', '.join(opens)} OPEN: the verdict waits for the value"] + rows)


@check("event")
def c_event(ctx, spec, P):
    """An event of a kind (and source / text) occurs after the origin (within a limit), or never (``expect``
    false)."""
    run = ctx.run(spec)
    o = origin_time(run, P.get("origin", "t0"))
    if o is None:
        return _res(NA, {}, "", ["the origin does not occur"], {"run": run.key})
    kind, src, text = P.get("kind"), P.get("source"), P.get("text")
    evs = [e for e in run.result.events if e["t"] >= o - 1e-12 and (kind is None or e["kind"] == kind)
           and (src is None or str(e["source"]) == src or str(src) in str(e["source"]))
           and (text is None or str(text) in e["text"])]
    first = evs[0] if evs else None
    what = " / ".join(str(x) for x in (kind, src, text) if x)
    meas = {"occurs": bool(first), "after_ms": None if first is None else (first["t"] - o) * 1e3,
            "text": None if first is None else first["text"], "count": len(evs)}
    if P.has("min_count"):                  # a repetition (e.g. a selection that keeps switching): how often
        n = int(P.get("min_count"))
        ts = np.array([e["t"] for e in evs])
        gaps = np.diff(ts) * 1e3 if len(evs) > 1 else np.array([])
        per = np.diff(ts[::2]) * 1e3 if len(evs) > 2 else np.array([])       # a two-way alternation's period
        meas["median_interval_ms"] = float(np.median(gaps)) if gaps.size else None
        meas["median_period_ms"] = float(np.median(per)) if per.size else None
        return _res(PASS if len(evs) >= n else FAIL, meas, f"{what} at least {n} times",
                    [f"{len(evs)} x {what}" + (f", every second one {meas['median_period_ms']:.4g} ms apart (the "
                                               f"period of an alternation)" if per.size else "")], {"run": run.key})
    if P.get("expect", True) is False:
        return _res(PASS if first is None else FAIL, meas, f"no {what}",
                    [f"no {what}" if first is None else f"{what} at {first['t'] * 1e3:.4g} ms: {first['text']}"],
                    {"run": run.key})
    if first is None:
        return _res(FAIL, meas, what, [f"no {what} after the origin"], {"run": run.key})
    v, r = _budget(what, first["t"] - o, _ms(P.get("within_ms")), P.opened("within_ms"))
    return _res(v or PASS, meas, what, [r, first["text"]], {"run": run.key})


# ------------------------------------------------------------------------------------------------- package checks

def _refs(obj, out: set):
    if isinstance(obj, str) and obj.startswith("$") and len(obj) > 1 and not obj.startswith("$$"):
        out.add(obj[1:])
    elif isinstance(obj, dict):
        for v in obj.values():
            _refs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _refs(v, out)
    return out


def item_parameters(pkg: dict, item: dict) -> set:
    """Every parameter an item's checks use: in the check parameters and in the scenarios (and variants) they run."""
    sc = pkg.get("scenarios") or {}
    out: set = set()
    for c in item.get("checks") or []:
        _refs(c.get("params") or {}, out)
        swept = (c.get("params") or {}).get("param")
        if swept:
            out.update([swept] if isinstance(swept, str) else list(swept))
        s = sc.get(c.get("scenario")) or {}
        _refs(s.get("scenario") or {}, out)
        for v in ([c.get("variant")] if c.get("variant") else []) + list(c.get("variants") or []):
            _refs((s.get("variants") or {}).get(v) or {}, out)
    return out


@check("open_kept")
def c_open_kept(ctx, spec, P):
    """The named parameters stay OPEN in the package (no guessed value) - a value entered by the user for a run is
    shown as such; the items that wait for each are listed."""
    pkg = ctx.package
    ids = list(P.raw.get("params") or ())
    rows, bad = [], []
    for pid in ids:
        p = ctx.params.get(pid)
        dep = [it["id"] for it in pkg.get("items", []) if pid in item_parameters(pkg, it)]
        if p is None:
            bad.append(f"{pid} is not in the parameter registry")
            continue
        if p.get("value") is not None and p.get("provenance") == "OPEN":
            bad.append(f"{pid} is OPEN but carries the value {p['value']!r}")
        state = ("entered by the user for this run" if pid in ctx.values else
                 "declared" if p.get("value") is not None else "OPEN (no value)")
        rows.append(f"{pid}: {state}; {len(dep)} item(s) depend on it"
                    + (f" ({', '.join(dep[:6])}{', ...' if len(dep) > 6 else ''})" if dep else ""))
    return _res(PASS if not bad else FAIL, {"parameters": len(ids)}, "kept OPEN until the source gives the value",
                bad + rows)


@check("package_coverage")
def c_package_coverage(ctx, spec, P):
    """Every item of the package has a machine check or a stated manual reason, every parameter reference resolves,
    and the requirement-to-evidence matrix therefore has a row with a verdict for everything the source lists."""
    pkg = ctx.package
    items = pkg.get("items", [])
    params = {p["id"] for p in pkg.get("parameters", [])}
    empty = [i["id"] for i in items if not i.get("checks") and not i.get("manual")]
    unknown = sorted({r for i in items for r in item_parameters(pkg, i)} - params)
    sc_used = {c.get("scenario") for i in items for c in i.get("checks") or []}
    unused = sorted(set(pkg.get("scenarios") or {}) - sc_used)
    by_kind: dict = {}
    for i in items:
        by_kind[i.get("kind", "")] = by_kind.get(i.get("kind", ""), 0) + 1
    machine = sum(1 for i in items if any(c.get("check") != "manual" for c in i.get("checks") or []))
    bad = [f"{len(empty)} item(s) without a check or a manual reason: {', '.join(empty[:8])}"] if empty else []
    bad += [f"unknown parameter reference(s): {', '.join(unknown[:8])}"] if unknown else []
    return _res(PASS if not bad else FAIL, {"items": len(items), "machine_checked": machine, "by_kind": by_kind,
                                             "unused_scenarios": unused},
                "a verdict row for every item", bad + [f"{len(items)} items, {machine} with a machine check; "
                                                       f"kinds {by_kind}"] +
                ([f"scenarios not used by any item: {', '.join(unused)}"] if unused else []))


@check("question")
def c_question(ctx, spec, P):
    """An open question to the customer or an internal owner: MANUAL until answered; lists which OPEN parameters
    the answer closes and how many items wait for it (the order in which to ask)."""
    pkg = ctx.package
    it = ctx.item(spec.get("_item"))
    closes = list(P.raw.get("closes") or ())
    dep = sorted({i["id"] for i in pkg.get("items", []) for pid in closes if pid in item_parameters(pkg, i)})
    st = (it.get("status") or "OPEN").upper()
    if st == "DONE" and it.get("evidence"):
        return _res(PASS, {"closes": closes, "waiting_items": len(dep)}, "answered",
                    [f"answer recorded: {it.get('evidence')}"])
    return _res(MANUAL, {"closes": closes, "waiting_items": len(dep)}, "an answer from the owner",
                [f"closes {', '.join(closes) or '-'}; {len(dep)} item(s) wait for it"
                 + (f": {', '.join(dep[:10])}{', ...' if len(dep) > 10 else ''}" if dep else "")])


@check("model_interfaces")
def c_model_interfaces(ctx, spec, P):
    """The interfaces / fault classes / mechanisms a source lists are represented in the simulation model (by name:
    ``signal:`` vehicle input, ``fault:`` fault kind, ``mech:`` mechanism kind, ``channel:`` recorded channel,
    ``sensor:`` a sensor kind of the design, ``role:`` a sensor role).  Presence in the model, not the real circuit."""
    from .engine import FAULT_KINDS, _Recorder
    from .protection import KIND_PARAMS
    from .system import SIGNALS
    data = ctx.design_data()
    sensors = {s.get("kind") for s in data.get("sensors") or []}
    roles = set((data.get("roles") or {}).keys()) | set((data.get("roles") or {}).values())
    sys_ch = {"sys_permit", "sys_reasons", "sys_latch", "sys_confirmed", "sys_state", "src_LV", "src_HV",
              "itf_pos_limit", "itf_ext_active", "itf_fallback", "rx_age", "em2_i_dc", "tw_hi", "tw_lo", "tw_tol",
              "tw_int"}

    def has(ref: str) -> bool:
        kind, _, name = ref.partition(":")
        return {"signal": name in SIGNALS, "fault": name in FAULT_KINDS, "mech": name in KIND_PARAMS,
                "channel": name in _Recorder.KEYS or name in sys_ch or name.startswith("rail_"),
                "sensor": name in sensors, "role": name in roles}.get(kind, False)
    rows, miss = [], []
    for label, refs in (P.raw.get("interfaces") or {}).items():
        refs = [refs] if isinstance(refs, str) else list(refs)
        gone = [r for r in refs if not has(r)]
        if not refs:
            miss.append(f"{label}: not represented in the model")
        elif gone:
            miss.append(f"{label}: missing {', '.join(gone)}")
        else:
            rows.append(f"{label}: {', '.join(refs)}")
    return _res(PASS if not miss else UNKNOWN, {"represented": len(rows), "missing": len(miss)},
                "every listed interface is represented in the simulation", miss + rows +
                ["presence in the simulation model - the real circuit, pin map and data base are separate evidence"])


@check("model_tables")
def c_model_tables(ctx, spec, P):
    """Declared tables against the implementation: the power-stage states each operating state allows, and the
    naming of the bridge states (ASO = six-switch-off, APS = ASC, PWM)."""
    from .system import BRIDGE_STAGE, DEFAULT_ALLOW
    bad, rows = [], []
    for st, want in (P.raw.get("allow") or {}).items():
        got = set(DEFAULT_ALLOW.get(st, ()))
        if set(want) != got:
            bad.append(f"{st}: implemented {sorted(got)} vs declared {sorted(want)}")
        else:
            rows.append(f"{st}: {'/'.join(sorted(got))}")
    for code, stage in (P.raw.get("stages") or {}).items():
        if BRIDGE_STAGE.get(code) != stage:
            bad.append(f"{code}: implemented {BRIDGE_STAGE.get(code)} vs declared {stage}")
        else:
            rows.append(f"{code} = {stage}")
    return _res(PASS if not bad else FAIL, {"mismatches": len(bad)}, "the declared tables as implemented",
                bad + rows)


@check("passive_discharge")
def c_passive_discharge(ctx, spec, P):
    """The bleeder alone: V(t) = V0 exp(-t / (R C)) - time to the limit against the deadline (the battery
    disconnected, the bleeder always across the link; the product's declared R and C)."""
    from .configure import build_setup
    setup, _r, _i = build_setup(ctx.product, {"speed_rpm": 0.0, "torque_Nm": 0.0, "horizon_ms": 1.0})
    R, C = setup.dc.R_bleed, setup.dc.C
    v0 = float(P.get("v0_V") or setup.dc.V_oc)
    lim = float(P.get("v_limit_V", 60.0))
    if not R:
        return _res(FAIL, {}, "", ["no bleeder declared: the link does not discharge passively"])
    t = R * C * math.log(v0 / lim) if v0 > lim else 0.0
    meas = {"t_s": t, "R_ohm": R, "C_uF": C * 1e6, "v0_V": v0}
    dl = P.get("deadline_s")
    reasons = [f"R {R:g} ohm, C {C * 1e6:g} uF (tau {R * C:.4g} s): {v0:g} V -> {lim:g} V in {t:.4g} s"]
    if dl is None:
        return _res(UNKNOWN if P.opened("deadline_s") else PASS, meas, "", reasons +
                    ([f"passes if {P.opened('deadline_s')} >= {t:.4g} s (OPEN)"] if P.opened("deadline_s") else []))
    ok = t <= float(dl) + 1e-12
    return _res(PASS if ok else FAIL, meas, f"below {lim:g} V within {dl:g} s by the bleeder alone",
                reasons + [f"{'<=' if ok else '>'} {dl:g} s"])


@check("threshold_ratio")
def c_threshold_ratio(ctx, spec, P):
    """A comparator threshold against the product's normal range: threshold / reference within ratio x (1 +- tol)
    and above the normal maximum (the reference: the inverter's phase-current limit or the DC source's discharge
    current limit)."""
    if spec.get("scenario"):
        setup = ctx.run(spec).setup
    else:
        from .configure import build_setup
        setup, _r, _i = build_setup(ctx.product, {"speed_rpm": 0.0, "torque_Nm": 0.0, "horizon_ms": 1.0})
    m = next((m for m in setup.mechanisms if m.mech_id == P.get("mech")), None)
    if m is None:
        return _res(UNKNOWN, {}, "", [f"no mechanism {P.get('mech')} in the design / scenario"])
    thr = next((float(m.params[k]) for k in ("threshold_A", "threshold_V") if k in m.params), None)
    ref = P.get("reference", "inverter_current_limit")
    if ref == "inverter_current_limit":
        ref_v = float(ctx.product.drive.inverter.current_limit_A_peak)
    elif ref == "dc_discharge_current_limit":
        ref_v = (ctx.product.project.data("dc_source").get("limits") or {}).get("discharge_current_max_A")
        ref_v = None if ref_v is None else float(ref_v)
    else:
        ref_v = P.get("reference_value")
    meas = {"threshold": thr, "reference": ref_v, "ratio": None if not ref_v or thr is None else thr / ref_v}
    if thr is None or not ref_v:
        return _res(UNKNOWN, meas, "", [f"threshold {thr} / reference {ref} {ref_v}: not both known"])
    ratio, tol = P.get("ratio"), P.get("tol", 0.0)
    reasons = [f"{P.get('mech')} threshold {thr:g} = {thr / ref_v:.3g} x {ref} ({ref_v:g})"]
    verdicts = [PASS if thr > ref_v else FAIL]
    reasons.append("above the normal range" if thr > ref_v else "inside the normal range: normal operation can trip it")
    if ratio is None:
        verdicts.append(UNKNOWN)
        reasons.append(f"the ratio {P.opened('ratio')} OPEN")
    else:
        lo, hi = float(ratio) * (1 - float(tol or 0.0)), float(ratio) * (1 + float(tol or 0.0))
        ok = lo - 1e-9 <= thr / ref_v <= hi + 1e-9
        verdicts.append(PASS if ok else FAIL)
        reasons.append(f"{thr / ref_v:.3g} {'within' if ok else 'outside'} [{lo:.3g}, {hi:.3g}]")
    return _res(worst(verdicts), meas, "threshold outside the normal range at the declared ratio", reasons)
