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
            "regen_100": "FAIL", "dis_350": "FAIL", "range": "PASS", "stall": "PASS"}      # range: monotonicity certificate


def run_self_test(app, out_dir) -> int:
    from .main_window import PAGE_INFO, MainWindow
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

    def reading(name, panel, *keys):
        """The engineering reading of each calculation exists, did not fail and has content."""
        bad = []
        for k in keys:
            ins = (panel.readings.get(k) or (None, None))[1]
            if ins is None or getattr(ins, "failed", False) or not ins.sections or not ins.headline:
                bad.append(f"{k}: {'missing' if ins is None else ins.headline[:160]}")
        check(f"reading:{name}", not bad, "; ".join(bad) or f"{len(keys)} reading(s)")

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
                page.tabs.setCurrentWidget(page.insight)
                shot(win, "01_decision_summary")        # the engineering reading (first tab)
                page.tabs.setCurrentWidget(page.summary_tab)
                shot(win, "01b_decision_evidence")
                page.tabs.setCurrentWidget(page.views)
                for k in range(page.views.tabs.count()):
                    page.views.tabs.setCurrentIndex(k)
                    shot(win, f"02_decision_view_{k}")
                page.tabs.setCurrentWidget(page.env_panel)  # T-n position (computes the envelope)
                shot(win, "03_decision_tn")
                page.tabs.setCurrentWidget(page.insight)
            if p["key"] == "ts012_450":
                page.tabs.setCurrentWidget(page.insight)
                shot(win, "04a_decision_fail_reading")
                page.tabs.setCurrentWidget(page.an_tab)
                shot(win, "04_decision_analyses")
                from ..report_pdf import build_pdf
                res = page.result
                pdf = build_pdf(out / "report_REQ-TS-012-LV.pdf", res["record"], res["rec"], res["case"])
                check("pdf_report", pdf.is_file() and pdf.stat().st_size > 50_000, f"{pdf.stat().st_size} bytes")
                page.tabs.setCurrentWidget(page.insight)

        # PWM consequences at the decision point (review priority 3) and a Vdc stated as battery OCV (priority 2)
        page.presets.setCurrentIndex(0)
        page.run()
        pr = (page.result or {}).get("pwm_risk") or {}
        check("decision:pwm_risk", pr.get("status") == "EVALUATED" and pr["instantaneous_peak"]["bound_A"] >
              pr["fundamental"]["i_peak_A"] and any("PWM" in page.an_tabs.tabText(i) for i in range(page.an_tabs.count())),
              pr.get("status"))
        page.vdc_kind.setCurrentIndex(page.vdc_kind.findData("battery_ocv"))
        page.src_R.setValue(20.0)
        page.run()
        sc_ = ((page.result or {}).get("record") or {}).get("source_coupling") or {}
        check("decision:battery_ocv", sc_.get("status") == "RESOLVED" and sc_["V_terminal_V"][0] < 600.0
              and any("battery OCV" in q for q in page.result["record"]["verdict"]["qualifiers"]), sc_.get("status"))
        page.vdc_kind.setCurrentIndex(page.vdc_kind.findData("inverter_dc_terminal"))

        def visit(key, _row, actions, shots):
            win.show_page(key)
            pg = win.pages[key]
            for a in actions:
                getattr(pg, a)() if isinstance(a, str) else a(pg)
                app.processEvents()
            for k, (tab_setter, name) in enumerate(shots):
                if tab_setter:
                    tab_setter(pg)
                shot(win, name)
            return pg

        ex = visit("explorer", 1, ["run", lambda pg: pg._picked(-300.0, 100.0)],
                   [(lambda pg: pg.views.tabs.setCurrentWidget(pg.views.overview), "05_explorer"),
                    (lambda pg: pg.views.tabs.setCurrentWidget(pg.insight), "05a_explorer_reading")])
        check("explorer:forward", ex.views.pv is not None and abs(ex.views.pv.point.id_A + 300.0) < 1e-9)
        reading("explorer", ex.insight, "explorer")
        tr_pg = visit("trajectory", 2, ["run"], [(lambda pg: pg.tabs.setCurrentWidget(pg.p_plane), "06_trajectory_speed"),
                                                 (lambda pg: pg.tabs.setCurrentWidget(pg.insight), "06a_trajectory_reading"),
                                                 (lambda pg: pg.tabs.setCurrentWidget(pg.p_vars), "07_trajectory_vars")])
        tr_pg.m_torque.setChecked(True)
        tr_pg.run()
        check("trajectory", tr_pg.p_plane._draw is not None)
        reading("trajectory", tr_pg.insight, "trajectory")
        shot(win, "08_trajectory_torque")
        perf = visit("performance", 3, [lambda pg: pg.res_combo.setCurrentIndex(0), "run"],
                     [(lambda pg: pg.tabs.setCurrentWidget(pg.p_env), "09_envelope"),
                      (lambda pg: pg.tabs.setCurrentWidget(pg.insight), "09a_envelope_reading"),
                      (lambda pg: pg.tabs.setCurrentWidget(pg.p_map.parentWidget()), "10_map_eta"),
                      (lambda pg: pg.tabs.setCurrentWidget(pg.p_detail), "11_envelope_detail")])
        check("performance", perf.res is not None and perf.res["map"]["status"].size > 0)
        reading("performance", perf.insight, "performance")
        des = visit("design", 4, ["run1", "run2"],
                    [(lambda pg: pg.tabs.setCurrentWidget(pg.p_curve.parentWidget()), "12_design_sizing"),
                     (lambda pg: pg.tabs.setCurrentWidget(pg.p_dom.parentWidget()), "13_design_dominance"),
                     (lambda pg: pg.tabs.setCurrentWidget(pg.insight), "13a_design_reading")])
        check("design", des.p_curve._draw is not None and des.p_dom._draw is not None)
        reading("design", des.insight, "sizing", "bottleneck")
        # requirement set (review 6198099 user features): the template judged on the project, then candidates
        rs = visit("requirement_set", 4, ["check_reading", "run"],
                   [(lambda pg: pg.tabs.setCurrentWidget(pg.res_split), "12a_requirement_set"),
                    (lambda pg: pg.tabs.setCurrentWidget(pg.prio_table), "12b_requirement_priorities"),
                    (lambda pg: (pg.tabs.setCurrentWidget(pg.insight),
                                 pg.insight.pick.setCurrentIndex(pg.insight.pick.findData("set"))), "12d_requirement_reading")])
        reading("requirement_set", rs.insight, "set", "row")
        sm = rs.result["set"]["summary"] if rs.result else {}
        check("requirement_set", (sm.get("total"), sm.get("PASS"), sm.get("FAIL"), sm.get("UNKNOWN")) == (5, 3, 1, 1)
              and rs.detail.toPlainText().startswith("1. ") and "REQ-A" in rs.detail.toPlainText(), sm)
        rs.cands.setPlainText("charge 150 kW: charge_power_max_W=150000, charge_current_max_A=400\n"
                              "current 250 A: I_peak_max_A=250")
        rs.run()
        cs = {c["name"]: c for c in ((rs.result or {}).get("candidates") or {}).get("candidates", [])}
        check("requirement_set:candidates", cs.get("charge 150 kW", {}).get("improves") == ["REQ-B"]
              and set(cs.get("current 250 A", {}).get("worsens", [])) == {"REQ-A", "REQ-C", "REQ-D", "REQ-E"},
              {k: (c["improves"], c["worsens"]) for k, c in cs.items()})
        rs.tabs.setCurrentWidget(rs.cand_table)
        shot(win, "12c_requirement_candidates")
        rs.tabs.setCurrentWidget(rs.res_split)
        rs.res_table.selectRow(1)
        rs.open_in_decision()
        got = page.result["record"] if page.result else {}
        check("requirement_set:open", got.get("requirement", {}).get("req_id") == "REQ-B"
              and got.get("verdict", {}).get("verdict") == "FAIL", got.get("requirement", {}).get("req_id"))
        saf = visit("safety", 5, ["run_ftti", "run_discharge", "run_passive", "run_overvoltage", "run_safe"],
                    [(lambda pg: pg.ftti_tabs.setCurrentIndex(1), "14_safety_ftti"),
                     (lambda pg: pg.ftti_tabs.setCurrentWidget(pg.i_ftti), "14a_safety_ftti_reading"),
                     (lambda pg: (pg.tabs.setCurrentIndex(1), pg.dc_tabs.setCurrentWidget(pg.s_dis.parentWidget())),
                      "15_safety_dclink"),
                     (lambda pg: pg.dc_tabs.setCurrentWidget(pg.s_pas.parentWidget()), "15a_safety_passive"),
                     (lambda pg: pg.dc_tabs.setCurrentWidget(pg.s_ov.parentWidget()), "15b_safety_overvoltage"),
                     (lambda pg: (pg.dc_tabs.setCurrentWidget(pg.i_dc),
                                  pg.i_dc.pick.setCurrentIndex(pg.i_dc.pick.findData("overvoltage"))),
                      "15c_safety_overvoltage_reading"),
                     (lambda pg: (pg.tabs.setCurrentIndex(2), pg.safe_tabs.setCurrentWidget(pg.s_safe)), "16_safety_state")])
        check("safety", all(p._draw is not None for p in (saf.p_ftti, saf.p_dis, saf.p_pas, saf.p_ov, saf.p_safe)))
        reading("safety:ftti", saf.i_ftti, "ftti")
        reading("safety:dclink", saf.i_dc, "discharge", "passive", "overvoltage")
        reading("safety:safe_state", saf.i_safe, "safe_state")
        th = visit("thermal", 6, ["run"], [(lambda pg: pg.tabs.setCurrentWidget(pg.res_tab), "17_thermal"),
                                           (lambda pg: pg.tabs.setCurrentWidget(pg.insight), "17a_thermal_reading"),
                                           (lambda pg: pg.tabs.setCurrentWidget(pg.net_tab), "17b_thermal_network"),
                                           (lambda pg: pg.tabs.setCurrentWidget(pg.p_zth), "17c_thermal_zth"),
                                           (lambda pg: pg.tabs.setCurrentWidget(pg.editor_tab), "17d_thermal_editor")])
        check("thermal", th.plot._draw is not None and "s" in th.headline.text(), th.headline.text())
        th.run_cycle()                                   # repeated load (review 6198099 priority 1)
        shot(win, "17e_thermal_repeated_load")
        cy = th.last_cycle or {}
        check("thermal:repeated_load", th.p_cyc._draw is not None and (cy.get("periodic") or {}).get("reached")
              and cy.get("first_limit") is not None and (cy.get("allowed") or {}).get("pulse_duration_s", 0) > 0,
              (cy.get("claim") or {}).get("status"))
        reading("thermal", th.insight, "thermal", "thermal_cycle")
        th.tabs.setCurrentWidget(th.res_tab)
        t_ref = th.last["res"]["request"]["time_to_first_limit_s"]
        th.c_flow.setValue(5.0)
        th.run()
        t_low = th.last["res"]["request"]["time_to_first_limit_s"]
        check("thermal:coolant_flow", isinstance(t_ref, float) and isinstance(t_low, float) and t_low < t_ref,
              f"10 L/min {t_ref} s, 5 L/min {t_low} s")
        th.c_flow.setValue(10.0)
        th.tabs.setCurrentWidget(th.res_tab)
        check("schematics", all(p._draw is not None for p in (saf.s_dis, saf.s_pas, saf.s_ov, saf.s_safe, page.views.overview)))
        pro = visit("protection", 0, ["run"], [(lambda pg: pg.ptabs.setCurrentWidget(pg.p_time), "22_protection_timeline"),
                                               (lambda pg: pg.ptabs.setCurrentWidget(pg.i_prot), "22a_protection_reading"),
                                               (lambda pg: pg.ptabs.setCurrentWidget(pg.p_win.parentWidget()),
                                                "23_protection_window"),
                                               (lambda pg: pg.ptabs.setCurrentWidget(pg.p_loop), "24_protection_loop")])
        prot_ids = [r["id"] for r in (pro.last or {}).get("rows", [])]
        check("protection:ov", pro.last is not None and pro.last["trace"]["protected"] and len(prot_ids) == 9,
              f"{pro.last and pro.last['summary_status']} {prot_ids}")
        pro.preset.setCurrentIndex(1)
        pro.run()
        check("protection:ot", pro.last is not None and pro.last["unit"] == "degC" and pro.last["trace"]["protected"])
        reading("protection", pro.i_prot, "protection")
        pro.tabs.setCurrentIndex(1)
        pro.run_asc()
        asc_ok = pro.last_asc is not None and pro.last_asc["claim"]["status"] == "UNKNOWN" and \
            len(pro.last_asc["requirements"]) == 2
        check("protection:asc", asc_ok, pro.last_asc and pro.last_asc["claim"]["detail"])
        reading("protection:asc", pro.i_asc, "asc")
        pro.asc_tabs.setCurrentIndex(1)
        shot(win, "25_asc_transient")
        pro.asc_tabs.setCurrentWidget(pro.i_asc)
        shot(win, "25a_asc_reading")
        # causal fault simulation: a representative scenario judged on the truth, the reaction candidates from the
        # same initial condition, a campaign whose counterexample is re-run, and the plant's validation (Radau)
        fs = visit("fault_sim", 0, [lambda pg: pg.preset.setCurrentIndex(pg.preset.findData("cs_offset")),
                                    lambda pg: pg._load_preset(), "run"], [])
        ok_v = (fs.last or {}).get("verdicts") or {}
        check("fault:protection_success", fs.last is not None and all(
            x in ("PASS", "NOT_APPLICABLE") for k, x in ok_v.items() if k.startswith("TSR")), str(ok_v))
        fs.preset.setCurrentIndex(fs.preset.findData("res_lost"))
        fs._load_preset()
        fs.run()
        app.processEvents()
        v = (fs.last or {}).get("verdicts") or {}
        acts = [e.get("reaction") for e in (fs.last or {}).get("events", []) if e["kind"] == "actuation"]
        check("fault:wrong_reaction", v.get("TSR-06") == "FAIL" and fs.t_req.rowCount() == 14
              and acts[:1] == ["six_switch_off"], f"{ {k: x for k, x in v.items() if x != 'PASS'} } {acts[:2]}")
        reading("fault_sim", fs.insight, "fault_sim")
        for tab, name in ((fs.p_wave, "25b_fault_waveforms"), (fs.tab_zoom, "25b2_fault_transient"),
                          (fs.tab_timeline, "25c_fault_timeline"),
                          (fs.tab_req, "25d_fault_requirements"), (fs.insight, "25e_fault_reading")):
            fs.tabs.setCurrentWidget(tab)
            if tab is fs.tab_req:
                fs.t_req.selectRow(next(r for r in range(fs.t_req.rowCount()) if fs.t_req.item(r, 0).text() == "TSR-06"))
            shot(win, name)
        # the transient zoom: every extreme read from the simulated samples; the data cursor follows a curve
        rows_t = {fs.t_trans.item(r, 0).text(): r for r in range(fs.t_trans.rowCount())}
        r_v = rows_t.get("V_dc [V]")
        check("fault:transient", fs.p_zoom._draw is not None and fs.t_trans.rowCount() == 5 and r_v is not None
              and float(fs.t_trans.item(r_v, 2).text()) > 850.0, f"{fs.t_trans.rowCount()} rows")
        from matplotlib.backend_bases import MouseEvent
        fs.tabs.setCurrentWidget(fs.tab_zoom)
        app.processEvents()
        ax0 = fs.p_zoom.figure.axes[0]
        x0, x1 = ax0.get_xlim()
        px, py = ax0.transData.transform((0.5 * (x0 + x1), sum(ax0.get_ylim()) / 2))
        MouseEvent("motion_notify_event", fs.p_zoom.canvas, px, py)._process()
        check("plot:data_cursor", fs.p_zoom.cursor.enabled and fs.p_zoom.cursor.live is not None
              and "ms" in fs.p_zoom.readout.text(), fs.p_zoom.readout.text()[:120])
        # the processor lost while rolling: the hardware selection by the DC voltage cycles (a missing requirement)
        from ..insight.fault import bridge_cycling
        fs.preset.setCurrentIndex(fs.preset.findData("hw_vdc_rolling"))
        fs._load_preset()
        fs.run()
        app.processEvents()
        cyc = bridge_cycling(fs.last or {}) or {}
        check("fault:hw_vdc_cycling", cyc.get("changes", 0) >= 6 and 5.0 < (cyc.get("period_ms") or 0) < 12.0
              and 55.0 < cyc.get("v_min_V", 0) and cyc.get("v_max_V", 1e9) < 110.0, str(cyc)[:200])
        fs.tabs.setCurrentWidget(fs.tab_zoom)
        shot(win, "25b3_fault_hw_vdc_cycling")
        fs.preset.setCurrentIndex(fs.preset.findData("res_lost"))
        fs._load_preset()
        from PySide6.QtCore import Qt
        keep = ("policy", "none", "asc_low", "asc_high", "six_switch_off", "torque_zero", "FW2_ASC_LOW", "SOFT_ASC_V")
        for i in range(fs.cand_list.count()):            # the primitive reactions and two declared strategies
            it = fs.cand_list.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in keep else Qt.Unchecked)
        fs.run_compare()
        rows = {r["candidate"]: r for r in (fs.last_cmp or {}).get("rows", [])}
        check("fault:candidates", len(rows) == 8 and rows["policy"]["overall"] == "FAIL"
              and "TSR-06" not in rows["asc_low"]["failing"] and "TSR-06" not in rows["FW2_ASC_LOW"]["failing"],
              str({k: r["overall"] for k, r in rows.items()}))
        reading("fault:candidates", fs.i_cmp, "fault_compare")
        shot(win, "25f_fault_candidates")
        fs.set_axes([{"path": "speed_rpm", "values": [9000, 12000]}])
        fs.cref.setValue(0)
        fs.counterexamples = []
        fs.run_campaign()
        fs.tabs.setCurrentWidget(fs.tab_camp)
        cx = [c for c in fs.counterexamples if c["scenario"]["speed_rpm"] == 12000]
        rerun_ok = False
        if cx:
            fs.t_cx.selectRow(fs.counterexamples.index(cx[0]))
            fs.rerun_selected()
            rerun_ok = "재현" in fs.cx_state.text() or "reproduced" in fs.cx_state.text()
        check("fault:campaign", fs.last_camp is not None and len(fs.last_camp["runs"]) == 2 and bool(cx) and rerun_ok
              and fs.last_camp["scope"]["region"] == "NOT_ESTABLISHED", fs.cx_state.text())
        reading("fault:campaign", fs.i_camp, "fault_campaign")
        shot(win, "25g_fault_campaign")
        fs.run_validation()
        fs.tabs.setCurrentWidget(fs.tab_val)
        val = fs.last_val or {}
        check("fault:validation", val.get("total", 0) >= 10 and val.get("passed") == val.get("total"),
              f"{val.get('passed')}/{val.get('total')}")
        reading("fault:validation", fs.i_val, "fault_validation")
        shot(win, "25h_fault_validation")
        fs.tabs.setCurrentWidget(fs.tab_dep)
        fs.show_dependencies()
        shot(win, "25i_fault_dependencies")
        # the design editor (typed fields, no JSON): a strategy, a debounce time; the variant is what runs
        ed = fs.editor
        fs.top.setCurrentWidget(ed)
        ed.tabs.setCurrentIndex(4)
        ed.l_strat.setCurrentRow(next((i for i in range(ed.l_strat.count()) if ed.l_strat.item(i).text() == "SOFT_ASC_V"), 0))
        shot(win, "25j_fault_strategies")
        ed.tabs.setCurrentIndex(3)
        r_tq = next(i for i, m in enumerate(ed.work["mechanisms"]) if m["id"] == "SM-TQ")
        ed.t_mech.selectRow(r_tq)
        ed.t_mech.item(r_tq, next(j for j, c in enumerate(ed.t_mech.cols) if c.key == "p_time")).setText("25")
        shot(win, "25k_fault_design_editor")
        check("fault:design_variant", fs.scenario().get("overrides") == {"mechanisms.SM-TQ.params.debounce_ms": 25.0},
              fs.design_variant.sig.text())
        # the safety case of the design under study: static review, verification matrix, reading, report
        fs.top.setCurrentWidget(fs.case_tab)
        fs.run_review()
        bad = [f for f in (fs.last_review or {}).get("findings", []) if f["status"] == "INCONSISTENT"]
        check("fault:review_finds", any("SM-TQ" in f["element"] for f in bad), [f["element"] for f in bad])
        ed.revert()
        fs.run_review()
        cnt = (fs.last_review or {}).get("counts") or {}
        check("fault:review", cnt.get("INCONSISTENT") == 0 and cnt.get("MISSING") == 0 and cnt.get("WARNING", 0) >= 1,
              cnt)
        subset = ("step_ok", "false_trip", "cs_offset", "ov_regen", "res_lost", "sw_short")
        for i in range(fs.verif_list.count()):
            it = fs.verif_list.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in subset else Qt.Unchecked)
        fs.run_verification()
        mx = fs.last_verif or {}
        check("fault:verification", [r["key"] for r in mx.get("rows", [])] == ["step_ok", "cs_offset", "ov_regen",
                                                                              "res_lost", "sw_short"]
              and [x["key"] for x in mx.get("skipped", [])] == ["false_trip"]
              and fs.t_matrix.columnCount() == 3 + 5, [r["key"] for r in mx.get("rows", [])])
        reading("fault:safety_case", fs.i_case, "fault_case")
        fs.case_tabs.setCurrentIndex(fs.case_tabs.indexOf(fs.t_matrix.parentWidget()))
        shot(win, "25l_fault_verification_matrix")
        fs.case_tabs.setCurrentWidget(fs.i_case)
        shot(win, "25m_fault_safety_case")
        html = api.fault_report({"overrides": fs.variant(), "matrix": fs._current_matrix(),
                                 "counterexamples": fs.counterexamples}, win.state.project)
        (out / "fault_safety_case.html").write_text(html, encoding="utf-8")
        check("fault:report", "5. 검증 매트릭스" in html and "3. 정적 설계 검토" in html and len(html) > 20_000,
              f"{len(html)} chars")
        for i in range(fs.verif_list.count()):
            fs.verif_list.item(i).setCheckState(Qt.Checked)
        fs.top.setCurrentIndex(0)
        # reference verification: the built-in customer package classified and traced; a proposal verified
        rf = visit("reference", 0, [lambda pg: pg.load_builtin("customer_inverter")], [])
        from PySide6.QtCore import Qt as _Qt
        tops = {rf.tree.topLevelItem(k).data(0, _Qt.UserRole) for k in range(rf.tree.topLevelItemCount())}
        check("reference:builtin_hierarchy", len(rf.package["items"]) >= 370 and {"A-02", "A-03", "PROP-SG-HV"} <= tops
              and rf.t_gaps.rowCount() > 20 and rf.t_props.rowCount() >= 8, f"{len(rf.package['items'])} items")
        rf.tabs.setCurrentWidget(rf.tab_hier)
        shot(win, "25n_reference_hierarchy")
        rf.load_builtin("example")
        rf.profile.setCurrentIndex(rf.profile.findData("illustrative"))
        rf.search.setText("EX-PROP-01")
        rf.run()
        app.processEvents()
        row = next((r for r in (rf.last or {}).get("rows", []) if r["id"] == "EX-PROP-01"), {})
        check("reference:proposal", row.get("verdict") == "PASS" and row.get("proposed"), row.get("verdict"))
        rf.tabs.setCurrentWidget(rf.overview)
        shot(win, "25p_reference_overview")
        rf.tabs.setCurrentWidget(rf.tab_matrix)
        rf.matrix.selectRow(0)
        shot(win, "25o_reference_matrix")
        rf.search.setText("")
        pw = visit("power", 0, ["run_module"], [(lambda pg: pg.mod_tabs.setCurrentIndex(1), "26_power_module"),
                                                (lambda pg: pg.mod_tabs.setCurrentWidget(pg.i_mod), "26a_power_module_reading")])
        check("power:module", pw.last_module is not None and pw.last_module["losses"]["established"]
              and pw.last_module["operating_point"]["Pinv_module_W"] > 0)
        pw.tabs.setCurrentIndex(1)
        pw.run_ripple()
        pw.rip_tabs.setCurrentIndex(1)
        shot(win, "27_power_ripple")
        check("power:ripple", pw.last_ripple is not None and pw.last_ripple["I_cap_rms_A"] > 0
              and pw.last_ripple["claims"]["capacitor_life"]["status"] == "UNKNOWN")
        pw.tabs.setCurrentIndex(2)
        pw.run_life()
        pw.life_tabs.setCurrentIndex(1)
        shot(win, "28_power_life")
        check("power:lifetime", pw.last_life is not None and pw.last_life["damage"]["claim"]["status"] == "UNKNOWN"
              and pw.last_life["damage"]["cycles_counted"] > 0)
        reading("power:module", pw.i_mod, "module")
        reading("power:ripple", pw.i_rip, "ripple")
        reading("power:lifetime", pw.i_life, "lifetime")
        oh = visit("oew_hev", 0, ["run_oew"], [(lambda pg: pg.o_tabs.setCurrentWidget(pg.p_sets), "29_oew_sets"),
                                                (lambda pg: pg.o_tabs.setCurrentWidget(pg.p_opt), "30_oew_point"),
                                                (lambda pg: pg.o_tabs.setCurrentWidget(pg.p_pair), "31_oew_paired"),
                                                (lambda pg: pg.o_tabs.setCurrentWidget(pg.p_sch), "32_oew_circuit")])
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
        oh.h_tabs.setCurrentWidget(oh.p_joint)
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
        reading("oew", oh.i_oew, "oew", "oew_compare")
        reading("hev", oh.i_hev, "hev_joint", "hev_crank", "hev_rejection", "hev_planetary")
        oh.h_tabs.setCurrentWidget(oh.i_hev)
        oh.i_hev.pick.setCurrentIndex(oh.i_hev.pick.findData("hev_joint"))
        shot(win, "36a_hev_reading")
        em = visit("emi", 0, ["run"], [(lambda pg: pg.e_tabs.setCurrentWidget(pg.p_spec), "37_emi_spectrum"),
                                       (lambda pg: pg.e_tabs.setCurrentWidget(pg.i_emi), "37a_emi_reading"),
                                       (lambda pg: pg.e_tabs.setCurrentWidget(pg.p_net), "38_emi_network")])
        check("emi:screening", em.last is not None and em.last["claim"]["status"] == "UNKNOWN"
              and "SCREENING_ONLY" in em.last["claim"]["reasons"], em.last and em.last["claim"]["detail"])
        em.tabs.setCurrentIndex(1)
        em.run_oew()
        em.ocm_tabs.setCurrentIndex(1)
        shot(win, "39_emi_oew_cm")
        cs = (em.last_oew or {}).get("cases", {})
        check("emi:oew_cm", len(cs) == 2 and cs["0"]["u0_rms_V"] < cs["0.5"]["u0_rms_V"]
              and cs["0"]["cm6_rms_V"] > cs["0.5"]["cm6_rms_V"])
        reading("emi", em.i_emi, "emi")
        reading("emi:oew", em.i_ocm, "emi_oew")
        ef = visit("efficiency", 0, ["run_point"], [(lambda pg: pg.e_tabs.setCurrentWidget(pg.i_eff), "43a_efficiency_reading"),
                                                    (lambda pg: pg.e_tabs.setCurrentWidget(pg.p_point), "43_efficiency_point")])
        eb = ((ef.last_point or {}).get("ledger") or {}).get("boundaries", {})
        check("efficiency:five_boundaries", all(eb.get(k, {}).get("status") == "DEFINED" for k in
                                                ("inverter", "motor", "inverter_motor", "reducer", "edrive"))
              and abs(eb["telescoping_residuals"]["edrive"]) < 1e-12, str({k: v.get("status") for k, v in eb.items()
                                                                            if isinstance(v, dict) and "status" in v}))
        ef.run_map()
        shot(win, "44_efficiency_maps")
        st_map = (ef.last_map or {}).get("status")
        check("efficiency:maps", st_map is not None and "FEASIBLE" in set(st_map.ravel()))
        ef.run_mission()
        shot(win, "45_efficiency_mission")
        em_ = (ef.last_mission or {}).get("energy") or {}
        check("efficiency:mission", bool(ef.last_mission and ef.last_mission["delivered"])
              and 0 < (em_.get("eta_traction") or 0) < 1 and 0 < (em_.get("eta_regeneration") or 0) < 1)
        ef.tabs.setCurrentIndex(1)
        ef.run_ab()
        ef.ab_tabs.setCurrentIndex(1)
        shot(win, "46_module_ab")
        ab_rows = (ef.last_ab or {}).get("rows", [])
        check("efficiency:module_ab", bool(ab_rows) and all(r["A"].get("Tj_C") is not None and r["B"].get("Tj_C") is not None
                                                            for r in ab_rows)
              and all(r["compare"]["verdict"] in ("A_LOWER_LOSS", "B_LOWER_LOSS", "UNDECIDED") for r in ab_rows),
              str([r["compare"]["verdict"] for r in ab_rows]))
        reading("efficiency", ef.i_eff, "point", "map", "mission")
        reading("efficiency:module_ab", ef.i_ab, "module_compare")
        pw_ = visit("pwm_driveline", 0, ["run_policies"], [(lambda pg: pg.p_tabs.setCurrentWidget(pg.i_pwm), "47a_pwm_reading"),
                                                           (lambda pg: pg.p_tabs.setCurrentWidget(pg.pl_pol), "47_pwm_policies")])
        pol = {p["policy"]["name"]: p for p in (pw_.last_pol or {}).get("policies", [])}
        check("pwm:policies", pol.get("fixed 10 kHz", {}).get("admissible") and
              not pol.get("thermal fallback 6 kHz", {}).get("admissible", True) and
              (pw_.last_pol or {}).get("best_inverter_energy_among_evaluated") == "light-load 8 kHz",
              str({k: v.get("violations") for k, v in pol.items()}))
        pw_.run_timing()
        shot(win, "48_pwm_timing")
        check("pwm:transition", bool(pw_.last_tim) and pw_.last_tim["transition_shadow"]["ok"]
              and not pw_.last_tim["transition_immediate"]["ok"])
        pw_.run_ripple()
        rr = (pw_.last_rip or {}).get("rows", [])
        check("pwm:ripple", bool(rr) and all(abs(x["ripple_rms_A"] / x["ripple_rms_spectrum_A"] - 1) < 1e-3 for x in rr))
        pw_.run_transients()
        shot(win, "48b_pwm_sampling_transition")
        tv = (pw_.last_trn or {}).get("transition", {}).get("variants", {})
        sc = (pw_.last_trn or {}).get("sampling_curves", {})
        check("pwm:sampling_transition", bool(tv) and tv["bumpless (volts, Ki*Ts remapped)"]["excursion_A"] == 0.0
              and tv["integrator reset"]["excursion_A"] > tv["error-sum integrator, Ki*Ts remapped"]["excursion_A"] > 0
              and sc.get("dc_link_shunt", {}).get("valid_fraction", [1])[0] == 0.0,
              str({k: v.get("excursion_A") for k, v in tv.items()}))
        pw_.tabs.setCurrentIndex(1)
        pw_.d_tabs.setCurrentWidget(pw_.pl_dl)
        pw_.run_driveline()
        shot(win, "49_antijerk_variants")
        dv = (pw_.last_dl or {}).get("variants", {})
        check("antijerk:variants", set(dv) == {"off", "shaping", "feedback", "combined"}
              and dv["off"]["metrics"]["peak_vehicle_jerk_m_s3"] > dv["combined"]["metrics"]["peak_vehicle_jerk_m_s3"]
              and dv["combined"]["status"] == "FEASIBLE",           # shaping + washout feedback meets the targets
              str({k: v["status"] for k, v in dv.items()}))
        pw_.run_stability()
        shot(win, "50_antijerk_stability")
        gr = (pw_.last_stab or {}).get("grid", [])
        check("antijerk:stability", any(not x["stable"] for row in gr for x in row) and any(x["stable"] for row in gr for x in row))
        reading("pwm", pw_.i_pwm, "pwm_policies", "pwm_timing", "pwm_ripple", "pwm_transients")
        reading("driveline", pw_.i_dl, "driveline", "driveline_stability")
        pw_.d_tabs.setCurrentWidget(pw_.i_dl)
        pw_.i_dl.pick.setCurrentIndex(pw_.i_dl.pick.findData("driveline"))
        shot(win, "50a_antijerk_reading")
        mc = visit("machine", 0, ["run_trade"], [(lambda pg: pg.trade_tabs.setCurrentIndex(1), "40_machine_trade"),
                                                 (lambda pg: pg.trade_tabs.setCurrentWidget(pg.i_trade), "40a_machine_trade_reading")])
        mrows = {r["candidate"]: r for r in (mc.last_trade or {}).get("rows", []) if "checks" in r}
        check("machine:trade", "ref" in mrows and "N+10%" in mrows and mrows["ref"]["all_feasible"]
              and mrows["N+10%"]["checks"]["UGO back-EMF"]["status"] == "INFEASIBLE"
              and abs(mrows["N+10%"]["checks"]["UGO back-EMF"]["value"] / mrows["ref"]["checks"]["UGO back-EMF"]["value"]
                      - 1.1) < 1e-9, str({k: v.get("binding") for k, v in mrows.items()}))
        mc.tabs.setCurrentIndex(1)
        mc.run_wind()
        mc.wind_tabs.setCurrentIndex(1)
        shot(win, "41_machine_winding")
        mw = mc.last_wind or {}
        check("machine:winding", abs(mw.get("kw1", 0) - 0.9330127018922193) < 1e-12 and mw.get("balanced")
              and abs((mw.get("compare") or {}).get("k_turns", 0) - 1.25) < 1e-12, mw.get("kw1"))
        n0 = mc.t_cand.rowCount()
        mc.send_candidate()
        check("machine:k_turns_to_trade", mc.t_cand.rowCount() == n0 + 1 and mc.tabs.currentIndex() == 0)
        mc.tabs.setCurrentIndex(2)
        mc.run_size()
        mc.size_tabs.setCurrentIndex(1)
        shot(win, "42_machine_sizing")
        check("machine:sizing", mc.last_size is not None and
              all(abs(r["check_T_Nm"] - mc.last_size["T_Nm"]) < 1e-9 for r in mc.last_size["rows"]))
        reading("machine", mc.i_trade, "machine_trade")
        reading("machine:winding", mc.i_wind, "winding")
        reading("machine:sizing", mc.i_size, "concept_sizing")
        visit("model", 7, [], [(None, "18_model")])
        # project data package (R2): identity, consistency, every result names its product data, a revision switch
        # reloads the pages and marks older results stale
        pj = visit("project", 0, [], [(None, "18b_project")])
        check("project:identity", pj.last_check["status"] == "OK" and win.state.project.id == "SYNTH-TRACTION-200KW",
              pj.last_check["status"])
        uses = {k: u for pg in win.usages.values() for k, u in pg.items()}
        check("project:usage", len(uses) >= 20 and all(u.get("project_digest") for u in uses.values())
              and uses.get("emi", {}).get("local_edits") == [], str(sorted(uses)))
        import copy as _copy
        from ..examples import SYNTHETIC_PROJECT
        from ..project import Project, builtin_project
        d = _copy.deepcopy(SYNTHETIC_PROJECT)
        d["project"]["revision"] = "SELFTEST"
        d["sections"]["controller"]["data"]["deadtime_us"] = 1.2
        win.state.set_project(Project.from_dict(d))
        emi_pg = visit("emi", 0, [], [(None, "18c_project_stale")])
        check("project:switch", emi_pg.e_td.value() == 1.2 and win.banners["emi"].property("state") == "stale"
              and win.banners["protection"].property("state") != "stale", win.banners["emi"].text())
        win.state.set_project(builtin_project())
        check("project:restore", win.banners["emi"].property("state") != "stale" and emi_pg.e_td.value() == 1.5)
        # datasheet import (the example spec: WebPlotDigitizer + long-format CSV) -> the project's module section
        from .pages.model import examples_dir
        exd = examples_dir()
        ds_path = exd / "datasheets" / "module_example.json" if exd else None
        if ds_path is not None and ds_path.is_file():
            dlg = pj.import_datasheet(str(ds_path))
            dlg.resize(1100, 720)
            dlg.show()
            app.processEvents()
            dlg.grab().save(str(out / "18d_datasheet_import.png"))
            dlg.apply()
            prov = win.state.project.sections["module"].provenance
            pw_pg = visit("power", 0, [], [])
            check("datasheet:module", win.state.project.modified and prov["origin"] == "supplier"
                  and "EXM-750-820" in pw_pg.m_src.text() and not prov["qualified"], prov.get("source"))
        else:
            check("datasheet:module", False, "examples/datasheets not found")
        win.state.set_project(builtin_project())
        # datasheet VALUE ENTRY (representative values typed by hand): each form, filled with its example, builds the
        # same section as the example spec file; the motor then replaces the project's drive
        if exd is not None and (exd / "datasheets" / "motor_example.json").is_file():
            from .. import datasheet as DS
            from .datasheet_entry_dialog import EXAMPLES
            ent = pj.enter_datasheet("module")
            ent.resize(1320, 860)
            ent.show()
            same = {}
            for kind, name in EXAMPLES.items():
                ent.fill_example(kind)
                spec, base_dir = DS.load_spec(exd / "datasheets" / name)
                ref, rres = DS.apply(win.state.project, spec, base_dir)
                same[kind] = (ent.new_project is not None and ent.new_project.sections[ent.result["section"]].digest
                              == ref.sections[rres["section"]].digest)
                if kind in ("module", "motor"):
                    app.processEvents()
                    ent.grab().save(str(out / f"18f_datasheet_entry_{kind}.png"))
            check("datasheet:entry_forms", all(same.values()), same)
            ent.set_kind("motor")
            ok = ent.apply()
            prov = win.state.project.sections["drive"].provenance
            mdl = visit("model", 7, [], [])
            check("datasheet:entry_motor", ok and prov["origin"] == "supplier" and win.state.drive.drive_id == "EXMOT-200"
                  and "EXMOT-200" in mdl.title.text(), win.state.drive.drive_id)
            win.state.set_project(builtin_project())
        else:
            check("datasheet:entry_forms", False, "examples/datasheets not found")
        # MathWorks transfer package: export of the active project; nothing has run on a target yet
        mw_dir = out / "mathworks_package"
        if mw_dir.exists():
            import shutil
            shutil.rmtree(mw_dir)
        mw = pj.mathworks_package(str(mw_dir))
        mw.show()
        app.processEvents()
        mw.grab().save(str(out / "18e_mathworks_package.png"))
        v = mw.verification or {}
        check("mathworks:package", v.get("package_check") == "PASS" and v.get("parity") == "NOT_RUN"
              and v.get("model_generation") == "NOT_RUN" and not v.get("linked_as_current_evidence")
              and (mw_dir / "matlab" / "+twb" / "runAll.m").is_file(), v.get("problems"))
        mw.close()
        html = win.page_guide_html()
        check("guide", all(PAGE_INFO[k]() in html for k in PAGE_INFO) and win.current_page() in PAGE_INFO)
        vv = visit("verification", 8, ["run"], [(None, "19_verification")])
        check("acceptance", "PASS" in vv.summary.text() and "MISMATCH" not in vv.summary.text(), vv.summary.text())
        xp = vv.export_exchange(str(out / "twb_exchange.json"))
        xj = json.loads(Path(xp).read_text(encoding="utf-8")) if xp else {}
        check("exchange:package", xj.get("schema") == "twb-exchange/1" and len(xj.get("fixtures", {})) >= 12)
        # flux-map drive: model switch + a decision on the D2 test drive
        win.state.set_drive({"builtin": "MANUFACTURED_FLUX_MAP_TEST_DRIVE"}, "flux map", "builtin")
        win.show_page("decision")
        page.req_id.setText("FM-20")
        page.torque.setValue(20.0)
        page.speed.setValue(3000.0)
        page.vdc.setValue(600.0)
        page.range_on.setChecked(False)
        page.dur_none.setChecked(True)
        for w in (page.an_dom, page.an_relax, page.an_size_v, page.an_size_i):
            w.setChecked(False)
        steps = []

        def step_seen(key, frac, msg):
            if key == "decision":
                steps.append((frac, msg))
        win.runner.progress.connect(step_seen)
        page.run()
        win.runner.progress.disconnect(step_seen)
        got = page.result["record"]["verdict"]["verdict"] if page.result else None
        check("decision:flux_map", got in ("PASS", "FAIL", "UNKNOWN") and page.result["record"]["model"]["fidelity"] == "D2", got)
        # the long flux-map scan says where it is (A5): engine steps reach the status bar, the bar never goes back
        from ..plots.labels import progress_label
        scan = [m for _f, m in steps if progress_label("torque capability") in m]
        fr = [f for f, _m in steps]
        check("progress:engine_steps", len(scan) >= 10 and all(b >= a for a, b in zip(fr, fr[1:])),
              f"{len(steps)} messages, {len(scan)} in the capability scan; e.g. {scan[len(scan) // 2] if scan else '-'}")
        page.tabs.setCurrentWidget(page.views)
        shot(win, "20_flux_map_decision")
        win.set_theme("dark", persist=False)
        shot(win, "21_dark_theme")
        # workspace (J1): every page's inputs, page data and the project written to a file and read back in place
        from . import workspace as WS
        ws_file = WS.save(WS.build(win), out / ("selftest" + WS.SUFFIX))
        ws_back = WS.load(ws_file)
        ws_problems = WS.apply(win, ws_back)
        check("workspace:roundtrip", not ws_problems and WS.content(WS.build(win)) == WS.content(ws_back),
              "; ".join(ws_problems[:5]) or f"{len(ws_back['pages'])} pages, {ws_file.stat().st_size // 1024} KB")
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
