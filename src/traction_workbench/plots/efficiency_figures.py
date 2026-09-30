"""Figures for efficiency by control volume (module-efficiency addendum).

Every figure draws the result dictionaries of ``api.efficiency`` / ``api.efficiency_map`` /
``api.efficiency_mission`` / ``api.module_compare`` as they are: the point power flow with the five boundaries,
boundary efficiency maps (feasible cells only, UNKNOWN hatched), the mission energy ledger per direction, and the
module A/B comparison with the declared error budget.
"""

from __future__ import annotations

import math
import textwrap

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from ..i18n import tr
from . import style as S
from .figures import _note, _reset

BND = (("inverter", lambda: tr("인버터 DC↔AC", "inverter DC↔AC")), ("motor", lambda: tr("모터 AC↔축", "motor AC↔shaft")),
       ("inverter_motor", lambda: tr("인버터+모터 DC↔축", "inverter+motor DC↔shaft")),
       ("reducer", lambda: tr("감속기 축↔출력", "reducer shaft↔output")),
       ("edrive", lambda: tr("eDrive DC↔출력", "eDrive DC↔output")))
ST_COL = {"DEFINED": "#1a7f37", "N/A": "#6e7781", "UNKNOWN": "#b7791f", "INCONSISTENT": "#cf222e"}


def _kw(v):
    return None if v is None else v / 1e3


# ---------------------------------------------------------------------------------------------- point

def fig_efficiency_point(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    led = res.get("ledger")
    if not led:
        ax = fig.subplots()
        ax.text(0.5, 0.5, tr("운전점 없음: ", "no operating point: ") + str(res.get("reason", "")), ha="center",
                va="center", transform=ax.transAxes, wrap=True)
        ax.set_axis_off()
        return fig
    gs = fig.add_gridspec(2, 2, width_ratios=[1.35, 1.0], height_ratios=[0.42, 1.0])
    axf = fig.add_subplot(gs[0, 0])
    ax = fig.add_subplot(gs[1, 0])
    ax2 = fig.add_subplot(gs[:, 1])
    p = led["ports_W"]
    # port flow strip: electrical -> mechanical positive; a '?' port is not established
    axf.set_axis_off()
    axf.set_xlim(0, 4)
    axf.set_ylim(0, 1)
    ports = [("P_dc", tr("HV DC 단자", "HV DC terminal")), ("P_ac", tr("모터 AC 단자", "motor AC terminal")),
             ("P_m", tr("모터 축", "motor shaft")), ("P_o", tr("선언 출력", "declared output"))]
    for i, (k, lab) in enumerate(ports):
        v = p[k]
        col = S.ACCENT if v is not None else ST_COL["UNKNOWN"]
        axf.text(i + 0.5, 0.62, f"{k}\n{'?' if v is None else f'{v / 1e3:+.2f} kW'}", ha="center", va="center",
                 fontsize=8, color=t["fg"], bbox=dict(boxstyle="round,pad=0.4", fc=t["panel"], ec=col, lw=1.6))
        axf.text(i + 0.5, 0.14, lab, ha="center", va="center", fontsize=7, color=t["muted"])
        if i < 3:
            axf.annotate("", xy=(i + 1.18, 0.62), xytext=(i + 0.82, 0.62),
                         arrowprops=dict(arrowstyle="-|>", color=t["muted"], lw=1.2))
    axf.set_title(tr(f"포트 전력 (전기→기계 +) · 코어 에너지 모드 {led['energy_mode_core']}\n"
                     f"T_em·ω = {p['P_em'] / 1e3:.3f} kW (축 전력 아님)",
                     f"port powers (electrical→mechanical +) · core energy mode {led['energy_mode_core']}\n"
                     f"T_em·ω = {p['P_em'] / 1e3:.3f} kW (not shaft power)"), fontsize=8.5)
    # loss breakdown (W), unknown items explicit
    colors = {"inverter": "#6e7781", "motor": S.ACCENT, "reducer": "#8250df"}
    items = led["loss_items"]
    y = np.arange(len(items))[::-1]
    for yy, it in zip(y, items):
        if it["W"] is None and it.get("lower_bound_W") is not None:
            ax.barh(yy, it["lower_bound_W"], color="none", ec=colors.get(it["boundary"], t["muted"]), hatch="..",
                    lw=1.0)
            ax.text(it["lower_bound_W"], yy, tr(f"  ≥ {it['lower_bound_W']:.1f} W (R_dc 하한, 정확값 없음)",
                                                f"  ≥ {it['lower_bound_W']:.1f} W (R_dc lower bound, no exact value)"),
                    va="center", fontsize=7.5, color=ST_COL["UNKNOWN"])
        elif it["W"] is None and it.get("upper_bound_W") is not None:
            ax.barh(yy, it["upper_bound_W"], color="none", ec="#8250df", ls="--", lw=1.0)
            ax.text(it["upper_bound_W"], yy, tr(f"  ≤ {it['upper_bound_W']:.1f} W (선언 상한, 값 아님)",
                                                f"  ≤ {it['upper_bound_W']:.1f} W (declared bound, not a value)"),
                    va="center", fontsize=7.5, color="#8250df")
        elif it["W"] is None:
            ax.barh(yy, 1.0, color="none", ec=ST_COL["UNKNOWN"], hatch="//", lw=1.0)
            ax.text(1.0, yy, tr("  미상 (0으로 두지 않음)", "  unknown (not set to zero)"), va="center", fontsize=7.5,
                    color=ST_COL["UNKNOWN"])
        else:
            ax.barh(yy, it["W"], color=colors.get(it["boundary"], t["muted"]), alpha=0.85)
            ax.text(it["W"], yy, f"  {it['W']:.1f} W", va="center", fontsize=7.5, color=t["fg"])
    ax.set_yticks(y)
    ax.set_yticklabels([f"{it['boundary']}: {it['item']}" for it in items], fontsize=7)
    ax.set_xlabel(tr("손실 [W]", "loss [W]"))
    known = [v for it in items for v in (it["W"], it.get("upper_bound_W"), it.get("lower_bound_W")) if v is not None]
    ax.set_xlim(0, (max(known) if known else 1.0) * 1.6)
    tot = led["loss_total_W"]
    ax.set_title(tr(f"손실 원장: 알려진 소계 {led['loss_known_subtotal_W']:.1f} W" +
                    (" = 총 손실" if tot is not None else f" + 미상 {len(led['loss_unknown_items'])}항 (총 손실 미확정)"),
                    f"loss ledger: known subtotal {led['loss_known_subtotal_W']:.1f} W" +
                    (" = total" if tot is not None else f" + {len(led['loss_unknown_items'])} unknown (total not established)")),
                 fontsize=8.5)
    # boundary table, written top-down with wrapped lines (a long definition never runs into the next column)
    ax2.set_axis_off()
    b = led["boundaries"]
    pos = {"y": 0.98}

    def put(text, x=0.0, size=7.6, color=None, weight="normal", width=None, dy=0.046, ha="left", keep=False):
        for ln in (textwrap.wrap(text, width) if width else [text]) or [""]:
            ax2.text(x, pos["y"], ln, fontsize=size, color=color or t["fg"], fontweight=weight, ha=ha, va="top",
                     transform=ax2.transAxes)
            if not keep:
                pos["y"] -= dy

    put(tr("경계", "boundary"), size=8, weight="bold", keep=True)
    put("η", x=0.98, size=8, weight="bold", ha="right")
    for key, lab in BND:
        r = b[key]
        col = ST_COL.get(r["status"], t["fg"])
        put(lab(), size=7.8, keep=True)
        put(f"{100 * r['eta']:.3f}%" if r["eta"] is not None and r["status"] == "DEFINED" else r["status"], x=0.98,
            size=8, color=col, weight="bold", ha="right", dy=0.04)
        txt = r["definition"] if r["status"] == "DEFINED" else r.get("reason", "")
        iv = r.get("eta_interval_incl_pwm_hf")
        if iv:                                        # the PWM harmonic loss as an interval, never a single value
            txt = (txt or "") + tr("  ·  PWM 고조파 포함 η ∈ ", "  ·  incl. PWM harmonic η ∈ ") + \
                f"[{'—' if iv[0] is None else f'{100 * iv[0]:.3f}'}, {100 * iv[1]:.3f}] %"
        put(txt or "—", x=0.04, size=6.6, color=t["muted"], width=62, dy=0.036)
        pos["y"] -= 0.012
    res_t = b.get("telescoping_residuals") or {}
    put(tr("망원 항등식 잔차: ", "telescoping residuals: ") +
        (", ".join(f"{k} {v:.1e}" for k, v in res_t.items()) or tr("적용 안 됨", "not applicable")),
        size=7, color=t["muted"], width=70, dy=0.036)
    for k, v in (led.get("aux_metrics") or {}).items():
        put(f"{k}: {100 * v:.2f}%", size=7, width=70, dy=0.036)
    sc = led.get("inverter_scope") or {}
    put(tr("인버터 경계: ", "inverter boundary: ") + str(sc.get("model")), size=6.3, color=t["muted"], width=64,
        dy=0.032)
    put(tr("제외: ", "excluded: ") + ", ".join(sc.get("excluded", [])), size=6.3, color=t["muted"], width=64,
        dy=0.032)
    ax2.set_title(tr("다섯 경계의 효율 (각 경계의 두 포트로 판정)", "five boundary efficiencies (each on its own ports)"),
                  fontsize=9)
    return fig


# ---------------------------------------------------------------------------------------------- maps

def fig_efficiency_maps(fig, mp: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    sp = np.asarray(mp["speeds_rpm"], float)
    tq = np.asarray(mp["torques_Nm"], float)
    SP, TQ = np.meshgrid(sp, tq)
    st = np.asarray(mp["status"])
    unk = st == "UNKNOWN"
    axs = fig.subplots(2, 3, sharex=True, sharey=True)
    panels = list(BND) + [("loss", lambda: tr("알려진 손실 소계 [kW]", "known loss subtotal [kW]"))]
    for ax, (key, lab) in zip(axs.ravel(), panels):
        Z = (np.asarray(mp["loss_known_W"], float) / 1e3) if key == "loss" else 100 * np.asarray(mp["grids"][key], float)
        Z = np.where(st == "INFEASIBLE", np.nan, Z)
        fin = np.isfinite(Z)
        if fin.sum() >= 4:
            lo, hi = np.nanmin(Z), np.nanmax(Z)
            if key != "loss":
                lo = max(lo, 70.0)
            lv = np.linspace(lo, hi + 1e-6, 12) if hi > lo else np.linspace(lo - 1, lo + 1, 3)
            cf = ax.contourf(SP, TQ, np.where(fin, np.clip(Z, lo, None), np.nan), levels=lv, cmap=t["cmap"])
            fig.colorbar(cf, ax=ax, pad=0.01, shrink=0.9).ax.tick_params(labelsize=6)
        else:
            ax.text(0.5, 0.5, tr("값 없음 (데이터/모델 부족)", "no values (data / model missing)"), ha="center", va="center",
                    transform=ax.transAxes, fontsize=7.5, color=t["muted"])
        if unk.any():
            ax.contourf(SP, TQ, (unk & fin).astype(float), levels=[0.5, 1.5], colors="none", hatches=["xx"])
        ax.axhline(0, color=t["fg"], lw=0.6)
        ax.set_title(lab() + ("" if key == "loss" else " [%]"), fontsize=8)
        ax.tick_params(labelsize=6.5)
    for ax in axs[1]:
        ax.set_xlabel(tr("속도 [rpm]", "speed [rpm]"), fontsize=7.5)
    for ax in axs[:, 0]:
        ax.set_ylabel(tr("축 토크 [N·m]", "shaft torque [N·m]"), fontsize=7.5)
    axs[0, 0].legend(handles=[Patch(fc="none", ec=t["muted"], hatch="xx", label=tr("판정 UNKNOWN (참고값)", "claim UNKNOWN (reference)")),
                              Patch(fc=t["bg"], ec=t["grid"], label=tr("빈칸 = INFEASIBLE / 정의 안 됨", "blank = INFEASIBLE / undefined"))],
                     loc="upper right", fontsize=6, framealpha=0.9)
    fig.suptitle(tr(f"경계별 모델 효율 지도 (최소전류 정책점) · Vdc {mp['Vdc_V']:g} V · 구동: 출력/입력, 회생: |입력|/|출력|",
                    f"model efficiency by boundary (minimum-current policy points) · Vdc {mp['Vdc_V']:g} V · motoring "
                    f"out/in, regen |in|/|out|"), fontsize=9.5)
    return fig


# ---------------------------------------------------------------------------------------------- mission

def fig_efficiency_mission(fig, ms: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    e = ms["energy"]
    ax, ax2, axs = fig.subplots(1, 3, gridspec_kw={"width_ratios": [1.2, 0.9, 0.75]})
    segs = ms["segments"]
    tt = 0.0
    for s in segs:
        d = s["duration_s"]
        for key, col, lab in (("P_dc", S.ACCENT, "P_dc"), ("P_o", S.PHASE[2], "P_o")):
            v = s["ports_W"].get(key)
            if v is not None:
                ax.plot([tt, tt + d], [v / 1e3, v / 1e3], color=col, lw=2 if key == "P_dc" else 1.4)
        if s["status"] != "FEASIBLE":
            ax.axvspan(tt, tt + d, color="#cf222e", alpha=0.12, lw=0)
        tt += d
    ax.axhline(0, color=t["fg"], lw=0.7)
    ax.set_xlabel(tr("시간 [s]", "time [s]"))
    ax.set_ylabel(tr("전력 [kW]", "power [kW]"))
    ax.legend(handles=[Line2D([], [], color=S.ACCENT, lw=2, label="P_dc"),
                       Line2D([], [], color=S.PHASE[2], lw=1.4, label=f"{e['output_port']} ({tr('출력', 'output')})"),
                       Patch(fc="#cf222e", alpha=0.15, label=tr("요구 미달성 구간", "requirement not delivered"))],
              fontsize=7, loc="upper right")
    ax.set_title(tr("미션 포트 전력 (구간별 일정)", "mission port powers (piecewise constant)"), fontsize=9)
    kwh = 3.6e6
    ports = ["P_dc", "P_ac", "P_m", "P_o"]
    x = np.arange(len(ports))
    ep = [e["E_pos_J"][k] / kwh * 1e3 for k in ports]
    en = [-e["E_neg_J"][k] / kwh * 1e3 for k in ports]
    ax2.bar(x, ep, color=S.ACCENT, alpha=0.8, label="E+")
    ax2.bar(x, en, color="#bf8700", alpha=0.8, label="E−")
    for i in range(len(ports)):
        ax2.annotate(f"{ep[i]:.1f}", (x[i], ep[i]), xytext=(0, 2), textcoords="offset points", ha="center", fontsize=7)
        ax2.annotate(f"{-en[i]:.1f}", (x[i], en[i]), xytext=(0, -9), textcoords="offset points", ha="center", fontsize=7)
    ax2.set_xticks(x)
    ax2.set_xticklabels(ports)
    ax2.axhline(0, color=t["fg"], lw=0.7)
    ax2.set_ylabel("[Wh]")
    ax2.legend(fontsize=7, loc="upper right")
    et, er = e.get("eta_traction"), e.get("eta_regeneration")
    part = e.get("partial") or {}

    def _eta_line(v, key, ko, en):
        if v:
            return tr(f"{ko} = {100 * v:.2f}%", f"{en} = {100 * v:.2f}%")
        pv = part.get(key)
        if part:
            ptxt = "—" if pv is None else f"{100 * pv:.2f}%"
            return tr(f"{ko}: UNKNOWN ({part['undetermined_s']:.0f} s 미확정; 알려진 구간만 {ptxt})",
                      f"{en}: UNKNOWN ({part['undetermined_s']:.0f} s undetermined; known segments only {ptxt})")
        return tr(f"{ko}: 구간 없음", f"{en}: no segment")
    port = e.get("output_port", "P_o")
    lines = [_eta_line(et, "eta_traction_partial", f"구동 η ({port}/P_dc 에너지)", f"traction η ({port}/P_dc energy)"),
             _eta_line(er, "eta_regeneration_partial", f"회생 η (P_dc 회수/{port} 입력)",
                       f"regen η (P_dc recovered/{port} input)"),
             tr(f"순 DC {e['E_dc_net_J'] / kwh * 1e3:.1f} Wh, 순 출력 {e['E_out_net_J'] / kwh * 1e3:.1f} Wh (비율은 효율 아님)",
                f"net DC {e['E_dc_net_J'] / kwh * 1e3:.1f} Wh, net output {e['E_out_net_J'] / kwh * 1e3:.1f} Wh (ratio is not an efficiency)"),
             tr(f"대기 입력 {e['segments']['idle']['dc_in'] / kwh * 1e3:.2f} Wh", f"idle input {e['segments']['idle']['dc_in'] / kwh * 1e3:.2f} Wh")]
    if not ms.get("delivered", True):
        lines.append(tr("⚠ 일부 구간 미달성: 에너지 비교 순위 금지", "⚠ some segments not delivered: no energy ranking"))
    axs.set_axis_off()
    axs.text(0.0, 0.98, "\n\n".join(lines), ha="left", va="top", fontsize=7.6, color=t["fg"], wrap=True,
             transform=axs.transAxes)
    pb = e.get("boundary_direction_eta") or {}
    rows = []
    for key, lab in BND:
        d = pb.get(key) or {}
        f, r = d.get("forward"), d.get("reverse")
        rows.append(f"{lab()}: " + (f"{100 * f:.2f}%" if f else "—") + " / " + (f"{100 * r:.2f}%" if r else "—"))
    axs.text(0.0, 0.40, tr("경계별 방향 에너지 효율 (구동 / 회생):", "boundary energy efficiency (drive / regen):") + "\n" +
             "\n".join(rows), ha="left", va="top", fontsize=7.2, color=t["muted"], transform=axs.transAxes)
    ax2.set_title(tr("포트별 양/음 에너지 (Wh)", "positive / negative energy per port (Wh)"), fontsize=9)
    return fig


# ---------------------------------------------------------------------------------------------- module A/B

def fig_module_compare(fig, cmp: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    rows = cmp["rows"]
    ca, cb = cmp["candidates"]
    ax, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.4, 1.0]})
    x = np.arange(len(rows))
    w = 0.36
    for k, (tag, c, col) in enumerate((("A", ca, "#6e7781"), ("B", cb, S.ACCENT))):
        vals, err = [], []
        for r in rows:
            p = (r[tag] or {}).get("point")
            v = p["Pinv_W"] if p else np.nan
            vals.append(v)
            err.append((c["loss_error_rel"] or 0.0) * v if p else 0.0)
        ax.bar(x + (k - 0.5) * w, vals, w, yerr=err, capsize=3, color=col, alpha=0.85,
               label=f"{tag}: {c['name']} ({c['technology']}, {c['fsw_Hz'] / 1e3:g} kHz)")
    for i, r in enumerate(rows):
        v = r["compare"]
        lab = {"A_LOWER_LOSS": "A ↓", "B_LOWER_LOSS": "B ↓", "UNDECIDED": tr("미확정", "undecided"),
               "NOT_COMPARABLE": tr("비교 불가", "not comparable")}.get(v["verdict"], v["verdict"])
        top = max([(r[tg] or {}).get("point", {}) and r[tg]["point"]["Pinv_W"] or 0 for tg in ("A", "B")] + [0])
        ax.annotate(lab, (x[i], top), xytext=(0, 12), textcoords="offset points", ha="center", fontsize=7.5,
                    color=t["fg"], fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r['speed_rpm']:g} rpm\n{r['torque_Nm']:g} N·m" for r in rows], fontsize=7)
    ax.set_ylabel(tr("인버터 손실 [W] (오차 막대 = 선언 오차 예산)", "inverter loss [W] (bars = declared error budget)"))
    ax.legend(fontsize=6.8, loc="upper right")
    ax.set_title(tr(f"{'고정 정책' if cmp['mode'] == 'fixed_policy' else '설계별 정책'} 비교 · 냉각수 {cmp['coolant_C']:g} °C "
                    "(Tj는 결과)", f"{'fixed-policy' if cmp['mode'] == 'fixed_policy' else 'design-specific'} comparison · "
                    f"coolant {cmp['coolant_C']:g} °C (Tj is a result)"), fontsize=9)
    # Tj and delta eta
    tja = [(r["A"] or {}).get("Tj_C", np.nan) for r in rows]
    tjb = [(r["B"] or {}).get("Tj_C", np.nan) for r in rows]
    ax2.plot(x, tja, marker="o", color="#6e7781", label="Tj A")
    ax2.plot(x, tjb, marker="s", color=S.ACCENT, label="Tj B")
    ax2.set_ylabel("Tj [°C]")
    ax2.set_xticks(x)
    ax2.set_xticklabels([f"{r['speed_rpm']:g}/{r['torque_Nm']:g}" for r in rows], fontsize=7)
    ax3 = ax2.twinx()
    dpp = [((r.get("delta_eta_pp") or {}).get("edrive") if (r.get("delta_eta_pp") or {}).get("edrive") is not None
            else (r.get("delta_eta_pp") or {}).get("inverter_motor")) for r in rows]
    ax3.bar(x, [np.nan if v is None else v for v in dpp], 0.3, color="#1a7f37", alpha=0.35)
    ax3.set_ylabel(tr("Δη (B−A) [%p]", "Δη (B−A) [pp]"), color="#1a7f37")
    ax2.legend(fontsize=7, loc="upper left")
    mc = (cmp.get("mission") or {}).get("compare")
    if mc:
        txt = tr("미션: ", "mission: ") + mc["verdict"]
        if mc.get("delta") is not None:
            txt += f" (ΔE {mc['delta'] / 3.6e3:+.2f} Wh" + (f", ±{mc['band'] / 3.6e3:.2f} Wh" if mc.get("band") else "") + ")"
        _note(ax2, txt, loc="lower left", fontsize=6.8)
    ax2.set_title(tr("접합 온도와 효율 차 (eDrive 또는 인버터+모터)", "junction temperature and efficiency difference"),
                  fontsize=9)
    return fig
