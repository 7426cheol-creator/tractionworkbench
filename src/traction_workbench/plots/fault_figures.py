"""Figures of the causal fault simulation: synchronized waveforms with the event lines, the event timeline with the
FDTI / FRTI / FHTI chain, the reaction-candidate comparison, the campaign verdict map, the validation evidence and
the declared dependency matrix.  They draw the ``api.fault_*`` results as they are; nothing is recomputed here.
"""

from __future__ import annotations


import numpy as np
from matplotlib.patches import Patch

from ..i18n import tr
from ..insight.fault import fault_word
from . import style as S
from .figures import _note, _reset

VCOL = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f", "NOT_APPLICABLE": "#8c959f"}
BRIDGE = {0: ("PWM", "#d0d7de"), 1: ("ASC-low", "#0969da"), 2: ("ASC-high", "#8250df"), 3: ("6SO", "#bf8700"),
          4: ("off", "#57606a")}
EVCOL = {"fault": "#cf222e", "detection": "#e36209", "actuation": "#0969da", "bridge": "#0969da",
         "recovery": "#1a7f37", "fault_cleared": "#1a7f37", "reaction_blocked": "#8b0000",
         "reaction_conflict": "#8250df", "plant": "#57606a", "driver": "#e36209", "controller": "#57606a",
         "out_of_model": "#000000"}
EVKINDS = ("fault", "detection", "actuation", "reaction_blocked", "reaction_conflict", "recovery", "fault_cleared",
           "plant", "controller", "out_of_model")


def _ms(t):
    return np.asarray(t, dtype=float) * 1e3


def _event_lines(ax, res, kinds=("fault", "detection", "actuation"), labels=False):
    seen = set()
    for e in res.get("events") or []:
        if e["kind"] not in kinds:
            continue
        key = (e["kind"], round(e["t"] * 1e6))
        if key in seen:
            continue
        seen.add(key)
        ax.axvline(e["t"] * 1e3, color=EVCOL.get(e["kind"], "#57606a"), lw=0.9,
                   ls={"fault": "-", "detection": "--", "actuation": "-."}.get(e["kind"], ":"), zorder=2, alpha=0.8)


def _fsr_chain(res):
    for f in (res.get("evaluation") or {}).get("fsr") or []:
        if f["timeline"].get("t_D") is not None:
            return f
    return None


def _unit(u: str) -> str:
    return (u or "").replace("N*m", "N·m")


def tsr_bounds(res) -> dict:
    """The TSR bounds of the trace quantities: {"v_dc": (lo, hi), "i_phase": (None, max |i|)}."""
    out = {}
    for tsr in (res.get("evaluation") or {}).get("tsr") or []:
        c = tsr.get("criterion") or {}
        if tsr.get("type") != "bound":
            continue
        q = {"v_dc": "v_dc", "i_phase_abs": "i_phase"}.get(c.get("quantity"))
        if q is None:
            continue
        lo, hi = out.get(q, (None, None))
        if c.get("min") is not None:
            lo = c["min"] if lo is None else max(lo, c["min"])
        if c.get("max") is not None:
            hi = c["max"] if hi is None else min(hi, c["max"])
        out[q] = (lo, hi)
    return out


def _fit_y(ax, arrays, bounds=(), pad: float = 0.08, near: float = 0.6):
    """Y limits from the plotted data.  A bound (a TSR limit line) stays in view only when it is near the data (within
    ``near`` of the data span beyond it): a far one would flatten the transient into a line, so it is named at the
    edge of the axes instead ("off scale", with the margin to the data)."""
    vals = [np.asarray(a, dtype=float) for a in arrays]
    vals = [a[np.isfinite(a)] for a in vals]
    vals = [a for a in vals if a.size]
    if not vals:
        return
    dlo, dhi = min(float(a.min()) for a in vals), max(float(a.max()) for a in vals)
    if dhi - dlo <= 1e-9 * max(1.0, abs(dlo), abs(dhi)):         # a flat trace (e.g. no battery current): +-1 unit
        dlo, dhi = dlo - 1.0, dhi + 1.0
    lo, hi = dlo, dhi
    span = max(hi - lo, 1e-9, 0.02 * max(abs(lo), abs(hi)))
    off = []
    for b in bounds:
        v, label, unit = (tuple(b) + ("",))[:3]
        if lo - near * span <= v <= hi + near * span:
            lo, hi = min(lo, v), max(hi, v)
        else:
            off.append((v, label, unit))
    span = max(hi - lo, 1e-9, 0.02 * max(abs(lo), abs(hi)))
    ax.set_ylim(lo - pad * span, hi + pad * span)
    ax._twb_off = {"up": 0, "down": 0}
    for v, label, unit in off:
        up = v > dhi
        n = ax._twb_off["up" if up else "down"]
        ax._twb_off["up" if up else "down"] = n + 1
        margin = f"{(v - dhi if up else dlo - v):.4g}" + (f" {unit}" if unit else "")
        txt = ax.text(0.006, (0.975 - 0.07 * n) if up else (0.025 + 0.07 * n),
                      f"{'▲' if up else '▼'} {label} " + tr(f"(축 밖, 여유 {margin})", f"(off scale, margin {margin})"),
                      transform=ax.transAxes, ha="left", va="top" if up else "bottom", fontsize=6.5, color="#8b0000",
                      zorder=21, bbox=dict(boxstyle="round,pad=0.2", fc=S.theme()["bg"], ec="none", alpha=0.8))
        txt.set_in_layout(False)


def _callouts(host, entries):
    """Simulated extremes ``entries`` = [(axes, x_ms, y, text, color)] (the axes may be ``host`` or its twin): a marker
    on each sample and its text stacked in free rows of the host's area - points in the upper half from the top down,
    the others from the bottom up, in time order - with a leader line, so texts never sit on each other."""
    t = S.theme()
    items = []
    for ax, x, y, text, col in entries:
        if x is None or y is None or not np.isfinite(y):
            continue
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        items.append(((x - x0) / (x1 - x0), (y - y0) / (y1 - y0), ax, x, y, text, col))
    items.sort(key=lambda e: e[0])
    off = {"up": 0, "down": 0}
    for a in {id(e[2]): e[2] for e in items}.values():
        for k, v in (getattr(a, "_twb_off", None) or {}).items():
            off[k] = max(off[k], v)
    slot = {"up": 0, "down": 0}
    for fx, fy, ax, x, y, text, col in items:
        ax.plot([x], [y], marker="o", ms=5, mfc="none", mec=col, mew=1.4, ls="none", zorder=22)
        side = "up" if fy >= 0.5 else "down"
        n = slot[side] + off[side]
        slot[side] += 1
        ty = 0.9 - 0.1 * n if side == "up" else 0.1 + 0.1 * n
        right = fx > 0.58
        tx = min(max(fx - 0.035 if right else fx + 0.035, 0.01), 0.99)
        a = ax.annotate(text, xy=(x, y), xycoords="data", xytext=(tx, ty), textcoords="axes fraction",
                        ha="right" if right else "left", va="center", fontsize=6.8, color=col, zorder=23,
                        annotation_clip=True,      # zoomed away from its sample: hidden, no leader line across
                        bbox=dict(boxstyle="round,pad=0.2", fc=t["bg"], ec=col, lw=0.6, alpha=0.92),
                        arrowprops=dict(arrowstyle="-", color=col, lw=0.6, shrinkA=0, shrinkB=4))
        a.set_in_layout(False)


def _legend(ax, handles=None, labels=None, others=(), **kw):
    """The legend of ``ax`` at the standard place that hides the fewest samples - of ``ax`` and of the axes ``others``
    (a twin: matplotlib's "best" looks at its own axes only) - and not a note: an overlapped text (an extreme's value,
    an instant's name, an off-scale limit) weighs like hundreds of covered samples."""
    if handles is None:
        handles, labels = ax.get_legend_handles_labels()
    leg = ax.legend(handles, labels, loc="best", **kw)
    own = getattr(leg, "_auto_legend_data", None)
    if own is None:                                  # (a matplotlib without the hook: its own "best")
        return leg

    def data(*a):
        bboxes, lines, offsets = own(*a)
        r = a[0] if a else ax.figure.canvas.get_renderer()
        for o in (ax, *others):
            if o is not ax:
                lines += [ln.get_transform().transform_path(ln.get_path()) for ln in o.lines if ln.get_visible()]
            for t in o.texts:
                if t.get_visible() and t.get_text():
                    bboxes += [t.get_window_extent(r)] * 300
        return bboxes, lines, offsets
    leg._auto_legend_data = data
    return leg


def _peak_text(v: float, unit: str, t_s: float, t_fault: float | None, prefix: str = "") -> str:
    s = f"{prefix}{v:.4g} {_unit(unit)} @ {t_s * 1e3:.3f} ms"
    if t_fault is not None:
        s += tr(f" (고장 +{(t_s - t_fault) * 1e3:.3g} ms)", f" (fault +{(t_s - t_fault) * 1e3:.3g} ms)")
    return s


def _mark_extremes(axes: dict, m: dict):
    """The extremes after the fault (``transient.transient_metrics``) on the waveform axes: {"T": ax, "i": ax,
    "v": ax, "ib": ax}."""
    _mark_extremes_in(axes, {r["quantity"]: r for r in m.get("rows") or []}, m.get("t_fault_s"))


def _bridge_strip(ax, tt, br, mcu, min_label: float = 0.06):
    """The commanded bridge state (upper half) and the MCU state (lower band) as colored spans."""
    br = np.asarray(br).astype(int)
    if len(tt) > 1:
        start = 0
        for i in range(1, len(br) + 1):
            if i == len(br) or br[i] != br[start]:
                name, col = BRIDGE.get(int(br[start]), ("?", "#999999"))
                ax.axvspan(tt[start], tt[min(i, len(tt) - 1)], ymin=0.5, ymax=1.0, color=col, alpha=0.85, lw=0)
                if tt[min(i, len(tt) - 1)] - tt[start] > min_label * (tt[-1] - tt[0]):
                    lab = ax.text(0.5 * (tt[start] + tt[min(i, len(tt) - 1)]), 0.75, name, ha="center",
                                  va="center", fontsize=7, color="#ffffff" if int(br[start]) else "#1f2328",
                                  clip_on=True)    # zoomed: a label outside the view neither shows nor
                    lab.set_in_layout(False)       # pushes the layout (the axes would shrink to fit it)
                start = i
        mcu = np.asarray(mcu).astype(int)
        start = 0
        for i in range(1, len(mcu) + 1):
            if i == len(mcu) or mcu[i] != mcu[start]:
                col = {0: "#1a7f37", 1: "#cf222e", 2: "#bf8700"}.get(int(mcu[start]), "#999999")
                ax.axvspan(tt[start], tt[min(i, len(tt) - 1)], ymin=0.0, ymax=0.42, color=col, alpha=0.55, lw=0)
                start = i
    ax.set_yticks([0.2, 0.75])
    ax.set_yticklabels([tr("MCU", "MCU"), tr("브리지 명령", "bridge cmd")], fontsize=7)
    ax.set_ylim(0, 1)


def fig_fault_waveforms(fig, res: dict, title: str | None = None):
    """Truth, measurement, estimate, command and actual bridge on one time axis (fault / detection / reaction lines).
    The extremes after the fault are marked with their value and instant; a limit far from the data is named at the
    axes' edge instead of flattening the traces; the shaded span is the window of the transient view."""
    from ..extensions.faultsim.transient import transient_metrics
    _reset(fig, title)
    t = S.theme()
    tr_ = res["trace"]
    tt = _ms(tr_["t"])
    bounds = tsr_bounds(res)
    m = transient_metrics(res, bounds=bounds)
    axs = fig.subplots(4, 1, sharex=True, gridspec_kw={"height_ratios": [1.25, 1.15, 1.0, 0.42]})
    ax = axs[0]
    wins = (res.get("evaluation") or {}).get("windows") or {}
    shown = []
    for i, (tid, w) in enumerate(wins.items()):
        if i == 0:
            ax.fill_between(tt, w["lo"], w["hi"], color="#1a7f37", alpha=0.10, lw=0,
                            label=tr(f"요구 허용 창 ({tid})", f"requirement window ({tid})"))
            shown += [w["lo"], w["hi"]]
    mlo, mhi = np.asarray(tr_["mon_lo"]), np.asarray(tr_["mon_hi"])
    if np.isfinite(mlo).any():
        ax.plot(tt, mlo, color="#e36209", lw=0.7, ls=":", label=tr("감시기 창 (측정 기반)", "monitor window (measured)"))
        ax.plot(tt, mhi, color="#e36209", lw=0.7, ls=":")
    ax.plot(tt, tr_["T_request"], color=S.REQUEST, lw=1.3, ls="--", label=tr("차량 요청 (의도)", "vehicle request (intent)"))
    ax.plot(tt, tr_["T_cmd"], color=t["muted"], lw=0.9, drawstyle="steps-post",
            label=tr("수신 명령 (메시지)", "received command (message)"))
    est = np.asarray(tr_["T_est_mon"])
    if np.isfinite(est).any():
        ax.plot(tt, est, color="#e36209", lw=0.8, drawstyle="steps-post", label=tr("감시기 추정 토크", "monitor estimate"))
    ax.plot(tt, tr_["T_shaft"], color=S.ACCENT, lw=1.8, label=tr("실제 축 토크 (참값)", "actual shaft torque (truth)"))
    ax.set_ylabel(tr("토크 [N·m]", "torque [N·m]"))
    _fit_y(ax, [tr_["T_request"], tr_["T_cmd"], tr_["T_shaft"], est, mlo, mhi] + shown, pad=0.12)
    _legend(ax, fontsize=6.5, ncol=2)
    ax = axs[1]
    for k, ph in enumerate("abc"):
        ax.plot(tt, tr_[f"i_{ph}"], color=S.PHASE[k], lw=1.1, label=tr(f"i_{ph} 참값", f"i_{ph} truth"))
    ax.plot(tt, tr_["i_a_meas"], color=S.PHASE[0], lw=0.8, ls="--", alpha=0.8,
            label=tr("i_a 센서 출력", "i_a sensor output"))
    lim = []
    for tsr in (res.get("evaluation") or {}).get("tsr") or []:
        c = tsr["criterion"]
        if tsr["type"] == "bound" and c.get("quantity") == "i_phase_abs" and c.get("max") is not None:
            ax.axhline(c["max"], color="#8b0000", lw=1, ls="-", label=f"{tsr['id']} {c['max']:g} A")
            ax.axhline(-c["max"], color="#8b0000", lw=1, ls="-")
            lim += [(c["max"], f"{tsr['id']} {c['max']:g} A", "A"), (-c["max"], f"{tsr['id']} −{c['max']:g} A", "A")]
    ax.set_ylabel(tr("상전류 [A]", "phase current [A]"))
    _fit_y(ax, [tr_["i_a"], tr_["i_b"], tr_["i_c"], tr_["i_a_meas"]], lim, pad=0.1)
    _legend(ax, fontsize=6.5, ncol=3)
    ax = axs[2]
    ax.plot(tt, tr_["v_dc"], color="#8250df", lw=1.4, label=tr("DC-link 전압 (참값)", "DC-link voltage (truth)"))
    ax.plot(tt, tr_["v_dc_meas"], color="#8250df", lw=0.8, ls="--", label=tr("제어 전압 센서", "control voltage sensor"))
    lim = []
    for tsr in (res.get("evaluation") or {}).get("tsr") or []:
        c = tsr["criterion"]
        if tsr["type"] == "bound" and c.get("quantity") == "v_dc" and c.get("max") is not None:
            ax.axhline(c["max"], color="#8b0000", lw=1, label=f"{tsr['id']} {c['max']:g} V")
            lim.append((c["max"], f"{tsr['id']} {c['max']:g} V", "V"))
    ax.set_ylabel("V_dc [V]")
    _fit_y(ax, [tr_["v_dc"], tr_["v_dc_meas"]], lim, pad=0.25)
    ax2 = ax.twinx()
    ax2.plot(tt, tr_["i_bat"], color=t["muted"], lw=0.8, label=tr("배터리 전류", "battery current"))
    ax2.set_ylabel(tr("배터리 전류 [A]", "battery current [A]"), fontsize=8)
    _fit_y(ax2, [tr_["i_bat"]], pad=0.25)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    _legend(ax2, h1 + h2, l1 + l2, others=(ax,), fontsize=6.5, ncol=2)      # on the twin: drawn over both
    _bridge_strip(axs[3], tt, tr_["bridge"], tr_["mcu"])
    axs[3].set_xlabel(tr("시간 [ms]", "time [ms]"))
    for a in axs:
        _event_lines(a, res)
        a.grid(True, alpha=0.3)
    f = _fsr_chain(res)
    if f is not None and f["timeline"].get("t_S") is not None:
        for a in axs[:3]:
            a.axvline(f["timeline"]["t_S"] * 1e3, color="#1a7f37", lw=1.1, ls=":")
    if len(tt) > 1:
        axs[0].set_xlim(tt[0], tt[-1])
    w = m.get("window_s")
    if w is not None and m.get("t_fault_s") is not None and (w[1] - w[0]) < 0.8 * (tt[-1] - tt[0]) * 1e-3:
        for a in axs[:3]:
            a.axvspan(w[0] * 1e3, w[1] * 1e3, color="#8250df", alpha=0.07, lw=0, zorder=0)
        z = axs[0].text(w[1] * 1e3, 0.02, tr(" ◂ 과도 확대 구간", " ◂ transient zoom"), transform=axs[0].get_xaxis_transform(),
                        fontsize=6.5, color="#8250df", ha="left", va="bottom", zorder=21, clip_on=True)
        z.set_in_layout(False)
    _mark_extremes({"T": axs[0], "i": axs[1], "v": axs[2], "ib": ax2}, m)
    axs[0].set_title(tr("실선 빨강 = 고장 · 파선 주황 = 검출 · 쇄선 파랑 = 반응 · 점선 초록 = 안전 조건 도달(유지) · ○ = 고장 후 극값",
                        "red = fault · orange dashed = detection · blue dash-dot = reaction · green dotted = safe "
                        "condition reached (and held) · ○ = extreme after the fault"), fontsize=8)


def _event_labels(ax, res, a_ms: float, b_ms: float):
    """Names of the fault / detection / reaction instants inside [a, b] ms, at the top of ``ax`` (instants closer than
    1.5 % of the window share one label)."""
    groups = []
    for e in res.get("events") or []:
        if e["kind"] not in ("fault", "detection", "actuation"):
            continue
        x = e["t"] * 1e3
        if not a_ms <= x <= b_ms:
            continue
        if e["kind"] == "fault":
            name = tr("고장", "fault") + ": " + fault_word(e["source"])
        elif e["kind"] == "detection":
            name = f"{e['source']} " + tr("검출", "detects")
        else:
            react = e.get("reaction") or str(e.get("text", "")).split(" via ")[0]
            name = f"{e['source']} → {react}"
        if groups and x - groups[-1][0] < 0.015 * (b_ms - a_ms):
            if name not in groups[-1][1]:
                groups[-1][1].append(name)
        else:
            groups.append((x, [name], e["kind"]))
    for x, names, kind in groups:
        txt = ax.text(x, 0.97, f"{' / '.join(names)}  {x:.3f} ms", transform=ax.get_xaxis_transform(), rotation=90,
                      ha="right", va="top", fontsize=6.3, color=EVCOL.get(kind, "#57606a"), zorder=21, clip_on=True,
                      bbox=dict(boxstyle="round,pad=0.15", fc=S.theme()["bg"], ec="none", alpha=0.75))
        txt.set_in_layout(False)


def fig_fault_transient(fig, res: dict, title: str | None = None, window_ms=None):
    """The reaction transient zoomed (``transient.transient_window`` unless ``window_ms``): torque, phase currents,
    DC-link voltage with the battery current and the bridge, every simulated sample marked (the resolution the
    values were computed with), the extremes after the fault with value and instant, the instants named."""
    from ..extensions.faultsim.transient import transient_metrics
    _reset(fig, title)
    t = S.theme()
    tr_ = res["trace"]
    tt = _ms(tr_["t"])
    bounds = tsr_bounds(res)
    m = transient_metrics(res, bounds=bounds)
    w = list(window_ms) if window_ms else ([x * 1e3 for x in m["window_s"]] if m.get("window_s") else
                                           [float(tt[0]), float(tt[-1])])
    k = np.where((tt >= w[0]) & (tt <= w[1]))[0]
    if len(k) < 2:
        k = np.arange(len(tt))
    sl = slice(max(int(k[0]) - 1, 0), min(int(k[-1]) + 2, len(tt)))
    x = tt[sl]

    def arr(key):
        return np.asarray(tr_[key], dtype=float)[sl]

    mk = dict(marker=".", ms=2.4)
    axs = fig.subplots(4, 1, sharex=True, gridspec_kw={"height_ratios": [1.15, 1.15, 1.05, 0.34]})
    ax = axs[0]
    ax.plot(x, arr("T_request"), color=S.REQUEST, lw=1.1, ls="--", label=tr("차량 요청 (의도)", "vehicle request (intent)"))
    ax.plot(x, arr("T_cmd"), color=t["muted"], lw=0.9, drawstyle="steps-post",
            label=tr("수신 명령 (메시지)", "received command (message)"))
    ax.plot(x, arr("T_shaft"), color=S.ACCENT, lw=1.4, label=tr("실제 축 토크 (참값)", "actual shaft torque (truth)"), **mk)
    ax.set_ylabel(tr("토크 [N·m]", "torque [N·m]"))
    _fit_y(ax, [arr("T_request"), arr("T_cmd"), arr("T_shaft")], pad=0.14)
    _legend(ax, fontsize=6.5, ncol=1)                  # narrow: it fits a corner the swing leaves free
    ax = axs[1]
    for j, ph in enumerate("abc"):
        ax.plot(x, arr(f"i_{ph}"), color=S.PHASE[j], lw=1.0, label=tr(f"i_{ph} 참값", f"i_{ph} truth"), **mk)
    lim = []
    lo, hi = bounds.get("i_phase", (None, None))
    if hi is not None:
        ax.axhline(hi, color="#8b0000", lw=1, label=f"|i| ≤ {hi:g} A")
        ax.axhline(-hi, color="#8b0000", lw=1)
        lim += [(hi, f"|i| ≤ {hi:g} A", "A"), (-hi, f"|i| ≤ {hi:g} A", "A")]
    ax.set_ylabel(tr("상전류 [A]", "phase current [A]"))
    _fit_y(ax, [arr("i_a"), arr("i_b"), arr("i_c")], lim, pad=0.12)
    _legend(ax, fontsize=6.5, ncol=4)
    ax = axs[2]
    ax.plot(x, arr("v_dc"), color="#8250df", lw=1.3, label=tr("DC-link 전압 (참값)", "DC-link voltage (truth)"), **mk)
    lim = []
    lo, hi = bounds.get("v_dc", (None, None))
    if hi is not None:
        ax.axhline(hi, color="#8b0000", lw=1, label=f"V_dc ≤ {hi:g} V")
        lim.append((hi, f"V_dc ≤ {hi:g} V", "V"))
    ax.set_ylabel("V_dc [V]")
    _fit_y(ax, [arr("v_dc")], lim, pad=0.3)
    ax2 = ax.twinx()
    ax2.plot(x, arr("i_bat"), color=t["muted"], lw=0.9, label=tr("배터리 전류", "battery current"), **mk)
    ax2.set_ylabel(tr("배터리 전류 [A]", "battery current [A]"), fontsize=8)
    _fit_y(ax2, [arr("i_bat")], pad=0.3)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    _legend(ax2, h1 + h2, l1 + l2, others=(ax,), fontsize=6.5, ncol=3)
    _bridge_strip(axs[3], x, np.asarray(tr_["bridge"])[sl], np.asarray(tr_["mcu"])[sl], min_label=0.04)
    axs[3].set_xlabel(tr("시간 [ms]", "time [ms]"))
    for a in axs:
        _event_lines(a, res)
        a.grid(True, alpha=0.3)
        a.set_xlim(w[0], w[1])
    _event_labels(axs[0], res, w[0], w[1])
    rows = {r["quantity"]: r for r in m.get("rows") or []}
    shown = {}
    for q, r in rows.items():                    # extremes inside the window only (a later one is in the table)
        keep = dict(r)
        for key in ("t_min_s", "t_max_s", "t_extreme_s"):
            if key in keep and not (w[0] <= keep[key] * 1e3 <= w[1]):
                keep[key] = None
        shown[q] = keep
    _mark_extremes_in({"T": axs[0], "i": axs[1], "v": axs[2], "ib": ax2}, shown, m.get("t_fault_s"))
    res_us = np.diff(x) * 1e3
    res_us = res_us[res_us > 0]
    axs[0].set_title(tr(f"과도 확대 {w[0]:.3f}–{w[1]:.3f} ms · 점 = 시뮬레이션 샘플 (간격 {res_us.min():.3g}–{res_us.max():.3g} µs) · "
                        "○ = 고장 후 극값",
                        f"transient zoom {w[0]:.3f}–{w[1]:.3f} ms · dots = simulated samples (spacing "
                        f"{res_us.min():.3g}–{res_us.max():.3g} µs) · ○ = extreme after the fault")
                     if res_us.size else tr("과도 확대", "transient zoom"), fontsize=8)


def _mark_extremes_in(axes: dict, rows: dict, tf):
    """Mark the extremes of ``rows`` ({quantity: transient row}) on {"T": ax, "i": ax, "v": ax, "ib": twin of v}; an
    instant set to None (outside a zoomed window) is not marked."""
    def ok(r, key):
        if r is None or r.get(key) is None:
            return False
        if r.get("max") is not None and r.get("min") is not None:      # a flat trace after the fault (e.g. no
            span = abs(r["max"] - r["min"])                             # battery current once disconnected) has
            if span <= 1e-6 * max(1.0, abs(r["max"]), abs(r["min"])):   # no extreme worth a note
                return False
        return True
    r = rows.get("T_shaft")
    if r and "T" in axes:
        _callouts(axes["T"], [(axes["T"], r[k] * 1e3, r[v], _peak_text(r[v], r["unit"], r[k], tf), S.ACCENT)
                              for k, v in (("t_min_s", "min"), ("t_max_s", "max")) if ok(r, k)])
    r = rows.get("i_phase")
    if r and "i" in axes and ok(r, "t_extreme_s"):
        _callouts(axes["i"], [(axes["i"], r["t_extreme_s"] * 1e3, r["extreme"],
                               _peak_text(r["extreme"], r["unit"], r["t_extreme_s"], tf,
                                          prefix=f"|i|max i_{r['phase']} "), S.PHASE["abc".index(r["phase"])])])
    ent = []
    r = rows.get("v_dc")
    if r and "v" in axes:
        ent += [(axes["v"], r[k] * 1e3, r[v], _peak_text(r[v], r["unit"], r[k], tf, f"V_dc {v} "), "#8250df")
                for k, v in (("t_max_s", "max"), ("t_min_s", "min")) if ok(r, k)]
    r = rows.get("i_bat")
    if r and "ib" in axes:
        ent += [(axes["ib"], r[k] * 1e3, r[v], _peak_text(r[v], r["unit"], r[k], tf, f"I_bat {v} "),
                 S.theme()["muted"]) for k, v in (("t_max_s", "max"), ("t_min_s", "min")) if ok(r, k)]
    if ent and "v" in axes:
        _callouts(axes["v"], ent)


def fig_fault_timeline(fig, res: dict, title: str | None = None):
    """The event timeline by source and the FSR's t_F -> t_D -> t_R -> t_S chain against its budgets and the FTTI."""
    _reset(fig, title)
    t = S.theme()
    ev = [e for e in res.get("events") or [] if e["kind"] in EVKINDS]
    srcs = []
    for e in ev:
        if e["source"] not in srcs:
            srcs.append(e["source"])
    gs = fig.add_gridspec(2, 1, height_ratios=[max(2.0, 0.35 * len(srcs)), 1.1])
    ax = fig.add_subplot(gs[0])
    for e in ev:
        y = srcs.index(e["source"])
        ax.plot(e["t"] * 1e3, y, marker={"fault": "X", "detection": "o", "actuation": "s", "reaction_blocked": "x",
                                        "reaction_conflict": "D", "recovery": "^"}.get(e["kind"], "."),
                color=EVCOL.get(e["kind"], t["muted"]), ms=7, ls="none")
    faults = {e["source"] for e in ev if e["kind"] in ("fault", "fault_cleared")}
    ax.set_yticks(range(len(srcs)))
    ax.set_yticklabels([fault_word(x) if x in faults else x for x in srcs], fontsize=7)
    ax.grid(True, axis="x", alpha=0.3)
    ax.set_xlabel(tr("시간 [ms]", "time [ms]"))
    lo_t = min((e["t"] for e in ev), default=0.0) * 1e3
    hi_t = max((e["t"] for e in ev), default=1.0) * 1e3
    pad = max(0.05 * (hi_t - lo_t), 0.05)
    ax.set_xlim(lo_t - pad, hi_t + pad)
    ax.set_ylim(len(srcs) - 0.5 + 0.9, -0.6)                       # room below the last row for the legend
    ax.legend(handles=[Patch(color=EVCOL[k], label=lab) for k, lab in
                       (("fault", tr("고장", "fault")), ("detection", tr("검출", "detection")),
                        ("actuation", tr("반응", "reaction")), ("reaction_blocked", tr("반응 차단", "blocked")),
                        ("reaction_conflict", tr("반응 충돌", "conflict")), ("recovery", tr("복귀", "recovery")))],
              loc="lower center", fontsize=6.5, ncol=6, frameon=False)
    ax.set_title(tr("사건 타임라인 (출처별)", "event timeline (by source)"), fontsize=9)
    _fsr_lanes(fig.add_subplot(gs[1]), res)


def _fsr_lanes(ax, res):
    """One lane per FSR on one trajectory: t_F -> t_D (FDTI) -> t_S (FRTI), FHTI = t_S - t_F, with the budget ticks
    and the SG's FTTI; an undetected violation or a safe condition never reached is drawn to the end of the run."""
    ev = res.get("evaluation") or {}
    fsrs = [f for f in ev.get("fsr") or [] if f["timeline"].get("t_F") is not None][:4]
    if not fsrs:
        ax.axis("off")
        _note(ax, tr("주 고장 없음: FDTI/FRTI/FHTI를 잴 원점(t_F)이 없습니다",
                     "no primary fault: no origin (t_F) to measure FDTI / FRTI / FHTI from"), loc="upper left",
              fontsize=8)
        return
    t_end = float(res["trace"]["t"][-1]) if res.get("trace") and len(res["trace"]["t"]) else None
    ftti = {g["id"]: g.get("ftti_s") for g in ev.get("sg") or []}
    marks, far = [], []
    lo = min(f["timeline"]["t_F"] for f in fsrs)
    hi = lo
    for f in fsrs:
        tl, b = f["timeline"], f.get("budgets") or {}
        for v in (tl["t_D"], tl["t_S"], tl["t_V"]):
            hi = max(hi, v or hi)
        if (tl["t_D"] is not None and tl["t_S"] is None) or (tl["t_D"] is None and tl["t_V"] is not None):
            hi = max(hi, t_end or hi)
        if b.get("FDTI_s"):
            hi = max(hi, tl["t_F"] + b["FDTI_s"])
        if b.get("FRTI_s") and tl["t_D"] is not None:
            hi = max(hi, tl["t_D"] + b["FRTI_s"])
    span = max(hi - lo, 1e-4)
    for y, f in enumerate(fsrs):
        tl, b = f["timeline"], f.get("budgets") or {}
        tF, tD, tS = tl["t_F"], tl["t_D"], tl["t_S"]
        ax.plot(tF * 1e3, y, "X", color=EVCOL["fault"], ms=7, zorder=4)
        parts = []
        if tD is not None:
            ax.barh(y, (tD - tF) * 1e3, left=tF * 1e3, height=0.34, color="#e36209", alpha=0.85, zorder=3)
            parts.append(f"FDTI {tl['FDTI'] * 1e3:.3g} ms")
            if tS is not None:
                ax.barh(y, (tS - tD) * 1e3, left=tD * 1e3, height=0.34, color="#1a7f37", alpha=0.85, zorder=3)
                parts.append(f"FRTI {tl['FRTI'] * 1e3:.3g} ms")
                parts.append(f"FHTI {tl['FHTI'] * 1e3:.3g} ms")
            elif not f.get("safe_state"):
                parts.append(tr("안전 상태 요구 없음: FRTI 정의 안 됨 (한계 요구로 판정)",
                                "no safe-state requirement: FRTI undefined (judged by its bounds)"))
            elif t_end is not None:
                ax.barh(y, (t_end - tD) * 1e3, left=tD * 1e3, height=0.34, color="none", edgecolor="#cf222e",
                        hatch="///", zorder=3)
                parts.append(tr("안전 조건 미도달", "safe condition not reached"))
            if tl.get("t_R") is not None:
                ax.plot([tl["t_R"] * 1e3] * 2, [y - 0.24, y + 0.24], color="#0969da", lw=1.6, zorder=4)
        elif tl.get("t_V") is not None and t_end is not None:
            ax.barh(y, (t_end - tl["t_V"]) * 1e3, left=tl["t_V"] * 1e3, height=0.34, color="none",
                    edgecolor="#cf222e", hatch="xxx", zorder=3)
            parts.append(tr(f"미검출 위반 {tl['t_V'] * 1e3:.3g} ms부터", f"undetected violation from "
                                                                      f"{tl['t_V'] * 1e3:.3g} ms"))
        else:
            parts.append(tr("처리할 위반·검출 없음", "nothing to handle"))
        for x, col in (((tF + b["FDTI_s"]) if b.get("FDTI_s") else None, "#e36209"),
                       ((tD + b["FRTI_s"]) if (b.get("FRTI_s") and tD is not None) else None, "#1a7f37")):
            if x is not None:
                ax.plot([x * 1e3] * 2, [y - 0.3, y + 0.3], color=col, lw=1.0, ls=":", zorder=2)
        F = min((ftti.get(g) for g in f["sg"] if ftti.get(g)), default=None)
        if F:
            xf = tF + F
            if xf <= lo + 1.6 * span:
                hi = max(hi, xf)
                marks.append((xf, y))
            else:
                far.append((F, y))
        ax.text(tF * 1e3, y - 0.3, f"{f['id']} ({VCOL_TXT.get(f['verdict'], f['verdict'])}): " + " · ".join(parts),
                fontsize=7, va="bottom", ha="left", color=VCOL.get(f["verdict"], "#57606a"), zorder=5)
    span = max(hi - lo, 1e-4)
    for xf, y in marks:
        ax.plot([xf * 1e3] * 2, [y - 0.32, y + 0.32], color="#cf222e", lw=1.4, ls="--", zorder=2)
        ax.text(xf * 1e3, y + 0.34, " FTTI", fontsize=6.5, color="#cf222e", va="top")
    x_right = (hi + 0.04 * span) * 1e3
    for F, y in far:
        ax.annotate(f"FTTI {F * 1e3:g} ms →", (x_right, y), fontsize=6.5, color="#cf222e", ha="right", va="center")
    ax.set_xlim((lo - 0.04 * span) * 1e3, x_right)
    ax.set_ylim(len(fsrs) - 0.45, -0.75)
    ax.set_yticks(range(len(fsrs)))
    ax.set_yticklabels([f["id"] for f in fsrs], fontsize=7)
    ax.set_xlabel(tr("시간 [ms]", "time [ms]"))
    ax.grid(True, axis="x", alpha=0.3)
    ax.set_title(tr("FSR별: 고장 t_F ✕ → 검출 t_D (주황 FDTI) → 안전 조건 도달·유지 t_S (초록 FRTI), 파랑 = 반응 명령 t_R, "
                    "점선 = 예산",
                    "per FSR: fault t_F ✕ → detection t_D (orange FDTI) → safe condition reached and held t_S (green "
                    "FRTI), blue = reaction command t_R, dotted = budgets"), fontsize=8)


VCOL_TXT = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "UNKNOWN", "NOT_APPLICABLE": "N/A"}


def fig_fault_compare(fig, cmp: dict, title: str | None = None):
    """Protection on / off and the reaction candidates from the same initial condition: torque, current, DC link."""
    _reset(fig, title)
    rows = cmp["rows"]
    axs = fig.subplots(3, 1, sharex=True)
    cols = ["#0969da", "#57606a", "#1a7f37", "#8250df", "#bf8700", "#e36209", "#cf222e"]
    for i, r in enumerate(rows):
        tr_ = r["trace"]
        tt = _ms(tr_["t"])
        lab = f"{r['candidate']} — {r['overall']}"
        c = cols[i % len(cols)]
        ls = "--" if r["candidate"] == "none" else "-"
        axs[0].plot(tt, tr_["T_shaft"], color=c, lw=1.2, ls=ls, label=lab)
        ia = np.max(np.abs(np.vstack([tr_["i_a"], tr_["i_b"], tr_["i_c"]])), axis=0)
        axs[1].plot(tt, ia, color=c, lw=1.0, ls=ls)
        axs[2].plot(tt, tr_["v_dc"], color=c, lw=1.2, ls=ls)
    axs[0].plot(_ms(rows[0]["trace"]["t"]), rows[0]["trace"]["T_request"], color=S.REQUEST, lw=1, ls=":",
                label=tr("요청", "request"))
    axs[0].set_ylabel(tr("축 토크 [N·m]", "shaft torque [N·m]"))
    axs[1].set_ylabel(tr("최대 |상전류| [A]", "max |phase current| [A]"))
    axs[2].set_ylabel("V_dc [V]")
    axs[2].set_xlabel(tr("시간 [ms]", "time [ms]"))
    axs[0].legend(loc="upper right", fontsize=6.5, ncol=2)
    for a in axs:
        a.grid(True, alpha=0.3)
    ok = cmp.get("passing_candidates") or []
    _note(axs[0], tr("모든 요구를 만족하는 후보: " + ", ".join(ok), "candidates meeting every requirement: " + ", ".join(ok))
          if ok else tr("모든 요구를 만족하는 후보 없음: 실행 가능한 안전 반응이 없습니다",
                        "no candidate meets every requirement: no executable safe reaction"), loc="lower left", fontsize=7)


def fig_fault_campaign(fig, camp: dict, title: str | None = None):
    """Verdict of every run over the first two axes (and the metric most sensitive to them), boundaries marked."""
    _reset(fig, title)
    runs = camp["runs"]
    axes = [a["path"] for a in camp["axes"]]
    ax1, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.2, 1.0]})
    x = [r["point"][axes[0]] for r in runs]
    y = [r["point"][axes[1]] for r in runs] if len(axes) > 1 else [0.0] * len(runs)
    num = all(isinstance(v, (int, float)) for v in x + y)
    if num:
        jit = 0.0
        for r, xi, yi in zip(runs, x, y):
            ax1.scatter(xi, yi + jit, s=60, color=VCOL.get(r.get("overall"), "#999999"), edgecolor="#1f2328", lw=0.5)
        for b in camp.get("boundaries") or []:
            if b["axis"] == axes[0]:
                yy = b["others"].get(axes[1], 0.0) if len(axes) > 1 else 0.0
                ax1.plot(b["between"], [yy, yy], color="#cf222e", lw=3, alpha=0.6)
            elif len(axes) > 1 and b["axis"] == axes[1]:
                xx = b["others"].get(axes[0], 0.0)
                ax1.plot([xx, xx], b["between"], color="#cf222e", lw=3, alpha=0.6)
    else:
        ax1.bar(range(len(runs)), [1] * len(runs), color=[VCOL.get(r.get("overall"), "#999") for r in runs])
    ax1.set_xlabel(axes[0], fontsize=8)
    ax1.set_ylabel(axes[1] if len(axes) > 1 else "", fontsize=8)
    ax1.set_title(tr("실행별 종합 판정 (빨간 막대 = 판정이 바뀌는 구간)", "overall verdict per run (red bar = where the "
                                                              "verdict changes)"), fontsize=8.5)
    ax1.legend(handles=[Patch(color=VCOL[v], label=v) for v in ("PASS", "FAIL", "UNKNOWN")], fontsize=7, loc="best")
    ax1.grid(True, alpha=0.3)
    w = camp.get("worst") or {}
    keys = [k for k in ("i_phase_peak_A", "v_dc_max_V", "i_bat_charge_max_A", "FHTI_ms") if k in w]
    labels = {"i_phase_peak_A": tr("최대 상전류 [A]", "peak phase current [A]"),
              "v_dc_max_V": tr("최대 V_dc [V]", "max V_dc [V]"),
              "i_bat_charge_max_A": tr("최대 충전 전류 [A]", "max charging current [A]"), "FHTI_ms": "FHTI [ms]"}
    ax2.axis("off")
    lines = [tr("최악값 (각각 한 실행에서):", "worst values (each from ONE run):")]
    for k in keys:
        lines.append(f"  {labels[k]}: {w[k]['value']:.4g}  (run {w[k]['run']})")
    if "FDTI_plus_FRTI_bound_ms" in w:
        lines.append(tr(f"  FDTI 최악 + FRTI 최악 = {w['FDTI_plus_FRTI_bound_ms']['value']:.4g} ms: 서로 다른 실행의 합 → 상한일 뿐 궤적 아님",
                        f"  worst FDTI + worst FRTI = {w['FDTI_plus_FRTI_bound_ms']['value']:.4g} ms: a sum over different "
                        f"runs → a bound, not a trajectory"))
    sc = camp.get("scope") or {}
    lines += ["", tr("탐색 집합: ", "explored set: ") + str(sc.get("explored_set")) + " — " + str(sc.get("explored_text", "")),
              tr("연속 영역: 보장되지 않음 (표본 사이는 증명하지 않음)", "continuous region: not established (nothing between "
                                                            "samples is proven)")]
    ax2.text(0.0, 1.0, "\n".join(lines), va="top", ha="left", fontsize=7.5, transform=ax2.transAxes, wrap=True)


def fig_fault_validation(fig, val: dict, title: str | None = None):
    """Every validation comparison as error / tolerance (log scale): below 1 passes."""
    _reset(fig, title)
    rows = val["rows"]
    ax = fig.subplots()
    ratios = [max(1e-9, r["abs_error"] / r["tolerance"]) if r["tolerance"] > 0 else 1e-9 for r in rows]
    cols = [VCOL["PASS"] if r["pass"] else VCOL["FAIL"] for r in rows]
    ypos = np.arange(len(rows))
    ax.barh(ypos, ratios, color=cols)
    ax.axvline(1.0, color="#cf222e", lw=1)
    ax.set_xscale("log")
    ax.set_yticks(ypos)
    ax.set_yticklabels([f"{r['case'][:34]} · {r['quantity'][:30]}" for r in rows], fontsize=6.5)
    ax.invert_yaxis()
    ax.set_xlabel(tr("오차 / 허용오차 (1 미만 통과)", "error / tolerance (below 1 passes)"))
    ax.grid(True, axis="x", alpha=0.3)
    kinds = sorted({r["reference_kind"] for r in rows})
    _note(ax, tr("참조: ", "references: ") + ", ".join(kinds), loc="lower right", fontsize=7)


def fig_fault_dependencies(fig, ind: dict, title: str | None = None):
    """Declared dependencies: mechanism x resource (a filled cell = the mechanism needs it); shared sensors marked."""
    _reset(fig, title)
    rows = ind["mechanisms"]
    res = [r["resource"] for r in ind["resources"]]
    M = np.zeros((len(rows), len(res)))
    for i, r in enumerate(rows):
        for j, x in enumerate(res):
            M[i, j] = 1.0 if x in r["needs"] else 0.0
    ax = fig.subplots()
    ax.imshow(M, cmap="Blues", vmin=0, vmax=1.4, aspect="auto")
    ax.set_xticks(range(len(res)))
    ax.set_xticklabels(res, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([f"{r['mechanism']} ({r['path']})" + (" ◆" if r["shares_sensors_with_control"] else "")
                        for r in rows], fontsize=7)
    ax.set_title(tr("선언된 의존성: 칸 = 메커니즘이 그 자원을 필요로 함 · ◆ = 제어와 같은 센서 사용(공통 원인)",
                    "declared dependencies: cell = the mechanism needs the resource · ◆ = shares sensors with control "
                    "(common cause)"), fontsize=8.5)
    for i in range(len(rows)):
        for j in range(len(res)):
            if M[i, j]:
                ax.text(j, i, "●", ha="center", va="center", fontsize=7, color="#0a3069")
