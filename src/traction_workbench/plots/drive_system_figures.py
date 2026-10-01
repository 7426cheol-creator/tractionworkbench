"""Figures of the drive-system views: drive cycle (trace, energy per component, operating points), integrated
charging and system budgets.  Each figure draws the result dictionary of its ``api`` function as it is."""

from __future__ import annotations

import matplotlib as mpl
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from ..i18n import tr
from . import style as S
from .figures import _note, _reset

STATE_COL = {"traction": "#0969da", "regeneration": "#1a7f37", "regeneration_limited": "#bf8700",
             "friction_only": "#8250df", "not_delivered": "#cf222e", "unknown": "#6e7781", "drive_drag": "#57606a"}
LOSS_LAB = {"battery": lambda: tr("배터리·하니스 R I²", "battery + harness R I²"),
            "inverter": lambda: tr("인버터", "inverter"), "motor_copper": lambda: tr("모터 동손", "motor copper"),
            "motor_rotational": lambda: tr("모터 회전·철손", "motor rotational / iron"),
            "reducer": lambda: tr("감속기", "reducer"), "axle": lambda: tr("액슬(차동·하프샤프트)", "axle (differential, shafts)")}


def _state_spans(ax, t, states, which, color, alpha=0.18):
    """Shade the intervals whose state is in ``which``."""
    t = np.asarray(t, float)
    on = np.array([s in which for s in states])
    if not on.any():
        return False
    i = 0
    while i < on.size:
        if on[i]:
            j = i
            while j + 1 < on.size and on[j + 1]:
                j += 1
            ax.axvspan(t[i] - 0.5, t[j] + 0.5, color=color, alpha=alpha, lw=0)
            i = j + 1
        else:
            i += 1
    return True


# ---------------------------------------------------------------------------------------------- drive cycle

def fig_cycle_trace(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t_ = S.theme()
    tr_ = res.get("trace") or {}
    if not tr_:
        ax = fig.subplots()
        ax.set_axis_off()
        _note(ax, tr("시간 이력이 없습니다 (keep_trace = false)", "no time history (keep_trace = false)"))
        return
    ax1, ax2, ax3 = fig.subplots(3, 1, sharex=True, gridspec_kw={"height_ratios": [1.1, 1.0, 1.0]})
    t = np.asarray(tr_["t_mid_s"], float)
    st = tr_["state"]
    for p in res.get("phases") or []:
        ax1.axvline(p["start_s"], color=t_["grid"], lw=0.8, ls=":")
        ax1.annotate(p["name"].replace("_", " "), (0.5 * (p["start_s"] + p["end_s"]), 1.0),
                     xycoords=("data", "axes fraction"), xytext=(0, -10), textcoords="offset points",
                     ha="center", fontsize=7, color=t_["muted"])
    ax1.plot(t, tr_["v_kmh"], color=S.ACCENT, lw=1.1)
    nd = _state_spans(ax1, t, st, {"not_delivered"}, STATE_COL["not_delivered"], 0.25)
    un = _state_spans(ax1, t, st, {"unknown"}, STATE_COL["unknown"], 0.25)
    ax1.set_ylabel(tr("차속 [km/h]", "speed [km/h]"))
    hs = [Line2D([], [], color=S.ACCENT, lw=1.1, label=tr("주행 사이클 (구간 중점)", "trace (interval midpoints)"))]
    if nd:
        hs.append(Patch(fc=STATE_COL["not_delivered"], alpha=0.3, label=tr("구동이 전달하지 못함", "not delivered")))
    if un:
        hs.append(Patch(fc=STATE_COL["unknown"], alpha=0.3, label="UNKNOWN"))
    ax1.legend(handles=hs, fontsize=7, loc="upper left")
    dem = np.asarray(tr_["T_out_demand_Nm"], float)
    drv = np.asarray(tr_["T_out_drive_Nm"], float)
    ax2.plot(t, dem, color=t_["muted"], lw=0.9, label=tr("요구 (감속기 출력)", "demand (reducer output)"))
    ax2.plot(t, drv, color=S.PHASE[2], lw=1.0, label=tr("구동 부담분", "taken by the drive"))
    ax2.fill_between(t, drv, dem, where=dem < drv, color=STATE_COL["friction_only"], alpha=0.25, lw=0,
                     label=tr("마찰 제동", "friction brakes"))
    ax2.axhline(0, color=t_["fg"], lw=0.6)
    ax2.set_ylabel(tr("출력 토크 [N·m]", "output torque [N·m]"))
    ax2.legend(fontsize=7, loc="upper left", ncol=3)
    pb = np.asarray(tr_["P_ocv_W"], float) / 1e3
    pf = np.asarray(tr_["P_friction_W"], float) / 1e3
    ax3.plot(t, pb, color=S.ACCENT, lw=1.0, label=tr("배터리 (OCV)", "battery (OCV)"))
    ax3.fill_between(t, 0, -pf, color=STATE_COL["friction_only"], alpha=0.35, lw=0,
                     label=tr("마찰 제동 (음수로 표시)", "friction brakes (shown negative)"))
    ax3.axhline(0, color=t_["fg"], lw=0.6)
    ax3.set_ylabel(tr("전력 [kW]", "power [kW]"))
    ax3.set_xlabel(tr("시간 [s]", "time [s]"))
    ax3.legend(fontsize=7, loc="upper left", ncol=2)
    c = res.get("cycle") or {}
    ax1.set_title(f"{c.get('name', '')} — {c.get('distance_km', 0):.2f} km, {c.get('duration_s', 0):.0f} s", fontsize=9)


def fig_cycle_energy(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t_ = S.theme()
    ax, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.35, 1.0]})
    e, L, rw = res["energy_kWh"], res["losses_kWh"], res["road_work_kWh"]
    km = (res.get("cycle") or {}).get("distance_km") or 0.0
    # where the battery's traction energy goes, and what comes back
    rows = [(tr("구름 (A)", "rolling (A)"), rw["rolling_constant"], "#6e7781"),
            (tr("선형 (B v)", "linear (B v)"), rw["linear"], "#8c959f"),
            (tr("공기 (C v²)", "aero (C v²)"), rw["aero_quadratic"], "#57606a"),
            (tr("경사", "grade"), rw["grade"], "#a8b1ba"),
            (tr("마찰 제동", "friction brakes"), e["friction_brakes"], STATE_COL["friction_only"])]
    rows += [(LOSS_LAB[k](), L[k], col) for k, col in (("battery", "#bf8700"), ("inverter", "#cf222e"),
                                                        ("motor_copper", "#0969da"), ("motor_rotational", "#54aeff"),
                                                        ("reducer", "#1a7f37"), ("axle", "#4ac26b"))]
    rows.append((tr("HV 보조 부하", "HV auxiliaries"), e["aux_hv"], "#d4a72c"))
    rows = [r for r in rows if abs(r[1]) > 1e-12]
    y = np.arange(len(rows))[::-1]
    vals = [r[1] * 1e3 for r in rows]
    ax.barh(y, vals, color=[r[2] for r in rows], alpha=0.9)
    for yi, v, r in zip(y, vals, rows):
        txt = f"{v:.0f} Wh" + (f"  ({v / km:.1f} Wh/km)" if km > 0 else "")
        ax.annotate(txt, (max(v, 0), yi), xytext=(3, 0), textcoords="offset points", va="center", fontsize=7)
    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in rows], fontsize=8)
    ax.axvline(0, color=t_["fg"], lw=0.6)
    ax.set_xlabel("[Wh]")
    reg = res["regeneration"]
    rr = reg.get("recovery_ratio")
    ax.set_title(tr(f"에너지가 가는 곳 — 회생 회수 {e['battery_regen_in'] * 1e3:.0f} Wh"
                    + (f" (제동 에너지의 {100 * rr:.0f} %)" if rr is not None else ""),
                    f"where the energy goes — regeneration recovers {e['battery_regen_in'] * 1e3:.0f} Wh"
                    + (f" ({100 * rr:.0f} % of braking)" if rr is not None else "")), fontsize=9)
    xmax = max(vals) if vals else 1.0
    ax.set_xlim(min(0.0, min(vals) * 1.1) if vals else 0.0, xmax * 1.45)
    ph = res.get("phases") or []
    cons = res["consumption_Wh_per_km"]
    if ph and all(p.get("Wh_per_km_battery_ocv") is not None for p in ph):
        x = np.arange(len(ph) + 1)
        v = [p["Wh_per_km_battery_ocv"] for p in ph] + [cons["battery_ocv"]]
        lab = [p["name"].replace("_", "\n") for p in ph] + [tr("전체", "total")]
        ax2.bar(x, v, color=[S.ACCENT] * len(ph) + [S.PHASE[2]], alpha=0.85)
        for xi, vi in zip(x, v):
            ax2.annotate(f"{vi:.0f}", (xi, vi), xytext=(0, 2), textcoords="offset points", ha="center", fontsize=7)
        ax2.set_xticks(x)
        ax2.set_xticklabels(lab, fontsize=7)
        ax2.set_ylabel(tr("배터리 소비 [Wh/km]", "battery consumption [Wh/km]"))
        ax2.set_title(tr("구간별 소비", "consumption per phase"), fontsize=9)
    else:
        ax2.set_axis_off()
        lines = [tr(f"배터리 (OCV): {cons['battery_ocv']:.1f} Wh/km", f"battery (OCV): {cons['battery_ocv']:.1f} Wh/km")
                 if cons.get("battery_ocv") is not None else tr("배터리 소비: UNKNOWN", "battery consumption: UNKNOWN"),
                 tr(f"인버터 DC 순: {cons['inverter_dc_net']:.1f} Wh/km", f"inverter DC net: {cons['inverter_dc_net']:.1f} Wh/km")
                 if cons.get("inverter_dc_net") is not None else ""]
        if res.get("range_km"):
            lines.append(tr(f"주행거리 (가용 에너지/소비): {res['range_km']:.0f} km",
                            f"range (usable energy / consumption): {res['range_km']:.0f} km"))
        ax2.text(0.02, 0.95, "\n".join(x for x in lines if x), va="top", fontsize=9, transform=ax2.transAxes)


def fig_cycle_points(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t_ = S.theme()
    ax = fig.subplots()
    tr_ = res.get("trace") or {}
    if not tr_:
        ax.set_axis_off()
        _note(ax, tr("시간 이력이 없습니다", "no time history"))
        return
    n = np.asarray(tr_["n_rpm"], float)
    T = np.asarray(tr_["T_m_Nm"], float)
    st = np.asarray(tr_["state"], dtype=object)
    shown = []
    for key in ("traction", "regeneration", "regeneration_limited", "friction_only", "not_delivered"):
        m = st == key
        if m.any():
            ax.scatter(n[m], T[m], s=6, alpha=0.45, color=STATE_COL[key], lw=0)
            shown.append(key)
    names = {"traction": tr("구동", "traction"), "regeneration": tr("회생", "regeneration"),
             "regeneration_limited": tr("회생 (한계로 축소)", "regeneration (limited)"),
             "friction_only": tr("마찰 제동만 (저속)", "friction only (low speed)"),
             "not_delivered": tr("전달 못함", "not delivered")}
    ax.legend(handles=[Line2D([], [], ls="", marker="o", ms=5, color=STATE_COL[k], label=names[k]) for k in shown],
              fontsize=7, loc="upper right")
    ax.axhline(0, color=t_["fg"], lw=0.6)
    ax.set_xlabel(tr("모터 속도 [rpm]", "motor speed [rpm]"))
    ax.set_ylabel(tr("모터 축 토크 [N·m]", "motor shaft torque [N·m]"))
    ax.set_title(tr("주행 사이클의 모터 운전점 (1 s 구간마다 한 점)", "motor operating points of the trace (one per 1 s "
                                                         "interval)"), fontsize=9)


# ---------------------------------------------------------------------------------------------- integrated charging

_CHG_CMAP = mpl.colormaps["cividis"]
LIMIT_COL = {"neutral_rms": "#8250df", "phase_peak": "#cf222e", "junction": "#bf8700", "winding": "#0969da",
             "battery_power": "#1a7f37", "battery_current": "#4ac26b", "charger_current": "#57606a",
             "charger_power": "#8c959f", "search bound": "#d0d7de"}
LIMIT_NAME = {"neutral_rms": lambda: tr("중성선 RMS", "neutral RMS"), "phase_peak": lambda: tr("상 피크 전류", "phase peak"),
              "junction": lambda: tr("접합 온도", "junction temperature"),
              "winding": lambda: tr("고정자 동손", "stator copper loss"),
              "battery_power": lambda: tr("배터리 충전 전력", "battery charge power"),
              "battery_current": lambda: tr("배터리 충전 전류", "battery charge current"),
              "charger_current": lambda: tr("충전기 전류", "charger current"),
              "charger_power": lambda: tr("충전기 전력", "charger power"),
              "search bound": lambda: tr("탐색 상한", "search bound")}


def fig_charging_waveforms(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t_ = S.theme()
    w = res.get("waveform")
    if not w:
        ax = fig.subplots()
        ax.set_axis_off()
        _note(ax, res.get("reason") or tr("파형 없음", "no waveform"))
        return
    ax1, ax2, ax3 = fig.subplots(3, 1, sharex=True, gridspec_kw={"height_ratios": [1.5, 0.7, 0.8]})
    t = np.asarray(w["t_us"], float)
    T = t[-1]
    tt = np.concatenate((t, t[1:] + T))                 # two periods for reading

    def two(y):
        y = np.asarray(y, float)
        return np.concatenate((y, y[1:]))
    for key, col, lab in (("ia_A", S.PHASE[0], "i_a"), ("ib_A", S.PHASE[1], "i_b"), ("ic_A", S.PHASE[2], "i_c")):
        ax1.plot(tt, two(w[key]), color=col, lw=1.0, label=lab)
    ax1b = ax1.twinx()
    ax1b.plot(tt, two(w["neutral_A"]), color=t_["fg"], lw=1.0, ls="--", label=tr("중성선 (충전기)", "neutral (charger)"))
    ax1b.set_ylabel(tr("중성선 [A]", "neutral [A]"))
    ax1.set_ylabel(tr("상전류 [A]", "phase current [A]"))
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1b.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper right", ncol=4)
    c = res["currents"]
    ax1.set_title(tr(f"한 스위칭 주기 ×2 — 상 리플 {max(c['phase_ripple_pp_A']):.1f} A pk-pk, 중성선 리플 "
                     f"{c['neutral_ripple_pp_A']:.1f} A pk-pk ({res['inputs']['interleave']})",
                     f"two switching periods — phase ripple {max(c['phase_ripple_pp_A']):.1f} A pk-pk, neutral ripple "
                     f"{c['neutral_ripple_pp_A']:.1f} A pk-pk ({res['inputs']['interleave']})"), fontsize=9)
    for k, (key, col) in enumerate((("upper_a", S.PHASE[0]), ("upper_b", S.PHASE[1]), ("upper_c", S.PHASE[2]))):
        ax2.step(tt, two(w[key]) * 0.8 + 1.1 * (2 - k), where="post", color=col, lw=1.0)
    ax2.set_yticks([1.1 * (2 - k) + 0.4 for k in range(3)])
    ax2.set_yticklabels(["a", "b", "c"])
    ax2.set_ylabel(tr("상측 도통", "upper path on"))
    if w.get("torque_Nm") is not None:
        ax3.plot(tt, two(w["torque_Nm"]), color=S.ACCENT, lw=1.0)
        tq = res.get("torque") or {}
        _note(ax3, tr(f"평균 {tq.get('mean_Nm', 0):.3g} N·m, |피크| {tq.get('peak_abs_Nm', 0):.3g} N·m (영상분은 토크 없음)",
                      f"mean {tq.get('mean_Nm', 0):.3g} N·m, |peak| {tq.get('peak_abs_Nm', 0):.3g} N·m (zero sequence "
                      f"makes none)"), "upper right")
    ax3.axhline(0, color=t_["fg"], lw=0.6)
    ax3.set_ylabel(tr("토크 [N·m]", "torque [N·m]"))
    ax3.set_xlabel("t [µs]")


def fig_charging_losses(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t_ = S.theme()
    ax, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.3, 1.0]})
    legs = res.get("legs") or []
    names, cond, sw = [], [], []
    for j, leg in enumerate(legs):
        for die, P in leg["dies_W"].items():
            names.append(f"{'abc'[j]} {die.replace('_', ' ')}")
            # split the die heat: switching events of that die vs conduction
            swk = {"upper_igbt": "upper_switch", "lower_igbt": "lower_switch", "upper_mosfet": "upper_switch",
                   "lower_mosfet": "lower_switch", "upper_diode": "upper_recovery", "lower_diode": "lower_recovery"}[die]
            s_ = leg["switching_W"].get(swk, 0.0) + (leg["switching_W"].get(swk.replace("switch", "recovery"), 0.0)
                                                    if die.endswith("mosfet") else 0.0)
            sw.append(s_)
            cond.append(P - s_)
    y = np.arange(len(names))[::-1]
    ax.barh(y, cond, color=S.ACCENT, alpha=0.85, label=tr("도통", "conduction"))
    ax.barh(y, sw, left=cond, color="#bf8700", alpha=0.85, label=tr("스위칭·회복", "switching / recovery"))
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel(tr("다이 발열 [W]", "die heat [W]"))
    ax.legend(fontsize=7, loc="lower right")
    tj = res.get("Tj_C")
    ax.set_title(tr(f"다이별 발열 — 최고 Tj {tj:.1f} °C ({res.get('hottest_die')})" if tj is not None else
                    "다이별 발열 — Tj 미확정", f"heat per die — hottest Tj {tj:.1f} °C ({res.get('hottest_die')})"
                    if tj is not None else "heat per die — Tj not established"), fontsize=9)
    ax2.set_axis_off()
    L = res.get("losses_W") or {}
    lines = [tr(f"충전기 {res['P_charger_W'] / 1e3:.2f} kW → 배터리 {res['P_battery_W'] / 1e3:.2f} kW",
                f"charger {res['P_charger_W'] / 1e3:.2f} kW → battery {res['P_battery_W'] / 1e3:.2f} kW"),
             tr(f"효율 {100 * res['efficiency']:.2f} %" if res.get("efficiency") else "효율 —",
                f"efficiency {100 * res['efficiency']:.2f} %" if res.get("efficiency") else "efficiency —"),
             tr(f"소자 {L.get('devices') or 0:.0f} W · 권선 {L.get('motor_copper') or 0:.0f} W · DC-link "
                f"{L.get('dc_link_capacitor') or 0:.2f} W", f"devices {L.get('devices') or 0:.0f} W · winding "
                f"{L.get('motor_copper') or 0:.0f} W · DC link {L.get('dc_link_capacitor') or 0:.2f} W"),
             tr(f"상측 듀티 {res['duty_upper']:.3f}", f"upper-path duty {res['duty_upper']:.3f}"), ""]
    for c in res.get("checks") or []:
        v = c["value"]
        vs = "—" if v is None else (f"{v / 1e3:.2f} k" if c["unit"] == "W" else f"{v:.1f} ")
        ls = "—" if c["limit"] is None else (f"{c['limit'] / 1e3:.3g} k" if c["unit"] == "W" else f"{c['limit']:.4g} ")
        lines.append(f"{c['status']:>7}  {LIMIT_NAME.get(c['id'], lambda: c['id'])()}: {vs}{c['unit']} / {ls}{c['unit']}")
    ax2.text(0.0, 0.98, "\n".join(lines), va="top", ha="left", fontsize=8,
             transform=ax2.transAxes, color=t_["fg"])


def fig_charging_capability(fig, cap: dict, title: str | None = None):
    _reset(fig, title)
    t_ = S.theme()
    ax = fig.subplots()
    rows = cap.get("rows") or []
    vcs = cap.get("V_chargers_V") or []
    used = set()
    for k, vc in enumerate(vcs):
        rr = [r for r in rows if r["V_charger_V"] == vc]
        xb = [r["V_battery_V"] for r in rr]
        yp = [None if r.get("P_max_W") is None else r["P_max_W"] / 1e3 for r in rr]
        col = _CHG_CMAP(0.1 + 0.75 * (k / max(1, len(vcs) - 1)))   # sequential: never a limit colour
        ax.plot([x for x, y in zip(xb, yp) if y is not None], [y for y in yp if y is not None], color=col, lw=1.4,
                marker="", label=f"V_charger {vc:g} V")
        for r, x, y in zip(rr, xb, yp):
            if y is None:
                ax.annotate("UNKNOWN", (x, 0), fontsize=7, color=t_["muted"], ha="center")
                continue
            lim = (r.get("limiting") or ["search bound"])[0]
            used.add(lim)
            ax.scatter([x], [y], s=34, color=LIMIT_COL.get(lim, "#6e7781"), zorder=3, edgecolor=t_["bg"], lw=0.6)
    h1 = ax.get_legend_handles_labels()
    lim_h = [Line2D([], [], ls="", marker="o", ms=6, color=LIMIT_COL.get(k, "#6e7781"),
                    label=tr("한계: ", "limit: ") + LIMIT_NAME.get(k, lambda: k)()) for k in sorted(used)]
    ax.legend(h1[0] + lim_h, h1[1] + [h.get_label() for h in lim_h], fontsize=7, loc="lower right", ncol=2)
    ax.set_xlabel(tr("배터리 전압 [V]", "battery voltage [V]"))
    ax.set_ylabel(tr("최대 충전 전력 (충전기 측) [kW]", "maximum charging power (charger side) [kW]"))
    ax.set_title(tr("통합 충전 능력 — 점의 색 = 그 점을 막는 한계", "integrated charging capability — dot colour = the "
                                                       "limit that binds there"), fontsize=9)
    ax.set_ylim(bottom=0)
