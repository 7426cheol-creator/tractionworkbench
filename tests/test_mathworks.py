"""MathWorks transfer package (twb-mathworks/1): the Python reference, its data and semantics carried to MATLAB /
Simulink / System Composer, and checked across the three layers.

X-numbers refer to the portability handoff's test list.  The target-side tests run the package's own MATLAB code
in GNU Octave when it is installed (a MATLAB-language proxy: MATLAB and Simulink themselves stay NOT_RUN); the
re-import tests do not need any runtime - they build a report from the reference values and then tamper with it.
"""

import ast
import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from traction_workbench import mathworks as MW
from traction_workbench.cli import main as cli
from traction_workbench.mathworks import package as P
from traction_workbench.project import builtin_project

OCTAVE = shutil.which("octave-cli") or shutil.which("octave")
needs_octave = pytest.mark.skipif(OCTAVE is None, reason="GNU Octave not installed: the MATLAB-language target "
                                                         "checks run in the CI parity job")
ENGINE = {"physics", "models", "solvers", "analysis", "extensions", "io", "api", "service", "decision", "project",
          "parsers", "exchange", "spec_fixtures"}


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    out = tmp_path_factory.mktemp("mw") / "pkg"
    man = MW.export_package(out)
    return out, man


def _load(pkg_dir, rel):
    return json.loads((Path(pkg_dir) / rel).read_text(encoding="utf-8"))


def _cases(pkg_dir):
    return {c["case_id"]: c for n in ("forward", "flux_lookup", "requirement_witness")
            for c in _load(pkg_dir, f"cases/{n}.json")["cases"]}


def _rewrite(pkg_dir, rel, fn):
    """Change a package file AND re-seal the manifest (to reach the checks behind the integrity check)."""
    p = Path(pkg_dir)
    doc = _load(p, rel)
    fn(doc)
    data = P._json_bytes(doc)
    (p / rel).write_bytes(data)
    man = _load(p, "manifest.json")
    for f in man["files"]:
        if f["path"] == rel:
            f["sha256"], f["bytes"] = hashlib.sha256(data).hexdigest(), len(data)
    man["semantic_fingerprint"] = P.fingerprint(man["files"])
    (p / "manifest.json").write_bytes(P._json_bytes(man))


def _perfect_report(pkg_dir) -> dict:
    """A report as a target that reproduces layer 2 exactly would write it."""
    man = _load(pkg_dir, "manifest.json")
    cases = []
    for cid, c in _cases(pkg_dir).items():
        if c["kind"] == "requirement_witness" and c["python"] is None:
            cases.append({"case_id": cid, "kind": c["kind"], "status": "NOT_SUPPORTED", "native": {}})
        else:
            cases.append({"case_id": cid, "kind": c["kind"], "status": "PASS", "native": copy.deepcopy(c["python"])})
    return {"schema": MW.REPORT_SCHEMA, "runtime": {"product": "MATLAB", "version": "24.2", "release": "2024b"},
            "package": {"semantic_fingerprint": man["semantic_fingerprint"],
                        "consumed_files": [{"path": f["path"], "sha256": f["sha256"]} for f in man["files"]]},
            "stages": {"package_check": {"status": "PASS"}, "native_evaluator": {"status": "PASS"},
                       "simulink_harness": {"status": "NOT_RUN"}},
            "cases": cases}


# -- layers 1 / 2 -------------------------------------------------------------------------------------------


def test_the_oracle_never_imports_the_engine():
    """Layer-1 values must not come from the implementation they judge (source-separated acceptance)."""
    src = Path(MW.__file__).with_name("oracle.py").read_text(encoding="utf-8")
    mods = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add("." * n.level + (n.module or ""))
    assert mods <= {"__future__", "hashlib", "json", "math", "pathlib"}, mods


def test_python_agrees_with_the_accepted_source_on_every_case(pkg):
    out, man = pkg
    assert man["oracle_disagreements"] == []
    cases = _cases(out)
    kinds = {o["kind"] for c in cases.values() for o in c.get("oracle", [])}
    assert {"golden", "closed_form", "definition"} <= kinds
    golden = [c for c in cases.values() if any(o["kind"] == "golden" for o in c.get("oracle", []))]
    assert {c["case_id"] for c in golden} >= {f"REF.F0{i}_{s}" for i, s in (
        (0, "STANDSTILL"), (1, "FORWARD_MOTORING"), (2, "VOLTAGE_AND_DC_VIOLATION"), (3, "REGENERATION"),
        (4, "REVERSE_MOTORING"), (5, "SPMSM_NO_ROTATIONAL_OR_INVERTER_LOSS"))}
    assert all(c["python_vs_oracle"]["status"] == "PASS" for c in cases.values() if "python_vs_oracle" in c)


def test_boundary_cases_are_where_they_claim_to_be(pkg):
    """X06: points placed +-0.5 and +-2 tolerances from a limit classify as constructed (ACTIVE is a pass)."""
    c = _cases(pkg[0])
    st = {k: v["python"]["constraints"] for k, v in c.items() if k.startswith("BND.")}
    assert [st[f"BND.VOLTAGE.{t}"]["VOLTAGE"] for t in ("inside_2tol", "inside_half_tol", "outside_half_tol",
                                                         "outside_2tol")] == ["SATISFIED", "ACTIVE", "ACTIVE",
                                                                              "VIOLATED"]
    assert [st[f"BND.CURRENT.{t}"]["CURRENT"] for t in ("inside_2tol", "exact", "outside_2tol")] == \
        ["SATISFIED", "ACTIVE", "VIOLATED"]
    assert [st[f"BND.CHARGE.{t}"]["DC_CHARGE_POWER"] for t in ("inside_2tol", "exact", "outside_2tol")] == \
        ["SATISFIED", "ACTIVE", "VIOLATED"]
    assert st["BND.DOMAIN.speed_on_edge"]["SPEED_MAX"] == "ACTIVE"
    assert c["BND.DOMAIN.speed_beyond"]["python"]["violated_groups"] == ["DOMAIN"]
    assert c["BND.VOLTAGE.outside_half_tol"]["python"]["accepted"] is True       # on the boundary is a pass
    assert c["BND.VOLTAGE.outside_2tol"]["python"]["accepted"] is False


def test_semantic_cases_keep_their_meaning(pkg):
    c = _cases(pkg[0])
    nd = c["SEM.DC_LIMITS.not_declared"]["python"]
    assert nd["accepted"] is False and nd["gate_reasons"] == ["MISSING_INPUT"]      # missing is not unlimited
    assert c["SEM.DC_LIMITS.declared_unlimited"]["python"]["accepted"] is True
    assert c["SEM.TEMPERATURE.no_reference"]["python"]["reason"] == "MISSING_INPUT"
    assert c["REF.F00_STANDSTILL"]["python"]["energy_mode"] == "STANDSTILL"
    assert c["REF.F03_REGENERATION"]["python"]["energy_mode"] == "REGENERATING"
    assert c["REF.F04_REVERSE_MOTORING"]["python"]["energy_mode"] == "MOTORING"      # -n, -T: motoring
    out = c["REF.MANUFACTURED.outside_map"]["python"]
    assert out["evaluable"] is False and out["Te_Nm"] is None                        # no extrapolation
    dom = c["REF.MANUFACTURED.allowed_domain_violated"]["python"]                    # covered, not allowed
    assert dom["evaluable"] is True and dom["violated_groups"] == ["DOMAIN"]
    lk = {k[len("VF.MAP."):]: v["python"] for k, v in c.items() if k.startswith("VF.MAP.") and v["kind"] ==
          "flux_lookup"}
    assert lk["edge_left_cell_valid"]["covered"] and lk["edge_lower_cell_valid"]["covered"]
    assert not lk["edge_both_invalid"]["covered"] and not lk["in_hole_cell"]["covered"]
    assert not lk["outside_id"]["covered"] and not lk["outside_iq"]["covered"] and lk["upper_corner"]["covered"]
    assert lk["no_temperature"]["reason"] == "MISSING_INPUT" and lk["above_planes"]["reason"] == \
        "OUTSIDE_MODEL_DOMAIN"
    assert lk["blend_70C"]["psi_d_Wb"] == pytest.approx(0.5 * (lk["interior"]["psi_d_Wb"] +
                                                              lk["plane_120C"]["psi_d_Wb"]), rel=1e-14)
    q = {k: v["python"] for k, v in c.items() if k.startswith("VF.QODD.")}
    assert q["VF.QODD.negative_iq"]["psi_q_Wb"] == pytest.approx(-q["VF.QODD.positive_iq"]["psi_q_Wb"], rel=1e-14)
    assert q["VF.QODD.negative_iq"]["psi_d_Wb"] == pytest.approx(q["VF.QODD.positive_iq"]["psi_d_Wb"], rel=1e-14)


def test_requirement_witnesses_carry_the_layer2_claim(pkg):
    c = _cases(pkg[0])
    ok = c["REQ.req_ts_012_600V.REQ-TS-012@A.c0"]
    assert ok["layer2_claim"]["status"] == "FEASIBLE" and ok["python"]["claim_support"] == "FEASIBLE"
    assert ok["requirement"]["original_text"].startswith("REQ-TS-012")
    rg = c["REQ.regen_100Nm_12000rpm.REQ-RG-100@A.c0"]
    assert rg["layer2_claim"]["status"] == "INFEASIBLE"
    assert rg["python"]["witness_accepted"] is False and rg["python"]["claim_support"] == "UNKNOWN"  # never INFEASIBLE
    lv = c["REQ.req_ts_012_450V_sizing.REQ-TS-012-LV@A.c0"]
    assert lv["witness"] is None and lv["python"] is None and "NOT_SUPPORTED" in lv["native_scope"]


# -- package integrity, determinism, regeneration -----------------------------------------------------------


def test_export_is_deterministic(tmp_path):
    """X01: the same inputs give the same files and fingerprint (only the manifest's generation time moves)."""
    a = MW.export_package(tmp_path / "a")
    b = MW.export_package(tmp_path / "b")
    assert a["semantic_fingerprint"] == b["semantic_fingerprint"]
    for f in a["files"]:
        assert (tmp_path / "a" / f["path"]).read_bytes() == (tmp_path / "b" / f["path"]).read_bytes()
    assert MW.check_package(tmp_path / "a")["status"] == "PASS"
    for f in a["files"]:                                   # ASCII JSON: identical bytes in MATLAB and Octave
        if f["path"].endswith(".json"):
            (tmp_path / "a" / f["path"]).read_bytes().decode("ascii")


def test_check_refuses_damage_unknown_schema_and_missing_semantics(tmp_path):
    """X01: a damaged file, an unknown schema, a missing unit, a transposed map, a double temperature correction
    and another convention are each refused - never guessed."""
    base = tmp_path / "p"
    MW.export_package(base)

    def fresh(name):
        d = tmp_path / name
        shutil.copytree(base, d)
        return d
    d = fresh("damaged")
    (d / "models" / "PRODUCT.json").write_bytes((d / "models" / "PRODUCT.json").read_bytes().replace(b"0.015", b"0.016"))
    r = MW.check_package(d)
    assert r["status"] == "FAIL" and any("models/PRODUCT.json" in p for p in r["problems"])
    d = fresh("schema")
    _rewrite_manifest = json.loads((d / "manifest.json").read_text())
    _rewrite_manifest["schema"] = "twb-mathworks/2"
    (d / "manifest.json").write_text(json.dumps(_rewrite_manifest))
    assert "not guessed" in MW.check_package(d)["problems"][0]
    d = fresh("unit")
    _rewrite(d, "contract.json", lambda c: c["quantities"]["vd_V"].update(unit=""))
    assert any("vd_V has no unit" in p for p in MW.check_package(d)["problems"])
    d = fresh("transposed")

    def transpose(m):
        pl = m["motor"]["flux"]["planes"][0]
        for k in ("psi_d_Wb", "psi_q_Wb", "valid"):
            pl[k] = [list(r) for r in zip(*pl[k])]
    _rewrite(d, "models/VF_D2_MAP.json", transpose)
    assert any("shape is not [numel(id_axis) numel(iq_axis)] = [6 7]" in p for p in MW.check_package(d)["problems"])
    d = fresh("double")
    _rewrite(d, "models/VF_D2_MAP.json", lambda m: m["motor"]["flux"].update(
        psi_temperature={"coeff_per_K": -0.0012, "valid_C": [-40, 180], "basis": "x"}))
    assert any("correct temperature twice" in p for p in MW.check_package(d)["problems"])
    d = fresh("convention")
    _rewrite(d, "models/PRODUCT.json", lambda m: m["conventions"].update(park="power_invariant"))
    assert any("conventions differ" in p for p in MW.check_package(d)["problems"])


def test_regeneration_never_overwrites_edited_or_foreign_files(tmp_path):
    """X09: an edited generated file stops the next export; a foreign folder is refused; files the generator did
    not write are kept; unchanged generated files are replaced."""
    d = tmp_path / "pkg"
    MW.export_package(d)
    (d / "results" / "notes.txt").write_text("mine")
    (d / "README.md").write_text("edited")
    with pytest.raises(MW.PackageConflict, match="README.md"):
        MW.export_package(d)
    MW.export_package(d, force=True)
    assert (d / "results" / "notes.txt").read_text() == "mine" and (d / "README.md").read_text() != "edited"
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.txt").write_text("x")
    with pytest.raises(MW.PackageConflict, match="not empty"):
        MW.export_package(other)


# -- report re-import ---------------------------------------------------------------------------------------


def test_report_reimport_statuses_and_identity(tmp_path):
    """X10 / X12: no report -> every target stage NOT_RUN; a report of this package -> recomputed PASS; another
    package's or a tampered report is not linked; a changed design makes the result stale."""
    d = tmp_path / "pkg"
    MW.export_package(d)
    v = MW.verify_report(d)
    assert (v["package_check"], v["parity"], v["model_generation"], v["target_environment"]) == \
        ("PASS", "NOT_RUN", "NOT_RUN", "NOT_CHECKED") and not v["linked_as_current_evidence"]
    rep = _perfect_report(d)
    rp = tmp_path / "report.json"
    rp.write_text(json.dumps(rep))
    v = MW.verify_report(d, rp, builtin_project())
    assert v["parity"] == "PASS" and v["linked_as_current_evidence"] and v["cases"]["NOT_SUPPORTED"] == 1
    assert "2024b" in v["target_environment"]
    foreign = copy.deepcopy(rep)
    foreign["package"]["semantic_fingerprint"] = "0" * 64
    rp.write_text(json.dumps(foreign))
    assert MW.verify_report(d, rp)["parity"] == "FOREIGN_REPORT"
    stale = copy.deepcopy(rep)
    stale["package"]["consumed_files"][0]["sha256"] = "1" * 64
    rp.write_text(json.dumps(stale))
    assert MW.verify_report(d, rp)["parity"] == "FOREIGN_REPORT"
    bad = copy.deepcopy(rep)
    case = next(c for c in bad["cases"] if c["case_id"] == "REF.F01_FORWARD_MOTORING")
    case["native"]["Te_Nm"] += 1e-6                             # the report still says PASS
    rp.write_text(json.dumps(bad))
    v = MW.verify_report(d, rp)
    assert v["parity"] == "FAIL" and v["failed_cases"][0]["case_id"] == "REF.F01_FORWARD_MOTORING"
    assert any("differ from the recomputation" in p for p in v["problems"])
    missing = copy.deepcopy(rep)
    missing["cases"] = missing["cases"][1:]
    rp.write_text(json.dumps(missing))
    assert MW.verify_report(d, rp)["cases"]["MISSING"] == 1 and MW.verify_report(d, rp)["parity"] == "FAIL"
    rp.write_text(json.dumps(rep))
    p = builtin_project()
    changed = p.with_section("dc_source", {**p.data("dc_source"), "Vdc_nominal_V": 650.0})
    v = MW.verify_report(d, rp, changed)
    assert v["parity"] == "PASS" and not v["linked_as_current_evidence"]
    assert any("not current evidence" in x for x in v["problems"])


def test_architecture_candidates_separate_relations_and_never_guess(pkg):
    a = _load(pkg[0], "architecture/candidates.json")
    items = {i["id"]: i for i in a["data_items"]}
    assert items["PAR.INV.Imax"]["kind"] == "design_limit" and "not a software saturation" in \
        items["PAR.INV.Imax"]["meaning"]
    assert items["PAR.MOTOR.pole_pairs"]["meaning"].startswith("pole PAIRS")
    sig = {s["id"]: s for s in a["signals"]}
    assert "not declared" in sig["SIG.TORQUE_REQUEST"]["rate"]               # nothing invented
    assert sig["SIG.PHASE_CURRENT_MEAS"]["rate"] == "10000 Hz"               # from the controller section
    assert {c["id"] for c in a["physical_connections"]} == {"CON.HV_DC", "CON.AC_3PH", "CON.SHAFT", "CON.HEAT"}
    case_ids = set(_cases(pkg[0]))
    assert all(set(link["cases"]) <= case_ids for link in a["requirement_links"])
    prof = _load(pkg[0], "architecture/profile_template.json")
    mapped = {m["id"] for m in prof["id_map"]}
    assert set(items) | set(sig) | {c["id"] for c in a["components"]} == mapped


def test_gap_report_and_readme_state_the_scope(pkg):
    g = _load(pkg[0], "gap_report.json")
    st = {i["capability"]: i["status"] for i in g["items"]}
    assert st["forward evaluation, constant dq (D1)"] == "exported_executable"
    assert st["Simulink static evaluation harness"] == "exported_recipe"
    assert "exported_data_only" in st.values() and "not_applicable" in st.values() and "missing" in st.values()
    text = (pkg[0] / "README.md").read_text(encoding="utf-8")
    assert "NOT_RUN" in text and "외삽" in text and "fingerprint" in text


def test_cli_mathworks(tmp_path, capsys):
    d = tmp_path / "pkg"
    assert cli(["mathworks", "export", str(d)]) == 0
    assert cli(["mathworks", "check", str(d)]) == 0
    capsys.readouterr()
    assert cli(["mathworks", "verify", str(d), "--json"]) == 1           # nothing ran yet: not a pass
    assert json.loads(capsys.readouterr().out)["parity"] == "NOT_RUN"


# -- the target side, executed (GNU Octave as the MATLAB-language runner) ------------------------------------


@needs_octave
def test_native_matlab_evaluator_reproduces_layers_2_and_1(tmp_path):
    d = tmp_path / "pkg"
    MW.export_package(d)
    r = MW.run_local(d, "octave")
    v = r["verification"]
    assert r["exit_code"] == 0, Path(r["log"]).read_text()
    assert v["parity"] == "PASS" and v["linked_as_current_evidence"], v
    assert v["cases"]["FAIL"] == 0 and v["cases"]["ERROR"] == 0 and v["cases"]["NOT_SUPPORTED"] == 1
    assert v["stages"]["self_checks"] == "PASS" and v["stages"]["simulink_harness"] == "NOT_RUN"
    assert "GNU Octave" in v["target_environment"] and "MATLAB / Simulink not run" in v["target_environment"]


MUTATIONS = {   # file, original, planted defect, cases that must catch it
    "torque_factor": ("staticPoint.m", "tem = 1.5 * P.p", "tem = 1.5000001 * P.p", {"REF.F01_FORWARD_MOTORING"}),
    "no_edge_rule": ("fluxMapLookup.m", "if ~good\n    onD", "if false\n    onD",
                     {"VF.MAP.edge_left_cell_valid", "VF.MAP.edge_lower_cell_valid"}),
    "clip_not_unknown": ("fluxMapLookup.m", "if x < xa(1) || x > xa(nd) || y < ya(1) || y > ya(nq)\n    return\nend",
                         "x = min(max(x, xa(1)), xa(nd));\ny = min(max(y, ya(1)), ya(nq));",
                         {"VF.MAP.outside_id", "REF.MANUFACTURED.outside_map"}),
    "missing_limit_unlimited": ("evaluatePoint.m", "if strcmp(L.(keys{k}).state, 'not_declared')", "if false",
                                {"SEM.DC_LIMITS.not_declared"}),
    "active_is_violation": ("evaluatePoint.m", "elseif slack < -tol", "elseif slack < tol",
                            {"BND.CURRENT.exact", "BND.VOLTAGE.inside_half_tol"}),
    "transposed_map": ("selectPlane.m", "P.valid = v;", "P.valid = v; P.psi_d_Wb = P.psi_d_Wb'; P.psi_q_Wb = P.psi_q_Wb';",
                       {"REF.MANUFACTURED.test_point", "VF.MAP.forward_blend"}),
    "power_invariant_park": ("staticPoint.m", "pac = 1.5 * (vd * id + vq * iq);", "pac = (vd * id + vq * iq);",
                             {"REF.F01_FORWARD_MOTORING", "REF.F03_REGENERATION"}),
    "no_temperature_blend": ("selectPlane.m", "if ischar(interp) && strcmp(interp, 'linear')", "if false",
                             {"VF.MAP.blend_70C", "VF.MAP.forward_blend"}),
    "rs_law_ignored": ("resolveModel.m", "            K.Rs = rs;", "            K.Rs = rs0;", {"VF.TEMP.hot_120C"}),
}


@needs_octave
@pytest.mark.parametrize("name", list(MUTATIONS))
def test_planted_defects_in_the_port_are_caught(pkg, tmp_path, name):
    """The comparison has teeth: each planted defect of the MATLAB code fails the cases built for it."""
    import subprocess
    fn, old, new, must = MUTATIONS[name]
    work = tmp_path / "matlab"
    shutil.copytree(pkg[0] / "matlab", work)
    f = work / "+twb" / fn
    s = f.read_text()
    assert old in s
    f.write_text(s.replace(old, new, 1))
    cmd = (f"addpath('{work}'); pkg = twb.loadPackage('{pkg[0]}'); r = twb.runParity(pkg); "
           f"for k = 1:numel(r.cases); c = r.cases{{k}}; if ~strcmp(c.status, 'PASS'); "
           f"printf('%s %s\\n', c.status, c.case_id); end; end")
    out = subprocess.run([OCTAVE, "--no-gui", "--quiet", "--norc", "--eval", cmd], capture_output=True, text=True,
                         timeout=300).stdout
    failed = {line.split()[1] for line in out.splitlines() if line.startswith(("FAIL", "ERROR"))}
    assert must <= failed, out
