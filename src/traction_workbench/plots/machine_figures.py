"""Figures for machine design around a validated reference (handoff section 10).

Every figure draws the result dictionaries of ``api.machine_trade`` / ``api.winding`` / ``api.concept_sizing`` as
they are: the trade study as T-n envelopes plus a candidate x requirement margin matrix, the winding as a star of
slots, the slot layout and the three-phase MMF spectrum, the concept sizing as a D-L envelope.
"""

from __future__ import annotations

import math

import numpy as np
from matplotlib.patches import Patch, Rectangle

from ..i18n import tr
from . import style as S
from .figures import _note, _reset

CAND = ("#1f2328", "#0969da", "#cf222e", "#1a7f37", "#8250df", "#bf8700", "#0b7285", "#e36209")
PH = {"A": S.PHASE[0], "B": S.PHASE[1], "C": S.PHASE[2]}


def _cand_color(i: int, t: dict) -> str:
    c = CAND[i % len(CAND)]
    return t["fg"] if i == 0 else c


def _fmt(v, unit: str) -> str:
    if v is None:
        return "—"
    a = abs(v)
    s = f"{v:.0f}" if a >= 100 else (f"{v:.1f}" if a >= 10 else f"{v:.2f}")
    return f"{s} {unit}".strip()


# ---------------------------------------------------------------------------------------------- trade study

def fig_machine_trade(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    rows = [r for r in res["rows"] if "checks" in r]
    ax, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.0, 1.35]})
    for i, r in enumerate(rows):
        env = r.get("envelope") or []
        xs = [e["speed_rpm"] for e in env if e["torque_Nm"] is not None]
        ys = [e["torque_Nm"] for e in env if e["torque_Nm"] is not None]
        if xs:
            ax.plot(xs, ys, color=_cand_color(i, t), lw=2.2 if i == 0 else 1.5, ls="-" if i == 0 else "--",
                    marker="o", ms=3, label=r["candidate"] + (tr(" (기준)", " (reference)") if not r["lineage"]["derived"] else ""))
    for c in res["checks"]:
        if c["kind"] in ("capability", "point", "copper") and c.get("torque_Nm") is not None:
            ax.plot([c["speed_rpm"]], [c["torque_Nm"]], marker="X", ms=9, color=S.REQUEST, ls="none", zorder=5)
            ax.annotate(c["name"], (c["speed_rpm"], c["torque_Nm"]), xytext=(5, 5), textcoords="offset points",
                        fontsize=6.5, color=S.REQUEST)
    vdc = next((r.get("envelope_Vdc_V") for r in rows if r.get("envelope_Vdc_V")), None)
    ax.set_xlabel(tr("속도 [rpm]", "speed [rpm]"))
    ax.set_ylabel(tr("축 토크 [N·m]", "shaft torque [N·m]"))
    ax.set_title(tr(f"정책 T–n 포락선 (Vdc {vdc:g} V, 표시용 witness)" if vdc else "정책 T–n 포락선",
                    f"policy T-n envelope (Vdc {vdc:g} V, display witness)" if vdc else "policy T-n envelope"),
                 fontsize=9)
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=7, loc="upper right")
    ax.grid(True, alpha=0.4)
    # margin matrix
    names = [c["name"] for c in res["checks"]]
    nr, nc = len(rows), len(names)
    ax2.set_xlim(0, nc)
    ax2.set_ylim(0, nr)
    ax2.invert_yaxis()
    for i, r in enumerate(rows):
        for j, n in enumerate(names):
            c = r["checks"].get(n, {})
            st = c.get("status", "UNKNOWN")
            ax2.add_patch(Rectangle((j, i), 1, 1, fc=S.CLAIM.get(st, "#9e9e9e"), alpha=0.28, ec=t["grid"], lw=0.8))
            rel = c.get("rel_margin")
            txt = _fmt(c.get("value"), c.get("unit", "")) + ("\n" + f"{rel * 100:+.1f}%" if rel is not None else "\n" + st)
            ax2.text(j + 0.5, i + 0.5, txt, ha="center", va="center", fontsize=6.6, color=t["fg"])
            if r.get("binding") == n:
                ax2.add_patch(Rectangle((j + 0.03, i + 0.05), 0.94, 0.9, fill=False, ec=t["fg"], lw=2.0))
    ax2.set_xticks(np.arange(nc) + 0.5)
    ax2.set_xticklabels([n.replace(" @ ", "\n@ ") for n in names], fontsize=6.6, rotation=0)
    ax2.set_yticks(np.arange(nr) + 0.5)
    ax2.set_yticklabels([r["candidate"] + ("" if r["all_feasible"] else " ✗") for r in rows], fontsize=7.5)
    ax2.tick_params(length=0)
    for sp in ax2.spines.values():
        sp.set_visible(False)
    ax2.set_title(tr("같은 요구·온도·전원에서의 결합 여유 (굵은 테두리 = 구속 요구)",
                     "coupled requirement margins, same requirements / temperatures / sources (bold = binding)"),
                  fontsize=9)
    ax2.legend(handles=[Patch(fc=S.CLAIM[k], alpha=0.35, label=k) for k in ("FEASIBLE", "INFEASIBLE", "UNKNOWN")],
               fontsize=6.5, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3, frameon=False)
    _note(ax, tr("파생 후보 = 기준의 스케일링 (검증 안 됨)\n무효화 데이터는 표 참조",
                 "derived candidates = scaled reference (not validated)\ninvalidated data listed in the table"),
          loc="lower left", fontsize=6.5)
    return fig


# ---------------------------------------------------------------------------------------------- winding

def fig_winding(fig, w: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    gs = fig.add_gridspec(2, 2, height_ratios=[1.35, 0.55], width_ratios=[1.0, 1.25])
    ax = fig.add_subplot(gs[0, 0])
    ax3 = fig.add_subplot(gs[0, 1])
    ax2 = fig.add_subplot(gs[1, :])
    Q = w["Q"]
    groups: dict = {}
    for s, a in enumerate(w["slot_angles_deg"]):
        groups.setdefault(round(a, 6), []).append(s)
    for a, slots in sorted(groups.items()):
        ph = w["slot_top"][slots[0]]
        col = PH[ph[0]]
        r = 1.0
        th = math.radians(a)
        ax.annotate("", xy=(r * math.cos(th), r * math.sin(th)), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="-|>", color=col, lw=1.4 if ph[1] == "+" else 1.0,
                                    ls="-" if ph[1] == "+" else "--"))
        lab = ",".join(str(s + 1) for s in slots[:4]) + ("…" if len(slots) > 4 else "")
        ax.text(1.16 * math.cos(th), 1.16 * math.sin(th), f"{lab}\n{ph}", ha="center", va="center", fontsize=5.8,
                color=col)
    for k, ph in enumerate(("A", "B", "C")):
        ang = math.radians(w["phase_axes_deg"][ph])
        ax.plot([0, 0.62 * math.cos(ang)], [0, 0.62 * math.sin(ang)], color=PH[ph], lw=3.2, alpha=0.55,
                solid_capstyle="round", label=tr(f"상 {ph} 축", f"phase {ph} axis"))
    ax.set_xlim(-1.45, 1.45)
    ax.set_ylim(-1.45, 1.45)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.legend(fontsize=6.5, loc="lower left", frameon=False)
    ax.set_title(tr(f"Star of slots  Q={Q}, 2p={w['poles']}, y={w['y']}  (+ 실선 / − 점선)",
                    f"star of slots  Q={Q}, 2p={w['poles']}, y={w['y']}  (+ solid / − dashed)"), fontsize=9)
    # spectrum
    harm = [h for h in w["harmonics"] if h["kw"] > 0.01]
    x = np.array([h["nu_el"] for h in harm])
    y = np.array([h["kw"] for h in harm])
    cols = []
    for h in harm:
        if h["order_mech"] == w["p"]:
            cols.append(S.ACCENT)
        elif h["order_mech"] < w["p"]:
            cols.append("#cf222e")
        else:
            cols.append(t["muted"])
    width = 0.8 / max(w["p"], 1) if any(h["order_mech"] < w["p"] for h in harm) or w["t_periodicity"] < w["p"] else 0.5
    bars = ax3.bar(x, y, width=max(width, 0.12), color=cols, alpha=0.85)
    for b, h in zip(bars, harm):
        if h["direction"] == "backward":
            b.set_hatch("///")
    if w["type"] == "fractional-slot":
        ax3.set_xlim(0, min(13.5, max(x) + 0.5) if len(x) else 13.5)     # low orders drive rotor loss / NVH
    ax3.set_xlabel(tr("전기 차수 ν = 기계 차수 / p", "electrical order ν = mechanical order / p"))
    ax3.set_ylabel(tr("3상 MMF 권선계수", "three-phase MMF winding factor") if w["balanced"]
                   else tr("A상 권선계수 (평형 3상 아님)", "phase-A winding factor (not a balanced 3-phase)"))
    if not w["balanced"]:
        _note(ax3, tr("불가능/불평형 조합: 3상 MMF 스펙트럼 없음\n(A상 계수만 표시)",
                      "infeasible / unbalanced combination: no three-phase\nMMF spectrum (phase-A factors only)"),
              loc="upper left")
    ax3.set_ylim(0, 1.08)
    ax3.grid(True, axis="y", alpha=0.4)
    ax3.legend(handles=[Patch(fc=S.ACCENT, label=tr(f"기본파 kw1 = {w['kw1']:.4f}", f"working kw1 = {w['kw1']:.4f}")),
                        Patch(fc="#cf222e", label=tr("저차 (서브하모닉)", "sub-harmonics")),
                        Patch(fc=t["muted"], label=tr("고조파", "harmonics")),
                        Patch(fc="none", ec=t["fg"], hatch="///", label=tr("역방향 회전", "backward rotating"))],
               fontsize=6.5, loc="upper right")
    ax3.set_title(tr(f"{w['type']}, q = {w['q']:.3g}, t = {w['t_periodicity']}, "
                     f"{'평형' if w['balanced'] else '불평형/불가'}",
                     f"{w['type']}, q = {w['q']:.3g}, t = {w['t_periodicity']}, "
                     f"{'balanced' if w['balanced'] else 'unbalanced / infeasible'}"), fontsize=9)
    # slot layout (double layer)
    for s in range(Q):
        for layer, arr, y0 in (("top", w["slot_top"], 0.5), ("bottom", w["slot_bottom"], 0.0)):
            ph = arr[s] or "?"
            col = PH.get(ph[0], "#9e9e9e")
            ax2.add_patch(Rectangle((s, y0), 0.92, 0.45, fc=col, alpha=0.75 if ph.endswith("+") else 0.35,
                                    ec=t["grid"], lw=0.5))
            if Q <= 60:
                ax2.text(s + 0.46, y0 + 0.225, ph, ha="center", va="center", fontsize=5.2 if Q > 36 else 6.2,
                         color=t["fg"])
    ax2.set_xlim(-0.2, Q + 0.2)
    ax2.set_ylim(-0.05, 1.0)
    step = 1 if Q <= 24 else (2 if Q <= 48 else 4)
    ax2.set_xticks(np.arange(0, Q, step) + 0.46)
    ax2.set_xticklabels([str(s + 1) for s in range(0, Q, step)], fontsize=6)
    ax2.set_yticks([0.225, 0.725])
    ax2.set_yticklabels([tr("하층", "bottom"), tr("상층", "top")], fontsize=7)
    ax2.tick_params(length=0)
    for sp in ax2.spines.values():
        sp.set_visible(False)
    extra = ""
    if "N_series" in w:
        extra = tr(f" · 직렬 턴 {w['N_series']:g} (코일당 {w['turns_per_coil']}, 병렬 {w['parallel_paths']}) · 유효 {w['N_eff']:.2f}",
                   f" · series turns {w['N_series']:g} ({w['turns_per_coil']}/coil, {w['parallel_paths']} paths) · "
                   f"effective {w['N_eff']:.2f}")
    ax2.set_title(tr(f"이층 권선 배치 (슬롯 번호), 상순 {w['phase_sequence']}", f"double-layer layout (slot number), "
                     f"{w['phase_sequence']}") + extra, fontsize=8.5)
    return fig


# ---------------------------------------------------------------------------------------------- concept sizing

def fig_concept_sizing(fig, cs: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots(1, 1)
    rows = cs["rows"]
    sig = sorted({r["sigma_kPa"] for r in rows})
    asp = sorted({r["L_over_D"] for r in rows})
    marks = ("o", "s", "^", "D", "v", "P")
    import matplotlib as mpl
    cm = mpl.colormaps["viridis"]
    for r in rows:
        ci = sig.index(r["sigma_kPa"]) / max(len(sig) - 1, 1)
        mi = asp.index(r["L_over_D"]) % len(marks)
        ok = r.get("tip_speed_ok", True)
        ax.plot(r["D_rotor_mm"], r["L_stack_mm"], marker=marks[mi], ms=8, ls="none", color=cm(0.1 + 0.8 * ci),
                mec=t["fg"] if ok else "#cf222e", mew=0.6 if ok else 2.0)
        ax.annotate(f"{r['rotor_volume_L']:.2f} L", (r["D_rotor_mm"], r["L_stack_mm"]), xytext=(5, -2),
                    textcoords="offset points", fontsize=6.3, color=t["muted"])
    dmax = cs.get("D_max_tip_mm")
    D = np.linspace(min(r["D_rotor_mm"] for r in rows) * 0.8, max(r["D_rotor_mm"] for r in rows) * 1.15, 100)
    for s in sig:
        Vr = cs["T_Nm"] / (2 * s * 1e3)
        L = Vr / (math.pi * (D / 2e3) ** 2) * 1e3
        ax.plot(D, L, color=cm(0.1 + 0.8 * sig.index(s) / max(len(sig) - 1, 1)), lw=1, alpha=0.6,
                label=tr(f"σ = {s:g} kPa (V_r = {Vr * 1e3:.2f} L)", f"σ = {s:g} kPa (V_r = {Vr * 1e3:.2f} L)"))
    ytop = max(r["L_stack_mm"] for r in rows) * 1.35
    for a in asp:
        ax.plot(D, a * D, color=t["grid"], lw=0.8, ls=":")
        xl = min(D[-1], 0.97 * ytop / a)
        ax.text(xl, a * xl, f" L/D = {a:g}", fontsize=6.5, color=t["muted"], va="center",
                ha="left" if xl == D[-1] else "right")
    ax.set_xlabel(tr("회전자 직경 D [mm]", "rotor diameter D [mm]"))
    ax.set_ylabel(tr("적층 길이 L [mm]", "stack length L [mm]"))
    ax.set_ylim(0, ytop)
    ax.set_xlim(D[0], D[-1] * 1.04)
    if dmax:
        ax.axvline(dmax, color="#cf222e", ls="--", lw=1.2)
        ax.text(dmax, ax.get_ylim()[1] * 0.97, tr(f" 원주속도 한계 {cs['tip_speed_limit_m_s']:g} m/s @ "
                                                  f"{cs['n_max_rpm']:g} rpm: D ≤ {dmax:.0f} mm",
                                                  f" tip-speed limit {cs['tip_speed_limit_m_s']:g} m/s @ "
                                                  f"{cs['n_max_rpm']:g} rpm: D ≤ {dmax:.0f} mm"),
                color="#cf222e", fontsize=7, va="top")
    ax.grid(True, alpha=0.35)
    from matplotlib.lines import Line2D
    hs, _ = ax.get_legend_handles_labels()
    hs += [Line2D([], [], marker=marks[i % len(marks)], ls="none", color=t["muted"], label=f"L/D = {a:g}")
           for i, a in enumerate(asp)]
    hs.append(Line2D([], [], marker="o", ls="none", mfc="none", mec="#cf222e", mew=2.0,
                     label=tr("원주속도 초과", "tip speed exceeded")))
    ax.legend(handles=hs, fontsize=7, loc="upper left")
    ax.set_title(tr(f"개념 사이징 T = 2σV_r, T = {cs['T_Nm']:g} N·m (정격 아님: 열·감자·기계 근거 별도)",
                    f"concept sizing T = 2σV_r, T = {cs['T_Nm']:g} N·m (not a rating: thermal / demag / "
                    f"mechanical evidence separate)"), fontsize=9)
    return fig
