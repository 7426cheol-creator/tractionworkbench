"""Switched-leg plant: 3-phase 2-level bridge + PMSM / IPMSM (isolated star point) + DC link + battery branch.

Bridge.  Every leg is described by the pole voltage (w.r.t. the DC mid-point) it imposes for each sign of its phase
current: ``v_plus`` while current leaves the leg (i > 0: the upper switch or the LOWER diode carries it) and
``v_minus`` while it enters (i < 0: the lower switch or the UPPER diode).  A passive leg has v_plus <= v_minus, and
every leg state maps onto that pair:

* a switched leg (instantaneous model): upper on -> (+Vdc/2, +Vdc/2); lower on -> (-Vdc/2, -Vdc/2); both off (dead
  time, six-switch-off) -> (-Vdc/2, +Vdc/2), i.e. the diodes decide;
* the PWM-period average of a healthy leg with duty d and dead time t_dt:
  v_plus = (d - t_dt f_sw - 1/2) Vdc, v_minus = (d + t_dt f_sw - 1/2) Vdc (the dead-time error is the
  current-sign dependent part; without it the pair collapses to the averaged leg);
* an open upper switch or a lost upper gate supply: leaving current only through the lower diode (v_plus = -Vdc/2);
* a switch held on or shorted: both current signs see its rail;
* an open phase connection or a missing diode path: that direction is -/+inf (no finite voltage drives it).

At zero current a leg with v_plus < v_minus holds i = 0 (it floats) when the pole voltage that keeps di/dt = 0 lies in
[v_plus, v_minus] (ideal-diode complementarity, Filippov sliding); otherwise the current leaves zero in the direction
the rail drives.  The modes of all legs at zero current are decided together (at most 27 combinations).  This one
rule gives the open-circuit back-EMF, the onset of uncontrolled rectification (line-line EMF above Vdc), six-switch-off
rectification, the dead-time zero-current clamp, open-switch and open-phase operation and partial short circuits -
without assuming a symmetric steady state.

Machine: dq model with the parameters of the operating temperatures (the kernel's Rs(T), psi(T)), constant
inductances (psi_d = psi_PM + L_d i_d, psi_q = L_q i_q), amplitude-invariant Park / Clarke, isolated star point (no
zero sequence; the dq model represents unbalanced phase currents exactly because i_a + i_b + i_c = 0).  Speed held by
the load (vehicle inertia) or integrated with a declared inertia.

DC link: capacitor C; battery = Thevenin source (V_oc, R_bat) with an optional series inductance L_bat behind the
main contactor; a battery that does not accept charge blocks negative current (a series-diode behaviour, located as
an event); bleeder and switchable active-discharge resistors.  Opening the contactor interrupts the battery branch:
the inductive energy 1/2 L i^2 goes to the contactor arc (booked, not modelled).

The bridge is lossless (ideal switches and diodes); the energy ledger (battery terminal, capacitor, copper, air gap,
bleeder, bridge DC side, magnetic storage) is integrated with the states, so its residual measures the integration,
not the physics - the physics is checked against closed forms and an independent abc-frame formulation
(``reference.py``).

Outside this plant (the simulation stops there with the reason, never extrapolates): a leg short between the rails
(both switches shorted, or shoot-through without a desaturation turn-off), a PWM-switched leg with an open diode
(avalanche of the interrupted current).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

INF = math.inf
SQ3 = math.sqrt(3.0)
TWO_PI = 2.0 * math.pi
PHASE_SHIFT = (0.0, -TWO_PI / 3.0, TWO_PI / 3.0)
PHASES = ("a", "b", "c")
# state vector
ID, IQ, TH, WM, VDC, IBAT, E_BAT, E_CU, E_MECH, E_BLEED, E_INV = range(11)
N_STATE = 11


class OutOfModel(Exception):
    """The bridge reached a state this plant does not represent (the message says what data would be needed)."""


class ShootThrough(OutOfModel):
    """A shorted switch and its gated complement conduct together (a leg short between the rails)."""

    def __init__(self, leg: int, healthy: str):
        self.leg, self.healthy = leg, healthy
        super().__init__(f"shoot-through in leg {PHASES[leg]}: the {healthy} switch is gated while its complement "
                         f"is shorted (a DC-link short: loop inductance, device saturation current and the "
                         f"desaturation turn-off are needed)")


# --------------------------------------------------------------------------------------------------- bridge legs

@dataclass
class LegHealth:
    upper_switch: str = "ok"          # ok | open | short
    lower_switch: str = "ok"
    upper_diode: str = "ok"           # ok | open
    lower_diode: str = "ok"
    phase_open: bool = False          # the motor phase connection is open
    upper_gate: bool = True           # the upper gate can be driven (driver supply, driver enable, not desat-latched)
    lower_gate: bool = True

    def copy(self) -> "LegHealth":
        return LegHealth(**self.__dict__)


@dataclass
class LegCommand:
    kind: str = "pwm"                 # pwm (period average) | upper_on | lower_on | off
    duty: float = 0.5                 # upper-switch duty for pwm

    def label(self) -> str:
        return f"pwm {self.duty:.3f}" if self.kind == "pwm" else self.kind


def gate_fractions(cmd: LegCommand) -> tuple[float, float]:
    if cmd.kind == "pwm":
        d = min(1.0, max(0.0, cmd.duty))
        return d, 1.0 - d
    return {"upper_on": (1.0, 0.0), "lower_on": (0.0, 1.0)}.get(cmd.kind, (0.0, 0.0))


def leg_voltages(leg: int, cmd: LegCommand, h: LegHealth, vdc: float, dt_frac: float) -> tuple[float, float]:
    """(v_plus, v_minus) of one leg.  Raises ShootThrough / OutOfModel for states outside the plant."""
    half = 0.5 * vdc
    if h.phase_open:
        return -INF, INF
    up_able = h.upper_switch == "ok" and h.upper_gate
    lo_able = h.lower_switch == "ok" and h.lower_gate
    up_short, lo_short = h.upper_switch == "short", h.lower_switch == "short"
    up_frac, lo_frac = gate_fractions(cmd)
    if up_short and lo_short:
        raise OutOfModel(f"both switches of leg {PHASES[leg]} shorted: a DC-link short (loop inductance, device "
                         f"saturation current and the fuse / contactor behaviour are needed)")
    if up_short and lo_able and lo_frac > 0.0:
        raise ShootThrough(leg, "lower")
    if lo_short and up_able and up_frac > 0.0:
        raise ShootThrough(leg, "upper")
    if up_short:
        return half, half
    if lo_short:
        return -half, -half
    upd_ok, lod_ok = h.upper_diode == "ok", h.lower_diode == "ok"
    pwm = cmd.kind == "pwm" and 0.0 < up_frac < 1.0
    if pwm and not (upd_ok and lod_ok):
        raise OutOfModel(f"leg {PHASES[leg]} is PWM-switched with an open diode: the interrupted current's voltage "
                         f"spike (device avalanche) is not represented")
    t = dt_frac if pwm else 0.0
    if pwm and t == 0.0 and up_able and lo_able:
        v = (up_frac - 0.5) * vdc                         # the averaged ideal leg: one voltage for both signs
        return v, v
    fu = max(0.0, up_frac - t) if up_able else 0.0        # leaving current: upper switch, else the lower diode
    v_plus = half if fu >= 1.0 else ((fu - 0.5) * vdc if lod_ok else -INF)
    fl = max(0.0, lo_frac - t) if lo_able else 0.0        # entering current: lower switch, else the upper diode
    v_minus = -half if fl >= 1.0 else ((0.5 - fl) * vdc if upd_ok else INF)
    return v_plus, v_minus


def single_valued(pair, vdc) -> bool:
    lo, hi = pair
    return math.isfinite(lo) and math.isfinite(hi) and hi - lo <= 1e-12 * max(1.0, abs(vdc))


# --------------------------------------------------------------------------------------------------- parameters

@dataclass
class MachineParams:
    p: int
    psi: float
    Ld: float
    Lq: float
    Rs: float
    b_visc: float = 0.0               # rotational loss torque b w + c w|w| (opposes rotation)
    c_quad: float = 0.0
    J: float | None = None            # None: the speed is held by the load (vehicle inertia) over the horizon
    T_load: float = 0.0               # load torque opposing positive torque (dynamic speed only)
    basis: str = ""

    @classmethod
    def from_drive(cls, drive, scenario, J: float | None = None, T_load: float = 0.0) -> "MachineParams":
        """Parameters at the scenario's winding / magnet temperatures (the kernel's resolution of Rs(T), psi(T))."""
        from ...models.flux import ConstantFluxModel
        from ...physics import DriveKernel
        if not isinstance(drive.motor.flux, ConstantFluxModel):
            raise OutOfModel("the fault simulation needs the constant-parameter machine model: a flux map is not "
                             "dynamically qualified here (psi(i) states, the map inverse and its dynamic "
                             "qualification would be needed)")
        k = DriveKernel(drive, scenario)
        if not k.evaluable:
            raise OutOfModel("the drive model is not evaluable at this scenario: "
                             + "; ".join(i.message for i in k.issues))
        rot = drive.motor.rotational_loss
        return cls(p=drive.motor.pole_pairs, psi=float(k.psi), Ld=float(k.Ld), Lq=float(k.Lq), Rs=float(k.Rs),
                   b_visc=0.0 if rot is None else float(getattr(rot, "viscous_Nm_per_rad_s", 0.0) or 0.0),
                   c_quad=0.0 if rot is None else float(getattr(rot, "quadratic_Nm_per_rad2_s2", 0.0) or 0.0),
                   J=J, T_load=T_load,
                   basis=f"{drive.drive_id} rev {drive.revision}: constant-parameter dq model at the scenario "
                         f"temperatures (psi {float(k.psi):.4g} Wb, Ld {float(k.Ld) * 1e6:.4g} uH, "
                         f"Lq {float(k.Lq) * 1e6:.4g} uH, Rs {float(k.Rs) * 1e3:.4g} mohm)")


@dataclass
class DcParams:
    C: float
    V_oc: float
    R_bat: float
    L_bat: float | None = None        # series inductance of the battery branch (None / 0: algebraic branch)
    R_bleed: float | None = None      # passive discharge resistor across the link
    R_active: float | None = None     # active discharge resistor (switched)
    active_discharge_on: bool = False
    contactor_closed: bool = True
    charge_accepting: bool = True     # False: the battery takes no charging current (BMS / cell protection)

    @property
    def inductive(self) -> bool:
        return bool(self.L_bat) and self.L_bat > 0.0


def phase_currents(i_d, i_q, th):
    return (i_d * math.cos(th) - i_q * math.sin(th),
            i_d * math.cos(th + PHASE_SHIFT[1]) - i_q * math.sin(th + PHASE_SHIFT[1]),
            i_d * math.cos(th + PHASE_SHIFT[2]) - i_q * math.sin(th + PHASE_SHIFT[2]))


# --------------------------------------------------------------------------------------------------- the plant

class Plant:
    """Right-hand side, leg modes, located events and energy ledger of the switched plant."""

    def __init__(self, m: MachineParams, dc: DcParams, dt_frac: float = 0.0):
        self.m, self.dc, self.dt_frac = m, dc, dt_frac
        self.health = [LegHealth(), LegHealth(), LegHealth()]
        self.cmd = [LegCommand(), LegCommand(), LegCommand()]
        self.bat_blocked = False       # battery branch held at zero (charge acceptance lost, current reached zero)
        self.E_arc = 0.0               # inductive energy of interrupted battery current (contactor arc)
        # a current below this is zero for the mode decision (1e-6 of the machine's characteristic current psi / L_d)
        self.i_zero_A = 1e-6 * abs(m.psi) / m.Ld if m.Ld > 0 else 1e-9
        self.zeno = {"intervals": 0, "time_s": 0.0}     # time-stepped (event location suspended) intervals

    # -- electrical relations ---------------------------------------------------------------------------------
    def torque(self, i_d, i_q):
        m = self.m
        return 1.5 * m.p * (m.psi * i_q + (m.Ld - m.Lq) * i_d * i_q)

    def magnetic_energy(self, i_d, i_q):
        return 0.75 * (self.m.Ld * i_d * i_d + self.m.Lq * i_q * i_q)

    def leg_pairs(self, v_dc):
        return [leg_voltages(k, self.cmd[k], self.health[k], v_dc, self.dt_frac) for k in range(3)]

    def _derivs(self, th, w_e, i_d, i_q, va, vb, vc):
        m = self.m
        v_al = (2.0 * va - vb - vc) / 3.0
        v_be = (vb - vc) / SQ3
        c, s = math.cos(th), math.sin(th)
        v_d, v_q = v_al * c + v_be * s, -v_al * s + v_be * c
        did = (v_d - m.Rs * i_d + w_e * m.Lq * i_q) / m.Ld
        diq = (v_q - m.Rs * i_q - w_e * (m.Ld * i_d + m.psi)) / m.Lq
        dph = []
        for sh in PHASE_SHIFT:
            cc, ss = math.cos(th + sh), math.sin(th + sh)
            dph.append(did * cc - diq * ss - w_e * (i_d * ss + i_q * cc))
        return did, diq, dph

    def solve_poles(self, th, w_e, i_d, i_q, pairs, modes):
        """Pole voltages for the modes; floating legs get the voltage that keeps their current at zero.
        Returns (poles, ok, did, diq, di_phase); ok = every floating leg inside its interval."""
        zero = [k for k in range(3) if modes[k] == 0]
        poles = [0.0, 0.0, 0.0]
        for k in range(3):
            if modes[k] > 0:
                poles[k] = pairs[k][0]
            elif modes[k] < 0:
                poles[k] = pairs[k][1]
        if any(not math.isfinite(poles[k]) for k in range(3) if modes[k] != 0):
            return poles, False, 0.0, 0.0, None
        if not zero:
            did, diq, dph = self._derivs(th, w_e, i_d, i_q, *poles)
            return poles, True, did, diq, dph
        # the phase-current derivatives are affine in the pole voltages: probe the columns (exact for a linear map)
        base = list(poles)
        for k in zero:
            base[k] = 0.0
        _, _, d0 = self._derivs(th, w_e, i_d, i_q, *base)
        sens = {}
        for k in zero:
            pr = list(base)
            pr[k] = 1.0
            _, _, d1 = self._derivs(th, w_e, i_d, i_q, *pr)
            sens[k] = [d1[j] - d0[j] for j in range(3)]
        tol = 1e-6 * max(1.0, max((abs(v) for v in poles if math.isfinite(v)), default=1.0))
        if len(zero) == 1:
            k = zero[0]
            poles[k] = -d0[k] / sens[k][k]
            ok = pairs[k][0] - tol <= poles[k] <= pairs[k][1] + tol
        else:
            k1, k2 = zero[0], zero[1]
            a11, a12, a21, a22 = sens[k1][k1], sens[k2][k1], sens[k1][k2], sens[k2][k2]
            det = a11 * a22 - a12 * a21
            b1, b2 = -d0[k1], -d0[k2]
            v1 = (b1 * a22 - a12 * b2) / det
            v2 = (a11 * b2 - a21 * b1) / det
            if len(zero) == 2:
                poles[k1], poles[k2] = v1, v2
                ok = all(pairs[k][0] - tol <= poles[k] <= pairs[k][1] + tol for k in zero)
            else:
                # three floating legs: two constraints fix the line-to-line voltages, the common mode is free
                rel = [0.0, 0.0, 0.0]
                rel[k1], rel[k2] = v1, v2
                t_lo = max(pairs[k][0] - rel[k] for k in range(3))
                t_hi = min(pairs[k][1] - rel[k] for k in range(3))
                ok = t_lo <= t_hi + tol
                if math.isfinite(t_lo) and math.isfinite(t_hi):
                    t = 0.5 * (t_lo + t_hi)
                else:
                    t = t_lo if math.isfinite(t_lo) else (t_hi if math.isfinite(t_hi) else 0.0)
                poles = [rel[k] + t for k in range(3)]
        did, diq, dph = self._derivs(th, w_e, i_d, i_q, *poles)
        return poles, ok, did, diq, dph

    def decide_modes(self, x, zero_tol_A: float = 1e-9) -> list:
        """Leg modes consistent with the current signs and the ideal-diode complementarity at zero current."""
        i_d, i_q, th, w_m, v_dc = x[ID], x[IQ], x[TH], x[WM], x[VDC]
        w_e = self.m.p * w_m
        pairs = self.leg_pairs(v_dc)
        ia = phase_currents(i_d, i_q, th)
        tol = max(zero_tol_A * max(1.0, abs(i_d), abs(i_q)), self.i_zero_A)
        fixed, free = [0, 0, 0], []
        for k in range(3):
            if single_valued(pairs[k], v_dc):
                fixed[k] = 1 if ia[k] >= 0 else -1
            elif abs(ia[k]) > tol:
                fixed[k] = 1 if ia[k] > 0 else -1
            else:
                free.append(k)
        if not free:
            return fixed
        best = None
        for combo in itertools.product((0, 1, -1), repeat=len(free)):
            modes = list(fixed)
            for k, md in zip(free, combo):
                modes[k] = md
            poles, ok, _, _, dph = self.solve_poles(th, w_e, i_d, i_q, pairs, modes)
            if not ok or dph is None:
                continue
            if any((md > 0 and dph[k] <= 0.0) or (md < 0 and dph[k] >= 0.0) for k, md in zip(free, combo)):
                continue
            if 0 in combo and not self._stays_consistent(x, modes, free):
                continue
            nz = combo.count(0)
            if best is None or nz > best[0]:
                best = (nz, modes)
        if best is None:
            raise OutOfModel("no consistent leg conduction mode at a current zero")
        return best[1]

    def _stays_consistent(self, x, modes, free, dt: float = 1e-8) -> bool:
        """Look-ahead at a decision point: a floating leg whose pole voltage sits on a rail (inside only by the
        tolerance) is consistent only if it moves back inside - otherwise that diode must conduct.  Decides the tie
        between 'floating at the edge' and 'conducting from zero' by the direction of motion."""
        d = self.rhs(x, modes)
        xd = [a + dt * b for a, b in zip(x, d)]
        vals = self.mode_event_values(xd, modes)
        vdc = max(1.0, abs(x[VDC]))
        for k in free:
            if modes[k] == 0 and vals[k] is not None and vals[k] < -1e-9 * vdc:
                return False
        return True

    # -- battery branch -------------------------------------------------------------------------------------
    def battery_current(self, x):
        dc = self.dc
        if not dc.contactor_closed:
            return 0.0
        if dc.inductive:
            return 0.0 if self.bat_blocked else x[IBAT]
        i = (dc.V_oc - x[VDC]) / dc.R_bat
        return i if (dc.charge_accepting or i >= 0.0) else 0.0

    def _dibat(self, x):
        dc = self.dc
        if not (dc.inductive and dc.contactor_closed) or self.bat_blocked:
            return 0.0
        return (dc.V_oc - dc.R_bat * x[IBAT] - x[VDC]) / dc.L_bat

    def battery_event_value(self, x):
        """> 0 while the battery branch regime holds (inductive branch without charge acceptance only)."""
        dc = self.dc
        if not (dc.inductive and dc.contactor_closed) or dc.charge_accepting:
            return None
        return (dc.V_oc - x[VDC]) if self.bat_blocked else x[IBAT]

    def update_battery_mode(self, x):
        dc = self.dc
        if not (dc.inductive and dc.contactor_closed) or dc.charge_accepting:
            self.bat_blocked = False
            return x
        if not self.bat_blocked and x[IBAT] <= 0.0 and dc.V_oc - x[VDC] <= 0.0:
            self.bat_blocked = True
            x = list(x)
            x[IBAT] = 0.0
        elif self.bat_blocked and dc.V_oc - x[VDC] > 0.0:
            self.bat_blocked = False
        return x

    def open_contactor(self, x):
        """The contactor interrupts the battery branch; the inductive energy goes to the arc (booked)."""
        x = list(x)
        if self.dc.inductive:
            self.E_arc += 0.5 * self.dc.L_bat * x[IBAT] ** 2
            x[IBAT] = 0.0
        self.dc.contactor_closed = False
        return x

    def bleed_current(self, v_dc):
        dc = self.dc
        i = v_dc / dc.R_bleed if dc.R_bleed else 0.0
        if dc.active_discharge_on and dc.R_active:
            i += v_dc / dc.R_active
        return i

    # -- right-hand side --------------------------------------------------------------------------------------
    def rhs(self, x, modes):
        """d/dt of [i_d, i_q, theta_e, w_m, v_dc, i_bat, E_bat, E_cu, E_mech, E_bleed, E_inv]."""
        i_d, i_q, th, w_m, v_dc = x[ID], x[IQ], x[TH], x[WM], x[VDC]
        m, dc = self.m, self.dc
        w_e = m.p * w_m
        pairs = self.leg_pairs(v_dc)
        poles, _ok, did, diq, _ = self.solve_poles(th, w_e, i_d, i_q, pairs, modes)
        ia = phase_currents(i_d, i_q, th)
        i_dc = 0.0
        if v_dc > 0.0:
            for k in range(3):
                if modes[k] != 0 and math.isfinite(poles[k]):
                    i_dc += (poles[k] / v_dc + 0.5) * ia[k]
        i_bat = self.battery_current(x)
        i_bl = self.bleed_current(v_dc)
        dv = (i_bat - i_dc - i_bl) / dc.C
        T = self.torque(i_d, i_q)
        if m.J is None:
            dw = 0.0
        else:
            dw = (T - (m.b_visc * w_m + m.c_quad * w_m * abs(w_m)) - m.T_load) / m.J
        p_cu = 1.5 * m.Rs * (i_d * i_d + i_q * i_q)
        return [did, diq, w_e, dw, dv, self._dibat(x), v_dc * i_bat, p_cu, T * w_m, v_dc * i_bl, v_dc * i_dc]

    def dc_current(self, x, modes):
        """Current drawn by the bridge from the + rail (averaged over the PWM period in the averaged model)."""
        i_d, i_q, th, w_m, v_dc = x[ID], x[IQ], x[TH], x[WM], x[VDC]
        if v_dc <= 0:
            return 0.0
        pairs = self.leg_pairs(v_dc)
        poles, _ok, _, _, _ = self.solve_poles(th, self.m.p * w_m, i_d, i_q, pairs, modes)
        ia = phase_currents(i_d, i_q, th)
        return sum((poles[k] / v_dc + 0.5) * ia[k] for k in range(3) if modes[k] != 0 and math.isfinite(poles[k]))

    def poles(self, x, modes):
        pairs = self.leg_pairs(x[VDC])
        poles, _ok, _, _, _ = self.solve_poles(x[TH], self.m.p * x[WM], x[ID], x[IQ], pairs, modes)
        return poles

    # -- located events ---------------------------------------------------------------------------------------
    def mode_event_values(self, x, modes):
        """Per leg a value > 0 while its regime holds (None: the leg has one voltage for both signs); then the
        battery branch value (None when it has no regime change)."""
        i_d, i_q, th, w_m, v_dc = x[ID], x[IQ], x[TH], x[WM], x[VDC]
        pairs = self.leg_pairs(v_dc)
        ia = phase_currents(i_d, i_q, th)
        poles = None
        if 0 in modes:
            poles, _ok, _, _, _ = self.solve_poles(th, self.m.p * w_m, i_d, i_q, pairs, modes)
        out = []
        for k in range(3):
            if single_valued(pairs[k], v_dc):
                out.append(None)
            elif modes[k] != 0:
                out.append(modes[k] * ia[k])
            else:
                lo, hi = pairs[k]
                out.append(min(poles[k] - lo, hi - poles[k]))
        out.append(self.battery_event_value(x))
        return out


def rk4(plant: Plant, x, modes, h):
    k1 = plant.rhs(x, modes)
    x2 = [a + 0.5 * h * b for a, b in zip(x, k1)]
    k2 = plant.rhs(x2, modes)
    x3 = [a + 0.5 * h * b for a, b in zip(x, k2)]
    k3 = plant.rhs(x3, modes)
    x4 = [a + h * b for a, b in zip(x, k3)]
    k4 = plant.rhs(x4, modes)
    return [a + h / 6.0 * (b + 2.0 * c + 2.0 * d + e) for a, b, c, d, e in zip(x, k1, k2, k3, k4)]


def project_floating(x, modes):
    """Hold the floating legs' currents exactly at zero (removes the drift of the derivative-level constraint)."""
    zero = [k for k in range(3) if modes[k] == 0]
    if not zero:
        return x
    x = list(x)
    if len(zero) >= 2:
        x[ID] = x[IQ] = 0.0
        return x
    k = zero[0]
    c, s = math.cos(x[TH] + PHASE_SHIFT[k]), -math.sin(x[TH] + PHASE_SHIFT[k])
    ik = c * x[ID] + s * x[IQ]
    x[ID] -= ik * c
    x[IQ] -= ik * s
    return x


@dataclass
class ExternalEvent:
    """A continuous-time threshold crossing (a comparator, a desaturation detector, a BMS limit).  ``value`` stays
    > 0 until the event; ``on_fire(t, x)`` returns True when the integration must stop there."""
    name: str
    value: object
    on_fire: object
    armed: bool = True


ZENO_EVENTS, ZENO_WINDOW_S, ZENO_STEP_S, ZENO_SPAN_S = 20, 2e-6, 1e-6, 20e-6


def integrate(plant: Plant, x, modes, t0, t1, h_max, externals=(), t_tol=1e-9, on_step=None):
    """Advance from t0 to t1 with located mode changes and external events; ``on_step(t, x, modes)`` after every
    accepted step and at every located event.

    Zeno guard: where the complementarity solution slides on a conduction boundary (e.g. a rectifier whose DC link
    sits at the EMF peak), located mode events can pile up at vanishing intervals.  More than ZENO_EVENTS leg events
    within ZENO_WINDOW_S switch to time stepping (modes re-decided at every ZENO_STEP_S step, external events still
    located) for ZENO_SPAN_S; the intervals are counted in ``plant.zeno`` so the reduced precision is reported.
    Returns (x, modes, t_reached, stopped_by) - stopped_by is the external event that asked to stop, else None."""
    t = t0
    x = project_floating(x, modes)
    recent: list = []
    ts_until = -math.inf
    while t1 - t > 1e-15:
        if t < ts_until:
            h = min(ZENO_STEP_S, t1 - t, ts_until - t)
            xn = project_floating(rk4(plant, x, modes, h), modes)
            ext = [ev for ev in externals if ev.armed]
            g0 = [ev.value(t, x) for ev in ext]
            g1 = [ev.value(t + h, xn) for ev in ext]
            fired = [i for i in range(len(ext)) if g0[i] > 0.0 >= g1[i]]
            if fired:
                lo, hi = 0.0, h
                while hi - lo > t_tol:
                    mid = 0.5 * (lo + hi)
                    xm = rk4(plant, x, modes, mid)
                    if any(g0[i] > 0.0 >= ext[i].value(t + mid, xm) for i in fired):
                        hi = mid
                    else:
                        lo = mid
                xn = project_floating(rk4(plant, x, modes, hi), modes)
                h = hi
            plant.zeno["time_s"] += h
            x, t = xn, t + h
            x = plant.update_battery_mode(x)
            if on_step is not None:
                on_step(t, x, modes)
            stop = None
            for i in fired:
                ext[i].armed = False
                if ext[i].on_fire(t, x):
                    stop = ext[i]
            modes = plant.decide_modes(x)
            x = project_floating(x, modes)
            if stop is not None:
                return x, modes, t, stop
            continue
        h = min(h_max, t1 - t)
        xn = rk4(plant, x, modes, h)
        e0, e1 = plant.mode_event_values(x, modes), plant.mode_event_values(xn, modes)
        ext = [ev for ev in externals if ev.armed]
        g0 = [ev.value(t, x) for ev in ext]
        g1 = [ev.value(t + h, xn) for ev in ext]

        def hit_at(e_a, e_b, g_a, g_b):
            legs = [k for k in range(4) if e_a[k] is not None and e_b[k] is not None and e_a[k] > 0.0 >= e_b[k]]
            exts = [i for i in range(len(ext)) if g_a[i] > 0.0 >= g_b[i]]
            return legs, exts
        legs, exts = hit_at(e0, e1, g0, g1)
        if not legs and not exts:
            x, t = project_floating(xn, modes), t + h
            if on_step is not None:
                on_step(t, x, modes)
            continue
        lo, hi = 0.0, h
        while hi - lo > t_tol:
            mid = 0.5 * (lo + hi)
            xm = rk4(plant, x, modes, mid)
            lg, ex = hit_at(e0, plant.mode_event_values(xm, modes), g0,
                            [ev.value(t + mid, xm) for ev in ext])
            if lg or ex:
                hi = mid
            else:
                lo = mid
        x = rk4(plant, x, modes, hi)
        t += hi
        legs, exts = hit_at(e0, plant.mode_event_values(x, modes), g0, [ev.value(t, x) for ev in ext])
        for k in legs:
            if k < 3 and modes[k] != 0:       # a current reached zero: put it exactly there
                mm = [1, 1, 1]
                mm[k] = 0
                x = project_floating(x, mm)
        if any(k < 3 for k in legs):
            recent.append(t)
            while recent and recent[0] < t - ZENO_WINDOW_S:
                recent.pop(0)
            if len(recent) > ZENO_EVENTS:
                ts_until = t + ZENO_SPAN_S
                plant.zeno["intervals"] += 1
                recent.clear()
        if 3 in legs:
            x = plant.update_battery_mode(x)
        if on_step is not None:
            on_step(t, x, modes)
        stop = None
        for i in exts:
            ext[i].armed = False
            if ext[i].on_fire(t, x):
                stop = ext[i]
        modes = plant.decide_modes(x)
        x = project_floating(x, modes)
        if stop is not None:
            return x, modes, t, stop
    return x, modes, t1, None                  # reached t1 (accumulated step rounding is not a different instant)
