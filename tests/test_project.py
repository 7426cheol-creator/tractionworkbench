"""Project data package (system review R2): one set of product data for every analysis, with identity.

Checks the contract, not numbers: the built-in project is the single source of the page examples, sections are
validated by the parsers of their analyses, digests name the data, a revision change names exactly the sections and
the analyses it touches, results can be marked stale, and a request states per component whether it used the
project's data or a local edit.
"""

import copy
import json
import math

import pytest

from traction_workbench import api
from traction_workbench.cli import main as cli
from traction_workbench.errors import InputValidationError
from traction_workbench.examples import SYNTHETIC_PROJECT
from traction_workbench.project import (ANALYSES, SECTIONS, TASK_ANALYSIS, Project, builtin_project, check_project,
                                        diff_projects, digest, load_project, request_usage, save_project,
                                        stale_sections)


def edited(**changes):
    """The built-in project file with dotted-path changes, e.g. edited(**{"controller.deadtime_us": 1.2})."""
    d = copy.deepcopy(SYNTHETIC_PROJECT)
    for path, value in changes.items():
        keys = path.split(".")
        node = d["sections"][keys[0]]["data"]
        for k in keys[1:-1]:
            node = node[int(k)] if isinstance(node, list) else node[k]
        last = keys[-1]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    return d


def test_builtin_project_is_valid_and_identified():
    p = builtin_project()
    assert p.id == "SYNTH-TRACTION-200KW" and p.origin == "synthetic" and not p.modified
    assert all(n in p.sections for n, s in SECTIONS.items() if s.required)
    ident = p.identity()
    assert all(ident["sections"][n] == digest(s.data) for n, s in p.sections.items())
    assert Project.from_dict(p.to_dict()).digest() == p.digest()            # canonical content, stable identity
    assert check_project(p)["status"] == "OK"


def test_page_examples_take_their_product_data_from_the_project():
    p = api.PROJECT
    for name in api.EXAMPLE_NAMES:                     # one derivation: the examples ARE the project composition
        assert json.dumps(api.example(name, p), sort_keys=True, default=str) == \
            json.dumps(getattr(api, f"EXAMPLE_{name}"), sort_keys=True, default=str), name
    c = p.data("controller")
    dead = {api.EXAMPLE_MODULE["deadtime_us"], api.EXAMPLE_EMI["source"]["t_dead_us"],
            api.EXAMPLE_PWM["transition"]["deadtime_us"], c["deadtime_us"]}
    assert dead == {1.5}                                # one dead time for losses, EMI and PWM transitions
    assert api.EXAMPLE_EMI["network"]["C_dc_uF"] == api.EXAMPLE_RIPPLE["capacitor"]["C_uF"] == \
        api.EXAMPLE_PROTECTION["plant"]["C_uF"] == p.data("dc_link")["C_uF"]
    assert api.EXAMPLE_PWM["timing"]["min_pulse_us"] == api.EXAMPLE_EMI["source"]["min_pulse_us"]
    fo = api.EXAMPLE_MISSION["junction_network"]["R_K_per_W"]
    assert math.isclose(sum(fo), api.EXAMPLE_MODULE["Rth_K_per_W"], rel_tol=1e-12)
    assert api.EXAMPLE_DRIVELINE["driveline"]["ratio"] == api.EXAMPLE_REDUCER["ratio"]


def test_another_project_changes_every_example_that_reads_it():
    q = Project.from_dict(edited(**{"controller.deadtime_us": 1.2, "controller.fsw_kHz": 12.0, "dc_link.C_uF": 450.0}))
    assert api.example("EMI", q)["source"]["t_dead_us"] == 1.2 and api.example("EMI", q)["network"]["C_dc_uF"] == 450.0
    assert api.example("MODULE", q)["fsw_kHz"] == 12.0 and api.example("RIPPLE", q)["capacitor"]["C_uF"] == 450.0
    assert api.example("PWM", q)["transition"]["deadtime_us"] == 1.2
    assert api.example("PROTECTION", q)["plant"]["C_uF"] == 450.0
    assert api.example("ASC", q) == api.EXAMPLE_ASC                           # scenario-only examples unchanged
    lean = copy.deepcopy(SYNTHETIC_PROJECT)
    del lean["sections"]["module"]
    with pytest.raises(InputValidationError, match="module"):               # never a silent mix of two products
        api.example("MODULE", Project.from_dict(lean))


def test_sections_are_validated_by_their_parsers():
    for bad, field in ((edited(**{"controller.modulation": "sixstep"}), "controller"),
                       (edited(**{"dc_link.C_uF": -1.0}), "dc_link"),
                       (edited(**{"controller.carrier": "random"}), "controller"),
                       (edited(**{"module.thermal_path": None}), "module")):
        with pytest.raises(InputValidationError, match=field):
            Project.from_dict(bad)
    d = copy.deepcopy(SYNTHETIC_PROJECT)
    del d["sections"]["drive"]
    with pytest.raises(InputValidationError, match="required"):
        Project.from_dict(d)
    d = copy.deepcopy(SYNTHETIC_PROJECT)
    d["sections"]["gearbox"] = {"data": {}}
    with pytest.raises(InputValidationError, match="unknown"):
        Project.from_dict(d)
    d = edited(**{"drive.reference_sha256": "0" * 64})                   # another built-in drive than recorded
    with pytest.raises(InputValidationError, match="not the one"):
        Project.from_dict(d)


def test_limits_null_is_not_declared_and_unlimited_is_explicit(tmp_path):
    d = edited(**{"dc_source.limits": {"discharge_power_max_W": "unlimited", "charge_power_max_W": None,
                                       "discharge_current_max_A": 400.0, "charge_current_max_A": 200.0}})
    p = Project.from_dict(d)
    lim = p.limits_dict()
    assert math.isinf(lim["discharge_power_max_W"]) and lim["charge_power_max_W"] is None
    q = load_project(save_project(p, tmp_path / "p.json"))
    assert q.digest() == p.digest() and q.to_dict()["sections"]["dc_source"]["data"]["limits"][
        "discharge_power_max_W"] == "unlimited"


def test_consistency_rules_catch_two_values_for_one_quantity():
    rows = lambda d: {(f["rule"], f["status"]) for f in check_project(Project.from_dict(d))["findings"]}  # noqa: E731
    assert ("PRJ-01", "INCONSISTENT") in rows(edited(**{"module.thermal_path.Rth_K_per_W": 0.12}))
    assert ("PRJ-10", "INCONSISTENT") in rows(edited(**{"driveline.reducer.ratio": 8.5}))
    assert ("PRJ-02", "WARNING") in rows(edited(**{"controller.deadtime_us": 1.2}))
    assert ("PRJ-08", "WARNING") in rows(edited(**{"controller.torque_path.actuator_tau_ms": 0.1}))


def test_revision_diff_names_sections_paths_and_affected_analyses():
    a = builtin_project()
    b = Project.from_dict(edited(**{"controller.deadtime_us": 1.2}))
    d = diff_projects(a, b)
    assert list(d["changed_sections"]) == ["controller"]
    assert [x["path"] for x in d["changed_sections"]["controller"]["paths"]] == ["deadtime_us"]
    assert {"emi", "pwm", "module_losses"} <= set(d["affected_analyses"])
    assert {"decision", "protection", "machine_design"} <= set(d["unaffected_analyses"])
    for aid, v in d["affected_analyses"].items():
        assert set(v["sections"]) <= set(ANALYSES[aid][1])


def test_working_copy_revision_and_stale_results():
    p = builtin_project()
    same = p.with_section("controller", p.data("controller"))
    assert same is p                                                     # unchanged content: not modified
    w = p.with_section("controller", {**p.data("controller"), "deadtime_us": 1.2})
    assert w.modified and w.label.endswith("(modified)")
    with pytest.raises(InputValidationError):
        w.as_revision("B", "")                                          # a revision needs its change note
    r = w.as_revision("B", "dead time measured on the target")
    assert not r.modified and r.change_log[-1]["revision"] == "B"
    used = p.usage("emi")
    assert stale_sections(used, p) == []
    assert stale_sections(used, r) == ["controller"]
    assert stale_sections(p.usage("protection"), r) == []               # the dc-link result is still current
    other = Project.from_dict({**copy.deepcopy(SYNTHETIC_PROJECT),
                               "project": {**SYNTHETIC_PROJECT["project"], "id": "OTHER"}})
    assert stale_sections(used, other)[0] == "<project>"


def test_request_usage_tells_project_data_from_local_edits():
    p = builtin_project()
    body = {**api.example("EMI"), "drive": p.data("drive"), "limits": p.limits_dict()}
    u = request_usage(p, "emi", body)
    assert u["analysis"] == "emi" and u["local_edits"] == [] and set(u["sections"]) == set(ANALYSES["emi"][1])
    body["network"] = {**body["network"], "C_y_nF": 220.0}
    assert request_usage(p, "emi", body)["local_edits"] == ["EMI network"]
    # provenance text and table rounding are not edits; a dropped field that the engine would default is only
    # "the project's" while the default equals the project value
    txt = {**api.example("EMI"), "network": {**api.example("EMI")["network"], "basis": "UI"}}
    assert request_usage(p, "emi", txt)["local_edits"] == []
    jn = api.example("MISSION")["junction_network"]
    rounded = {**api.example("MISSION"), "module": api.example("MODULE"),
               "junction_network": {**jn, "R_K_per_W": [round(x, 6) for x in jn["R_K_per_W"]]}}
    assert request_usage(p, "lifetime", rounded)["local_edits"] == []
    lean = copy.deepcopy(SYNTHETIC_PROJECT)
    del lean["sections"]["module"]
    u = request_usage(Project.from_dict(lean), "module", {"module": api.example("MODULE")})
    assert u["components"]["module"]["missing_in_project"] and u["local_edits"] == ["module"]
    assert set(TASK_ANALYSIS.values()) <= set(ANALYSES)


def test_cli_project_commands(tmp_path, capsys):
    a = tmp_path / "a.json"
    assert cli(["project", "export", str(a)]) == 0
    assert cli(["project", "check", str(a)]) == 0
    b = tmp_path / "b.json"
    d = json.loads(a.read_text(encoding="utf-8"))
    d["project"]["revision"] = "B"
    d["sections"]["driveline"]["data"]["reducer"]["ratio"] = 8.5
    b.write_text(json.dumps(d), encoding="utf-8")
    assert cli(["project", "check", str(b)]) == 2                     # INCONSISTENT
    capsys.readouterr()
    assert cli(["project", "diff", str(a), str(b), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert list(out["changed_sections"]) == ["driveline"] and "efficiency" in out["affected_analyses"]
    assert cli(["project", "show"]) == 0
