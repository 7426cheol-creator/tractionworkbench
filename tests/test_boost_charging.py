"""Integrated charging (system view, item 9): exact ripple against the boost formulas, interleaving cancellation,
device losses by hand on linear curves, energy balance, the declared limits and the capability limiter."""

import math
from dataclasses import replace

import pytest

from traction_workbench import api
from traction_workbench.extensions import boost_charging as bc
from traction_workbench.parsers import module_model_from_dict
from traction_workbench.project import builtin_project

FSW = 10e3


def _flat(v0, r, unit="V"):
    # temperature-independent linear curve (same values at 25 and 175 degC) up to 800 A
    return {"unit": unit, "temps_C": [25.0, 175.0], "currents_A": [0.0, 800.0],
            "values": [[v0, v0 + r * 800.0], [v0, v0 + r * 800.0]]}


def _module(technology="IGBT", scaling=True, deadtime_us=0.0):
    m = {"name": "test module", "technology": technology, "value_kind": "typical", "energy_basis": "per_device",
         "v_test_V": 600.0, "fsw_kHz": FSW / 1e3, "deadtime_us": deadtime_us,
         "curves": {"v_on": _flat(0.8, 1.5e-3), "v_rev": _flat(0.9, 1.2e-3), "e_on": _flat(0.4, 0.04, "mJ"),
                    "e_off": _flat(0.5, 0.05, "mJ"), "e_rr": _flat(0.2, 0.02, "mJ")}}
    if technology == "SiC_MOSFET":
        m["curves"]["v_channel_rev"] = _flat(0.0, 1.6e-3)
    if scaling:
        m["vdc_scaling"] = {"exponent": 1.0, "valid_V": [300.0, 800.0], "basis": "test"}
    return module_model_from_dict(m)


def _path(**kw):
    d = {"neutral_access": True, "L0_uH": 30.0, "L0_basis": "test", "interleave": "120deg"}
    d.update(kw)
    return bc.path_from_dict(d)


DRIVE = api._drive({})


def _point(path=None, model=None, **kw):
    a = dict(V_c=400.0, V_b=600.0, I_charge=150.0, coolant_C=40.0, Rth_K_per_W=0.1)
    a.update(kw)
    return bc.charging_point(DRIVE, model or _module(), path or _path(), **a)


def test_in_phase_ripple_is_the_boost_formula():
    r = _point(path=_path(interleave="none"))
    d = r["duty_upper"]
    R = r["inputs"]["R_phase_ohm"]
    assert d == pytest.approx((400.0 - R * 50.0) / 600.0, rel=1e-12)
    pp = 600.0 * d * (1 - d) / (30e-6 * FSW)
    c = r["currents"]
    assert c["zero_sequence_ripple_pp_A"] == pytest.approx(pp, rel=1e-9)
    assert all(x == pytest.approx(pp, rel=1e-9) for x in c["phase_ripple_pp_A"])
    assert c["neutral_ripple_pp_A"] == pytest.approx(3 * pp, rel=1e-9)
    assert c["neutral_mean_A"] == pytest.approx(150.0, rel=1e-9)
    assert c["phase_dc_A"] == pytest.approx(50.0)
    # equal phase currents are pure zero sequence: no torque
    assert r["torque"]["peak_abs_Nm"] == pytest.approx(0.0, abs=1e-9)


def test_interleaving_cancels_the_neutral_ripple_at_one_third_duty():
    R = DRIVE.motor.Rs_ohm
    V_c = 600.0 / 3.0 + R * 50.0                     # upper duty exactly 1/3
    r = _point(V_c=V_c, I_charge=150.0)
    assert r["duty_upper"] == pytest.approx(1.0 / 3.0, rel=1e-12)
    c = r["currents"]
    assert c["neutral_ripple_pp_A"] < 1e-6 and c["zero_sequence_ripple_pp_A"] < 1e-6
    assert min(c["phase_ripple_pp_A"]) > 1.0          # the phases still ripple through L_d / L_q
    assert c["neutral_mean_A"] == pytest.approx(150.0, rel=1e-9)


def test_device_losses_by_hand_on_linear_curves():
    # in phase: each phase current is a triangle around I_k with pk-pk dI; linear curves -> closed form (L0 large
    # enough that the current never reverses: with a reversal the reverse paths conduct and the closed form changes)
    r = _point(path=_path(interleave="none", L0_uH=300.0), model=_module(scaling=False), V_b=600.0)
    d = r["duty_upper"]
    D = 1 - d
    Ik = 50.0
    dI = r["currents"]["zero_sequence_ripple_pp_A"]
    ms = Ik * Ik + dI * dI / 12.0                     # mean square of the triangle over either segment
    assert Ik - dI / 2 > 0
    leg = r["legs"][0]
    assert leg["conduction_W"]["lower_switch"] == pytest.approx(D * (0.8 * Ik + 1.5e-3 * ms), rel=1e-5)
    assert leg["conduction_W"]["upper_reverse"] == pytest.approx(d * (0.9 * Ik + 1.2e-3 * ms), rel=1e-5)
    i_lo, i_hi = Ik - dI / 2, Ik + dI / 2
    E = lambda e0, e1, i: (e0 + e1 * i) * 1e-3       # noqa: E731
    assert leg["switching_W"]["lower_switch"] == pytest.approx(FSW * (E(0.4, 0.04, i_lo) + E(0.5, 0.05, i_hi)),
                                                               rel=1e-6)
    assert leg["switching_W"]["upper_recovery"] == pytest.approx(FSW * E(0.2, 0.02, i_lo), rel=1e-6)
    # energy balance: charger power = battery power + every loss
    L = r["losses_W"]
    assert r["P_charger_W"] - r["P_battery_W"] == pytest.approx(L["devices"] + L["motor_copper"], rel=1e-12)
    assert 0.9 < r["efficiency"] < 1.0


def test_switching_away_from_the_test_voltage_needs_a_scaling_law():
    r = _point(model=_module(scaling=False), V_b=650.0)
    assert r["status"] == "UNKNOWN" and not r["established"]
    assert any("scaling law" in p for p in r["problems"])
    assert r["Tj_C"] is None and r["losses_W"]["devices"] is None


def test_not_applicable_and_unknown_inputs():
    assert _point(path=_path(neutral_access=False))["status"] == "NOT_APPLICABLE"
    assert _point(V_c=650.0)["status"] == "NOT_APPLICABLE"            # no boost needed
    r = _point(path=_path(L0_uH=None))
    assert r["status"] == "UNKNOWN" and "L0" in r["reason"]


def test_limits_are_judged_only_where_declared():
    r = _point(limits={"charger_current_max_A": 100.0})
    st = {c["id"]: c["status"] for c in r["checks"]}
    assert st["charger_current"] == "FAIL" and r["status"] == "FAIL"
    assert "neutral_rms" in r["not_checked"] and st["neutral_rms"] == "UNKNOWN"
    ok = _point(limits={"charger_current_max_A": 500.0})
    assert ok["status"] == "PASS" and "junction" in ok["not_checked"]


def test_capability_names_the_binding_limit():
    lim = {"charger_current_max_A": 600.0, "battery_charge_power_max_W": 100e3}
    cap = bc.capability(DRIVE, _module(), _path(), V_c=400.0, V_b=600.0, coolant_C=40.0, Rth_K_per_W=0.1,
                        limits=lim)
    assert cap["limiting"] == ["battery_power"]
    r = _point(I_charge=cap["I_max_A"], limits=lim)
    assert r["P_battery_W"] == pytest.approx(100e3, rel=0.01)
    cap2 = bc.capability(DRIVE, _module(), _path(neutral_current_max_A=200.0), V_c=400.0, V_b=600.0, coolant_C=40.0,
                         Rth_K_per_W=0.1, limits=lim)
    assert cap2["limiting"] == ["neutral_rms"] and cap2["I_max_A"] == pytest.approx(200.0, abs=1.0)


def test_discharge_moves_the_heat_to_the_upper_switches():
    r = _point(I_charge=-120.0)
    assert r["P_battery_W"] < r["P_charger_W"] < 0           # the battery supplies the load and the losses
    assert "upper_igbt" in r["hottest_die"]
    assert 0.9 < r["efficiency"] < 1.0


def test_sic_dead_time_body_diode_share():
    m0 = _module("SiC_MOSFET", deadtime_us=0.0)
    m1 = _module("SiC_MOSFET", deadtime_us=0.5)
    a = _point(model=m0, path=_path(interleave="none"))["legs"][0]["conduction_W"]["upper_reverse"]
    b = _point(model=m1, path=_path(interleave="none"))["legs"][0]["conduction_W"]["upper_reverse"]
    assert b > a                     # the body diode (higher drop) carries the current in the dead times


def test_project_charging_section_and_api():
    prj = builtin_project()
    assert prj.has("charging") and "charging" in prj.usage("charging")["sections"]
    ex = api.example("CHARGING", prj)
    assert ex["charging"]["L0_uH"] == prj.data("charging")["L0_uH"]
    r = api.charging_point({})
    assert r["status"] == "PASS" and r["efficiency"] > 0.98
    m = api.charging_capability({"V_chargers_V": [300.0, 500.0], "V_batteries_V": [600.0]})
    lims = {row["V_charger_V"]: row["limiting"] for row in m["rows"]}
    assert lims[300.0] == ["neutral_rms"]               # low charger voltage: the neutral current binds
    assert "battery_power" in lims[500.0]               # high charger voltage: the pack's acceptance binds


def test_a_reversing_ripple_current_uses_the_reverse_paths():
    # 30 uH in phase: the ripple (~444 A pk-pk) is larger than twice the 50 A mean - the current reverses in each period
    r = _point(path=_path(interleave="none"))
    leg = r["legs"][0]
    assert r["currents"]["phase_dc_A"] < r["currents"]["zero_sequence_ripple_pp_A"] / 2
    assert leg["conduction_W"]["lower_reverse"] > 0 and leg["conduction_W"]["upper_switch"] > 0
