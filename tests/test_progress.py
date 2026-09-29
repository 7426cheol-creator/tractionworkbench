"""Progress and cancellation inside long calculations (UX review A3, A5; ``traction_workbench.progress``).

* without a listener a span is inert, and a listener never changes a result (every decision preset, analyses on);
* nested loops report a fraction that never goes back, inside the stretch the run gave them;
* a stop requested at an engine step ends the calculation there, also through the engine's broad failure handlers
  (``except Exception`` -> UNKNOWN): a stop never becomes a verdict or a finding;
* the PWM line spectrum is the same sum evaluated a block at a time (bit for bit, without the lines x edges matrix).
"""

import json

import numpy as np
import pytest

from traction_workbench import api, progress as P, service as S
from traction_workbench.extensions import emi


def _record(case) -> str:
    d = S.evaluate_case_full(case)[0]
    d.pop("elapsed_s")
    return json.dumps(d, sort_keys=True, default=str)


def _case(preset) -> dict:
    return api.case_from_body({"requirement": preset["req"], "analyses": preset.get("analyses", {})})


def test_a_span_without_a_listener_is_inert():
    with P.span(3, "x") as sp:
        sp.step()
        sp.remaining(10)
        assert sp.levels() == () and sp.fraction == 0.0


def test_nested_loops_move_the_fraction_forward_inside_their_stretch():
    seen = []
    with P.listening(lambda f, sp: seen.append((f, sp.levels()))) as root:
        with P.span(2, "outer") as a:                   # outside any stretch: named steps, no fraction
            a.step()
        P.at(root, 0.2, 0.8)
        with P.span(2, "outer") as a:
            for _ in range(2):
                a.step("o")
                with P.span(4, "inner") as b:
                    for _ in range(3):
                        b.step()
                    b.remaining(1)                      # the length became known late: the rest is shared
                    b.step()
            with P.span(5, "same step") as c:           # a second loop in a used-up step names its steps only
                c.step()
        P.at(root, 0.9)
        with P.span(3, "after") as d:
            d.step()
    fr = [f for f, _ in seen]
    assert all(y >= x for x, y in zip(fr, fr[1:]))
    assert seen[0][0] == 0.0 and seen[0][1][0].label == "outer"
    inside = [f for f, lv in seen if lv[-1].label == "inner"]
    assert len(inside) == 8 and 0.2 <= min(inside) and max(inside) < 0.8
    assert [f for f, lv in seen if lv[-1].label == "same step"] == [0.8]
    assert seen[-1][0] == 0.9 and seen[-1][1] == (P.Level("after", 1, 3),)
    lv = next(lv for _, lv in seen if len(lv) == 2 and lv[1].label == "inner")
    assert lv[0] == P.Level("outer", 1, 2, "o") and lv[1] == P.Level("inner", 1, 4)


def test_a_listener_never_changes_a_result():
    for p in api.PRESETS:
        case = _case(p)
        steps = []
        with P.listening(lambda f, sp: steps.append(sp.levels())):
            got = _record(case)
        assert got == _record(case), p["key"]
        assert any(lv and lv[-1].label == "torque capability" for lv in steps), p["key"]


def test_a_stop_ends_the_calculation_and_never_becomes_a_verdict():
    """The trade study judges each check inside ``except Exception`` (a failed check is UNKNOWN, never a pass): a stop
    raised at a step inside a check passes through it."""
    assert issubclass(P.Cancelled, BaseException) and not issubclass(P.Cancelled, Exception)
    n = {"steps": 0}

    def stop_at_20(frac, sp):
        n["steps"] += 1
        if n["steps"] >= 20:
            raise P.Cancelled()
    case = _case(api.PRESETS[0])
    with P.listening(stop_at_20), pytest.raises(P.Cancelled):
        S.evaluate_case_full(case)
    assert n["steps"] == 20
    # the trade study reports a failed check as UNKNOWN ("never a pass"); a stop is not a failed check
    n["steps"] = 0
    with P.listening(stop_at_20), pytest.raises(P.Cancelled):
        api.machine_trade({})
    assert n["steps"] == 20


@pytest.mark.parametrize("n_f, n_e", [(0, 5), (7, 0), (1, 1), (37, 13), (5000, 60)])
def test_the_line_spectrum_is_the_same_sum_a_block_at_a_time(monkeypatch, n_f, n_e):
    rng = np.random.default_rng(n_f * 1000 + n_e)
    t0, tau, dv = rng.uniform(0, 1e-3, n_e), rng.uniform(1e-8, 1e-6, n_e), rng.normal(0, 300, n_e)
    freqs, T = np.linspace(1e3, 5e6, n_f), 1e-3

    def dense():
        f = np.asarray(freqs, dtype=float)[:, None]
        ph = np.exp(-1j * emi.TWO_PI * f * (np.asarray(t0)[None, :] + 0.5 * np.asarray(tau)[None, :]))
        sinc = np.sinc(f * np.asarray(tau)[None, :])
        s = (ph * sinc * np.asarray(dv)[None, :]).sum(axis=1)
        return 2.0 * s / (1j * emi.TWO_PI * f[:, 0] * T)
    for block in (1, 64, emi.LINE_BLOCK):
        monkeypatch.setattr(emi, "LINE_BLOCK", block)
        got = emi.edge_lines(t0, tau, dv, freqs, T)
        assert got.shape == (n_f,) and np.array_equal(got, dense())
