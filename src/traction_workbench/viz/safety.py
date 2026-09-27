"""Time and speed curves for the screening extensions (same equations as ``extensions``)."""

from __future__ import annotations

import math

import numpy as np

from ..extensions.dclink import back_emf_ll_peak, speed_for_back_emf
from ..extensions.safe_state import asc_steady_state
from ..extensions.thermal import ThermalModel
from ..models.components import DriveModel


def discharge_curve(res: dict, samples: int = 400) -> dict:
    """RC discharge V(t) = V0 exp(-t/RC) from an ``active_discharge`` result."""
    tau = res["tau_s"]
    t_end = max(res["t_target_s"], res["t_reach_s"]) * 1.25
    t = np.linspace(0.0, t_end, samples)
    v = res["V0_V"] * np.exp(-t / tau)
    out = {"t_s": t, "V": v, "i_A": v / res["R_used_ohm"], "p_W": v * v / res["R_used_ohm"]}
    floor = res.get("back_emf_ll_peak_V")
    if floor:
        out["V_with_back_emf"] = np.maximum(v, floor)
    return out


def overvoltage_curve(res: dict, samples: int = 400) -> dict:
    """Link voltage after battery disconnect: 1/2 C (V^2 - V1^2) = E_in(t)."""
    C, V1, P = res["C_F"], res["V1_V"], res["P_in_W"]
    tr = res.get("reaction_time_s")
    t_lim = res["time_to_limit_constant_power_s"]
    t_end = 1.3 * max(tr or 0.0, t_lim, res["max_reaction_time_s"])
    t = np.linspace(0.0, t_end, samples)
    if tr is None:
        e = P * t                                  # power never removed: bounding line
    elif res["profile"] == "constant":
        e = P * np.minimum(t, tr)
    else:
        tt = np.minimum(t, tr)
        e = P * (tt - tt * tt / (2.0 * tr))
    v = np.sqrt(V1 * V1 + 2.0 * e / C)
    return {"t_s": t, "V": v, "E_J": e, "V_unlimited": np.sqrt(V1 * V1 + 2.0 * P * t / C)}


def asc_vs_speed(drive: DriveModel, Vdc_V: float, speeds=None, n: int = 121) -> dict:
    if speeds is None:
        hi = max(abs(drive.domain.speed_rpm[0]), abs(drive.domain.speed_rpm[1]))
        speeds = np.linspace(0.0, hi, n)
    speeds = np.asarray(speeds, float)
    cols = {k: np.full(speeds.size, np.nan) for k in ("i_peak_A", "id_A", "iq_A", "Tshaft_Nm", "Te_Nm",
                                                        "copper_loss_W", "back_emf_ll_peak_V")}
    for i, s in enumerate(speeds):
        if s > 0:
            r = asc_steady_state(drive, float(s), float(Vdc_V))
            if r.get("evaluable"):
                for k in ("i_peak_A", "id_A", "iq_A", "Tshaft_Nm", "Te_Nm", "copper_loss_W"):
                    cols[k][i] = r[k]
        v = back_emf_ll_peak(drive, float(s))
        if v is not None:
            cols["back_emf_ll_peak_V"][i] = v
    return {"speeds": speeds, **cols, "Vdc_V": float(Vdc_V),
            "ucg_onset_rpm": speed_for_back_emf(drive, float(Vdc_V)),
            "current_limit_A": drive.inverter.current_limit_A_peak}


def ftti_timeline(t: dict) -> dict:
    """Worst- and best-case event times along the counted (non-overlapping) chain items."""
    events = list(t["events"])
    order = {e: i for i, e in enumerate(events)}
    worst = {events[0]: 0.0}
    best = {events[0]: 0.0}
    for it in sorted((x for x in t["items"] if x["counted"]), key=lambda x: order[x["from"]]):
        if it["from"] in worst:
            worst[it["to"]] = worst[it["from"]] + (it["worst_s"] or 0.0)
            best[it["to"]] = best[it["from"]] + (it["min_s"] or 0.0)
    bars = []
    for it in t["items"]:
        start_w = worst.get(it["from"])
        start_b = best.get(it["from"])
        bars.append({"id": it["id"], "owner": it.get("owner", ""), "from": it["from"], "to": it["to"],
                     "start_worst_s": start_w, "start_best_s": start_b, "min_s": it.get("min_s"),
                     "nom_s": it.get("nom_s"), "max_s": it.get("max_s"), "worst_s": it.get("worst_s"),
                     "counted": it["counted"]})
    dup = set()
    for d in t.get("duplicate_budgets", []):
        dup.update(d["items"])
    return {"events": events, "worst_event_s": worst, "best_event_s": best, "bars": bars, "duplicates": dup,
            "ftti_s": t["ftti_s"], "worst_s": t["worst_s"], "best_s": t["best_s"],
            "budget_checks": t.get("budget_checks", [])}


def thermal_curves(model: ThermalModel, node_rows: list[dict], coolant_C: float, t_end_s: float,
                   samples: int = 500) -> dict:
    """Node temperatures T(t) = T_coolant + P * Zth(t) for the constant-loss thermal screening."""
    t = np.concatenate([[0.0], np.geomspace(max(t_end_s, 1e-3) * 1e-5, max(t_end_s, 1e-3), samples - 1)])
    curves = []
    by_id = {n.node_id: n for n in model.nodes}
    for row in node_rows:
        nd = by_id.get(row["node"])
        if nd is None:
            continue
        z = nd.network.zth_array(t)
        ref = float(row.get("fluid_reference_C", coolant_C))
        curves.append({"node": nd.node_id, "T_C": ref + row["power_W"] * z, "limit_C": nd.limit_C,
                       "power_W": row["power_W"], "time_to_limit_s": row["time_to_limit_s"], "fluid_reference_C": ref,
                       "station": row.get("station")})
    return {"t_s": t, "curves": curves, "coolant_C": coolant_C}


def zth_curves(model: ThermalModel, t_min: float = 1e-4, t_max: float = 1e4, samples: int = 400) -> dict:
    """Junction(node)-to-fluid thermal impedance Z_th(t) of every node (as used in the calculation)."""
    t = np.geomspace(t_min, t_max, samples)
    return {"t_s": t, "nodes": [{"node": nd.node_id, "zth_K_per_W": nd.network.zth_array(t),
                                 "R_K_per_W": list(nd.network.R_K_per_W), "tau_s": list(nd.network.tau_s)}
                                for nd in model.nodes]}


def availability_curve(av: dict) -> dict:
    rows = [r for r in av.get("rows", []) if r.get("torque_Nm") is not None]
    dur = np.array([r["duration_s"] for r in rows], float)
    tq = np.array([r["torque_Nm"] for r in rows], float)
    finite = np.isfinite(dur)
    return {"duration_s": dur[finite], "torque_Nm": tq[finite],
            "continuous_Nm": float(tq[~finite][0]) if (~finite).any() else None,
            "limited_by": [r["limited_by"] for r, f in zip(rows, finite) if f],
            "static_Nm": av.get("static_capability_Nm")}


def headline_availability(av: dict, T_request: float | None = None) -> dict | None:
    """'X N*m for t s, then Y N*m' style summary from the availability table (screening estimate)."""
    c = availability_curve(av)
    if c["duration_s"].size == 0:
        return None
    peak = c["static_Nm"]
    t_peak = None
    for d, tq in zip(c["duration_s"], c["torque_Nm"]):
        if tq >= peak - 1e-6 * max(1.0, abs(peak)):
            t_peak = float(d)
    return {"peak_Nm": peak, "peak_holds_at_least_s": t_peak, "continuous_Nm": c["continuous_Nm"]}


def fmt_seconds(s: float | None) -> str:
    if s is None:
        return "-"
    if math.isinf(s):
        return "continuous"
    if s < 1e-3:
        return f"{s * 1e6:.3g} us"
    if s < 1:
        return f"{s * 1e3:.3g} ms"
    return f"{s:.3g} s"


def passive_curves(res: dict, samples: int = 400) -> dict:
    """Bleeder discharge V(t) (and with the active resistor in parallel, if given)."""
    t_end = 1.25 * max(res["t_target_s"], res["t_reach_s"])
    t = np.linspace(0.0, t_end, samples)
    out = {"t_s": t, "V": res["V0_V"] * np.exp(-t / res["tau_s"])}
    wa = res.get("with_active")
    if wa:
        out["V_with_active"] = res["V0_V"] * np.exp(-t / wa["tau_s"])
    floor = res.get("back_emf_ll_peak_V")
    if floor:
        out["V_with_back_emf"] = np.maximum(out["V"], floor)
    return out


def passive_window(res: dict, samples: int = 300) -> dict:
    """Design window of the bleeder: discharge time and continuous loss versus R_p."""
    C, ln = res["C_F"], math.log(res["V0_V"] / res["Vf_V"])
    anchors = [res["R_max_ohm"], res["R_used_ohm"]] + ([res["R_min_ohm"]] if res.get("R_min_ohm") else [])
    R = np.geomspace(min(anchors) / 20.0, max(anchors) * 20.0, samples)
    return {"R_ohm": R, "t_reach_s": R * C * ln, "P_cont_max_W": res["V_max_V"] ** 2 / R,
            "P_cont_nom_W": res["V_nom_V"] ** 2 / R}
