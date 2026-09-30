import json
import math
from pathlib import Path

import pytest

from traction_workbench import spec_fixtures as sf

SPEC = Path(__file__).resolve().parents[1] / "reference" / "traction_workbench_spec_v1"


def golden(name: str) -> dict:
    """Read an immutable reference fixture directly from disk (never from production output)."""
    return json.loads((SPEC / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def drive():
    return sf.synthetic_drive()


@pytest.fixture(scope="session")
def limits():
    return sf.synthetic_limits()


def scenario(n, vdc, scenario_id=None, **kw):
    return sf.synthetic_scenario(n, vdc, scenario_id, **kw)


def emi_rail_lines(E, src, net, f):
    """Port lines {model: [V+, V-]} of the four CM return models computed independently of the engine's band path
    (review 3 F-23): the CM lines of the rising and of the falling edges and the DM lines from the edge sums, every
    model a superposition of solve_network runs at the rail taps."""
    import numpy as np
    e = E.pwm_edges(src)
    T = e["period_s"]
    _, t0, sgn, tau, cur = (np.asarray(x, dtype=float) for x in zip(*e["edges"]))
    r = sgn > 0
    cm_r = E.edge_lines(t0[r], tau[r], sgn[r] * src.Vdc_V / 3.0, f, T)
    cm_f = E.edge_lines(t0[~r], tau[~r], sgn[~r] * src.Vdc_V / 3.0, f, T)
    dm = E.edge_lines(t0, tau, sgn * cur, f, T)
    zero = 0.0 * dm

    def ports(v, i, alpha):
        x = E.solve_network(net, f, v, i, alpha)
        return np.stack([x["v_meas_plus"], x["v_meas_minus"]])
    return {"midpoint": ports(cm_r + cm_f, dm, 0.5),
            "edge_sign": ports(cm_r, zero, 1.0) + ports(cm_f, zero, 0.0) + ports(zero, dm, 0.5),
            "hv_plus": ports(cm_r + cm_f, dm, 1.0),
            "hv_minus": ports(cm_r + cm_f, dm, 0.0)}


def envelope_brute(c, n=1 << 16):
    """max over t of |sum_k c_k exp(j 2 pi k t)| on n equally spaced instants: a dense-sampling lower bound of the
    rectangular-IF peak reading, within (pi (d - 1) / n)^2 / 4 of it."""
    import numpy as np
    return float(np.max(np.abs(np.fft.ifft(np.asarray(c, dtype=complex), n=n))) * n)
