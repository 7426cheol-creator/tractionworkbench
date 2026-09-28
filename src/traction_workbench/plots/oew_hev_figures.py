"""Figures for the open-end-winding dual inverter and the hybrid system (OEW/HEV addendum).

Every figure draws the result dictionaries of ``api.oew`` / ``api.oew_compare`` / ``api.hev_*`` as they are.
"""

from __future__ import annotations

import math

import numpy as np
from matplotlib.patches import Circle, Patch, Polygon, Rectangle

from ..extensions.oew import hull
from ..i18n import tr
from . import style as S
from .figures import _note, _reset

SQ3 = math.sqrt(3.0)
COL = {"FEASIBLE": "#1a7f37", "INFEASIBLE": "#cf222e", "UNKNOWN": "#b7791f"}
CFG = {"single_vsi_Nm": ("#6e7781", "-", lambda: tr("단일 VSI (VA)", "single VSI (VA)")),
       "oew_common_bus_Nm": (S.ACCENT, "-", lambda: tr("OEW 공통 bus (u0 = 0)", "OEW common bus (u0 = 0)")),
       "oew_isolated_Nm": ("#8250df", "-", lambda: tr("OEW 절연 (VA + VB)", "OEW isolated (VA + VB)")),
       "single_vsi_same_stack_Nm": ("#bf8700", "--", lambda: tr("단일 VSI, 같은 총 전압 (VA + VB)",
                                                                "single VSI, same total stack (VA + VB)"))}


def _hexagon(ax, r_circ, **kw):
    ang = np.radians(np.arange(0, 361, 60))
    ax.plot(r_circ * np.cos(ang), r_circ * np.sin(ang), **kw)


# ---------------------------------------------------------------------------------------------- OEW

def fig_oew_voltage_sets(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    g = res["geometry"]
    ax, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.25, 1.0]})
    VA, VB = g["VA_V"], g["VB_V"]
    allp = np.array(g["all_points"])
    adm = np.array(g["admissible_points"])
    ax.plot(allp[:, 0], allp[:, 1], ls="none", marker="o", ms=4, color=t["muted"], alpha=0.6,
            label=tr(f"64 상태쌍 투영 ({len(allp)}점)", f"64 state pairs projected ({len(allp)} points)"))
    h_all = np.array(hull([tuple(p) for p in allp]) + [tuple(hull([tuple(p) for p in allp])[0])])
    h_adm = np.array(hull([tuple(p) for p in adm]) + [tuple(hull([tuple(p) for p in adm])[0])])
    if g["kind"] == "common_bus":
        ax.plot(h_all[:, 0], h_all[:, 1], color=t["muted"], ls="--", lw=1,
                label=tr("전체 투영 (허용 안 된 u0를 숨김)", "full projection (hides a disallowed u0)"))
        ax.plot(adm[:, 0], adm[:, 1], ls="none", marker="o", ms=7, color=S.ACCENT,
                label=tr(f"u0 = 0 상태쌍 ({g['admissible_pairs']}쌍, {g['admissible_unique_alphabeta']}점)",
                         f"u0 = 0 pairs ({g['admissible_pairs']} pairs, {g['admissible_unique_alphabeta']} points)"))
        ax.fill(h_adm[:, 0], h_adm[:, 1], color=S.ACCENT, alpha=0.08)
        ax.plot(h_adm[:, 0], h_adm[:, 1], color=S.ACCENT, lw=1.8)
    else:
        ax.fill(h_adm[:, 0], h_adm[:, 1], color="#8250df", alpha=0.08)
        ax.plot(h_adm[:, 0], h_adm[:, 1], color="#8250df", lw=1.8,
                label=tr("절연: 부유 δ가 u0 흡수", "isolated: floating delta takes up u0"))
    _hexagon(ax, 2 * VA / 3, color="#6e7781", lw=1.4, label=tr(f"단일 VSI {VA:g} V", f"single VSI {VA:g} V"))
    for r, col, ls, lab in ((VA / SQ3, "#6e7781", ":", f"V/√3 = {VA / SQ3:.1f} V"),
                            (g["hull_inradius_V"], S.ACCENT if g["kind"] == "common_bus" else "#8250df", "-",
                             tr(f"보장 반경 {g['hull_inradius_V']:.1f} V", f"guaranteed radius {g['hull_inradius_V']:.1f} V"))):
        ax.add_patch(Circle((0, 0), r, fill=False, ec=col, ls=ls, lw=1.2))
    w = (res.get("result") or {}).get("witness")
    if w:
        U = w["operating_point"]["U_cmd_pk_V"]
        ax.add_patch(Circle((0, 0), U, fill=False, ec=S.REQUEST, lw=1.6, ls="-."))
        ax.plot([], [], color=S.REQUEST, ls="-.", label=tr(f"운전점 |u| = {U:.1f} V", f"operating |u| = {U:.1f} V"))
    lim = 1.25 * max(g["hull_circumradius_V"], g["unconstrained_hull_circumradius_V"])
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.set_xlabel("u_α [V]")
    ax.set_ylabel("u_β [V]")
    ax.legend(fontsize=6.5, loc="upper left")
    ax.set_title(tr("권선 전압 αβ 집합 (이상 스위치, 선형 평균)", "winding-voltage alpha-beta sets (ideal switches)"),
                 fontsize=9)
    labels = [tr("단일 VSI\nV/√3", "single VSI\nV/√3"), tr("OEW 공통 bus\nu0 = 0: V", "OEW common bus\nu0 = 0: V"),
              tr("OEW 절연\n(VA+VB)/√3", "OEW isolated\n(VA+VB)/√3")]
    vals = [VA / SQ3, VA, (VA + VB) / SQ3]
    cols = ["#6e7781", S.ACCENT, "#8250df"]
    ax2.bar(range(3), vals, color=cols)
    for i, v in enumerate(vals):
        ax2.text(i, v, f"{v:.1f} V", ha="center", va="bottom", fontsize=8)
    ax2.set_xticks(range(3))
    ax2.set_xticklabels(labels, fontsize=7)
    ax2.set_ylabel(tr("상 기본파 peak 한도 [V]", "phase fundamental peak limit [V]"))
    ax2.set_title(tr(f"VA = {VA:g} V, VB = {VB:g} V: 이득은 √3 (공통 bus), 2가 아님",
                     f"VA = {VA:g} V, VB = {VB:g} V: the common-bus gain is √3, not 2"), fontsize=9)
    ax2.set_ylim(0, max(vals) * 1.25)
    _note(ax2, tr("비교 분모 고정: 같은 모터·권선. 두 절연 전원 = 같은 총 전압의 단일 VSI와 한도 동일\n"
                  "(소자 내압이 다름). 토크 2배가 아니라 base speed/약계자 전류가 달라짐",
                  "fixed comparison: same motor and winding; two isolated sources = one VSI on the same total stack\n"
                  "(different device blocking voltage); not twice the torque but a different base speed / FW current"),
          loc="upper left", fontsize=6.3)


def fig_oew_point(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    w = res["result"]["witness"]
    axs = fig.subplots(2, 2)
    wf = w["waveforms"]
    th = np.asarray(wf["theta_deg"])
    a1 = axs[0][0]
    a1.plot(th, wf["u_a_V"], color=S.ACCENT, lw=1.5, label="u_a (= v_Aa − v_Ba)")
    a1.set_ylabel(tr("권선 전압 [V]", "winding voltage [V]"), color=S.ACCENT)
    b1 = a1.twinx()
    b1.plot(th, wf["i_a_A"], color=S.REQUEST, lw=1.5, label="i_a")
    b1.set_ylabel(tr("상전류 [A] (A→권선→B)", "phase current [A] (A->winding->B)"), color=S.REQUEST)
    b1.grid(False)
    b1.spines["right"].set_visible(True)
    a1.set_title(tr("a상 권선 전압·전류 (전기각)", "phase a winding voltage / current"), fontsize=9)
    a2 = axs[0][1]
    a2.plot(th, wf["dA_a"], color=S.ACCENT, lw=1.5, label=tr("브리지 A 듀티 d_Aa", "bridge A duty d_Aa"))
    a2.plot(th, wf["dB_a"], color="#8250df", lw=1.5, label=tr("브리지 B 듀티 d_Ba", "bridge B duty d_Ba"))
    a2.axhspan(0, 1, color=t["grid"], alpha=0.25)
    a2.set_ylim(-0.05, 1.05)
    a2.legend(fontsize=7, loc="lower right")
    a2.set_title(tr("두 브리지의 듀티 (중앙 정렬 오프셋)", "both bridges' duties (centered offsets)"), fontsize=9)
    a3 = axs[1][0]
    a3.plot(th, wf["i0_A"], color="#cf222e", lw=1.5, label="i0")
    a3.plot(th, wf["u0_cmd_V"], color=t["muted"], lw=1.2, ls="--", label="u0* [V]")
    a3.set_xlabel(tr("전기각 [deg]", "electrical angle [deg]"))
    a3.legend(fontsize=7)
    zs = w["zero_sequence"]
    a3.set_title(tr("영상분: ", "zero sequence: ") + zs.get("policy", "")[:60], fontsize=8.5)
    a4 = axs[1][1]
    br = w["bridges"]
    names = ["A P_ac", "B P_ac", tr("A 손실", "A loss"), tr("B 손실", "B loss")]
    vals = [br["A"]["P_ac_W"], br["B"]["P_ac_W"], br["A"]["loss"].get("dc_side_W") or 0.0,
            br["B"]["loss"].get("dc_side_W") or 0.0]
    cols = [S.ACCENT, "#8250df", "#bf8700", "#bf8700"]
    if "shared_source" in w["ports"]:
        names.append(tr("공통 전원", "shared source"))
        vals.append(w["ports"]["shared_source"]["P_dc_W"] or 0.0)
        cols.append("#1a7f37")
    else:
        for tag in ("A", "B"):
            names.append(tr(f"전원 {tag}", f"source {tag}"))
            vals.append(w["ports"][tag]["P_dc_W"] or 0.0)
            cols.append("#1a7f37")
    a4.bar(range(len(vals)), np.asarray(vals) / 1e3, color=cols)
    a4.axhline(0, color=t["fg"], lw=0.8)
    a4.set_xticks(range(len(vals)))
    a4.set_xticklabels(names, fontsize=7, rotation=20)
    a4.set_ylabel("kW")
    a4.set_title(tr("포트 회계 (I_dc,B = −s_Bᵀ i)", "port accounting (I_dc,B = -s_B^T i)"), fontsize=9)
    lines = [f"{c['name']}: {c['status']}" for c in w["claims"]]
    lines.append(tr(f"상 peak {w['currents']['phase_peak_A']:.0f} A (브리지당, 반으로 나누지 않음)",
                    f"phase peak {w['currents']['phase_peak_A']:.0f} A per bridge (never halved)"))
    a4.set_ylim(min(0.0, min(vals) / 1e3) * 1.15, max(vals) / 1e3 * 1.6 if max(vals) > 0 else 1.0)
    _note(a4, "\n".join(lines), loc="upper left", fontsize=6.2)


def fig_oew_compare(fig, cmp: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    rows = [r for r in cmp["rows"] if "single_vsi_Nm" in r]
    n = [r["speed_rpm"] for r in rows]
    for key, (col, ls, lab) in CFG.items():
        y = [r.get(key) for r in rows]
        ax.plot(n, [np.nan if v is None else v for v in y], color=col, ls=ls, lw=2.2 if "oew" in key else 1.6,
                marker="o", ms=3.5, label=lab())
    ax.set_xlabel(tr("속도 [rpm]", "speed [rpm]"))
    ax.set_ylabel(tr("토크 [N·m] (격자 witness, 하한)", "torque [N·m] (grid witness, lower bound)"))
    ax.set_title(tr("같은 모터·같은 방법의 전기적 능력 비교 (전압 + 전류)",
                    "electrical capability, same motor and method (voltage + current)"), fontsize=9)
    ax.legend(fontsize=7.5, loc="upper right")
    notes = [tr("브리지당 전류 한도 동일 (OEW라고 2배가 아님)", "same per-bridge current limit (not doubled by OEW)"),
             tr("DC 전원·손실·열·i0 과도는 미적용", "DC source, losses, thermal and i0 transients not applied")]
    if not cmp.get("zero_sequence_declared"):
        notes.append(tr("공통 bus: 영상분 데이터 없음 → e0 = 0 가정 (조건부)", "common bus: no zero-sequence data -> e0 = 0 assumed"))
    else:
        notes.append(tr(f"공통 bus 영상분 정책: {cmp.get('zs_policy')}", f"common-bus zero-sequence policy: {cmp.get('zs_policy')}"))
    _note(ax, "\n".join(notes), loc="lower left", fontsize=7)


def _pair_label(r: dict) -> str:
    reason = r.get("reason", "")
    if r.get("role") == "normal operation":
        return tr("정상 운전", "normal")
    if "zero-sequence voltage" in reason:
        return tr("u0 = V (DC)\n반례", "u0 = V (DC)\ncounterexample")
    if "half-wave" in reason:
        return tr("반파 단락\n+ 정류", "half-wave short\n+ rectify")
    if "short the winding" in reason or "shorts the winding" in reason:
        return tr("권선 단락\n(ASC 과도)", "winding short\n(ASC transient)")
    if "still switching" in reason:
        return tr("토크 미제거", "torque not\nremoved")
    if "rectification" in reason:
        return tr("정류 위험", "rectification")
    if "no steady diode" in reason:
        return tr("도통 없음", "no conduction")
    if "diode paths" in reason:
        return tr("다이오드 경로", "diode paths")
    return r["status"]


def fig_oew_paired(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    rows = res["paired_states"]
    states = ["pwm", "asc_top", "asc_bottom", "off"]
    names = {"pwm": "PWM", "asc_top": "ASC +", "asc_bottom": "ASC −", "off": tr("OFF (6SO)", "OFF (6SO)")}
    for r in rows:
        i, j = states.index(r["A"]), states.index(r["B"])
        ax.add_patch(Rectangle((j, 3 - i), 1, 1, fc=COL[r["status"]], alpha=0.25, ec=t["bg"], lw=2))
        ax.text(j + 0.5, 3 - i + 0.62, r["status"], ha="center", va="center", fontsize=7, color=COL[r["status"]],
                fontweight="bold")
        ax.text(j + 0.5, 3 - i + 0.32, _pair_label(r), ha="center", va="center", fontsize=6.5, color=t["fg"])
    ax.set_xlim(0, 4)
    ax.set_ylim(0, 4)
    ax.set_xticks([k + 0.5 for k in range(4)])
    ax.set_xticklabels([names[s] for s in states])
    ax.set_yticks([3 - k + 0.5 for k in range(4)])
    ax.set_yticklabels([names[s] for s in states])
    ax.set_xlabel(tr("브리지 B 상태", "bridge B state"))
    ax.set_ylabel(tr("브리지 A 상태", "bridge A state"))
    ax.grid(False)
    ax.set_aspect("equal")
    emf = res.get("emf_phase_peak_V")
    ax.set_title(tr("안전 상태는 두 브리지 '쌍'의 속성 (각 인버터의 ASC 승인 합으로 판단 불가)",
                    "a safe state is a property of the PAIR (not the sum of each bridge's ASC approval)"), fontsize=9)
    txt = tr(f"{res['topology']['kind']} · 역기전력 상 peak {emf:.0f} V" if emf else f"{res['topology']['kind']}",
             f"{res['topology']['kind']} · back-EMF phase peak {emf:.0f} V" if emf else f"{res['topology']['kind']}")
    ax.text(4.05, 0.1, txt + "\n" + tr("상태는 게이트 명령이 아닌 도통 경로로 판정", "judged from conduction paths, not gate commands"),
            fontsize=7, color=t["muted"], va="bottom")


def fig_oew_ripple(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    rip = res.get("i0_ripple") or {}
    cols = [S.ACCENT, "#cf222e", "#8250df"]
    lines = []
    for (k, r), c in zip(rip.items(), cols):
        tr_ = r["trace"]
        ax.plot(np.asarray(tr_["t_s"]) * 1e3, tr_["i0_A"], color=c, lw=1.0,
                label=tr(f"캐리어 위상차 {float(k) * 360:.0f}°", f"carrier shift {float(k) * 360:.0f} deg"))
        lines.append(f"{float(k) * 360:.0f}°: i0 p-p {r['i0_pp_A']:.1f} A, ripple RMS {r['i0_ripple_rms_A']:.1f} A, "
                     f"<u0> err {r['max_carrier_average_u0_error_V']:.1e} V")
    ax.set_xlabel(tr("시간 [ms] (기본파 1주기)", "time [ms] (one fundamental period)"))
    ax.set_ylabel("i0 [A]")
    ax.legend(fontsize=7, loc="upper right")
    ax.set_title(tr("평균 u0는 명령과 같아도 순시 u0 계단이 작은 L0로 i0 리플을 만든다 (스위칭 모델)",
                    "the carrier-average u0 equals the command, yet u0 steps drive i0 ripple through a small L0"),
                 fontsize=9)
    if lines:
        _note(ax, "\n".join(lines), loc="lower left", fontsize=6.8)


# ---------------------------------------------------------------------------------------------- HEV

def fig_hev_joint(fig, js: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.35, 1.0]})
    t1, t2 = np.asarray(js["T1_Nm"]), np.asarray(js["T2_Nm"])
    st = np.array(js["status"], dtype=object)

    def edges(v):
        m = 0.5 * (v[1:] + v[:-1])
        return np.concatenate([[v[0] - (m[0] - v[0])], m, [v[-1] + (v[-1] - m[-1])]])
    e1, e2 = edges(t1), edges(t2)
    for i in range(t1.size):
        for j in range(t2.size):
            ax.add_patch(Rectangle((e1[i], e2[j]), e1[i + 1] - e1[i], e2[j + 1] - e2[j], fc=COL.get(st[i, j], "#999"),
                                   alpha=0.55 if st[i, j] == "FEASIBLE" else 0.28, ec="none"))
    box = js.get("separate_maxima_box") or {}
    if box.get("T1") and box.get("T2"):
        ax.add_patch(Rectangle((box["T1"][0], box["T2"][0]), box["T1"][1] - box["T1"][0], box["T2"][1] - box["T2"][0],
                               fill=False, ec=t["fg"], ls="--", lw=1.3))
        ax.text(box["T1"][1], box["T2"][1], tr(" 개별 최대의 직사각형\n (동시에 가능하지 않음)",
                                               " box of separate maxima\n (not available together)"),
                fontsize=6.8, va="top", ha="right", color=t["fg"])
    rq = js.get("request")
    if rq:
        ax.plot(rq["T1_Nm"], rq["T2_Nm"], marker="*", ms=16, color=S.REQUEST, mec=t["fg"], zorder=6)
    m1, m2 = js["machines"]
    ax.set_xlim(e1[0], e1[-1])
    ax.set_ylim(e2[0], e2[-1])
    ax.set_xlabel(f"{m1['name']} {tr('토크', 'torque')} [N·m] @ {m1['speed_rpm']:g} rpm ({m1['role']})")
    ax.set_ylabel(f"{m2['name']} {tr('토크', 'torque')} [N·m] @ {m2['speed_rpm']:g} rpm ({m2['role']})")
    ax.legend(handles=[Patch(color=COL["FEASIBLE"], alpha=0.55, label=tr("동시 가능 (witness)", "jointly feasible (witness)")),
                       Patch(color=COL["INFEASIBLE"], alpha=0.28, label=tr("불가 (부품 또는 공통 전원)", "infeasible (component or shared source)")),
                       Patch(color=COL["UNKNOWN"], alpha=0.28, label="UNKNOWN")], fontsize=6.8, loc="lower left")
    ax.set_title(tr(f"공통 bus 동시 토크 집합 K ({js['joint_cells_feasible']}/{js['box_cells_feasible_separately']} 셀)",
                    f"joint torque set K on one bus ({js['joint_cells_feasible']}/{js['box_cells_feasible_separately']} cells)"),
                 fontsize=9)
    if rq:
        br = rq["branch_P_dc_W"]
        names = [f"{m1['name']} P_dc", f"{m2['name']} P_dc", tr("기기 순합", "machines net"), tr("배터리", "battery"),
                 tr("순환 전력", "circulating")]
        vals = [br[0], br[1], rq["net_machines_W"], rq["P_source_W"], rq["circulating_W"]]
        vals = [0.0 if v is None else v / 1e3 for v in vals]
        cols = [S.ACCENT, "#8250df", t["muted"], "#1a7f37", "#bf8700"]
        ax2.bar(range(5), vals, color=cols)
        for i, v in enumerate(vals):
            ax2.text(i, v, f"{v:.1f}", ha="center", va="bottom" if v >= 0 else "top", fontsize=7.5)
        ax2.axhline(0, color=t["fg"], lw=0.8)
        ax2.set_xticks(range(5))
        ax2.set_xticklabels(names, fontsize=7, rotation=20)
        ax2.set_ylabel("kW")
        ax2.set_title(tr(f"요구 ({rq['T1_Nm']:.0f}, {rq['T2_Nm']:.0f}) N·m → {rq['status']}: 가지 부하 ≠ 순전력",
                         f"request ({rq['T1_Nm']:.0f}, {rq['T2_Nm']:.0f}) N·m -> {rq['status']}: branch stress != net"),
                      fontsize=9)
        _note(ax2, tr("인버터·커패시터·냉각은 가지 전력으로 선정\n(순전력이 작아도 가지 전력은 클 수 있음)",
                      "size inverters / capacitor / cooling on branch power\n(a small net can hide large branches)"),
              loc="upper right", fontsize=6.8)


def fig_hev_crank(fig, cr: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2, ax3 = fig.subplots(3, 1, sharex=True)
    runs = cr["runs"]
    worst = max(runs, key=lambda r: (not r["ok"], r["reached_s"] or 0.0))
    cmap = __import__("matplotlib").colormaps["viridis"]
    for k, r in enumerate(runs):
        tr_ = r["trace"]
        col = cmap(k / max(1, len(runs) - 1))
        ax1.plot(tr_["t_s"], tr_["n_rpm"], color=col if r["ok"] else "#cf222e", lw=1.2 if r["ok"] else 2.0,
                 label=f"θ0 = {r['theta0_deg']:g}°" + ("" if r["ok"] else " ✗"))
    ax1.axhline(cr["inputs"]["n_target_rpm"], color=t["fg"], ls="--", lw=1)
    ax1.axvline(cr["inputs"]["t_max_s"], color="#cf222e", ls=":", lw=1)
    ax1.set_ylabel(tr("크랭크 속도 [rpm]", "crank speed [rpm]"))
    ax1.legend(fontsize=6, ncols=3, loc="upper left")
    ax1.set_title(tr("크랭킹 replay: 초기 크랭크각별 궤적 (크랭킹 요구, 엔진 시동 보장 아님)",
                     "cranking replay per initial crank angle (cranking requirement, not an engine-start guarantee)"),
                  fontsize=9)
    tw = worst["trace"]
    ratio = cr["inputs"]["ratio"]
    ax2.plot(tw["t_s"], np.asarray(tw["T_em_Nm"]) * ratio, color=S.ACCENT, lw=1.5,
             label=tr("기동 토크 (크랭크 환산)", "starter torque at the crank"))
    ax2.plot(tw["t_s"], tw["T_comp_Nm"], color=S.REQUEST, lw=1.2, label=tr("압축 토크 (각도 의존, 부호 있음)", "compression torque (signed)"))
    ax2.set_ylabel("N·m")
    ax2.legend(fontsize=6.5, loc="upper right")
    ax3.plot(tw["t_s"], tw["V_bus_V"], color="#8250df", lw=1.5, label="V_bus")
    ax3.axhline(cr["starter_capability"]["V_floor_V"], color="#cf222e", ls="--", lw=1, label=tr("UV 바닥", "UV floor"))
    ax3.set_ylabel("V")
    b3 = ax3.twinx()
    b3.plot(tw["t_s"], np.asarray(tw["P_dc_W"]) / 1e3, color="#bf8700", lw=1.2)
    b3.set_ylabel(tr("기동기 P_dc [kW] (주황)", "starter P_dc [kW] (orange)"), color="#bf8700")
    b3.grid(False)
    b3.spines["right"].set_visible(True)
    ax3.set_xlabel(tr("시간 [s]", "time [s]"))
    ax3.legend(fontsize=6.5, loc="lower right")
    c = cr["claim"]
    _note(ax1, f"{c['status']}: " + (c["qualifiers"][0] if c.get("qualifiers") else c["detail"])[:90], loc="lower right",
          fontsize=6.8)


def fig_hev_rejection(fig, lr: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    tr_ = lr["trace"]
    tm = np.asarray(tr_["t_s"]) * 1e3
    ax.plot(tm, tr_["V_V"], color=S.ACCENT, lw=2, label="V_bus")
    c = lr["claim"]
    ax.set_xlabel(tr("부하 차단 후 시간 [ms]", "time after the load trip [ms]"))
    ax.set_ylabel("V_bus [V]")
    b = ax.twinx()
    b.plot(tm, np.asarray(tr_["P_excess_W"]) / 1e3, color=S.REQUEST, lw=1.2, ls="--")
    b.set_ylabel(tr("잉여 전력 [kW] (주황)", "excess power [kW] (orange)"), color=S.REQUEST)
    b.grid(False)
    b.spines["right"].set_visible(True)
    if lr.get("t_peak_s") is not None:
        ax.plot([lr["t_peak_s"] * 1e3], [lr["V_peak_V"]], marker="o", ms=5, color=S.ACCENT)
        ax.annotate(tr(f"최대 {lr['V_peak_V']:.2f} V", f"peak {lr['V_peak_V']:.2f} V"),
                    (lr["t_peak_s"] * 1e3, lr["V_peak_V"]), textcoords="offset points", xytext=(6, -12), fontsize=7)
    tl = lr["time_to_limit_s"]
    if tl is not None and math.isfinite(tl):
        ax.axvline(tl * 1e3, color="#cf222e", ls=":", lw=1.2)
        ax.text(tl * 1e3, 0.02, tr(f" 한계 도달 {tl * 1e6:.0f} µs", f" limit reached {tl * 1e6:.0f} us"),
                transform=ax.get_xaxis_transform(), color="#cf222e", fontsize=7.5)
    ax.legend(fontsize=7, loc="upper left")
    ax.set_title(tr("부하 차단 후 에너지 장부: 공통 커패시터 여유는 한 번만 계상",
                    "energy ledger after a load rejection: the common capacitor margin counted once"), fontsize=9)
    _note(ax, f"{c['status']}: {c['detail'][:110]}\nE_margin = {lr['E_margin_J']:.4g} J, P_excess = {lr['P_excess_W'] / 1e3:.4g} kW",
          loc="lower right", fontsize=6.8)


def fig_planetary(fig, pl: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    Ns, Nr = pl["Ns"], pl["Nr"]
    sp, tq = pl["speeds_rpm"], pl["torques_Nm"]
    xs = {"sun": 0.0, "carrier": float(Nr), "ring": float(Nr + Ns)}
    names = {"sun": tr("선 S", "sun S"), "carrier": tr("캐리어 C", "carrier C"), "ring": tr("링 R", "ring R")}
    ys = [sp[k] for k in ("sun", "carrier", "ring")]
    lo, hi = min(ys + [0.0]), max(ys + [0.0])
    pad = 0.15 * (hi - lo + 1.0)
    for k, x in xs.items():
        ax.plot([x, x], [lo - pad, hi + pad], color=t["muted"], lw=3, solid_capstyle="butt")
        ax.text(x, hi + pad * 1.15, names[k], ha="center", fontsize=8.5, fontweight="bold")
        ax.plot(x, sp[k], marker="o", ms=8, color=S.ACCENT, zorder=5)
        ax.annotate(f"{sp[k]:.0f} rpm\nT = {tq[k]:+.1f} N·m", (x, sp[k]), xytext=(8, -4), textcoords="offset points",
                    fontsize=7.5)
        arrow = np.sign(tq[k]) * 0.1 * (hi - lo + 1.0)
        ax.annotate("", (x + 1.5, sp[k] + arrow), (x + 1.5, sp[k]),
                    arrowprops=dict(arrowstyle="-|>", color=S.REQUEST, lw=1.6))
    xx = np.array([xs["sun"], xs["ring"]])
    ax.plot(xx, [sp["sun"], sp["ring"]], color=S.ACCENT, lw=1.6)
    ax.axhline(0, color=t["fg"], lw=0.8)
    ax.set_xlim(-0.12 * (Nr + Ns), 1.25 * (Nr + Ns))
    ax.set_xticks([])
    ax.set_ylabel(tr("속도 [rpm]", "speed [rpm]"))
    chk = pl["check"]
    ax.set_title(tr(f"단순 유성기어 레버도 (Ns = {Ns}, Nr = {Nr}): Willis 식은 직선",
                    f"simple planetary lever diagram (Ns = {Ns}, Nr = {Nr}): Willis is a straight line"), fontsize=9)
    _note(ax, f"{chk['status']}: {chk['detail']}\n" + tr("토크는 기어로 들어가는 방향이 +, 이상 무손실·무질량",
                                                          "torques positive into the gear set; ideal massless, lossless"),
          loc="lower right", fontsize=7)


# ---------------------------------------------------------------------------------------------- conducted EMI

def fig_emi_screening(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax, ax2 = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [2.2, 1.0]})
    f = np.asarray(res["grid_Hz"]) / 1e6
    L = np.asarray(res["limit_dBuV"], dtype=float)
    ax.plot(f, res["plus_dBuV"], color=S.ACCENT, lw=1.3, label=tr("HV+ 측정단 (추정)", "HV+ port (estimate)"))
    ax.plot(f, res["minus_dBuV"], color="#8250df", lw=1.1, label=tr("HV− 측정단 (추정)", "HV− port (estimate)"))
    ax.plot(f, res["from_cm_source_dBuV"], color="#bf8700", lw=1.0, ls="--", label=tr("CM 소스 기여 (HV+)", "CM-source share (HV+)"))
    ax.plot(f, res["from_dm_source_dBuV"], color="#1a7f37", lw=1.0, ls="--", label=tr("DM 소스 기여 (HV+)", "DM-source share (HV+)"))
    if np.any(np.isfinite(L)):
        ax.plot(f, L, color=t["fg"], lw=2.0, label=tr("한계 (입력 곡선)", "limit (entered curve)"))
        rs = res["profile"].get("design_reserve_dB") or 0.0
        ax.plot(f, L - rs, color=t["fg"], lw=1.0, ls=":", label=tr(f"한계 − 설계 여유 {rs:g} dB", f"limit − reserve {rs:g} dB"))
        ex = np.asarray(res["margin_dB"], dtype=float) < 0
        ax.fill_between(f, L - rs, np.asarray(res["E_upper_dBuV"]), where=ex, color="#cf222e", alpha=0.12,
                        label=tr("예측 초과 (스크리닝)", "predicted exceedance (screening)"))
    ax.set_xscale("log")
    ax.set_ylabel("dBµV")
    ax.legend(fontsize=6.8, loc="upper right", ncols=2)
    c = res["claim"]
    ax.set_title(tr("HV 전도성 방출: 소스(PWM 에지) → 경로(CM/DM 망) → 수신기(RBW 선합) — 스크리닝",
                    "HV conducted emission: source (PWM edges) -> path (CM/DM network) -> receiver (RBW line sum)"),
                 fontsize=9)
    _note(ax, f"{c['status']}: {c['detail'][:120]}\n" + tr("선합 추정치 ≠ CISPR 수신기 판독 (QP/AV 미모델)",
                                                           "line-sum estimate != CISPR receiver reading (QP/AV not modelled)"),
          loc="lower left", fontsize=6.6)
    A = np.asarray(res["required_attenuation_dB"], dtype=float)
    dom = np.asarray(res["dominant_source"])
    cols = np.where(dom == "CM", "#bf8700", "#1a7f37")
    ax2.bar(f, np.nan_to_num(A), width=f * 0.03, color=cols)
    ax2.set_xscale("log")
    ax2.set_ylabel(tr("필요 감쇠 [dB]", "required attenuation [dB]"))
    ax2.set_xlabel(tr("주파수 [MHz]", "frequency [MHz]"))
    ax2.legend(handles=[Patch(color="#bf8700", label=tr("CM 지배 → Y-cap/CM 초크/본딩", "CM-dominated -> Y-cap / CM choke / bonding")),
                        Patch(color="#1a7f37", label=tr("DM 지배 → X-cap/DM 인덕턴스/DC-link ESL", "DM-dominated -> X-cap / DM L / DC-link ESL"))],
               fontsize=6.8, loc="upper right")


def fig_emi_measured(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax = fig.subplots()
    m = res.get("measured") or {}
    if not m:
        ax.text(0.5, 0.5, tr("측정 trace를 가져오세요 (CSV: f_Hz, level_dB)", "import a measured trace (CSV: f_Hz, level_dB)"),
                ha="center", va="center", transform=ax.transAxes, color=t["muted"])
        return
    f = np.asarray(m["f_Hz"]) / 1e6
    x = np.asarray(m["level_dB"])
    U = m.get("U_meas_dB") or 0.0
    ax.plot(f, x, color=S.ACCENT, lw=1.2, label=tr("측정 trace", "measured trace"))
    ax.fill_between(f, x - U, x + U, color=S.ACCENT, alpha=0.15, label=tr(f"측정 불확도 ±{U:g} dB", f"measurement uncertainty ±{U:g} dB"))
    L = np.asarray(m.get("limit") or [], dtype=float)
    if L.size == f.size:
        ax.plot(f, L, color=t["fg"], lw=2, label=tr("한계", "limit"))
        rs = res["profile"].get("design_reserve_dB") or 0.0
        ax.plot(f, L - rs, color=t["fg"], lw=1, ls=":", label=tr("한계 − 여유", "limit − reserve"))
    ax.set_xscale("log")
    ax.set_xlabel(tr("주파수 [MHz]", "frequency [MHz]"))
    ax.set_ylabel("dBµV")
    ax.legend(fontsize=7, loc="upper right")
    ax.set_title(tr(f"측정 trace 판정 (같은 요구 프로파일): {m.get('verdict')}",
                    f"measured-trace verdict (same profile): {m.get('verdict')}"), fontsize=9)
    _note(ax, (m.get("reason") or "")[:120] + "\n" + tr("시험 대표성·승인은 별도", "representativeness / approval are separate"),
          loc="lower left", fontsize=7)


def fig_oew_cm(fig, res: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1.5, 1.0]})
    cases = res["cases"]
    styles = {"0": ("-", tr("캐리어 동위상", "carriers in phase")), "0.5": ("--", tr("캐리어 180° 교차", "carriers interleaved 180°"))}
    for k, c in cases.items():
        ls, lab = styles.get(k, ("-", k))
        f = np.asarray(c["f_Hz"]) / 1e3
        ax1.plot(f, np.asarray(c["u0_amp_V"]), color="#cf222e", ls=ls, lw=1.0, label=f"u0 ({tr('권선', 'winding')}) · {lab}")
        ax1.plot(f, np.asarray(c["cm6_amp_V"]), color=S.ACCENT, ls=ls, lw=1.0, label=f"v_cm6 ({tr('섀시', 'chassis')}) · {lab}")
    ax1.set_yscale("log")
    ax1.set_ylim(bottom=1e-2)
    ax1.set_xlabel(tr("주파수 [kHz]", "frequency [kHz]"))
    ax1.set_ylabel(tr("선 진폭 [V]", "line amplitude [V]"))
    ax1.legend(fontsize=6.5, loc="upper right")
    ax1.set_title(tr("공통 bus OEW: 권선 영상분 u0 ≠ 섀시 공통모드 v_cm6", "common-bus OEW: winding u0 != chassis common mode"),
                  fontsize=9)
    names, u0s, cms, ia, isum = [], [], [], [], []
    for k, c in cases.items():
        names.append(styles.get(k, ("", k))[1])
        u0s.append(c["u0_rms_V"])
        cms.append(c["cm6_rms_V"])
        dc = c.get("dc_currents") or {}
        ia.append(dc.get("I_A_rms_A", 0.0))
        isum.append(dc.get("I_sum_rms_A", 0.0))
    x = np.arange(len(names))
    ax2.bar(x - 0.3, u0s, 0.2, color="#cf222e", label="u0 RMS [V]")
    ax2.bar(x - 0.1, cms, 0.2, color=S.ACCENT, label="v_cm6 RMS [V]")
    ax2.bar(x + 0.1, ia, 0.2, color="#bf8700", label=tr("브리지 A 직류측 리플 [A]", "bridge A DC ripple [A]"))
    ax2.bar(x + 0.3, isum, 0.2, color="#1a7f37", label=tr("합성 bus 리플 [A]", "combined bus ripple [A]"))
    ax2.set_xticks(x)
    ax2.set_xticklabels(names, fontsize=7.5)
    ax2.legend(fontsize=6.5, loc="upper left")
    ax2.set_title(tr("한쪽을 줄이면 다른 쪽이 커질 수 있음 (C-01/C-02)", "suppressing one can raise the other (C-01 / C-02)"),
                  fontsize=9)
    z = res.get("zsv_free") or {}
    if z:
        _note(ax1, tr(f"영상분 제거 상태쌍 시퀀스: u0 최대 {z['u0_max_V']:.1f} V, v_cm6 계단 {z['v_cm6_steps_V']}",
                      f"zero-u0 pair sequence: u0 max {z['u0_max_V']:.1f} V, v_cm6 steps {z['v_cm6_steps_V']}"),
              loc="lower left", fontsize=6.3)
