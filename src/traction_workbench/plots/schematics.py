"""Explanatory schematics drawn with matplotlib (IEC-style symbols).

* powertrain circuit: HV battery, main contactors (+ pre-charge), DC-link capacitor, active discharge,
  three-phase bridge (switches with anti-parallel diodes), PMSM and shaft, with the scenario state
  (contactors open/closed, switch pattern, diode conduction) and power-flow arrows;
* safe-state bridges (ASC vs freewheel);
* thermal networks (Foster chain / Cauer ladder) and the coolant loop.

The drawings show topology and scenario state for understanding; they are not layouts and add no physics.
"""

from __future__ import annotations

import math

from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

from ..i18n import tr
from . import style as S

FS = 7.5


def _c():
    t = S.theme()
    return {"fg": t["fg"], "muted": t["muted"], "bg": t["bg"], "panel": t["panel"], "on": S.ACCENT, "hot": "#cf222e",
            "flow": S.REQUEST, "ok": "#1a7f37", "grid": t["grid"]}


def new_axes(fig, xlim, ylim, rect=None):
    ax = fig.add_axes(rect) if rect is not None else fig.add_subplot()
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")
    return ax


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

def wire(ax, *pts, color=None, lw=1.5, ls="-", z=2):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color or _c()["fg"], lw=lw, ls=ls, zorder=z, solid_capstyle="round", solid_joinstyle="round")


def dot(ax, x, y, color=None, r=0.055):
    ax.add_patch(Circle((x, y), r, color=color or _c()["fg"], zorder=6))


def text(ax, x, y, s, ha="center", va="center", size=FS, color=None, weight="normal", box=False, z=8):
    kw = {}
    if box:
        c = _c()
        kw["bbox"] = dict(boxstyle="round,pad=0.25", fc=c["panel"], ec=c["grid"], alpha=0.95)
    ax.text(x, y, s, ha=ha, va=va, fontsize=size, color=color or _c()["fg"], fontweight=weight, zorder=z, **kw)


def resistor(ax, p0, p1, label=None, color=None, lw=1.5, lab_off=(0.22, 0.0), ha="left", body=0.55):
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    h = min(body, 0.6 * L)
    w = 0.24
    xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
    a = (xc - ux * h / 2, yc - uy * h / 2)
    b = (xc + ux * h / 2, yc + uy * h / 2)
    wire(ax, p0, a, color=color, lw=lw)
    wire(ax, b, p1, color=color, lw=lw)
    nx, ny = -uy, ux
    corners = [(a[0] + nx * w / 2, a[1] + ny * w / 2), (b[0] + nx * w / 2, b[1] + ny * w / 2),
               (b[0] - nx * w / 2, b[1] - ny * w / 2), (a[0] - nx * w / 2, a[1] - ny * w / 2)]
    ax.add_patch(Polygon(corners, closed=True, fill=True, fc=_c()["bg"], ec=color or _c()["fg"], lw=lw, zorder=4))
    if label:
        text(ax, xc + lab_off[0], yc + lab_off[1], label, ha=ha)


def capacitor(ax, p0, p1, label=None, color=None, lw=1.5, lab_off=(0.3, 0.0), ha="left", gap=0.16, plate=0.5):
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
    a = (xc - ux * gap / 2, yc - uy * gap / 2)
    b = (xc + ux * gap / 2, yc + uy * gap / 2)
    wire(ax, p0, a, color=color, lw=lw)
    wire(ax, b, p1, color=color, lw=lw)
    nx, ny = -uy, ux
    for q in (a, b):
        wire(ax, (q[0] + nx * plate / 2, q[1] + ny * plate / 2), (q[0] - nx * plate / 2, q[1] - ny * plate / 2),
             color=color, lw=lw + 1.0, z=4)
    if label:
        text(ax, xc + lab_off[0], yc + lab_off[1], label, ha=ha)


def battery(ax, x, y0, y1, label=None, color=None):
    yc = (y0 + y1) / 2
    wire(ax, (x, y0), (x, yc - 0.42), color=color)
    wire(ax, (x, yc + 0.42), (x, y1), color=color)
    for k, yy in enumerate((yc + 0.42, yc + 0.14, yc - 0.14, yc - 0.42)):
        w = 0.6 if k % 2 == 0 else 0.3
        wire(ax, (x - w / 2, yy), (x + w / 2, yy), color=color, lw=2.6 if k % 2 == 0 else 3.6, z=4)
    text(ax, x + 0.42, yc + 0.5, "+", size=9, weight="bold")
    text(ax, x + 0.42, yc - 0.5, "−", size=9, weight="bold")
    if label:
        text(ax, x - 0.5, yc, label, ha="right")


def switch(ax, p0, p1, closed=True, label=None, color=None, lab_off=(0.0, 0.3), lw=1.5, blade=0.6):
    """Contactor / relay contact between p0 and p1 (straight segment)."""
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
    a = (xc - ux * blade / 2, yc - uy * blade / 2)
    b = (xc + ux * blade / 2, yc + uy * blade / 2)
    col = color or (_c()["fg"] if closed else _c()["hot"])
    wire(ax, p0, a, color=color, lw=lw)
    wire(ax, b, p1, color=color, lw=lw)
    dot(ax, *a, color=col, r=0.05)
    dot(ax, *b, color=col, r=0.05)
    if closed:
        wire(ax, a, b, color=col, lw=lw + 0.6, z=5)
    else:
        ang = math.radians(32)
        nx, ny = -uy, ux
        ex = a[0] + blade * (ux * math.cos(ang) + nx * math.sin(ang))
        ey = a[1] + blade * (uy * math.cos(ang) + ny * math.sin(ang))
        wire(ax, a, (ex, ey), color=col, lw=lw + 0.6, z=5)
    if label:
        text(ax, xc + lab_off[0], yc + lab_off[1], label, color=col if not closed else None)


def diode(ax, x, y, up=True, color=None, size=0.22, lw=1.3):
    c = color or _c()["fg"]
    s = size
    if up:
        tri = [(x - s, y - s * 0.8), (x + s, y - s * 0.8), (x, y + s * 0.8)]
        bar = ((x - s, y + s * 0.8), (x + s, y + s * 0.8))
    else:
        tri = [(x - s, y + s * 0.8), (x + s, y + s * 0.8), (x, y - s * 0.8)]
        bar = ((x - s, y - s * 0.8), (x + s, y - s * 0.8))
    ax.add_patch(Polygon(tri, closed=True, fc=c if color else _c()["bg"], ec=c, lw=lw, zorder=5))
    wire(ax, *bar, color=c, lw=lw + 0.4, z=5)


def semi(ax, x, y_top, y_bot, yc, on=False, conducting_diode=False, label=None, pwm=False):
    """Power switch (box) with an anti-parallel diode between y_bot (emitter side) and y_top (collector side)."""
    c = _c()
    h, w = 0.62, 0.36
    wire(ax, (x, y_top), (x, yc + h / 2))
    wire(ax, (x, yc - h / 2), (x, y_bot))
    fc = c["on"] if on else c["bg"]
    ax.add_patch(Rectangle((x - w / 2, yc - h / 2), w, h, fc=fc, ec=c["fg"], lw=1.3, zorder=5,
                           hatch="////" if pwm and not on else None, alpha=1.0))
    if on:
        text(ax, x, yc, "ON", size=6, color="white", weight="bold")
    # anti-parallel diode branch
    xd = x + 0.5
    dcol = c["flow"] if conducting_diode else None
    wire(ax, (x, yc + h / 2 + 0.12), (xd, yc + h / 2 + 0.12), (xd, yc + 0.28), color=dcol, lw=1.2)
    wire(ax, (x, yc - h / 2 - 0.12), (xd, yc - h / 2 - 0.12), (xd, yc - 0.28), color=dcol, lw=1.2)
    diode(ax, xd, yc, up=True, color=dcol, size=0.2)
    if label:
        text(ax, x - 0.3, yc, label, ha="right", size=6.5, color=c["muted"])


def motor(ax, x, y, r=0.85, label="M\n3~", spinning=False):
    c = _c()
    ax.add_patch(Circle((x, y), r, fc=c["panel"], ec=c["fg"], lw=1.6, zorder=4))
    text(ax, x, y, label, size=9, weight="bold")
    if spinning:
        ax.add_patch(FancyArrowPatch((x - 0.45 * r, y + 1.15 * r), (x + 0.45 * r, y + 1.15 * r),
                                     connectionstyle="arc3,rad=-0.4", arrowstyle="-|>", mutation_scale=9,
                                     color=c["muted"], lw=1.1, zorder=4))


def flow(ax, p0, p1, label=None, color=None, lw=2.4, lab_off=(0.0, 0.28), size=FS, rad=0.0):
    col = color or _c()["flow"]
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=13, color=col, lw=lw, zorder=7,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        text(ax, (p0[0] + p1[0]) / 2 + lab_off[0], (p0[1] + p1[1]) / 2 + lab_off[1], label, color=col, size=size,
             weight="bold")


def block(ax, x, y, w, h, label, fc=None, ec=None, size=FS, weight="bold"):
    c = _c()
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc=fc or c["panel"], ec=ec or c["fg"], lw=1.4, zorder=4))
    text(ax, x, y, label, size=size, weight=weight)


# ---------------------------------------------------------------------------
# powertrain
# ---------------------------------------------------------------------------

X_PAS, X_CAP, X_DIS = 3.45, 4.45, 6.05
LEGS = ((7.5, 2.55, "a"), (9.0, 2.0, "b"), (10.5, 1.45, "c"))
MX, MR = 12.6, 0.85
TOP, BOT = 4.0, 0.0
XLIM = (-1.7, 15.2)
YLIM = (-1.35, 5.7)


def _num(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.4g}"


def draw_powertrain(ax, relay_closed=True, precharge=False, discharge_on=False, bridge="pwm", power_flow=None,
                    rectifying=False, labels=None, show_battery=True, spinning=True, show_discharge=True,
                    show_passive=True, passive_on=False):
    """bridge: 'pwm' | 'off' | 'asc_low' | 'asc_high'; power_flow: 'motoring' | 'regen' | 'rectify' | None."""
    c = _c()
    L = labels or {}
    x_b, x_k0, x_k1 = 0.6, 1.3, 2.7
    if show_battery:
        battery(ax, x_b, BOT, TOP, L.get("battery", tr("HV 배터리", "HV battery")))
        wire(ax, (x_b, TOP), (x_k0, TOP))
        wire(ax, (x_b, BOT), (x_k0, BOT))
        switch(ax, (x_k0, TOP), (x_k1, TOP), relay_closed, L.get("relay_p", tr("메인 릴레이 (+)", "main contactor (+)")),
               lab_off=(0.0, -0.38))
        switch(ax, (x_k0, BOT), (x_k1, BOT), relay_closed, L.get("relay_n", tr("메인 릴레이 (−)", "main contactor (−)")),
               lab_off=(0.0, 0.38))
        # pre-charge branch around the + contactor
        wire(ax, (x_k0 - 0.1, TOP), (x_k0 - 0.1, TOP + 0.75), color=c["muted"], lw=1.1)
        resistor(ax, (x_k0 - 0.1, TOP + 0.75), (x_k0 + 0.75, TOP + 0.75), color=c["muted"], lw=1.1, body=0.45)
        switch(ax, (x_k0 + 0.75, TOP + 0.75), (x_k1 + 0.1, TOP + 0.75), precharge, None, color=c["muted"], lw=1.1,
               blade=0.4)
        wire(ax, (x_k1 + 0.1, TOP + 0.75), (x_k1 + 0.1, TOP), color=c["muted"], lw=1.1)
        text(ax, (x_k0 + x_k1) / 2, TOP + 1.1, tr("프리차지", "pre-charge"), size=6.5, color=c["muted"])
        dot(ax, x_k0 - 0.1, TOP)
        dot(ax, x_k1 + 0.1, TOP)
        if not relay_closed:
            text(ax, (x_k0 + x_k1) / 2, (TOP + BOT) / 2, tr("배터리\n분리", "battery\ndisconnected"), color=c["hot"],
                 weight="bold", size=8)
    x_start = x_k1 if show_battery else X_CAP - 0.6
    x_last = LEGS[-1][0]
    wire(ax, (x_start, TOP), (x_last, TOP))
    wire(ax, (x_start, BOT), (x_last, BOT))
    capacitor(ax, (X_CAP, TOP), (X_CAP, BOT), L.get("cap", "C_dc"), lab_off=(0.32, 0.0))
    dot(ax, X_CAP, TOP)
    dot(ax, X_CAP, BOT)
    if show_passive and show_battery:
        pcol = c["hot"] if passive_on else c["muted"]
        resistor(ax, (X_PAS, TOP), (X_PAS, 2.35), None, color=pcol, body=0.8)
        wire(ax, (X_PAS, 2.35), (X_PAS, BOT), color=pcol)
        text(ax, X_PAS - 0.2, 3.2, L.get("pas_r", "R_p"), ha="right", size=6.8, color=pcol if passive_on else None)
        text(ax, X_PAS - 0.2, 1.25, tr("패시브\n방전\n(상시)", "passive\nbleeder\n(always)"), ha="right", size=6.3,
             color=c["muted"])
        dot(ax, X_PAS, TOP)
        dot(ax, X_PAS, BOT)
        if passive_on:
            flow(ax, (X_PAS + 0.3, 3.45), (X_PAS + 0.3, 2.45), None, color=c["hot"], lw=1.8)
    if show_discharge:
        dcol = c["hot"] if discharge_on else None
        resistor(ax, (X_DIS, TOP), (X_DIS, 2.0), L.get("dis_r", "R_dis"), color=dcol, lab_off=(0.25, 0.0))
        switch(ax, (X_DIS, 2.0), (X_DIS, BOT), discharge_on, None, color=dcol if discharge_on else c["muted"], blade=0.7)
        text(ax, X_DIS - 0.22, 0.75, tr("능동\n방전", "active\ndischarge"), ha="right", size=6.5, color=c["muted"])
        dot(ax, X_DIS, TOP)
        dot(ax, X_DIS, BOT)
        if discharge_on:
            flow(ax, (X_DIS - 0.32, 3.35), (X_DIS - 0.32, 2.35), None, color=c["hot"], lw=1.8)
    for (x, ym, ph) in LEGS:
        semi(ax, x, TOP, ym, 3.3, on=bridge == "asc_high", conducting_diode=rectifying, pwm=bridge == "pwm")
        semi(ax, x, ym, BOT, 0.7, on=bridge == "asc_low", conducting_diode=rectifying, pwm=bridge == "pwm")
        dot(ax, x, TOP)
        dot(ax, x, BOT)
        dot(ax, x, ym)
        wire(ax, (x, ym), (MX - MR * math.cos(math.asin((ym - 2.0) / MR)), ym))
        text(ax, MX - MR - 0.2, ym + 0.14, ph, size=7, color=c["muted"])
    mode_txt = {"pwm": tr("PWM 스위칭", "PWM switching"), "off": tr("모든 스위치 OFF", "all switches OFF"),
                "asc_low": tr("ASC: 하단 3개 ON", "ASC: 3 low-side ON"), "asc_high": tr("ASC: 상단 3개 ON", "ASC: 3 high-side ON")}
    text(ax, LEGS[1][0], TOP + 0.62, tr("인버터 (3상 2-level)", "inverter (3-phase 2-level)") + " · " + mode_txt.get(bridge, bridge),
         size=7.5, weight="bold")
    motor(ax, MX, 2.0, r=MR, spinning=spinning)
    wire(ax, (MX + MR, 2.0), (MX + 2.1, 2.0), lw=3.2)
    text(ax, MX + 1.5, 2.35, L.get("shaft", tr("축", "shaft")), size=7)
    x_dc_arrow = ((x_start + 0.15, X_CAP - 0.15) if show_battery else None)
    rail_back = (LEGS[0][0] - 0.1, X_CAP + 0.15)       # along the top rail, bridge -> DC link
    if power_flow == "motoring":
        if x_dc_arrow:
            flow(ax, (x_dc_arrow[0], TOP + 0.3), (x_dc_arrow[1], TOP + 0.3), None, color=c["on"])
        flow(ax, (MX - 2.0, 2.85), (MX - 1.05, 2.85), None, color=c["on"])
        flow(ax, (MX + 0.95, 1.55), (MX + 2.0, 1.55), L.get("shaft_flow"), color=c["on"], lab_off=(0, -0.33))
        if L.get("dc_flow"):
            text(ax, (X_CAP + X_DIS) / 2, TOP + 0.72, L["dc_flow"], color=c["on"], weight="bold", size=7)
    elif power_flow in ("regen", "rectify"):
        col = c["hot"] if power_flow == "rectify" else c["flow"]
        flow(ax, (MX - 1.05, 2.85), (MX - 2.0, 2.85), None, color=col)
        if power_flow == "regen":
            flow(ax, (MX + 2.0, 1.55), (MX + 0.95, 1.55), L.get("shaft_flow"), lab_off=(0, -0.33))
        if relay_closed and show_battery:
            flow(ax, (x_dc_arrow[1], TOP + 0.3), (x_dc_arrow[0], TOP + 0.3), None, color=col)
        else:
            flow(ax, (rail_back[0], TOP + 0.3), (rail_back[1], TOP + 0.3), None, color=col)
        if L.get("dc_flow"):
            text(ax, (X_CAP + X_DIS) / 2 + 0.3, TOP + 0.72, L["dc_flow"], color=col, weight="bold", size=7)
    if L.get("phase"):
        text(ax, MX, TOP + 0.35, L["phase"], size=7)
    if L.get("motor"):
        text(ax, MX, 0.72, L["motor"], size=7)
    if L.get("vdc"):
        text(ax, X_CAP - 0.25, 2.0, L["vdc"], ha="right", size=7)
    if L.get("note"):
        text(ax, (XLIM[0] + XLIM[1]) / 2, -0.85, L["note"], size=7, box=True)


def fig_system_overview(fig, info: dict, title: str | None = None):
    """Powertrain with the operating point's quantities (decision / explorer pages)."""
    fig.clear()
    fig.set_layout_engine("none")
    if title:
        fig.suptitle(title, fontsize=10, fontweight="bold", color=S.theme()["fg"])
    ax = new_axes(fig, XLIM, YLIM, rect=[0.01, 0.02, 0.98, 0.9])
    mode = info.get("energy_mode")
    pf = "regen" if mode == "REGENERATING" or (info.get("Pdc_W") or 0) < 0 else ("motoring" if (info.get("Pdc_W") or 0) > 0 else None)
    labels = {
        "cap": f"C_dc\nVdc = {info['Vdc_V']:.0f} V",
        "dc_flow": None if info.get("Pdc_W") is None else f"P_dc {info['Pdc_W'] / 1e3:.1f} kW\nI_dc {info['Idc_A']:.1f} A",
        "phase": f"I_ph {info['i_rms_A']:.1f} A rms · f_e {info['f_e_Hz']:.0f} Hz\nV_LL {info['v_ll_rms_V']:.0f} V rms",
        "shaft_flow": None if info.get("Pshaft_W") is None else f"P_shaft {info['Pshaft_W'] / 1e3:.1f} kW",
        "motor": f"T {info['T_Nm']:.1f} N·m · n {info['speed_rpm']:.0f} rpm",
        "note": info.get("note"),
    }
    draw_powertrain(ax, relay_closed=True, bridge="pwm", power_flow=pf, labels=labels)


def fig_dclink_schematic(fig, scenario: str, info: dict, title: str | None = None):
    """scenario: 'discharge' (battery open, discharge resistor on) or 'overvoltage' (battery open while regenerating)."""
    fig.clear()
    fig.set_layout_engine("none")
    if title:
        fig.suptitle(title, fontsize=10, fontweight="bold", color=S.theme()["fg"])
    ax = new_axes(fig, XLIM, YLIM, rect=[0.01, 0.02, 0.98, 0.9])
    if scenario == "passive":
        rect = bool(info.get("rectifying"))
        draw_powertrain(ax, relay_closed=False, passive_on=True, discharge_on=bool(info.get("active_parallel")),
                        bridge="off", power_flow="rectify" if rect else None, rectifying=rect,
                        spinning=info.get("spinning", False),
                        labels={"cap": f"C {info['C_uF']:.0f} µF\n{info['V0_V']:.0f}→{info['Vf_V']:.0f} V",
                                "pas_r": "R_p" if info.get("R_ohm") is None else f"R_p {info['R_ohm'] / 1e3:.4g} kΩ",
                                "dc_flow": tr("역기전력 정류", "rectified back-EMF") if rect else None,
                                "motor": info.get("motor_label"), "note": info.get("note")})
    elif scenario == "discharge":
        rect = bool(info.get("rectifying"))
        draw_powertrain(ax, relay_closed=False, discharge_on=True, bridge="off",
                        power_flow="rectify" if rect else None, rectifying=rect, spinning=info.get("spinning", False),
                        labels={"cap": f"C {info['C_uF']:.0f} µF\n{info['V0_V']:.0f}→{info['Vf_V']:.0f} V",
                                "dis_r": "R_dis" if info.get("R_ohm") is None else f"R {info['R_ohm']:.4g} Ω",
                                "dc_flow": tr("역기전력 정류", "rectified back-EMF") if rect else None,
                                "motor": info.get("motor_label"), "note": info.get("note")})
    else:
        draw_powertrain(ax, relay_closed=False, bridge="pwm", power_flow="regen", spinning=True,
                        labels={"cap": f"C {info['C_uF']:.0f} µF\n≤ {info['V_limit_V']:.0f} V",
                                "dc_flow": "P_in" if info.get("P_in_W") is None else f"P_in {info['P_in_W'] / 1e3:.1f} kW",
                                "motor": info.get("motor_label"), "note": info.get("note")})


def fig_safe_state_schematic(fig, info: dict, title: str | None = None):
    """ASC (low-side switches on, windings shorted) next to freewheel (all off, diodes may rectify)."""
    fig.clear()
    fig.set_layout_engine("none")
    c = _c()
    if title:
        fig.suptitle(title, fontsize=10, fontweight="bold", color=c["fg"])
    for k, (mode, head) in enumerate((("asc_low", tr("ASC (능동 단락)", "ASC (active short circuit)")),
                                      ("off", tr("Freewheel / 6SO (모두 OFF)", "freewheel / 6SO (all off)")))):
        ax = new_axes(fig, (2.5, XLIM[1]), (-1.4, 5.3), rect=[0.005 + 0.5 * k, 0.03, 0.49, 0.86])
        rect = mode == "off" and info.get("rectifying", False)
        draw_powertrain(ax, bridge=mode, show_battery=False, rectifying=rect, show_discharge=False,
                        power_flow="rectify" if rect else None, spinning=True,
                        labels={"cap": f"Vdc {info['Vdc_V']:.0f} V",
                                "dc_flow": tr("정류 충전", "rectified charging") if rect else None,
                                "motor": info.get("asc_label" if mode == "asc_low" else "fw_label")})
        if mode == "asc_low":
            # circulating short-circuit current through the low-side switches and the windings
            flow(ax, (MX - 1.1, 1.1), (LEGS[0][0] + 0.45, 0.35), None, color=c["hot"], rad=-0.25)
            text(ax, 8.4, -0.95, info.get("asc_note", tr("권선이 단락되어 전류가 인버터 하단에서 순환 (DC 링크로 전력 없음)",
                                                          "windings shorted: current circulates in the low side (no DC power)")),
                 size=6.8, box=True)
        else:
            text(ax, 8.4, -0.95, info.get("fw_note", ""), size=6.8, box=True)
        text(ax, 8.4, 5.05, head, size=9, weight="bold")


# ---------------------------------------------------------------------------
# thermal
# ---------------------------------------------------------------------------

def _source(ax, x, y, kind, label, side="left"):
    c = _c()
    ax.add_patch(Circle((x, y), 0.3, fc=c["bg"], ec=c["fg"], lw=1.4, zorder=5))
    if kind == "current":
        ax.add_patch(FancyArrowPatch((x, y - 0.18), (x, y + 0.2), arrowstyle="-|>", mutation_scale=8, color=c["fg"],
                                     lw=1.2, zorder=6))
    else:
        text(ax, x, y + 0.1, "+", size=7, weight="bold")
        text(ax, x, y - 0.13, "−", size=7, weight="bold")
    if side == "left":
        text(ax, x - 0.4, y, label, ha="right", size=6.8, color=c["muted"])
    else:
        text(ax, x + 0.4, y, label, ha="left", size=6.8, color=c["muted"])


def draw_foster(ax, R, tau, y, name, ref_label, x0=0.0, width=10.0):
    c = _c()
    n = len(R)
    xs = [x0 + 1.2 + k * (width - 2.2) / n for k in range(n + 1)]
    _source(ax, x0 + 0.4, y - 0.6, "current", "P")
    wire(ax, (x0 + 0.4, y - 0.3), (x0 + 0.4, y), (xs[0], y))
    dot(ax, xs[0], y)
    text(ax, xs[0] - 0.1, y + 0.2, "T_j", ha="right", size=7, weight="bold")
    for k in range(n):
        a, b = xs[k], xs[k + 1]
        up, dn = y + 0.45, y - 0.45
        wire(ax, (a, up), (a, dn))
        wire(ax, (b, up), (b, dn))
        resistor(ax, (a, up), (b, up), None, body=min(0.9, 0.5 * (b - a)))
        capacitor(ax, (a, dn), (b, dn), None, plate=0.34, gap=0.12)
        cval = tau[k] / R[k] if R[k] > 0 else float("inf")
        text(ax, (a + b) / 2, up + 0.3, f"R{k + 1} {_num(R[k])} K/W", size=6.3)
        text(ax, (a + b) / 2, dn - 0.3, f"τ{k + 1} {_num(tau[k])} s · C {_num(cval)} J/K", size=6.0, color=c["muted"])
        dot(ax, b, y)
    xe = xs[-1]
    wire(ax, (xe, y), (xe + 0.5, y), (xe + 0.5, y - 0.3))
    _source(ax, xe + 0.5, y - 0.6, "temp", ref_label, side="right")
    wire(ax, (x0 + 0.4, y - 0.9), (x0 + 0.4, y - 1.2), (xe + 0.5, y - 1.2), (xe + 0.5, y - 0.9), color=c["muted"], lw=1.0)
    text(ax, x0 - 0.1, y + 1.25, name, ha="left", size=7.5, weight="bold")
    text(ax, (x0 + xe) / 2, y - 1.45, tr("Foster: 단 사이 절점은 물리 온도가 아님 (Z_th 곡선 맞춤)",
                                        "Foster: inner nodes are not physical temperatures (fit of Z_th)"),
         size=6.2, color=c["muted"])


def draw_cauer(ax, R, C, y, name, ref_label, x0=0.0, width=10.0):
    c = _c()
    n = len(R)
    xs = [x0 + 1.2 + k * (width - 2.2) / n for k in range(n + 1)]
    ygnd = y - 1.2
    _source(ax, x0 + 0.4, y - 0.6, "current", "P")
    wire(ax, (x0 + 0.4, y - 0.3), (x0 + 0.4, y), (xs[0], y))
    text(ax, xs[0] - 0.1, y + 0.25, "T_j", ha="right", size=7, weight="bold")
    for k in range(n):
        a, b = xs[k], xs[k + 1]
        dot(ax, a, y)
        capacitor(ax, (a, y), (a, ygnd), None, plate=0.34, gap=0.12)
        text(ax, a + 0.3, (y + ygnd) / 2 - 0.02, f"C{k + 1} {_num(C[k])} J/K", ha="left", size=6.0, color=c["muted"])
        resistor(ax, (a, y), (b, y), None, body=min(0.9, 0.45 * (b - a)))
        text(ax, (a + b) / 2, y + 0.3, f"R{k + 1} {_num(R[k])} K/W", size=6.3)
    xe = xs[-1]
    wire(ax, (xe, y), (xe + 0.5, y), (xe + 0.5, y - 0.3))
    _source(ax, xe + 0.5, y - 0.6, "temp", ref_label, side="right")
    wire(ax, (x0 + 0.4, y - 0.9), (x0 + 0.4, ygnd), (xe + 0.5, ygnd), (xe + 0.5, y - 0.9), color=c["muted"], lw=1.0)
    text(ax, x0 - 0.1, y + 1.0, name, ha="left", size=7.5, weight="bold")
    text(ax, (x0 + xe) / 2, ygnd - 0.25, tr("Cauer: 절점 = 층(칩·솔더·기판·냉각판…) 온도의 근사",
                                           "Cauer: nodes approximate layer temperatures (chip, solder, substrate, plate…)"),
         size=6.2, color=c["muted"])


def draw_coolant_loop(ax, info: dict, y=0.0, x0=0.0):
    """Radiator/pump -> stations in order -> back; temperatures and heat per station if available."""
    c = _c()
    st = info.get("stations") or []
    fluid = info.get("fluid") or {}
    n = max(1, len(st))
    xs = [x0 + 3.2 + k * 3.3 for k in range(n)]
    block(ax, x0 + 0.8, y, 1.6, 0.9, tr("라디에이터\n+ 펌프", "radiator\n+ pump"), size=6.8)
    prev = x0 + 1.6
    t_in = fluid.get("_loop", {}).get("T_inlet_C")
    for k, s in enumerate(st):
        name = s["name"]
        f = fluid.get(name, {})
        lab = {"inverter": tr("인버터 냉각판", "inverter cold plate"), "motor": tr("모터 워터재킷", "motor water jacket")}.get(name, name)
        heat = f"+{f['P_W'] / 1e3:.2f} kW" if f.get("P_W") is not None else "+P"
        block(ax, xs[k], y, 1.9, 0.9, f"{lab}\n{heat}", size=6.8)
        flow(ax, (prev + 0.05, y), (xs[k] - 0.98, y), None, color=c["on"], lw=1.8)
        tlabel = f"{f['T_in_C']:.1f} °C" if f.get("T_in_C") is not None else ("T_in" if k == 0 else "")
        text(ax, (prev + xs[k] - 0.95) / 2, y + 0.3, tlabel, size=6.5, color=c["on"])
        prev = xs[k] + 0.95
    x_end = prev + 1.0
    flow(ax, (prev + 0.05, y), (x_end, y), None, color=c["on"], lw=1.8)
    t_out = fluid.get("_loop", {}).get("T_outlet_C")
    text(ax, (prev + x_end) / 2, y + 0.3, f"{t_out:.1f} °C" if t_out is not None else "T_out", size=6.5, color=c["on"])
    wire(ax, (x_end, y), (x_end, y - 0.9), (x0 + 0.8, y - 0.9), (x0 + 0.8, y - 0.45), color=c["on"], lw=1.4)
    q = info.get("flow_L_per_min")
    parts = []
    if q is not None:
        parts.append(f"Q = {q:g} L/min")
    if info.get("glycol_vol_pct") is not None:
        parts.append(f"EG {info['glycol_vol_pct']:g} vol%")
    if info.get("cp_J_per_kgK"):
        parts.append(f"c_p {info['cp_J_per_kgK']:.0f} J/(kg·K) · ρ {info['rho_kg_per_m3']:.0f} kg/m³")
    if info.get("capacity_rate_W_per_K"):
        parts.append(f"ṁ·c_p = {info['capacity_rate_W_per_K']:.0f} W/K")
    if t_in is not None:
        parts.insert(0, f"T_in = {t_in:.1f} °C")
    text(ax, (x0 + x_end) / 2, y - 1.25, " · ".join(parts), size=6.8, box=True)
    return x_end


def fig_thermal_network(fig, nodes: list, coolant: dict | None, title: str | None = None):
    """nodes: [{'name', 'kind', 'R', 'tau'|'C', 'ref'}]; coolant: CoolantLoop.describe() + optional 'fluid'."""
    fig.clear()
    fig.set_layout_engine("none")
    if title:
        fig.suptitle(title, fontsize=10, fontweight="bold", color=S.theme()["fg"])
    rows = len(nodes) + (1 if coolant else 0)
    height = 3.3 * len(nodes) + (2.6 if coolant else 0) + 0.3
    ax = new_axes(fig, (-1.0, 13.2), (-height + 1.2, 1.9), rect=[0.01, 0.01, 0.98, 0.92])
    y = 0.4
    for nd in nodes:
        if nd["kind"] == "cauer":
            draw_cauer(ax, nd["R"], nd["C"], y, nd["name"], nd["ref"], width=11.0)
        else:
            draw_foster(ax, nd["R"], nd["tau"], y, nd["name"], nd["ref"], width=11.0)
        y -= 3.3
    if coolant:
        text(ax, -0.5, y + 0.95, tr("냉각수 순환 (순서대로 가열)", "coolant loop (heated in order)"), ha="left", size=7.5,
             weight="bold")
        draw_coolant_loop(ax, coolant, y=y + 0.1, x0=0.0)
    del rows


# ---------------------------------------------------------------------------
# open-end winding dual inverter and hybrid system (OEW/HEV addendum)
# ---------------------------------------------------------------------------

def coil(ax, p0, p1, label=None, color=None, bumps=4, lw=1.5):
    c = color or _c()["fg"]
    (x0, y), (x1, _) = p0, p1
    L = x1 - x0
    lead = 0.18 * L
    wire(ax, (x0, y), (x0 + lead, y), color=c, lw=lw)
    wire(ax, (x1 - lead, y), (x1, y), color=c, lw=lw)
    w = (L - 2 * lead) / bumps
    th = [math.pi * k / 16 for k in range(17)]
    for b in range(bumps):
        xs = [x0 + lead + b * w + w / 2 - (w / 2) * math.cos(t) for t in th]
        ys = [y + (w / 2) * math.sin(t) for t in th]
        ax.plot(xs, ys, color=c, lw=lw, zorder=3)
    if label:
        text(ax, (x0 + x1) / 2, y + w * 0.9, label, size=6.8, color=_c()["muted"])


def fig_oew_schematic(fig, topo: dict, title: str | None = None, flows: dict | None = None):
    """Two two-level bridges on the two ends of one winding; common bus or two isolated sources."""
    fig.clear()
    fig.set_layout_engine("none")
    if title:
        fig.suptitle(title, fontsize=10, fontweight="bold", color=S.theme()["fg"])
    c = _c()
    ax = new_axes(fig, (-1.2, 21.2), (-2.4, 6.6), rect=[0.01, 0.02, 0.98, 0.9])
    legsA = ((4.0, 2.55, "a"), (5.5, 2.0, "b"), (7.0, 1.45, "c"))
    legsB = ((14.0, 2.55, "a"), (15.5, 2.0, "b"), (17.0, 1.45, "c"))
    kind = topo.get("kind", "common_bus")
    VA, VB = topo.get("VA_V"), topo.get("VB_V")
    # sources and rails
    if kind == "common_bus":
        battery(ax, 0.3, BOT, TOP, f"V = {VA:g} V")
        wire(ax, (0.3, TOP), (legsB[-1][0], TOP))
        wire(ax, (0.3, BOT), (legsB[-1][0], BOT))
        capacitor(ax, (2.4, TOP), (2.4, BOT), "C", lab_off=(0.3, 0.0))
        capacitor(ax, (18.6, TOP), (18.6, BOT), None)
        wire(ax, (legsB[-1][0], TOP), (18.6, TOP))
        wire(ax, (legsB[-1][0], BOT), (18.6, BOT))
        for x in (2.4, 18.6):
            dot(ax, x, TOP)
            dot(ax, x, BOT)
        text(ax, 10.5, TOP + 0.35, tr("공통 + 레일 (δ = 0): 영상분 전류의 도전 귀환 경로",
                                      "common + rail (delta = 0): conductive return for zero-sequence current"),
             size=7, color=c["hot"])
        text(ax, 10.5, BOT - 0.35, tr("공통 − 레일", "common − rail"), size=7, color=c["hot"])
    else:
        battery(ax, 0.3, BOT, TOP, f"VA = {VA:g} V")
        wire(ax, (0.3, TOP), (legsA[-1][0], TOP))
        wire(ax, (0.3, BOT), (legsA[-1][0], BOT))
        capacitor(ax, (2.4, TOP), (2.4, BOT), "C_A", lab_off=(0.3, 0.0))
        battery(ax, 20.3, BOT, TOP, f"VB = {VB:g} V")
        wire(ax, (legsB[0][0], TOP), (20.3, TOP))
        wire(ax, (legsB[0][0], BOT), (20.3, BOT))
        capacitor(ax, (18.6, TOP), (18.6, BOT), "C_B", lab_off=(0.3, 0.0))
        for x in (2.4, 18.6):
            dot(ax, x, TOP)
            dot(ax, x, BOT)
        text(ax, 10.5, TOP + 0.35, tr("두 절연 섬: 저주파 귀환 없음 → ia+ib+ic = 0, 부유 δ",
                                      "two isolated islands: no LF return -> ia+ib+ic = 0, floating delta"),
             size=7, color="#8250df")
    for legs, name in ((legsA, "A"), (legsB, "B")):
        for (x, ym, ph) in legs:
            semi(ax, x, TOP, ym, 3.3, pwm=True)
            semi(ax, x, ym, BOT, 0.7, pwm=True)
            dot(ax, x, TOP)
            dot(ax, x, BOT)
            dot(ax, x, ym)
        text(ax, legs[1][0], TOP + 0.95, tr(f"브리지 {name}", f"bridge {name}"), size=8, weight="bold")
    for (xa, ym, ph), (xb, _, _) in zip(legsA, legsB):
        wire(ax, (xa, ym), (9.0, ym))
        coil(ax, (9.0, ym), (12.0, ym), None)
        wire(ax, (12.0, ym), (xb, ym))
        text(ax, 8.8, ym + 0.14, f"{ph}A", size=6.5, color=c["muted"], ha="right")
        text(ax, 12.2, ym + 0.14, f"{ph}B", size=6.5, color=c["muted"], ha="left")
    ax.add_patch(Rectangle((8.7, 0.95), 3.6, 2.2, fill=False, ec=c["muted"], ls="--", lw=1.0))
    text(ax, 10.5, 3.45, tr("한 3상 권선의 양끝 (6단자)", "one 3-phase winding, both ends (6 terminals)"), size=7)
    text(ax, 10.5, 0.55, "i: A → " + tr("권선", "winding") + " → B;  u_k = v_Ak − v_Bk", size=6.8, color=c["muted"])
    flow(ax, (9.2, 2.85), (11.8, 2.85), None, color=c["on"], lw=1.4)
    note = (tr("양 브리지 모두 권선 전류 전체를 운반 (반분하지 않음) · 전력 배분 ≠ 전류 분담",
               "both bridges carry the full winding current (never halved) · power split != current sharing"))
    text(ax, 10.5, -1.0, note, size=7, box=True)
    if flows:
        text(ax, legsA[1][0], -1.9, flows.get("A", ""), size=7, color=c["on"], weight="bold")
        text(ax, legsB[1][0], -1.9, flows.get("B", ""), size=7, color="#8250df", weight="bold")
        if flows.get("src"):
            text(ax, 10.5, -1.9, flows["src"], size=7, color=c["ok"], weight="bold")


def _ground(ax, x, y, color=None):
    col = color or _c()["fg"]
    wire(ax, (x, y), (x, y - 0.25), color=col, lw=1.3)
    for k, w in enumerate((0.34, 0.22, 0.1)):
        wire(ax, (x - w, y - 0.25 - 0.1 * k), (x + w, y - 0.25 - 0.1 * k), color=col, lw=1.3)


def fig_hev_schematic(fig, info: dict, title: str | None = None):
    """Declared connection graph of a two-machine hybrid on one DC bus (positions are shorthand only)."""
    fig.clear()
    fig.set_layout_engine("none")
    if title:
        fig.suptitle(title, fontsize=10, fontweight="bold", color=S.theme()["fg"])
    c = _c()
    ax = new_axes(fig, (-0.8, 17.8), (-1.6, 7.2), rect=[0.01, 0.02, 0.98, 0.9])
    ym, ye = 1.0, 4.8
    block(ax, 1.2, ym, 2.0, 1.1, tr("엔진", "engine"), fc="#fff1e5" if S.theme_name() == "light" else None)
    # clutch
    wire(ax, (2.2, ym), (3.0, ym), lw=3)
    ax.plot([3.0, 3.0], [ym - 0.45, ym + 0.45], color=c["fg"], lw=2.2)
    ax.plot([3.35, 3.35], [ym - 0.45, ym + 0.45], color=c["fg"], lw=2.2)
    text(ax, 3.18, ym - 0.75, tr("클러치 (실제 상태)", "clutch (actual state)"), size=6.3, color=c["muted"])
    wire(ax, (3.35, ym), (4.6, ym), lw=3)
    motor(ax, 5.4, ym, r=0.75, label="EM1")
    text(ax, 5.4, ym - 1.05, info.get("em1_pos", tr("P1 (크랭크축)", "P1 (crankshaft)")), size=6.5, color=c["muted"])
    wire(ax, (6.15, ym), (7.6, ym), lw=3)
    block(ax, 8.6, ym, 2.0, 1.1, tr("변속기", "transmission"))
    wire(ax, (9.6, ym), (10.85, ym), lw=3)
    motor(ax, 11.6, ym, r=0.75, label="EM2")
    text(ax, 11.6, ym - 1.05, info.get("em2_pos", tr("P2/P3 (변속기 측)", "P2/P3 (transmission side)")), size=6.5,
         color=c["muted"])
    wire(ax, (12.35, ym), (13.6, ym), lw=3)
    block(ax, 14.4, ym, 1.6, 1.0, tr("종감속", "final drive"), size=6.8)
    wire(ax, (15.2, ym), (16.2, ym), lw=3)
    ax.add_patch(Circle((16.8, ym), 0.6, fc=c["panel"], ec=c["fg"], lw=2))
    text(ax, 16.8, ym + 0.95, tr("바퀴", "wheels"), size=7)
    # electrical
    battery(ax, 0.4, ye - 0.9, ye + 0.9, tr("배터리", "battery"))
    wire(ax, (0.4, ye + 0.9), (1.4, ye + 0.9), (1.4, ye), (1.5, ye))
    _ground(ax, 0.4, ye - 0.9)
    block(ax, 2.4, ye, 1.8, 1.2, tr("부스트\n(선택)", "boost\n(optional)"), size=6.8)
    wire(ax, (3.3, ye), (13.2, ye), lw=2.6)
    text(ax, 8.6, ye + 0.35, tr("공통 DC bus (하나의 전원 한도, 한 번만 계상)", "common DC bus (one source limit, counted once)"),
         size=7, color=c["hot"])
    capacitor(ax, (8.6, ye), (8.6, ye - 1.3), "C_bus", lab_off=(0.3, 0.0))
    _ground(ax, 8.6, ye - 1.3)
    text(ax, 16.0, ye - 0.4, tr("단선도 (−극은 접지 기호)", "single-line (return shown as ground)"), size=6.3,
         color=c["muted"])
    for x, name, key in ((5.4, "INV1", "p1_W"), (11.6, "INV2", "p2_W")):
        block(ax, x, ye - 1.7, 1.5, 0.8, name, size=7)
        wire(ax, (x, ye), (x, ye - 1.3))
        wire(ax, (x, ye - 2.1), (x, ym + 0.75))
        p = info.get(key)
        if p is not None:
            col = c["on"] if p > 0 else c["flow"]
            if p > 0:
                flow(ax, (x + 0.35, ye - 0.1), (x + 0.35, ye - 1.2), f"{p / 1e3:+.1f} kW", color=col, lab_off=(0.85, 0.0))
            else:
                flow(ax, (x + 0.35, ye - 1.2), (x + 0.35, ye - 0.1), f"{p / 1e3:+.1f} kW", color=col, lab_off=(0.85, 0.0))
    pb = info.get("p_src_W")
    if pb is not None:
        text(ax, 2.4, ye + 1.05, tr(f"배터리 {pb / 1e3:+.1f} kW", f"battery {pb / 1e3:+.1f} kW"), size=7.5,
             color=c["ok"], weight="bold")
    text(ax, 8.6, -1.2, tr("P0–P4 위치 표기는 약칭일 뿐: 축·기어·클러치·DC 노드의 실제 접속 그래프가 입력입니다",
                           "P0-P4 labels are shorthand: the actual shaft / gear / clutch / DC-node graph is the input"),
         size=7, box=True)
