"""Service, request API and CLI layers."""

import json
import math
from pathlib import Path

import pytest

from conftest import golden
from traction_workbench import service as S
from traction_workbench import spec_fixtures as sf
from traction_workbench.cli import main
from traction_workbench.errors import InputValidationError
from traction_workbench import api
from traction_workbench.io import load_case

EX = Path(__file__).resolve().parents[1] / "examples"


def test_declared_units_drive_matches_builtin_results():
    c = load_case(EX / "cases" / "req_ts_012_json_drive.json")
    rules = " | ".join(r["rule"] for r in c.conversions.records)
    assert "line-to-line resistance -> per-phase" in rules and "psi_PM" in rules and "fundamental RMS" in rules
    rec = S.evaluate_case(json.loads((EX / "cases" / "req_ts_012_json_drive.json").read_text(encoding="utf-8")))
    op = rec["conditions"][0]["policy_solution"]["operating_point"]
    e = next(x for x in golden("golden_inverse.json")["cases"] if x["case_id"].startswith("I02"))["expected_policy_solution"]
    assert abs(op["id_A_peak"] - e["id_A_peak"]) < 1e-6 and abs(op["Pdc_W"] - e["Pdc_W"]) < 1e-3
    assert rec["verdict"]["verdict"] == "PASS" and rec["unit_conversions"]


@pytest.mark.parametrize("name, verdict", [
    ("req_ts_012_600V.json", "PASS"), ("req_ts_012_450V_sizing.json", "FAIL"),
    ("req_ts_012_10s_without_rating.json", "UNKNOWN"), ("req_ts_012_10s_with_example_rating.json", "UNKNOWN"),
    ("regen_100Nm_12000rpm.json", "FAIL"), ("vdc_range_550_650.json", "PASS"),   # certified from the low end
])
def test_example_cases(name, verdict):
    rec = S.evaluate_case(json.loads((EX / "cases" / name).read_text(encoding="utf-8")))
    assert rec["verdict"]["verdict"] == verdict


def test_api_info_and_presets():
    info = api.info({})
    assert info["drive"]["drive_id"] == "SYNTH_IPMSM_200KW_REF_V1" and len(info["presets"]) >= 6
    rec = api.evaluate({"requirement": info["presets"][0]["req"]})
    assert rec["verdict"]["verdict"] == "PASS"


def test_api_curve_hits_golden_capability():
    cv = api.curve({"Vdc_V": 600, "speeds_rpm": [6000, 12000]})
    by = {r["speed_rpm"]: r for r in cv["rows"]}
    assert by[6000]["policy_max_Nm"] == pytest.approx(306.8288961202995, abs=1e-4)
    assert by[12000]["policy_max_Nm"] == pytest.approx(152.5550589346928, abs=1e-4)
    assert by[12000]["policy_min_Nm"] == pytest.approx(-83.71107155914689, abs=1e-4)


def test_api_map_and_screening():
    mp = api.idiq({"speed_rpm": 12000, "Vdc_V": 600, "torque_Nm": 150})
    assert mp["contours"]["voltage_budget"] and mp["contours"]["torque_request"] and mp["policy_point"]
    t = api.timing(api.EXAMPLE_TIMING)
    assert t["duplicate_budgets"] and t["claim"]["status"] == "FEASIBLE"
    ov = api.overvoltage({"C_uF": 500, "V1_V": 600, "V_limit_V": 850, "speed_rpm": 12000, "torque_Nm": -80,
                          "reaction_time_ms": 2})
    assert ov["regen_operating_point"]["Pdc_W"] < 0 and ov["claim"]["status"] == "INFEASIBLE"
    with pytest.raises(InputValidationError):
        api.overvoltage({"C_uF": 500, "V1_V": 600, "V_limit_V": 850, "speed_rpm": 12000, "torque_Nm": 80})


def test_api_validation_errors():
    with pytest.raises(InputValidationError):
        api.evaluate({"requirement": {"id": "X", "torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 0}})
    with pytest.raises(InputValidationError):
        api.curve({"Vdc_V": "abc"})


def test_cli(tmp_path, capsys):
    assert main(["solve", "--n", "12000", "--torque", "150", "--vdc", "600"]) == 0
    assert "policy_static" in capsys.readouterr().out
    out = tmp_path / "rec"
    assert main(["evaluate", str(EX / "cases" / "req_ts_012_600V.json"), "--out", str(out)]) == 0
    assert len(list(out.glob("*.json"))) == 1 and len(list(out.glob("*.md"))) == 1
    assert main(["evaluate", str(EX / "cases" / "regen_100Nm_12000rpm.json"), "--exit-code"]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"drive": {"builtin": "SYNTH_IPMSM_200KW_REF_V1"}, "requirement": {"id": "B", "text": "t", '
                   '"target": {"value": 1, "unit": "N*m"}, "conditions": {"speed": {"value": 1, "unit": "rpm"}, '
                   '"Vdc": {"value": 600, "unit": "kilovolt"}}}}')
    assert main(["evaluate", str(bad)]) == 4
    assert main(["acceptance"]) == 0
