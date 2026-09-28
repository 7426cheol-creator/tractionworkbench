"""Layer-1 expected values for the MathWorks parity cases, derived from the ACCEPTED SOURCE, not from the engine.

Source-separated acceptance: a port is checked against the Python reference (layer 2) AND against values that do not
come from the Python implementation, so a defect both implementations share cannot pass unnoticed.  This module
therefore never imports the engine (physics, models, solvers, analysis, extensions, io, api, service); a test checks
its import list.  Its sources are:

* the reference package ``traction_workbench_spec_v1`` - read with the SHA-256 of its own manifest (a changed file
  is not the accepted source): ``golden_forward.json`` (expected values computed by the package authors) and the
  analytic potential of ``manufactured_flux_map.json``;
* the model-contract equations evaluated literally with the EXPORTED parameters (what the target receives), and the
  contract's tolerance and classification rules;
* for a flux map: the definition of the interpolant (bilinear inside valid cells, covered = the point lies in the
  closure of at least one valid cell), written here as a set test - not the engine's search-and-edge-rule code.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

REFERENCE_FILES = ("synthetic_drive.json", "golden_forward.json", "manufactured_flux_map.json")

# the manufactured potential as the reference package states it; the oracle refuses a package that states another
MANUFACTURED_ANALYTIC = {"psi_d": "0.1+0.0002*d-1e-10*d^3-1e-10*d*q^2",
                         "psi_q": "0.0004*q-2e-10*q^3-1e-10*d^2*q"}

# golden_forward.json key -> quantity name of the cases
GOLDEN_KEYS = {"i_peak_A": "i_peak_A", "i_phase_rms_A": "i_phase_rms_A", "vd_V_peak": "vd_V", "vq_V_peak": "vq_V",
               "v_peak_V": "v_peak_V", "Te_Nm": "Te_Nm", "Tshaft_Nm": "Tshaft_Nm", "Pshaft_W": "Pshaft_W",
               "Pcu_W": "Pcu_W", "Prot_W": "Prot_W", "Pac_W": "Pac_W", "Pinv_W": "Pinv_W", "Pdc_W": "Pdc_W",
               "Idc_A": "Idc_A", "Vmargin_V": "voltage_margin_V"}


class ReferenceMismatch(ValueError):
    """The reference package on disk is not the accepted one (manifest hash or stated formula differs)."""


def load_reference(ref_dir) -> dict:
    """The reference files, each verified against the package manifest."""
    ref_dir = Path(ref_dir)
    manifest = json.loads((ref_dir / "manifest.json").read_text(encoding="utf-8"))
    want = {f["name"]: f["sha256"] for f in manifest["files"]}
    files = {}
    for name in REFERENCE_FILES:
        data = (ref_dir / name).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != want.get(name):
            raise ReferenceMismatch(f"{name}: sha256 {sha[:12]} is not the manifest's {str(want.get(name))[:12]}")
        files[name] = {"sha256": sha, "content": json.loads(data)}
    stated = files["manufactured_flux_map.json"]["content"].get("analytic_flux")
    if stated != MANUFACTURED_ANALYTIC:
        raise ReferenceMismatch(f"manufactured_flux_map.json states the potential {stated!r}; the oracle implements "
                                f"{MANUFACTURED_ANALYTIC!r}")
    return {"version": manifest.get("version"), "files": files}


def source_label(ref: dict, name: str, detail: str = "") -> str:
    return (f"reference package v{ref['version']} {name} (sha256 {ref['files'][name]['sha256'][:12]})"
            + (f" {detail}" if detail else ""))


def golden_forward(ref: dict) -> dict:
    """case_id -> {input, overrides, expected (case quantity names), expected_violations}."""
    out = {}
    for c in ref["files"]["golden_forward.json"]["content"]["cases"]:
        out[c["case_id"]] = {"input": c["input"], "overrides": c.get("parameter_overrides") or {},
                             "expected": {GOLDEN_KEYS[k]: v for k, v in c["expected"].items() if k in GOLDEN_KEYS},
                             "expected_violations": list(c.get("expected_violations", []))}
    return out


# -- the model contract, evaluated literally ------------------------------------------------------------------


def tolerance(limit: float, abs_floor: float, rel: float) -> float:
    """Constraint tolerance of the contract: max(abs_floor, rel * |limit|)."""
    return max(abs_floor, rel * abs(limit))


def constraint_state(sense: str, limit, demand, tol: float) -> str:
    """SATISFIED / ACTIVE / VIOLATED / NOT_EVALUATED from the slack (limit - demand for 'upper')."""
    if limit is None or demand is None:
        return "NOT_EVALUATED"
    slack = limit - demand if sense == "upper" else demand - limit
    if not math.isfinite(slack):
        return "NOT_EVALUATED"
    if slack < -tol:
        return "VIOLATED"
    return "ACTIVE" if slack <= tol else "SATISFIED"


def energy_mode(p_shaft, p_dc, omega_m: float, p_tol: float, w_tol: float) -> str:
    """The classification rule of the contract (sign convention: electrical -> mechanical positive)."""
    if p_shaft is None or p_dc is None:
        return "UNDETERMINED"
    if abs(omega_m) <= w_tol:
        return "STANDSTILL"
    if abs(p_shaft) <= p_tol:
        return "ZERO_SHAFT_POWER"
    if p_shaft > p_tol:
        if p_dc <= p_tol or not (0.0 <= p_shaft / p_dc <= 1.0):
            return "ACCOUNTING_INCONSISTENCY"
        return "MOTORING"
    if p_dc < -p_tol:
        return "REGENERATING" if 0.0 <= abs(p_dc) / abs(p_shaft) <= 1.0 else "ACCOUNTING_INCONSISTENCY"
    return "BRAKING_WITHOUT_NET_DC_RECOVERY"


def dq_quantities(m: dict, n_rpm: float, vdc: float, id_a: float, iq_a: float, psd: float, psq: float,
                  Rs: float) -> dict:
    """Every forward quantity from the contract equations, for the flux linkages (psd, psq) at the point.

    ``m`` is an exported model file (models/<key>.json): pole pairs, rotational / inverter loss closures, voltage
    budget.  Quantities the model does not define are None (never zero).
    """
    p = m["motor"]["pole_pairs"]
    wm = 2.0 * math.pi * n_rpm / 60.0
    we = p * wm
    vd = Rs * id_a - we * psq
    vq = Rs * iq_a + we * psd
    rdrop = m["inverter"]["voltage"]["resistive_drop_ohm"]
    vcmd = math.hypot(vd + rdrop * id_a, vq + rdrop * iq_a)
    i2 = id_a * id_a + iq_a * iq_a
    te = 1.5 * p * (psd * iq_a - psq * id_a)
    pcu = 1.5 * Rs * i2
    pac = 1.5 * (vd * id_a + vq * iq_a)
    rot = m["motor"]["rotational_loss"]
    tau = None if rot is None else rot["b_Nm_per_rad_s"] * wm + rot["c_Nm_per_rad2_s2"] * wm * abs(wm)
    tsh = None if tau is None else te - tau
    loss = m["inverter"]["loss"]
    pinv = loss["offset_W"] + loss["coeff_W_per_A2"] * i2 if loss["kind"] == "quadratic_surrogate" else None
    pdc = None if pinv is None else pac + pinv
    v = m["inverter"]["voltage"]
    budget = v["diagnostic_budget_scale"] * (1.0 - v["reserve_fraction"]) * vdc / math.sqrt(3.0)
    return {"omega_m_rad_s": wm, "omega_e_rad_s": we, "f_e_Hz": abs(we) / (2.0 * math.pi),
            "psi_d_Wb": psd, "psi_q_Wb": psq, "vd_V": vd, "vq_V": vq, "v_peak_V": math.hypot(vd, vq),
            "v_LL_rms_V": math.sqrt(1.5) * math.hypot(vd, vq), "v_cmd_peak_V": vcmd,
            "i_peak_A": math.sqrt(i2), "i_phase_rms_A": math.sqrt(i2) / math.sqrt(2.0), "Te_Nm": te,
            "tau_rot_Nm": tau, "Tshaft_Nm": tsh, "Pshaft_W": None if tsh is None else tsh * wm, "Pcu_W": pcu,
            "Prot_W": None if tau is None else wm * tau, "Pac_W": pac, "Pinv_W": pinv, "Pdc_W": pdc,
            "Idc_A": None if pdc is None else pdc / vdc, "voltage_budget_V": budget,
            "voltage_ceiling_V": vdc / math.sqrt(3.0), "voltage_margin_V": budget - vcmd}


def constant_dq_flux(m: dict, id_a: float, iq_a: float, psi_pm: float | None = None) -> tuple:
    f = m["motor"]["flux"]
    psi = f["psi_pm_Wb"] if psi_pm is None else psi_pm
    return psi + f["Ld_H"] * id_a, f["Lq_H"] * iq_a


def constraints(m: dict, settings: dict, q: dict, n_rpm: float, id_a: float, iq_a: float, limits: dict) -> dict:
    """Constraint states from the contract rule (limits: name -> {'state': finite|unlimited|not_declared, 'value'})."""
    s = settings
    rel = s["constraint_rel_tol"]
    dom = m["domain"]
    out = {"VOLTAGE": constraint_state("upper", q["voltage_budget_V"], q["v_cmd_peak_V"],
                                       tolerance(q["voltage_budget_V"], s["voltage_abs_tol_V"], rel)),
           "CURRENT": constraint_state("upper", m["inverter"]["current_limit_A_peak"], q["i_peak_A"],
                                       tolerance(m["inverter"]["current_limit_A_peak"], s["current_abs_tol_A"], rel))}
    for name, sense, lim, val, floor in (("ID_MIN", "lower", dom["id_A"][0], id_a, s["current_abs_tol_A"]),
                                         ("ID_MAX", "upper", dom["id_A"][1], id_a, s["current_abs_tol_A"]),
                                         ("IQ_MIN", "lower", dom["iq_A"][0], iq_a, s["current_abs_tol_A"]),
                                         ("IQ_MAX", "upper", dom["iq_A"][1], iq_a, s["current_abs_tol_A"]),
                                         ("SPEED_MIN", "lower", dom["speed_rpm"][0], n_rpm, s["speed_abs_tol_rpm"]),
                                         ("SPEED_MAX", "upper", dom["speed_rpm"][1], n_rpm, s["speed_abs_tol_rpm"])):
        out[name] = constraint_state(sense, lim, val, tolerance(lim, floor, rel))
    for name, key, sense, sign, demand, floor in (
            ("DC_DISCHARGE_POWER", "discharge_power_max_W", "upper", 1.0, q["Pdc_W"], s["power_abs_tol_W"]),
            ("DC_CHARGE_POWER", "charge_power_max_W", "lower", -1.0, q["Pdc_W"], s["power_abs_tol_W"]),
            ("DC_DISCHARGE_CURRENT", "discharge_current_max_A", "upper", 1.0, q["Idc_A"], s["current_abs_tol_A"]),
            ("DC_CHARGE_CURRENT", "charge_current_max_A", "lower", -1.0, q["Idc_A"], s["current_abs_tol_A"])):
        lim = limits[key]
        if lim["state"] != "finite":
            continue                        # not declared or declared unlimited: no finite constraint
        L = sign * lim["value"]
        out[name] = constraint_state(sense, L, demand, tolerance(L, floor, rel))
    return out


# -- boundary constructions (points placed a chosen multiple of the tolerance from a limit) -------------------


def iq_on_voltage_circle(m: dict, n_rpm: float, id_a: float, v_target: float, psi_pm: float | None = None,
                         Rs: float | None = None) -> float:
    """Positive iq with |v_cmd| = v_target for a constant-dq model with the ideal voltage mapping (closed form).

    (Rs id - we Lq iq)^2 + (Rs iq + we psi_d)^2 = V^2 is a quadratic a iq^2 + b iq + c = 0.
    """
    f = m["motor"]["flux"]
    rs = m["motor"]["Rs_ohm"] if Rs is None else Rs
    psi = f["psi_pm_Wb"] if psi_pm is None else psi_pm
    we = m["motor"]["pole_pairs"] * 2.0 * math.pi * n_rpm / 60.0
    psd = psi + f["Ld_H"] * id_a
    a = (we * f["Lq_H"]) ** 2 + rs * rs
    b = 2.0 * rs * we * (psd - f["Lq_H"] * id_a)
    c = (rs * id_a) ** 2 + (we * psd) ** 2 - v_target ** 2
    disc = b * b - 4.0 * a * c
    if disc < 0:
        raise ValueError("the voltage circle does not reach this id")
    return (-b + math.sqrt(disc)) / (2.0 * a)


def iq_for_dc_power(m: dict, n_rpm: float, id_a: float, p_target: float) -> float:
    """iq < 0 (regeneration, smaller |iq|) with P_dc = p_target for a constant-dq model with the quadratic surrogate.

    P_dc = Te wm + Pcu + Pinv = A iq^2 + B iq + C with A = 1.5 Rs + a2, B = 1.5 p wm (psi_d - Lq id),
    C = (1.5 Rs + a2) id^2 + a0.
    """
    f, mo, loss = m["motor"]["flux"], m["motor"], m["inverter"]["loss"]
    wm = 2.0 * math.pi * n_rpm / 60.0
    A = 1.5 * mo["Rs_ohm"] + loss["coeff_W_per_A2"]
    B = 1.5 * mo["pole_pairs"] * wm * (f["psi_pm_Wb"] + f["Ld_H"] * id_a - f["Lq_H"] * id_a)
    C = A * id_a * id_a + loss["offset_W"] - p_target
    disc = B * B - 4.0 * A * C
    if disc < 0:
        raise ValueError("no point with this DC power at this id")
    roots = sorted(((-B - math.sqrt(disc)) / (2.0 * A), (-B + math.sqrt(disc)) / (2.0 * A)))
    neg = [r for r in roots if r < 0]
    if not neg:
        raise ValueError("no regenerating point with this DC power at this id")
    return max(neg)


# -- flux maps: the analytic families and the interpolant's definition -----------------------------------------


def manufactured_flux(d: float, q: float) -> tuple:
    """The manufactured conservative potential of the reference package (psi = grad F)."""
    return (0.1 + 0.0002 * d - 1e-10 * d ** 3 - 1e-10 * d * q * q,
            0.0004 * q - 2e-10 * q ** 3 - 1e-10 * d * d * q)


def verification_flux(d: float, q: float, psi_pm: float) -> tuple:
    """The verification family of the non-square test maps: the manufactured potential with its PM term psi_pm."""
    psd, psq = manufactured_flux(d, q)
    return psd - 0.1 + psi_pm, psq


def covering_cell(xa, ya, cell_valid, x: float, y: float):
    """A valid cell whose closure contains (x, y), or None.  Any such cell gives the same bilinear value (the
    interpolant restricted to a shared edge depends only on that edge's two nodes)."""
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    for i in range(len(xa) - 1):
        if not xa[i] <= x <= xa[i + 1]:
            continue
        for j in range(len(ya) - 1):
            if ya[j] <= y <= ya[j + 1] and cell_valid[i][j]:
                return i, j
    return None


def cell_validity(valid) -> list:
    n, k = len(valid), len(valid[0])
    return [[bool(valid[i][j] and valid[i + 1][j] and valid[i][j + 1] and valid[i + 1][j + 1]) for j in range(k - 1)]
            for i in range(n - 1)]


def bilinear(xa, ya, f, i: int, j: int, x: float, y: float) -> float:
    """Bilinear interpolation of the node values f[i][j] in cell (i, j)."""
    t = (x - xa[i]) / (xa[i + 1] - xa[i])
    u = (y - ya[j]) / (ya[j + 1] - ya[j])
    return ((1 - t) * (1 - u) * f[i][j] + t * (1 - u) * f[i + 1][j] + (1 - t) * u * f[i][j + 1]
            + t * u * f[i + 1][j + 1])


def map_lookup(plane: dict, x: float, y: float) -> dict:
    """psi at (x, y) of an exported plane by the interpolant's definition: covered or not, never extrapolated."""
    xa, ya = plane["id_axis_A"], plane["iq_axis_A"]
    cv = cell_validity(plane["valid"])
    cell = covering_cell(xa, ya, cv, x, y)
    if cell is None:
        return {"covered": False, "psi_d_Wb": None, "psi_q_Wb": None}
    i, j = cell
    return {"covered": True, "psi_d_Wb": bilinear(xa, ya, plane["psi_d_Wb"], i, j, x, y),
            "psi_q_Wb": bilinear(xa, ya, plane["psi_q_Wb"], i, j, x, y)}


def blend_planes(lo: dict, hi: dict, t_c: float) -> dict:
    """Linear temperature interpolation between two planes on identical axes (the declared rule): values where
    both nodes are valid, the intersection of the masks."""
    w = (t_c - lo["magnet_temp_C"]) / (hi["magnet_temp_C"] - lo["magnet_temp_C"])
    n, k = len(lo["valid"]), len(lo["valid"][0])
    valid = [[bool(lo["valid"][i][j] and hi["valid"][i][j]) for j in range(k)] for i in range(n)]

    def mix(key):
        return [[(1 - w) * lo[key][i][j] + w * hi[key][i][j] if valid[i][j] else None for j in range(k)]
                for i in range(n)]
    return {"id_axis_A": lo["id_axis_A"], "iq_axis_A": lo["iq_axis_A"], "psi_d_Wb": mix("psi_d_Wb"),
            "psi_q_Wb": mix("psi_q_Wb"), "valid": valid, "magnet_temp_C": t_c}


def plane_for(flux: dict, magnet_temp_C):
    """(plane, None) or (None, reason) by the contract's plane-selection rule."""
    planes = flux["planes"]
    tol = flux["temperature_match_tol_C"]
    if magnet_temp_C is None:
        return (planes[0], None) if len(planes) == 1 else (None, "MISSING_INPUT")
    for p in planes:
        if p["magnet_temp_C"] is not None and abs(p["magnet_temp_C"] - magnet_temp_C) <= tol:
            return p, None
    temps = [p["magnet_temp_C"] for p in planes if p["magnet_temp_C"] is not None]
    if flux["temperature_interpolation"] == "linear" and temps and temps[0] < magnet_temp_C < temps[-1]:
        k = next(i for i, t in enumerate(temps) if t > magnet_temp_C)
        return blend_planes(planes[k - 1], planes[k], magnet_temp_C), None
    return None, "OUTSIDE_MODEL_DOMAIN"
