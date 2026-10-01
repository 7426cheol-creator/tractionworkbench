"""The reaction transient of one fault simulation, read from its trace as simulated: per quantity the pre-fault value,
the extreme after the fault with its instant (absolute, after the fault, after the reaction), the local sample
spacing at that instant (the resolution the value was simulated with), the time outside a declared bound and the
settling time; and the window that holds the transient (for a zoomed view).

Nothing is interpolated or filtered: an extreme is a simulated sample (the engine refines its step at every event
and switching edge; the reported spacing says how finely the neighbourhood was resolved).
"""

from __future__ import annotations

import numpy as np

# quantity -> (trace keys, unit, kind): "signed" reads the value, "phase" the largest |i| of the three phases
QUANTITIES = {
    "T_shaft": (("T_shaft",), "N*m", "signed"),
    "T_em": (("T_em",), "N*m", "signed"),
    "i_phase": (("i_a", "i_b", "i_c"), "A", "phase"),
    "v_dc": (("v_dc",), "V", "signed"),
    "i_bat": (("i_bat",), "A", "signed"),
}


def _t_fault(res) -> float | None:
    ev = res.get("events") or []
    ts = [e["t"] for e in ev if e.get("kind") == "fault"]
    if ts:
        return float(min(ts))
    faults = ((res.get("scenario") or {}).get("faults") or [])
    ts = [f.get("t_ms", 0.0) * 1e-3 for f in faults]
    return float(min(ts)) if ts else None


def _t_reaction(res, t0: float | None) -> float | None:
    ev = res.get("events") or []
    ts = [e["t"] for e in ev if e.get("kind") == "actuation" and (t0 is None or e["t"] >= t0 - 1e-12)]
    return float(min(ts)) if ts else None


def _series(tr: dict, q: str):
    keys, unit, kind = QUANTITIES[q]
    if any(k not in tr for k in keys):
        return None, None
    if kind == "phase":
        m = np.vstack([np.asarray(tr[k], dtype=float) for k in keys])
        return m, unit
    return np.asarray(tr[keys[0]], dtype=float), unit


def _spacing(t: np.ndarray, k: int) -> float:
    """The coarser of the two steps next to sample ``k``: the true extreme lies within that of the sample."""
    steps = [d for d in ((t[k] - t[k - 1]) if k > 0 else None, (t[k + 1] - t[k]) if k + 1 < t.size else None)
             if d is not None]
    return float(max(steps)) if steps else 0.0


def _duration(t: np.ndarray, mask: np.ndarray) -> float:
    """Time the sample mask holds (a sample holds until the next sample)."""
    if t.size < 2:
        return 0.0
    dt = np.diff(t)
    return float(np.sum(dt[mask[:-1]]))


def transient_metrics(res: dict, bounds: dict | None = None, settle_rel: float = 0.05) -> dict:
    """The reaction transient of the result ``res`` (``api.fault_sim``).

    ``bounds``: {quantity: (lo, hi)} limits (None: no limit on that side) whose exceedance time is reported (the
    phase currents by magnitude).  Settling: the time after the fault from which the quantity stays within
    ``settle_rel`` of its excursion around its final value (the median of the last tenth of the run; the phase
    currents by their envelope max(|i_a|, |i_b|, |i_c|), whose six-pulse ripple of up to 13.4 % is inside the band);
    None when it does not settle before that last tenth."""
    tr = res.get("trace") or {}
    t = np.asarray(tr.get("t", []), dtype=float)
    t_f = _t_fault(res)
    t_r = _t_reaction(res, t_f)
    rows = []
    if t.size < 2:
        return {"t_fault_s": t_f, "t_reaction_s": t_r, "rows": rows, "window_s": None}
    a = t_f if t_f is not None else float(t[0])
    post = t >= a - 1e-12
    pre = (t >= a - 1e-3) & (t < a - 1e-12)
    tail = t >= t[0] + 0.9 * (t[-1] - t[0])
    bounds = bounds or {}
    for q in QUANTITIES:
        x, unit = _series(tr, q)
        if x is None:
            continue
        phase = x.ndim == 2
        if phase:
            mag = np.abs(x)
            env = mag.max(axis=0)
            val = env
        else:
            val = x
        if not post.any():
            continue
        idx = np.where(post)[0]
        vpost = val[idx]
        fin = np.isfinite(vpost)
        if not fin.any():
            continue
        if phase:
            k = int(idx[np.nanargmax(np.where(fin, vpost, -np.inf))])
            ph = int(np.argmax(mag[:, k]))
            signed = float(x[ph, k])
            row = {"quantity": q, "unit": unit, "phase": "abc"[ph], "extreme": signed, "t_extreme_s": float(t[k]),
                   "max": float(np.nanmax(x[:, idx])), "min": float(np.nanmin(x[:, idx]))}
            pre_v = float(np.nanmax(env[pre])) if pre.any() else None           # the pre-fault amplitude
        else:
            kmax = int(idx[np.nanargmax(np.where(fin, vpost, -np.inf))])
            kmin = int(idx[np.nanargmin(np.where(fin, vpost, np.inf))])
            pre_v = float(np.nanmean(val[pre])) if pre.any() else float(val[idx[0]])
            k = kmax if abs(val[kmax] - pre_v) >= abs(val[kmin] - pre_v) else kmin
            row = {"quantity": q, "unit": unit, "phase": None, "extreme": float(val[k]), "t_extreme_s": float(t[k]),
                   "max": float(val[kmax]), "t_max_s": float(t[kmax]), "min": float(val[kmin]),
                   "t_min_s": float(t[kmin])}
        row["pre"] = pre_v
        row["excursion"] = None if pre_v is None else (abs(row["extreme"]) - pre_v if phase else
                                                         row["extreme"] - pre_v)
        row["after_fault_ms"] = None if t_f is None else (row["t_extreme_s"] - t_f) * 1e3
        row["after_reaction_ms"] = None if t_r is None else (row["t_extreme_s"] - t_r) * 1e3
        row["resolution_us"] = _spacing(t, k) * 1e6          # at the extreme
        lo, hi = bounds.get(q, (None, None))
        if lo is not None or hi is not None:
            v = env if phase else val
            out = np.zeros(t.size, dtype=bool)
            if hi is not None:
                out |= v > hi
            if lo is not None:
                out |= v < lo
            out &= post
            row["bound"] = [lo, hi]
            row["outside_bound_ms"] = _duration(t, out) * 1e3
            row["first_outside_ms"] = None if not out.any() else (float(t[np.argmax(out)]) - a) * 1e3
        s = env if phase else val
        final = float(np.nanmedian(s[tail])) if tail.any() else float(s[-1])
        peak = float(np.nanmax(np.abs(s[idx] - final)))
        band = settle_rel * peak
        if phase:
            band = max(band, 0.15 * abs(final))
        outside = post & (np.abs(s - final) > band)
        row["final"] = final
        if not outside.any():
            row["settling_ms"] = 0.0
        else:
            last = int(np.where(outside)[0][-1])
            t_last = float(t[min(last + 1, t.size - 1)])
            row["settling_ms"] = None if tail[last] else (t_last - a) * 1e3
        rows.append(row)
    return {"t_fault_s": t_f, "t_reaction_s": t_r, "rows": rows, "window_s": transient_window(res, rows)}


def transient_window(res: dict, rows: list | None = None) -> tuple[float, float] | None:
    """The time window that holds the transient: from shortly before the fault to after the reaction and the latest
    extreme of the torque, the phase currents, the DC-link voltage and the battery current (with margins scaled to
    the transient's own length); the whole run when there is no fault."""
    tr = res.get("trace") or {}
    t = np.asarray(tr.get("t", []), dtype=float)
    if t.size < 2:
        return None
    t_f = _t_fault(res)
    if t_f is None:
        return float(t[0]), float(t[-1])
    if rows is None:
        rows = transient_metrics(res)["rows"]
    t_r = _t_reaction(res, t_f)
    ends = [r["t_extreme_s"] for r in rows if r["quantity"] in ("T_shaft", "i_phase", "v_dc", "i_bat")]
    if t_r is not None:
        ends.append(t_r)
    end = max(ends) if ends else t_f
    span = max(end - t_f, 0.5e-3)
    a = max(float(t[0]), t_f - max(0.2e-3, 0.15 * span))
    b = min(float(t[-1]), end + 0.3 * span + 0.3e-3)
    return a, b
