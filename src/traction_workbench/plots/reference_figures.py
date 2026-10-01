"""Figures of the reference-package verification: the verdicts by group, the evidence of one check (the physical
quantities with the safe-state conditions shaded and the six timeline instants on one clock), the torque-window
dashboard and the safe-state feasibility map.  They draw the ``api.reference_run`` result as it is; the conditions are
shaded with the tolerances the check itself used."""

from __future__ import annotations

import numpy as np
from matplotlib.patches import Rectangle

from ..i18n import tr
from . import style as S
from .fault_figures import _callouts, _peak_text
from .figures import _note, _reset, _side_title

VORDER = ("FAIL", "CONFLICT", "UNKNOWN", "MANUAL", "PASS", "NOT_APPLICABLE")
VCOL = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f", "CONFLICT": "#8250df", "MANUAL": "#0969da",
        "NOT_APPLICABLE": "#8c959f"}
BRIDGE = {0: "PWM", 1: "ASC-low", 2: "ASC-high", 3: "6SO", 4: "off", 5: "seq ASC-low", 6: "seq ASC-high", 7: "test"}
INSTANTS = (("t_fault", "#cf222e", "-"), ("t_criterion", "#bf8700", ":"), ("t_detect", "#e36209", "--"),
            ("t_reaction_req", "#8250df", "-."), ("t_gate_applied", "#0969da", "-"), ("t_physical_safe", "#1a7f37",
                                                                                      "--"))
CLASS_COL = {"both": "#1a7f37", "fw_only": "#bf8700", "asc_only": "#0969da", "other_only": "#8250df",
             "neither": "#cf222e", "undecided": "#8c959f"}


def _arr(tr_: dict, k: str):
    return np.array([np.nan if v is None else v for v in tr_.get(k) or []], dtype=float)


def fig_reference_summary(fig, summary: dict, title: str | None = None):
    """Verdicts per group (stacked, items counted once) and the totals per set (source requirements, internal,
    illustrative, proposals)."""
    from ..extensions.faultsim.refhier import set_name
    _reset(fig, title)
    t = S.theme()
    rows = summary.get("rows") or []
    groups = []
    for r in rows:
        if r.get("group", "") not in groups:
            groups.append(r.get("group", ""))
    ax = fig.add_subplot(111)
    if not rows:
        _note(ax, tr("검증 결과가 없습니다.", "no verification result"))
        return
    left = np.zeros(len(groups))
    for v in VORDER:
        n = np.array([sum(1 for r in rows if r.get("group", "") == g and r["verdict"] == v) for g in groups], float)
        if n.any():
            ax.barh(range(len(groups)), n, left=left, color=VCOL[v], label=v, height=0.7)
        left += n
    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels([g if len(g) <= 42 else g[:40] + "…" for g in groups], fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlabel(tr("항목 수", "items"))
    ax.legend(loc="lower right", fontsize=7.5, ncol=3)
    cnt = summary.get("counts") or {}
    lines = [f"{set_name(k)}: " + ", ".join(f"{v} {c[v]}" for v in VORDER if c.get(v)) for k, c in cnt.items()]
    _note(ax, "\n".join(lines) or "-", loc="upper right")
    ax.grid(axis="x", color=t["grid"], lw=0.5)


def fig_reference_evidence(fig, run: dict, check: dict | None = None, title: str | None = None):
    """One run as a check saw it: torque (with the TLSR band when declared), DC voltage and power, the bridge state
    and the supervisor's permit; C1..C3 violations shaded after the check's origin with the check's tolerances; the six
    instants of the fault timeline on the same clock."""
    _reset(fig, title)
    t = S.theme()
    tr_ = run.get("trace") or {}
    tt = _arr(tr_, "t") * 1e3
    axs = fig.subplots(3, 1, sharex=True, gridspec_kw={"height_ratios": [1.3, 1.0, 0.55]})
    ev = (check or {}).get("evidence") or {}
    judge = ev.get("judge") or {}
    T_em, T_sh = _arr(tr_, "T_em"), _arr(tr_, "T_shaft")
    ax = axs[0]
    ax.plot(tt, T_sh, color="#0969da", lw=1.2, label=tr("축 토크 (참값)", "shaft torque (truth)"))
    ax.plot(tt, T_em, color="#57606a", lw=0.8, ls="--", label=tr("전자기 토크", "electromagnetic torque"))
    if "T_request" in tr_:
        ax.plot(tt, _arr(tr_, "T_request"), color="#bf8700", lw=0.9, ls=":", label=tr("요청", "request"))
    if judge.get("t_min_Nm") is not None or judge.get("t_max_Nm") is not None:
        lo = judge.get("t_min_Nm") if judge.get("t_min_Nm") is not None else np.nanmin(T_sh)
        hi = judge.get("t_max_Nm") if judge.get("t_max_Nm") is not None else np.nanmax(T_sh)
        ax.axhspan(lo, hi, color="#1a7f37", alpha=0.06, lw=0, label=tr("TLSR 허용 (C4)", "TLSR band (C4)"))
    ax.set_ylabel("N·m")
    ax.legend(loc="upper right", fontsize=7)
    ax2 = axs[1]
    v = _arr(tr_, "v_dc")
    ax2.plot(tt, v, color="#8250df", lw=1.1, label="V_dc")
    ax2.set_ylabel("V")
    if "i_dc" in tr_:
        p = v * np.nan_to_num(_arr(tr_, "i_dc")) * 1e-3
        a3 = ax2.twinx()
        a3.plot(tt, p, color="#e36209", lw=0.8, label=tr("P_dc (브리지로)", "P_dc (into the bridge)"))
        a3.set_ylabel("kW")
        a3.axhline(0.0, color=t["grid"], lw=0.6)
        h1, l1 = ax2.get_legend_handles_labels()
        h2, l2 = a3.get_legend_handles_labels()
        ax2.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=7)
    ax4 = axs[2]
    br = _arr(tr_, "bridge")
    ax4.step(tt, br, where="post", color="#0969da", lw=1.0)
    codes = sorted({int(x) for x in br[np.isfinite(br)]})
    ax4.set_yticks(codes)
    ax4.set_yticklabels([BRIDGE.get(c, str(c)) for c in codes], fontsize=7)
    if "sys_permit" in tr_:
        ax4.step(tt, _arr(tr_, "sys_permit") * max(codes or [1]), where="post", color="#1a7f37", lw=0.8, ls=":",
                 label=tr("정상 토크 허가", "torque permit"))
        ax4.legend(loc="upper right", fontsize=7)
    ax4.set_xlabel("ms")
    # C1..C3 shading with the check's own tolerances, after its judgement window opens
    if judge:
        tT, tP = judge.get("torque_tol_Nm", 0.0), judge.get("power_tol_W", 0.0)
        P = v * np.nan_to_num(_arr(tr_, "i_dc"))
        gen = np.abs(T_em) > tT
        a = (ev.get("window_from_s") or 0.0) * 1e3
        for name, m, col in (("C1", gen & (P > tP), "#cf222e"), ("C2", gen & (P < -tP), "#e36209"),
                             ("C3", gen & ((br == 0) | (br == 7)), "#8250df")):
            m = m & (tt >= a)
            if m.any():
                axs[0].fill_between(tt, 0, 1, where=m, transform=axs[0].get_xaxis_transform(), color=col, alpha=0.12,
                                    lw=0, label=f"{name} " + tr("위반", "violated"))
        axs[0].legend(loc="upper right", fontsize=7)
    tl = run.get("timeline") or {}
    for k, col, ls in INSTANTS:
        if tl.get(k) is not None:
            for a_ in axs:
                a_.axvline(tl[k] * 1e3, color=col, lw=0.9, ls=ls, alpha=0.85)
            axs[0].text(tl[k] * 1e3, 1.0, k[2:], transform=axs[0].get_xaxis_transform(), rotation=90, fontsize=6.5,
                        va="top", ha="right", color=col)
    for a_ in axs:
        a_.grid(color=t["grid"], lw=0.5)
        if len(tt) > 1:
            a_.set_xlim(tt[0], tt[-1])
    # the extremes after the fault (the whole run without one), with value and instant
    t0 = tl.get("t_fault")
    k = np.where(np.isfinite(tt) & (tt >= (t0 * 1e3 if t0 is not None else -np.inf)))[0]

    def ext(y, which):
        yy = y[k]
        if not len(k) or not np.isfinite(yy).any():
            return None
        j = int(k[np.nanargmax(yy) if which == "max" else np.nanargmin(yy)])
        return tt[j], float(y[j])
    ent = []
    for which in ("max", "min"):
        e = ext(T_sh, which)
        if e:
            ent.append((axs[0], e[0], e[1], _peak_text(e[1], "N*m", e[0] * 1e-3, t0, f"T {which} "), "#0969da"))
    _callouts(axs[0], ent)
    ent = []
    for which in ("max", "min"):
        e = ext(v, which)
        if e:
            ent.append((ax2, e[0], e[1], _peak_text(e[1], "V", e[0] * 1e-3, t0, f"V_dc {which} "), "#8250df"))
    _callouts(ax2, ent)
    if check:
        txt = f"{check.get('verdict')} · {check.get('label') or check.get('check')}"
        rs = [x for x in check.get("reasons") or [] if x][:2]
        _side_title(axs[0], txt + (" — " + " · ".join(r[:160] for r in rs) if rs else ""), fontsize=8, max_lines=3)


def fig_torque_window(fig, run: dict, title: str | None = None):
    """The torque-window dashboard: request, estimate, the signed window with its tolerance and the integral."""
    _reset(fig, title)
    t = S.theme()
    tr_ = run.get("trace") or {}
    tt = _arr(tr_, "t") * 1e3
    ax, ax2 = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [1.4, 0.7]})
    if "tw_hi" in tr_:
        hi, lo, tol = _arr(tr_, "tw_hi"), _arr(tr_, "tw_lo"), np.nan_to_num(_arr(tr_, "tw_tol"))
        ax.fill_between(tt, lo, hi, color="#1a7f37", alpha=0.10, lw=0, label=tr("허용 창", "window"))
        ax.plot(tt, hi - tol, color="#1a7f37", lw=0.6, ls=":", label=tr("허용오차 적용 경계", "edge with tolerance"))
        ax.plot(tt, lo + tol, color="#1a7f37", lw=0.6, ls=":")
    for k, col, lab in (("T_request", "#bf8700", tr("요청", "request")), ("T_est_mon", "#e36209",
                                                                             tr("감시 추정", "monitor estimate")),
                        ("T_shaft", "#0969da", tr("축 토크 (참값)", "shaft torque (truth)"))):
        if k in tr_:
            ax.plot(tt, _arr(tr_, k), color=col, lw=1.0, label=lab)
    ax.set_ylabel("N·m")
    ax.legend(loc="upper right", fontsize=7)
    if "tw_int" in tr_:
        ax2.plot(tt, _arr(tr_, "tw_int"), color="#8250df", lw=1.0, label=tr("편차 적분", "deviation integral"))
        ax2.set_ylabel("N·m·s")
        ax2.legend(loc="upper left", fontsize=7)
    else:
        _note(ax2, tr("이 실행에는 적분 감시가 없습니다.", "no integral monitor in this run"))
    ax2.set_xlabel("ms")
    for a in (ax, ax2):
        a.grid(color=t["grid"], lw=0.5)


def fig_feasibility_map(fig, fmap: dict, title: str | None = None):
    """The safe-state feasibility map: one cell per speed x DC voltage, colored by which reaction reaches the
    physical safe state there (both / FW only / ASC only / neither / undecided)."""
    _reset(fig, title)
    t = S.theme()
    ax = fig.add_subplot(111)
    cells = fmap.get("cells") or []
    if not cells:
        _note(ax, tr("타당성 지도가 없습니다.", "no feasibility map"))
        return
    sp = sorted({c["speed_rpm"] for c in cells})
    vd = sorted({c["vdc_V"] for c in cells})
    temps = sorted({c.get("temperature_C") for c in cells}, key=lambda x: (x is None, x))
    w = 0.8 / len(temps)                       # one slice per temperature inside a cell
    for c in cells:
        x, y = sp.index(c["speed_rpm"]), vd.index(c["vdc_V"])
        x0 = x - 0.4 + temps.index(c.get("temperature_C")) * w
        ax.add_patch(Rectangle((x0, y - 0.4), w, 0.8, color=CLASS_COL.get(c["class"], "#8c959f")))
        lab = c["class"].replace("_", " ") + (f"\n{c['temperature_C']:g} °C" if c.get("temperature_C") is not None
                                              else "")
        ax.text(x0 + w / 2, y, lab, ha="center", va="center", fontsize=6.5, color="white")
    ax.set_xlim(-0.6, len(sp) - 0.4)
    ax.set_ylim(-0.6, len(vd) - 0.4)
    ax.set_xticks(range(len(sp)))
    ax.set_xticklabels([f"{s:g}" for s in sp])
    ax.set_yticks(range(len(vd)))
    ax.set_yticklabels([f"{v:g}" for v in vd])
    ax.set_xlabel(tr("속도 [rpm]", "speed [rpm]"))
    ax.set_ylabel("V_dc [V]")
    _note(ax, fmap.get("basis", ""), loc="lower left", fontsize=6.5)
    ax.grid(False)
    ax.set_facecolor(t["bg"])
