#!/usr/bin/env python3
"""Independent re-derivation of the v1.0 reference fixtures.

This script deliberately does NOT import the ``traction_workbench`` package.
It re-computes the fixture values with methods that differ from the
production solver:

* forward cases: direct substitution of the fixture equations;
* inverse cases: a *current-angle* parametrisation (id = I cos(phi),
  iq = I sin(phi)); the torque equation is solved for the current magnitude
  I(phi) and I^2 is minimised over phi.  Production eliminates iq and works
  on the id line instead;
* capability cases: KKT multipliers are recomputed from gradients and the
  concave-Lagrangian upper bound is evaluated in closed form;
* manufactured flux map: nodes are compared with the analytic potential,
  derivatives with the analytic gradient and with central differences.

The expected values in the JSON files are never modified.  The script exits
with status 1 if any check fails.  Run: ``python verification/independent_fixture_check.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import brentq, minimize_scalar

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC_DIR = ROOT / "reference" / "traction_workbench_spec_v1"

# Acceptance numbers from 02_Implementation_Handoff_KO.md, H11.
ALGEBRA_TOL = 1e-10          # normalised direct-substitution discrepancy
GOLDEN_CURRENT_TOL_A = 1e-3
GOLDEN_VOLTAGE_TOL_V = 1e-3
GOLDEN_POWER_TOL_W = 0.5


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "", **values) -> bool:
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail, **values})
        return ok

    @property
    def failures(self) -> list[dict]:
        return [r for r in self.rows if not r["ok"]]


# ---------------------------------------------------------------------------
# Plain fixture equations (constant-parameter synthetic drive)
# ---------------------------------------------------------------------------

def drive_params(drive: dict, overrides: dict | None = None) -> dict:
    p = dict(drive["parameters"])
    if overrides:
        p.update(overrides)
    dom = drive["declared_operating_domain"]
    lim = drive["source_limits"]
    return {
        "p": int(p["pole_pairs"]),
        "Rs": float(p["Rs_phase_ohm"]),
        "psi": float(p["psi_pm_Wb"]),
        "Ld": float(p["Ld_H"]),
        "Lq": float(p["Lq_H"]),
        "b": float(p["drag_coefficient_Nm_per_rad_s"]),
        "a0": float(p["inverter_loss_offset_W"]),
        "a2": float(p["inverter_loss_Ipk2_coefficient_ohm"]),
        "rv": float(p["voltage_reserve_fraction"]),
        "id_lo": float(dom["id_A_peak"][0]),
        "id_hi": float(dom["id_A_peak"][1]),
        "iq_lo": float(dom["iq_A_peak"][0]),
        "iq_hi": float(dom["iq_A_peak"][1]),
        "Imax": float(dom["I_peak_max_A"]),
        "Pdis": float(lim["discharge_power_max_W"]),
        "Pchg": float(lim["charge_power_max_W"]),
        "Idis": float(lim["discharge_average_current_max_A"]),
        "Ichg": float(lim["charge_average_current_max_A"]),
    }


def point(P: dict, id_: float, iq: float, n_rpm: float, Vdc: float) -> dict:
    wm = 2.0 * math.pi * n_rpm / 60.0
    we = P["p"] * wm
    psid = P["psi"] + P["Ld"] * id_
    psiq = P["Lq"] * iq
    vd = P["Rs"] * id_ - we * psiq
    vq = P["Rs"] * iq + we * psid
    Te = 1.5 * P["p"] * (psid * iq - psiq * id_)
    Tsh = Te - P["b"] * wm
    I2 = id_ * id_ + iq * iq
    Pcu = 1.5 * P["Rs"] * I2
    Prot = P["b"] * wm * wm
    Pac = 1.5 * (vd * id_ + vq * iq)
    Pinv = P["a0"] + P["a2"] * I2
    Pdc = Pac + Pinv
    vpk = math.hypot(vd, vq)
    vb = (1.0 - P["rv"]) * Vdc / math.sqrt(3.0)
    return {
        "n_rpm": n_rpm, "id_A_peak": id_, "iq_A_peak": iq,
        "i_peak_A": math.sqrt(I2), "i_phase_rms_A": math.sqrt(I2) / math.sqrt(2.0),
        "vd_V_peak": vd, "vq_V_peak": vq, "v_peak_V": vpk,
        "Te_Nm": Te, "Tshaft_Nm": Tsh, "Pshaft_W": Tsh * wm,
        "Pcu_W": Pcu, "Prot_W": Prot, "Pac_W": Pac, "Pinv_W": Pinv,
        "Pdc_W": Pdc, "Idc_A": Pdc / Vdc, "Vmargin_V": vb - vpk,
        "_wm": wm, "_we": we, "_vb": vb, "_I2": I2,
        "_res_pac_tem": Pac - (Te * wm + Pcu),
        "_res_pac_shaft": Pac - (Tsh * wm + Pcu + Prot),
    }


def violations(P: dict, pt: dict, Vdc: float, rel_tol: float = 1e-9) -> list[str]:
    out = []
    if pt["v_peak_V"] > pt["_vb"] * (1 + rel_tol):
        out.append("VOLTAGE")
    if pt["i_peak_A"] > P["Imax"] * (1 + rel_tol):
        out.append("CURRENT")
    dis = min(P["Pdis"], Vdc * P["Idis"])
    chg = min(P["Pchg"], Vdc * P["Ichg"])
    if pt["Pdc_W"] > dis * (1 + rel_tol):
        out.append("DISCHARGE_SOURCE")
    if pt["Pdc_W"] < -chg * (1 + rel_tol):
        out.append("CHARGE_SOURCE")
    if not (P["id_lo"] - 1e-9 <= pt["id_A_peak"] <= P["id_hi"] + 1e-9):
        out.append("ID_DOMAIN")
    if not (P["iq_lo"] - 1e-9 <= pt["iq_A_peak"] <= P["iq_hi"] + 1e-9):
        out.append("IQ_DOMAIN")
    return out


def electrical_ok(P: dict, pt: dict, rel_tol: float = 1e-9) -> bool:
    return (
        pt["v_peak_V"] <= pt["_vb"] * (1 + rel_tol)
        and pt["i_peak_A"] <= P["Imax"] * (1 + rel_tol)
        and P["id_lo"] - 1e-7 <= pt["id_A_peak"] <= P["id_hi"] + 1e-7
        and P["iq_lo"] - 1e-7 <= pt["iq_A_peak"] <= P["iq_hi"] + 1e-7
    )


# ---------------------------------------------------------------------------
# Independent inverse: current-angle parametrisation
# ---------------------------------------------------------------------------

def magnitude_for_angle(P: dict, phi: float, c: float) -> float:
    """Smallest positive current magnitude I with Tem(I, phi) = 1.5 p c, or nan."""
    s, co = math.sin(phi), math.cos(phi)
    A = (P["Ld"] - P["Lq"]) * s * co
    B = P["psi"] * s
    if abs(A) < 1e-18:
        if abs(B) < 1e-18:
            return float("nan")
        I = c / B
        return I if I > 0 else float("nan")
    disc = B * B + 4.0 * A * c
    if disc < 0:
        return float("nan")
    r = math.sqrt(disc)
    cands = [(-B + r) / (2 * A), (-B - r) / (2 * A)]
    cands = [x for x in cands if x > 0]
    return min(cands) if cands else float("nan")


def angle_min_current(P: dict, n_rpm: float, T_req: float, Vdc: float, samples: int = 400001):
    """Minimum-I^2 solution by a dense angle scan plus local refinement."""
    wm = 2.0 * math.pi * n_rpm / 60.0
    c = (T_req + P["b"] * wm) / (1.5 * P["p"])
    if c > 0:
        lo, hi = math.pi / 2.0, math.pi
    elif c < 0:
        lo, hi = -math.pi, -math.pi / 2.0
    else:
        raise ValueError("zero electromagnetic torque not used by the fixtures")
    eps = 1e-12
    phis = np.linspace(lo + (0 if c > 0 else eps), hi - (eps if c > 0 else 0), samples)

    def state(phi):
        I = magnitude_for_angle(P, phi, c)
        if not math.isfinite(I):
            return None
        return point(P, I * math.cos(phi), I * math.sin(phi), n_rpm, Vdc)

    def slack(pt):
        # normalised slacks, positive = satisfied
        return min(
            1.0 - pt["v_peak_V"] / pt["_vb"],
            1.0 - pt["i_peak_A"] / P["Imax"],
            (pt["id_A_peak"] - P["id_lo"]) / P["Imax"],
            (P["id_hi"] - pt["id_A_peak"]) / P["Imax"],
            (pt["iq_A_peak"] - P["iq_lo"]) / P["Imax"],
            (P["iq_hi"] - pt["iq_A_peak"]) / P["Imax"],
        )

    def slack_phi(phi):
        pt = state(phi)
        return -1.0 if pt is None else slack(pt)

    feas = np.zeros(samples, dtype=bool)
    I2 = np.full(samples, np.inf)
    for k, phi in enumerate(phis):
        pt = state(phi)
        if pt is not None and slack(pt) >= 0:
            feas[k] = True
            I2[k] = pt["_I2"]
    if not feas.any():
        return None, []

    candidates = []
    idx = np.flatnonzero(feas)
    # feasible runs
    runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
    intervals = []
    for run in runs:
        a, b = run[0], run[-1]
        # refine run ends against the neighbouring infeasible sample
        ends = []
        for k_in, k_out in ((a, a - 1), (b, b + 1)):
            if 0 <= k_out < samples:
                root = brentq(slack_phi, phis[k_in], phis[k_out], xtol=1e-15, rtol=1e-15)
                # step back to the feasible side if rounding lands outside
                ends.append(root)
            else:
                ends.append(phis[k_in])
        intervals.append((min(ends), max(ends)))
        candidates.extend(ends)
        # interior minima of I^2 over the run
        seg = I2[run]
        for j in range(len(run)):
            left = seg[j - 1] if j > 0 else np.inf
            right = seg[j + 1] if j + 1 < len(run) else np.inf
            if seg[j] <= left and seg[j] <= right:
                k = run[j]
                a_phi = phis[max(k - 1, 0)]
                b_phi = phis[min(k + 1, samples - 1)]
                # An interior minimum of |i| on the torque curve is a tangency
                # point: id*dT/diq - iq*dT/did = 0 (the MTPA condition).  Solve
                # it directly because I^2 is flat near its minimum; fall back to
                # a bounded scalar minimisation if no sign change is bracketed.
                def tangency(f):
                    pt = state(f)
                    d, q = pt["id_A_peak"], pt["iq_A_peak"]
                    return d * P["psi"] + (P["Ld"] - P["Lq"]) * (d * d - q * q)
                lo_t = phis[max(k - 2, 0)]
                hi_t = phis[min(k + 2, samples - 1)]
                try:
                    bracketed = tangency(lo_t) * tangency(hi_t) < 0
                except TypeError:
                    bracketed = False
                if bracketed:
                    candidates.append(brentq(tangency, lo_t, hi_t, xtol=1e-16, rtol=1e-15))
                else:
                    res = minimize_scalar(
                        lambda f: state(f)["_I2"] if state(f) is not None else np.inf,
                        bounds=(a_phi, b_phi), method="bounded",
                        options={"xatol": 1e-14, "maxiter": 500},
                    )
                    candidates.append(res.x)
    best = None
    for phi in candidates:
        pt = state(phi)
        if pt is None:
            continue
        if slack(pt) < -1e-9:
            continue
        if best is None or pt["_I2"] < best["_I2"] - 1e-9:
            best = pt
    return best, intervals


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_manifest(spec: Path, rep: Report) -> None:
    manifest = json.loads((spec / "manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        data = (spec / f["name"]).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        rep.check(f"manifest:{f['name']}", digest == f["sha256"] and len(data) == f["size_bytes"],
                  "sha256/size match" if digest == f["sha256"] else f"sha256 {digest}")


def check_forward(spec: Path, drive: dict, rep: Report) -> float:
    fx = json.loads((spec / "golden_forward.json").read_text(encoding="utf-8"))
    worst_identity = 0.0
    for case in fx["cases"]:
        P = drive_params(drive, case.get("parameter_overrides"))
        inp = case["input"]
        pt = point(P, inp["id_A_peak"], inp["iq_A_peak"], inp["n_rpm"], inp["Vdc_V"])
        worst = 0.0
        for key, exp in case["expected"].items():
            scale = max(1.0, abs(exp))
            worst = max(worst, abs(pt[key] - exp) / scale)
        rep.check(f"forward:{case['case_id']}:values", worst <= ALGEBRA_TOL,
                  f"max normalised discrepancy {worst:.3e}")
        e = case["expected"]
        res = max(
            abs(e["Pac_W"] - (e["Te_Nm"] * pt["_wm"] + e["Pcu_W"])),
            abs(e["Pac_W"] - (e["Pshaft_W"] + e["Pcu_W"] + e["Prot_W"])),
            abs(e["Pdc_W"] - (e["Pac_W"] + e["Pinv_W"])),
        )
        worst_identity = max(worst_identity, res)
        scale = max(abs(e["Pdc_W"]), abs(e["Pac_W"]), 1.0)
        rep.check(f"forward:{case['case_id']}:power_identity", res <= max(0.01, 1e-9 * scale),
                  f"residual {res:.3e} W")
        got = sorted(violations(P, pt, inp["Vdc_V"]))
        rep.check(f"forward:{case['case_id']}:violations", got == sorted(case["expected_violations"]),
                  f"computed {got}")
    rep.check("forward:max_identity_residual_vs_manifest", worst_identity <= 1e-9,
              f"{worst_identity:.3e} W (manifest reports 2.91e-11 W)")
    return worst_identity


def compare_solution(rep: Report, tag: str, got: dict, exp: dict) -> None:
    di = abs(got["id_A_peak"] - exp["id_A_peak"])
    dq = abs(got["iq_A_peak"] - exp["iq_A_peak"])
    dv = max(abs(got[k] - exp[k]) for k in ("vd_V_peak", "vq_V_peak", "v_peak_V"))
    dp = max(abs(got[k] - exp[k]) for k in ("Pshaft_W", "Pcu_W", "Prot_W", "Pac_W", "Pinv_W", "Pdc_W"))
    ok = di <= GOLDEN_CURRENT_TOL_A and dq <= GOLDEN_CURRENT_TOL_A and dv <= GOLDEN_VOLTAGE_TOL_V and dp <= GOLDEN_POWER_TOL_W
    rep.check(tag, ok, f"|did|={di:.2e} A |diq|={dq:.2e} A |dV|={dv:.2e} V |dP|={dp:.2e} W")


def check_inverse(spec: Path, drive: dict, rep: Report) -> None:
    fx = json.loads((spec / "golden_inverse.json").read_text(encoding="utf-8"))
    P = drive_params(drive)
    for case in fx["cases"]:
        inp = case["input"]
        best, intervals = angle_min_current(P, inp["n_rpm"], inp["Tshaft_requested_Nm"], inp["Vdc_V"])
        exp = case["expected_policy_solution"]
        cid = case["case_id"]
        if exp is None:
            rep.check(f"inverse:{cid}:no_solution", best is None,
                      "angle scan found no electrically feasible point" if best is None else "unexpected solution")
            rep.check(f"inverse:{cid}:existence_label", case["electrical_existence"] == "INFEASIBLE", "")
            continue
        if best is None:
            rep.check(f"inverse:{cid}:solution", False, "angle scan found no solution")
            continue
        compare_solution(rep, f"inverse:{cid}:golden", best, exp)
        # the golden point itself must satisfy torque and hard constraints
        g = point(P, exp["id_A_peak"], exp["iq_A_peak"], inp["n_rpm"], inp["Vdc_V"])
        tres = abs(g["Tshaft_Nm"] - inp["Tshaft_requested_Nm"])
        rep.check(f"inverse:{cid}:golden_torque_residual", tres <= max(1e-3, 1e-6 * abs(inp["Tshaft_requested_Nm"])),
                  f"{tres:.3e} N*m")
        hard = max(g["v_peak_V"] / g["_vb"] - 1.0, g["i_peak_A"] / P["Imax"] - 1.0)
        rep.check(f"inverse:{cid}:golden_hard_violation", hard <= 1e-7, f"normalised {hard:.3e}")
        dis = min(P["Pdis"], inp["Vdc_V"] * P["Idis"])
        chg = min(P["Pchg"], inp["Vdc_V"] * P["Ichg"])
        dc_ok = (-chg <= g["Pdc_W"] <= dis) and (-P["Ichg"] <= g["Idc_A"] <= P["Idis"])
        rep.check(f"inverse:{cid}:dc_ok_label", dc_ok == exp["dc_ok"], f"computed dc_ok={dc_ok}")
        want_policy = "FEASIBLE" if dc_ok else "INFEASIBLE"
        rep.check(f"inverse:{cid}:policy_label", want_policy == case["minimum_current_policy_with_dc"], "")
        # feasible id intervals (spot check at interior points and just outside)
        for lo, hi in case["reference_feasible_id_intervals_A"]:
            wm = 2 * math.pi * inp["n_rpm"] / 60
            k_of = lambda d: P["psi"] + (P["Ld"] - P["Lq"]) * d
            a = (inp["Tshaft_requested_Nm"] + P["b"] * wm) / (1.5 * P["p"])
            for d in np.linspace(lo, hi, 7)[1:-1]:
                q = a / k_of(d)
                ok = electrical_ok(P, point(P, d, q, inp["n_rpm"], inp["Vdc_V"]))
                if not ok:
                    rep.check(f"inverse:{cid}:interval_interior", False, f"id={d} infeasible")
                    break
            else:
                rep.check(f"inverse:{cid}:interval_interior", True, f"[{lo:.6f}, {hi:.6f}] interior feasible")
            if hi < P["id_hi"] - 1e-6:
                d = hi + 1e-3
                q = a / k_of(d)
                rep.check(f"inverse:{cid}:interval_edge", not electrical_ok(P, point(P, d, q, inp["n_rpm"], inp["Vdc_V"])),
                          "point just above the interval is infeasible")
        if case.get("independent_infeasibility_bound") and cid.startswith("I06"):
            b = case["independent_infeasibility_bound"]
            wm = 2 * math.pi * inp["n_rpm"] / 60
            pshaft = inp["Tshaft_requested_Nm"] * wm
            rep.check(f"inverse:{cid}:shaft_power_bound",
                      abs(pshaft - b["required_shaft_power_W"]) <= 1e-6 and pshaft > b["dc_discharge_cap_W"],
                      f"Pshaft={pshaft:.6f} W > {b['dc_discharge_cap_W']} W with zero loss")
        if case.get("independent_infeasibility_bound") and cid.startswith("I07"):
            b = case["independent_infeasibility_bound"]
            wm = 2 * math.pi * inp["n_rpm"] / 60
            pshaft = inp["Tshaft_requested_Nm"] * wm
            loss_max = P["b"] * wm ** 2 + (1.5 * P["Rs"] + P["a2"]) * P["Imax"] ** 2 + P["a0"]
            pmax = pshaft + loss_max
            rep.check(f"inverse:{cid}:max_loss_bound",
                      abs(pmax - b["maximum_possible_Pdc_W"]) <= 1e-6 and pmax < b["required_Pdc_lower_bound_W"],
                      f"max Pdc={pmax:.6f} W < {b['required_Pdc_lower_bound_W']} W")
    # I03 explicit necessary condition
    i03 = next(c for c in fx["cases"] if c["case_id"].startswith("I03"))
    b = i03["independent_infeasibility_bound"]
    inp = i03["input"]
    wm = 2 * math.pi * inp["n_rpm"] / 60
    we = P["p"] * wm
    Te = inp["Tshaft_requested_Nm"] + P["b"] * wm
    kmax = P["psi"] + (P["Ld"] - P["Lq"]) * P["id_lo"]
    iq_min = Te / (1.5 * P["p"] * kmax)
    vd_lb = we * P["Lq"] * iq_min
    vb = (1 - P["rv"]) * inp["Vdc_V"] / math.sqrt(3)
    rep.check("inverse:I03:necessary_bound",
              abs(iq_min - b["iq_min_A"]) < 1e-9 and abs(vd_lb - b["required_abs_vd_lower_bound_V"]) < 1e-9
              and abs(vb - b["allowed_voltage_peak_V"]) < 1e-9 and vd_lb > vb,
              f"iq_min={iq_min:.10f} A, |vd|>={vd_lb:.10f} V > budget {vb:.10f} V")


def lagrangian_certificate(P: dict, sol: dict, n_rpm: float, Vdc: float, pcap: float):
    wm = 2 * math.pi * n_rpm / 60
    we = P["p"] * wm
    d, q = sol["id_A_peak"], sol["iq_A_peak"]
    Dl = P["Ld"] - P["Lq"]
    kp = 1.5 * P["p"]
    gradT = np.array([kp * Dl * q, kp * (P["psi"] + Dl * d)])
    HT = kp * Dl * np.array([[0.0, 1.0], [1.0, 0.0]])
    a1 = np.array([P["Rs"], -we * P["Lq"]])
    a2 = np.array([we * P["Ld"], P["Rs"]])
    vd = P["Rs"] * d - we * P["Lq"] * q
    vq = P["Rs"] * q + we * (P["psi"] + P["Ld"] * d)
    gradV = 2 * vd * a1 + 2 * vq * a2
    HV = 2 * (np.outer(a1, a1) + np.outer(a2, a2))
    c2 = 1.5 * P["Rs"] + P["a2"]
    gradP = np.array([2 * c2 * d + 1.5 * we * Dl * q, 2 * c2 * q + 1.5 * we * (P["psi"] + Dl * d)])
    HP = 2 * c2 * np.eye(2) + 1.5 * we * Dl * np.array([[0.0, 1.0], [1.0, 0.0]])
    lam = np.linalg.solve(np.column_stack([gradV, gradP]), gradT)
    HL = HT - lam[0] * HV - lam[1] * HP
    eig = np.linalg.eigvalsh(HL)
    stat = gradT - lam[0] * gradV - lam[1] * gradP

    x0 = np.array([d, q])

    def L(x):
        pt = point(P, x[0], x[1], n_rpm, Vdc)
        gV = pt["v_peak_V"] ** 2 - pt["_vb"] ** 2
        gP = pt["Pdc_W"] - pcap
        return pt["Tshaft_Nm"] - lam[0] * gV - lam[1] * gP

    # Newton step to the stationary point of the concave quadratic Lagrangian
    gradL0 = stat
    xs = x0 - np.linalg.solve(HL, gradL0)
    ub = L(xs)
    return lam, eig, float(np.max(np.abs(stat))), ub


def check_capability(spec: Path, drive: dict, rep: Report) -> None:
    fx = json.loads((spec / "golden_capability.json").read_text(encoding="utf-8"))
    P = drive_params(drive)
    for k, case in enumerate(fx["cases"]):
        n, Vdc = case["n_rpm"], case["Vdc_V"]
        exp = case["expected_solution"]
        tag = f"capability[{k}]:{n}rpm/{Vdc}V/{case['active_source_power_W']:.0f}W"
        g = point(P, exp["id_A_peak"], exp["iq_A_peak"], n, Vdc)
        rep.check(f"{tag}:self_consistent",
                  abs(g["Tshaft_Nm"] - case["expected_Tshaft_Nm"]) < 1e-9
                  and abs(g["Pdc_W"] - case["active_source_power_W"]) < 1e-6
                  and abs(g["Vmargin_V"]) < 1e-9 and g["i_peak_A"] <= P["Imax"]
                  and P["id_lo"] <= g["id_A_peak"] <= P["id_hi"],
                  f"T={g['Tshaft_Nm']:.10f} Pdc={g['Pdc_W']:.6f} Vmargin={g['Vmargin_V']:.2e}")
        # the minimum-current policy at the boundary torque reproduces the point
        best, _ = angle_min_current(P, n, case["expected_Tshaft_Nm"], Vdc)
        if best is None:
            rep.check(f"{tag}:policy_point", False, "no policy solution")
        else:
            compare_solution(rep, f"{tag}:policy_point", best, exp)
        cap = case["active_source_power_W"]
        # The DC-feasible end of the bracket is the one nearer zero torque
        # (motoring: the lower end; regeneration: the upper end).
        inner, outer = sorted(case["root_bracket_Tshaft_Nm"], key=abs)
        s_in, _ = angle_min_current(P, n, inner, Vdc)
        s_out, _ = angle_min_current(P, n, outer, Vdc)
        dis = min(P["Pdis"], Vdc * P["Idis"])
        chg = min(P["Pchg"], Vdc * P["Ichg"])
        ok_in = s_in is not None and -chg <= s_in["Pdc_W"] <= dis
        ok_out = s_out is None or not (-chg <= s_out["Pdc_W"] <= dis)
        rep.check(f"{tag}:bracket", ok_in and ok_out,
                  f"policy DC-feasible at {inner} N*m, not at {outer} N*m")
        cert = case.get("maximum_torque_upper_bound_certificate")
        if cert:
            lam, eig, stat, ub = lagrangian_certificate(P, exp, n, Vdc, cap)
            rel = lambda a, b: abs(a - b) / max(abs(b), 1e-30)
            rep.check(f"{tag}:certificate_multipliers",
                      lam[0] > 0 and lam[1] > 0 and rel(lam[0], cert["lambda_voltage"]) < 1e-6
                      and rel(lam[1], cert["lambda_dc_power"]) < 1e-6,
                      f"lambda_V={lam[0]:.9e} lambda_P={lam[1]:.9e}")
            ref_eig = sorted(cert["lagrangian_hessian_eigenvalues"])
            rep.check(f"{tag}:certificate_concavity",
                      all(e < 0 for e in eig) and all(rel(a, b) < 1e-6 for a, b in zip(sorted(eig), ref_eig)),
                      f"eigenvalues {sorted(eig)}")
            rep.check(f"{tag}:certificate_upper_bound",
                      ub - case["expected_Tshaft_Nm"] <= 1e-6,
                      f"UB={ub:.10f} vs T*={case['expected_Tshaft_Nm']:.10f} (stationarity {stat:.1e})")


def check_flux_map(spec: Path, rep: Report) -> None:
    fx = json.loads((spec / "manufactured_flux_map.json").read_text(encoding="utf-8"))
    d_axis = np.array(fx["axes"]["id_A_peak"], dtype=float)
    q_axis = np.array(fx["axes"]["iq_A_peak"], dtype=float)
    psd = np.array(fx["psi_d_Wb"], dtype=float)
    psq = np.array(fx["psi_q_Wb"], dtype=float)
    mask = np.array(fx["validity_mask"], dtype=bool)
    rep.check("fluxmap:shape", psd.shape == (41, 41) == psq.shape == mask.shape, f"{psd.shape}")
    rep.check("fluxmap:axes", np.all(np.diff(d_axis) > 0) and np.allclose(np.diff(d_axis), 10.0)
              and np.all(np.diff(q_axis) > 0) and d_axis[0] == -200 and d_axis[-1] == 200, "strictly increasing, 10 A")
    D, Q = np.meshgrid(d_axis, q_axis, indexing="ij")   # row = id, column = iq
    psd_a = 0.1 + 0.0002 * D - 1e-10 * D ** 3 - 1e-10 * D * Q ** 2
    psq_a = 0.0004 * Q - 2e-10 * Q ** 3 - 1e-10 * D ** 2 * Q
    e_d = float(np.max(np.abs(psd - psd_a)))
    e_q = float(np.max(np.abs(psq - psq_a)))
    rep.check("fluxmap:nodes_match_analytic", e_d < 1e-15 and e_q < 1e-15, f"max |dpsi_d|={e_d:.2e}, |dpsi_q|={e_q:.2e} Wb")
    # transposed interpretation must NOT match (orientation check)
    e_t = float(np.max(np.abs(psd.T - psd_a)))
    rep.check("fluxmap:orientation_row_is_id", e_t > 1e-6, f"transposed mismatch {e_t:.2e} Wb")
    rep.check("fluxmap:mask", bool(mask.all()), "all nodes valid")
    tp = fx["test_point"]
    i = int(np.flatnonzero(d_axis == tp["id_A_peak"])[0])
    j = int(np.flatnonzero(q_axis == tp["iq_A_peak"])[0])
    p = fx["pole_pairs"]
    Te = 1.5 * p * (psd[i, j] * tp["iq_A_peak"] - psq[i, j] * tp["id_A_peak"])
    rep.check("fluxmap:test_point_flux", abs(psd[i, j] - tp["psi_d_Wb"]) < 1e-15 and abs(psq[i, j] - tp["psi_q_Wb"]) < 1e-15,
              f"psi_d={psd[i, j]!r}, psi_q={psq[i, j]!r}")
    rep.check("fluxmap:test_point_torque", abs(Te - tp["Te_Nm"]) < 1e-12, f"Te={Te!r} N*m")
    d0, q0 = tp["id_A_peak"], tp["iq_A_peak"]
    an = {
        "dpsi_d_did_H": 0.0002 - 3e-10 * d0 ** 2 - 1e-10 * q0 ** 2,
        "dpsi_q_diq_H": 0.0004 - 6e-10 * q0 ** 2 - 1e-10 * d0 ** 2,
        "dpsi_d_diq_H": -2e-10 * d0 * q0,
        "dpsi_q_did_H": -2e-10 * d0 * q0,
    }
    rep.check("fluxmap:analytic_derivatives", all(abs(an[k] - tp[k]) < 1e-15 for k in an),
              ", ".join(f"{k}={an[k]:.8e}" for k in an))
    h = 10.0
    fd = {
        "dpsi_d_did_H": (psd[i + 1, j] - psd[i - 1, j]) / (2 * h),
        "dpsi_q_diq_H": (psq[i, j + 1] - psq[i, j - 1]) / (2 * h),
        "dpsi_d_diq_H": (psd[i, j + 1] - psd[i, j - 1]) / (2 * h),
        "dpsi_q_did_H": (psq[i + 1, j] - psq[i - 1, j]) / (2 * h),
    }
    # central-difference truncation error for these cubic polynomials is h^2/6*f'''
    trunc = {"dpsi_d_did_H": h * h / 6 * 6e-10, "dpsi_q_diq_H": h * h / 6 * 12e-10,
             "dpsi_d_diq_H": 0.0, "dpsi_q_did_H": 0.0}
    rep.check("fluxmap:central_difference", all(abs(fd[k] - tp[k]) <= trunc[k] + 1e-15 for k in fd),
              ", ".join(f"{k}: fd-exact={fd[k] - tp[k]:.2e}" for k in fd))
    # reciprocity on all interior nodes
    dd_q = (psd[1:-1, 2:] - psd[1:-1, :-2]) / (2 * h)
    dq_d = (psq[2:, 1:-1] - psq[:-2, 1:-1]) / (2 * h)
    rec = float(np.max(np.abs(dd_q - dq_d)))
    rep.check("fluxmap:reciprocity_nodes", rec < 1e-15, f"max |dpsi_d/diq - dpsi_q/did| = {rec:.2e} H")


def check_semantics(spec: Path, rep: Report) -> None:
    fx = json.loads((spec / "semantic_boundary_cases.json").read_text(encoding="utf-8"))
    s00 = next(c for c in fx["cases"] if c["case_id"] == "S00_REGEN_LOSS_INTERVAL")
    lo = s00["Pshaft_W"] + s00["total_loss_interval_W"][0]
    hi = s00["Pshaft_W"] + s00["total_loss_interval_W"][1]
    rep.check("semantic:S00_interval", [lo, hi] == s00["Pdc_interval_W"], f"Pdc in [{lo}, {hi}] W")
    rep.check("semantic:S00_mixed", lo < -s00["charge_cap_W"] <= hi, "interval straddles the charge cap")
    red = fx["future_only_not_MVP"][0]
    eta, ratio = red["efficiency"], red["ratio_motor_to_output"]
    t_m = red["motoring"]["motor_torque_Nm"] * ratio * eta
    t_r = red["regen"]["motor_torque_Nm"] * ratio / eta
    rep.check("semantic:E00_reducer", abs(t_m - red["motoring"]["output_torque_Nm"]) < 1e-9
              and abs(t_r - red["regen"]["output_torque_Nm"]) < 1e-9
              and red["motor_speed_rpm"] / ratio == red["output_speed_rpm"],
              f"motoring {t_m}, regen {t_r}")
    th = fx["future_only_not_MVP"][1]
    tau = th["thermal_resistance_K_W"] * th["thermal_capacity_J_K"]
    dT = th["constant_loss_W"] * th["thermal_resistance_K_W"]
    T100 = th["coolant_temperature_C"] + dT * (1 - math.exp(-100 / tau))
    t80 = -tau * math.log(1 - (80 - th["coolant_temperature_C"]) / dT)
    rep.check("semantic:E01_thermal", abs(T100 - th["T_at_100s_C"]) < 1e-9 and abs(t80 - th["time_to_80C_s"]) < 1e-9,
              f"T(100 s)={T100:.10f} C, t(80 C)={t80:.10f} s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    ap.add_argument("--json", type=Path, help="write the check table as JSON")
    args = ap.parse_args(argv)
    spec = args.spec_dir
    rep = Report()
    check_manifest(spec, rep)
    drive = json.loads((spec / "synthetic_drive.json").read_text(encoding="utf-8"))
    check_forward(spec, drive, rep)
    check_inverse(spec, drive, rep)
    check_capability(spec, drive, rep)
    check_flux_map(spec, rep)
    check_semantics(spec, rep)
    width = max(len(r["check"]) for r in rep.rows)
    for r in rep.rows:
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']:<{width}}  {r['detail']}")
    print(f"\n{len(rep.rows) - len(rep.failures)}/{len(rep.rows)} independent fixture checks passed")
    if args.json:
        args.json.write_text(json.dumps(rep.rows, indent=2), encoding="utf-8")
    return 0 if not rep.failures else 1


if __name__ == "__main__":
    sys.exit(main())
