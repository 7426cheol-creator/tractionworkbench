"""Preview figures of a datasheet import: the curves exactly as they enter the project (grid points marked), so the
digitisation and the common current range can be checked before the section is applied."""

from __future__ import annotations

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


def fig_datasheet_capacitor(fig, data: dict, title: str | None = None):
    _reset(fig, title)
    ax = fig.subplots()
    esr = np.asarray(data.get("ESR_table") or [], float)
    if esr.size:
        ax.loglog(esr[:, 0], esr[:, 1], "o-", color=S.ACCENT)
    ax.set_xlabel(tr("주파수 [Hz]", "frequency [Hz]"))
    ax.set_ylabel(f"ESR [{data.get('ESR_unit', 'mohm')}]")
    ax.set_title(tr(f"ESR(f) · C = {data.get('C_uF', '?')} µF · ESL = {data.get('ESL_nH', '—')} nH (표 밖 외삽 없음)",
                    f"ESR(f) · C = {data.get('C_uF', '?')} µF · ESL = {data.get('ESL_nH', '—')} nH (no extrapolation)"),
                 fontsize=9)
    ax.grid(True, which="both", alpha=0.4)
    return fig
