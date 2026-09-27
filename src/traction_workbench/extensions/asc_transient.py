"""ASC fault-current transient and the customer's two current-time requirements (independent review, 9.13).

One fault event, one trajectory, several questions evaluated on it:

* the balanced, symmetric active short circuit in the flux state
      d psi_d / dt = v_d - Rs i_d + w_e psi_q,     d psi_q / dt = v_q - Rs i_q - w_e psi_d,   v_d = v_q = 0
  from the pre-fault state (id, iq at the policy point, same angle for all phases, currents persist during the
  declared delay until the short is established).  Constant-parameter model: psi_d = psi_PM + Ld id,
  psi_q = Lq iq, solved exactly (matrix exponential); an independent Runge-Kutta integration is returned as a
  cross-check.  With an inertia J the speed is integrated too (braking torque slows the rotor).
* phase currents i_a,b,c(t) from the dq trajectory and the electrical angle; the worst initial angle is found by
  sampling (dq norm is not the phase peak).
* each customer requirement is an explicit operator on the waveform with its own quantity, window, time origin
  and limit (``abs_peak``, ``rms``, ``envelope_after``, ``time_above``); an incomplete definition is
  REQUIREMENT_INCOMPLETE.  I^2 t is not used as a survival criterion unless the supplier defines it so.
* motor demagnetisation and device survival are judged only against imported envelopes (minimum d-axis current
  at the magnet temperature, device peak / I^2 t / pulse limits with their conditions); without them UNKNOWN.

Claim level: the constant-parameter model is a SCREENING model for transients (saturation, cross-coupling and
temperature change are not represented), flux maps are not dynamically qualified (see the magnetic
qualification) - so the joint claim is at most SCREENING: its per-item verdicts are reported, the joint status
stays UNKNOWN.  A 6SO/freewheel interval before the short needs a rectifier model and is not native.  The normal
current limit is never used to clip fault currents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_trapezoid = getattr(np, "trapezoid", None) or np.trapz      # numpy < 2.0 compatibility
from scipy.integrate import solve_ivp
from scipy.linalg import expm

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..models.flux import ConstantFluxModel, _finite
from ..physics import DriveKernel
from ..scenario import DcSourceLimits, Scenario
from ..status import Claim, Evidence, EvidenceKind, Reason, Status

TWO_PI = 2.0 * math.pi
OPERATORS = ("abs_peak", "rms", "envelope_after", "time_above")
QUANTITIES = ("phase", "dq_norm")
ORIGINS = ("fault", "asc_established")


@dataclass(frozen=True)
class CurrentTimeRequirement:
    req_id: str
    quantity: str            # phase | dq_norm
    operator: str            # abs_peak | rms | envelope_after | time_above
    t_start_s: float
    t_end_s: float
    limit_A: float
    origin: str = "fault"    # time origin of the window
    level_A: float | None = None      # for time_above: the current level
    text: str = ""

    def missing(self) -> list[str]:
        out = []
        if self.quantity not in QUANTITIES:
            out.append(f"quantity one of {QUANTITIES}")
        if self.operator not in OPERATORS:
            out.append(f"operator one of {OPERATORS}")
        if self.origin not in ORIGINS:
            out.append(f"time origin one of {ORIGINS}")
        if not (math.isfinite(self.t_start_s) and math.isfinite(self.t_end_s)) or self.t_end_s <= self.t_start_s or \
                self.t_start_s < 0:
            out.append("window 0 <= t_start < t_end")
        if self.operator == "time_above" and self.level_A is None:
            out.append("level_A for time_above")
        if not math.isfinite(self.limit_A):
            out.append("limit")
        return out


def _linear_system(k: DriveKernel):
    psi, ld, lq, rs = k.psi, k.Ld, k.Lq, k.Rs
    return psi, ld, lq, rs


def transient_constant(k: DriveKernel, id0: float, iq0: float, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Exact solution at constant speed: x = x_ss + expm(A t)(x0 - x_ss), x = [psi_d, psi_q]."""
    psi, ld, lq, rs = _linear_system(k)
    w = k.omega_e
    A = np.array([[-rs / ld, w], [-w, -rs / lq]])
    b = np.array([rs * psi / ld, 0.0])
    x_ss = -np.linalg.solve(A, b)
    x0 = np.array([psi + ld * id0, lq * iq0])
    out = np.empty((t.size, 2))
    for n, tt in enumerate(t):
        out[n] = x_ss + expm(A * tt) @ (x0 - x_ss)
    idt = (out[:, 0] - psi) / ld
    iqt = out[:, 1] / lq
    return idt, iqt


def transient_integrated(k: DriveKernel, id0: float, iq0: float, t: np.ndarray, J_kgm2: float | None = None,
                         T_load_Nm: float = 0.0) -> dict:
    """Independent RK45 integration (optionally with the mechanical state)."""
    psi, ld, lq, rs = _linear_system(k)
    p = k.p
    w0 = k.omega_e

    def rhs(tt, y):
        psd, psq, we = y
        i_d = (psd - psi) / ld
        i_q = psq / lq
        dpsd = -rs * i_d + we * psq
        dpsq = -rs * i_q - we * psd
        if J_kgm2:
            tem = 1.5 * p * (psd * i_q - psq * i_d)
            dwe = p * (tem - k.tau_rot_or_zero - T_load_Nm) / J_kgm2 if we > 0 else 0.0
        else:
            dwe = 0.0
        return [dpsd, dpsq, dwe]

    sol = solve_ivp(rhs, (0.0, float(t[-1])), [psi + ld * id0, lq * iq0, w0], t_eval=t, rtol=1e-10, atol=1e-12,
                    method="RK45")
    if not sol.success:
        return {"success": False, "message": sol.message}
    psd, psq, we = sol.y
    return {"success": True, "id_A": (psd - psi) / ld, "iq_A": psq / lq, "omega_e": we}


def _phase_currents(idt, iqt, theta):
    ia = idt * np.cos(theta) - iqt * np.sin(theta)
    ib = idt * np.cos(theta - TWO_PI / 3) - iqt * np.sin(theta - TWO_PI / 3)
    ic = idt * np.cos(theta + TWO_PI / 3) - iqt * np.sin(theta + TWO_PI / 3)
    return np.vstack([ia, ib, ic])


def evaluate_requirement(req: CurrentTimeRequirement, t: np.ndarray, iph: np.ndarray, idq: np.ndarray,
                         t_asc: float) -> dict:
    miss = req.missing()
    if miss:
        return {"req_id": req.req_id, "status": "REQUIREMENT_INCOMPLETE", "missing": miss}
    off = 0.0 if req.origin == "fault" else t_asc
    if req.t_end_s + off > t[-1] * (1 + 1e-9):
        return {"req_id": req.req_id, "status": "REQUIREMENT_INCOMPLETE",
                "missing": [f"simulation horizon {t[-1]:.4g} s shorter than the window end {req.t_end_s + off:.4g} s"]}
    sel = (t >= req.t_start_s + off - 1e-15) & (t <= req.t_end_s + off + 1e-15)
    if np.count_nonzero(sel) < 2:
        return {"req_id": req.req_id, "status": "REQUIREMENT_INCOMPLETE", "missing": ["window not resolved"]}
    tw = t[sel]
    if req.quantity == "phase":
        mags = np.abs(iph[:, sel])
        worst_phase = int(np.argmax(mags.max(axis=1)))
        wave = mags[worst_phase]
    else:
        wave = idq[sel]
        worst_phase = None
    if req.operator == "abs_peak":
        k = int(np.argmax(wave))
        value, when = float(wave[k]), float(tw[k])
    elif req.operator == "rms":
        sq = (iph[:, sel] ** 2) if req.quantity == "phase" else (wave ** 2)[None, :]
        rms_each = np.sqrt(_trapezoid(sq, tw, axis=1) / (tw[-1] - tw[0]))
        worst_phase = int(np.argmax(rms_each)) if req.quantity == "phase" else None
        value, when = float(rms_each.max()), None
    elif req.operator == "envelope_after":
        value, when = float(wave.max()), float(tw[int(np.argmax(wave))])
    else:
        dt = np.diff(tw, prepend=tw[0])
        value, when = float(np.sum(dt[wave > req.level_A])), None
    margin = req.limit_A - value
    return {"req_id": req.req_id, "quantity": req.quantity, "operator": req.operator,
            "window_s": [req.t_start_s, req.t_end_s], "origin": req.origin, "value": value, "limit": req.limit_A,
            "margin": margin, "at_s": when, "worst_phase": None if worst_phase is None else "abc"[worst_phase],
            "screening_verdict": "PASS" if margin >= 0 else "FAIL",
            "unit": "s" if req.operator == "time_above" else "A"}


def asc_transient(drive: DriveModel, scenario: Scenario, id0_A: float, iq0_A: float, t_delay_s: float,
                  horizon_s: float, requirements: tuple = (), t_6so_s: float = 0.0, J_kgm2: float | None = None,
                  demag_id_min_A: float | None = None, demag_basis: str = "", device_peak_A: float | None = None,
                  device_i2t_A2s: float | None = None, device_basis: str = "", angles: int = 72,
                  n_time: int = 4000) -> dict:
    """Fault at t = 0, short established at t_delay; pre-fault currents persist during the delay."""
    k = DriveKernel(drive, scenario)
    td = _finite("t_delay_s", t_delay_s)
    H = _finite("horizon_s", horizon_s)
    if td < 0 or H <= td:
        raise InputValidationError("need 0 <= t_delay < horizon", field="t_delay_s")
    if _finite("t_6so_s", t_6so_s) < 0:
        raise InputValidationError("6SO interval must be >= 0", field="t_6so_s")
    base = {"model": k.kind, "t_delay_s": td, "horizon_s": H}
    if not k.evaluable or k.issues:
        return {**base, "evaluable": False, "claim": Claim(
            "asc_joint", Status.UNKNOWN, "ASC transient requirements", "fault transient",
            reasons=tuple(dict.fromkeys(i.reason for i in k.issues)) or (Reason.OUTSIDE_MODEL_DOMAIN,),
            detail="model not valid at this scenario: " + "; ".join(i.message for i in k.issues)).to_dict()}
    if not isinstance(drive.motor.flux, ConstantFluxModel):
        return {**base, "evaluable": False, "claim": Claim(
            "asc_joint", Status.UNKNOWN, "ASC transient requirements", "fault transient",
            reasons=(Reason.OUTSIDE_MODEL_DOMAIN,),
            detail="flux map: the bilinear interpolant is not dynamically qualified (no qualified inverse map / "
                   "differential inductance) - the transient is not evaluated; import a qualified fault-domain "
                   "model").to_dict()}
    t_asc = td + t_6so_s
    ts = np.linspace(0.0, H - t_asc, max(200, int(n_time)))
    idt, iqt = transient_constant(k, id0_A, iq0_A, ts)
    rk = transient_integrated(k, id0_A, iq0_A, ts, J_kgm2)
    cross = None
    if rk["success"]:
        cross = float(max(np.max(np.abs(rk["id_A"] - idt)), np.max(np.abs(rk["iq_A"] - iqt))))
        if J_kgm2:
            idt, iqt = rk["id_A"], rk["iq_A"]
    # pre-short interval: steady pre-fault currents
    pre_t = np.linspace(0.0, t_asc, 40, endpoint=False) if t_asc > 0 else np.array([])
    t = np.concatenate([pre_t, t_asc + ts])
    id_all = np.concatenate([np.full(pre_t.size, id0_A), idt])
    iq_all = np.concatenate([np.full(pre_t.size, iq0_A), iqt])
    we = k.omega_e
    omega = np.full(t.size, we)
    if J_kgm2 and rk["success"]:
        omega = np.concatenate([np.full(pre_t.size, we), rk["omega_e"]])
    theta_rel = np.concatenate([[0.0], np.cumsum(0.5 * (omega[1:] + omega[:-1]) * np.diff(t))])
    idq = np.hypot(id_all, iq_all)
    reqs = [r if isinstance(r, CurrentTimeRequirement) else CurrentTimeRequirement(**r) for r in requirements]
    per_req = {r.req_id: None for r in reqs}
    worst_angle = {}
    for a in (np.arange(max(1, angles)) * TWO_PI / max(1, angles)):
        iph = _phase_currents(id_all, iq_all, a + theta_rel)
        for r in reqs:
            ev = evaluate_requirement(r, t, iph, idq, t_asc)
            if ev.get("status") == "REQUIREMENT_INCOMPLETE":
                per_req[r.req_id] = ev
                continue
            cur = per_req[r.req_id]
            if cur is None or ev["value"] > cur["value"]:
                per_req[r.req_id] = ev
                worst_angle[r.req_id] = math.degrees(a)
    for rid, a in worst_angle.items():
        per_req[rid]["worst_initial_angle_deg"] = a
    tem = 1.5 * k.p * ((k.psi + (k.Ld - k.Lq) * id_all) * iq_all)
    items = {}
    # demagnetisation: imported safe domain only
    if demag_id_min_A is None:
        items["demagnetisation"] = {"status": "UNKNOWN", "detail": "no validated demagnetisation envelope imported "
                                    "(the declared id domain is not a demag safe-domain)"}
    else:
        m = float(np.min(id_all))
        items["demagnetisation"] = {"status": "SCREENING_PASS" if m >= demag_id_min_A else "SCREENING_FAIL",
                                    "min_id_A": m, "limit_A": demag_id_min_A, "basis": demag_basis or "not stated",
                                    "detail": "dq current against an imported single-threshold envelope (local "
                                              "field and history not represented)"}
    peak_phase = max((per_req[r]["value"] for r in per_req if per_req[r] and per_req[r].get("operator") == "abs_peak"
                      and per_req[r].get("quantity") == "phase"), default=None)
    iph_worst = _phase_currents(id_all, iq_all, theta_rel)
    i2t = float(np.max(_trapezoid(iph_worst ** 2, t, axis=1)))
    if device_peak_A is None and device_i2t_A2s is None:
        items["device_survival"] = {"status": "UNKNOWN", "detail": "no supplier pulse / SOA / I^2 t envelope for this "
                                    "waveform, voltage, temperature and gate condition (a short-circuit withstand "
                                    "time is not an ASC permission)", "phase_I2t_A2s_screening": i2t}
    else:
        ok = True
        det = []
        pk = float(np.max(np.abs(iph_worst)))
        if device_peak_A is not None:
            ok &= pk <= device_peak_A
            det.append(f"peak {pk:.4g} A vs {device_peak_A:g} A")
        if device_i2t_A2s is not None:
            ok &= i2t <= device_i2t_A2s
            det.append(f"I^2t {i2t:.4g} A^2s vs {device_i2t_A2s:g} A^2s")
        items["device_survival"] = {"status": "SCREENING_PASS" if ok else "SCREENING_FAIL", "detail": "; ".join(det),
                                    "basis": device_basis or "not stated"}
    if t_6so_s > 0:
        items["6so_interval"] = {"status": "UNKNOWN", "detail": f"{t_6so_s * 1e3:g} ms freewheel before the short: the "
                                 "diode-rectifier interval is not modelled natively; the trajectory assumes the "
                                 "pre-fault currents persist (not conservative in general)"}
    reqs_done = [v for v in per_req.values() if v is not None]
    incomplete = [v for v in reqs_done if v.get("status") == "REQUIREMENT_INCOMPLETE"]
    fails = [v for v in reqs_done if v.get("screening_verdict") == "FAIL"]
    level = "SCREENING (constant-parameter linear magnetic model; saturation, cross-coupling and temperature change "
    level += "not represented; sampled initial angle)"
    if incomplete or not reqs:
        joint_detail = "customer current-time requirements incomplete or not stated"
        reasons = (Reason.REQUIREMENT_INCOMPLETE,)
    elif fails:
        joint_detail = ("screening indicates the requirement(s) " + ", ".join(v["req_id"] for v in fails) +
                        " are exceeded - confirm with a qualified nonlinear fault-domain model before redesign")
        reasons = (Reason.OUTSIDE_MODEL_DOMAIN,)
    else:
        joint_detail = ("screening meets the stated current-time requirements; demagnetisation and device survival "
                        "need imported envelopes and a qualified transient model for a joint PASS")
        reasons = (Reason.OUTSIDE_MODEL_DOMAIN,)
    step = max(1, t.size // 3000)
    iph0 = _phase_currents(id_all, iq_all, theta_rel + math.radians(next(iter(worst_angle.values()), 0.0)))
    return {**base, "evaluable": True, "claim_level": level,
            "pre_fault": {"id_A": id0_A, "iq_A": iq0_A, "speed_rpm": k.speed_rpm, "omega_e_rad_s": we},
            "steady_asc": {"id_A": float(idt[-1]), "iq_A": float(iqt[-1])},
            "peak_dq_A": float(np.max(idq)), "min_id_A": float(np.min(id_all)),
            "peak_torque_Nm": float(np.min(tem)) if abs(np.min(tem)) > abs(np.max(tem)) else float(np.max(tem)),
            "rk_cross_check_A": cross, "requirements": per_req, "items": items,
            "claim": Claim("asc_joint", Status.UNKNOWN, "ASC current-time requirements + demag + device survival",
                           level, reasons=reasons,
                           evidence=(Evidence.make(EvidenceKind.DIRECT_EVALUATION,
                                                   f"exact linear transient; RK cross-check {cross:.2e} A"
                                                   if cross is not None else "exact linear transient"),),
                           detail=joint_detail).to_dict(),
            "waveform": {"t_s": t[::step].tolist(), "id_A": id_all[::step].tolist(), "iq_A": iq_all[::step].tolist(),
                         "ia_A": iph0[0][::step].tolist(), "ib_A": iph0[1][::step].tolist(),
                         "ic_A": iph0[2][::step].tolist(), "t_asc_s": t_asc},
            "not_modelled": ["saturation / cross-coupling in the fault domain", "device drops and diode/channel "
                             "commutation", "phase skew of the short", "gate-driver UVLO / OC priority over ASC",
                             "magnet temperature change during the event"]}
