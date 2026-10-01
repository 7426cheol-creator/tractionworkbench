"""The reaction transient read from a trace (``faultsim.transient``) and its figures.

* the extremes are the simulated samples themselves (value, instant, the instant after the fault and after the
  reaction, the local sample spacing) - checked on a synthetic trace with known peaks and a refined step;
* the phase-current extreme is the largest |i| of the three phases, with its phase and sign;
* the time outside a bound, the first exceedance and the settling time (a never-settling quantity says so);
* the zoom window holds the fault, the reaction and every extreme, with margins, inside the run;
* the waveform figure keeps a far limit off the scale (named at the edge with its margin) and marks the extremes; the
  zoomed figure shows the window.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from traction_workbench.extensions.faultsim.transient import transient_metrics, transient_window


def synthetic(fault_ms=10.0, react_ms=11.0):
    """A 40 ms run, 10 us steps refined to 1 us between 10.4 and 10.6 ms: a battery-current spike to 400 A at
    10.5 ms (after the current fell from 150 A at the fault), a V_dc bump to 612 V at 11.2 ms, a phase current peak
    of -1400 A (phase b) at 12.5 ms and a torque oscillation that decays (settles) after the reaction."""
    t = np.unique(np.concatenate([np.arange(0, 40e-3, 10e-6), np.arange(10.4e-3, 10.6e-3, 1e-6)]))
    tf, tr_ = fault_ms * 1e-3, react_ms * 1e-3
    after = np.clip(t - tf, 0, None)
    T = np.where(t < tf, 150.0, -600.0 * np.exp(-after / 3e-3) * np.cos(2 * np.pi * 250 * after))
    i_bat = np.where(t < tf, 150.0, 0.0) + 400.0 * np.exp(-((t - 10.5e-3) / 20e-6) ** 2)
    v_dc = 600.0 + 12.0 * np.exp(-((t - 11.2e-3) / 50e-6) ** 2)
    w = 2 * np.pi * 400 * t
    amp = 200.0 + 1100.0 * np.exp(-((t - 12.5e-3) / 1e-3) ** 2)
    i_a, i_b, i_c = (amp * np.cos(w - k * 2 * np.pi / 3) for k in range(3))
    k = int(np.argmin(np.abs(t - 12.5e-3)))
    i_b = i_b.copy()
    i_b[k] = -1400.0                                                   # a sharp negative peak on phase b
    trace = {"t": t, "T_shaft": T, "T_em": T, "i_a": i_a, "i_b": i_b, "i_c": i_c, "v_dc": v_dc, "i_bat": i_bat}
    events = [{"t": tf, "kind": "fault", "source": "sensor"}, {"t": tr_, "kind": "actuation", "source": "SM"}]
    return {"trace": trace, "events": events, "scenario": {"faults": [{"t_ms": fault_ms}]}}


@pytest.fixture(scope="module")
def m():
    return transient_metrics(synthetic(), bounds={"v_dc": (None, 610.0), "i_phase": (None, 1200.0)})


def row(m, q):
    return next(r for r in m["rows"] if r["quantity"] == q)


def test_the_extreme_is_the_simulated_sample(m):
    r = row(m, "i_bat")
    assert r["max"] == pytest.approx(400.0, abs=1e-6) and r["min"] == pytest.approx(0.0, abs=1e-6)
    assert r["extreme"] == r["max"]                                     # the largest change from the 150 A before
    assert r["t_max_s"] == pytest.approx(10.5e-3, abs=0.5e-6)
    assert r["resolution_us"] == pytest.approx(1.0, abs=1e-6)            # the refined neighbourhood (the coarser
    #                                                                     step next to it, not a duplicate sample)
    assert r["after_fault_ms"] == pytest.approx(0.5, abs=1e-3)
    assert r["after_reaction_ms"] == pytest.approx(-0.5, abs=1e-3)       # the spike came before the reaction
    v = row(m, "v_dc")
    assert v["max"] == pytest.approx(612.0, abs=1e-3) and v["t_max_s"] == pytest.approx(11.2e-3, abs=5e-6)
    assert v["resolution_us"] == pytest.approx(10.0, abs=1e-6)


def test_phase_extreme_names_its_phase_and_sign(m):
    r = row(m, "i_phase")
    assert r["phase"] == "b" and r["extreme"] == pytest.approx(-1400.0)
    assert r["t_extreme_s"] == pytest.approx(12.5e-3, abs=5e-6)
    assert r["pre"] == pytest.approx(200.0, rel=0.02)                    # the pre-fault amplitude


def test_time_outside_the_bound_and_first_exceedance(m):
    v = row(m, "v_dc")
    t = synthetic()["trace"]["t"]
    vv = synthetic()["trace"]["v_dc"]
    out = (vv > 610.0) & (t >= 10e-3)
    expect = float(np.sum(np.diff(t)[out[:-1]])) * 1e3
    assert v["outside_bound_ms"] == pytest.approx(expect, rel=1e-9) and expect > 0
    assert v["first_outside_ms"] == pytest.approx((t[np.argmax(out)] - 10e-3) * 1e3)


def test_settling_and_a_quantity_that_never_settles():
    res = synthetic()
    m = transient_metrics(res)
    T = row(m, "T_shaft")
    assert T["final"] == pytest.approx(0.0, abs=1.0)
    t, x = res["trace"]["t"], res["trace"]["T_shaft"]
    post = t >= 10e-3
    band = 0.05 * np.max(np.abs(x[post] - T["final"]))                  # 5 % of the excursion around the final value
    last = np.where(post & (np.abs(x - T["final"]) > band))[0][-1]
    assert T["settling_ms"] == pytest.approx((t[last + 1] - 10e-3) * 1e3)
    assert 8.0 < T["settling_ms"] < 10.0                                # 600 exp(-t / 3 ms) = 30 N*m at 9 ms
    res["trace"]["v_dc"] = 600.0 + 10.0 * np.sin(2 * np.pi * 1000 * res["trace"]["t"])     # rings to the end
    assert row(transient_metrics(res), "v_dc")["settling_ms"] is None


def test_zoom_window_holds_the_transient(m):
    a, b = m["window_s"]
    peaks = [r["t_extreme_s"] for r in m["rows"] if r["quantity"] in ("T_shaft", "i_phase", "v_dc", "i_bat")]
    assert a < 10e-3 < 11e-3 < b and all(a <= p <= b for p in peaks)
    assert 0 <= a and b <= 40e-3
    assert transient_window({"trace": {"t": np.arange(0, 1e-2, 1e-5)}, "events": []}) == (0.0, pytest.approx(
        1e-2 - 1e-5))                                                    # no fault: the whole run


def test_no_trace_is_not_an_error():
    out = transient_metrics({"trace": {"t": []}, "events": []})
    assert out["rows"] == [] and out["window_s"] is None


def test_figures_mark_extremes_and_keep_far_limits_off_scale():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from traction_workbench.plots import fault_figures as FF
    res = synthetic()
    tr_ = res["trace"]
    n = len(tr_["t"])
    for k in ("T_request", "T_cmd", "T_est_mon", "mon_lo", "mon_hi"):
        tr_[k] = np.full(n, np.nan) if k in ("T_est_mon", "mon_lo", "mon_hi") else np.zeros(n)
    tr_["i_a_meas"], tr_["v_dc_meas"] = tr_["i_a"], tr_["v_dc"]
    tr_["bridge"], tr_["mcu"] = np.where(tr_["t"] >= 11e-3, 1, 0), np.zeros(n)
    res["evaluation"] = {"tsr": [{"id": "TSR-06", "type": "bound", "criterion": {"quantity": "v_dc", "max": 850.0}}]}
    fig = Figure(figsize=(10, 9))
    FF.fig_fault_waveforms(fig, res)
    ax_v = fig.axes[2]
    lo, hi = ax_v.get_ylim()
    assert hi < 700.0                                                   # 850 V would flatten the 12 V bump
    texts = [t.get_text() for t in ax_v.texts]
    assert any("TSR-06 850 V" in t and ("off scale" in t or "축 밖" in t) for t in texts)
    notes = [c.get_text() for a in fig.axes for c in a.get_children() if hasattr(c, "arrow_patch")]
    assert any("612" in s and "11.200 ms" in s for s in notes)          # V_dc max with its instant
    assert any("-1400" in s and "i_b" in s for s in notes)
    fig = Figure(figsize=(10, 9))
    FF.fig_fault_transient(fig, res)
    a, b = transient_metrics(res)["window_s"]
    assert fig.axes[0].get_xlim() == pytest.approx((a * 1e3, b * 1e3))
    assert math.isfinite(fig.axes[0].get_ylim()[0])
