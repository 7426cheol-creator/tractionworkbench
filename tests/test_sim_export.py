"""Exports for vehicle simulators (system view, item 12): the maps are the policy points of the model (energy balance
per cell, the full-load curve as the policy capability), cells off the envelope or undecided stay empty, every format
carries the same numbers, and the FMU (FMI 2.0, validated and run with FMPy) returns the tables at the grid nodes,
clamps the request to the full-load curve and mirrors reverse rotation."""

import json
import math
import zipfile

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench.extensions import sim_export as se

B = dict(api.EXAMPLE_EFFICIENCY)
DRIVE = api._eff_drive(B)
RED = api._reducer(B.get("reducer"))
SPEEDS = [0.0, 3000.0, 6000.0, 9000.0, 12000.0]


@pytest.fixture(scope="module")
def maps():
    return se.compute_maps(DRIVE, Vdc_list=[600.0], limits=api._limits(B), speeds_rpm=SPEEDS,
                           torques_Nm=[-300.0, -150.0, 0.0, 150.0, 300.0, 450.0], reducer=RED, oil_temp_C=80.0,
                           source={"test": True})


def test_cells_are_policy_points_with_their_energy_balance(maps):
    from traction_workbench.scenario import Scenario
    from traction_workbench.solvers.capability import policy_capability
    from traction_workbench.solvers.policy import PolicyEvaluator
    t, st = maps["tables"], maps["status"]
    ok = st == se.STATUS_CODE["FEASIBLE"]
    assert ok.sum() > 10
    for a, i, j in np.argwhere(ok):
        T, n = maps["torques_Nm"][i], maps["speeds_rpm"][j]
        assert t["P_shaft_W"][a, i, j] == pytest.approx(T * n * math.pi / 30.0, abs=1e-6, rel=1e-9)
        # DC power = shaft power + inverter + motor loss (the ledger's telescoping identity)
        assert t["P_dc_W"][a, i, j] - t["P_shaft_W"][a, i, j] == pytest.approx(
            t["loss_inverter_W"][a, i, j] + t["loss_motor_W"][a, i, j], rel=1e-9, abs=1e-6)
    ev = PolicyEvaluator(DRIVE, Scenario("t", 6000.0, 600.0, api._limits(B)))
    assert maps["T_max_Nm"][0, 2] == pytest.approx(policy_capability(ev, 1, certify=False).value_Nm)
    beyond = st == se.STATUS_CODE["BEYOND"]
    assert beyond.any() and np.isnan(t["P_dc_W"][beyond]).all()            # off the envelope: empty, never filled
    for (a, i, j) in np.argwhere(beyond):
        T = maps["torques_Nm"][i]
        assert T > maps["T_max_Nm"][a, j] or T < maps["T_min_Nm"][a, j]
    assert "motor: PWM harmonic copper" in maps["meta"]["not_evaluated"]


def test_a_voltage_without_a_scaling_law_stays_unknown():
    kw = dict(limits=api._limits(B), speeds_rpm=[3000.0, 6000.0], torques_Nm=[-100.0, 0.0, 100.0])
    m = se.compute_maps(DRIVE, Vdc_list=[500.0], **kw)
    assert (m["status"] == se.STATUS_CODE["UNKNOWN"]).all() and np.isnan(m["tables"]["P_dc_W"]).all()
    b = {**B, "module": {**B["module"], "vdc_scaling": api._SYNTH_VDC_SCALING}}
    m2 = se.compute_maps(api._eff_drive(b), Vdc_list=[500.0], **kw)
    assert (m2["status"] == se.STATUS_CODE["FEASIBLE"]).all()


def test_auto_torque_grid_spans_the_full_load():
    m = se.compute_maps(DRIVE, Vdc_list=[600.0], limits=api._limits(B), speeds_rpm=[1000.0, 8000.0],
                        torque_step_Nm=100.0)
    tq = m["torques_Nm"]
    assert 0.0 in tq and tq[-1] >= np.nanmax(m["T_max_Nm"]) and tq[0] <= np.nanmin(m["T_min_Nm"])
    assert all(abs(x / 100.0 - round(x / 100.0)) < 1e-9 for x in tq)


def test_fill_nearest_only_fills_and_marks():
    t = np.array([[[1.0, np.nan], [np.nan, 4.0]]])
    f, mask = se.fill_nearest(t, np.isfinite(t))
    assert f[0, 0, 0] == 1.0 and f[0, 1, 1] == 4.0 and mask.tolist() == [[[False, True], [True, False]]]


def test_csv_grids_and_mat_carry_the_same_numbers(maps, tmp_path):
    from scipy.io import loadmat
    p = se.write_csv_long(maps, tmp_path / "long.csv")
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 + len(maps["Vdc_V"]) * len(maps["speeds_rpm"]) * len(maps["torques_Nm"])
    files = se.write_csv_grids(maps, tmp_path / "grids")
    assert (tmp_path / "grids" / "P_dc_W_600V.csv").exists() and any(f.name == "README.txt" for f in files)
    se.write_mat(maps, tmp_path / "m.mat")
    s = loadmat(str(tmp_path / "m.mat"), squeeze_me=True, struct_as_record=False)["twb_maps"]
    pdc = np.atleast_3d(s.P_dc_W)
    a = 0
    for i, j in [(3, 1), (2, 2), (0, 3)]:
        want = maps["tables"]["P_dc_W"][a, i, j]
        got = pdc[i, j, a] if pdc.ndim == 3 else pdc[i, j]
        assert (math.isnan(want) and math.isnan(got)) or got == pytest.approx(want)
    assert list(np.atleast_1d(s.speed_rpm)) == maps["speeds_rpm"]
    assert json.loads(s.meta_json)["schema"] == "twb-simmaps/1"


def test_the_fmu_validates_and_returns_the_tables(maps, tmp_path):
    fmpy = pytest.importorskip("fmpy")
    from fmpy.fmi2 import FMU2Slave
    from fmpy.validation import validate_fmu
    path = tmp_path / "drive.fmu"
    r = se.write_fmu(maps, path, "DriveMapsTest")
    names = zipfile.ZipFile(path).namelist()
    assert "modelDescription.xml" in names and "sources/DriveMapsTest.c" in names
    assert validate_fmu(str(path)) == []
    if r["binary"] is None:
        pytest.skip("no C compiler: a source FMU (validated, not run)")
    md = fmpy.read_model_description(str(path))
    vr = {v.name: v.valueReference for v in md.modelVariables}
    s = FMU2Slave(guid=md.guid, unzipDirectory=fmpy.extract(str(path)), modelIdentifier="DriveMapsTest",
                  instanceName="t")
    s.instantiate()
    s.setupExperiment(startTime=0.0)
    s.enterInitializationMode()
    s.exitInitializationMode()

    def ev(n, T, V=600.0):
        s.setReal([vr["speed_rpm"], vr["torque_request_Nm"], vr["Vdc_V"]], [n, T, V])
        s.doStep(0.0, 1e-3)
        keys = ("torque_Nm", "T_max_Nm", "T_min_Nm", "limited", "P_dc_W", "loss_total_W")
        return dict(zip(keys, s.getReal([vr[k] for k in keys])))
    ok = maps["status"][0] == se.STATUS_CODE["FEASIBLE"]
    for i, j in np.argwhere(ok):
        out = ev(maps["speeds_rpm"][j], maps["torques_Nm"][i])
        assert out["P_dc_W"] == pytest.approx(maps["tables"]["P_dc_W"][0, i, j], rel=1e-12, abs=1e-9)
        assert out["limited"] == 0.0
    hi = ev(6000.0, 1e4)                                     # beyond the full load: clamped to it
    assert hi["limited"] == 1.0 and hi["torque_Nm"] == pytest.approx(maps["T_max_Nm"][0, 2])
    fw = ev(6000.0, 150.0)
    rv = ev(-6000.0, -150.0)                                 # reverse rotation: the mirror, same power
    assert rv["torque_Nm"] == pytest.approx(-150.0) and rv["P_dc_W"] == pytest.approx(fw["P_dc_W"])
    assert rv["T_max_Nm"] == pytest.approx(-fw["T_min_Nm"]) and rv["T_min_Nm"] == pytest.approx(-fw["T_max_Nm"])
    s.terminate()
    s.freeInstance()
    res = fmpy.simulate_fmu(str(path), fmi_type="ModelExchange", stop_time=0.01, output=["P_dc_W"],
                            start_values={"speed_rpm": 6000.0, "torque_request_Nm": 150.0, "Vdc_V": 600.0})
    assert res["P_dc_W"][-1] == pytest.approx(fw["P_dc_W"])


def test_api_round_trip(tmp_path):
    from traction_workbench.project import builtin_project
    ex = api.example("SIM_EXPORT", builtin_project())
    m = api.sim_maps({**ex, "speed_max_rpm": 8000.0, "speed_step_rpm": 4000.0, "torque_step_Nm": 100.0})
    assert m["counts"]["FEASIBLE"] > 0 and m["meta"]["source"]["switching_energy_vs_Vdc"] == "test voltage only"
    back = se.maps_from_json(json.loads(json.dumps(m)))
    assert np.array_equal(np.isnan(back["tables"]["P_dc_W"]),
                          np.array([[[v is None for v in row] for row in sl] for sl in m["tables"]["P_dc_W"]]))
    r = api.sim_write(m, "csv", tmp_path / "x.csv")
    assert r["files"] and (tmp_path / "x.csv").exists()
    with pytest.raises(Exception):
        api.sim_write(m, "xlsx", tmp_path / "x.xlsx")
