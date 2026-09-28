"""Figures for the variable-PWM policy evaluation and the torque shaping / active driveline damping evaluation.

Every figure draws the result dictionaries of ``api.pwm_policies`` / ``api.pwm_timing`` / ``api.pwm_ripple`` /
``api.driveline`` / ``api.driveline_stability`` as they are.
"""

from __future__ import annotations

import math

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from ..i18n import tr
from . import style as S
from .figures import _note, _reset

POL = ("#1f2328", "#0969da", "#cf222e", "#1a7f37", "#8250df", "#bf8700")
VAR = {"off": "#6e7781", "shaping": "#0969da", "feedback": "#bf8700", "combined": "#1a7f37"}


def _pc(i, t):
    return t["fg"] if i == 0 else POL[i % len(POL)]


KIND = {"inline_phase": ("#0969da", "inline"), "leg_shunt": ("#1a7f37", "leg shunts"),
        "dc_link_shunt": ("#cf222e", "DC-link shunt")}
TRV = ("#1f2328", "#1a7f37", "#bf8700", "#cf222e", "#8250df")


# ---------------------------------------------------------------------------------------------- PWM policies

def fig_pwm_policies(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    pols = res["policies"]
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0])
    ax = fig.add_subplot(gs[0, :])
    ax2 = fig.add_subplot(gs[1, 0])
    ax3 = fig.add_subplot(gs[1, 1])
    mism_labeled = False
    for i, p in enumerate(pols):
        tt, ff = [0.0], []
        for s in p["segments"]:
            ff.append(s["fsw_Hz"] / 1e3)
            tt.append(tt[-1] + s["duration_s"])
        ax.step(tt, ff + [ff[-1]], where="post", color=_pc(i, t), lw=2.2 if i == 0 else 1.6,
                ls="-" if p["admissible"] else "--",
                label=p["policy"]["name"] + ("" if p["admissible"] else tr("  (허용 안 됨)", "  (not admissible)")))
        for e in p["transitions"]:
            if "protective" in e["reason"]:
                ax.plot(e["t_s"], e["to_fsw_Hz"] / 1e3, marker="v", ms=9, color="#cf222e", ls="none")
        # requested vs waveform-used carrier: the waveform models use a synchronous carrier (integer ratio to f_e)
        mism = [(tt[k] + 0.5 * s["duration_s"], s["fsw_waveform_used_Hz"] / 1e3)
                for k, s in enumerate(p["segments"]) if abs(s.get("fsw_error_percent") or 0.0) > 0.5]
        if mism:
            ax.plot(*zip(*mism), marker="x", ms=6, ls="none", color=_pc(i, t))
            mism_labeled = True
    if mism_labeled:
        ax.plot([], [], marker="x", ls="none", color=t["fg"],
                label=tr("× 파형 모델 fsw (동기 캐리어 ≠ 요청)", "× waveform fsw (synchronous carrier ≠ requested)"))
    tt = 0.0
    for sg in pols[0]["segments"]:
        ax.axvline(tt, color=t["grid"], lw=0.8)
        tt += sg["duration_s"]
    ax.set_xlabel(tr("시간 [s]", "time [s]"))
    ax.set_ylabel(tr("캐리어 주파수 [kHz]", "carrier frequency [kHz]"))
    ax.legend(fontsize=7, loc="upper left")
    ax.set_title(tr("같은 궤적에서의 정책별 fsw (▼ 보호 선점)", "fsw per policy on the same trajectory (▼ protective pre-emption)"),
                 fontsize=9)
    ax.grid(True, alpha=0.35)
    # energy per policy: the known stack (established) and the open / bounded parts as an interval - a bound is
    # never drawn as consumed energy
    names = [p["policy"]["name"] for p in pols]
    x = np.arange(len(pols))
    inv = np.array([(p["E_inv_J"] if p["E_inv_J"] is not None else np.nan) / 1e3 for p in pols])
    cu = np.array([(p["E_cu_pwm_J"] or 0.0) / 1e3 for p in pols])
    cu_lb = np.array([(p["E_cu_pwm_lower_bound_J"] or 0.0) / 1e3 if p["E_cu_pwm_J"] is None else 0.0 for p in pols])
    ax2.bar(x, inv, 0.55, color=[_pc(i, t) for i in range(len(pols))], alpha=0.8,
            label=tr("인버터 반도체 손실 (확정)", "inverter semiconductor loss (established)"))
    if cu.any():
        ax2.bar(x, cu, 0.55, bottom=inv, color="#bf8700", alpha=0.75,
                label=tr("모터 PWM 동손 (R_ac(f), 확정)", "motor PWM copper (R_ac(f), established)"))
    if cu_lb.any():
        ax2.bar(x, cu_lb, 0.55, bottom=inv, color="none", ec="#bf8700", hatch="..",
                label=tr("모터 PWM 동손 ≥ R_dc 하한 (정확값 없음)", "motor PWM copper ≥ R_dc lower bound (no exact value)"))
    tops = []
    for i, p in enumerate(pols):
        e = p["energy"]
        lo = None if e["lower_J"] is None else e["lower_J"] / 1e3
        hi = None if e["upper_J"] is None else e["upper_J"] / 1e3
        if lo is not None:
            if hi is not None:
                ax2.errorbar(x[i] + 0.2, 0.5 * (lo + hi), yerr=[[0.5 * (hi - lo)], [0.5 * (hi - lo)]], fmt="none",
                             ecolor="#8250df", elinewidth=1.6, capsize=4,
                             label=tr("비교 구간 [확정, 확정 + Fe+PM HF 상한]", "comparison interval [known, known + Fe+PM HF bound]")
                             if i == 0 else None)
                tops.append(hi)
            else:
                ax2.annotate("", (x[i] + 0.2, lo * 1.35), (x[i] + 0.2, lo), arrowprops={"arrowstyle": "->",
                                                                                        "color": "#8250df", "ls": "--"})
                ax2.plot([], [], color="#8250df", ls="--", label=tr("상한 없음 (열린 구간)", "no upper end (open interval)")
                         if i == 0 else None)
                tops.append(lo * 1.35)
        v = p.get("versus_baseline")
        if v and v.get("delta_E_inv_J") is not None:
            txt = f"Δinv {100 * (v.get('relative_inv') or 0):+.1f}%\n" + tr("총합: ", "total: ") + v["total"]["status"]
            ax2.annotate(txt, (x[i], (tops[-1] if tops else inv[i])), xytext=(0, 4), textcoords="offset points",
                         ha="center", fontsize=6.5, color=t["fg"])
        if not p["admissible"]:
            ax2.annotate("✗", (x[i], inv[i] / 2), ha="center", fontsize=14, color="#cf222e")
    tops = [v for v in tops + list(inv + cu + cu_lb) if np.isfinite(v)]
    if tops:                                         # head room: the per-bar notes stay inside the axes, under the title
        ax2.set_ylim(0, max(tops) * 1.6)
    ax2.set_xticks(x)
    ax2.set_xticklabels([n.replace(" ", "\n", 1) for n in names], fontsize=7)
    ax2.set_ylabel(tr("궤적 에너지 [kJ]", "trajectory energy [kJ]"))
    ax2.legend(fontsize=6.0, loc="upper left", framealpha=0.95)
    ax2.set_title(tr("정책 민감 손실 (상한은 구간 끝, 확정값 아님)", "policy-sensitive loss (a bound is an interval end, not a value)"),
                  fontsize=9)
    # utilisation of the mandatory limits
    lim = res["limits"]
    items = [("Tj", "Tj_max_C", "Tj_max_C", False), (tr("피크 전류 상한", "peak-current bound"), "i_peak_bound_max_A",
                                                        "i_peak_incl_ripple_max_A", False),
             (tr("커패시터 전류", "cap. current"), "I_cap_rms_max_A", "cap_rms_max_A", False),
             (tr("위상 여유", "phase margin"), "phase_margin_min_deg", "phase_margin_min_deg", True)]
    w = 0.8 / max(len(pols), 1)
    for i, p in enumerate(pols):
        vals = []
        for _lab, key, lkey, inv_ in items:
            v, L = p.get(key), lim.get(lkey)
            vals.append(np.nan if (v is None or L is None) else ((L / v) if inv_ else (v / L)))
        ax3.bar(np.arange(len(items)) + (i - (len(pols) - 1) / 2) * w, vals, w, color=_pc(i, t), alpha=0.8,
                label=p["policy"]["name"])
    ax3.axhline(1.0, color="#cf222e", lw=1.2, ls="--")
    ax3.set_ylim(0, max(1.2, ax3.get_ylim()[1] * 1.45))     # head room: legend and note above the tallest bar
    ax3.set_xticks(np.arange(len(items)))
    ax3.set_xticklabels([it[0] for it in items], fontsize=7.5)
    ax3.set_ylabel(tr("한도 사용률 (>1 위반)", "limit utilisation (>1 violates)"))
    ax3.set_title(tr("필수 제약 (효율과 맞바꾸지 않음)", "mandatory constraints (never traded for efficiency)"), fontsize=9)
    ax3.legend(fontsize=6.5, loc="upper left")
    best = res.get("best_inverter_energy_among_evaluated")
    be = res.get("best_policy_energy_among_evaluated") or {}
    tot = be.get("policy") or be.get("status", "—")
    _note(ax3, tr(f"평가 후보 중 인버터 에너지 최선: {best or '없음'}\n정책 에너지(구간): {tot}\n"
                  f"Pareto: {', '.join(res['pareto']) or '없음'}",
                  f"best inverter energy among evaluated: {best or 'none'}\npolicy energy (interval): {tot}\n"
                  f"Pareto: {', '.join(res['pareto']) or 'none'}"),
          loc="upper right", fontsize=6.5)
    return fig


def fig_pwm_timing(fig, tm: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    rows = tm["rows"]
    f = np.array([r["fsw_Hz"] for r in rows]) / 1e3
    ax, ax2, ax3 = fig.subplots(1, 3, gridspec_kw={"width_ratios": [1.0, 1.0, 1.2]})
    pm = np.array([r.get("phase_margin_deg", np.nan) if r["deadline_ok"] else np.nan for r in rows], float)
    ax.plot(f, pm, marker="o", color=S.ACCENT, label=tr("전류 루프 위상 여유", "current-loop phase margin"))
    bad = [r["fsw_Hz"] / 1e3 for r in rows if not r["deadline_ok"]]
    for b in bad:
        ax.axvspan(b * 0.97, b * 1.03, color="#cf222e", alpha=0.2)
    if bad:
        ax.plot([], [], color="#cf222e", alpha=0.3, lw=8, label=tr("deadline 미준수", "deadline missed"))
    ax.set_xlabel(tr("캐리어 [kHz]", "carrier [kHz]"))
    ax.set_ylabel(tr("위상 여유 [°]", "phase margin [°]"))
    ax.legend(fontsize=7, loc="lower right")
    ax.grid(True, alpha=0.35)
    ax.set_title(tr("지연 원장 → 전류 루프 여유", "delay ledger → current-loop margin"), fontsize=9)
    ph_m = np.array([r.get("phase_at_mode_deg", np.nan) for r in rows], float)
    ph_c = np.array([r.get("phase_at_crossover_deg", np.nan) for r in rows], float)
    ax2.plot(f, ph_c, marker="s", color="#cf222e", label=tr("전류 루프 교차 주파수에서", "at the current-loop crossover"))
    ax2.plot(f, ph_m, marker="o", color=S.PHASE[2],
             label=tr(f"기계 모드 {tm['mode_frequency_Hz']:g} Hz에서", f"at the mechanical mode {tm['mode_frequency_Hz']:g} Hz"))
    ax2.set_xlabel(tr("캐리어 [kHz]", "carrier [kHz]"))
    ax2.set_ylabel(tr("지연에 의한 위상 [°]", "delay phase [°]"))
    ax2.set_yscale("log")
    ax2.legend(fontsize=7)
    ax2.grid(True, which="both", alpha=0.3)
    ax2.set_title(tr("fsw는 전류 루프를 바꾸지 수십 Hz 모드는 거의 안 바꿈", "fsw moves the current loop, barely a tens-of-Hz mode"),
                  fontsize=8.5)
    for k, (key, lab, dy) in enumerate((("transition_shadow", tr("shadow (원자적 reload)", "shadow (atomic reload)"), 0.0),
                                        ("transition_immediate", tr("즉시 기록", "immediate write"), -1.5))):
        r = tm[key]
        sim = r["sim"]
        up = sorted(sim["upper"])
        tt = [0.0]
        lv = [0.0]
        for te, s in up:
            tt += [te, te]
            lv += [lv[-1], 1.0 if s > 0 else 0.0]
        tt.append(sim["t_end_s"])
        lv.append(lv[-1])
        col = "#1a7f37" if r["ok"] else "#cf222e"
        ax3.plot(np.array(tt) * 1e6, np.array(lv) * 0.9 + dy, color=col, lw=1.3,
                 label=f"{lab}: {'OK' if r['ok'] else tr('위반', 'violation')} (duty err "
                       f"{100 * (r['worst_period_duty_error'] or 0):.1f}%)")
    ax3.set_yticks([])
    ax3.set_xlabel("t [µs]")
    ax3.legend(fontsize=6.8, loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False)
    ax3.set_title("", fontsize=8)
    return fig


def fig_pwm_ripple(fig, rp: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax, ax2 = fig.subplots(1, 2)
    for i, r in enumerate(rp["rows"]):
        col = POL[(i + 1) % len(POL)]
        ax.plot(np.asarray(r["t_s"]) * 1e3, r["di_A"], color=col, lw=0.8,
                label=f"{r['fsw_Hz'] / 1e3:g} kHz: {r['ripple_rms_A']:.2f} A rms (FFT {r['ripple_rms_spectrum_A']:.2f})")
        ax2.loglog(r["harmonic_f_Hz"], np.maximum(r["harmonic_I_pk_A"], 1e-6), color=col, lw=0.8, marker=".", ms=2,
                   ls="none")
    ax.set_xlabel(tr("시간 [ms] (기본파 한 주기)", "time [ms] (one fundamental period)"))
    ax.set_ylabel(tr("상전류 리플 [A]", "phase current ripple [A]"))
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.35)
    p = rp["point"]
    ax.set_title(tr(f"RL 리플 (L_hf = {rp['L_hf_H'] * 1e6:g} µH 선언) · {p['speed_rpm']:g} rpm / {p['torque_Nm']:g} N·m",
                    f"RL ripple (declared L_hf = {rp['L_hf_H'] * 1e6:g} µH) · {p['speed_rpm']:g} rpm / {p['torque_Nm']:g} N·m"),
                 fontsize=9)
    ax2.set_xlabel(tr("주파수 [Hz]", "frequency [Hz]"))
    ax2.set_ylabel(tr("고조파 전류 peak [A]", "harmonic current peak [A]"))
    ax2.set_ylim(1e-3, None)
    ax2.grid(True, which="both", alpha=0.3)
    ax2.set_title(tr("엣지 합 스펙트럼 (독립 계산)", "edge-sum spectrum (independent)"), fontsize=9)
    return fig


# ---------------------------------------------------------------------------------------------- driveline

def fig_pwm_transients(fig, r: dict, title: str | None = None):
    """Current-sample validity vs modulation index per acquisition kind, and one carrier-frequency change replayed
    with alternative integrator / gain mappings (``api.pwm_transients``)."""
    _reset(fig, title)
    t = S.theme()
    ax, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.0, 1.25]})
    pt = r["point"]
    for kind, c in r["sampling_curves"].items():
        col, lab = KIND.get(kind, (S.ACCENT, kind))
        ax.plot(c["m"], np.asarray(c["valid_fraction"]) * 100.0, color=col, lw=1.8, marker="o", ms=3,
                label=tr({"inline": "인라인 상전류", "leg shunts": "레그 저측 션트 (두 레그 재구성)",
                          "DC-link shunt": "DC-link 단일 션트"}[lab], lab))
    ax.axvline(pt["m"], color=t["muted"], lw=1.0, ls=":")
    here = r["sampling_here"]
    col = KIND.get(r["sensing"]["kind"], (S.ACCENT, ""))[0]
    ax.plot([pt["m"]], [100.0 * here["valid_fraction"]], marker="*", ms=14, color=col, ls="none",
            label=tr(f"선언 구성 @ 운전점: {here['status']}", f"declared set-up @ point: {here['status']}"))
    ax.set_xlabel(tr("변조 지수 m (선형 SVPWM ≤ 2/√3)", "modulation index m (linear SVPWM <= 2/sqrt3)"))
    ax.set_ylabel(tr("유효 전류 샘플 [%] (기본파 1주기)", "valid current samples [%] (one fundamental period)"))
    ax.set_ylim(-3, 125)                                    # headroom for the note above the 100 % lines
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.grid(True, alpha=0.35)
    ax.legend(fontsize=6.8, loc="lower right")
    sn = r["sensing"]
    _note(ax, tr(f"settle {1e6 * sn['settle_s']:.2g} µs + aperture {1e6 * sn['aperture_s']:.2g} µs, "
                 f"fsw {pt['fsw_Hz'] / 1e3:g} kHz\n무효 샘플은 선언 정책으로 유지/예측 (실제 전류로 대체 안 함)",
                 f"settle {1e6 * sn['settle_s']:.2g} us + aperture {1e6 * sn['aperture_s']:.2g} us, "
                 f"fsw {pt['fsw_Hz'] / 1e3:g} kHz\ninvalid samples are held / predicted by the declared policy "
                 "(never the true current)"), loc="upper left", fontsize=6.5)
    tr_ = r["transition"]
    lim = tr_.get("excursion_limit_A")
    for i, (name, v) in enumerate(tr_["variants"].items()):
        if not v.get("evaluated"):
            continue
        tt = np.asarray(v["t_s"]) * 1e3
        ax2.plot(tt, v["i_err_A"], color=TRV[i % len(TRV)], lw=1.6 if i == 0 else 1.2,
                 ls="-" if i else "--", label=f"{name}: {v['excursion_A']:.3g} A" +
                 (tr(f", Ki×{v['Ki_eff_ratio']:.2g}", f", Ki x{v['Ki_eff_ratio']:.2g}") if abs(v["Ki_eff_ratio"] - 1) > 1e-9 else ""))
    if lim:
        ax2.axhspan(-lim, lim, color=S.VERDICT["PASS"], alpha=0.08, lw=0)
        for sgn in (1, -1):
            ax2.axhline(sgn * lim, color="#cf222e", lw=0.9, ls=":")
    ax2.axvline(0.0, color=t["muted"], lw=1.0)
    ax2.set_xlim(-1.0, None)
    ax2.set_xlabel(tr(f"전환 후 시간 [ms] ({tr_['from_Hz'] / 1e3:g} → {tr_['to_Hz'] / 1e3:g} kHz)",
                      f"time after the change [ms] ({tr_['from_Hz'] / 1e3:g} -> {tr_['to_Hz'] / 1e3:g} kHz)"))
    ax2.set_ylabel(tr("q축 전류 편차 i − i_ref [A]", "q-axis current deviation i - i_ref [A]"))
    ax2.grid(True, alpha=0.35)
    ax2.legend(fontsize=6.5, loc="lower right")
    _note(ax2, tr(f"운전점 {pt['speed_rpm']:.0f} rpm · {pt['torque_Nm']:.0f} N·m: i_q {pt['iq_A']:.0f} A, "
                  f"v_q {pt['vq_V']:.0f} V\n일정 운전점에서의 전환 자체의 과도 (한 축, 비결합)",
                  f"point {pt['speed_rpm']:.0f} rpm · {pt['torque_Nm']:.0f} N m: i_q {pt['iq_A']:.0f} A, "
                  f"v_q {pt['vq_V']:.0f} V\nthe change's own transient at a constant point (one decoupled axis)"),
          loc="upper right", fontsize=6.5)
    return fig


def fig_driveline(fig, r: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    axs = fig.subplots(2, 2, sharex=True)
    g = r["referred"]["g"]
    rad = r.get("wheel_radius_m")
    k = (rad / g) if rad else 1.0                         # vehicle a = r omega_l' / g (no slip); else motor coords
    for name, v in r["variants"].items():
        col = VAR.get(name, S.ACCENT)
        s = v["sim"]
        tt = np.asarray(s["t_s"])
        lab = f"{name}: {v['status']}"
        axs[0, 0].plot(tt, s["T_act"], color=col, lw=1.3, label=lab)
        axs[0, 1].plot(tt, np.asarray(s["acc_l"]) * k, color=col, lw=1.3)
        axs[1, 0].plot(tt, np.asarray(s["jerk_l"]) * k, color=col, lw=1.0)
        axs[1, 1].plot(tt, s["T_shaft"], color=col, lw=1.2)
    rec = r["variants"].get("off", {}).get("record")
    if rec:
        axs[0, 0].step(rec["t_s"], rec["T_request"], where="post", color=t["muted"], ls="--", lw=1.0,
                       label=tr("요청", "request"))
    faded = False
    for name, v in r["variants"].items():                    # damping unavailable / fading (stale signal)
        rc = v.get("record") or {}
        gain = np.asarray(rc.get("gain", []), float)
        if gain.size and np.any(gain < 1.0 - 1e-12):
            ts = np.asarray(rc["t_s"], float)
            low = gain < 1.0 - 1e-12
            edges = np.flatnonzero(np.diff(np.concatenate([[0], low.astype(int), [0]])))
            for a, b in zip(edges[::2], edges[1::2] - 1):
                for ax in axs.ravel():
                    ax.axvspan(ts[a], ts[b], color=t["muted"], alpha=0.12, lw=0)
            faded = True
    if faded:
        axs[0, 0].plot([], [], color=t["muted"], lw=6, alpha=0.3, label=tr("감쇠 불가·페이드 (신호 stale)",
                                                                          "damping faded (stale signal)"))
    w = r.get("window")
    if w:
        axs[0, 0].axhline(w["T_max_Nm"], color="#cf222e", lw=0.8, ls=":")
    req = r.get("requirement") or {}
    jmax = req.get("peak_vehicle_jerk_max_m_s3") if rad else req.get("peak_jerk_max")
    if jmax:
        for sgn in (1, -1):
            axs[1, 0].axhline(sgn * jmax, color="#cf222e", lw=0.9, ls="--")
    axs[0, 0].set_ylabel(tr("실제 토크 T_act [N·m]", "actual torque T_act [N·m]"))
    if rad:
        axs[0, 1].set_ylabel(tr("차량 가속도 [m/s²] (무슬립)", "vehicle acceleration [m/s²] (no slip)"))
        axs[1, 0].set_ylabel(tr("차량 저크 [m/s³] (상태로부터 정확)", "vehicle jerk [m/s³] (exact from state)"))
    else:
        axs[0, 1].set_ylabel(tr("부하 각가속도 [rad/s²] (모터 좌표)", "load angular acceleration [rad/s²] (motor coord.)"))
        axs[1, 0].set_ylabel(tr("부하 각저크 [rad/s³] (모터 좌표)", "load angular jerk [rad/s³] (motor coord.)"))
    axs[1, 1].set_ylabel(tr("축 토크 (모터 좌표) [N·m]", "shaft torque (motor coord.) [N·m]"))
    for ax in axs[1]:
        ax.set_xlabel(tr("시간 [s]", "time [s]"))
    for ax in axs.ravel():
        ax.grid(True, alpha=0.35)
    axs[0, 0].legend(fontsize=6.8, loc="lower right")
    md = r["modal"]
    lines = [tr(f"모드 {md['f_n_Hz']:.2f} Hz, ζ = {md['zeta']:.3f}", f"mode {md['f_n_Hz']:.2f} Hz, ζ = {md['zeta']:.3f}")]
    for name, v in r["variants"].items():
        m = v["metrics"]
        st = v.get("stability") or {}
        t90 = "—" if m.get("t_to_90_s") is None else f"{1e3 * m['t_to_90_s']:.0f} ms"
        ts = "—" if m.get("t_settle_s") is None else f"{1e3 * m['t_settle_s']:.0f} ms"
        jk = m.get("peak_vehicle_jerk_m_s3") if rad else m.get("peak_jerk_abs")
        jtxt = "—" if jk is None else f"{jk:.1f}"
        zc = "" if st.get("dominant_zeta") is None else f" · ζcl {st['dominant_zeta']:.2f}"
        lines.append(f"{name}: t90 {t90} · jerk {jtxt} · settle {ts}{zc}")
    _note(axs[0, 1], "\n".join(lines), loc="lower right", fontsize=6.3)
    return fig


def fig_driveline_stability(fig, s: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.3, 1.0]})
    grid = s["grid"]
    Kd = [row[0]["Kd"] for row in grid]
    dl = [x["delay_ms"] for x in grid[0]]
    Z = np.array([[x["zeta"] if x["stable"] and x["zeta"] is not None else np.nan for x in row] for row in grid])
    U = np.array([[0.0 if x["stable"] else 1.0 for x in row] for row in grid])
    ax.imshow(np.where(U > 0, 1.0, np.nan), origin="lower", aspect="auto", cmap="Reds", vmin=0, vmax=1.5,
              extent=(-0.5, len(dl) - 0.5, -0.5, len(Kd) - 0.5))
    im = ax.imshow(Z, origin="lower", aspect="auto", cmap=t["cmap"], vmin=0, vmax=1.0,
                   extent=(-0.5, len(dl) - 0.5, -0.5, len(Kd) - 0.5))
    ax.grid(False)
    for i, row in enumerate(grid):
        for j, x in enumerate(row):
            z = x["zeta"]
            light = (not x["stable"]) or z is None or z < 0.45        # dark viridis cells: white text
            ax.text(j, i, "✗" if not x["stable"] else (f"{z:.2f}" if z is not None else "—"),
                    ha="center", va="center", fontsize=7, color="white" if light else "black")
    ax.set_xticks(range(len(dl)))
    ax.set_xticklabels([f"{d:g}" for d in dl], fontsize=7)
    ax.set_yticks(range(len(Kd)))
    ax.set_yticklabels([f"{k:g}" for k in Kd], fontsize=7)
    ax.set_xlabel(tr("지연 [ms] (샘플 → 인가)", "delay [ms] (sample → applied)"))
    ax.set_ylabel("Kd [N·m·s/rad]")
    fig.colorbar(im, ax=ax, pad=0.01).set_label(tr("폐루프 모드 ζ (샘플링 루프)", "closed-loop mode ζ (sampled loop)"))
    ax.set_title(tr(f"샘플링 루프 안정성 ({s['feedback_kind']}, Ts {s['sample_ms']:g} ms, ✗ 불안정)",
                    f"sampled-loop stability ({s['feedback_kind']}, Ts {s['sample_ms']:g} ms, ✗ unstable)"), fontsize=9)
    c = s["continuous_relative_speed"]
    k = [x["Kd"] for x in c]
    ax2.plot(k, [x["zeta_undelayed"] for x in c], marker="o", color=S.ACCENT, label=tr("ζ (지연 없음)", "ζ (no delay)"))
    ax2b = ax2.twinx()
    ax2b.plot(k, [x["first_destabilising_delay_ms"] or np.nan for x in c], marker="s", color="#cf222e",
              label=tr("첫 불안정 지연", "first destabilising delay"))
    ax2.set_xlabel("Kd [N·m·s/rad]")
    ax2.set_ylabel("ζ", color=S.ACCENT)
    ax2b.set_ylabel(tr("지연 [ms]", "delay [ms]"), color="#cf222e")
    ax2.set_title(tr("연속 상대속도 피드백 (분석 기준): 감쇠 ↑ ↔ 지연 여유 ↓",
                     "continuous relative-speed feedback (reference): damping ↑ ↔ delay margin ↓"), fontsize=8.5)
    ax2.grid(True, alpha=0.35)
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2b.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, fontsize=7, loc="center right")
    md = s["modal"]
    _note(ax2, tr(f"개루프 모드 {md['f_n_Hz']:.2f} Hz, ζ {md['zeta']:.3f}", f"open-loop mode {md['f_n_Hz']:.2f} Hz, ζ {md['zeta']:.3f}"),
          loc="upper left", fontsize=7)
    return fig
