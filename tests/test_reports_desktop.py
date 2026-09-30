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
    pas = api.passive({"C_uF": 500, "V0_V": 600, "Vf_V": 60, "t_target_s": 120, "R_kohm": 90, "V_max_V": 600,
                       "P_allow_W": 5, "active_R_ohm": 1737})
    nodes = [{"name": "j", "kind": "foster", "R": [0.01, 0.03], "tau": [0.01, 1.0], "ref": "T_f"},
             {"name": "w", "kind": "cauer", "R": [0.003, 0.005], "C": [1500.0, 6000.0], "ref": "T_f"}]
    jobs = [(SC.fig_system_overview, (info,)),
            (SC.fig_dclink_schematic, ("discharge", {"C_uF": 500, "V0_V": 600, "Vf_V": 60, "R_ohm": 1737, "rectifying": True,
                                                     "spinning": True})),
            (SC.fig_dclink_schematic, ("overvoltage", {"C_uF": 500, "V1_V": 600, "V_limit_V": 850, "P_in_W": 95000.0})),
            (SC.fig_safe_state_schematic, ({"Vdc_V": 600, "rectifying": True, "asc_label": "a", "fw_label": "b"},)),
            (SC.fig_dclink_schematic, ("passive", {"C_uF": 500, "V0_V": 600, "Vf_V": 60, "R_ohm": 9e4})),
            (F.fig_passive_discharge, (pas, SF2.passive_curves(pas), SF2.passive_window(pas))),
            (SC.fig_thermal_network, (nodes, det["coolant"])),
            (F.fig_zth, (SF2.zth_curves(det["model"]),))]
    for i, (fn, args) in enumerate(jobs):
        fig = Figure(figsize=(10, 5))
        fn(fig, *args)
        fig.savefig(tmp_path / f"s{i}.png", dpi=50)
    assert len(list(tmp_path.glob("s*.png"))) == len(jobs)


@pytest.mark.parametrize("lang, theme", [("ko", "light"), ("en", "dark")])
def test_review_and_oew_hev_figures_render(tmp_path, lang, theme):
    """Figures of the review analyses (protection, ASC, module, ripple, lifetime) and of the OEW / HEV addendum."""
    from traction_workbench.plots import oew_hev_figures as OH
    from traction_workbench.plots import review_figures as RF
    from traction_workbench.plots import schematics as SC
    set_language(lang)
    style.apply(theme)
    try:
        prot = api.protection(api.EXAMPLE_PROTECTION)
        asc = api.asc({})
        mod = api.module_losses({})
        rip = api.dclink_ripple({})
        life = api.lifetime({})
        oew = api.oew({})
        cmp = api.oew_compare({"compare_speeds_rpm": [2000.0, 8000.0, 14000.0]})
        js = api.hev_joint({"n_levels": 7})
        cr = api.hev_crank({"crank": {**api.EXAMPLE_HEV["crank"], "theta0_deg": [0, 90]}})
        rej = api.hev_rejection({})
        pl = api.hev_planetary({})
        em = api.emi({"n_grid": 40})
        eo = api.emi_oew({})
        rq = js["request"]
        jobs = [(RF.fig_protection_timeline, (prot,)), (RF.fig_threshold_window, (prot,)),
                (RF.fig_protection_loop, (prot,)), (RF.fig_asc_transient, (asc,)), (RF.fig_module_losses, (mod,)),
                (RF.fig_ripple, (rip,)), (RF.fig_lifetime, (life,)),
                (OH.fig_oew_voltage_sets, (oew,)), (OH.fig_oew_point, (oew,)), (OH.fig_oew_compare, (cmp,)),
                (OH.fig_oew_paired, (oew,)), (OH.fig_oew_ripple, (oew,)), (OH.fig_hev_joint, (js,)),
                (OH.fig_hev_crank, (cr,)), (OH.fig_hev_rejection, (rej,)), (OH.fig_planetary, (pl,)),
                (SC.fig_oew_schematic, (oew["topology"],)), (OH.fig_emi_screening, (em,)), (OH.fig_emi_measured, (em,)),
                (OH.fig_oew_cm, (eo,)), (SC.fig_emi_network, (em["network"],)),
                (SC.fig_hev_schematic, ({"p1_W": rq["branch_P_dc_W"][0], "p2_W": rq["branch_P_dc_W"][1],
                                         "p_src_W": rq["P_source_W"]},))]
        for i, (fn, args) in enumerate(jobs):
            fig = Figure(figsize=(11, 6))
            fn(fig, *args)
            fig.savefig(tmp_path / f"r{i:02d}.png", dpi=50)
        assert len(list(tmp_path.glob("r*.png"))) == len(jobs)
    finally:
        set_language("ko")
        style.apply("light")


@pytest.mark.parametrize("lang, theme", [("ko", "light"), ("en", "dark")])
def test_efficiency_figures_render(tmp_path, lang, theme):
    """Point (defined, regen, mixed flow, no point), boundary maps, mission and module A/B figures."""
    from traction_workbench.plots import efficiency_figures as EF
    set_language(lang)
    style.apply(theme)
    try:
        small = {"map_speeds_rpm": [1000.0, 6000.0, 12000.0], "map_torques_Nm": [-100.0, 50.0, 200.0]}
        jobs = [(EF.fig_efficiency_point, api.efficiency({})),
                (EF.fig_efficiency_point, api.efficiency({"speed_rpm": 4000.0, "torque_Nm": -120.0})),
                (EF.fig_efficiency_point, api.efficiency({"loss_model": "surrogate", "speed_rpm": 1000.0, "torque_Nm": -0.1})),
                (EF.fig_efficiency_point, api.efficiency({"torque_Nm": 5000.0})),
                (EF.fig_efficiency_maps, api.efficiency_map(small)),
                (EF.fig_efficiency_mission, api.efficiency_mission({})),
                (EF.fig_module_compare, api.module_compare({"compare": {**api.EXAMPLE_EFFICIENCY["compare"],
                                                                        "requests": [[6000.0, 150.0, 600.0]]},
                                                            "mission": None}))]
        for i, (fn, res) in enumerate(jobs):
            fig = Figure(figsize=(11, 6))
            fn(fig, res)
            fig.savefig(tmp_path / f"e{i}.png", dpi=50)
        assert len(list(tmp_path.glob("e*.png"))) == len(jobs)
    finally:
        set_language("ko")
        style.apply("light")


@pytest.mark.parametrize("lang, theme", [("ko", "light"), ("en", "dark")])
def test_pwm_and_driveline_figures_render(tmp_path, lang, theme):
    """Variable-PWM policies, timing / transition, ripple; anti-jerk variants (with and without wheel radius) and
    the stability map."""
    from traction_workbench.plots import pwm_figures as PF
    set_language(lang)
    style.apply(theme)
    try:
        segs = api.EXAMPLE_PWM["segments"][:2]
        dl_nor = {**api.EXAMPLE_DRIVELINE, "driveline": {**api.EXAMPLE_DRIVELINE["driveline"], "wheel_radius_m": None},
                  "requirement": {"t_to_90_max_s": 0.3, "peak_jerk_max": 1e4, "settle_max_s": 1.0}}
        jobs = [(PF.fig_pwm_policies, api.pwm_policies({"segments": segs, "use_capacitor": False})),
                (PF.fig_pwm_timing, api.pwm_timing({})), (PF.fig_pwm_ripple, api.pwm_ripple({"fsw_list_kHz": [10.0]})),
                (PF.fig_driveline, api.driveline({})), (PF.fig_driveline, api.driveline(dl_nor)),
                (PF.fig_driveline_stability, api.driveline_stability({"stability": {"Kd_list": [1.0, 3.0],
                                                                                     "delay_ms_list": [0.0, 10.0, 30.0]}}))]
        for i, (fn, res) in enumerate(jobs):
            fig = Figure(figsize=(11, 6))
            fn(fig, res)
            fig.savefig(tmp_path / f"p{i}.png", dpi=50)
        assert len(list(tmp_path.glob("p*.png"))) == len(jobs)
    finally:
        set_language("ko")
        style.apply("light")


@pytest.mark.parametrize("lang, theme", [("ko", "light"), ("en", "dark")])
def test_machine_design_figures_render(tmp_path, lang, theme):
    """Trade study, winding (balanced, fractional-slot and infeasible) and concept-sizing figures."""
    from traction_workbench.plots import machine_figures as MF
    set_language(lang)
    style.apply(theme)
    try:
        tr_ = api.machine_trade({"candidates": api.EXAMPLE_MACHINE["candidates"][:3],
                                 "checks": api.EXAMPLE_MACHINE["checks"][:2] + api.EXAMPLE_MACHINE["checks"][3:4],
                                 "envelope_speeds_rpm": [0.0, 8000.0, 16000.0]})
        jobs = [(MF.fig_machine_trade, tr_), (MF.fig_winding, api.winding({})),
                (MF.fig_winding, api.winding({"Q": 12, "p": 5, "y": 1, "parallel_paths": 1, "turns_per_coil": 20})),
                (MF.fig_winding, api.winding({"Q": 10, "p": 4, "y": 1, "parallel_paths": 1, "compare": None})),
                (MF.fig_concept_sizing, api.concept_sizing({}))]
        for i, (fn, res) in enumerate(jobs):
            fig = Figure(figsize=(11, 6))
            fn(fig, res)
            fig.savefig(tmp_path / f"m{i}.png", dpi=50)
        assert len(list(tmp_path.glob("m*.png"))) == len(jobs)
    finally:
        set_language("ko")
        style.apply("light")


def test_pdf_report(tmp_path):
    from traction_workbench.report_pdf import build_pdf
    case = json.loads((EX / "cases" / "req_ts_012_450V_sizing.json").read_text(encoding="utf-8"))
    rec, obj, c = S.evaluate_case_full(case)
    out = build_pdf(tmp_path / "r.pdf", rec, obj, c, envelope=False)
    data = out.read_bytes()
    assert data[:5] == b"%PDF-" and len(data) > 50_000
    assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") + data.count(b"/Type /Page/") >= 5


def test_pdf_report_gives_the_callers_plot_theme_back(tmp_path):
    """The report is light, but building it must not restyle the application's plots (a dark-theme user who saved
    a PDF got white figures on every page afterwards)."""
    import matplotlib as mpl
    from traction_workbench.report_pdf import build_pdf
    case = json.loads((EX / "cases" / "req_ts_012_600V.json").read_text(encoding="utf-8"))
    rec, obj, c = S.evaluate_case_full(case)
    style.apply("dark")
    try:
        build_pdf(tmp_path / "r.pdf", rec, obj, c, envelope=False)
        assert style.theme_name() == "dark"
        assert mpl.rcParams["axes.facecolor"] == style.THEMES["dark"]["bg"]
        assert mpl.rcParams["text.color"] == style.THEMES["dark"]["fg"]
    finally:
        style.apply("light")


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
        lay = {page.layers_table.item(r, 0).text(): page.layers_table.item(r, 1).text()
               for r in range(page.layers_table.rowCount())}
        from traction_workbench.desktop.pages.decision import LAYER_NAMES
        # separate statements, named for people; the fifth (review of 63a2b61, 4) compares the model margin with the
        # declared error budget and is 'not assessed' without one - it never changes the model verdict
        assert set(lay) == {LAYER_NAMES[k]() for k in ("mathematical", "model", "requirement", "qualification",
                                                       "robustness")}
        assert len(lay) == 5 and lay[LAYER_NAMES["robustness"]()].startswith("NOT_ASSESSED")
        ex = win.pages["explorer"]
        ex._picked(-250.0, 120.0)
        assert ex.views.pv.point.id_A == -250.0 and ex.views.pv.point.iq_A == 120.0
        fwd_status = ex.claims.item(0, 1).text()
        assert fwd_status in ("ACCEPTED", "DIAGNOSTIC ONLY")                # a picked point is never just 'OK'
        mdl = win.pages["model"]
        orig_limits = dict(win.state.limits_dict)
        mdl.refresh()
        assert mdl.audit.rowCount() >= 5
        kind, _val, _sc = mdl.lim_fields["discharge_power_max_W"]
        kind.setCurrentIndex(kind.findData("unlimited"))                  # declared unlimited is math.inf, not None
        mdl._apply_limits()
        assert win.state.limits_dict["discharge_power_max_W"] == float("inf")
        kind.setCurrentIndex(kind.findData("missing"))
        mdl._apply_limits()
        assert win.state.limits_dict["discharge_power_max_W"] is None
        win.state.set_limits(orig_limits)
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
        sp.run_ftti()
        rows = {sp.t_ftti.item(r, 0).text(): sp.t_ftti.item(r, 1).text() for r in range(sp.t_ftti.rowCount())}
        assert any("SYS_FRTI" in v or "DECAY" in v for v in rows.values())   # the chosen path is shown
        sp.f_endpoint.setCurrentIndex(sp.f_endpoint.findData("command_issued"))
        sp.run_ftti()
        assert sp.t_ftti.item(0, 1).text().startswith("UNKNOWN")           # a command is not the physical safe state
        sp.f_endpoint.setCurrentIndex(sp.f_endpoint.findData("physical_safe_state"))
        sp.d_n.setValue(3000.0)
        sp.run_discharge()
        txt = " ".join(sp.t_dis.item(r, 1).text() for r in range(sp.t_dis.rowCount()))
        assert txt.startswith("UNKNOWN") and "rectification risk" in txt and "not a bound" in txt
        sp.run_passive()
        sp.run_overvoltage()
        assert sp.t_ov.item(0, 1).text().startswith("INFEASIBLE")
        sp.run_safe()
        assert all(p._draw is not None for p in (sp.s_dis, sp.s_pas, sp.p_pas, sp.s_ov, sp.s_safe))
        assert page.views.overview._draw is not None or ex.views.overview._draw is not None
        pro = win.pages["protection"]
        pro.run()
        assert pro.last is not None and pro.last["trace"]["protected"]
        pw = win.pages["power"]
        pw.run_module()
        assert pw.last_module is not None and pw.last_module["losses"]["established"]
        oh = win.pages["oew_hev"]
        oh.run_oew()
        assert oh.last_oew is not None and oh.last_oew["result"]["witness"] is not None
        oh.run_rej()
        assert oh.last_rej["claim"]["status"] == "INFEASIBLE"             # example 9.4: 1 ms reaction is too slow
        ep = win.pages["emi"]
        ep.run()
        assert ep.last is not None and ep.last["claim"]["status"] == "UNKNOWN"      # screening is never a pass
        # project data package (R2): a result names its product data; a revision switch reloads the pages' product
        # inputs and marks results that read a changed section stale; local edits are reported, never hidden
        import copy as _copy
        from traction_workbench.examples import SYNTHETIC_PROJECT
        from traction_workbench.project import Project, builtin_project
        assert ep.last["project_usage"]["local_edits"] == [] and win.banners["emi"].property("state") == "info"
        d = _copy.deepcopy(SYNTHETIC_PROJECT)
        d["project"]["revision"] = "T"
        d["sections"]["controller"]["data"]["deadtime_us"] = 1.2
        d["sections"]["dc_link"]["data"]["C_uF"] = 450.0
        win.state.set_project(Project.from_dict(d))
        assert ep.e_td.value() == 1.2 and win.pages["power"].r_C.value() == 450.0
        assert win.pages["safety"].d_C.value() == 450.0 and win.pages["power"].m_dt.value() == 1.2
        assert win.banners["emi"].property("state") == "stale"
        ep.run()
        assert ep.last["project_usage"]["project_label"].endswith("rev T") and win.banners["emi"].property("state") == "info"
        ep.net["C_y_nF"].setValue(220.0)
        ep.run()
        assert ep.last["project_usage"]["local_edits"] == ["EMI network"] and win.banners["emi"].property("state") == "local"
        win.state.set_project(builtin_project())
        efp = win.pages["efficiency"]
        efp.run_point()
        assert efp.last_point["ledger"]["boundaries"]["edrive"]["status"] == "DEFINED"
        efp.r_on.setChecked(False)                      # no reducer data: the eDrive efficiency is UNKNOWN, not 100 %
        efp.run_point()
        assert efp.last_point["ledger"]["boundaries"]["edrive"]["status"] == "UNKNOWN"
        efp.t_req.load([[6000.0, 150.0, 600.0]])
        efp.ab_mis.setChecked(False)
        efp.run_ab()
        assert efp.last_ab["rows"][0]["compare"]["verdict"] in ("A_LOWER_LOSS", "B_LOWER_LOSS", "UNDECIDED")
        pd = win.pages["pwm_driveline"]
        pd.t_seg.load([[5.0, 6000.0, 45.0, 600.0, 70.0], [5.0, 3000.0, 250.0, 600.0, 95.0]])
        pd.p_cap.setChecked(False)
        pd.run_policies()
        assert pd.last_pol is not None and len(pd.last_pol["policies"]) == 3
        pd.run_driveline()
        assert pd.last_dl["variants"]["combined"]["stability"]["stable"]
        pd.run_transients()
        assert pd.last_trn["sampling_here"]["status"] in ("OK", "UNKNOWN", "VIOLATION")
        pd.sn_kind.setCurrentIndex(pd.sn_kind.findData("dc_link_shunt"))      # a single shunt fails at low modulation
        pd.run_policies()
        assert all(any("current sampling" in v for v in p["violations"]) for p in pd.last_pol["policies"])
        pd.sg_drop.setText("450-560")                                          # wheel-speed dropout, declared fallback
        pd.m_em.setChecked(True)
        pd.run_driveline()
        fb = pd.last_dl["variants"]["feedback"]
        assert fb["safety"]["status"] in ("FEASIBLE", "INFEASIBLE") and "evaluated_until_s" in fb["metrics"]
        mp = win.pages["machine"]
        mp.t_cand.load([["ref", 1.0, 1.0, 1.0, None, None], ["N+10%", 1.1, 1.0, 1.0, None, None],
                        ["L+", 1.2, 1.2, 1.0, None, None]])            # stack change without end shares: refused
        mp.t_chk.load([["ugo", "ugo", 12000.0, 450.0, None, 900.0], ["asc", "asc", 12000.0, 450.0, None, None]])
        mp.t_env_on.setChecked(False)
        mp.run_trade()
        rows = {r["candidate"]: r for r in mp.last_trade["rows"]}
        assert "error" in rows["L+"] and rows["ref"]["checks"]["asc"]["status"] == "UNKNOWN"    # no limit: no pass
        assert rows["N+10%"]["checks"]["ugo"]["status"] == "INFEASIBLE"
        mp.run_wind()
        assert mp.last_wind["balanced"] and mp.w_send.isEnabled()
        mp.run_size()
        assert mp.last_size is not None
        win.set_theme("dark", persist=False)          # never write the test machine's user settings
        win.set_theme("light", persist=False)
        assert not (app.property("twb_errors") or [])
        win.close()
    finally:
        TaskRunner.synchronous = False
        matplotlib.use("Agg", force=True)
        style.apply("light")


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_figure_words_fit_a_small_window(lang):
    """UX review F1: on a small canvas (a 1280 x 720 window gives a figure about 480 px wide) the two side-by-side
    plots keep their width (a legend or note wider than its axes used to squeeze a plot to a sliver), the subplot
    titles neither overlap nor leave the figure, and the figure title wraps instead of being cut."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    set_language(lang)
    style.apply("light")
    try:
        d, lim = sf.synthetic_drive(), sf.synthetic_limits()
        pt = PolicyEvaluator(d, Scenario("t", 12000.0, 600.0, lim)).solve(150.0).point
        th = api.thermal({"speed_rpm": 3000, "Vdc_V": 600, "coolant_temp_C": 65, "torque_Nm": 450, "duration_s": 10})
        jobs = [(F.fig_power_constraints, (O.power_chain(pt), O.constraint_rows(pt)),
                 "REQ-TS-012 · 12000 rpm · 150 N·m · 600 V · minimum-current policy point"),
                (F.fig_thermal, (SF.availability_curve(th["availability"]),
                                 SF.thermal_curves(api._thermal_model(None), th["request"]["nodes"], 65.0, 30.0), 450.0,
                                 10.0), "thermal → torque availability · n = 3000 rpm · coolant inlet 65 °C")]
        for fn, args, title in jobs:
            for w_px in (430, 480, 800):
                fig = Figure(figsize=(w_px / 100, 3.6), dpi=100)
                FigureCanvasAgg(fig)
                fn(fig, *args, title=title)
                F.fit_texts(fig)
                fig.canvas.draw()
                r = fig.canvas.get_renderer()
                a1, a2 = fig.axes[:2]
                for ax in (a1, a2):                                     # no plot squeezed by its legend or notes
                    assert ax.get_window_extent(r).width >= 0.2 * w_px, (fn.__name__, w_px, lang)
                t1, t2 = (ax.title.get_window_extent(r) for ax in (a1, a2))
                assert t1.x1 <= t2.x0 and t1.x0 >= -1 and t2.x1 <= w_px + 1, (fn.__name__, w_px, lang)
                st = fig._suptitle.get_window_extent(r)
                assert st.x0 >= -1 and st.x1 <= w_px + 1, (fn.__name__, w_px, lang)
    finally:
        set_language("ko")
        style.apply("light")
