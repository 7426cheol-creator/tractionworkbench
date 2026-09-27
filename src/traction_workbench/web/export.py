"""Static snapshot of the web UI: one self-contained HTML file with precomputed
example results (no server needed; free-form inputs require ``twb serve``)."""

from __future__ import annotations

import json
from pathlib import Path

from .. import service as S
from ..decision import _jsonable
from . import api

STATIC = Path(__file__).resolve().parent / "static"


def _js_num(x) -> str:
    x = float(x)
    return str(int(x)) if x.is_integer() else repr(x)


def build_static_data(progress=print) -> dict:
    data = {"info": api.info({}), "evaluate": {}, "maps": {}, "curves": {}, "fixed": {}}
    vdcs = {450.0, 600.0}
    for p in api.PRESETS:
        progress(f"  evaluating preset {p['key']}")
        rec = api.evaluate({"requirement": p["req"], "analyses": p.get("analyses", {})})
        data["evaluate"][p["key"]] = rec
        conds = rec["conditions"]
        pick = next((c for c in conds if c["requirement_claim_at_this_condition"]["status"] != "FEASIBLE"), conds[0])
        sc = pick["scenario"]
        n, v, t = sc["speed_rpm_mechanical"], sc["Vdc_V_inverter_dc_terminal"], rec["requirement"]["target_Nm"]
        vdcs.add(float(v))
        key = f"{_js_num(n)}|{_js_num(v)}|{_js_num(t)}"
        data["maps"][key] = api.idiq({"speed_rpm": n, "Vdc_V": v, "torque_Nm": t})
    for v in sorted(vdcs):
        progress(f"  capability curve at {v:g} V")
        data["curves"][_js_num(v)] = api.curve({"Vdc_V": v})
    progress("  acceptance, screening examples")
    data["acceptance"] = S.acceptance_summary()
    fx = data["fixed"]
    fx["timing"] = api.timing(api.EXAMPLE_TIMING)
    fx["discharge"] = api.discharge({"C_uF": 500, "V0_V": 600, "Vf_V": 60, "t_target_s": 2, "speed_rpm": 0})
    fx["overvoltage"] = api.overvoltage({"C_uF": 500, "V1_V": 600, "V_limit_V": 850, "speed_rpm": 12000,
                                         "torque_Nm": -80, "reaction_time_ms": 2, "profile": "constant"})
    fx["safe_state"] = api.safe_state({"speed_rpm": 12000, "Vdc_V": 600, "hv_state": "battery_connected",
                                       "device_voltage_rating_V": 1200,
                                       "rules": [{"rule_id": "PRJ-LV-FW", "when": {"Vdc_below_V": 60},
                                                  "require": "FREEWHEEL", "basis": "project/customer rule (not physics)"}]})
    fx["thermal"] = api.thermal({"speed_rpm": 3000, "Vdc_V": 600, "coolant_temp_C": 65, "torque_Nm": 450,
                                 "duration_s": 10})
    fx["sizing"] = api.sizing({"torque_Nm": 150, "speed_rpm": 12000, "Vdc_V": 450, "parameter": "Vdc_V",
                               "range": [400, 800], "samples": 41})
    fx["dominance"] = api.dominance({"speed_rpm": 12000, "Vdc_V": 500, "direction": 1})
    return _jsonable(data)


def build_static_html(out: Path, progress=print) -> Path:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    data = build_static_data(progress)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    def swap(text, tag, repl):
        a, b = f"<!--{tag}-->", f"<!--/{tag}-->"
        i, j = text.index(a), text.index(b) + len(b)
        return text[:i] + repl + text[j:]

    html = swap(html, "STYLE", f"<style>\n{css}\n</style>")
    html = swap(html, "DATA", f"<script>window.TWB_STATIC = {payload};</script>")
    html = swap(html, "SCRIPT", f"<script>\n{js}\n</script>")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
