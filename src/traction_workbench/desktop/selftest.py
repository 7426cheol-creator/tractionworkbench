"""Headless self-test of the desktop application (also run on the packaged executable in CI).

Drives every page through its real code path (tasks run synchronously), checks the decision verdicts of the
built-in examples and the golden acceptance, writes window screenshots, a PDF report and ``selftest.json``.
Exit code 0 only if every check passes.
"""

from __future__ import annotations

import json
import platform
import time
import traceback
from pathlib import Path

from .. import __version__, api

EXPECTED = {"ts012_600": "PASS", "ts012_450": "FAIL", "ts012_10s": "UNKNOWN", "regen_80": "PASS",
            "regen_100": "FAIL", "dis_350": "FAIL", "range": "UNKNOWN", "stall": "PASS"}


def run_self_test(app, out_dir) -> int:
    from .main_window import MainWindow
    from .worker import TaskRunner

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    app.setProperty("twb_selftest", True)
    TaskRunner.synchronous = True
    checks: list[dict] = []
    t_start = time.perf_counter()

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)})

    def shot(win, name):
        app.processEvents()
        win.grab().save(str(out / f"{name}.png"))

    try:
        win = MainWindow()
        win.resize(1600, 1000)
        win.show()
        app.processEvents()
        page = win.pages["decision"]
        for i, p in enumerate(api.PRESETS):
            page.presets.setCurrentIndex(i)
            page.run()
            app.processEvents()
            got = page.result["record"]["verdict"]["verdict"] if page.result else None
            check(f"decision:{p['key']}", got == EXPECTED.get(p["key"]), f"verdict {got}, expected {EXPECTED.get(p['key'])}")
            if p["key"] == "ts012_600":
                shot(win, "01_decision_summary")
                page.tabs.setCurrentIndex(1)
                for k in range(page.views.tabs.count()):
                    page.views.tabs.setCurrentIndex(k)
                    shot(win, f"02_decision_view_{k}")
                page.tabs.setCurrentIndex(2)            # T-n position (computes the envelope)
                shot(win, "03_decision_tn")
                page.tabs.setCurrentIndex(0)
            if p["key"] == "ts012_450":
                page.tabs.setCurrentIndex(3)
                shot(win, "04_decision_analyses")
                from ..report_pdf import build_pdf
                res = page.result
                pdf = build_pdf(out / "report_REQ-TS-012-LV.pdf", res["record"], res["rec"], res["case"])
                check("pdf_report", pdf.is_file() and pdf.stat().st_size > 50_000, f"{pdf.stat().st_size} bytes")
                page.tabs.setCurrentIndex(0)

        from .main_window import PAGES
        rows = {k: i for i, (k, *_rest) in enumerate(PAGES)}

        def visit(key, _row, actions, shots):
            win.nav.setCurrentRow(rows[key])            # rows follow PAGES (pages can be inserted)
            pg = win.pages[key]
            for a in actions:
                getattr(pg, a)() if isinstance(a, str) else a(pg)
                app.processEvents()
            for k, (tab_setter, name) in enumerate(shots):
                if tab_setter:
                    tab_setter(pg)
                shot(win, name)
            return pg

        ex = visit("explorer", 1, ["run", lambda pg: pg._picked(-300.0, 100.0)], [(None, "05_explorer")])
        check("explorer:forward", ex.views.pv is not None and abs(ex.views.pv.point.id_A + 300.0) < 1e-9)
        tr_pg = visit("trajectory", 2, ["run"], [(None, "06_trajectory_speed"), (lambda pg: pg.tabs.setCurrentIndex(1), "07_trajectory_vars")])
        tr_pg.m_torque.setChecked(True)
        tr_pg.run()
        check("trajectory", tr_pg.p_plane._draw is not None)
        shot(win, "08_trajectory_torque")
        perf = visit("performance", 3, [lambda pg: pg.res_combo.setCurrentIndex(0), "run"],
                     [(None, "09_envelope"), (lambda pg: pg.tabs.setCurrentIndex(1), "10_map_eta"),
                      (lambda pg: pg.tabs.setCurrentIndex(2), "11_envelope_detail")])
        check("performance", perf.res is not None and perf.res["map"]["status"].size > 0)
        des = visit("design", 4, ["run1", "run2"], [(None, "12_design_sizing"), (lambda pg: pg.tabs.setCurrentIndex(1), "13_design_dominance")])
        check("design", des.p_curve._draw is not None and des.p_dom._draw is not None)
        saf = visit("safety", 5, ["run_ftti", "run_discharge", "run_passive", "run_overvoltage", "run_safe"],
                    [(None, "14_safety_ftti"), (lambda pg: pg.tabs.setCurrentIndex(1), "15_safety_dclink"),
                     (lambda pg: pg.dc_tabs.setCurrentIndex(1), "15a_safety_passive"),
                     (lambda pg: pg.dc_tabs.setCurrentIndex(2), "15b_safety_overvoltage"),
                     (lambda pg: pg.tabs.setCurrentIndex(2), "16_safety_state")])
        check("safety", all(p._draw is not None for p in (saf.p_ftti, saf.p_dis, saf.p_pas, saf.p_ov, saf.p_safe)))
        th = visit("thermal", 6, ["run"], [(None, "17_thermal"), (lambda pg: pg.tabs.setCurrentIndex(1), "17b_thermal_network"),
                                           (lambda pg: pg.tabs.setCurrentIndex(2), "17c_thermal_zth"),
                                           (lambda pg: pg.tabs.setCurrentIndex(3), "17d_thermal_editor")])
        check("thermal", th.plot._draw is not None and "s" in th.headline.text(), th.headline.text())
        t_ref = th.last["res"]["request"]["time_to_first_limit_s"]
        th.c_flow.setValue(5.0)
        th.run()
        t_low = th.last["res"]["request"]["time_to_first_limit_s"]
        check("thermal:coolant_flow", isinstance(t_ref, float) and isinstance(t_low, float) and t_low < t_ref,
              f"10 L/min {t_ref} s, 5 L/min {t_low} s")
        th.c_flow.setValue(10.0)
        th.tabs.setCurrentIndex(0)
        check("schematics", all(p._draw is not None for p in (saf.s_dis, saf.s_pas, saf.s_ov, saf.s_safe, page.views.overview)))
        pro = visit("protection", 0, ["run"], [(None, "22_protection_timeline"),
                                               (lambda pg: pg.ptabs.setCurrentIndex(1), "23_protection_window"),
                                               (lambda pg: pg.ptabs.setCurrentIndex(2), "24_protection_loop")])
        prot_ids = [r["id"] for r in (pro.last or {}).get("rows", [])]
        check("protection:ov", pro.last is not None and pro.last["trace"]["protected"] and len(prot_ids) == 9,
              f"{pro.last and pro.last['summary_status']} {prot_ids}")
        pro.preset.setCurrentIndex(1)
        pro.run()
        check("protection:ot", pro.last is not None and pro.last["unit"] == "degC" and pro.last["trace"]["protected"])
        pro.tabs.setCurrentIndex(1)
        pro.run_asc()
        asc_ok = pro.last_asc is not None and pro.last_asc["claim"]["status"] == "UNKNOWN" and \
            len(pro.last_asc["requirements"]) == 2
        check("protection:asc", asc_ok, pro.last_asc and pro.last_asc["claim"]["detail"])
        shot(win, "25_asc_transient")
        pw = visit("power", 0, ["run_module"], [(None, "26_power_module")])
        check("power:module", pw.last_module is not None and pw.last_module["losses"]["established"]
              and pw.last_module["operating_point"]["Pinv_module_W"] > 0)
        pw.tabs.setCurrentIndex(1)
        pw.run_ripple()
        shot(win, "27_power_ripple")
        check("power:ripple", pw.last_ripple is not None and pw.last_ripple["I_cap_rms_A"] > 0
              and pw.last_ripple["claims"]["capacitor_life"]["status"] == "UNKNOWN")
        pw.tabs.setCurrentIndex(2)
        pw.run_life()
        shot(win, "28_power_life")
        check("power:lifetime", pw.last_life is not None and pw.last_life["damage"]["claim"]["status"] == "UNKNOWN"
              and pw.last_life["damage"]["cycles_counted"] > 0)
        oh = visit("oew_hev", 0, ["run_oew"], [(None, "29_oew_sets"), (lambda pg: pg.o_tabs.setCurrentIndex(1), "30_oew_point"),
                                                (lambda pg: pg.o_tabs.setCurrentIndex(3), "31_oew_paired"),
                                                (lambda pg: pg.o_tabs.setCurrentIndex(5), "32_oew_circuit")])
        w = (oh.last_oew or {}).get("result", {}).get("witness")
        check("oew:point", w is not None and oh.last_oew["result"]["status"] == "FEASIBLE"
              and abs(w["identities"]["ports_minus_winding_W"]) < 1e-3, oh.last_oew and oh.last_oew["result"]["status"])
        g = oh.last_oew["geometry"] if oh.last_oew else {}
        check("oew:geometry", g.get("admissible_pairs") == 20 and g.get("admissible_unique_alphabeta") == 7
              and abs(g.get("hull_inradius_V", 0) - 400.0) < 1e-9, str({k: g.get(k) for k in ("admissible_pairs",
                                                                                                 "hull_inradius_V")}))
        oh.run_compare()
        shot(win, "33_oew_tn")
        cmp_rows = [r for r in (oh.last_cmp or {}).get("rows", []) if r.get("single_vsi_Nm") is not None]
        check("oew:compare", bool(cmp_rows) and cmp_rows[-1]["oew_common_bus_Nm"] > cmp_rows[-1]["single_vsi_Nm"])
        oh.tabs.setCurrentIndex(1)
        oh.run_joint()
        shot(win, "34_hev_joint")
        check("hev:joint", oh.last_joint is not None and
              oh.last_joint["joint_cells_feasible"] < oh.last_joint["box_cells_feasible_separately"])
        oh.run_crank()
        shot(win, "35_hev_crank")
        check("hev:crank", oh.last_crank is not None and oh.last_crank["claim"]["status"] in ("UNKNOWN", "INFEASIBLE"))
        oh.run_rej()
        check("hev:rejection", oh.last_rej is not None and abs(oh.last_rej["E_margin_J"] - 10.625) < 1e-9)
        oh.run_planetary()
        shot(win, "36_hev_planetary")
        check("hev:planetary", oh.last_pl is not None and oh.last_pl["check"]["status"] == "FEASIBLE")
        visit("model", 7, [], [(None, "18_model")])
        vv = visit("verification", 8, ["run"], [(None, "19_verification")])
        check("acceptance", "PASS" in vv.summary.text() and "MISMATCH" not in vv.summary.text(), vv.summary.text())
        # flux-map drive: model switch + a decision on the D2 test drive
        win.state.set_drive({"builtin": "MANUFACTURED_FLUX_MAP_TEST_DRIVE"}, "flux map", "builtin")
        win.nav.setCurrentRow(0)
        page.req_id.setText("FM-20")
        page.torque.setValue(20.0)
        page.speed.setValue(3000.0)
        page.vdc.setValue(600.0)
        page.range_on.setChecked(False)
        page.dur_none.setChecked(True)
        for w in (page.an_dom, page.an_relax, page.an_size_v, page.an_size_i):
            w.setChecked(False)
        page.run()
        got = page.result["record"]["verdict"]["verdict"] if page.result else None
        check("decision:flux_map", got in ("PASS", "FAIL", "UNKNOWN") and page.result["record"]["model"]["fidelity"] == "D2", got)
        page.tabs.setCurrentIndex(1)
        shot(win, "20_flux_map_decision")
        win.set_theme("dark")
        shot(win, "21_dark_theme")
        errs = app.property("twb_errors") or []
        check("no_error_dialogs", not errs, "; ".join(errs))
    except Exception:  # noqa: BLE001
        check("exception", False, traceback.format_exc())
    report = {"software": __version__, "python": platform.python_version(), "platform": platform.platform(),
              "elapsed_s": time.perf_counter() - t_start, "checks": checks,
              "passed": sum(c["ok"] for c in checks), "total": len(checks), "ok": all(c["ok"] for c in checks)}
    (out / "selftest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        import sys
        if sys.stdout is not None:
            print(json.dumps({k: report[k] for k in ("passed", "total", "ok", "elapsed_s")}))
            for c in checks:
                if not c["ok"]:
                    print("FAIL", c["check"], c["detail"][:2000])
    except Exception:  # noqa: BLE001 - windowed builds have no console
        pass
    return 0 if report["ok"] else 1
