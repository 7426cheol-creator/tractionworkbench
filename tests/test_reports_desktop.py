"""Figures, PDF report, CLI report and a headless smoke test of the desktop application."""

import json
import os
from pathlib import Path

import numpy as np
import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from traction_workbench import api, service as S  # noqa: E402
from traction_workbench import spec_fixtures as sf  # noqa: E402
from traction_workbench.cli import main  # noqa: E402
from traction_workbench.i18n import set_language  # noqa: E402
from traction_workbench.plots import figures as F  # noqa: E402
from traction_workbench.plots import style  # noqa: E402
from traction_workbench.scenario import Scenario  # noqa: E402
from traction_workbench.solvers.policy import PolicyEvaluator  # noqa: E402
from traction_workbench.viz import maps as M, operating as O, safety as SF, sweeps as SW  # noqa: E402

EX = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("lang, theme", [("ko", "light"), ("en", "dark")])
def test_every_figure_renders(tmp_path, lang, theme):
    set_language(lang)
    style.apply(theme)
    try:
        d, lim = sf.synthetic_drive(), sf.synthetic_limits()
        sc = Scenario("t", 12000.0, 600.0, lim)
        pt = PolicyEvaluator(d, sc).solve(150.0).point
        pv = O.point_view(d, sc, pt.id_A, pt.iq_A, 150.0)
        pl = M.idiq_plane(d, lim, 12000.0, 600.0, 150.0, resolution=121)
        ts = SW.torque_sweep(d, lim, 12000.0, 600.0, n=15)
        env = SW.envelope(d, lim, 600.0, n=9)
        mp = M.tn_map(d, lim, 600.0, *M.default_axes(d, lim, 600.0, 7, 8))
        th = api.thermal({"speed_rpm": 3000, "Vdc_V": 600, "coolant_temp_C": 65, "torque_Nm": 450, "duration_s": 10})
        dis = api.discharge({"C_uF": 500, "V0_V": 600, "Vf_V": 60, "t_target_s": 2, "speed_rpm": 500})
        ov = api.overvoltage({"C_uF": 500, "V1_V": 600, "V_limit_V": 850, "speed_rpm": 12000, "torque_Nm": -80,
                              "reaction_time_ms": 2})
        jobs = [
            (F.fig_waveforms, (O.waveforms(pv),)), (F.fig_phasor, (O.phasor(pv), O.hexagon(pv))),
            (F.fig_power_constraints, (O.power_chain(pt), O.constraint_rows(pt))), (F.fig_idiq, (pl, ts)),
            (F.fig_sweep, (ts,)), (F.fig_envelope, (env, [{"id": "R", "speed_rpm": 12000, "torque_Nm": 150, "verdict": "PASS"}])),
            (F.fig_map, (mp, "eta", env)), (F.fig_map, (mp, "I_rms_A", env)),
            (F.fig_ftti, (SF.ftti_timeline(api.timing(api.EXAMPLE_TIMING)),)),
            (F.fig_discharge, (dis, SF.discharge_curve(dis))), (F.fig_overvoltage, (ov, SF.overvoltage_curve(ov))),
            (F.fig_safe_state, (SF.asc_vs_speed(d, 600.0, n=21), 12000.0)),
            (F.fig_thermal, (SF.availability_curve(th["availability"]),
                             SF.thermal_curves(api._thermal_model(None), th["request"]["nodes"], 65.0, 30.0), 450.0, 10.0)),
            (F.fig_acceptance, (S.acceptance_summary(),)),
        ]
        for i, (fn, args) in enumerate(jobs):
            fig = Figure(figsize=(9, 6))
            fn(fig, *args)
            fig.savefig(tmp_path / f"{i:02d}.png", dpi=60)
        assert len(list(tmp_path.glob("*.png"))) == len(jobs)
    finally:
        set_language("ko")
        style.apply("light")


def test_schematics_render(tmp_path):
    from traction_workbench.plots import schematics as SC
    from traction_workbench.viz import safety as SF2
    d, lim = sf.synthetic_drive(), sf.synthetic_limits()
    sc = Scenario("t", 12000.0, 600.0, lim)
    pt = PolicyEvaluator(d, sc).solve(-80.0).point
    info = O.overview_info(O.point_view(d, sc, pt.id_A, pt.iq_A, -80.0))
    assert info["energy_mode"] == "REGENERATING" and info["Pdc_W"] < 0
    det = api.thermal_details(api.EXAMPLE_THERMAL, 65.0)
    nodes = [{"name": "j", "kind": "foster", "R": [0.01, 0.03], "tau": [0.01, 1.0], "ref": "T_f"},
             {"name": "w", "kind": "cauer", "R": [0.003, 0.005], "C": [1500.0, 6000.0], "ref": "T_f"}]
    jobs = [(SC.fig_system_overview, (info,)),
            (SC.fig_dclink_schematic, ("discharge", {"C_uF": 500, "V0_V": 600, "Vf_V": 60, "R_ohm": 1737, "rectifying": True,
                                                     "spinning": True})),
            (SC.fig_dclink_schematic, ("overvoltage", {"C_uF": 500, "V1_V": 600, "V_limit_V": 850, "P_in_W": 95000.0})),
            (SC.fig_safe_state_schematic, ({"Vdc_V": 600, "rectifying": True, "asc_label": "a", "fw_label": "b"},)),
            (SC.fig_thermal_network, (nodes, det["coolant"])),
            (F.fig_zth, (SF2.zth_curves(det["model"]),))]
    for i, (fn, args) in enumerate(jobs):
        fig = Figure(figsize=(10, 5))
        fn(fig, *args)
        fig.savefig(tmp_path / f"s{i}.png", dpi=50)
    assert len(list(tmp_path.glob("s*.png"))) == len(jobs)


def test_pdf_report(tmp_path):
    from traction_workbench.report_pdf import build_pdf
    case = json.loads((EX / "cases" / "req_ts_012_450V_sizing.json").read_text(encoding="utf-8"))
    rec, obj, c = S.evaluate_case_full(case)
    out = build_pdf(tmp_path / "r.pdf", rec, obj, c, envelope=False)
    data = out.read_bytes()
    assert data[:5] == b"%PDF-" and len(data) > 50_000
    assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") + data.count(b"/Type /Page/") >= 5


def test_cli_report(tmp_path, capsys):
    out = tmp_path / "rep.pdf"
    assert main(["report", str(EX / "cases" / "req_ts_012_600V.json"), "--pdf", str(out), "--no-envelope"]) == 0
    assert out.stat().st_size > 50_000 and "PASS" in capsys.readouterr().out


def test_desktop_smoke(tmp_path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    matplotlib.use("QtAgg", force=True)
    from PySide6.QtWidgets import QApplication
    from traction_workbench.desktop import theme
    from traction_workbench.desktop.main_window import MainWindow
    from traction_workbench.desktop.worker import TaskRunner
    app = QApplication.instance() or QApplication([])
    app.setProperty("twb_selftest", True)
    theme.apply(app, "light")
    TaskRunner.synchronous = True
    try:
        win = MainWindow()
        win.show()
        page = win.pages["decision"]
        page.presets.setCurrentIndex(1)                 # 450 V: proven infeasible
        page.run()
        assert page.result["record"]["verdict"]["verdict"] == "FAIL"
        assert page.views.plane is not None and page.views.pv is None
        ex = win.pages["explorer"]
        ex._picked(-250.0, 120.0)
        assert ex.views.pv.point.id_A == -250.0 and ex.views.pv.point.iq_A == 120.0
        th = win.pages["thermal"]
        th.run()
        t1 = th.last["res"]["request"]["time_to_first_limit_s"]
        th.c_flow.setValue(5.0)                          # less flow: hotter coolant and a larger convective R
        th.run()
        t2 = th.last["res"]["request"]["time_to_first_limit_s"]
        assert isinstance(t1, float) and isinstance(t2, float) and t2 < t1
        node0 = th.editor.tabs.widget(0)
        node0.r_cauer.setChecked(True)                   # same numbers read as Cauer R/C: still a valid model
        assert th.full_spec()["nodes"][0]["network"] == "cauer"
        from traction_workbench.desktop.thermal_editor import parse_stage_text
        R, X, _how = parse_stage_text("i 1 2 3 4\nr 0.01 0.03 0.08 0.08\ntau 0.002 0.03 0.4 2.5")
        assert R == [0.01, 0.03, 0.08, 0.08] and X == [0.002, 0.03, 0.4, 2.5]
        R, X, _how = parse_stage_text("0.01\t0.002\n0.03\t0.03")
        assert R == [0.01, 0.03] and X == [0.002, 0.03]
        sp = win.pages["safety"]
        sp.run_discharge()
        sp.run_overvoltage()
        sp.run_safe()
        assert all(p._draw is not None for p in (sp.s_dis, sp.s_ov, sp.s_safe))
        assert page.views.overview._draw is not None or ex.views.overview._draw is not None
        win.set_theme("dark")
        win.set_theme("light")
        assert not (app.property("twb_errors") or [])
        win.close()
    finally:
        TaskRunner.synchronous = False
        matplotlib.use("Agg", force=True)
        style.apply("light")
