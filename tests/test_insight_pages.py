"""The engineering readings of every page, driven through the pages' real code paths.

Each page calculation hands its result to a reading (``InsightPanel.read``).  For every reading recorded here:

* it is made without failing, in Korean and in English, and says something (headline and sections);
* no identifier (claim, constraint, reason code, snake_case field, True/False) reaches the text a person reads;
* the English reading has no Korean left in it;
* reading a result never changes it;

and selected numbers are checked against the result they read.  The result tables next to the readings name claims
by their display names (the code stays in the tooltip), and an editable table too narrow for its headers scrolls
instead of cutting them.
"""

import copy
import json
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from traction_workbench.i18n import language, set_language  # noqa: E402
from traction_workbench.insight import num  # noqa: E402

CODE = re.compile(r"\b(?:[a-z]+_[a-z0-9_]+|[A-Z]{3,}_[A-Z_]+|True|False)\b")
# names in their own right inside a reading (formulas, symbols); anything else snake_case is an identifier leak
ALLOWED = {"eta_mot", "eta_regen", "P_shaft", "P_dc", "iq_min", "id_A", "iq_A", "R_s", "T_e", "P_cu", "P_inv", "P_ac",
           "f_e", "v_d", "I_dc", "P_ESR", "x_N", "x_lim", "t_req", "V_f", "V_nom", "V_max", "V_lim", "P_in", "E_in",
           "E_after", "V_tr", "R_p", "R_a", "T_j", "L_hf", "L_d", "L_q", "k_t", "k_N", "L_q,diff", "u0", "i0", "e0",
           "I_fund", "v_cm", "P_mesh", "P_drag", "T_em", "T_shaft", "E_cap", "T_ntc", "T_coolant", "P_hot", "P_o",
           "P_m", "P_em", "V_r", "N_eff", "t_confirm", "t_action", "t_limit", "t_xcross", "c_p", "T_mag"}
_CAPTURED: list = []


def _norm(v):
    return json.dumps(v, sort_keys=True, default=str)


@pytest.fixture(scope="module")
def win():
    import matplotlib
    matplotlib.use("QtAgg")
    from PySide6.QtWidgets import QApplication
    before = language()
    set_language("ko")
    app = QApplication.instance() or QApplication([])
    app.setProperty("twb_selftest", True)
    from traction_workbench.desktop import widgets
    from traction_workbench.desktop.main_window import MainWindow
    from traction_workbench.desktop.worker import TaskRunner
    TaskRunner.synchronous = True
    orig = widgets.InsightPanel.read

    def read(self, key, title, fn, *args):
        _CAPTURED.append((key, fn, copy.deepcopy(args), _norm(args)))
        return orig(self, key, title, fn, *args)
    widgets.InsightPanel.read = read
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    app.processEvents()
    runs = {"explorer": ["run"], "trajectory": ["run"], "design": ["run1", "run2"],
            "safety": ["run_ftti", "run_discharge", "run_passive", "run_overvoltage", "run_safe"],
            "thermal": ["run", "run_cycle"], "protection": ["run", "run_asc"],
            "power": ["run_module", "run_ripple", "run_life"],
            "oew_hev": ["run_oew", "run_compare", "run_joint", "run_crank", "run_rej", "run_planetary"],
            "emi": ["run", "run_oew"], "efficiency": ["run_point", "run_map", "run_mission", "run_ab"],
            "pwm_driveline": ["run_policies", "run_timing", "run_ripple", "run_transients", "run_driveline",
                              "run_stability"],
            "machine": ["run_trade", "run_wind", "run_size"]}
    for page, actions in runs.items():
        w.show_page(page)
        for a in actions:
            getattr(w.pages[page], a)()
            app.processEvents()
    yield w
    widgets.InsightPanel.read = orig
    w.close()
    set_language(before)


def _texts(ins) -> str:
    return "\n".join(ins.plain_lines())


def test_every_page_reading_is_made_and_readable_in_both_languages(win):
    keys = {k for k, *_ in _CAPTURED}
    expected = {"explorer", "trajectory", "sizing", "bottleneck", "ftti", "discharge", "passive", "overvoltage",
                "safe_state", "thermal", "thermal_cycle", "protection", "asc", "module", "ripple", "lifetime", "oew",
                "oew_compare", "hev_joint", "hev_crank", "hev_rejection", "hev_planetary", "emi", "emi_oew", "point",
                "map", "mission", "module_compare", "pwm_policies", "pwm_timing", "pwm_ripple", "pwm_transients",
                "driveline", "driveline_stability", "machine_trade", "winding", "concept_sizing"}
    assert expected <= keys, sorted(expected - keys)
    problems = []
    for key, fn, args, before in _CAPTURED:
        # names the user declared (an FTTI chain's items and events) are data, shown as written
        declared = set()
        if key == "ftti":
            res = args[0]
            declared = {it["id"] for it in res.get("items") or []} | set(res.get("events") or [])
        for lang in ("ko", "en"):
            set_language(lang)
            try:
                ins = fn(*copy.deepcopy(args))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{key}/{lang}: {type(exc).__name__}: {exc}")
                continue
            txt = _texts(ins)
            if not ins.headline or not ins.sections:
                problems.append(f"{key}/{lang}: empty reading")
            bad = sorted({m for m in CODE.findall(txt) if m not in ALLOWED | declared})
            if bad:
                problems.append(f"{key}/{lang}: identifiers {bad[:8]}")
            if lang == "en":
                kor = [ln for ln in txt.splitlines() if re.search(r"[가-힣]", ln)]
                if kor:
                    problems.append(f"{key}/en: Korean left: {kor[0][:120]}")
            assert ins.html() and ins.markdown()
        set_language("ko")
        fn(*args)
        assert _norm(args) == before, f"{key}: the reading changed its result"
    assert not problems, "\n".join(problems)


def _reading(key):
    fn, args = next((f, a) for k, f, a, _ in reversed(_CAPTURED) if k == key)
    return fn(*copy.deepcopy(args)), args


def test_safety_readings_state_the_results_numbers(win):
    set_language("ko")
    ins, (res,) = _reading("ftti")
    assert f"{num(res['worst_s'] * 1e3)} ms" in ins.headline and f"{num(res['margin_s'] * 1e3)} ms" in ins.headline
    path = [it for it in res["items"] if it["id"] in res["chosen_path"]]
    top = max(path, key=lambda it: it["worst_s"])
    assert any(top["id"] in it.text and "가장 큰 항목" in it.text for s in ins.sections for it in s.items)
    ins, (res,) = _reading("overvoltage")
    txt = _texts(ins)
    assert f"{num(res['V_peak_V'])} V" in ins.headline and f"{num(res['energy_headroom_J'])} J" in txt
    # the capacitance that would absorb the declared reaction: C = 2 E_in / (V_lim^2 - V1^2), an identity of the result
    c_need = 2 * res["energy_in_J"] / (res["V_limit_V"] ** 2 - res["V1_V"] ** 2) * 1e6
    assert f"{num(c_need)} µF" in txt
    ins, (res,) = _reading("passive")
    txt = _texts(ins)
    assert f"{num(res['loss_time_product_Ws'])} W·s" in txt and f"{num(res['R_min_ohm'] / 1e3)} kΩ" in txt


def test_efficiency_and_mission_readings_add_up(win):
    set_language("ko")
    ins, (res,) = _reading("point")
    b = res["ledger"]["boundaries"]
    assert f"{100 * b['edrive']['eta']:.2f} %" in ins.headline
    ins, (res,) = _reading("mission")
    txt = _texts(ins)
    seg = max((s for s in res["segments"] if s.get("loss_known_W")), key=lambda s: s["loss_known_W"] * s["duration_s"])
    assert f"{num(seg['loss_known_W'] * seg['duration_s'] / 3600)} Wh" in txt   # the largest loss-energy segment
    first = next(s for s in ins.sections if s.title.startswith("구간별")).items[0]
    assert f"{num(seg['speed_rpm'])} rpm" in first.text


def test_control_readings_name_the_tightest_margin_and_the_trade(win):
    set_language("ko")
    ins, (res,) = _reading("pwm_policies")
    txt = _texts(ins)
    assert "가장 빠듯함" in txt
    bad = [p for p in res["policies"] if not p["admissible"]]
    assert all(p["policy"]["name"] in ins.headline for p in bad)
    ins, (res,) = _reading("driveline")
    ok = [n for n, v in res["variants"].items() if v["status"] == "FEASIBLE"]
    assert ins.verdict == ("PASS" if ok else "FAIL")


def test_page_tables_name_claims_not_codes(win):
    from traction_workbench.plots.labels import claim_label
    p = win.pages
    set_language("ko")
    p["efficiency"].run_point()                              # its table shows the last calculation's rows
    tables = {"explorer": p["explorer"].claims, "oew": p["oew_hev"].t_oew,
              "efficiency": next(t for t in p["efficiency"].findChildren(type(p["explorer"].claims))
                                 if t.rowCount() and "existence" in (t.item(0, 0).toolTip() or ""))}
    for name, t in tables.items():
        coded = [t.item(r, 0) for r in range(t.rowCount()) if t.item(r, 0).toolTip() != t.item(r, 0).text()]
        assert coded, name                                   # the claim rows are there, with their codes as tooltips
        for it in coded:
            assert not CODE.search(it.text()), (name, it.text())
            if claim_label(it.toolTip()) != it.toolTip():    # a claim code: its display name, never another text
                assert it.text() == claim_label(it.toolTip()), (name, it.text())
        assert any(claim_label(it.toolTip()) != it.toolTip() for it in coded), name


def test_a_narrow_editable_table_scrolls_instead_of_cutting_headers(win):
    from PySide6.QtWidgets import QApplication, QHeaderView
    from traction_workbench.desktop.pages.pwm_driveline import RULE_HEAD
    from traction_workbench.desktop.widgets import NumTable
    t = NumTable(RULE_HEAD, [["light-load 8 kHz", "hot module (protective)", 8.0]])
    h = t.horizontalHeader()
    t.show()
    try:
        for width in (300, 700, 1100, 1600, 2600):               # scrolls, sized to the need, or equal shares
            t.resize(width, 200)
            QApplication.processEvents()
            for j in range(t.columnCount()):                     # every header and value whole, at every width
                assert h.sectionSize(j) >= max(h.sectionSizeHint(j), t.sizeHintForColumn(j)), (width, RULE_HEAD[j])
        assert h.sectionResizeMode(0) == QHeaderView.Stretch     # with room for everything the columns share it
    finally:
        t.close()
