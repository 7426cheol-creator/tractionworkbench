"""Safe-state feasibility over the operating domain: which reaction reaches the physical safe state where.

For every cell of speed x DC voltage (x temperature, direction, HV state) and every candidate reaction, one causal
simulation: the machine turns at the cell's speed (held by the vehicle), the reaction is commanded at a fixed instant
and the physical safe state (C1..C4 of ``safestate``) is judged on the second half of the horizon - the steady
behaviour the reaction settles to; the transient peaks of the first part are reported with it.  A cell is ``both`` /
``fw_only`` / ``asc_only`` / ``neither`` (no candidate reaches the safe state there) or ``undecided`` (a condition
cannot be judged: an undeclared C4 limit, the run left the model).  The map is evidence for a reaction-selection
table, not the table itself: the selection rule and its hysteresis stay declared project data.
"""

from __future__ import annotations

from .safestate import FAIL, PASS, judge_safe_state

FW, ASC = "six_switch_off", "asc_low"


def _classify(cell: dict) -> str:
    v = {r: x["verdict"] for r, x in cell["reactions"].items()}
    fw = [r for r in v if r in ("six_switch_off",)]
    asc = [r for r in v if r.startswith("asc") or r.startswith("seq_asc")]
    if any(x not in (PASS, FAIL) for x in v.values()):
        return "undecided"
    fw_ok = any(v[r] == PASS for r in fw)
    asc_ok = any(v[r] == PASS for r in asc)
    other_ok = any(v[r] == PASS for r in v if r not in fw and r not in asc)
    if fw_ok and asc_ok:
        return "both"
    if fw_ok:
        return "fw_only"
    if asc_ok:
        return "asc_only"
    return "other_only" if other_ok else "neither"


def feasibility_map(product, speeds_rpm, vdcs_V, reactions=(FW, ASC), hv_state: str = "connected",
                    torque_tol_Nm: float = 5.0, power_tol_W: float = 500.0, t_min_Nm=None, t_max_Nm=None,
                    horizon_ms: float = 40.0, t_cmd_ms: float = 2.0, temperatures=(None,), progress=None) -> dict:
    from .campaign import run_one
    speeds = [float(s) for s in (speeds_rpm or (3000.0, 6000.0, 9000.0, 12000.0))]
    vdcs = [float(v) for v in (vdcs_V or (400.0, 600.0))]
    cells = []
    n_all = len(speeds) * len(vdcs) * len(temperatures) * len(reactions)
    k = 0
    for temp in temperatures:
        for vdc in vdcs:
            for sp in speeds:
                cell = {"speed_rpm": sp, "vdc_V": vdc, "temperature_C": temp, "reactions": {}}
                for r in reactions:
                    k += 1
                    if progress is not None:
                        progress(k, n_all, f"feasibility {sp:g} rpm {vdc:g} V {r}")
                    faults = [{"kind": "command_reaction", "t_ms": t_cmd_ms, "params": {"reaction": r}}]
                    if hv_state == "disconnected":
                        faults.insert(0, {"kind": "battery_disconnect", "t_ms": t_cmd_ms})
                    sc = {"speed_rpm": sp, "Voc_V": vdc, "torque_Nm": 0.0, "horizon_ms": horizon_ms,
                          "faults": faults, "protection": False}
                    if temp is not None:
                        sc.update(magnet_temp_C=temp, winding_temp_C=temp)
                    try:
                        out = run_one(product, sc, keep_trace=True)
                    except Exception as exc:                  # noqa: BLE001 - a cell outside the model is recorded
                        cell["reactions"][r] = {"verdict": "UNKNOWN", "reason": f"{type(exc).__name__}: {exc}"}
                        continue
                    res = out["result"]
                    j = judge_safe_state(res, t_cmd_ms * 1e-3, torque_tol_Nm=torque_tol_Nm, power_tol_W=power_tol_W,
                                         t_min_Nm=t_min_Nm, t_max_Nm=t_max_Nm,
                                         transition_s=0.5 * (horizon_ms - t_cmd_ms) * 1e-3)
                    s = res.summary
                    cell["reactions"][r] = {
                        "verdict": j["verdict"], "reasons": j["reasons"],
                        "failed": [c for c, i in j["conditions"].items() if i.get("verdict") == FAIL],
                        "i_peak_A": s["i_phase_peak_A"], "T_brake_peak_Nm": s["T_brake_max_Nm"],
                        "v_dc_max_V": s["v_dc_max_V"], "status": res.status}
                cell["class"] = _classify(cell)
                cells.append(cell)
    counts: dict = {}
    for c in cells:
        counts[c["class"]] = counts.get(c["class"], 0) + 1
    return {"cells": cells, "counts": counts, "speeds_rpm": speeds, "vdcs_V": vdcs, "reactions": list(reactions),
            "hv_state": hv_state, "basis": f"causal runs of {horizon_ms:g} ms, reaction at {t_cmd_ms:g} ms, judged on the "
                                           f"second half (steady behaviour); T_tol {torque_tol_Nm:g} N*m, P_tol "
                                           f"{power_tol_W:g} W, C4 limits {t_min_Nm} .. {t_max_Nm} N*m"}
