"""Regression anchors of the core decision path with the DATASHEET MODULE loss model active.

The golden acceptance and the independent fixture check run the built-in drive (quadratic loss surrogate); these
anchors pin forward losses, policy solutions and claims, capability, the domain reason outside the switching test
voltage and a Vdc sizing boundary with the module model in the core.  Implementation anchors, not an independent
reference: regenerate with ``python verification/make_module_anchor.py`` only for an intended model change.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = json.loads((ROOT / "tests" / "fixtures" / "module_core_anchor.json").read_text(encoding="utf-8"))
REF = DOC["values"]
TOL = DOC["tolerance"]
sys.path.insert(0, str(ROOT / "verification"))
from make_module_anchor import compute  # noqa: E402


@pytest.fixture(scope="module")
def now():
    return compute()


def _num(a, b, rel, abs_=0.0):
    if a is None or b is None:
        return a is b
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def test_forward_module_losses_are_unchanged(now):
    for r, n in zip(REF["forward"], now["forward"]):
        assert (n["evaluable"], n["established"]) == (r["evaluable"], r["established"])
        for key in ("Pinv_W", "Pdc_W", "hottest_position_W"):
            assert _num(n[key], r[key], TOL["forward_rel"]), (key, r, n)


def test_policy_solutions_and_claims_are_unchanged(now):
    for r, n in zip(REF["policy"], now["policy"]):
        assert n["claims"] == r["claims"] and n["policy_claim"] == r["policy_claim"], (r, n)
        for key in ("id_A", "iq_A"):
            assert _num(n[key], r[key], TOL["solver_rel"], TOL["solver_abs_A"]), (key, r, n)
        for key in ("Pdc_W", "Pinv_W"):
            assert _num(n[key], r[key], TOL["solver_rel"]), (key, r, n)


def test_capability_is_unchanged(now):
    for r, n in zip(REF["capability"], now["capability"]):
        assert n["accepted"] == r["accepted"] and _num(n["value_Nm"], r["value_Nm"], TOL["solver_rel"]), (r, n)


def test_outside_the_switching_test_voltage_is_unknown_for_a_domain_reason(now):
    """Without a declared Vdc scaling law the module losses are not extrapolated: the DC claims are UNKNOWN because
    the data do not cover the point (OUTSIDE_MODEL_DOMAIN), not because a loss model were missing."""
    assert now["unscaled_700V"] == REF["unscaled_700V"]
    assert "OUTSIDE_MODEL_DOMAIN" in now["unscaled_700V"]["reasons"]["dc_source"]


def test_scaled_module_sizing_boundary_is_unchanged(now):
    r, n = REF["sizing"], now["sizing"]
    assert n["regions"] == r["regions"]
    assert _num(n["minimal_feasible_value"], r["minimal_feasible_value"], 0.0, 1e-5)
