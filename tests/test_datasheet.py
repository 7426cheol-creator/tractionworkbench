"""Datasheet import (the datasheet tier of roadmap P1-A/B): digitized curves and tabulated values -> project sections.

Expected values are computed here, independently of the importer: the resampled curve must equal a plain linear
interpolation of each temperature's own digitized points, only on the currents every temperature covers; nothing is
extrapolated or invented; the section enters the project with its provenance and the same digest every time.
"""

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench import datasheet as DS
from traction_workbench.cli import main as cli
from traction_workbench.errors import InputValidationError
from traction_workbench.project import (builtin_project, diff_projects, load_project, module_section_from_spec)

EX = Path(__file__).resolve().parents[1] / "examples" / "datasheets"
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
    m = api.module_losses(ex)
    assert m["losses"]["established"]
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
