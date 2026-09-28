"""Datasheet import (the datasheet tier of roadmap P1-A/B): digitized curves and tabulated values -> project sections.

Expected values are computed here, independently of the importer: the resampled curve must equal a plain linear
interpolation of each temperature's own digitized points, only on the currents every temperature covers; nothing is
extrapolated or invented; the section enters the project with its provenance and the same digest every time.
"""

import copy
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench import datasheet as DS
from traction_workbench import service as S
from traction_workbench.cli import main as cli
from traction_workbench.errors import InputValidationError
from traction_workbench.io import drive_from_dict
from traction_workbench.project import (builtin_project, diff_projects, load_project, module_section_from_spec)

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples" / "datasheets"
PART = {"manufacturer": "Example Semiconductor (fictitious)", "part_number": "EXM-TEST", "revision": "1"}


def _eon(i, T):
    return (1 + 0.004 * (T - 25)) * (0.015 * i + 1.1e-5 * i * i)


def _points(f, ranges):
    return {T: [(float(i), f(float(i), T)) for i in np.linspace(a, b, n)] for T, (a, b, n) in ranges.items()}


def test_long_format_and_webplotdigitizer_export_give_the_same_curve():
    ranges = {25.0: (20.0, 800.0, 9), 150.0: (20.0, 880.0, 11)}
    pts = _points(_eon, ranges)
    long_csv = "curve,T_C,I_A,value\n" + "\n".join(f"e_on,{T:g},{i!r},{v!r}" for T, ps in pts.items() for i, v in ps)
    a, b = pts[25.0], pts[150.0]
    rows = ["Tj=25 C,,150 degC,", "X,Y,X,Y"]
    for k in range(max(len(a), len(b))):
        left = f"{a[k][0]!r},{a[k][1]!r}" if k < len(a) else ","
        right = f"{b[k][0]!r},{b[k][1]!r}" if k < len(b) else ","
        rows.append(f"{left},{right}")
    wpd = "\ufeff" + "\n".join(rows)                  # a BOM, as spreadsheet exports write it
    c1, _ = DS.resample_curve("e_on", DS.parse_points_csv(long_csv)["e_on"], "mJ")
    c2, _ = DS.resample_curve("e_on", DS.parse_points_csv(wpd, "e_on")["e_on"], "mJ")
    assert c1 == c2


def test_resampling_never_extrapolates_and_says_what_it_dropped():
    ranges = {25.0: (20.0, 800.0, 9), 150.0: (50.0, 880.0, 7)}
    pts = _points(_eon, ranges)
    curve, findings = DS.resample_curve("e_on", pts, "mJ")
    grid = np.array(curve["currents_A"])
    assert grid[0] == 50.0 and grid[-1] == 800.0                   # the range every temperature covers
    for T, row in zip(curve["temps_C"], curve["values"]):
        xs = [p[0] for p in pts[T]]
        ys = [p[1] for p in pts[T]]
        for g, v in zip(grid, row):                                 # plain interpolation of that temperature's points
            k = max(j for j in range(len(xs) - 1) if xs[j] <= g)
            w = (g - xs[k]) / (xs[k + 1] - xs[k])
            assert v == pytest.approx(ys[k] + w * (ys[k + 1] - ys[k]), rel=1e-12)
    text = " ".join(f["detail"] for f in findings)
    assert "no extrapolation" in text and "50..800 A" in text
    assert any("starts at 50 A" in f["detail"] for f in findings)   # below it: not given (UNKNOWN), not zero


def test_zero_current_anchor_only_when_declared():
    pts = _points(_eon, {25.0: (20.0, 800.0, 5)})
    plain, _ = DS.resample_curve("e_on", pts, "mJ")
    anchored, f = DS.resample_curve("e_on", pts, "mJ", anchor_zero=True)
    assert plain["currents_A"][0] == 20.0
    assert anchored["currents_A"][0] == 0.0 and all(row[0] == 0.0 for row in anchored["values"])
    assert any("assumption of this import" in x["detail"] for x in f)
    with pytest.raises(InputValidationError, match="only for switching energies"):
        DS.resample_curve("v_on", _points(lambda i, T: 0.7 + 1e-3 * i, {25.0: (10.0, 800.0, 5)}), "V", anchor_zero=True)


def test_import_refuses_what_it_cannot_know():
    pts = _points(_eon, {25.0: (20.0, 800.0, 5)})
    with pytest.raises(InputValidationError, match="energy unit|not a"):
        DS.resample_curve("e_on", pts, "V")
    bad = {25.0: [(10.0, 1.0), (10.0, 2.0), (20.0, 3.0)]}
    with pytest.raises(InputValidationError, match="two different values"):
        DS.resample_curve("e_on", bad, "mJ")
    with pytest.raises(InputValidationError, match="share no current range"):
        DS.resample_curve("e_on", {25.0: [(0, 1), (10, 2)], 150.0: [(20, 1), (30, 2)]}, "mJ")
    spec = json.loads((EX / "module_example.json").read_text(encoding="utf-8"))
    no_pn = copy.deepcopy(spec)
    no_pn["part"]["part_number"] = ""
    with pytest.raises(InputValidationError, match="part number"):
        DS.apply(builtin_project(), no_pn, EX)
    no_curve = copy.deepcopy(spec)
    del no_curve["curves"]["e_off"]
    with pytest.raises(InputValidationError, match="e_off"):
        DS.apply(builtin_project(), no_curve, EX)
    with pytest.raises(InputValidationError, match="CURRENT transition"):      # tr / tf are not voltage edges
        DS.apply(builtin_project(), {"kind": "gate_edges", "part": PART, "tr_ns": 80.0, "tf_ns": 120.0,
                                     "condition": "x"})


def test_module_import_enters_the_project_with_provenance_and_a_reproducible_digest():
    spec = json.loads((EX / "module_example.json").read_text(encoding="utf-8"))
    p = builtin_project()
    q1, res = DS.apply(p, spec, EX)
    q2, _ = DS.apply(p, copy.deepcopy(spec), EX)
    assert q1.sections["module"].digest == q2.sections["module"].digest           # same spec, same section
    assert q1.modified and not p.modified
    pv = q1.sections["module"].provenance
    assert pv["origin"] == "supplier" and pv["qualified"] is False and "EXM-750-820" in pv["source"]
    d = diff_projects(p, q1)
    assert list(d["changed_sections"]) == ["module"]
    assert {"module_losses", "efficiency", "pwm", "lifetime"} <= set(d["affected_analyses"])
    ex = api.example("MODULE", q1)                        # every page reads the imported curves
    assert ex["curves"] == res["data"]["curves"] and ex["v_test_V"] == 600.0
    m = api.module_losses({"module": ex})                 # the request form the power page sends
    assert m["losses"]["established"]
    builtin = api.module_losses({})                       # the built-in example module: other curves, other losses
    assert m["losses"]["semiconductor_W"] != pytest.approx(builtin["losses"]["semiconductor_W"], rel=1e-3)
    assert any(f["item"] == "thermal_path" and "kept from the project" in f["detail"] for f in res["findings"])


def test_capacitor_and_gate_edge_imports():
    p = builtin_project()
    cap = json.loads((EX / "capacitor_example.json").read_text(encoding="utf-8"))
    q, res = DS.apply(p, cap, EX)
    dl = q.data("dc_link")
    assert dl["C_uF"] == 500.0 and dl["ESR_hf_mohm"] == pytest.approx(3.1) and dl["life_hours_table"]
    csv_cap = {**cap, "ESR_table": {"csv": "f_Hz,ESR_mohm\n1e+02,3.2\n1.0e3,2.1\n1e+06,3.1\n"}}
    assert DS.apply(p, csv_cap, EX)[1]["data"]["ESR_table"] == [[100.0, 3.2], [1000.0, 2.1], [1e6, 3.1]]
    gate = json.loads((EX / "gate_edges_example.json").read_text(encoding="utf-8"))
    q, res = DS.apply(p, gate, EX)
    g = q.data("controller")["gate"]
    vdc = p.data("dc_source")["Vdc_nominal_V"]
    assert g["t_rise_ns"] == g["t_fall_ns"] == pytest.approx(vdc / 12.0)      # the faster slope, both node edges
    assert "Vcc = 400 V" in g["basis"] and api.example("EMI", q)["source"]["t_rise_ns"] == pytest.approx(vdc / 12.0)


def test_page_module_back_into_the_project():
    p = builtin_project()
    spec = api.example("MODULE", p)                       # the engine form a page edits
    data, notes = module_section_from_spec(spec, p.data("module"), p.data("controller"))
    assert notes == [] and data == p.data("module")       # unchanged content -> the same section
    edited = {**spec, "fsw_kHz": 12.0, "Rth_K_per_W": 0.1}
    data, notes = module_section_from_spec(edited, p.data("module"), p.data("controller"))
    assert "foster" not in data["thermal_path"] and data["thermal_path"]["Rth_K_per_W"] == 0.1
    assert any("controller setting" in n for n in notes) and any("Foster network" in n for n in notes)


def test_cli_datasheet(tmp_path, capsys):
    out = tmp_path / "p.json"
    assert cli(["datasheet", str(EX / "module_example.json"), "--out", str(out), "--revision", "B"]) == 0
    q = load_project(out)
    assert q.revision == "B" and not q.modified and q.sections["module"].provenance["origin"] == "supplier"
    assert "datasheet import" in q.change_log[-1]["change"]
    capsys.readouterr()
    assert cli(["datasheet", str(EX / "gate_edges_example.json"), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["section"] == "controller" and len(r["digest"]) == 64


# -- representative values typed from the characteristic tables ------------------------------------------------------

def _spec(name):
    return json.loads((EX / name).read_text(encoding="utf-8"))


def _rep():
    return _spec("module_representative_example.json")


def _stated_bound(findings, item):
    detail = next(f["detail"] for f in findings if f["item"] == item and "declared current scaling" in f["detail"])
    return float(re.search(r"within ([0-9.eE+-]+) %", detail).group(1)) / 100.0


def test_representative_curves_are_the_declared_models_through_the_datasheet_values():
    spec = _rep()
    r = spec["representative"]
    res = DS.module_section(spec, builtin_project().data("module"))
    cv = res["data"]["curves"]
    i_nom, i_max = r["I_nom_A"], r["I_max_A"]
    for name, key in (("v_on", "switch"), ("v_rev", "reverse")):
        c = cv[name]
        assert c["temps_C"] == r["temps_C"] and c["currents_A"] == [0.0, i_max] and c["unit"] == "V"
        for row, v0, vn in zip(c["values"], r[key]["V0_V"], r[key]["V_nom_V"]):
            assert row[0] == v0                                                          # the threshold at 0 A
            assert np.interp(i_nom, c["currents_A"], row) == pytest.approx(vn, rel=1e-12)  # the datasheet point
            assert row[1] == pytest.approx(v0 + (vn - v0) / i_nom * i_max, rel=1e-12)
    en = r["energies"]
    for name, key, k in (("e_on", "E_on_mJ", en["k_on"]), ("e_off", "E_off_mJ", en["k_off"]),
                         ("e_rr", "E_rr_mJ", en["k_rr"])):
        c = cv[name]
        grid = np.array(c["currents_A"])
        assert c["unit"] == "mJ" and grid[0] == 0.0 and grid[-1] == i_max
        for row, e_ref in zip(c["values"], en[key]):
            assert np.interp(en["I_A"], grid, row) == pytest.approx(e_ref, rel=1e-12)   # the datasheet value is kept
            np.testing.assert_allclose(row, e_ref * (grid / en["I_A"]) ** k, rtol=1e-12, atol=0)
    assert cv["e_off"]["currents_A"] == [0.0, i_max]                  # k = 1 is a line: two nodes are exact
    for name, k in (("e_on", en["k_on"]), ("e_rr", en["k_rr"])):      # a power law is sampled: the stated bound holds
        c = cv[name]
        grid = np.array(c["currents_A"])
        x = np.geomspace(grid[1], i_max, 200001)
        law = c["values"][1][-1] * (x / i_max) ** k
        dev = float(np.max(np.abs(np.interp(x, grid, c["values"][1]) - law) / law))
        assert 0 < dev <= _stated_bound(res["findings"], name)
    text = " ".join(f["detail"] for f in res["findings"])
    assert "declared conduction model" in text and "not the datasheet output characteristic" in text
    assert "above it the losses are not established" in text
    assert "characteristic values typed from the datasheet" in res["provenance"]["evidence"]


def test_representative_module_losses_are_established_only_inside_the_declared_current_range():
    p = builtin_project()
    q, _ = DS.apply(p, _rep(), EX)
    inside = api.module_losses({"module": api.example("MODULE", q)})
    assert inside["losses"]["established"] and inside["operating_point"]["i_peak_A"] < 1640.0
    small = _rep()
    small["representative"].update(I_nom_A=200.0, I_max_A=250.0)
    small["representative"]["energies"]["I_A"] = 200.0
    q2, _ = DS.apply(p, small, EX)
    out = api.module_losses({"module": api.example("MODULE", q2)})
    assert out["operating_point"]["i_peak_A"] > 250.0
    assert not out["losses"]["established"] and out["claim"]["status"] == "UNKNOWN"
    assert all("no extrapolation" in x for x in out["losses"]["problems"])
    assert out["policy_with_module"]["physical_existence_with_dc"] == "UNKNOWN"   # no loss beyond the data


def test_the_three_conduction_forms():
    r = copy.deepcopy(_rep()["representative"])
    r["switch"] = {"model": "resistance", "R_mohm": [1.6, 2.4]}
    cv, _ = DS.representative_curves(r, "IGBT", "doc")
    np.testing.assert_allclose(cv["v_on"]["values"], [[0.0, 1.6e-3 * 1640.0], [0.0, 2.4e-3 * 1640.0]], rtol=1e-12)
    r["switch"] = {"model": "two_points", "I_A": [100.0, 400.0], "V_V": [[0.90, 0.85], [1.50, 1.60]]}
    cv, _ = DS.representative_curves(r, "IGBT", "doc")
    for row, (a, b) in zip(cv["v_on"]["values"], ((0.90, 1.50), (0.85, 1.60))):
        assert np.interp(100.0, cv["v_on"]["currents_A"], row) == pytest.approx(a, rel=1e-12)
        assert np.interp(400.0, cv["v_on"]["currents_A"], row) == pytest.approx(b, rel=1e-12)
    r["switch"] = {"model": "threshold_slope", "V0_V": [0.8, 0.7], "r_mohm": [1.0, 1.5]}
    cv, _ = DS.representative_curves(r, "IGBT", "doc")
    np.testing.assert_allclose(cv["v_on"]["values"], [[0.8, 0.8 + 1.0e-3 * 1640.0], [0.7, 0.7 + 1.5e-3 * 1640.0]],
                               rtol=1e-12)


@pytest.mark.parametrize("change, match", [
    ({"switch": {"V_nom_V": [1.44, 1.62]}}, "one on-state voltage fixes one point"),
    ({"switch": {"model": "threshold_slope", "V_nom_V": [1.44, 1.62]}}, "threshold V0"),
    ({"switch": {"model": "threshold_slope", "V0_V": [0.62], "V_nom_V": [1.44]}}, "one value per junction temperature"),
    ({"switch": {"model": "threshold_slope", "V0_V": [1.5, 1.7], "V_nom_V": [1.44, 1.62]}}, "must exceed the threshold"),
    ({"switch": {"model": "two_points", "I_A": [400.0, 100.0], "V_V": [[1.5, 1.6], [0.9, 0.85]]}}, "increase"),
    ({"switch": {"model": "two_points", "I_A": [100.0, 400.0], "V_V": [[0.3, 0.3], [1.5, 1.5]]}}, "reaches 0 V"),
    ({"switch": {"model": "ohmic"}}, "conduction model must be one of"),
    ({"reverse": None}, "representative.reverse"),
    ({"I_max_A": 700.0}, "must be >= I_nom_A"),
    ({"temps_C": [150.0, 25.0]}, "strictly increasing"),
])
def test_values_that_do_not_fix_a_conduction_model_are_refused(change, match):
    spec = _rep()
    spec["representative"].update(change)
    with pytest.raises(InputValidationError, match=match):
        DS.apply(builtin_project(), spec, EX)


@pytest.mark.parametrize("change, match", [
    ({"E_on_mJ": None}, "E_on_mJ is required"),
    ({"E_off_mJ": [25.3]}, "one value per junction temperature"),
    ({"E_on_mJ": [0.0, 29.0]}, "must be > 0"),
    ({"I_A": 2000.0}, "above the curve range"),
    ({"k_on": 3.5}, "outside 0 < k <= 3"),
])
def test_switching_energy_values_are_checked(change, match):
    spec = _rep()
    spec["representative"]["energies"].update(change)
    with pytest.raises(InputValidationError, match=match):
        DS.apply(builtin_project(), spec, EX)


def test_energy_exponents_are_declarations():
    spec = _rep()
    en = spec["representative"]["energies"]
    del en["k_off"], en["basis"]
    res = DS.module_section(spec, builtin_project().data("module"))
    assert any(f["item"] == "e_off" and "k = 1" in f["detail"] for f in res["findings"])        # the default, said
    assert any(f["level"] == "WARNING" and f["item"] == "e_on" and "without a basis" in f["detail"]
               for f in res["findings"])
    both = _rep()
    both["curves"] = _spec("module_example.json")["curves"]
    with pytest.raises(InputValidationError, match="not both"):
        DS.apply(builtin_project(), both, EX)


def test_sic_representative_values_and_a_typed_thermal_path():
    spec = _rep()
    spec["technology"] = "SiC_MOSFET"
    r = spec["representative"]
    r["switch"] = {"model": "resistance", "R_mohm": [2.0, 3.2]}
    del r["energies"]["E_rr_mJ"]
    base = builtin_project().data("module")
    with pytest.raises(InputValidationError, match="E_rr of the recovering diode"):    # per-device energies
        DS.module_section(spec, base)
    r["energies"]["E_rr_mJ"] = [0.0, 0.0]                                             # negligible, said explicitly
    res = DS.module_section(spec, base)
    assert res["data"]["curves"]["e_rr"]["values"] == [[0.0, 0.0], [0.0, 0.0]]
    assert any(f["item"] == "e_rr" and "declared 0" in f["detail"] for f in res["findings"])
    assert "v_channel_rev" not in res["data"]["curves"]
    assert any(f["item"] == "v_channel_rev" and "declaration" in f["detail"] for f in res["findings"])
    r["channel_reverse"] = {"model": "resistance", "R_mohm": [2.1, 3.4]}
    spec["thermal_path"] = {"Rth_K_per_W": 0.11, "T_ref_C": 65.0, "basis": "R_th(j-f) at 10 l/min, datasheet table"}
    res = DS.module_section(spec, base)
    assert res["data"]["curves"]["v_channel_rev"]["values"][1][1] == pytest.approx(3.4e-3 * r["I_max_A"], rel=1e-12)
    assert res["data"]["thermal_path"] == {"Rth_K_per_W": 0.11, "T_ref_C": 65.0,
                                           "basis": "R_th(j-f) at 10 l/min, datasheet table"}
    spec["thermal_path"]["basis"] = ""
    with pytest.raises(InputValidationError, match="basis is required"):
        DS.module_section(spec, base)
    spec["thermal_path"]["basis"] = "x"
    spec["zth_foster"] = {"R_K_per_W": [0.1], "tau_s": [1.0], "reference": "junction-to-fluid"}
    with pytest.raises(InputValidationError, match="not both"):
        DS.module_section(spec, base)


def test_one_capacitor_esr_value_holds_only_over_its_declared_band():
    p = builtin_project()
    spec = _spec("capacitor_representative_example.json")
    q, res = DS.apply(p, spec, EX)
    dl, er = q.data("dc_link"), spec["ESR_representative"]
    assert dl["ESR_table"] == [[er["band_Hz"][0], er["ESR_mohm"]], [er["band_Hz"][1], er["ESR_mohm"]]]
    assert dl["ESR_hf_mohm"] == er["ESR_mohm"] and dl["ESR_table_T_C"] == 25.0
    assert any("declared constant over" in f["detail"] for f in res["findings"])
    assert any(f["item"] == "ESR_hf_mohm" and "not a datasheet value at EMI frequencies" in f["detail"]
               for f in res["findings"])
    full = api.dclink_ripple({"ripple": api.example("RIPPLE", q)})      # the band covers every carrying harmonic
    assert full["esr_coverage"]["complete"] and full["P_cap_W"] is not None
    narrow = copy.deepcopy(spec)
    narrow["ESR_representative"]["band_Hz"] = [1e3, 1e5]
    q2, _ = DS.apply(p, narrow, EX)
    part = api.dclink_ripple({"ripple": api.example("RIPPLE", q2)})
    assert part["P_cap_W"] is None and part["current_share_outside_ESR_band"] > 0.01    # outside: not a guess
    assert part["claims"]["capacitor_loss"]["status"] == "UNKNOWN"
    for band, match in (([2e4, 1e6], "inside it"), ([1e6, 1e3], "low < high"), ([1e3], "band_Hz")):
        bad = copy.deepcopy(spec)
        bad["ESR_representative"]["band_Hz"] = band
        with pytest.raises(InputValidationError, match=match):
            DS.apply(p, bad, EX)
    both = {**spec, "ESR_table": [[100.0, 3.0], [1e6, 3.0]]}
    with pytest.raises(InputValidationError, match="not both"):
        DS.apply(p, both, EX)
    no_basis = copy.deepcopy(spec)
    del no_basis["ESR_representative"]["basis"]
    assert any(f["level"] == "WARNING" and "no basis" in f["detail"] for f in DS.apply(p, no_basis, EX)[1]["findings"])


def _case_at_project_limits(p):
    case = json.loads((ROOT / "examples" / "cases" / "req_ts_012_600V.json").read_text(encoding="utf-8"))
    lim = p.limits_dict()
    case["scenario"] = {"source_limits": {n: {"value": lim[f"{n}_{u}"], "unit": u} for n, u in (
        ("discharge_power_max", "W"), ("charge_power_max", "W"), ("discharge_current_max", "A"),
        ("charge_current_max", "A"))}}
    return case


def _numbers(d, pre=""):
    if isinstance(d, dict):
        return {k2: v2 for k, v in d.items() for k2, v2 in _numbers(v, f"{pre}.{k}").items()}
    if isinstance(d, list):
        return {k2: v2 for i, v in enumerate(d) for k2, v2 in _numbers(v, f"{pre}[{i}]").items()}
    return {pre: d} if isinstance(d, (int, float)) and not isinstance(d, bool) else {}


def test_motor_datasheet_values_reproduce_the_reference_machine():
    """The synthetic reference machine stated the way a motor datasheet states it (8 poles, Ke line-to-line RMS per
    1000 rpm, R line-to-line, L in mH, a no-load loss at one speed) gives the reference decision, number for number."""
    p = builtin_project()
    spec = _spec("motor_example.json")
    we = 4 * 2 * math.pi * 1000.0 / 60.0
    spec["motor"]["Ke"]["value"] = 0.1 * we * math.sqrt(1.5)          # psi_PM = 0.1 Wb as V_LL,rms per 1000 rpm
    w0 = 2 * math.pi * 6000.0 / 60.0
    spec["motor"]["rotational_loss"]["no_load_loss"]["P_W"] = 0.002 * w0 * w0      # b = 0.002 as a no-load loss
    q, res = DS.apply(p, spec, EX)
    assert res["section"] == "drive" and q.sections["drive"].provenance["origin"] == "supplier"
    d, ref = drive_from_dict(q.data("drive")), drive_from_dict(p.data("drive"))
    assert d.motor.pole_pairs == ref.motor.pole_pairs == 4
    for got, want in ((d.motor.flux.psi_pm_Wb, 0.1), (d.motor.Rs_ohm, 0.015), (d.motor.flux.Ld_H, 2e-4),
                      (d.motor.flux.Lq_H, 4e-4), (d.motor.rotational_loss.viscous_Nm_per_rad_s, 0.002)):
        assert got == pytest.approx(want, rel=1e-12)
    assert d.inverter == ref.inverter and d.domain == ref.domain          # design data kept from the project
    rules = {c["rule"] for c in res["conversions"]}
    assert {"wye: line-to-line resistance -> per-phase (/ 2)", "line-to-line RMS -> phase peak (x sqrt(2/3))",
            "back-EMF constant -> psi_PM = V_phase_peak / omega_e"} <= rules
    assert any(f["item"] == "rotational_loss" and "a declaration" in f["detail"] for f in res["findings"])
    case = _case_at_project_limits(p)
    a = S.evaluate_case(case)
    b = S.evaluate_case({**case, "drive": q.data("drive")})
    assert a["verdict"]["status"] == b["verdict"]["status"] == "FEASIBLE"
    na, nb = _numbers(a["conditions"]), _numbers(b["conditions"])
    assert na.keys() == nb.keys() and len(na) > 50
    for k, v in na.items():
        assert nb[k] == pytest.approx(v, rel=1e-9, abs=1e-12), k
    assert b["model"]["provenance"]["origin"] == "supplier"


def test_a_motor_without_rotational_loss_warns_and_decides_nothing():
    p = builtin_project()
    spec = _spec("motor_example.json")
    del spec["motor"]["rotational_loss"]
    q, res = DS.apply(p, spec, EX)
    assert any(f["level"] == "WARNING" and f["item"] == "rotational_loss" for f in res["findings"])
    r = S.evaluate_case({**_case_at_project_limits(p), "drive": q.data("drive")})
    assert r["verdict"]["status"] == "UNKNOWN" and "MISSING_INPUT" in r["verdict"]["reasons"]


@pytest.mark.parametrize("change, match", [
    ({"poles": 7}, "even integer"),
    ({"poles": 8.0}, "even integer"),
    ({"pole_pairs": 4}, "not both"),
    ({"model": "flux_map"}, "constant_dq"),
    ({"Ke": {"value": 51.3, "unit": "V", "basis": "line_line_rms"}}, "per_speed"),
    ({"Rs": {"value": 30, "unit": "mohm"}}, "resistance definition missing"),
    ({"Ld": {"value": 0.2}}, "explicit unit"),
    ({"rotational_loss": {"no_load_loss": {"P_W": 789.57}}}, "speed_rpm"),
])
def test_motor_values_with_an_open_definition_are_refused(change, match):
    spec = _spec("motor_example.json")
    spec["motor"].update(change)
    with pytest.raises(InputValidationError, match=match):
        DS.apply(builtin_project(), spec, EX)


def test_motor_needs_the_design_data_from_somewhere():
    with pytest.raises(InputValidationError, match="inverter and domain"):
        DS.motor_section(_spec("motor_example.json"), None)
    no_rev = _spec("motor_example.json")
    no_rev["part"]["revision"] = ""
    with pytest.raises(InputValidationError, match="revision"):
        DS.apply(builtin_project(), no_rev, EX)


def test_cli_motor_datasheet(tmp_path, capsys):
    assert cli(["datasheet", str(EX / "motor_example.json"), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["section"] == "drive" and r["provenance"]["origin"] == "supplier" and len(r["digest"]) == 64
    out = tmp_path / "p.json"
    assert cli(["datasheet", str(EX / "module_representative_example.json"), "--out", str(out), "--revision", "C"]) == 0
    assert load_project(out).sections["module"].provenance["origin"] == "supplier"
