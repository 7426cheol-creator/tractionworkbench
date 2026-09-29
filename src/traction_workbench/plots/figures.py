"""Matplotlib figure builders shared by the desktop application and the PDF report.

Every builder clears the given ``matplotlib.figure.Figure`` and draws plain
data produced by ``traction_workbench.viz``; nothing here computes physics.
"""

from __future__ import annotations

import math

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Arc, Circle, FancyArrowPatch, Patch, Rectangle
from matplotlib.ticker import MaxNLocator

from ..i18n import tr
from . import style as S
from .labels import change_kind_label, param_label

PH = ("a", "b", "c")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reset(fig, title: str | None = None):
    fig.clear()
    fig.set_layout_engine("constrained")
    if title:
        fig.suptitle(title, fontsize=11, fontweight="bold", color=S.theme()["fg"])


def _note(ax, text: str, loc: str = "upper left", fontsize: float = 7.5):
    t = S.theme()
    x, y, ha, va = {"upper left": (0.01, 0.98, "left", "top"), "upper right": (0.99, 0.98, "right", "top"),
                    "lower left": (0.01, 0.02, "left", "bottom"),
                    "lower right": (0.99, 0.02, "right", "bottom")}[loc]
    note = ax.text(x, y, text, transform=ax.transAxes, ha=ha, va=va, fontsize=fontsize, color=t["fg"], zorder=20,
                   bbox=dict(boxstyle="round,pad=0.35", fc=t["panel"], ec=t["grid"], alpha=0.92))
    note.set_in_layout(False)          # a note sits inside its axes: it never takes the plot's width (see fit_texts)
    note._twb_note = text


def _wrapped(text: str, width: int = 90, max_lines: int = 4) -> str:
    """Long claim text as a compact note: one clause per line, wrapped (the page's result table has the full text)."""
    import textwrap
    lines = []
    for part in (q for q in text.split("; ") if q):
        lines += textwrap.wrap(part, width) or [part]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] += " …"
    return "\n".join(lines)


def _side_title(ax, text: str, fontsize: float = 9, max_lines: int = 4) -> None:
    """Title of one of several side-by-side axes.  ``fit_texts`` wraps it (and the axes' legend labels) to the axes'
    laid-out width: constrained layout ignores the width of titles, so a long one would run into its neighbour or off
    the figure on a small window or in English."""
    ax._twb_side_title = (text, fontsize, max_lines)
    ax.set_title(text, fontsize=fontsize)


def _wrap_to(text: str, room: float, width, max_lines: int) -> str:
    """The widest wrap of ``text`` whose lines fit ``room`` px (the narrowest within ``max_lines`` lines per original
    line when none does); line breaks already in the text are kept."""
    import textwrap
    if width(text) <= room:
        return text
    parts = text.split("\n")
    best = text
    for n in range(max(len(p) for p in parts) - 1, 5, -1):
        lines = [ln for p in parts for ln in (textwrap.wrap(p, n, break_long_words=False) or [p])]
        if len(lines) > max_lines * len(parts):
            break
        best = "\n".join(lines)
        if width(best) <= room:
            break
    return best


def fit_texts(fig) -> None:
    """Fit a drawn figure's words to its current size: the figure title wraps to the figure width, and every side
    title (``_side_title``) with its legend labels wraps to its axes' width, the axes placed as if the legends fitted
    (a legend wider than its axes would otherwise squeeze the plot to a sliver).  Called after drawing and whenever
    the canvas is resized; the raw texts are kept, so each fit starts from them.  Presentation only: a failure leaves
    the figure as drawn."""
    import matplotlib as mpl
    from matplotlib.font_manager import FontProperties
    try:
        get = getattr(fig.canvas, "get_renderer", None)
        renderer = get() if get is not None else None
        if renderer is None:
            return
        px = fig.get_figwidth() * fig.dpi

        def measure(prop):
            return lambda s: max(renderer.get_text_width_height_descent(ln, prop, ismath=False)[0]
                                 for ln in s.split("\n"))

        st = getattr(fig, "_suptitle", None)
        if st is not None and st.get_text():
            raw = getattr(st, "_twb_raw", None) or st.get_text()
            st._twb_raw = raw
            st.set_text(_wrap_to(raw, px - 12, measure(st.get_fontproperties()), 3))
        axes = [a for a in fig.axes if getattr(a, "_twb_side_title", None)]
        if not axes:
            return
        legends = [(lg, lg.get_in_layout()) for lg in (a.get_legend() for a in axes) if lg is not None]
        try:
            for lg, _was in legends:
                lg.set_in_layout(False)
            engine = fig.get_layout_engine()
            if engine is not None:
                engine.execute(fig)                        # the axes where the next draw puts them
        finally:
            for lg, was in legends:
                lg.set_in_layout(was)
        for ax in axes:
            text, fontsize, max_lines = ax._twb_side_title
            room = ax.get_position().width * px
            prop = FontProperties(size=fontsize, weight=mpl.rcParams["axes.titleweight"])
            ax.set_title(_wrap_to(text, room, measure(prop), max_lines), fontsize=fontsize)
            for t in ax.texts:                             # notes inside the axes (``_note``)
                raw = getattr(t, "_twb_note", None)
                if raw and "$" not in raw:
                    pad = 1.6 * t.get_fontsize() * fig.dpi / 72
                    t.set_text(_wrap_to(raw, room - pad, measure(t.get_fontproperties()), 3))
            lg = ax.get_legend()
            if lg is None:
                continue
            for t in lg.get_texts():
                raw = getattr(t, "_twb_raw", None) or t.get_text()
                t._twb_raw = raw
                if "$" in raw:                             # math text keeps its one line
                    continue
                pad = 4.3 * t.get_fontsize() * fig.dpi / 72    # handle, gaps and frame of one legend row
                t.set_text(_wrap_to(raw, room - pad, measure(t.get_fontproperties()), 3))
    except Exception:  # noqa: BLE001 - presentation only
        pass


def _discharge_summary(res: dict, width: int = 60) -> str:
    """Short plot note for a discharge result; the full claim detail is in the page's result table."""
    c = res["claim"]
    lines = [tr(f"{c['status']}: RC 도달 {res['t_reach_s']:.4g} s / 허용 {res['t_target_s']:g} s",
                f"{c['status']}: RC reaches the target in {res['t_reach_s']:.4g} s / {res['t_target_s']:g} s")]
    if res.get("rectification_risk"):
        lines.append(tr(f"정류 위험: 역기전력 {res['back_emf_ll_peak_V']:.4g} V > {res['Vf_V']:g} V → RC 시간은 하한",
                        f"rectification risk: back-EMF {res['back_emf_ll_peak_V']:.4g} V > {res['Vf_V']:g} V"
                        f" → the RC time is a lower bound"))
        est = res.get("rectified_link_screening")
        if est:
            lines.append(tr(f"링크 유지 전압 ≈ {est['V_dc_V']:.4g} V (스크리닝 추정, 한계 아님)",
                            f"link held near {est['V_dc_V']:.4g} V (screening estimate, not a bound)"))
    elif c["status"] != "FEASIBLE":
        rest = "; ".join(c["detail"].split("; ")[1:])
        if rest:
            lines += _wrapped(rest, width, 2).split("\n")
    return "\n".join(lines)


def _runs(mask: np.ndarray):
    """Contiguous True runs as (start, stop) index pairs (stop inclusive)."""
    idx = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(int), [0]])))
    return list(zip(idx[::2], idx[1::2] - 1))


def _span_edges(x: np.ndarray, a: int, b: int) -> tuple[float, float]:
    lo = x[a] - (0.5 * (x[a] - x[a - 1]) if a > 0 else 0.0)
    hi = x[b] + (0.5 * (x[b + 1] - x[b]) if b + 1 < x.size else 0.0)
    return lo, hi


def _shade_status(ax, x, status, alpha=0.13):
    for code in (1, 2, 3):
        for a, b in _runs(np.asarray(status) == code):
            lo, hi = _span_edges(np.asarray(x), a, b)
            ax.axvspan(lo, hi, color=S.STATUS[code], alpha=alpha, lw=0, zorder=0)


def status_handles():
    return [Patch(fc=S.STATUS[1], alpha=0.35, label=tr("DC 한계 위반", "DC limit violated")),
            Patch(fc=S.STATUS[2], alpha=0.35, label=tr("해 없음 (증명)", "no solution (proven)")),
            Patch(fc=S.STATUS[3], alpha=0.35, label=tr("미확정", "not established"))]


def _arrow(ax, p0, p1, color, lw=2.0, ls="-", z=5, label=None, alpha=1.0):
    a = FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=12, color=color, lw=lw, linestyle=ls,
                        zorder=z, alpha=alpha, shrinkA=0, shrinkB=0)
    ax.add_patch(a)
    if label:
        ax.annotate(label, xy=p1, xytext=(4, 4), textcoords="offset points", fontsize=8, color=color, zorder=z + 1)
    return a


def _nice_levels(lo: float, hi: float, n: int = 12) -> np.ndarray:
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        return np.array([])
    return MaxNLocator(nbins=n, steps=[1, 2, 2.5, 5, 10]).tick_values(lo, hi)


def _fmt_kw(w):
    return "-" if w is None or not np.isfinite(w) else f"{w / 1e3:.2f} kW"


def speed_label():
    return tr("회전속도 n [rpm]", "speed n [rpm]")


def torque_label():
    return tr("축 토크 T [N·m]", "shaft torque T [N·m]")


# ---------------------------------------------------------------------------
# operating point
# ---------------------------------------------------------------------------

def fig_waveforms(fig, w: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    axs = fig.subplots(4, 1, sharex=True)
    x = w["x"]
    ax = axs[0]
    for k in range(3):
        ax.plot(x, w["i_abc"][k], color=S.PHASE[k], label=f"$i_{PH[k]}$")
    lim = w.get("current_limit_A")
    if lim:
        for sgn in (1, -1):
            ax.axhline(sgn * lim, color=S.GROUP["CURRENT"], ls="--", lw=1)
        ax.plot([], [], color=S.GROUP["CURRENT"], ls="--", lw=1, label=tr("전류 한계 (기본파 peak)", "current limit (fundamental peak)"))
    ax.set_ylabel(tr("상전류 [A]", "phase current [A]"))
    ax.legend(loc="upper right", ncol=4, fontsize=7)
    _note(ax, f"Î = {w['i_peak_A']:.1f} A  ({w['i_rms_A']:.1f} A rms)\n"
              + (f"f_e = {w['f_e_Hz']:.1f} Hz" + (tr(" · 역회전 (a-c-b)", " · reverse (a-c-b)") if w["reverse_rotation"] else "")
                 if not w["standstill"] else tr("정지: 전기각에 따른 DC 분포", "standstill: DC split by electrical angle")))
    ax = axs[1]
    for k in range(3):
        ax.plot(x, w["v_abc"][k], color=S.PHASE[k], label=f"$v_{PH[k]}$")
    ax.plot(x, w["v_ll"][0], color=t["muted"], ls="--", lw=1.1, label="$v_{ab}$")
    for sgn in (1, -1):
        ax.axhline(sgn * w["budget_V"], color=S.GROUP["VOLTAGE"], ls=":", lw=1.2)
    ax.plot([], [], color=S.GROUP["VOLTAGE"], ls=":", lw=1.2, label=tr("전압 명령 예산 (상 peak)", "command budget (phase peak)"))
    ax.set_ylabel(tr("전압 [V]", "voltage [V]"))
    ax.legend(loc="upper right", ncol=5, fontsize=7)
    _note(ax, f"V̂_ph = {w['v_peak_V']:.1f} V · V_LL = {w['v_ll_rms_V']:.1f} V rms (V̂_LL {w['v_ll_peak_V']:.1f} V)")
    ax = axs[2]
    lo, hi = w["duty_reserve_band"]
    if lo > 0:
        ax.axhspan(0.0, lo, color=S.GROUP["VOLTAGE"], alpha=0.12, lw=0)
        ax.axhspan(hi, 1.0, color=S.GROUP["VOLTAGE"], alpha=0.12, lw=0)
    for k in range(3):
        ax.plot(x, w["duty_abc"][k], color=S.PHASE[k], label=f"$d_{PH[k]}$")
    ax.plot(x, 0.5 + w["zero_sequence_V"] / w["Vdc_V"], color=t["muted"], lw=0.9, ls="-.",
            label=tr("영상분 (min-max)", "zero sequence (min-max)"))
    for yv in (0.0, 1.0):
        ax.axhline(yv, color=t["fg"], lw=0.8)
    ax.set_ylim(-0.04, 1.28)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_ylabel(tr("듀티 [-]", "duty [-]"))
    ax.legend(loc="upper right", ncol=4, fontsize=7)
    _note(ax, tr(f"변조율 m = V̂_cmd/(Vdc/√3) = {w['m_linear']:.3f} (1 = 선형 SVPWM 한계)\n"
                 f"듀티 범위 {w['duty_extremes'][0]:.3f}–{w['duty_extremes'][1]:.3f} · 음영 = 전압 reserve",
                 f"m = V̂_cmd/(Vdc/√3) = {w['m_linear']:.3f} (1 = linear SVPWM limit)\n"
                 f"duty range {w['duty_extremes'][0]:.3f}–{w['duty_extremes'][1]:.3f} · shaded = voltage reserve"))
    ax = axs[3]
    for k in range(3):
        ax.plot(x, w["psi_abc"][k], color=S.PHASE[k], label=f"$\\psi_{PH[k]}$")
    ax.set_ylabel(tr("쇄교자속 [Wb]", "flux linkage [Wb]"))
    ax.legend(loc="upper right", ncol=3, fontsize=7)
    _note(ax, tr(f"p(t) = Σ v·i = {w['Pac_W'] / 1e3:.3f} kW (평형 3상: 일정 = P_ac)\n평균값 모델: 스위칭 리플·데드타임 미포함",
                 f"p(t) = Σ v·i = {w['Pac_W'] / 1e3:.3f} kW (balanced: constant = P_ac)\naverage model: no switching ripple or dead time"),
          loc="lower left")
    axs[-1].set_xlabel(tr("시간 [ms]", "time [ms]") if w["x_kind"] == "time" else tr("전기각 [deg]", "electrical angle [deg]"))
    axs[-1].set_xlim(x[0], x[-1])


def fig_phasor(fig, ph: dict, hx: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2)
    v = np.array(ph["v"])
    rsi = np.array(ph["Rs_i"])
    jw = np.array(ph["jw_psi"])
    cand = [ph["budget_V"], ph["ceiling_V"], float(np.hypot(*v)), float(np.hypot(*jw))]
    if ph["e0"] is not None:
        cand.append(float(np.hypot(*ph["e0"])))
    R = 1.15 * max(cand)
    ax1.add_patch(Circle((0, 0), ph["budget_V"], fill=False, ls=":", ec=S.GROUP["VOLTAGE"], lw=1.3))
    ax1.add_patch(Circle((0, 0), ph["ceiling_V"], fill=False, ls="--", ec=S.GROUP["VOLTAGE"], lw=0.8, alpha=0.6))
    ax1.axhline(0, color=t["muted"], lw=0.8)
    ax1.axvline(0, color=t["muted"], lw=0.8)
    labels = {"e0": "$e_0 = \\omega_e\\psi_{PM}$", "jwLd_id": "$\\omega_e L_d i_d$", "wLq_iq": "$-\\omega_e L_q i_q$",
              "Rs_i": "$R_s i$", "jw_psi": "$j\\omega_e\\psi$"}
    colors = {"e0": t["muted"], "jwLd_id": S.GROUP["VOLTAGE"], "wLq_iq": "#17becf", "Rs_i": "#8c564b", "jw_psi": "#17becf"}
    p0 = np.zeros(2)
    for key, vec in ph["chain"]:
        vec = np.array(vec)
        if np.hypot(*vec) < 1e-9:
            continue
        _arrow(ax1, tuple(p0), tuple(p0 + vec), colors[key], lw=3.2 if key == "e0" else 1.8, alpha=0.45 if key == "e0" else 1.0,
               z=4 if key == "e0" else 6, label=labels[key] if np.hypot(*vec) > 0.04 * R else None)
        p0 = p0 + vec
    _arrow(ax1, (0, 0), tuple(v), S.ACCENT, lw=2.8, label="$v$", z=7)
    i = np.array(ph["i"])
    imag = float(np.hypot(*i))
    if imag > 0:
        sc = 0.7 * R / imag
        _arrow(ax1, (0, 0), tuple(i * sc), S.REQUEST, lw=2.2, label="$i$", z=6)
        if ph.get("phi_deg") is not None:
            a1, a2 = ph["angle_i_deg"], ph["angle_v_deg"]
            lo, hi = (a1, a2) if ((a2 - a1) % 360) <= 180 else (a2, a1)
            ax1.add_patch(Arc((0, 0), 0.5 * R, 0.5 * R, theta1=lo, theta2=hi if hi >= lo else hi + 360,
                              color=t["fg"], lw=1))
    ax1.set_xlim(-R, R)
    ax1.set_ylim(-R, R)
    ax1.set_aspect("equal")
    ax1.set_xlabel(tr("d축 [V]", "d axis [V]"))
    ax1.set_ylabel(tr("q축 [V]", "q axis [V]"))
    ax1.set_title(tr("dq 벡터도 (회전자 기준)", "dq phasor diagram (rotor frame)"))
    lines = [f"|v| = {np.hypot(*v):.1f} V · |i| = {imag:.1f} A"]
    if ph.get("phi_deg") is not None:
        lines.append(f"φ = {ph['phi_deg']:.2f}° · PF = cos φ = {ph['power_factor']:.4f}")
    if imag > 0:
        lines.append(tr(f"전류 화살표: 방향만 (척도 {0.7 * R / imag:.3g} V/A)", f"current arrow: direction (scale {0.7 * R / imag:.3g} V/A)"))
    if ph.get("torque_split_Nm"):
        ts = ph["torque_split_Nm"]
        lines.append(tr(f"토크 분해: 자석 {ts['magnet']:.1f} + 릴럭턴스 {ts['reluctance']:.1f} N·m",
                        f"torque split: magnet {ts['magnet']:.1f} + reluctance {ts['reluctance']:.1f} N·m"))
    _note(ax1, "\n".join(lines), loc="lower left")
    # space-vector hexagon
    hxx, hxy = hx["hex_xy"]
    ax2.fill(hxx, hxy, color=t["muted"], alpha=0.07)
    ax2.plot(hxx, hxy, color=t["muted"], lw=1.2)
    r6 = 2.0 / 3.0 * hx["linear_limit_V"] * math.sqrt(3.0)
    for kk, lab in enumerate(hx["vertex_labels"]):
        a = math.radians(60 * kk)
        ax2.text(1.14 * r6 * math.cos(a), 1.12 * r6 * math.sin(a), lab, ha="left" if kk == 0 else ("right" if kk == 3 else "center"),
                 va="center", fontsize=7, color=t["muted"])
    for rad, ls, lw, col, lab in ((hx["linear_limit_V"], "--", 1.0, t["muted"], tr("선형 한계 Vdc/√3", "linear limit Vdc/√3")),
                                  (hx["budget_V"], ":", 1.4, S.GROUP["VOLTAGE"], tr("명령 예산 (1−r_v)Vdc/√3", "budget (1−r_v)Vdc/√3")),
                                  (hx["command_V"], "-", 1.6, S.ACCENT, tr("명령 전압 궤적 |v_cmd|", "command locus |v_cmd|"))):
        ax2.add_patch(Circle((0, 0), rad, fill=False, ls=ls, lw=lw, ec=col))
        ax2.plot([], [], ls=ls, lw=lw, color=col, label=f"{lab} = {rad:.1f} V")
    _arrow(ax2, (0, 0), tuple(hx["v_alpha_beta"]), S.ACCENT, lw=2.4, label="$v_{\\alpha\\beta}$")
    ia = np.array(hx["i_alpha_beta"])
    if np.hypot(*ia) > 0:
        _arrow(ax2, (0, 0), tuple(ia * 0.6 * hx["linear_limit_V"] / np.hypot(*ia)), S.REQUEST, lw=1.8,
               label="$i_{\\alpha\\beta}$")
    L = 1.3 * r6
    ax2.set_xlim(-L, L)
    ax2.set_ylim(-L, L)
    ax2.set_aspect("equal")
    ax2.set_xlabel("α [V]")
    ax2.set_ylabel("β [V]")
    ax2.set_title(tr(f"공간벡터 육각형 (θ = {hx['theta0_deg']:.0f}° 스냅샷)", f"space-vector hexagon (θ = {hx['theta0_deg']:.0f}°)"))
    ax2.legend(loc="lower center", fontsize=7, bbox_to_anchor=(0.5, -0.02))


def fig_power_constraints(fig, chain: list | None, rows: list, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    gs = fig.add_gridspec(1, 2, width_ratios=[1.05, 1.0])
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])
    if chain:
        names = {"P_dc": "$P_{dc}$", "P_inv": tr("인버터 손실", "inverter loss"), "P_ac": "$P_{ac}$",
                 "P_cu": tr("동손", "copper loss"), "P_em": "$T_e\\omega_m$", "P_rot": tr("기계·회전 손실", "rotational loss"),
                 "P_shaft": "$P_{shaft}$"}
        prev = 0.0
        for i, r in enumerate(chain):
            val = r["value_W"] / 1e3
            if r["kind"] == "level":
                ax1.bar(i, val, color=S.ACCENT, alpha=0.85, width=0.6)
                prev = val
                ax1.annotate(f"{val:.2f}", (i, val), xytext=(0, 3 if val >= 0 else -10), textcoords="offset points",
                             ha="center", fontsize=7.5, color=t["fg"])
            else:
                lo = min(prev, prev + val)
                ax1.bar(i, abs(val), bottom=lo, color="#cf222e", alpha=0.8, width=0.6)
                ax1.annotate(f"{val:+.2f}", (i, max(prev, prev + val)), xytext=(0, 3), textcoords="offset points",
                             ha="center", fontsize=7.5, color="#cf222e")
                prev = prev + val
        ax1.set_xticks(range(len(chain)))
        ax1.set_xticklabels([names[r["key"]] for r in chain], rotation=30, ha="right", fontsize=7.5)
        ax1.axhline(0, color=t["fg"], lw=0.8)
        loss = -sum(r["value_W"] for r in chain if r["kind"] == "loss")
        pdc, psh = chain[0]["value_W"], chain[-1]["value_W"]
        eta = (psh / pdc if psh > 0 and pdc > 0 else (pdc / psh if psh < 0 and pdc < 0 else None))
        df = "P_shaft/P_dc" if (psh > 0 and pdc > 0) else "|P_dc|/|P_shaft|"
        _note(ax1, tr(f"손실 합계 {loss / 1e3:.2f} kW (기본파 모델)" + (f" · η_inv+motor = {100 * eta:.2f}% ({df})" if eta
                                                                   else " · η_inv+motor N/A"),
                      f"total loss {loss / 1e3:.2f} kW (fundamental model)" + (f" · η_inv+motor = {100 * eta:.2f}% ({df})"
                                                                                if eta else " · η_inv+motor N/A")),
              loc="upper right")
        ax1.set_ylabel(tr("전력 [kW]", "power [kW]"))
        _side_title(ax1, tr("전력 흐름 (DC → 축, 손실 차감)", "power chain (DC → shaft, losses subtracted)"))
    else:
        ax1.text(0.5, 0.5, tr("손실 모델 없음: DC/축 전력 미정의", "loss model missing: DC/shaft power undefined"),
                 ha="center", va="center", transform=ax1.transAxes)
        ax1.set_axis_off()
    items = [r for r in rows if r["utilization"] is not None and (r["group"] != "DOMAIN" or r["state"] != "SATISFIED"
                                                                  or r["name"] in ("ID_MIN", "SPEED_MAX"))]
    items.sort(key=lambda r: r["utilization"])
    y = np.arange(len(items))
    util = np.array([100.0 * r["utilization"] for r in items])
    ax2.barh(y, util, color=[S.STATE[r["state"]] for r in items], alpha=0.9, height=0.6)
    ax2.axvline(100.0, color=t["fg"], lw=1)
    for yi, r, u in zip(y, items, util):
        sl = r["slack"]
        txt = f"{u:.1f}%"
        if sl is not None:
            unit = r["unit"].split(" ")[0]
            val = f"{sl / 1e3:.4g} kW" if unit == "W" else f"{sl:.4g} {unit}"
            txt += tr(f"  여유 {val}", f"  slack {val}")
        ax2.text(u + 2, yi, txt, va="center", fontsize=7, color=t["fg"], clip_on=True).set_in_layout(False)
    ax2.set_yticks(y)
    ax2.set_yticklabels([r["name"] for r in items], fontsize=7.5)
    ax2.set_xlim(0, max(135.0, (util.max() if util.size else 0) + 45))
    ax2.set_xlabel(tr("사용률 = 요구/한계 [%]", "utilisation = demand/limit [%]"))
    _side_title(ax2, tr("제약 사용률 (고유 단위 여유)", "constraint utilisation (native-unit slack)"))
    ax2.legend(handles=[Patch(fc=S.STATE[k], label=lab) for k, lab in (
        ("SATISFIED", tr("만족", "satisfied")), ("ACTIVE", tr("활성(경계)", "active (boundary)")),
        ("VIOLATED", tr("위반", "violated")))], loc="lower right", fontsize=7)


# ---------------------------------------------------------------------------
# id-iq plane
# ---------------------------------------------------------------------------

def draw_idiq(ax, pl: dict, traj: dict | None = None, legend: bool = True, torque_levels: bool = True):
    t = S.theme()
    X, Y = pl["X"], pl["Y"]
    handles = []
    ax.contourf(X, Y, pl["electrical_ok"].astype(float), levels=[0.5, 1.5], colors=[t["feasible"]], alpha=0.7)
    handles.append(Patch(fc=t["feasible"], label=tr("전기적 가능 (V·I·도메인)", "electrically feasible (V, I, domain)")))
    if pl.get("all_ok") is not None and pl["all_ok"].any():
        ax.contourf(X, Y, pl["all_ok"].astype(float), levels=[0.5, 1.5], colors=[t["feasible_all"]], alpha=0.55)
        handles.append(Patch(fc=t["feasible_all"], alpha=0.55, label=tr("모든 한계 만족 (DC 포함)", "all limits incl. DC")))
    elif pl.get("dc_grid_note"):
        handles.append(Patch(fc="none", ec="none", label=tr("DC 한계: 격자 미평가 (모듈 손실은 점별) — 정책점에서 판정",
                                                           "DC limits: not evaluated on the grid (pointwise module "
                                                           "loss) - judged at the policy point")))
    if not pl["covered"].all():
        ax.contourf(X, Y, (~pl["covered"]).astype(float), levels=[0.5, 1.5], colors="none", hatches=["xx"])
        handles.append(Patch(fc="none", ec=t["muted"], hatch="xx", label=tr("모델 데이터 없음", "no model data")))
    T = pl["T"]
    if torque_levels:
        inside = np.hypot(X, Y) <= pl["Imax_A"] * 1.05
        tm = np.nanmax(np.abs(np.where(inside, T, np.nan))) if np.isfinite(T).any() else 0.0
        lv = _nice_levels(-tm, tm, 14)
        lv = lv[np.abs(lv) > 1e-9]
        if lv.size:
            cs = ax.contour(X, Y, T, levels=lv, colors=t["muted"], linewidths=0.55, alpha=0.8)
            ax.clabel(cs, fmt=lambda v: f"{v:.0f}", fontsize=6.5, inline=True)
            handles.append(Line2D([], [], color=t["muted"], lw=0.6, label=tr("등토크선 [N·m]", "constant torque [N·m]")))
    vcol = S.GROUP["VOLTAGE"]
    if np.isfinite(pl["V"]).any():
        ax.contour(X, Y, pl["V"], levels=[pl["budget_V"]], colors=[vcol], linewidths=2.0)
        ax.contour(X, Y, pl["V"], levels=[pl["ceiling_V"]], colors=[vcol], linewidths=0.9, linestyles="--")
    handles.append(Line2D([], [], color=vcol, lw=2, label=tr(f"전압 한계 V_b = {pl['budget_V']:.1f} V @ {pl['speed_rpm']:.0f} rpm",
                                                          f"voltage limit V_b = {pl['budget_V']:.1f} V @ {pl['speed_rpm']:.0f} rpm")))
    handles.append(Line2D([], [], color=vcol, lw=0.9, ls="--", label=tr("하드웨어 상한 Vdc/√3", "hardware ceiling Vdc/√3")))
    for s, V, vb in pl.get("ellipses", ()):
        if np.isfinite(V).any():
            cs = ax.contour(X, Y, V, levels=[vb], colors=[vcol], linewidths=0.8, alpha=0.45)
            ax.clabel(cs, fmt=lambda _v, s=s: f"{s:.0f} rpm", fontsize=6.5, inline=True)
    ax.add_patch(Circle((0, 0), pl["Imax_A"], fill=False, ec=S.GROUP["CURRENT"], lw=2))
    handles.append(Line2D([], [], color=S.GROUP["CURRENT"], lw=2, label=tr(f"전류 한계 {pl['Imax_A']:.0f} A", f"current limit {pl['Imax_A']:.0f} A")))
    d0, d1 = pl["domain_id_A"]
    q0, q1 = pl["domain_iq_A"]
    ax.add_patch(Rectangle((d0, q0), d1 - d0, q1 - q0, fill=False, ls=":", ec=t["fg"], lw=1.1))
    handles.append(Line2D([], [], color=t["fg"], lw=1.1, ls=":", label=tr("선언된 운전 도메인", "declared domain")))
    if pl.get("Pdc") is not None:
        if pl["P_dis_eff_W"] is not None:
            ax.contour(X, Y, pl["Pdc"], levels=[pl["P_dis_eff_W"]], colors=[S.GROUP["DISCHARGE_SOURCE"]], linewidths=1.5,
                       linestyles="-.")
            handles.append(Line2D([], [], color=S.GROUP["DISCHARGE_SOURCE"], lw=1.5, ls="-.",
                                  label=tr(f"DC 방전 한계 {pl['P_dis_eff_W'] / 1e3:.0f} kW", f"DC discharge limit {pl['P_dis_eff_W'] / 1e3:.0f} kW")))
        if pl["P_chg_eff_W"] is not None:
            ax.contour(X, Y, pl["Pdc"], levels=[-pl["P_chg_eff_W"]], colors=[S.GROUP["CHARGE_SOURCE"]], linewidths=1.5,
                       linestyles="-.")
            handles.append(Line2D([], [], color=S.GROUP["CHARGE_SOURCE"], lw=1.5, ls="-.",
                                  label=tr(f"DC 충전 한계 −{pl['P_chg_eff_W'] / 1e3:.0f} kW", f"DC charge limit −{pl['P_chg_eff_W'] / 1e3:.0f} kW")))
    for tag in ("motoring", "braking"):
        xs, ys = pl["mtpa"][tag]
        if len(xs):
            ax.plot(xs, ys, color=S.MTPA, lw=1.6, alpha=0.9)
    handles.append(Line2D([], [], color=S.MTPA, lw=1.6, label="MTPA"))
    if pl.get("mtpv"):
        for tag in ("motoring", "braking"):
            xs, ys = pl["mtpv"][tag]
            ax.plot(xs, ys, color=S.MTPV, ls=":", lw=1.6)
        handles.append(Line2D([], [], color=S.MTPV, ls=":", lw=1.6, label=tr("MTPV (Rs 무시, 참고)", "MTPV (Rs neglected, guide)")))
    if pl.get("T_request_Nm") is not None and np.isfinite(T).any():
        ax.contour(X, Y, T, levels=[pl["T_request_Nm"]], colors=[S.REQUEST], linewidths=2.4)
        handles.append(Line2D([], [], color=S.REQUEST, lw=2.4, label=tr(f"요구 토크 {pl['T_request_Nm']:g} N·m", f"requested {pl['T_request_Nm']:g} N·m")))
    if pl.get("policy_point") is not None:
        ax.plot(*pl["policy_point"], marker="*", ms=17, mfc=S.REQUEST, mec=t["fg"], mew=0.8, ls="none", zorder=12)
        handles.append(Line2D([], [], marker="*", ms=12, mfc=S.REQUEST, mec=t["fg"], ls="none",
                              label=pl.get("point_label") or tr("최소전류 정책점", "minimum-current policy point")))
    if traj is not None:
        tid, tiq, st = traj["id_A"], traj["iq_A"], traj["status"]
        ok = np.isfinite(tid)
        ax.plot(tid[ok], tiq[ok], color=t["fg"], lw=1.0, alpha=0.7, zorder=9)
        for code, mk in ((0, "o"), (1, "s"), (3, "^")):
            m = ok & (st == code)
            if m.any():
                ax.plot(tid[m], tiq[m], marker=mk, ms=3.8, ls="none", color=S.STATUS[code], zorder=10)
        fw = ok & traj["voltage_active"] & (st == 0)
        if fw.any():
            ax.plot(tid[fw], tiq[fw], marker="o", ms=6, ls="none", mfc="none", mec=S.GROUP["VOLTAGE"], mew=1.0, zorder=11)
        handles.append(Line2D([], [], color=S.STATUS[0], marker="o", ms=4, ls="-", lw=1,
                              label=tr("운전점 궤적 (정책점)", "operating trajectory (policy points)")))
        handles.append(Line2D([], [], marker="o", ms=6, mfc="none", mec=S.GROUP["VOLTAGE"], ls="none",
                              label=tr("전압 제한 운전 (약계자)", "voltage-limited (field weakening)")))
        if (st == 1).any():
            handles.append(Line2D([], [], marker="s", ms=4, color=S.STATUS[1], ls="none", label=tr("DC 한계 위반점", "DC-limit violating")))
    v0, v1, w0, w1 = pl.get("view") or (pl["x"][0], pl["x"][-1], pl["y"][0], pl["y"][-1])
    ax.set_xlim(v0, v1)
    ax.set_ylim(w0, w1)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("$i_d$ [A]")
    ax.set_ylabel("$i_q$ [A]")
    if legend:
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=7, borderaxespad=0)
    return handles


def fig_idiq(fig, pl: dict, traj: dict | None = None, title: str | None = None):
    _reset(fig, title)
    ax = fig.subplots()
    draw_idiq(ax, pl, traj)
    T_is = tr("축 토크", "shaft torque") if pl["T_is_shaft"] else tr("전자기 토크", "electromagnetic torque")
    ax.set_title(tr(f"id–iq 제약 지도 · n = {pl['speed_rpm']:.0f} rpm · Vdc = {pl['Vdc_V']:.0f} V · 등토크선 = {T_is}",
                    f"id–iq constraint map · n = {pl['speed_rpm']:.0f} rpm · Vdc = {pl['Vdc_V']:.0f} V · contours = {T_is}"),
                 fontsize=9)


# ---------------------------------------------------------------------------
# sweeps / envelopes
# ---------------------------------------------------------------------------

def fig_sweep(fig, sw: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    axs = fig.subplots(2, 2, sharex=True)
    x = sw["x"]
    st = sw["status"]
    xl = torque_label() if sw["x_kind"] == "torque" else speed_label()
    for ax in axs.flat:
        _shade_status(ax, x, st)
        fw = np.asarray(sw["voltage_active"]) & (st == 0)
        for a, b in _runs(fw):
            lo, hi = _span_edges(np.asarray(x), a, b)
            ax.axvspan(lo, hi, color=S.GROUP["VOLTAGE"], alpha=0.07, lw=0, zorder=0)
    ax = axs[0, 0]
    ax.plot(x, sw["id_A"], color=S.PHASE[0], label="$i_d$")
    ax.plot(x, sw["iq_A"], color=S.PHASE[1], label="$i_q$")
    ax.plot(x, sw["I_peak_A"], color=t["fg"], lw=1.8, label="$|i|$ (peak)")
    if sw.get("current_limit_A"):
        ax.axhline(sw["current_limit_A"], color=S.GROUP["CURRENT"], ls="--", lw=1, label=tr("전류 한계", "current limit"))
    ax.set_ylabel(tr("전류 [A]", "current [A]"))
    ax.legend(fontsize=7, loc="best")
    ax = axs[0, 1]
    ax.plot(x, sw["v_cmd_V"], color=S.ACCENT, lw=1.8, label="$|v_{cmd}|$")
    if sw.get("voltage_budget_V"):
        ax.axhline(sw["voltage_budget_V"], color=S.GROUP["VOLTAGE"], ls="--", lw=1.1, label=tr("전압 예산 V_b", "voltage budget V_b"))
    vdc = sw.get("Vdc_V")
    if vdc:
        k = math.sqrt(3.0) / vdc
        sec = ax.secondary_yaxis("right", functions=(lambda v, k=k: v * k, lambda m, k=k: m / k))
        sec.set_ylabel(tr("변조율 m = V̂/(Vdc/√3)", "modulation m = V̂/(Vdc/√3)"), color=t["muted"])
    ax.set_ylabel(tr("명령 전압 [V peak]", "command voltage [V peak]"))
    ax.legend(fontsize=7, loc="best")
    ax = axs[1, 0]
    ax.plot(x, sw["Pshaft_W"] / 1e3, color=S.PHASE[2], label="$P_{shaft}$")
    ax.plot(x, sw["Pdc_W"] / 1e3, color=S.ACCENT, label="$P_{dc}$")
    ax.plot(x, sw["P_loss_W"] / 1e3, color="#cf222e", lw=1.0, label=tr("총 손실 (모든 항 확정 시)", "total loss (all terms known)"))
    if np.any(np.isnan(sw["P_loss_W"]) & np.isfinite(sw["P_loss_known_W"])):
        ax.plot(x, sw["P_loss_known_W"] / 1e3, color="#cf222e", lw=1.0, ls=":",
                label=tr("알려진 손실 소계 (미상 항 제외)", "known loss subtotal (unknown terms excluded)"))
    pdc = sw["Pdc_W"][np.isfinite(sw["Pdc_W"])]
    if sw.get("P_dis_eff_W") is not None and pdc.size and pdc.max() > 0:
        ax.axhline(sw["P_dis_eff_W"] / 1e3, color=S.GROUP["DISCHARGE_SOURCE"], ls="-.", lw=1, label=tr("방전 한계", "discharge limit"))
    if sw.get("P_chg_eff_W") is not None and pdc.size and pdc.min() < 0:
        ax.axhline(-sw["P_chg_eff_W"] / 1e3, color=S.GROUP["CHARGE_SOURCE"], ls="-.", lw=1, label=tr("충전 한계", "charge limit"))
    ax.set_ylabel(tr("전력 [kW]", "power [kW]"))
    ax.set_xlabel(xl)
    ax.legend(fontsize=7, loc="best")
    ax = axs[1, 1]
    ax.plot(x, 100 * sw["eta"], color=S.PHASE[2], lw=1.8, label=tr("η 인버터+모터 (DC↔축)", "η inverter+motor (DC↔shaft)"))
    ax.plot(x, 100 * sw["eta_motor"], color=S.PHASE[0], lw=1.0, label=tr("η 모터 (AC↔축)", "η motor (AC↔shaft)"))
    ax.plot(x, 100 * sw["eta_inverter"], color=S.PHASE[1], lw=1.0, label=tr("η 인버터 (DC↔AC)", "η inverter (DC↔AC)"))
    ax.plot(x, 100 * np.abs(sw["pf"]), color=t["muted"], ls="--", lw=1.0, label=tr("|역률| × 100", "|PF| × 100"))
    ax.set_ylim(0, 102)
    ax.set_ylabel("[%]")
    ax.set_xlabel(xl)
    ax.legend(fontsize=7, loc="lower right")
    handles = [Patch(fc=S.GROUP["VOLTAGE"], alpha=0.2, label=tr("전압 제한(약계자) 구간", "voltage-limited (FW) range"))]
    handles += [h for h, code in zip(status_handles(), (1, 2, 3)) if (st == code).any()]
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles), fontsize=7.5)


_EXTRA = ("#a6611a", "#018571", "#e7298a", "#66a61e", "#7570b3", "#e6ab02")
_ACTIVE_COLORS = {
    "CURRENT": S.CONSTRAINT["CURRENT"], "VOLTAGE": S.CONSTRAINT["VOLTAGE"], "CURRENT+VOLTAGE": "#9c179e",
    "VOLTAGE+DC_DISCHARGE_POWER": "#a6611a", "VOLTAGE+DC_CHARGE_POWER": "#018571",
    "CURRENT+DC_DISCHARGE_POWER": "#e7298a", "CURRENT+DC_CHARGE_POWER": "#66a61e",
    "DC_DISCHARGE_POWER": S.CONSTRAINT["DC_DISCHARGE_POWER"], "DC_DISCHARGE_CURRENT": S.CONSTRAINT["DC_DISCHARGE_CURRENT"],
    "DC_CHARGE_POWER": S.CONSTRAINT["DC_CHARGE_POWER"], "DC_CHARGE_CURRENT": S.CONSTRAINT["DC_CHARGE_CURRENT"],
}
_RELEVANT = ("CURRENT", "VOLTAGE", "DC_DISCHARGE_POWER", "DC_DISCHARGE_CURRENT", "DC_CHARGE_POWER", "DC_CHARGE_CURRENT",
             "ID_MIN")


def _active_key(names) -> str:
    s = [n for n in _RELEVANT if n in names]
    if not s:
        return "-"
    dc = [n for n in s if n.startswith("DC_")]
    if dc:
        return dc[0] if len(s) == 1 else "+".join(s)
    return "+".join(s)


def _colored_line(ax, x, y, active, lw=2.6):
    keys = [_active_key(a) for a in active]
    used = {}
    t = S.theme()
    for i in range(len(x) - 1):
        if not (np.isfinite(y[i]) and np.isfinite(y[i + 1])):
            continue
        k = keys[i + 1] if keys[i + 1] != "-" else keys[i]
        if k not in _ACTIVE_COLORS:
            _ACTIVE_COLORS[k] = _EXTRA[len(_ACTIVE_COLORS) % len(_EXTRA)] if k != "-" else t["muted"]
        col = _ACTIVE_COLORS[k]
        used[k] = col
        ax.add_collection(LineCollection([[(x[i], y[i]), (x[i + 1], y[i + 1])]], colors=[col], linewidths=lw, zorder=6))
    return used


def fig_envelope(fig, env: dict, reqs=(), compare=(), title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    x = env["x"]
    mx = env.get("max")
    mn = env.get("min")
    if mx is not None and mn is not None:
        ax.fill_between(x, mn["T_Nm"], mx["T_Nm"], color=t["feasible"], alpha=0.55, lw=0,
                        label=tr("정책 달성 영역 (DC 포함)", "policy region (incl. DC)"))
    used = {}
    for tab in (mx, mn):
        if tab is None:
            continue
        ax.plot(x, tab["electrical_T_Nm"], color=t["muted"], ls="--", lw=1.1)
        used.update(_colored_line(ax, x, tab["T_Nm"], tab["active"]))
    ax.plot([], [], color=t["muted"], ls="--", lw=1.1, label=tr("전기적 한계 (V·I·도메인만)", "electrical limit (V, I, domain only)"))
    for key, col in used.items():
        ax.plot([], [], color=col, lw=2.6, label=tr("정책 경계 · 활성: ", "policy boundary · active: ") + (key if key != "-" else tr("없음", "none")))
    for n_c, (lab, e2) in enumerate(compare):
        col = ("#e377c2", "#17becf", "#bcbd22", "#8c564b")[n_c % 4]
        for tag in ("max", "min"):
            if e2.get(tag) is not None:
                ax.plot(e2["x"], e2[tag]["T_Nm"], lw=1.3, ls=(0, (4, 2)), color=col, label=lab if tag == "max" else None)
    for r in reqs:
        col = S.VERDICT.get(r.get("verdict"), S.REQUEST)
        ax.plot(r["speed_rpm"], r["torque_Nm"], marker="o" if r.get("verdict") == "PASS" else "X", ms=9, color=col,
                mec=t["fg"], mew=0.6, ls="none", zorder=12)
        ax.annotate(f"{r.get('id', '')} {r.get('verdict', '')}", (r["speed_rpm"], r["torque_Nm"]), xytext=(6, 6),
                    textcoords="offset points", fontsize=7.5, color=col, zorder=12)
    ax.axhline(0, color=t["fg"], lw=0.7)
    ax.set_xlim(x[0], x[-1])
    ax.set_xlabel(speed_label())
    ax.set_ylabel(torque_label())
    if mx is not None:
        ax2 = ax.twinx()
        w = x * 2 * math.pi / 60
        ax2.plot(x, mx["T_Nm"] * w / 1e3, color=t["muted"], lw=1.0, ls=":")
        if mn is not None:
            ax2.plot(x, mn["T_Nm"] * w / 1e3, color=t["muted"], lw=1.0, ls=":")
        ax2.set_ylabel(tr("정책 경계의 축 출력 [kW] (점선)", "shaft power on the policy boundary [kW] (dotted)"), color=t["muted"])
        ax2.grid(False)
        ax2.spines["right"].set_visible(True)
        lo, hi = ax.get_ylim()
        wmax = max(w.max(), 1e-9)
        pk = np.nanmax(np.abs(np.concatenate([mx["T_Nm"] * w, (mn["T_Nm"] * w) if mn is not None else [0]]))) / 1e3
        ax2.set_ylim(-1.1 * pk if mn is not None else 0, 1.1 * pk)
        del lo, hi, wmax
    method = env.get("method")
    ax.set_title(tr(f"T–n 성능 곡선 · Vdc = {env['Vdc_V']:.0f} V · " + ("솔버 (정책 capability)" if method == "solver" else "격자 추정 (시각화용)"),
                    f"T–n envelope · Vdc = {env['Vdc_V']:.0f} V · " + ("solver (policy capability)" if method == "solver" else "grid estimate (visualisation)")),
                 fontsize=9)
    ax.legend(loc="upper right", fontsize=7)


MAP_QUANTITIES = {
    "eta": (lambda: tr("η 인버터+모터 [%] (구동 P_shaft/P_dc · 회생 |P_dc|/|P_shaft|)",
                       "η inverter+motor [%] (motoring P_shaft/P_dc · regen |P_dc|/|P_shaft|)"), 100.0),
    "eta_motor": (lambda: tr("η 모터 [%] (구동 P_shaft/P_ac · 회생 |P_ac|/|P_shaft|)",
                             "η motor [%] (motoring P_shaft/P_ac · regen |P_ac|/|P_shaft|)"), 100.0),
    "eta_inverter": (lambda: tr("η 인버터 [%] (구동 P_ac/P_dc · 회생 |P_dc|/|P_ac|)",
                                "η inverter [%] (motoring P_ac/P_dc · regen |P_dc|/|P_ac|)"), 100.0),
    "P_loss_W": (lambda: tr("총 손실 [kW] (모든 손실 항 확정 시)", "total loss [kW] (all loss terms known)"), 1e-3),
    "P_loss_known_W": (lambda: tr("알려진 손실 소계 [kW] (미상 항 제외)", "known loss subtotal [kW] (unknown terms excluded)"),
                       1e-3),
    "Pcu_W": (lambda: tr("동손 [kW]", "copper loss [kW]"), 1e-3),
    "Pinv_W": (lambda: tr("인버터 손실 [kW]", "inverter loss [kW]"), 1e-3),
    "I_rms_A": (lambda: tr("상전류 [A rms]", "phase current [A rms]"), 1.0),
    "m_linear": (lambda: tr("변조율 V̂_cmd/(Vdc/√3) [-]", "modulation V̂_cmd/(Vdc/√3) [-]"), 1.0),
    "pf": (lambda: tr("역률 P_ac/(1.5·V̂·Î) [-]", "power factor P_ac/(1.5·V̂·Î) [-]"), 1.0),
    "id_A": (lambda: "$i_d$ [A]", 1.0),
    "iq_A": (lambda: "$i_q$ [A]", 1.0),
    "Pdc_W": (lambda: tr("DC 전력 [kW]", "DC power [kW]"), 1e-3),
    "Idc_A": (lambda: tr("DC 평균 전류 [A]", "DC average current [A]"), 1.0),
    "v_margin_V": (lambda: tr("전압 여유 [V]", "voltage margin [V]"), 1.0),
}


def fig_map(fig, mp: dict, quantity: str = "eta", env: dict | None = None, reqs=(), title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    label, scale = MAP_QUANTITIES[quantity]
    Z = mp["grids"][quantity] * scale
    sp, tq = mp["speeds"], mp["torques"]
    SP, TQ = np.meshgrid(sp, tq)
    finite = np.isfinite(Z)
    if not finite.any():
        ax.text(0.5, 0.5, tr("표시할 값 없음", "no values"), ha="center", va="center", transform=ax.transAxes)
        return
    zmin, zmax = np.nanmin(Z), np.nanmax(Z)
    if quantity.startswith("eta"):
        base = [50, 60, 70, 75, 80, 84, 86, 88, 90, 91, 92, 93, 94, 95, 96, 97, 97.5, 98, 98.5, 99, 99.5]
        levels = [v for v in base if zmin - 1e-9 <= v < zmax]
        levels = np.array(levels + [zmax + 1e-6])
        if levels.size < 2:
            levels = np.linspace(zmin, zmax + 1e-6, 8)
        extend = "min"
    else:
        levels = _nice_levels(zmin, zmax, 14)
        if levels.size < 2:
            levels = np.linspace(zmin - 1e-9, zmax + 1e-9, 3)
        extend = "both"
    cf = ax.contourf(SP, TQ, Z, levels=levels, cmap=t["cmap"], extend=extend)
    cs = ax.contour(SP, TQ, Z, levels=levels, colors=t["bg"], linewidths=0.5, alpha=0.8)
    ax.clabel(cs, fmt=lambda v: f"{v:.3g}", fontsize=6.5, inline=True)
    cb = fig.colorbar(cf, ax=ax, pad=0.01)
    cb.set_label(label())
    dcm = mp["status"] == 1
    if dcm.any():
        ax.contourf(SP, TQ, dcm.astype(float), levels=[0.5, 1.5], colors="none", hatches=["////"])
    unk = (mp["status"] == 3) & finite
    if unk.any():                   # a value exists but the policy/DC claim is UNKNOWN: not shown as a feasible value
        ax.contourf(SP, TQ, unk.astype(float), levels=[0.5, 1.5], colors="none", hatches=["xx"])
    bs = mp.get("base_speed")
    if bs is not None and np.isfinite(bs["speed_rpm"]).any():
        ax.plot(bs["speed_rpm"], bs["torques"], color=S.GROUP["VOLTAGE"], lw=1.4, ls="--")
    if env is not None:
        for tag in ("max", "min"):
            if env.get(tag) is not None:
                ax.plot(env["x"], env[tag]["T_Nm"], color=t["fg"], lw=1.8)
                ax.plot(env["x"], env[tag]["electrical_T_Nm"], color=t["fg"], lw=0.9, ls="--")
    if quantity.startswith("eta"):
        k = np.nanargmax(np.where(finite, Z, -np.inf))
        i, j = np.unravel_index(k, Z.shape)
        ax.plot(sp[j], tq[i], marker="D", ms=7, mfc="white", mec="black", zorder=12)
        ax.annotate(tr(f"최고 {Z[i, j]:.2f}%", f"peak {Z[i, j]:.2f}%"), (sp[j], tq[i]), xytext=(6, 6),
                    textcoords="offset points", fontsize=7.5, color=t["fg"],
                    bbox=dict(boxstyle="round,pad=0.2", fc=t["panel"], ec=t["grid"], alpha=0.9))
    for r in reqs:
        col = S.VERDICT.get(r.get("verdict"), S.REQUEST)
        ax.plot(r["speed_rpm"], r["torque_Nm"], marker="o" if r.get("verdict") == "PASS" else "X", ms=9, color=col,
                mec="white", mew=0.8, ls="none", zorder=13)
    ax.axhline(0, color=t["fg"], lw=0.6)
    ax.set_xlabel(speed_label())
    ax.set_ylabel(torque_label())
    ax.set_xlim(sp[0], sp[-1])
    ax.set_ylim(tq[0], tq[-1])
    h = [Line2D([], [], color=t["fg"], lw=1.8, label=tr("정책 경계 (DC 포함)", "policy boundary (incl. DC)")),
         Line2D([], [], color=t["fg"], lw=0.9, ls="--", label=tr("전기적 한계", "electrical limit")),
         Line2D([], [], color=S.GROUP["VOLTAGE"], lw=1.4, ls="--", label=tr("기저속도 곡선: 이보다 빠르면 약계자", "base-speed curve: field weakening beyond")),
         Patch(fc="none", ec=t["muted"], hatch="////", label=tr("정책점이 DC 한계 위반", "policy point violates DC limit")),
         Patch(fc="none", ec=t["muted"], hatch="xx", label=tr("판정 UNKNOWN (값은 참고용)", "claim UNKNOWN (value for reference)"))]
    ax.legend(handles=h, loc="upper right", fontsize=7)
    ax.set_title(tr(f"{label()} · Vdc = {mp['Vdc_V']:.0f} V · 최소전류 정책점 기준",
                    f"{label()} · Vdc = {mp['Vdc_V']:.0f} V · minimum-current policy points"), fontsize=9)


# ---------------------------------------------------------------------------
# design / bottleneck
# ---------------------------------------------------------------------------

def fig_capability_vs_parameter(fig, cv: dict, sizing: dict | None = None, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    x, y = cv["values"], cv["capability_Nm"]
    p = cv["parameter"]
    unit = p["unit"]
    ax.plot(x, y, color=S.ACCENT, lw=2, marker=".", ms=5, label=tr("정책 capability (DC 포함)", "policy capability (incl. DC)"))
    T = cv.get("T_request_Nm")
    if T is not None:
        ax.axhline(T, color=S.REQUEST, ls="--", lw=1.4, label=tr(f"요구 {T:g} N·m", f"request {T:g} N·m"))
        good = np.isfinite(y) & (y * cv["direction"] >= T * cv["direction"])
        for a, b in _runs(good):
            lo, hi = _span_edges(x, a, b)
            ax.axvspan(lo, hi, color=t["feasible"], alpha=0.35, lw=0, zorder=0)
    ax.axvline(cv["baseline"], color=t["fg"], lw=1.1, ls=":", label=tr(f"현재값 {cv['baseline']:g} {unit}", f"baseline {cv['baseline']:g} {unit}"))
    if sizing:
        # the evidence level of the sizing result survives the renderer (review R2 D-R2-04): unresolved regions are
        # drawn as such, and an edge is a 'local bracket' only next to an excluded region, else 'smallest witnessed'
        for (lo_r, hi_r), st in ((r["range"], r["status"]) for r in sizing.get("regions", [])):
            if st == "UNKNOWN":
                ax.axvspan(lo_r, hi_r, facecolor="none", edgecolor=t["muted"], hatch="///", lw=0, zorder=0)
        if any(r["status"] == "UNKNOWN" for r in sizing.get("regions", [])):
            ax.plot([], [], color="none", label=tr("미확정 영역 (///)", "unresolved region (///)"))
    if sizing and sizing.get("minimal_feasible_value") is not None:
        mv = sizing["minimal_feasible_value"]
        if sizing.get("minimal_is_bracketed"):
            lab = tr(f"국소 경계 {mv:.6g} {unit} (아래는 표본점에서 배제)", f"local bracket {mv:.6g} {unit} (excluded at the samples below)")
        else:
            lab = tr(f"찾은 최소 가능값 {mv:.6g} {unit} (최소 증명 아님)", f"smallest witnessed {mv:.6g} {unit} (not a proven minimum)")
        ax.axvline(mv, color=S.VERDICT["PASS"], lw=1.6, label=lab)
    ax.set_xlabel(f"{param_label(p['parameter'])} [{unit}] · {change_kind_label(p['change_kind'])}")
    ax.set_ylabel(tr("정책 capability [N·m]", "policy capability [N·m]"))
    ax.legend(loc="best", fontsize=7.5)
    _note(ax, p["meaning"] + "\n" + tr(f"n = {cv['speed_rpm']:.0f} rpm, 기준 Vdc = {cv['Vdc_V']:.0f} V",
                                       f"n = {cv['speed_rpm']:.0f} rpm, base Vdc = {cv['Vdc_V']:.0f} V"), loc="lower right")


def fig_dominance(fig, dom: dict, relax: dict | None = None, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    if relax:
        ax1, ax2 = fig.subplots(1, 2)
    else:
        ax1, ax2 = fig.subplots(), None
    rows = sorted(dom["single"], key=lambda r: r["gain_Nm"])
    y = np.arange(len(rows))
    cols = [S.CONSTRAINT.get(r["constraint"], t["muted"]) if r["classification"] == "limiting" else t["grid"] for r in rows]
    ax1.barh(y, [r["gain_Nm"] for r in rows], color=cols, height=0.6)
    base0 = dom.get("base_policy_capability_Nm")
    tiny = 1e-6 * max(1.0, abs(base0 or 0.0))           # solver noise around zero is "0", not "+-5e-11"
    for yi, r in zip(y, rows):
        g = r["gain_Nm"]
        ax1.text(max(g, 0.0), yi, ("  0 N·m" if abs(g) < tiny else f"  {g:+.3g} N·m") + ("  ★" if r["active_at_base"] else ""),
                 va="center", fontsize=7.5, color=t["fg"])
    ax1.set_yticks(y)
    ax1.set_yticklabels([r["constraint"] for r in rows], fontsize=7.5)
    ax1.set_xlabel(tr("한계 +1% 완화 시 capability 증가 [N·m]", "capability gain for +1% relaxation [N·m]"))
    base = dom.get("base_policy_capability_Nm")
    ax1.set_title(tr(f"병목 기여도 (기준 {base:.3f} N·m, ★ = 기준점에서 활성)" if base is not None else "병목 기여도",
                     f"bottleneck dominance (base {base:.3f} N·m, ★ = active at base)" if base is not None else "dominance"),
                  fontsize=9)
    xmax = max([r["gain_Nm"] for r in rows] + [1e-9])
    ax1.set_xlim(0, xmax * 1.6)
    if relax and ax2 is not None:
        sr = [r for r in relax["single"]]
        names = [r["constraint"] for r in sr]
        vals = [100 * r["minimal_relative_relaxation"] if r.get("minimal_relative_relaxation") is not None else np.nan for r in sr]
        yy = np.arange(len(sr))
        ax2.barh(yy, np.nan_to_num(vals, nan=0.0), color=[S.CONSTRAINT.get(n, t["muted"]) for n in names], height=0.6)
        for yi, v in zip(yy, vals):
            ax2.text(0 if not np.isfinite(v) else v, yi, tr("  단독으로 해결 불가", "  not sufficient alone") if not np.isfinite(v) else f"  +{v:.2f}%",
                     va="center", fontsize=7.5, color=t["fg"])
        ax2.set_yticks(yy)
        ax2.set_yticklabels(names, fontsize=7.5)
        ax2.set_xlabel(tr("요구를 만족한 가장 작은 표본 완화 [%] (표본 탐색)", "smallest sampled relaxation that met the request [%] (sampled)"))
        joint = relax.get("joint") or []
        jt = "; ".join("+".join(j["constraints"]) + f" +{100 * j['minimal_relative_relaxation_each']:.2f}%" for j in joint)
        ax2.set_title(tr(f"요구 {relax['T_request_Nm']:g} N·m 완화 분석", f"relaxation for {relax['T_request_Nm']:g} N·m"), fontsize=9)
        ax2.set_xlim(0, max([v for v in vals if np.isfinite(v)] + [60]) * 1.4)
        if jt:
            _note(ax2, tr("공동 병목: ", "joint bottleneck: ") + jt, loc="lower right")


# ---------------------------------------------------------------------------
# safety screening
# ---------------------------------------------------------------------------

def fig_ftti(fig, tl: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    bars = tl["bars"]
    owners = sorted({b["owner"] for b in bars})
    ocol = {o: c for o, c in zip(owners, ("#4c72b0", "#dd8452", "#55a868", "#c44e52", "#8172b3", "#937860"))}
    for yi, b in enumerate(bars):
        ms = 1e3
        start = b["start_worst_s"]
        if start is None:
            continue
        dup = b["id"] in tl["duplicates"]
        if b["counted"]:
            ax.barh(yi, (b["worst_s"] or 0.0) * ms, left=start * ms, color=ocol[b["owner"]], alpha=0.85, height=0.55,
                    hatch="////" if dup else None, edgecolor="#cf222e" if dup else None)
            if b["min_s"] is not None and b["start_best_s"] is not None:
                ax.barh(yi, b["min_s"] * ms, left=b["start_best_s"] * ms, color=t["fg"], alpha=0.35, height=0.18)
            if b.get("nom_s") is not None:
                ax.plot((start + b["nom_s"]) * ms, yi, marker="|", ms=12, color=t["fg"])
        else:
            budget = b["max_s"]
            ax.barh(yi, (budget or 0.0) * ms, left=start * ms, color="none", edgecolor="#cf222e" if dup else t["muted"],
                    height=0.55, hatch="xx" if dup else None, lw=1.2)
            ax.text(start * ms + (budget or 0) * ms, yi, tr(f"  예산 {budget * ms:.3g} ms (중복)", f"  budget {budget * ms:.3g} ms (duplicate)") if dup
                    else f"  {budget * ms:.3g} ms", va="center", fontsize=7, color="#cf222e" if dup else t["muted"])
        ax.text(start * ms + (b["worst_s"] or 0) * ms if b["counted"] else start * ms, yi + 0.34,
                f"{b['id']} [{b['owner']}]", fontsize=6.8, color=t["fg"])
    ax.axvline(tl["ftti_s"] * 1e3, color="#cf222e", lw=2, label=f"FTTI {tl['ftti_s'] * 1e3:.3g} ms")
    ax.axvline(tl["worst_s"] * 1e3, color=t["fg"], lw=1.2, ls="--", label=tr(f"최악 합 {tl['worst_s'] * 1e3:.3g} ms", f"worst sum {tl['worst_s'] * 1e3:.3g} ms"))
    ax.axvline(tl["best_s"] * 1e3, color=t["muted"], lw=1.0, ls=":", label=tr(f"최선 합 {tl['best_s'] * 1e3:.3g} ms", f"best sum {tl['best_s'] * 1e3:.3g} ms"))
    ends = [((b["start_worst_s"] or 0) + ((b["worst_s"] if b["counted"] else b["max_s"]) or 0)) for b in bars]
    ax.set_xlim(0, 1.2e3 * max(ends + [tl["ftti_s"], tl["worst_s"]]))
    ax.set_yticks(range(len(bars)))
    ax.set_yticklabels([f"{b['from']} → {b['to']}" for b in bars], fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlabel(tr("고장 발생 후 시간 [ms] (최악 경우 누적)", "time after fault [ms] (worst-case cumulative)"))
    h = [Patch(fc=c, label=o) for o, c in ocol.items()]
    h += [Patch(fc="none", ec="#cf222e", hatch="////", label=tr("중복 예산 (이중 계산)", "duplicated budget")),
          Patch(fc=t["fg"], alpha=0.35, label=tr("최선 경우 (min)", "best case (min)"))]
    leg1 = ax.legend(handles=h, loc="lower right", fontsize=7)
    ax.add_artist(leg1)
    ax.legend(loc="upper right", fontsize=7)
    checks = " · ".join(f"{c['budget']}: {'UNKNOWN' if c['ok'] is None else ('OK' if c['ok'] else 'NG')}"
                        for c in tl.get("budget_checks", []))
    if checks:
        _note(ax, checks, loc="lower left")


def fig_discharge(fig, res: dict, cur: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    ts = cur["t_s"]
    ax.plot(ts, cur["V"], color=S.ACCENT, lw=2, label=tr(f"RC 방전 (R = {res['R_used_ohm']:.4g} Ω, τ = {res['tau_s']:.4g} s)",
                                                        f"RC discharge (R = {res['R_used_ohm']:.4g} Ω, τ = {res['tau_s']:.4g} s)"))
    if "V_with_back_emf" in cur:
        ax.plot(ts, cur["V_with_back_emf"], color="#cf222e", lw=1.6, ls="--",
                label=tr(f"역기전력 정류 하한 {res['back_emf_ll_peak_V']:.1f} V", f"rectified back-EMF floor {res['back_emf_ll_peak_V']:.1f} V"))
    ax.axhline(res["Vf_V"], color=S.VERDICT["PASS"], lw=1.2, ls=":", label=tr(f"목표 {res['Vf_V']:g} V", f"target {res['Vf_V']:g} V"))
    ax.axvline(res["t_target_s"], color=t["fg"], lw=1.1, ls=":", label=tr(f"허용 시간 {res['t_target_s']:g} s", f"allowed {res['t_target_s']:g} s"))
    ax.plot(res["t_reach_s"], res["Vf_V"], marker="o", ms=7, color=S.ACCENT)
    ax.annotate(f"t = {res['t_reach_s']:.4g} s", (res["t_reach_s"], res["Vf_V"]), xytext=(6, 8), textcoords="offset points", fontsize=8)
    ax.set_xlabel(tr("시간 [s]", "time [s]"))
    ax.set_ylabel(tr("DC 링크 전압 [V]", "DC-link voltage [V]"))
    ax.set_ylim(0, res["V0_V"] * 1.08)
    ax2 = ax.twinx()
    ax2.plot(ts, cur["p_W"] / 1e3, color=t["muted"], lw=0.9, ls="-.")
    ax2.set_ylabel(tr("저항 전력 [kW] (일점쇄선)", "resistor power [kW] (dash-dot)"), color=t["muted"])
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    ax.legend(loc="upper right", fontsize=7.5)
    _note(ax, _discharge_summary(res), loc="lower left", fontsize=7)


def fig_overvoltage(fig, res: dict, cur: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    ms = cur["t_s"] * 1e3
    ax.plot(ms, cur["V"], color=S.ACCENT, lw=2.2, label=tr(f"차단 후 전압 (profile: {res['profile']})", f"link voltage after disconnect ({res['profile']})"))
    ax.plot(ms, cur["V_unlimited"], color=t["muted"], lw=1, ls="--", label=tr("회생 전력 미제거 시", "regen power never removed"))
    ax.axhline(res["V_limit_V"], color="#cf222e", lw=1.6, label=tr(f"한계 {res['V_limit_V']:g} V", f"limit {res['V_limit_V']:g} V"))
    ax.axvline(res["max_reaction_time_s"] * 1e3, color=S.VERDICT["PASS"], lw=1.2, ls=":",
               label=tr(f"허용 반응시간 {res['max_reaction_time_s'] * 1e3:.4g} ms", f"allowed reaction {res['max_reaction_time_s'] * 1e3:.4g} ms"))
    if res.get("reaction_time_s") is not None:
        ax.axvline(res["reaction_time_s"] * 1e3, color=t["fg"], lw=1.2, ls="-.",
                   label=tr(f"선언 반응시간 {res['reaction_time_s'] * 1e3:.4g} ms", f"declared reaction {res['reaction_time_s'] * 1e3:.4g} ms"))
        ax.plot(res["reaction_time_s"] * 1e3, res["V_peak_V"], marker="o", ms=7, color="#cf222e")
        ax.annotate(f"{res['V_peak_V']:.1f} V", (res["reaction_time_s"] * 1e3, res["V_peak_V"]), xytext=(6, -12),
                    textcoords="offset points", fontsize=8)
    if res.get("back_emf_ll_peak_V"):
        ax.axhline(res["back_emf_ll_peak_V"], color="#8c564b", lw=1, ls=":", label=tr(f"FW 시 역기전력 {res['back_emf_ll_peak_V']:.1f} V", f"back-EMF if freewheeling {res['back_emf_ll_peak_V']:.1f} V"))
    ax.set_xlabel(tr("배터리 차단 후 시간 [ms]", "time after battery disconnect [ms]"))
    ax.set_ylabel(tr("DC 링크 전압 [V]", "DC-link voltage [V]"))
    ax.legend(loc="upper left", fontsize=7.5)
    c = res["claim"]
    _note(ax, f"{c['status']}: {c['detail']}\nP_in = {res['P_in_W'] / 1e3:.2f} kW · C = {res['C_F'] * 1e6:.0f} µF",
          loc="lower right", fontsize=7)


def fig_safe_state(fig, asc: dict, speed_rpm: float | None = None, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2)
    n = asc["speeds"]
    ax1.plot(n, asc["i_peak_A"], color=S.GROUP["CURRENT"], lw=2, label=tr("ASC 정상상태 전류 |i| (peak)", "ASC steady-state |i| (peak)"))
    ax1.axhline(asc["current_limit_A"], color=S.GROUP["CURRENT"], ls="--", lw=1, label=tr("인버터 전류 한계", "inverter current limit"))
    ax1.set_xlabel(speed_label())
    ax1.set_ylabel(tr("전류 [A]", "current [A]"))
    a3 = ax1.twinx()
    a3.plot(n, asc["Tshaft_Nm"], color=S.ACCENT, lw=1.6, ls="-.")
    a3.set_ylabel(tr("ASC 제동 토크 [N·m] (일점쇄선)", "ASC braking torque [N·m] (dash-dot)"), color=S.ACCENT)
    a3.grid(False)
    a3.spines["right"].set_visible(True)
    ax1.set_title(tr("ASC (능동 단락): 정상상태, 진입 과도 미평가", "ASC: steady state only (entry transient not evaluated)"), fontsize=9)
    ax1.legend(loc="center right", fontsize=7)
    ax2.plot(n, asc["back_emf_ll_peak_V"], color="#8c564b", lw=2, label=tr("선간 역기전력 peak √3·ω_e·ψ", "line-line back-EMF peak √3·ω_e·ψ"))
    ax2.axhline(asc["Vdc_V"], color=S.ACCENT, lw=1.3, ls="--", label=f"Vdc = {asc['Vdc_V']:g} V")
    on = asc.get("ucg_onset_rpm")
    if on is not None and on < n[-1]:
        ax2.axvspan(on, n[-1], color="#cf222e", alpha=0.1, lw=0)
        ax2.axvline(on, color="#cf222e", lw=1.2)
        ax2.annotate(tr(f"비제어 정류 시작 {on:.0f} rpm", f"uncontrolled rectification from {on:.0f} rpm"), (on, asc["Vdc_V"]),
                     xytext=(6, 10), textcoords="offset points", fontsize=8, color="#cf222e")
    if speed_rpm is not None:
        for a in (ax1, ax2):
            a.axvline(speed_rpm, color=t["fg"], lw=1, ls=":")
    ax2.set_xlabel(speed_label())
    ax2.set_ylabel(tr("전압 [V]", "voltage [V]"))
    ax2.set_title(tr("Freewheel (6SO): 역기전력 vs DC 링크", "freewheel (6SO): back-EMF vs DC link"), fontsize=9)
    ax2.legend(loc="upper left", fontsize=7)


def fig_thermal(fig, av_curve: dict | None, th: dict | None, T_request: float | None = None, duration_s: float | None = None,
                title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2)
    if av_curve and av_curve["duration_s"].size:
        d, q = av_curve["duration_s"], av_curve["torque_Nm"]
        ax1.plot(d, q, color=S.ACCENT, lw=2, marker="o", ms=4, label=tr("지속시간별 가용 토크 (스크리닝)", "available torque vs duration (screening)"))
        if av_curve["continuous_Nm"] is not None:
            ax1.axhline(av_curve["continuous_Nm"], color=S.PHASE[2], ls="--", lw=1.2, label=tr(f"연속 {av_curve['continuous_Nm']:.1f} N·m", f"continuous {av_curve['continuous_Nm']:.1f} N·m"))
        if av_curve["static_Nm"] is not None:
            ax1.axhline(av_curve["static_Nm"], color=t["muted"], ls=":", lw=1, label=tr(f"정적 capability {av_curve['static_Nm']:.1f} N·m", f"static capability {av_curve['static_Nm']:.1f} N·m"))
        if T_request is not None and duration_s is not None:
            ax1.plot(duration_s, T_request, marker="X", ms=10, color=S.REQUEST, ls="none", label=tr("요구", "request"))
        ax1.set_xscale("log")
        ax1.set_ylim(0, 1.15 * max(float(np.nanmax(q)), T_request or 0.0))
        ax1.set_xlabel(tr("지속시간 [s]", "duration [s]"))
        ax1.set_ylabel(torque_label())
        ax1.legend(loc="lower left", fontsize=7)
        _side_title(ax1, tr("열 → 토크 가용성 (미검증 모델이면 UNKNOWN 유지)",
                            "thermal → torque availability (UNKNOWN if unvalidated)"))
    else:
        ax1.text(0.5, 0.5, tr("가용성 데이터 없음", "no availability data"), ha="center", va="center", transform=ax1.transAxes)
    if th and th["curves"]:
        for kk, c in enumerate(th["curves"]):
            col = S.PHASE[kk % 3]
            ax2.plot(th["t_s"], c["T_C"], color=col, lw=2, label=f"{c['node']} ({c['power_W']:.0f} W)")
            ax2.axhline(c["limit_C"], color=col, ls="--", lw=1)
            ref = c.get("fluid_reference_C")
            if ref is not None and abs(ref - th["coolant_C"]) > 1e-9:
                ax2.axhline(ref, color=col, ls=":", lw=1.1)
                ax2.annotate(tr(f"냉각수 기준 {ref:.1f} °C", f"coolant ref. {ref:.1f} °C"), (th["t_s"][1], ref),
                             xytext=(2, 2), textcoords="offset points", fontsize=6.5, color=col)
            ttl = c["time_to_limit_s"]
            ttl = float(ttl) if isinstance(ttl, (int, float)) else math.inf
            if math.isfinite(ttl) and ttl <= th["t_s"][-1]:
                ax2.plot(ttl, c["limit_C"], marker="o", ms=6, color=col)
                ax2.annotate(f"{ttl:.3g} s", (ttl, c["limit_C"]), xytext=(4, 4), textcoords="offset points", fontsize=7.5, color=col)
        ax2.axhline(th["coolant_C"], color=t["muted"], lw=0.8, ls=":")
        if duration_s is not None:
            ax2.axvline(duration_s, color=t["fg"], lw=1, ls=":")
        ax2.set_xscale("log")
        ax2.set_xlim(th["t_s"][1], th["t_s"][-1])
        ax2.set_xlabel(tr("시간 [s]", "time [s]"))
        ax2.set_ylabel(tr("온도 [°C]", "temperature [°C]"))
        ax2.legend(loc="upper left", fontsize=7)
        _side_title(ax2, tr("요구점의 노드 온도 (일정 손실, Foster)",
                            "node temperatures at the request (constant loss, Foster)"))
    else:
        ax2.text(0.5, 0.5, tr("요구점이 정적으로 가능하지 않아 온도 계산 안 함", "request not statically feasible: no temperature trace"),
                 ha="center", va="center", transform=ax2.transAxes, fontsize=8)
        ax2.set_axis_off()


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def fig_acceptance(fig, acc: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    rows = [r for r in acc["rows"] if r["value"] is not None and r["tolerance"]]
    ratio = np.array([max(r["value"], 1e-18) / r["tolerance"] for r in rows])
    gcol = {"forward": S.PHASE[0], "inverse": S.PHASE[1], "capability": S.PHASE[2]}
    y = np.arange(len(rows))
    ax.barh(y, ratio, color=[gcol.get(r["group"], t["muted"]) for r in rows], height=0.6)
    ax.axvline(1.0, color="#cf222e", lw=1.5)
    ax.set_xscale("log")
    ax.set_yticks(y)
    ax.set_yticklabels([f"{r['group']}: {r['case']}" for r in rows], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel(tr("오차 / 허용오차 (1 미만 = 통과, 로그)", "error / tolerance (< 1 passes, log)"))
    ax.legend(handles=[Patch(fc=c, label=g) for g, c in gcol.items()], loc="lower right", fontsize=7)
    n_pass = sum(r["pass"] for r in acc["rows"])
    ax.set_title(tr(f"golden 대비 acceptance {n_pass}/{len(acc['rows'])} 통과 · manifest {'OK' if acc['manifest_ok'] else 'MISMATCH'}",
                    f"acceptance vs golden {n_pass}/{len(acc['rows'])} pass · manifest {'OK' if acc['manifest_ok'] else 'MISMATCH'}"),
                 fontsize=9)


def fig_zth(fig, zc: dict, title: str | None = None):
    """Node-to-fluid thermal impedance Z_th(t) (log-log), with the stage time constants marked."""
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    for k, nd in enumerate(zc["nodes"]):
        col = S.PHASE[k % 3]
        ax.loglog(zc["t_s"], nd["zth_K_per_W"], color=col, lw=2, label=f"{nd['node']}  (R_th = {sum(nd['R_K_per_W']):.4g} K/W)")
        ax.axhline(sum(nd["R_K_per_W"]), color=col, lw=0.8, ls="--")
        for tau in nd["tau_s"]:
            if zc["t_s"][0] <= tau <= zc["t_s"][-1]:
                z = float(np.interp(tau, zc["t_s"], nd["zth_K_per_W"]))
                ax.plot(tau, z, marker="o", ms=4, color=col)
    ax.set_xlabel(tr("시간 [s]", "time [s]"))
    ax.set_ylabel(tr("Z_th (노드 → 냉각수) [K/W]", "Z_th (node → coolant) [K/W]"))
    ax.grid(True, which="both", alpha=0.5)
    ax.legend(loc="lower right", fontsize=7.5)
    _note(ax, tr("점 = 각 단의 시정수 τ_i · 점선 = 정상상태 R_th\n계산에 쓰인 값 (유량 보정·Cauer→Foster 변환 후)",
                 "dots = stage time constants τ_i · dashed = steady-state R_th\nvalues as used (after flow scaling and Cauer→Foster)"),
          loc="upper left")


def fig_passive_discharge(fig, res: dict, cur: dict, win: dict, title: str | None = None):
    """Bleeder (passive) discharge: V(t) and the R_p design window (discharge time vs continuous loss)."""
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2)
    ts = cur["t_s"]
    ax1.plot(ts, cur["V"], color=S.ACCENT, lw=2.2, label=tr(f"패시브 R_p = {res['R_used_ohm'] / 1e3:.4g} kΩ (τ = {res['tau_s']:.3g} s)",
                                                           f"passive R_p = {res['R_used_ohm'] / 1e3:.4g} kΩ (τ = {res['tau_s']:.3g} s)"))
    wa = res.get("with_active")
    if "V_with_active" in cur and wa:
        ax1.plot(ts, cur["V_with_active"], color=S.REQUEST, lw=1.6, ls="--",
                 label=tr(f"능동 R_a {wa['R_active_ohm']:.4g} Ω 병렬 → {wa['t_reach_s']:.3g} s",
                          f"with active R_a {wa['R_active_ohm']:.4g} Ω in parallel → {wa['t_reach_s']:.3g} s"))
    if "V_with_back_emf" in cur:
        ax1.plot(ts, cur["V_with_back_emf"], color="#cf222e", lw=1.4, ls="--",
                 label=tr(f"역기전력 정류 하한 {res['back_emf_ll_peak_V']:.1f} V", f"back-EMF floor {res['back_emf_ll_peak_V']:.1f} V"))
    ax1.axhline(res["Vf_V"], color=S.VERDICT["PASS"], ls=":", lw=1.2, label=tr(f"목표 {res['Vf_V']:g} V", f"target {res['Vf_V']:g} V"))
    ax1.axvline(res["t_target_s"], color=t["fg"], ls=":", lw=1.1, label=tr(f"허용 시간 {res['t_target_s']:g} s", f"allowed {res['t_target_s']:g} s"))
    ax1.plot(res["t_reach_s"], res["Vf_V"], marker="o", ms=7, color=S.ACCENT)
    ax1.annotate(f"t = {res['t_reach_s']:.4g} s", (res["t_reach_s"], res["Vf_V"]), xytext=(6, 8), textcoords="offset points", fontsize=8)
    ax1.set_ylim(0, res["V0_V"] * 1.08)
    ax1.set_xlabel(tr("배터리 분리 후 시간 [s]", "time after battery disconnect [s]"))
    ax1.set_ylabel(tr("DC 링크 전압 [V]", "DC-link voltage [V]"))
    ax1.set_title(tr("패시브 방전 V(t) = V₀·e^(−t/(R_p·C))", "passive discharge V(t) = V₀·e^(−t/(R_p·C))"), fontsize=9)
    ax1.legend(loc="upper right", fontsize=7)
    _note(ax1, _discharge_summary(res, 48), loc="lower left", fontsize=7)
    R = win["R_ohm"] / 1e3
    ax2.loglog(R, win["t_reach_s"], color=S.ACCENT, lw=2, label=tr("방전 시간 t = R_p·C·ln(V₀/V_f)", "discharge time t = R_p·C·ln(V₀/V_f)"))
    ax2.axhline(res["t_target_s"], color=S.ACCENT, ls=":", lw=1)
    ax2.set_xlabel("R_p [kΩ]")
    ax2.set_ylabel(tr("방전 시간 [s]", "discharge time [s]"), color=S.ACCENT)
    ax2.grid(True, which="both", alpha=0.4)
    a3 = ax2.twinx()
    a3.loglog(R, win["P_cont_max_W"], color="#cf222e", lw=2, label=tr(f"상시 손실 V_max²/R_p @ {res['V_max_V']:g} V", f"continuous loss @ {res['V_max_V']:g} V"))
    a3.loglog(R, win["P_cont_nom_W"], color="#cf222e", lw=1, ls="--", label=tr(f"상시 손실 @ {res['V_nom_V']:g} V", f"continuous loss @ {res['V_nom_V']:g} V"))
    a3.set_ylabel(tr("상시 손실 [W] (릴레이 닫힘 동안)", "continuous loss [W] (contactors closed)"), color="#cf222e")
    a3.grid(False)
    a3.spines["right"].set_visible(True)
    rmax = res["R_max_ohm"] / 1e3
    ax2.axvline(rmax, color=S.ACCENT, lw=1.3)
    ax2.annotate(tr(f"시간 한계\nR_p ≤ {rmax:.4g} kΩ", f"time limit\nR_p ≤ {rmax:.4g} kΩ"), (rmax, 0.9),
                 xycoords=("data", "axes fraction"), xytext=(5, 0), textcoords="offset points", fontsize=7.5,
                 color=S.ACCENT, ha="left", va="top")
    if res.get("P_allow_W"):
        a3.axhline(res["P_allow_W"], color="#cf222e", ls=":", lw=1)
        rmin = res["R_min_ohm"] / 1e3
        ax2.axvline(rmin, color="#cf222e", lw=1.3)
        ax2.annotate(tr(f"손실 한계\nR_p ≥ {rmin:.4g} kΩ", f"loss limit\nR_p ≥ {rmin:.4g} kΩ"), (rmin, 0.9),
                     xycoords=("data", "axes fraction"), xytext=(-5, 0), textcoords="offset points", fontsize=7.5,
                     color="#cf222e", ha="right", va="top")
        if rmin <= rmax:
            ax2.axvspan(rmin, rmax, color=t["feasible"], alpha=0.6, lw=0, zorder=0)
            ax2.text((rmin * rmax) ** 0.5, 0.04, tr("설계 창", "design window"), transform=ax2.get_xaxis_transform(),
                     ha="center", fontsize=8, color=S.VERDICT["PASS"], fontweight="bold")
        else:
            _note(ax2, tr("설계 창 없음: 패시브만으로는 시간·손실을 동시에 만족 못 함", "no window: the bleeder alone cannot meet both"),
                  loc="upper left", fontsize=7.5)
    ax2.plot(res["R_used_ohm"] / 1e3, res["t_reach_s"], marker="D", ms=7, color=t["fg"], zorder=5)
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = a3.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, loc="lower right", fontsize=7)
    ax2.set_title(tr(f"R_p 설계 창 · P·t = C·V²·ln(V₀/V_f) = {res['loss_time_product_Ws']:.4g} W·s (R_p와 무관)",
                     f"R_p design window · P·t = C·V²·ln(V₀/V_f) = {res['loss_time_product_Ws']:.4g} W·s (independent of R_p)"),
                  fontsize=8.5)
