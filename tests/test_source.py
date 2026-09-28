"""Optional DC source coupling (engineering review 6198099, priority 2): a Vdc stated as the battery OCV is judged at
the inverter terminal voltage of a declared Thevenin source; a terminal-stated Vdc is never reduced again."""

import math

import pytest

from traction_workbench import api
from traction_workbench import spec_fixtures as sf
from traction_workbench.analysis.source import TheveninSource, resolve_terminal_voltage, source_from_dict
from traction_workbench.errors import InputValidationError
from traction_workbench.scenario import Scenario
from traction_workbench.solvers.policy import PolicyEvaluator

BASE = {"id": "R-OCV", "text": "150 N*m at 12000 rpm", "torque_Nm": 150.0, "speed_rpm": 12000.0, "Vdc_V": 600.0}


def _ocv(R_mohm, **req):
    return api.evaluate({"requirement": {**BASE, **req, "Vdc_port": "battery_ocv"},
                         "source_model": {"kind": "thevenin", "R_eq_mohm": R_mohm, "basis": "test pack, 50 % SOC, 25 degC",
                                          **({"valid_current_A": req.pop("valid")} if "valid" in req else {})}})


def test_the_terminal_voltage_is_the_fixed_point_of_the_thevenin_source():
    d, lim = sf.synthetic_drive(), sf.synthetic_limits()
    src = TheveninSource(0.03, "test")
    r = resolve_terminal_voltage(d, Scenario("s", 12000.0, 600.0, lim), src, 150.0)
    V = r["V_terminal_V"]
    pdc = PolicyEvaluator(d, Scenario("s", 12000.0, V, lim)).solve(150.0).point.Pdc_W
    assert r["status"] == "RESOLVED" and V == pytest.approx(600.0 - 0.03 * pdc / V, abs=1e-6)
    assert r["I_dc_A"] == pytest.approx(pdc / V) and r["sag_V"] == pytest.approx(600.0 - V) and r["sag_V"] > 0
    assert V <= r["V_terminal_max_V"]                     # never above the model-free ceiling for this shaft power


def test_regeneration_raises_the_terminal_voltage_above_the_ocv():
    rg = api.evaluate({"requirement": {**BASE, "torque_Nm": -60.0, "Vdc_port": "battery_ocv"},
                       "source_model": {"R_eq_mohm": 20, "basis": "test"}})
    p = rg["source_coupling"]["points"][0]
    assert p["V_terminal_V"] > 600.0 and p["I_dc_A"] < 0 and p["direction"] == "charge"


def test_zero_resistance_is_the_terminal_statement_and_the_drop_only_lowers_the_voltage():
    direct = api.evaluate({"requirement": BASE})
    zero = _ocv(0.0)
    assert zero["conditions"][0]["scenario"]["Vdc_V_inverter_dc_terminal"] == pytest.approx(600.0)
    assert zero["verdict"]["verdict"] == direct["verdict"]["verdict"]
    assert "source_coupling" not in direct and "source_coupling" not in direct["input_snapshot"]   # identity kept
    v = [_ocv(R)["source_coupling"]["V_terminal_V"][0] for R in (5, 20, 60)]
    assert v[0] > v[1] > v[2]
    lay = _ocv(20)["verdict"]["layers"]["requirement"]["open_items"]
    assert any("battery OCV" in x and "20 mOhm" in x for x in lay)


def test_a_source_that_cannot_deliver_the_shaft_power_is_a_proven_fail():
    r = _ocv(2000.0)                                     # V_oc^2 / 4R = 45 kW < 188 kW shaft power
    assert r["verdict"]["verdict"] == "FAIL" and "NECESSARY_CONDITION_VIOLATED" in r["verdict"]["reasons"]
    assert r["source_coupling"]["status"] == "NO_SOLUTION"
    assert any("maximum" in q or "most this source" in q for q in r["verdict"]["qualifiers"])


def test_outside_the_source_model_or_no_fixed_point_is_unknown():
    out = api.evaluate({"requirement": {**BASE, "Vdc_port": "battery_ocv"},
                        "source_model": {"R_eq_mohm": 20, "basis": "t", "valid_current_A": [-100, 100]}})
    assert out["verdict"]["verdict"] == "UNKNOWN" and "OUTSIDE_MODEL_DOMAIN" in out["verdict"]["reasons"]
    col = _ocv(400.0)                                    # the drop takes the voltage where no electrical point exists
    assert col["verdict"]["verdict"] == "UNKNOWN" and col["source_coupling"]["status"] == "NOT_RESOLVED"
    assert any("no operating point can see more than" in q for q in col["verdict"]["qualifiers"])


def test_a_fixed_point_where_the_policy_point_is_lost_is_not_resolved(monkeypatch):
    import types

    from traction_workbench.analysis import source as S
    d, lim = sf.synthetic_drive(), sf.synthetic_limits()
    sc, src = Scenario("s", 12000.0, 600.0, lim), TheveninSource(0.03, "test")
    calls, stop_at = [], [0]

    class Counting(PolicyEvaluator):
        def solve(self, T):
            calls.append(T)
            if len(calls) == stop_at[0]:
                return types.SimpleNamespace(point=None, policy_claim=types.SimpleNamespace(
                    status=types.SimpleNamespace(value="UNKNOWN"), detail="lost at the edge"))
            return super().solve(T)

    monkeypatch.setattr(S, "PolicyEvaluator", Counting)
    assert S.resolve_terminal_voltage(d, sc, src, 150.0)["status"] == "RESOLVED"
    stop_at[0], n = len(calls), len(calls)
    calls.clear()
    r = S.resolve_terminal_voltage(d, sc, src, 150.0)    # the same path; only the solve at the fixed point fails
    assert len(calls) == n and r["status"] == "NOT_RESOLVED" and "at the fixed point" in r["reason"]


def test_an_ocv_range_maps_to_the_terminal_range():
    r = api.evaluate({"requirement": {**BASE, "torque_Nm": 100.0, "Vdc_V": [550, 650], "Vdc_port": "battery_ocv"},
                      "source_model": {"R_eq_mohm": 30, "basis": "test"}})
    vt = r["source_coupling"]["V_terminal_V"]
    assert vt[0] < 550 and vt[1] < 650 and vt[0] < vt[1]
    vs = [c["scenario"]["Vdc_V_inverter_dc_terminal"] for c in r["conditions"]]
    assert vs[0] == pytest.approx(vt[0]) and vs[-1] == pytest.approx(vt[1])     # judged over the terminal range


def test_contract():
    with pytest.raises(InputValidationError, match="source model"):
        api.evaluate({"requirement": {**BASE, "Vdc_port": "battery_ocv"}})
    with pytest.raises(InputValidationError, match="basis"):
        source_from_dict({"R_eq_mohm": 20})
    with pytest.raises(InputValidationError, match="R_eq"):
        source_from_dict({"basis": "x"})
    with pytest.raises(InputValidationError):
        source_from_dict({"kind": "electrochemical", "R_eq_mohm": 1, "basis": "x"})
    with pytest.raises(InputValidationError, match="battery-side"):        # the plain parser still refuses it
        api.evaluate({"requirement": {**BASE, "Vdc_port": "battery_terminal"}})
    assert TheveninSource(0.0, "ideal").max_transfer_W(600.0) == math.inf
