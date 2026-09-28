"""Figures for the analyses added after the independent engineering review.

Protection event timeline and threshold window (section 9), detection-loop diagram, ASC fault transient with
the customer's current-time windows (9.13), datasheet module losses (8.8), DC-link ripple (8.9) and thermal
cycling (12).  Every figure draws the result dictionaries of ``api`` as they are; nothing is recomputed here.
"""

from __future__ import annotations

import math

import numpy as np
from matplotlib.patches import Patch, Rectangle

from ..i18n import tr
from . import schematics as SC
from . import style as S
from .figures import _note, _reset

STATUS_COL = {"FEASIBLE": "#1a7f37", "INFEASIBLE": "#cf222e", "UNKNOWN": "#b7791f"}


def _ev(ax, x, label, color, y=0.97, ls="--", legend: str | None = None):
    """Event marker; with ``legend`` the label goes to the legend (coincident events stay readable)."""
    if x is None:
        return
    ax.axvline(x, color=color, lw=1.1, ls=ls, zorder=3, label=legend)
    if legend is None:
        ax.text(x, y, " " + label, transform=ax.get_xaxis_transform(), rotation=90, va="top", ha="left", fontsize=7,
                color=color)


# ---------------------------------------------------------------------------------------------- protection

def _tscale(t_end):
    if t_end < 5e-3:
        return 1e6, "µs"
    if t_end < 5.0:
        return 1e3, "ms"
    return 1.0, "s"


def fig_protection_timeline(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    tr_ = res["trace"]
    tt = np.asarray(tr_["t_s"])
    k, unit = _tscale(tt[-1])
    ax = fig.subplots()
    u = res.get("unit", "")
    ax.plot(np.asarray(tr_["no_reaction_t_s"]) * k, tr_["no_reaction_x"], color=t["muted"], lw=1, ls=":",
            label=tr("반응이 없을 때의 물리량", "physical variable without reaction"))
    ax.plot(tt * k, tr_["x"], color=S.ACCENT, lw=2.2, label=tr("물리량 x(t) (같은 궤적)", "physical x(t) (same trajectory)"))
    ax.plot(tt * k, tr_["y"], color=S.REQUEST, lw=1.2, ls="-.", label=tr("측정값 y(t) (필터·오차, 과소측정)",
                                                                        "measurement y(t) (filter, under-reading)"))
    st = np.asarray(tr_["samples_t_s"])
    if st.size and st.size <= 400:
        ax.plot(st * k, tr_["samples_y"], ls="none", marker=".", ms=3, color=S.REQUEST, alpha=0.6)
    th = res["thresholds"]
    for key, col, lab in (("warning", "#bf8700", tr("경고", "warning")), ("fault", "#cf222e", tr("고장", "fault")),
                          ("release", "#1a7f37", tr("해제", "release"))):
        if th.get(key) not in (None, ""):
            ax.axhline(float(th[key]), color=col, lw=1, ls="--", label=f"{lab} {float(th[key]):g} {u}")
    ax.axhline(res["limit"], color="#8b0000", lw=2, label=tr(f"물리 한계 {res['limit']:g} {u}", f"physical limit {res['limit']:g} {u}"))
    ev = tr_["events"]
    for key, lab, col, ls in (("t_xcross_s", "t_xcross", S.ACCENT, "--"), ("t_confirm_s", "t_confirm", "#cf222e", "-."),
                              ("t_action_effective_s", "t_action", "#1a7f37", "--"),
                              ("t_limit_s", "t_limit", "#8b0000", ":")):
        if ev.get(key) is not None:
            _ev(ax, ev[key] * k, lab, col, ls=ls, legend=f"{lab} = {ev[key] * k:.4g} {unit}")
    ax.plot(ev["t_peak_s"] * k, ev["peak"], marker="o", ms=7, color="#cf222e", zorder=6)
    ax.annotate(f"peak {ev['peak']:.4g} {u}", (ev["t_peak_s"] * k, ev["peak"]), xytext=(6, 6),
                textcoords="offset points", fontsize=8)
    ax.set_xlabel(tr(f"고장 발생 후 시간 [{unit}]", f"time after fault inception [{unit}]"))
    ax.set_ylabel(f"{res.get('variable', 'x')} [{u}]")
    ax.set_title(tr("같은 물리 궤적 위의 검출·반응·한계 (시간 관계가 판정 근거)",
                    "detection, reaction and limit on one physical trajectory"), fontsize=9)
    ax.legend(loc="lower right", fontsize=7)
    ok = tr_["protected"]
    _note(ax, (tr("보호됨: 한계 미도달", "protected: limit not reached") if ok else
               tr("한계 초과: 검출만으로는 보호가 아님", "limit exceeded: detection alone is not protection"))
          + f"\n{tr('판정 요약', 'summary')}: {res['summary_status']}", loc="upper left", fontsize=7.5)


def fig_threshold_window(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.1, 1.0]})
    w = res.get("window")
    u = res.get("unit", "")
    if w:
        lo, hi = w["nuisance_lower_bound"], w["protection_upper_bound"]
        terms = w["terms"]
        xn, xl = terms["x_normal_max"], terms["x_limit"]
        ys = [3, 2, 1, 0]
        ax1.barh(3, lo - xn, left=xn, color="#bf8700", alpha=0.35, label=tr("정상 최대 + E+ + Eθ", "normal max + E+ + Eθ"))
        upper_base = terms["physics_upper_bound"] if terms["physics_upper_bound"] is not None else xl - terms["dx_after"]
        ax1.barh(2, upper_base - hi, left=hi, color="#cf222e", alpha=0.35, label=tr("E− + Eθ + 반응 중 상승", "E− + Eθ + rise after trigger"))
        col = STATUS_COL["FEASIBLE"] if w["window_exists"] else STATUS_COL["UNKNOWN"]
        if w["window_exists"]:
            ax1.barh(1, hi - lo, left=lo, color=col, alpha=0.55, label=tr("보장 가능한 임계값 창", "guaranteed threshold window"))
        else:
            ax1.barh(1, lo - hi, left=hi, color=col, alpha=0.35, hatch="//",
                     label=tr("창 없음 (보수적 bound)", "no window (conservative bounds)"))
        cand = w.get("candidate", {}).get("theta_nom")
        if cand is not None:
            ax1.axvline(cand, color=S.ACCENT, lw=2, label=tr(f"선택 임계값 {cand:g} {u}", f"chosen threshold {cand:g} {u}"))
        ax1.axvline(xn, color=t["muted"], lw=1, ls=":")
        ax1.axvline(xl, color="#8b0000", lw=2)
        if terms["physics_upper_bound"] is not None:
            ax1.axvline(terms["physics_upper_bound"], color="#cf222e", lw=1.2, ls="--",
                        label=tr(f"에너지 bound V_tr,max {terms['physics_upper_bound']:.1f}", f"energy bound V_tr,max {terms['physics_upper_bound']:.1f}"))
        ax1.set_yticks(ys)
        ax1.set_yticklabels([tr("nuisance 방지", "no nuisance"), tr("보호", "protection"), tr("임계값 창", "window"), ""])
        ax1.set_xlabel(f"{res.get('variable', 'x')} [{u}]")
        ax1.legend(loc="lower left", fontsize=6.5)
        ax1.set_title(tr("충분조건 기반 임계값 창 (비면 UNKNOWN)", "sufficient-condition threshold window (empty -> UNKNOWN)"),
                      fontsize=9)
        b = res.get("ov_bound")
        if b:
            _note(ax1, f"E_after = {b['E_after_J']:.3g} J\nV_tr,max = "
                       f"{'—' if b['V_trigger_max_V'] is None else format(b['V_trigger_max_V'], '.2f')} V", loc="upper left",
                  fontsize=7)
    else:
        ax1.axis("off")
        ax1.text(0.5, 0.5, tr("정상 최대값 미선언: 창을 계산하지 않음", "normal maximum not declared: no window"),
                 ha="center", va="center", transform=ax1.transAxes, color=t["muted"])
    rows = res["rows"]
    ax2.axis("off")
    ax2.set_title(tr("PROT 검토표 (같은 궤적·bound 기반)", "PROT review table"), fontsize=9)
    y = 0.95
    for r in rows:
        col = STATUS_COL.get(r["status"], t["muted"])
        ax2.add_patch(Rectangle((0.0, y - 0.075), 0.2, 0.07, transform=ax2.transAxes, color=col, alpha=0.85))
        ax2.text(0.1, y - 0.04, {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL"}.get(r["status"], r["status"]),
                 transform=ax2.transAxes, ha="center", va="center", fontsize=6.5, color="white", fontweight="bold")
        ax2.text(0.22, y - 0.02, f"{r['id']} {r['item']}", transform=ax2.transAxes, fontsize=7, va="top",
                 color=t["fg"], fontweight="bold")
        ax2.text(0.22, y - 0.055, (r["detail"][:95] + ("…" if len(r["detail"]) > 95 else "")),
                 transform=ax2.transAxes, fontsize=6, va="top", color=t["muted"])
        y -= 0.105


def fig_protection_loop(fig, res: dict | None = None, title: str | None = None):
    """Causal chain plant -> sensor -> filter -> sampler -> debounce -> delay -> action -> plant."""
    _reset(fig, title)
    c = SC._c()
    ax = SC.new_axes(fig, (0, 16), (0, 5.2))
    se = (res or {}).get("sensor_desc", {})
    blocks = [(1.4, 3.6, tr("플랜트\nx(t)", "plant\nx(t)")),
              (4.0, 3.6, tr("센서\n(1+g)x+o", "sensor\n(1+g)x+o")),
              (6.6, 3.6, tr("필터\nτ dy/dt=x−y", "filter\nτ dy/dt=x−y")),
              (9.2, 3.6, tr("샘플링\nTs, 위상 φ", "sampler\nTs, phase φ")),
              (11.8, 3.6, tr("디바운스\nN연속, 리셋", "debounce\nN consecutive")),
              (14.4, 3.6, tr("판정·실행\n지연", "decision\n+ exec delay")),
              (14.4, 1.2, tr("구동\n(게이트/디레이팅)", "actuation\n(gate / derating)")),
              (8.0, 1.2, tr("플랜트 입력 변경\n(전력↓, ASC, 컷)", "plant input changes\n(power↓, ASC, cut)"))]
    for x, y, lab in blocks:
        SC.block(ax, x, y, 2.2, 1.3, lab, size=7)
    for (x0, y0), (x1, y1) in (((2.5, 3.6), (2.9, 3.6)), ((5.1, 3.6), (5.5, 3.6)), ((7.7, 3.6), (8.1, 3.6)),
                               ((10.3, 3.6), (10.7, 3.6)), ((12.9, 3.6), (13.3, 3.6)), ((14.4, 2.95), (14.4, 1.85)),
                               ((13.3, 1.2), (9.1, 1.2))):
        SC.flow(ax, (x0, y0), (x1, y1), color=c["on"], lw=1.6)
    SC.flow(ax, (6.9, 1.2), (1.4, 2.9), color=c["hot"], lw=1.6, rad=-0.15)
    SC.text(ax, 8.0, 4.85, tr("t_xcross → t_ycross → t_sample → t_confirm → t_action → (t_limit?)",
                              "t_xcross → t_ycross → t_sample → t_confirm → t_action → (t_limit?)"), size=8, weight="bold")
    SC.text(ax, 3.2, 0.35, tr("판정은 같은 인과 궤적의 시간 관계로: 검출이 빨라도 물리 한계를 넘으면 실패",
                              "judged on one causal trajectory: early detection is not protection if the limit is crossed"),
            size=7, color=c["muted"], ha="left")


# ---------------------------------------------------------------------------------------------- ASC transient

def fig_asc_transient(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(2, 1, sharex=True)
    w = res["waveform"]
    tm = np.asarray(w["t_s"]) * 1e3
    ax1.plot(tm, w["id_A"], color=S.MTPA, lw=1.8, label="i_d")
    ax1.plot(tm, w["iq_A"], color=S.ACCENT, lw=1.8, label="i_q")
    ax1.plot(tm, np.hypot(w["id_A"], w["iq_A"]), color=t["muted"], lw=1, ls="--", label="|i_dq|")
    ax1.axhline(res["steady_asc"]["id_A"], color=S.MTPA, lw=0.8, ls=":")
    _ev(ax1, w["t_asc_s"] * 1e3, "ASC", "#cf222e")
    ax1.set_ylabel(tr("dq 전류 [A] (peak)", "dq current [A] (peak)"))
    ax1.legend(loc="lower right", fontsize=7)
    ax1.set_title(tr("ASC 과도: 사고 전 운전점 → 단락 → 정상 ASC (정규 전류 한계로 clip하지 않음)",
                     "ASC transient: pre-fault point -> short -> steady ASC (no clipping at the normal limit)"),
                  fontsize=9)
    for ph, col in zip(("ia_A", "ib_A", "ic_A"), S.PHASE):
        ax2.plot(tm, w[ph], color=col, lw=1.1, label=ph[:2])
    t_asc = w["t_asc_s"]
    for k, (rid, r) in enumerate((res.get("requirements") or {}).items()):
        if not r or r.get("status") in ("REQUIREMENT_INCOMPLETE", "NOT_COVERED"):
            continue
        off = t_asc if r["origin"] == "asc_established" else 0.0
        a, b = (r["window_s"][0] + off) * 1e3, (r["window_s"][1] + off) * 1e3
        col = {"PASS": "#1a7f37", "FAIL": "#cf222e"}.get(r["screening_verdict"], "#b7791f")
        ax2.axvspan(a, b, color=col, alpha=0.08)
        if r["operator"] in ("abs_peak", "envelope_after"):
            ax2.hlines([r["limit"], -r["limit"]], a, b, colors=col, lw=1.6)
        elif r["operator"] == "rms":        # an RMS limit is not an instantaneous band: value vs limit, positive side
            ax2.hlines(r["value"], a, b, colors=col, lw=2.0)
            ax2.hlines(r["limit"], a, b, colors=col, lw=1.2, ls="--")
        elif r.get("level_A") is not None:   # time above: the current level is drawn, the limit is a duration
            ax2.hlines([r["level_A"], -r["level_A"]], a, b, colors=col, lw=1.0, ls=":")
        ax2.text(a, 0.98 - 0.09 * k, f" {rid}: {r['operator']} {r['value']:.4g} / {r['limit']:g} {r.get('unit', 'A')}",
                 transform=ax2.get_xaxis_transform(), fontsize=6.5, va="top", color=col, zorder=8,
                 bbox=dict(boxstyle="round,pad=0.15", fc=t["bg"], ec="none", alpha=0.8))
    ax2.set_xlabel(tr("고장 발생 후 시간 [ms]", "time after fault [ms]"))
    ax2.set_ylabel(tr("상전류 [A] (최악 초기각)", "phase current [A] (worst initial angle)"))
    ax2.legend(loc="lower right", fontsize=7, ncols=3)
    _note(ax2, res.get("claim_level", "")[:70] + ("…" if len(res.get("claim_level", "")) > 70 else ""),
          loc="upper right", fontsize=6.5)


# ---------------------------------------------------------------------------------------------- module losses

def fig_module_losses(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2, ax3 = fig.subplots(1, 3, gridspec_kw={"width_ratios": [1.0, 1.3, 1.0]})
    los = res.get("losses", {})
    leg = res.get("leg_detail", {})
    names = [("upper_switch", tr("상단 스위치", "upper switch")), ("upper_reverse", tr("상단 다이오드/역도통", "upper reverse")),
             ("lower_switch", tr("하단 스위치", "lower switch")), ("lower_reverse", tr("하단 다이오드/역도통", "lower reverse"))]
    if leg:
        cond = [leg["conduction_W"][k] for k, _ in names]
        swk = {"upper_switch": "upper_switch", "upper_reverse": "upper_recovery", "lower_switch": "lower_switch",
               "lower_reverse": "lower_recovery"}
        sw = [leg["switching_W"][swk[k]] for k, _ in names]
        x = np.arange(4)
        ax1.bar(x, cond, color=S.ACCENT, label=tr("도통", "conduction"))
        ax1.bar(x, sw, bottom=cond, color=S.REQUEST, label=tr("스위칭/역회복", "switching / recovery"))
        ax1.set_xticks(x)
        ax1.set_xticklabels([n for _, n in names], rotation=30, ha="right", fontsize=7)
        ax1.set_ylabel(tr("손실 [W] (한 상·모듈 1개)", "loss [W] (one leg, one module)"))
        ax1.legend(fontsize=7)
        ax1.set_title(tr("소자 위치별 손실", "loss per device position"), fontsize=9)
    rows = res.get("torque_sweep", [])
    if rows:
        tq = [r["torque_Nm"] for r in rows]
        ax2.plot(tq, [r["module_W"] for r in rows], color=S.ACCENT, lw=2, marker="o", ms=3,
                 label=tr("데이터시트 모듈 모델 (P_inv)", "datasheet module model (P_inv)"))
        ax2.plot(tq, [r["surrogate_W"] for r in rows], color=t["muted"], lw=1.3, ls="--",
                 label=tr("합성 2차 대리식 a0+a2·I²", "synthetic surrogate a0 + a2·I²"))
        ax2.plot(tq, [r["hottest_W"] for r in rows], color="#cf222e", lw=1.3,
                 label=tr("최고 발열 소자", "hottest device"))
        ax2.set_xlabel(tr("요구 토크 [N·m] (같은 속도)", "requested torque [N·m] (same speed)"))
        ax2.set_ylabel(tr("손실 [W]", "loss [W]"))
        ax2.legend(fontsize=7, loc="lower right")
        ax2.set_title(tr("토크에 따른 인버터 손실: 모델 비교", "inverter loss vs torque: model comparison"), fontsize=9)
    ss = res.get("standstill", {})
    if ss.get("established"):
        ax3.bar([0, 1], [ss["hottest_device_W"], ss["total_over_six_W"]], color=["#cf222e", t["muted"]])
        ax3.set_xticks([0, 1])
        ax3.set_xticklabels([tr("정지 시 최고 소자\n(전기각 최악)", "standstill hottest\n(worst angle)"),
                             tr("총손실/6\n(사용 금지)", "total/6\n(not valid)")], fontsize=7)
        ax3.set_ylabel(tr("소자 손실 [W]", "device loss [W]"))
        ax3.set_title(tr("정지(저속) 핫스팟 ≠ 총손실/6", "standstill hotspot ≠ total/6"), fontsize=9)
    et = res.get("electrothermal") or {}
    note = []
    if los:
        note.append(f"P_semi = {los.get('semiconductor_W', float('nan')):.4g} W · m = {los.get('modulation_index', 0):.3f} · "
                    f"cosφ = {los.get('power_factor', float('nan')):.3f}")
    if et.get("converged"):
        note.append(f"Tj (fixed point) = {et['Tj_C']:.1f} °C")
    if note:
        _note(ax2, "\n".join(note), loc="upper left", fontsize=7)


# ---------------------------------------------------------------------------------------------- DC-link ripple

def fig_ripple(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2)
    w = res["waveform"]
    tm = np.asarray(w["t_s"]) * 1e3
    ax1.plot(tm, w["i_inv_A"], color=S.ACCENT, lw=0.7, label=tr("인버터 입력전류 i_inv(t)", "inverter input current i_inv(t)"))
    ax1.axhline(res["I_dc_A"], color=t["fg"], lw=1, ls="--", label=tr(f"평균 {res['I_dc_A']:.1f} A", f"average {res['I_dc_A']:.1f} A"))
    ax1.set_xlabel(tr("시간 [ms] (기본파 1주기)", "time [ms] (one fundamental period)"))
    ax1.set_ylabel(tr("전류 [A]", "current [A]"))
    a3 = ax1.twinx()
    a3.plot(tm, w["v_ripple_V"], color=S.REQUEST, lw=1, alpha=0.8)
    a3.set_ylabel(tr("커패시터 전압 리플 [V] (주황)", "capacitor voltage ripple [V] (orange)"), color=S.REQUEST)
    a3.grid(False)
    a3.spines["right"].set_visible(True)
    ax1.legend(loc="lower left", fontsize=7)
    ax1.set_title(tr("스위칭 함수 파형 (이상 스위치, 동기 캐리어)", "switching-function waveform (ideal switches)"), fontsize=9)
    sp = res["spectrum"]
    f = np.asarray(sp["f_Hz"])[1:] / 1e3
    ax2.bar(f, np.asarray(sp["I_inv_rms_A"])[1:], width=(f[1] - f[0]) * 0.8 if f.size > 1 else 0.1, color=t["muted"],
            alpha=0.6, label=tr("인버터 AC 성분", "inverter AC harmonics"))
    ax2.plot(f, np.asarray(sp["I_cap_rms_A"], dtype=float)[1:], ls="none", marker="o", ms=3, color="#cf222e",
             label=tr("커패시터 가지 전류", "capacitor branch"))
    ax2.set_xlabel(tr("주파수 [kHz]", "frequency [kHz]"))
    ax2.set_ylabel(tr("고조파 RMS [A]", "harmonic RMS [A]"))
    ax2.legend(fontsize=7)
    ax2.set_title(tr("스펙트럼과 가지 분배 (Z_s vs Z_C)", "spectrum and branch split (Z_s vs Z_C)"), fontsize=9)
    cl = res.get("claims", {})
    def val(k, unit, digits):
        v, b = res.get(k), res.get(k.rsplit("_", 1)[0] + "_bounds_" + k.rsplit("_", 1)[1]) or [None, None]
        if v is not None:
            return f"{v:.{digits}f} {unit}"
        return f"[{b[0]:.{digits}f}, {b[1]:.{digits}f}] {unit}" if b[0] is not None and np.isfinite(b[1]) else "—"
    stt = res.get("state") or {}
    lines = [f"I_C,rms = {val('I_cap_rms_A', 'A', 1)} · I_inv,ac = {res['I_inv_ac_rms_A']:.1f} A",
             f"V_pp = {val('V_ripple_pp_V', 'V', 2)} · P_cap = " + ("—" if res["P_cap_W"] is None else f"{res['P_cap_W']:.2f} W")]
    if stt.get("T_C") is not None:
        lines.append(tr(f"커패시터 상태 {stt['T_C']:.1f} °C", f"capacitor state {stt['T_C']:.1f} °C"))
    for k, v in cl.items():
        lines.append(f"{k}: {v['status']}")
    if res.get("assumption"):
        lines.append(tr("가정: 소스 임피던스 미선언", "assumption: source impedance not declared"))
    _note(ax2, "\n".join(lines), loc="upper right", fontsize=6.8)


# ---------------------------------------------------------------------------------------------- lifetime

def fig_lifetime(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.6, 1.0]})
    tr_ = res["trace"]
    per = tr_.get("per_device_T_C") or {}
    for i, (name, Tn) in enumerate(per.items()):
        if name != tr_.get("device"):
            ax1.plot(tr_["t_s"], Tn, lw=0.8, alpha=0.75, color=(S.ACCENT, S.MTPA, S.MTPV, S.REQUEST)[i % 4],
                     label=f"Tj {name}")
    ax1.plot(tr_["t_s"], tr_["T_C"], color="#cf222e", lw=1.2,
             label=(f"Tj {tr_['device']} " + tr("(지배)", "(governing)")) if tr_.get("device") else
             tr("최고 발열 포락선 (한 소자 아님)", "hottest-die envelope (not one device)"))
    ax1.set_xlabel(tr("시간 [s]", "time [s]"))
    ax1.set_ylabel("Tj [°C]")
    ax1.legend(fontsize=7, loc="lower right")
    ax1.set_title(tr("미션 → 다이별 손실 → 다이별 Tj 이력 (스크리닝 전열 체인)",
                     "mission -> per-die losses -> per-die Tj histories (screening chain)"), fontsize=9)
    h = res.get("histogram", [])
    if h:
        x = [0.5 * (r["range_K"][0] + r["range_K"][1]) for r in h]
        wdt = (h[0]["range_K"][1] - h[0]["range_K"][0]) * 0.85
        ax2.bar(x, [r["cycles"] for r in h], width=wdt, color=S.ACCENT)
        ax2.set_ylim(0, max(r["cycles"] for r in h) * 1.55)
    ax2.set_xlabel(tr("ΔTj 범위 [K] (rainflow)", "ΔTj range [K] (rainflow)"))
    ax2.set_ylabel(tr("사이클 수", "cycles"))
    ax2.set_title(tr("rainflow 히스토그램 (ASTM E1049)", "rainflow histogram (ASTM E1049)"), fontsize=9)
    d = res["damage"]
    c = d["claim"]
    _note(ax2, f"{c['status']}\n" + c["detail"][:80] + ("…" if len(c["detail"]) > 80 else ""), loc="upper right",
          fontsize=6.8)
