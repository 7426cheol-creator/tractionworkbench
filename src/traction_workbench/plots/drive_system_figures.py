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


# ---------------------------------------------------------------------------------------------- system budgets

# contributor identity in a fixed order (validated categorical slots; green and red stay reserved for the verdicts)
_BUD_CAT = {"light": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"),
            "dark": ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9")}
BUDGET_ITEM = {"current_gain": lambda: tr("전류 센서 이득", "current-sensor gain"),
               "current_offset": lambda: tr("전류 센서 오프셋", "current-sensor offset"),
               "resolver_offset": lambda: tr("레졸버 오프셋", "resolver offset"),
               "magnet_temperature": lambda: tr("자석 온도 추정", "magnet temperature estimate"),
               "model_tolerance": lambda: tr("모델 공차 (ψ, L_d, L_q)", "model tolerance (ψ, L_d, L_q)"),
               "estimator": lambda: tr("토크 추정기", "torque estimator"),
               "monitor_mismatch": lambda: tr("모니터 불일치", "monitor mismatch")}
COMB_NAME = {"worst_case": lambda: tr("최악 (선형 합)", "worst case (linear)"),
             "rss": lambda: tr("RSS (제곱합 근)", "RSS (root sum of squares)"),
             "mixed": lambda: tr("혼합 (계통 선형 + 랜덤 RSS)", "mixed (systematic linear + random RSS)")}


def _bud_colors(ids):
    pal = _BUD_CAT["dark" if S.theme_name() == "dark" else "light"]
    return {k: pal[i % len(pal)] for i, k in enumerate(ids)}


def _item_name(k):
    return BUDGET_ITEM.get(k, lambda: k.replace("_", " "))()


def fig_budget_torque(fig, res: dict, title: str | None = None):
    """Shaft-torque error per operating point: the contributions stacked linearly (= the worst-case stack), the
    declared stack as a marker coloured by its verdict, the requirement as a tick per point."""
    _reset(fig, title)
    t_ = S.theme()
    ax = fig.subplots()
    pts = res.get("points") or []
    if not pts:
        ax.set_axis_off()
        _note(ax, tr("운전점이 없습니다", "no operating points"))
        return
    items = [k for k in ("current_gain", "current_offset", "resolver_offset", "magnet_temperature", "model_tolerance",
                         "estimator") if any((p.get("contributions_Nm") or {}).get(k) is not None for p in pts)]
    col = _bud_colors(items)
    x = np.arange(len(pts))
    bottom = np.zeros(len(pts))
    for k in items:
        v = np.array([((p.get("contributions_Nm") or {}).get(k) or 0.0) for p in pts])
        ax.bar(x, v, bottom=bottom, width=0.72, color=col[k], edgecolor=t_["bg"], lw=0.8, label=_item_name(k))
        bottom += v
    comb = res.get("combination", "mixed")
    used = set()
    for i, p in enumerate(pts):
        if "total_Nm" not in p:
            ax.annotate(tr("미평가", "not evaluated"), (i, 0), xytext=(0, 3), textcoords="offset points",
                        rotation=90, fontsize=6.5, color=t_["muted"], ha="center", va="bottom")
            continue
        lim = p.get("limit_Nm")
        if lim is not None:
            ax.plot([i - 0.42, i + 0.42], [lim, lim], color=t_["fg"], lw=1.6, solid_capstyle="butt")
        st = p.get("status", "UNKNOWN")
        used.add(st)
        ax.scatter([i], [p["total_Nm"]], marker="D", s=34, color=S.VERDICT.get(st, t_["muted"]), zorder=4,
                   edgecolor=t_["bg"], lw=0.8)
    # speed groups under the axis
    labels = [f"{p['torque_Nm']:.0f}" for p in pts]
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=90, fontsize=6.5)
    speeds = [p["speed_rpm"] for p in pts]
    start = 0
    for i in range(1, len(pts) + 1):
        if i == len(pts) or speeds[i] != speeds[start]:
            if start > 0:
                ax.axvline(start - 0.5, color=t_["grid"], lw=0.8)
            ax.annotate(f"{speeds[start]:.0f} rpm", ((start + i - 1) / 2, 1.0), xycoords=("data", "axes fraction"),
                        xytext=(0, -4), textcoords="offset points", ha="center", va="top", fontsize=7.5,
                        color=t_["muted"])
            start = i
    ax.set_xlim(-0.6, len(pts) - 0.4)
    ax.set_xlabel(tr("운전점 토크 요구 [N·m] (속도별 묶음)", "operating-point torque [N·m] (grouped by speed)"))
    ax.set_ylabel(tr("축 토크 오차 [N·m]", "shaft-torque error [N·m]"))
    h, lab = ax.get_legend_handles_labels()
    h = h[::-1]
    lab = lab[::-1]
    h.append(Line2D([], [], color=t_["fg"], lw=1.6))
    lab.append(tr("요구 max(abs, rel·|T|)", "requirement max(abs, rel·|T|)"))
    for st in ("PASS", "FAIL", "UNKNOWN"):
        if st in used:
            h.append(Line2D([], [], ls="", marker="D", ms=5.5, color=S.VERDICT[st]))
            lab.append(tr(f"선언 스택 ({comb}) — {st}", f"declared stack ({comb}) — {st}"))
    fig.legend(h, lab, fontsize=7, loc="outside lower center", ncol=4)
    ax.set_ylim(0, max(1e-9, max(max(bottom), max((p.get("limit_Nm") or 0) for p in pts))) * 1.12)
    ax.set_title(tr("토크 정확도 버짓 — 막대 = 기여 선형 합(최악), ◆ = 선언 스택, ─ = 요구",
                    "torque-accuracy budget — bars = contributions stacked linearly (worst case), ◆ = declared "
                    "stack, ─ = requirement"), fontsize=9)


def fig_budget_fusa(fig, res: dict, title: str | None = None):
    """The functional-safety link against |T|: safety window and monitor threshold as lines, per point the largest
    deviation the monitor lets through (threshold + mismatch + unseen errors) and the normal-operation error."""
    _reset(fig, title)
    t_ = S.theme()
    ax = fig.subplots()
    f = res.get("fusa")
    ev = [p for p in res.get("points") or [] if p.get("fusa_window_Nm") is not None]
    if not f or not ev:
        ax.set_axis_off()
        _note(ax, tr("프로젝트에 토크 창(TSR)·토크 모니터(SM)가 없습니다 — 기능안전 연결 없음",
                     "the project declares no torque window (TSR) or torque monitor (SM) — no functional-safety link"))
        return
    Tm = max(abs(p["torque_Nm"]) for p in ev) * 1.08
    tt = np.linspace(0.0, Tm, 200)

    def line(spec):
        return np.maximum(float(spec.get("abs_Nm") or 0.0), float(spec.get("rel") or 0.0) * tt)
    if f.get("torque_window"):
        ax.plot(tt, line(f["torque_window"]), color=t_["fg"], lw=1.6,
                label=tr("안전 창 (TSR) max(abs, rel·|T|)", "safety window (TSR) max(abs, rel·|T|)"))
    if f.get("monitor"):
        ax.plot(tt, line(f["monitor"]), color=t_["muted"], lw=1.2, ls="--",
                label=tr("모니터 문턱 (SM)", "monitor threshold (SM)"))
    used = set()
    for p in ev:
        st = p.get("undetected_status", "UNKNOWN")
        used.add(st)
        if p.get("undetected_Nm") is not None:
            ax.scatter([abs(p["torque_Nm"])], [p["undetected_Nm"]], marker="o", s=30, zorder=4,
                       color=S.VERDICT.get(st, t_["muted"]), edgecolor=t_["bg"], lw=0.8)
        ax.scatter([abs(p["torque_Nm"])], [p["fusa_error_Nm"]], marker="s", s=22, zorder=3, facecolor="none",
                   edgecolor=S.ACCENT, lw=1.0)
    h, lab = ax.get_legend_handles_labels()
    for st in ("PASS", "FAIL", "UNKNOWN"):
        if st in used:
            h.append(Line2D([], [], ls="", marker="o", ms=5.5, color=S.VERDICT[st]))
            lab.append(tr(f"미검출 최대 편차 = 문턱 + 불일치 + 모니터가 못 보는 오차 — {st}",
                          f"largest undetected deviation = threshold + mismatch + errors the monitor cannot see — {st}"))
    h.append(Line2D([], [], ls="", marker="s", ms=5, mfc="none", mec=S.ACCENT))
    lab.append(tr("정상 운전 오차 (최악 합)", "normal-operation error (worst-case sum)"))
    ax.legend(h, lab, fontsize=7, loc="upper left")
    ax.set_xlim(0, Tm)
    ax.set_ylim(bottom=0)
    ax.set_xlabel(tr("|토크 요구| [N·m]", "|torque request| [N·m]"))
    ax.set_ylabel(tr("토크 편차 [N·m]", "torque deviation [N·m]"))
    w = f.get("worst_undetected_point")
    ax.set_title(tr("기능안전 연결 — 창 위의 점: 모니터가 놓치는 고장이 창을 넘을 수 있음",
                    "functional-safety link — a dot above the window: a fault the monitor misses can leave the "
                    "window"), fontsize=9)
    if w is not None and w.get("undetected_margin_Nm") is not None:
        _note(ax, tr(f"최악: {w['speed_rpm']:.0f} rpm, {w['torque_Nm']:.0f} N·m — 문턱 {w['monitor_threshold_Nm']:.1f} + "
                     f"불일치 {w['monitor_mismatch_Nm'] or 0:.1f} + 못 보는 오차 {w['monitor_unseen_Nm']:.1f} = "
                     f"{w['undetected_Nm']:.1f} N·m vs 창 {w['fusa_window_Nm']:.1f} N·m",
                     f"worst: {w['speed_rpm']:.0f} rpm, {w['torque_Nm']:.0f} N·m — threshold "
                     f"{w['monitor_threshold_Nm']:.1f} + mismatch {w['monitor_mismatch_Nm'] or 0:.1f} + unseen "
                     f"{w['monitor_unseen_Nm']:.1f} = {w['undetected_Nm']:.1f} N·m vs window "
                     f"{w['fusa_window_Nm']:.1f} N·m"), "lower right")


def fig_budget_bars(fig, b: dict, title: str | None = None):
    """One budget: each contributor's value with its allocation, and the three stacks against the limit."""
    _reset(fig, title)
    t_ = S.theme()
    rows = b.get("contributors") or []
    ax, ax2 = fig.subplots(2, 1, gridspec_kw={"height_ratios": [max(2, len(rows)), 3]})
    unit = str(b.get("unit", "")).replace("*", "·")
    ids = [r["id"] for r in rows]
    # the torque items keep their identity colours (same as the per-point chart); other budgets name each bar on
    # the axis, so one colour serves them all (no colour is ever reused for a second contributor)
    col = _bud_colors([k for k in BUDGET_ITEM if k in ids]) if all(k in BUDGET_ITEM for k in ids) else \
        {k: S.ACCENT for k in ids}
    y = np.arange(len(rows))[::-1]
    vmax = 0.0
    for yi, r in zip(y, rows):
        v = r.get("value")
        if v is None:
            ax.annotate(tr("미확정", "not established"), (0, yi), xytext=(3, 0), textcoords="offset points",
                        va="center", fontsize=7.5, color=t_["muted"])
            continue
        ax.barh([yi], [v], height=0.62, color=col[r["id"]], edgecolor=t_["bg"], lw=0.8)
        vmax = max(vmax, v)
        a = r.get("allocation")
        txt = f"{v:.3g} {unit}"
        if r.get("share") is not None:
            txt += f"  ({100 * r['share']:.0f} %)"
        if a is not None:
            ax.plot([a, a], [yi - 0.38, yi + 0.38], color=t_["fg"], lw=1.6)
            vmax = max(vmax, a)
            st = r.get("allocation_status")
            txt += tr(f"  · 배분 {a:.3g} {st}", f"  · allocation {a:.3g} {st}")
        ax.annotate(txt, (max(v, a or 0.0), yi), xytext=(5, 0), textcoords="offset points", va="center",
                    fontsize=7.5, color=t_["fg"])
    ax.set_yticks(y)
    ax.set_yticklabels([_item_name(r["id"]) if r["id"] in BUDGET_ITEM else
                        (LOSS_LAB[r["id"]]() if r["id"] in LOSS_LAB else r["title"]) for r in rows], fontsize=7.5)
    ax.set_xlim(0, max(vmax, 1e-12) * 1.9)
    ax.set_xlabel(unit)
    am = b.get("allocation_method")
    ax.set_title(tr("기여 (막대) · 배분 (│" + (f", {am}" if am else "") + ")",
                    "contributors (bars) · allocation (│" + (f", {am}" if am else "") + ")"), fontsize=9)
    st = b.get("stacks") or {}
    comb = b.get("combination", "mixed")
    keys = [k for k in ("worst_case", "rss", "mixed") if k in st]
    yy = np.arange(len(keys))[::-1]
    for yi, k in zip(yy, keys):
        dec = k == comb
        ax2.barh([yi], [st[k]], height=0.6, color=S.VERDICT.get(b.get("status"), t_["muted"]) if dec else t_["grid"],
                 edgecolor=t_["bg"], lw=0.8)
        ax2.annotate(f"{st[k]:.4g} {unit}" + (tr("  ← 판정", "  ← decides") if dec else ""), (st[k], yi),
                     xytext=(4, 0), textcoords="offset points", va="center", fontsize=7.5, color=t_["fg"])
    lim = b.get("limit")
    if lim is not None:
        ax2.axvline(lim, color=t_["fg"], lw=1.6)
    ax2.set_yticks(yy)
    ax2.set_yticklabels([COMB_NAME[k]() for k in keys], fontsize=7.5)
    ax2.set_xlim(0, max([st.get(k, 0) for k in keys] + [lim or 0, 1e-12]) * 1.45)
    ax2.set_xlabel(unit)
    ax2.set_title(tr(f"스택 — {b.get('status')}", f"stacks — {b.get('status')}")
                  + ("" if lim is None else tr(f", 한계 (│) {lim:.4g} {unit}", f", limit (│) {lim:.4g} {unit}"))
                  + ("" if b.get("margin") is None else tr(f", 여유 {b['margin']:.3g} {unit}",
                                                           f", margin {b['margin']:.3g} {unit}")), fontsize=9)


# ---------------------------------------------------------------------------------------------- simulator export

SIM_Q = {"P_dc_W": lambda: tr("DC 전력", "DC power"), "loss_total_W": lambda: tr("총 손실 (평가된 항목)", "total loss (evaluated)"),
         "loss_inverter_W": lambda: tr("인버터 손실", "inverter loss"), "loss_motor_W": lambda: tr("모터 손실", "motor loss"),
         "loss_reducer_W": lambda: tr("감속기 손실", "reducer loss"), "P_out_W": lambda: tr("출력 전력", "output power"),
         "P_shaft_W": lambda: tr("축 전력", "shaft power"), "P_ac_W": lambda: tr("모터 단자 전력", "motor terminal power"),
         "id_A": lambda: "i_d", "iq_A": lambda: "i_q"}


def fig_sim_map(fig, maps: dict, title: str | None = None, quantity: str = "P_dc_W", vdc_index: int = 0):
    """One table of the export at one voltage: cells as computed (blank = no value), the full-load curves on top."""
    _reset(fig, title)
    t_ = S.theme()
    ax = fig.subplots()
    sp = np.asarray(maps["speeds_rpm"], float)
    tq = np.asarray(maps["torques_Nm"], float)
    a = min(max(0, int(vdc_index)), len(maps["Vdc_V"]) - 1)
    tab = np.array([[np.nan if v is None else v for v in row] for row in maps["tables"][quantity][a]], dtype=float)
    unit = "A" if quantity.endswith("_A") else "kW"
    z = tab if unit == "A" else tab / 1e3
    signed = quantity in ("P_dc_W", "P_ac_W", "P_shaft_W", "P_out_W", "id_A", "iq_A")
    if np.isfinite(z).any():
        if signed:
            lim = float(np.nanmax(np.abs(z))) or 1.0
            mesh = ax.pcolormesh(sp, tq, z, shading="nearest", cmap="RdBu_r", vmin=-lim, vmax=lim)
        else:
            mesh = ax.pcolormesh(sp, tq, z, shading="nearest", cmap="viridis")
        cb = fig.colorbar(mesh, ax=ax)
        cb.set_label(f"{SIM_Q.get(quantity, lambda: quantity)()} [{unit}]")
    st = np.asarray(maps["status"][a])
    unk = np.argwhere(st == 1)
    if unk.size:
        ax.scatter(sp[unk[:, 1]], tq[unk[:, 0]], marker="x", s=14, color=t_["muted"], lw=0.8, label="UNKNOWN")
    tmax = np.array([np.nan if v is None else v for v in maps["T_max_Nm"][a]], float)
    tmin = np.array([np.nan if v is None else v for v in maps["T_min_Nm"][a]], float)
    ax.plot(sp, tmax, color=t_["fg"], lw=1.6, label=tr("최대 토크 (구동)", "full load (motoring)"))
    ax.plot(sp, tmin, color=t_["fg"], lw=1.6, ls="--", label=tr("최대 토크 (회생)", "full load (generating)"))
    ax.axhline(0, color=t_["grid"], lw=0.8)
    ax.set_xlabel(tr("속도 [rpm]", "speed [rpm]"))
    ax.set_ylabel(tr("축 토크 [N·m]", "shaft torque [N·m]"))
    ax.legend(fontsize=7, loc="upper right")
    ax.set_title(tr(f"{SIM_Q.get(quantity, lambda: quantity)()} — Vdc {maps['Vdc_V'][a]:g} V (빈 칸 = 값 없음)",
                    f"{SIM_Q.get(quantity, lambda: quantity)()} — Vdc {maps['Vdc_V'][a]:g} V (blank = no value)"),
                 fontsize=9)


def fig_sim_fullload(fig, maps: dict, title: str | None = None):
    """The full-load curves (motoring above, generating below) at every exported voltage."""
    _reset(fig, title)
    t_ = S.theme()
    ax = fig.subplots()
    sp = np.asarray(maps["speeds_rpm"], float)
    n = len(maps["Vdc_V"])
    for a, V in enumerate(maps["Vdc_V"]):
        col = _CHG_CMAP(0.15 + 0.7 * (a / max(1, n - 1)))
        tmax = np.array([np.nan if v is None else v for v in maps["T_max_Nm"][a]], float)
        tmin = np.array([np.nan if v is None else v for v in maps["T_min_Nm"][a]], float)
        ax.plot(sp, tmax, color=col, lw=1.6, marker="o", ms=3, label=f"{V:g} V")
        ax.plot(sp, tmin, color=col, lw=1.6, ls="--", marker="o", ms=3)
    ax.axhline(0, color=t_["grid"], lw=0.8)
    ax.set_xlabel(tr("속도 [rpm]", "speed [rpm]"))
    ax.set_ylabel(tr("최대 축 토크 [N·m]", "full-load shaft torque [N·m]"))
    ax.legend(fontsize=7, loc="upper right", title="Vdc", title_fontsize=7)
    ax.set_title(tr("최대 토크 곡선 — 실선 구동, 점선 회생 (FMU는 요청을 이 곡선으로 제한)",
                    "full-load curves — solid motoring, dashed generating (the FMU clamps the request to them)"),
                 fontsize=9)
