"""Preview figures of a datasheet import: the curves exactly as they enter the project (grid points marked), so the
digitisation and the common current range can be checked before the section is applied."""

from __future__ import annotations

import math

import numpy as np

from ..i18n import tr
from . import style as S
from .figures import _reset

_ENERGY = {"J": 1e3, "mJ": 1.0, "uJ": 1e-3}          # -> mJ


def fig_datasheet_module(fig, data: dict, title: str | None = None):
    _reset(fig, title)
    t = S.theme()
    cv = data.get("curves") or {}
    ax1, ax2 = fig.subplots(1, 2)
    styles = {"v_on": "-", "v_rev": "--", "v_channel_rev": ":", "e_on": "-", "e_off": "--", "e_rr": ":"}
    for ax, keys, ylab in ((ax1, ("v_on", "v_rev", "v_channel_rev"), tr("전압 [V]", "voltage [V]")),
                           (ax2, ("e_on", "e_off", "e_rr"), tr("에너지 [mJ]", "energy [mJ]"))):
        for name in keys:
            c = cv.get(name)
            if not c:
                continue
            cur = np.asarray(c["currents_A"], float)
            k = 1.0 if c["unit"] == "V" else _ENERGY[c["unit"]]
            for j, (T, row) in enumerate(zip(c["temps_C"], c["values"])):
                col = S.PHASE[j % 3]
                ax.plot(cur, np.asarray(row, float) * k, styles[name], color=col, lw=1.4, marker="o", ms=2.5,
                        label=f"{name} · {T:g} °C")
        ax.set_xlabel(tr("전류 [A]", "current [A]"))
        ax.set_ylabel(ylab)
        ax.grid(True, alpha=0.4)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc="upper left")
    ax1.set_title(tr("도통 특성 (공통 전류 구간, 외삽 없음)", "on-state (common current range, no extrapolation)"),
                  fontsize=9, color=t["fg"])
    ax2.set_title(tr("스위칭 에너지 (시험 전압 ", "switching energies (test voltage ") + f"{data.get('v_test_V', '?'):g} V)",
                  fontsize=9, color=t["fg"])
    return fig


def fig_datasheet_capacitor(fig, data: dict, title: str | None = None, point: tuple | None = None):
    """ESR(f) as it enters the project; ``point`` (f_Hz, ESR) marks the one datasheet value of a representative
    entry (the flat line is the declared band, not a datasheet curve)."""
    _reset(fig, title)
    ax = fig.subplots()
    esr = np.asarray(data.get("ESR_table") or [], float)
    if esr.size:
        ax.loglog(esr[:, 0], esr[:, 1], "o-", color=S.ACCENT,
                  label=tr("프로젝트에 들어갈 ESR(f)", "ESR(f) entering the project"))
    if point is not None:
        ax.loglog([point[0]], [point[1]], "D", color=S.REQUEST, ms=7, zorder=5,
                  label=tr(f"데이터시트 값 ({point[1]:g} mΩ @ {point[0]:g} Hz) — 대역은 선언",
                           f"datasheet value ({point[1]:g} mOhm @ {point[0]:g} Hz) - the band is declared"))
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=7.5, loc="upper center")
    ax.set_xlabel(tr("주파수 [Hz]", "frequency [Hz]"))
    ax.set_ylabel(f"ESR [{data.get('ESR_unit', 'mohm')}]")
    ax.set_title(tr(f"ESR(f) · C = {data.get('C_uF', '?')} µF · ESL = {data.get('ESL_nH', '—')} nH (표 밖 외삽 없음)",
                    f"ESR(f) · C = {data.get('C_uF', '?')} µF · ESL = {data.get('ESL_nH', '—')} nH (no extrapolation)"),
                 fontsize=9)
    ax.grid(True, which="both", alpha=0.4)
    return fig


def mtpa_current(psi: float, Ld: float, Lq: float, i: np.ndarray) -> tuple:
    """Maximum torque per ampere of the constant-parameter machine: id = 2 dL I^2 / (psi + sqrt(psi^2 + 8 dL^2 I^2)),
    dL = Ld - Lq (the rationalised root, exact for a surface machine dL = 0)."""
    i = np.asarray(i, float)
    dl = Ld - Lq
    den = psi + np.sqrt(psi * psi + 8.0 * dl * dl * i * i)
    id_ = np.where(den > 0, 2.0 * dl * i * i / np.where(den > 0, den, 1.0), -i / np.sqrt(2.0))
    return id_, np.sqrt(np.maximum(i * i - id_ * id_, 0.0))


def fig_datasheet_motor(fig, m: dict, title: str | None = None):
    """What the typed motor values imply with the project's inverter - computed from the declared constant-parameter
    model only: the no-load back-EMF against the DC voltage (field weakening needed / uncontrolled generation when
    the inverter stops) and the current plane (MTPA, current limit, characteristic current psi / Ld)."""
    _reset(fig, title)
    t = S.theme()
    ax1, ax2 = fig.subplots(1, 2)
    p, psi, ld, lq = m["p"], m["psi_Wb"], m["Ld_H"], m["Lq_H"]
    vdc, res, imax = m["Vdc_V"], m.get("reserve", 0.0), m["I_max_A"]
    n_hi = max(float(m.get("n_max_rpm") or 0.0), 1.0)
    n = np.linspace(0.0, 1.05 * n_hi, 400)
    k = math.sqrt(3.0) * psi * p * 2.0 * math.pi / 60.0          # line-line peak volts per rpm
    ax1.plot(n, k * n, color=S.ACCENT, lw=1.8, label=tr("무부하 선간 역기전력 (피크)", "no-load back-EMF (line-line peak)"))
    ax1.axhline(vdc, color="#cf222e", ls="--", lw=1.2, label=f"Vdc = {vdc:g} V")
    if res > 0:
        ax1.axhline((1 - res) * vdc, color=S.GROUP["VOLTAGE"], ls=":", lw=1.2,
                    label=tr(f"선형 변조 한계 (1 − {res:g}) Vdc", f"linear modulation limit (1 - {res:g}) Vdc"))
    notes = []
    if k > 0:
        n_fw = (1 - res) * vdc / k
        n_ucg = vdc / k
        if n_fw <= 1.05 * n_hi:
            ax1.axvline(n_fw, color=S.GROUP["VOLTAGE"], lw=0.8, alpha=0.7)
        if n_ucg <= 1.05 * n_hi:
            ax1.axvspan(n_ucg, 1.05 * n_hi, color="#cf222e", alpha=0.08, lw=0)
            notes.append(tr(f"{n_ucg:,.0f} rpm 이상: 인버터 정지 시 다이오드 정류 (비제어 발전)",
                            f"above {n_ucg:,.0f} rpm: diode rectification when the inverter stops (UCG)"))
        notes.insert(0, tr(f"무부하에서도 약계자 필요: {n_fw:,.0f} rpm 이상", f"field weakening even at no load above "
                                                                       f"{n_fw:,.0f} rpm"))
    ax1.set_xlim(0, 1.05 * n_hi)
    ax1.set_ylim(0, max(1.15 * vdc, 1.05 * k * n_hi))
    ax1.set_xlabel(tr("속도 [rpm, 기계]", "speed [rpm, mechanical]"))
    ax1.set_ylabel(tr("전압 [V]", "voltage [V]"))
    ax1.set_title(tr("역기전력 대 DC 전압 (운전 영역 최고 속도까지)", "back-EMF vs DC voltage (to the domain's top speed)"),
                  fontsize=9, color=t["fg"])
    ax1.grid(True, alpha=0.4)
    ax1.legend(fontsize=7, loc="upper left")
    if notes:
        ax1.text(0.99, 0.02, "\n".join(notes), transform=ax1.transAxes, ha="right", va="bottom", fontsize=7.2,
                 color=t["fg"])
    th = np.linspace(0.0, 2.0 * math.pi, 361)
    ax2.plot(imax * np.cos(th), imax * np.sin(th), color=S.GROUP["CURRENT"], lw=1.3,
             label=tr(f"전류 한계 |i| = {imax:g} A", f"current limit |i| = {imax:g} A"))
    ii = np.linspace(0.0, imax, 200)
    idm, iqm = mtpa_current(psi, ld, lq, ii)
    ax2.plot(idm, iqm, color=S.MTPA, lw=1.8, label="MTPA")
    ax2.plot(idm, -iqm, color=S.MTPA, lw=1.0, ls="--")
    tq = 1.5 * p * (psi * iqm[-1] + (ld - lq) * idm[-1] * iqm[-1])
    ich = psi / ld if ld > 0 else float("inf")
    if math.isfinite(ich):
        ax2.plot([-ich], [0.0], "X", color=S.GROUP["VOLTAGE"], ms=9,
                 label=tr(f"특성 전류 −ψ/Ld = {-ich:,.0f} A", f"characteristic current -psi/Ld = {-ich:,.0f} A"))
    if ich <= imax:
        fw = tr("특성 전류가 한계 안: 이론상 약계자 속도 제한 없음", "characteristic current inside the limit: no "
                                                          "field-weakening speed bound in theory")
    else:
        fw = tr("특성 전류가 한계 밖: 약계자 속도에 한계가 있음", "characteristic current outside the limit: the "
                                                        "field-weakening speed is bounded")
    ax2.text(0.02, 0.02, tr(f"MTPA 전자기 토크 @ {imax:g} A: {tq:,.1f} N·m (전압 제한 전)",
                            f"MTPA electromagnetic torque @ {imax:g} A: {tq:,.1f} N*m (before the voltage limit)")
             + "\n" + fw, transform=ax2.transAxes, ha="left", va="bottom", fontsize=7.2, color=t["fg"])
    lim = 1.12 * max(imax, ich if math.isfinite(ich) else 0.0)
    ax2.set_xlim(-lim, 0.35 * lim)
    ax2.set_ylim(-lim, lim)
    ax2.set_aspect("equal", adjustable="datalim")
    ax2.axhline(0, color=t["muted"], lw=0.6)
    ax2.axvline(0, color=t["muted"], lw=0.6)
    ax2.set_xlabel("id [A]")
    ax2.set_ylabel("iq [A]")
    ax2.set_title(tr("전류 평면 (상수 dq 모델, 피크 값)", "current plane (constant dq model, peak values)"), fontsize=9,
                  color=t["fg"])
    ax2.grid(True, alpha=0.4)
    ax2.legend(fontsize=7, loc="upper left")
    return fig
