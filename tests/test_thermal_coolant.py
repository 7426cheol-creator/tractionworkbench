"""Coolant loop, Cauer networks and flow-dependent stages of the thermal screening."""

import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from conftest import scenario
from traction_workbench import api
from traction_workbench import spec_fixtures as sf
from traction_workbench.errors import InputValidationError
from traction_workbench.extensions.coolant import CoolantLoop, CoolantStation, eg_water_properties
from traction_workbench.extensions.thermal import (CauerNetwork, FosterNetwork, ThermalModel, ThermalNode, flow_scaled,
                                                   thermal_duration)
from traction_workbench.models import DataOrigin, Provenance


def test_eg_water_defaults_are_typical_and_bounded():
    p = eg_water_properties(50, 65)
    assert 3400 < p["cp_J_per_kgK"] < 3600 and 1030 < p["rho_kg_per_m3"] < 1060 and not p["clamped_to_table"]
    w = eg_water_properties(0, 20)
    assert w["cp_J_per_kgK"] == pytest.approx(4180, abs=5) and w["rho_kg_per_m3"] == pytest.approx(998, abs=1)
    assert eg_water_properties(50, 20)["cp_J_per_kgK"] < p["cp_J_per_kgK"]          # c_p rises with temperature
    assert eg_water_properties(60, 65)["cp_J_per_kgK"] < p["cp_J_per_kgK"]          # and falls with glycol content
    assert eg_water_properties(50, 130)["clamped_to_table"]
    with pytest.raises(InputValidationError):
        eg_water_properties(120, 65)


def test_coolant_energy_balance_along_the_loop():
    loop = CoolantLoop(12.0, 3500.0, 1050.0, (CoolantStation("inverter", (("inverter", 1.0),)),
                                              CoolantStation("motor", (("copper", 1.0), ("rotational", 0.5)))), "mean")
    cdot = 1050.0 * 12.0 / 60000.0 * 3500.0
    assert loop.capacity_rate_W_per_K == pytest.approx(cdot)
    f = loop.fluid_temperatures(65.0, {"inverter": 3000.0, "copper": 6000.0, "rotational": 2000.0})
    assert f["inverter"]["T_out_C"] == pytest.approx(65.0 + 3000.0 / cdot)
    assert f["motor"]["T_in_C"] == pytest.approx(f["inverter"]["T_out_C"])
    assert f["motor"]["P_W"] == pytest.approx(7000.0)
    assert f["_loop"]["T_outlet_C"] == pytest.approx(65.0 + 10000.0 / cdot)
    assert f["motor"]["T_ref_C"] == pytest.approx(0.5 * (f["motor"]["T_in_C"] + f["motor"]["T_out_C"]))
    with pytest.raises(InputValidationError):
        CoolantLoop(0.0, 3500.0, 1050.0, ())
    with pytest.raises(InputValidationError):
        CoolantLoop(10.0, 3500.0, 1050.0, (), reference="average")


def test_cauer_to_foster_is_exact():
    cn = CauerNetwork((0.003, 0.005, 0.006), (1500.0, 6000.0, 30000.0))
    fn = cn.to_foster()
    assert sum(fn.R_K_per_W) == pytest.approx(0.014, rel=1e-12)
    # step response of the ladder (junction node) by direct integration vs the Foster sum
    R, C = np.array(cn.R_K_per_W), np.array(cn.C_J_per_K)
    n = len(R)

    def rhs(_t, T):
        q = np.empty(n)
        for i in range(n):
            q[i] = (T[i] - (T[i + 1] if i + 1 < n else 0.0)) / R[i]
        d = np.zeros(n)
        d[0] += 1.0                                   # 1 W into the junction
        d -= q
        d[1:] += q[:-1]
        return d / C

    ts = [1.0, 10.0, 60.0, 600.0]
    sol = solve_ivp(rhs, (0, 600.0), np.zeros(n), t_eval=ts, rtol=1e-10, atol=1e-14, method="LSODA")
    assert np.allclose(sol.y[0], fn.zth_array(ts), rtol=1e-6, atol=1e-12)
    single = CauerNetwork((0.5,), (20.0,)).to_foster()
    assert single.R_K_per_W[0] == pytest.approx(0.5) and single.tau_s[0] == pytest.approx(10.0)
    with pytest.raises(InputValidationError):
        CauerNetwork((0.1, 0.2), (10.0,))


def test_flow_dependent_stages_scale_only_the_flagged_resistance():
    r = flow_scaled((0.01, 0.03, 0.08, 0.08), (False, False, False, True), 10.0, 5.0, 0.8)
    assert r[:3] == (0.01, 0.03, 0.08) and r[3] == pytest.approx(0.08 * 2 ** 0.8)
    assert flow_scaled((0.1,), (True,), 10.0, 10.0) == (0.1,)
    assert flow_scaled((0.1,), (), 10.0, 5.0) == (0.1,)
    with pytest.raises(InputValidationError):
        flow_scaled((0.1,), (True,), 10.0, -1.0)


def _model(coolant):
    prov = Provenance(DataOrigin.SYNTHETIC, "test", "1", "unvalidated")
    return ThermalModel("TM", "1", (ThermalNode("junction", FosterNetwork((0.05, 0.15), (0.05, 2.0)), 150.0,
                                                (("inverter", 1 / 6),), "inverter"),), prov, coolant=coolant)


def test_node_reference_is_the_local_fluid_temperature():
    drive = sf.synthetic_drive()
    sc = scenario(3000, 600, coolant_temp_C=65.0)
    no_loop = thermal_duration(drive, sc, _model(None), 450.0, 10.0)
    loop = CoolantLoop(10.0, 3507.5, 1045.0, (CoolantStation("inverter", (("inverter", 1.0),)),), "outlet")
    with_loop = thermal_duration(drive, sc, _model(loop), 450.0, 10.0)
    p_inv = with_loop["operating_point"]["losses_W"]["inverter"]
    rise = p_inv / loop.capacity_rate_W_per_K
    a, b = no_loop["nodes"][0], with_loop["nodes"][0]
    assert a["fluid_reference_C"] == 65.0 and b["fluid_reference_C"] == pytest.approx(65.0 + rise)
    assert b["steady_state_C"] - a["steady_state_C"] == pytest.approx(rise)
    assert b["time_to_limit_s"] < a["time_to_limit_s"]
    assert "infinite flow" in no_loop["coolant"]["note"] and with_loop["coolant"]["declared"]


def test_unknown_station_is_rejected():
    loop = CoolantLoop(10.0, 3500.0, 1050.0, (CoolantStation("motor", (("copper", 1.0),)),))
    with pytest.raises(InputValidationError):
        _model(loop)


def test_spec_parser_accepts_old_and_new_formats():
    old = {"model_id": "OLD", "nodes": [{"id": "j", "R_K_per_W": [0.05, 0.15], "tau_s": [0.05, 2.0], "limit_C": 150,
                                         "loss_share": {"inverter": 1 / 6}}]}
    m = api._thermal_model(old, 65.0)
    assert m.coolant is None and m.nodes[0].network.tau_s == (0.05, 2.0)
    new = api._thermal_model(api.EXAMPLE_THERMAL, 65.0)
    assert new.coolant.flow_L_per_min == 10.0 and new.coolant.glycol_vol_pct == 50.0
    assert new.nodes[1].station == "motor" and sum(new.nodes[1].network.R_K_per_W) == pytest.approx(0.014)
    spec = dict(api.EXAMPLE_THERMAL, coolant=dict(api.EXAMPLE_THERMAL["coolant"], flow_L_per_min=5.0))
    half = api._thermal_model(spec, 65.0)
    assert half.nodes[0].network.R_K_per_W[3] == pytest.approx(0.08 * 2 ** 0.8)
    user = dict(api.EXAMPLE_THERMAL, coolant=dict(api.EXAMPLE_THERMAL["coolant"], cp_J_per_kgK=3600.0, rho_kg_per_m3=1060.0))
    assert api._thermal_model(user, 65.0).coolant.cp_J_per_kgK == 3600.0
    d = api.thermal_details(api.EXAMPLE_THERMAL, 65.0)
    assert d["coolant"]["capacity_rate_W_per_K"] == pytest.approx(1045.0 * 10 / 60000 * 3507.5, rel=1e-9)
    assert math.isclose(d["nodes"][0]["Rth_total_K_per_W"], 0.2)
