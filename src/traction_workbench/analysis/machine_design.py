"""Machine design changes around a validated reference (handoff section 10, "motor design / FEA strategy").

Not a motor CAD or FEA: geometry, nonlinear field solutions, rotor mechanics and demagnetisation stay external.
What is native here is connecting a *validated* machine description to system decisions:

* **coherent scaling** of a reference machine - series turns (incl. parallel paths), active stack length and (for
  constant-parameter data) PM flux - where every parameter moves together:
  psi_PM' = k_N k_L k_PM psi_PM, L' = k_N^2 (k_L (1 - e_L) + e_L) L, R' = k_N^2 (k_L (1 - e_R) + e_R) R, and the
  ampere-turn domain scales by 1 / k_N (same slot fill and copper area).  The end-winding shares e_R, e_L are
  DECLARED when the stack changes (never assumed zero).  What the scaling cannot carry - AC copper loss, the
  demagnetisation envelope, HF parasitics, rotor mechanics, the thermal network, iron loss at a new flux density,
  cogging / ripple - is listed as invalidated for the candidate, not silently kept;
* a **trade study** that judges every candidate on the SAME requirements, temperatures and sources (coupled
  requirement margins: low-speed torque, high-speed torque at the lowest Vdc, a requirement point, UGO back-EMF,
  steady ASC current, copper loss), not on component peak efficiency;
* a **winding layout** check (star of slots): balance condition, winding factors of the fundamental and harmonics,
  periodicity and a cogging indicator - a winding factor alone approves neither losses nor NVH;
* **concept sizing** from a declared air-gap shear stress (rotor volume T = 2 sigma V_r) - a concept estimate,
  not a rating, no thermal / demag / mechanical approval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from .. import progress
from ..errors import InputValidationError
from ..identity import content_sha256
from ..models.components import DriveModel, RotationalLossModel, WindingDefinition
from ..models.flux import ConstantFluxModel, CurrentBox, FluxMapModel
from ..validation import finite as _finite
from ..models.provenance import Provenance
from ..scenario import DcSourceLimits, Scenario
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator

UNLIMITED = DcSourceLimits(math.inf, math.inf, math.inf, math.inf)
CHECK_KINDS = ("capability", "point", "ugo", "asc", "copper")


# --------------------------------------------------------------------------------------------- coherent scaling

@dataclass(frozen=True)
class ScalingSpec:
    name: str
    k_turns: float = 1.0                 # effective series turns per phase N'/N (turns and parallel paths together)
    k_stack: float = 1.0                 # active stack length ratio
    k_pm: float = 1.0                    # PM flux ratio at the same geometry (constant-parameter data only)
    end_R_share: float | None = None     # share of the reference Rs in the end windings (required if k_stack != 1)
    end_L_share: float | None = None     # share of Ld / Lq from end-winding leakage (required if k_stack != 1)
    rot_loss: str = "proportional_to_stack"   # rotational loss: proportional_to_stack | unchanged (declared)
    basis: str = ""
    winding_from: str | None = None      # identity of the reference winding a turns / path change was computed from
    winding_to: dict | None = None       # the changed {parallel_paths, turns_per_coil} of that winding

    def __post_init__(self):
        for name in ("k_turns", "k_stack", "k_pm"):
            if _finite(name, getattr(self, name)) <= 0:
                raise InputValidationError(f"{name} must be > 0", field=name)
        if (self.winding_from is None) != (self.winding_to is None):
            raise InputValidationError("a winding change needs both the reference identity and the new turns / paths",
                                       field="winding_to")
        if self.winding_to is not None and set(self.winding_to) != {"parallel_paths", "turns_per_coil"}:
            raise InputValidationError("winding_to is {parallel_paths, turns_per_coil}", field="winding_to")
        if abs(self.k_stack - 1.0) > 1e-12 and (self.end_R_share is None or self.end_L_share is None):
            raise InputValidationError("a stack-length change needs the declared end-winding shares of R and L "
                                       "(end effects do not scale with the stack; they are never assumed zero)",
                                       field="end_R_share")
        for name in ("end_R_share", "end_L_share"):
            v = getattr(self, name)
            if v is not None and not (0.0 <= _finite(name, v) < 1.0):
                raise InputValidationError(f"{name} must be in [0, 1)", field=name)
        if self.rot_loss not in ("proportional_to_stack", "unchanged"):
            raise InputValidationError("rot_loss must be proportional_to_stack or unchanged", field="rot_loss")

    @property
    def is_reference(self) -> bool:
        return all(abs(x - 1.0) < 1e-12 for x in (self.k_turns, self.k_stack, self.k_pm))

    def factors(self) -> dict:
        kN, kL = self.k_turns, self.k_stack
        eR = self.end_R_share or 0.0
        eL = self.end_L_share or 0.0
        return {"psi": kN * kL * self.k_pm, "L": kN * kN * (kL * (1 - eL) + eL), "R": kN * kN * (kL * (1 - eR) + eR),
                "current_axis": 1.0 / kN, "rotational_loss": kL if self.rot_loss == "proportional_to_stack" else 1.0}


def spec_from_dict(c: dict) -> ScalingSpec:
    """A candidate as entered (units in the names).  Only an absent / blank factor means "unchanged" (1); an entered
    0 stays 0 and is refused by the spec - a real input is never swapped for another valid design (review R2, MD-02).
    Blank end shares stay None (not declared)."""
    def factor(key):
        v = c.get(key)
        return 1.0 if v is None or (isinstance(v, str) and not v.strip()) else _finite(key, v)

    def opt(key):
        v = c.get(key)
        return None if v is None or (isinstance(v, str) and not v.strip()) else _finite(key, v)
    wt = c.get("winding_to")
    return ScalingSpec(str(c.get("name") or "candidate"), factor("k_turns"), factor("k_stack"), factor("k_pm"),
                       opt("end_R_share"), opt("end_L_share"), c.get("rot_loss") or "proportional_to_stack",
                       c.get("basis") or "", c.get("winding_from") or None, None if not wt else dict(wt))


INVALIDATED = {
    "turns": ["AC copper loss (conductor size / strands / hairpin geometry change)", "HF winding impedance and "
              "parasitic capacitance (EMC path)", "thermal network of the winding (copper distribution, fill)",
              "demagnetisation envelope in amperes (valid only as an ampere-turn limit)"],
    "stack": ["iron / PM loss maps (volume)", "rotor mechanics and overspeed envelope", "inertia and thermal "
              "capacities", "end-effect split (declared shares only)"],
    "pm": ["saturation and cross-coupling (a PM change is not a scaling for a nonlinear map)", "demagnetisation "
           "envelope (new magnet / grade)", "iron loss at the new flux density", "cogging and torque ripple"],
}


def winding_identity(w) -> str:
    """Content identity of a winding (WindingDefinition or a layout dict): slots, pole pairs, pitch, paths, turns."""
    d = w.identity() if isinstance(w, WindingDefinition) else {k: w.get(k) for k in ("Q", "p", "y", "parallel_paths",
                                                                                         "turns_per_coil")}
    return content_sha256(d)


def _winding_lineage(drive: DriveModel, spec: ScalingSpec):
    """The derived machine's winding: a checked turns / path change of the declared reference winding, or None (a
    generic k_N leaves the derived winding undefined)."""
    ref = drive.motor.winding
    if spec.winding_from is None:
        if ref is not None and abs(spec.k_turns - 1.0) > 1e-12:
            return None, "generic k_N: the derived machine's winding is not defined (not a change of the declared winding)"
        return ref, None
    if ref is None or winding_identity(ref) != spec.winding_from:
        raise InputValidationError("the candidate's winding change was computed from another reference winding than "
                                   "the active machine declares - not a change of THIS winding", field="winding_from")
    to = {k: int(spec.winding_to[k]) for k in ("parallel_paths", "turns_per_coil")}
    new = replace(ref, **to, basis=ref.basis + f" [changed: {to['turns_per_coil']} turns / {to['parallel_paths']} paths]")
    a = winding_layout(ref.Q, ref.p, ref.y, parallel_paths=ref.parallel_paths, turns_per_coil=ref.turns_per_coil,
                       harmonics=1)
    b = winding_layout(ref.Q, ref.p, ref.y, parallel_paths=new.parallel_paths, turns_per_coil=new.turns_per_coil,
                       harmonics=1)
    kN = effective_turns_ratio(a, b)
    if abs(kN - spec.k_turns) > 1e-9 * kN:
        raise InputValidationError(f"k_turns {spec.k_turns:g} does not follow from the winding change (k_N = {kN:.6g})",
                                   field="k_turns")
    return new, (f"winding {winding_identity(ref)[:12]} -> {winding_identity(new)[:12]}: {ref.turns_per_coil} -> "
                 f"{new.turns_per_coil} turns / coil, {ref.parallel_paths} -> {new.parallel_paths} paths (same Q, p, y)")


def scale_drive(drive: DriveModel, spec: ScalingSpec) -> tuple[DriveModel, dict]:
    """Derived candidate with lineage; the reference is returned unchanged for the identity spec."""
    if spec.is_reference and spec.winding_from is None:
        return drive, {"carried": [], "invalidated": [], "factors": spec.factors(), "derived": False}
    new_winding, wnote = _winding_lineage(drive, spec)
    fa = spec.factors()
    m = drive.motor
    flux = m.flux
    if isinstance(flux, FluxMapModel):
        if abs(spec.k_pm - 1.0) > 1e-12:
            raise InputValidationError("a PM change on a nonlinear flux map is not a scaling: import a map computed "
                                       "for the new magnet (saturation and cross-coupling change)", field="k_pm")
        if abs(spec.k_stack - 1.0) > 1e-12 and (spec.end_L_share or 0.0) > 0:
            raise InputValidationError("stack scaling of a 2D flux map with an end-leakage share needs a map for the "
                                       "new stack (the end leakage is not in the 2D data)", field="end_L_share")
        planes = []
        for pl in getattr(flux, "source_planes", flux.planes):     # un-mirrored source (symmetry re-applied)
            planes.append(replace(pl, id_axis_A=pl.id_axis_A * fa["current_axis"],
                                  iq_axis_A=pl.iq_axis_A * fa["current_axis"],
                                  psi_d_Wb=pl.psi_d_Wb * fa["psi"], psi_q_Wb=pl.psi_q_Wb * fa["psi"],
                                  label=(pl.label or "") + f" [scaled {spec.name}]"))
        new_flux = replace(flux, planes=tuple(planes))
    else:
        val = flux.validity
        new_val = None if val is None else CurrentBox(tuple(x * fa["current_axis"] for x in val.id_A),
                                                      tuple(x * fa["current_axis"] for x in val.iq_A))
        new_flux = ConstantFluxModel(flux.psi_pm_Wb * fa["psi"], flux.Ld_H * fa["L"], flux.Lq_H * fa["L"],
                                     validity=new_val)
    rot = m.rotational_loss
    if rot is not None and fa["rotational_loss"] != 1.0:
        rot = RotationalLossModel(viscous_Nm_per_rad_s=rot.viscous_Nm_per_rad_s * fa["rotational_loss"],
                                  quadratic_Nm_per_rad2_s2=rot.quadratic_Nm_per_rad2_s2 * fa["rotational_loss"],
                                  includes_iron_loss=rot.includes_iron_loss, basis=rot.basis + " [stack-scaled]",
                                  description=rot.description)
    motor = replace(m, motor_id=f"{m.motor_id}·{spec.name}", flux=new_flux, Rs_ohm=m.Rs_ohm * fa["R"],
                    rotational_loss=rot, winding=new_winding)
    dom = drive.domain
    domain = replace(dom, id_A=tuple(x * fa["current_axis"] for x in dom.id_A),
                     iq_A=tuple(x * fa["current_axis"] for x in dom.iq_A),
                     interpretation=(dom.interpretation + " | " if dom.interpretation else "") +
                                    f"scaled by 1/k_N = {fa['current_axis']:.4g} (read as an ampere-turn limit)")
    prov = Provenance(drive.provenance.origin, drive.provenance.source, drive.provenance.revision,
                      f"DERIVED by scaling from {drive.drive_id} rev {drive.revision} ({spec.name}): not validated",
                      drive.provenance.sha256, tuple(drive.provenance.notes) + (f"scaling basis: {spec.basis or 'n/a'}",))
    derived = replace(drive, drive_id=f"{drive.drive_id}·{spec.name}", revision=f"{drive.revision}+{spec.name}",
                      motor=motor, domain=domain, provenance=prov,
                      notes=tuple(drive.notes) + (f"derived candidate {spec.name}: k_N {spec.k_turns:g}, k_L "
                                                  f"{spec.k_stack:g}, k_PM {spec.k_pm:g}",))
    inval = []
    if abs(spec.k_turns - 1) > 1e-12:
        inval += INVALIDATED["turns"]
    if abs(spec.k_stack - 1) > 1e-12:
        inval += INVALIDATED["stack"]
    if abs(spec.k_pm - 1) > 1e-12:
        inval += INVALIDATED["pm"]
    carried = [f"PM flux x{fa['psi']:.4g} (k_N k_L k_PM)", f"Ld, Lq x{fa['L']:.4g} (k_N^2 (k_L (1-e_L) + e_L))",
               f"Rs x{fa['R']:.4g} (k_N^2 (k_L (1-e_R) + e_R); same slot fill and copper area)",
               f"current axes / domain x{fa['current_axis']:.4g} (same ampere-turns)",
               f"rotational loss x{fa['rotational_loss']:.4g} ({spec.rot_loss})"]
    if wnote:
        (carried if spec.winding_from else inval).append(wnote)
    return derived, {"carried": carried, "invalidated": inval, "factors": fa, "derived": True,
                     "validation_status": prov.validation_status,
                     "winding": None if new_winding is None else {**new_winding.identity(),
                                                                  "identity": winding_identity(new_winding)}}


# --------------------------------------------------------------------------------------------- trade study

@dataclass(frozen=True)
class DesignCheck:
    name: str
    kind: str                     # capability | point | ugo | asc | copper
    speed_rpm: float
    Vdc_V: float
    torque_Nm: float | None = None
    limit: float | None = None    # ugo: Vdc for the back-EMF comparison; asc: current limit A; copper: W
    magnet_temp_C: float | None = None

    def __post_init__(self):
        if self.kind not in CHECK_KINDS:
            raise InputValidationError(f"check kind must be one of {CHECK_KINDS}", field="kind")


def _check(d: DriveModel, c: DesignCheck, limits: DcSourceLimits) -> dict:
    sc = Scenario(c.name, c.speed_rpm, c.Vdc_V, limits, magnet_temp_C=c.magnet_temp_C)
    if c.kind == "capability":
        ev = PolicyEvaluator(d, sc)
        cap = policy_capability(ev, +1 if (c.torque_Nm or 1) >= 0 else -1)
        if not cap.accepted:
            return {"status": "UNKNOWN", "value": cap.value_Nm, "margin": None, "unit": "N·m",
                    "detail": "capability not accepted: " + "; ".join(cap.gate_messages or cap.notes or ())}
        val = cap.value_Nm
        if c.torque_Nm is None:
            return {"status": "UNKNOWN", "value": val, "margin": None, "unit": "N·m", "detail": "no requirement"}
        claim = ev.solve(float(c.torque_Nm)).policy_claim          # the claim is the witness-gated solve itself
        mg = abs(val) - abs(c.torque_Nm)
        return {"status": claim.status.value, "value": val, "margin": mg, "unit": "N·m",
                "rel_margin": mg / max(abs(c.torque_Nm), 1e-9),
                "detail": f"policy capability {val:.4g} N·m ({'certified' if cap.certified else 'witnessed'}) vs "
                          f"{c.torque_Nm:g} N·m; requirement claim {claim.status.value}"}
    if c.kind in ("point", "copper"):
        sol = PolicyEvaluator(d, sc).solve(float(c.torque_Nm))
        pt = sol.point
        st = sol.policy_claim.status.value
        if pt is None:
            return {"status": st, "value": None, "margin": None, "unit": "", "detail": sol.policy_claim.detail}
        if c.kind == "copper":
            val = pt.Pcu_W
            if c.limit is None:
                return {"status": "UNKNOWN", "value": val, "margin": None, "unit": "W", "detail": "no copper-loss limit"}
            mg = c.limit - val
            return {"status": "FEASIBLE" if (mg >= 0 and st == "FEASIBLE") else ("INFEASIBLE" if mg < 0 else st),
                    "value": val, "margin": mg, "unit": "W", "rel_margin": mg / max(c.limit, 1e-9),
                    "detail": f"P_cu {val:.4g} W (|i| {pt.i_peak_A:.4g} A) vs {c.limit:g} W (same torque, same "
                              f"requirement point); requirement claim {st}"}
        im = d.inverter.current_limit_A_peak - pt.i_peak_A
        return {"status": st, "value": pt.i_peak_A, "margin": im, "unit": "A", "rel_margin": im / d.inverter.current_limit_A_peak,
                "detail": f"|i| {pt.i_peak_A:.4g} A (inverter margin {im:.4g} A), voltage margin {round(pt.voltage_margin_V, 3):g} V, "
                          f"P_dc {pt.Pdc_W if pt.Pdc_W is None else round(pt.Pdc_W, 1)} W"}
    if c.kind == "ugo":
        from ..extensions.dclink import back_emf_ll_peak
        e = back_emf_ll_peak(d, c.speed_rpm, c.magnet_temp_C)
        if e is None:
            return {"status": "UNKNOWN", "value": None, "margin": None, "unit": "V",
                    "detail": "back-EMF not evaluable (magnet temperature / flux-map plane)"}
        vlim = c.limit if c.limit is not None else c.Vdc_V
        mg = vlim - e
        return {"status": "FEASIBLE" if mg >= 0 else "INFEASIBLE", "value": e, "margin": mg, "unit": "V",
                "rel_margin": mg / vlim, "detail": f"open-circuit line back-EMF peak {e:.4g} V vs {vlim:g} V (above: "
                                                   f"uncontrolled rectification after a loss of control)"}
    from ..extensions.safe_state import asc_steady_state
    a = asc_steady_state(d, c.speed_rpm, magnet_temp_C=c.magnet_temp_C)
    if not a or not a.get("evaluable", True) or a.get("i_peak_A") is None:
        return {"status": "UNKNOWN", "value": None, "margin": None, "unit": "A",
                "detail": "steady ASC current not evaluable" + (f": {a.get('reason')}" if isinstance(a, dict) else "")}
    val = a["i_peak_A"]
    if c.limit is None:
        return {"status": "UNKNOWN", "value": val, "margin": None, "unit": "A", "detail": "no ASC current limit declared"}
    mg = c.limit - val
    return {"status": "FEASIBLE" if mg >= 0 else "INFEASIBLE", "value": val, "margin": mg, "unit": "A",
            "rel_margin": mg / c.limit, "detail": f"steady ASC |i| {val:.4g} A vs {c.limit:g} A (steady screening, "
                                                   f"not the transient peak)"}


def trade_study(reference: DriveModel, specs: list, checks: list, limits: DcSourceLimits | None = None,
                envelope_speeds=None, envelope_Vdc: float | None = None) -> dict:
    """Every candidate on the same checks; the binding check is the smallest relative margin.

    ``specs`` are ScalingSpec objects or dicts as entered (a dict that fails validation becomes a refused row)."""
    lim = limits or UNLIMITED
    rows = []
    with progress.span(len(specs), "candidates") as steps:
        for spec in specs:
            steps.step()
            name = spec.get("name", "candidate") if isinstance(spec, dict) else spec.name
            try:                            # a refused candidate is reported in its row; the others are still judged
                if isinstance(spec, dict):
                    spec = spec_from_dict(spec)
                d, lin = scale_drive(reference, spec)
            except InputValidationError as exc:
                rows.append({"candidate": name, "error": str(exc)})
                continue
            res = {}
            for c in checks:
                try:
                    res[c.name] = _check(d, c, lim)
                except Exception as exc:  # noqa: BLE001 - a failed check is reported, never a pass
                    res[c.name] = {"status": "UNKNOWN", "value": None, "margin": None, "unit": "", "detail": str(exc)}
            rel = {k: v.get("rel_margin") for k, v in res.items() if v.get("rel_margin") is not None}
            binding = min(rel, key=rel.get) if rel else None
            row = {"candidate": spec.name, "spec": {"k_turns": spec.k_turns, "k_stack": spec.k_stack, "k_pm": spec.k_pm,
                                                    "end_R_share": spec.end_R_share, "end_L_share": spec.end_L_share},
                   "lineage": lin, "checks": res, "binding": binding,
                   "all_feasible": all(v["status"] == "FEASIBLE" for v in res.values()),
                   "parameters": {"psi_pm_Wb": getattr(d.motor.flux, "psi_pm_Wb", None), "Ld_H": getattr(d.motor.flux, "Ld_H", None),
                                  "Lq_H": getattr(d.motor.flux, "Lq_H", None), "Rs_ohm": d.motor.Rs_ohm}}
            if envelope_speeds:
                env = []                    # display envelope: witnessed (gate-accepted) values, not certified bounds
                vdc = envelope_Vdc or checks[0].Vdc_V
                for n in envelope_speeds:
                    sc = Scenario("env", float(n), vdc, lim, magnet_temp_C=checks[0].magnet_temp_C)
                    cap = policy_capability(PolicyEvaluator(d, sc), +1, samples=41, certify=False)
                    env.append({"speed_rpm": float(n), "torque_Nm": cap.value_Nm if cap.accepted else None})
                row["envelope"] = env
                row["envelope_Vdc_V"] = vdc
            rows.append(row)
    return {"rows": rows, "checks": [c.__dict__ for c in checks],
            "meaning": "coupled requirement margins on the same requirements, temperatures and sources; derived "
                       "candidates are scaled references (not validated) with the listed invalidated data"}


# --------------------------------------------------------------------------------------------- winding layout

def winding_layout(Q: int, p: int, y: int | None = None, m: int = 3, harmonics: int = 25,
                   parallel_paths: int = 1, turns_per_coil: int | None = None) -> dict:
    """Double-layer star-of-slots winding: phase belts of 60 electrical degrees, coil pitch y (slots).

    Feasible (m = 3, double layer) iff Q / (m t) is an integer with t = gcd(Q, p); balanced iff the phases get equal
    coil sides whose axes are 120 electrical degrees apart.  Phase winding factor of mechanical order k:
    |sum sign exp(j k 2 pi s / Q)| / sides over the coil sides of phase A (k = p is the working harmonic, kw1).  The
    harmonic spectrum is the three-phase MMF factor for balanced currents (triplen orders cancel, direction reported);
    orders below p are sub-harmonics (fractional-slot windings: rotor loss / NVH risk, not quantified here).
    Parallel paths a must divide the number of identical sections (t, or 2t when Q/t is even).
    """
    for name, v in (("Q", Q), ("p", p), ("m", m), ("parallel_paths", parallel_paths)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise InputValidationError(f"{name} must be an integer", field=name)
    if m != 3:
        raise InputValidationError("only three-phase windings (m = 3) are laid out", field="m")
    if Q < m or p < 1 or parallel_paths < 1:
        raise InputValidationError("slots Q >= 3, pole pairs p >= 1 and parallel paths a >= 1 are required", field="Q")
    if turns_per_coil is not None and (isinstance(turns_per_coil, bool) or not isinstance(turns_per_coil, int)
                                       or turns_per_coil < 1):
        raise InputValidationError("turns per coil must be a positive integer", field="turns_per_coil")
    t = math.gcd(Q, p)
    q = Q / (2 * p * m)
    feasible = (Q % (m * t) == 0)
    if y is None:
        y = max(1, int(round(Q / (2 * p))))
    if isinstance(y, bool) or not isinstance(y, int) or not (1 <= y < Q):
        raise InputValidationError("coil pitch y must be an integer with 1 <= y < Q", field="y")
    # electrical slot angles, reduced exactly (integer arithmetic) to avoid belt-boundary rounding
    num = [(p * s * 360 * 2) % (720 * Q) for s in range(Q)]          # 2 Q * angle in degrees, integer
    ang = [n / (2 * Q) for n in num]                                  # degrees in [0, 360)
    belts = ("A+", "C-", "B+", "A-", "C+", "B-")
    top = []
    for n in num:
        k = ((n + 30 * 2 * Q) % (720 * Q)) // (120 * Q)               # belt k covers [60k - 30, 60k + 30) degrees
        top.append(belts[int(k) % 6])
    # double layer: the coil whose go side is in slot s (top) returns in slot s + y (bottom, opposite sign)
    sides = {ph: [] for ph in ("A", "B", "C")}
    bottom = [""] * Q
    for s in range(Q):
        ph, sg = top[s][0], (1 if top[s][1] == "+" else -1)
        sides[ph].append((s, sg))
        r = (s + y) % Q
        sides[ph].append((r, -sg))
        bottom[r] = ph + ("-" if sg > 0 else "+")
    counts = {ph: len(v) for ph, v in sides.items()}

    def kw_mech(ph, k):
        if not sides[ph]:                              # a phase without coil sides (e.g. Q = 3, p = 3): no factor
            return 0.0, math.nan
        z = sum(sg * np.exp(1j * 2 * math.pi * k * s / Q) for s, sg in sides[ph])
        return abs(z) / len(sides[ph]), math.degrees(np.angle(z))
    kw1, angA = kw_mech("A", p)
    _, angB = kw_mech("B", p)
    _, angC = kw_mech("C", p)
    shift_AB = (angB - angA) % 360.0
    shift_AC = (angC - angA) % 360.0
    near = lambda a, b: math.isfinite(a) and min(abs(a - b), 360 - abs(a - b)) < 1e-6          # noqa: E731
    balanced = feasible and len(set(counts.values())) == 1 and kw1 > 1e-9 and (
        (near(shift_AB, 120) and near(shift_AC, 240)) or (near(shift_AB, 240) and near(shift_AC, 120)))
    sequence = "A-B-C along increasing slot number" if near(shift_AB, 120) else (
        "A-C-B along increasing slot number" if near(shift_AB, 240) else "not a balanced three-phase sequence")
    # three-phase MMF factor of mechanical order k for balanced currents (triplen orders cancel):
    # max over both rotation directions of |sum_ph W_ph(k) exp(+-j phi_ph)| / (3 sides), phi = 0, 120, 240 deg
    def w_ph(ph, k):
        return sum(sg * np.exp(-1j * 2 * math.pi * k * s / Q) for s, sg in sides[ph])
    phis = {"A": 0.0, "B": 2 * math.pi / 3, "C": 4 * math.pi / 3}
    n_side = max(len(sides["A"]), 1)
    harm = []
    for k in range(1, harmonics * p + 1):
        w = {ph: w_ph(ph, k) for ph in phis}
        pa = float(abs(w["A"]) / n_side)
        if balanced:
            fw = abs(sum(w[ph] * np.exp(1j * phis[ph]) for ph in phis)) / (3 * n_side)
            bw = abs(sum(w[ph] * np.exp(-1j * phis[ph]) for ph in phis)) / (3 * n_side)
            kwk, dirn = max(fw, bw), ("forward" if fw >= bw else "backward")
        else:                   # no balanced three-phase MMF exists: the phase-A factor only, direction undefined
            kwk, dirn = pa, "n/a"
        if kwk > 1e-9:
            harm.append({"order_mech": k, "nu_el": k / p, "kw": float(kwk), "phase_kw": pa, "direction": dirn})
    sub = [h for h in harm if h["order_mech"] < p and h["kw"] > 0.05]
    sections = 2 * t if (Q // t) % 2 == 0 else t
    paths_ok = sections % parallel_paths == 0
    out = {"Q": Q, "p": p, "poles": 2 * p, "y": y, "y_over_pole_pitch": y * 2 * p / Q, "q": q, "t_periodicity": t,
           "feasible": bool(feasible), "balanced": bool(balanced), "kw1": float(kw1), "harmonics": harm,
           "subharmonics": sub, "spectrum": "three-phase MMF (balanced currents)" if balanced else
           "phase A only (no balanced three-phase winding)", "slot_top": top, "slot_bottom": bottom, "slot_angles_deg": ang,
           "phase_axes_deg": {"A": angA, "B": angB, "C": angC}, "phase_shift_AB_deg": shift_AB,
           "phase_sequence": sequence, "coil_sides": counts, "cogging_lcm_Q_2p": Q * 2 * p // math.gcd(Q, 2 * p),
           "parallel_paths": parallel_paths, "max_parallel_paths": sections, "parallel_paths_ok": bool(paths_ok),
           "type": "integer-slot" if abs(q - round(q)) < 1e-12 and q >= 1 else "fractional-slot",
           "note": "ideal star-of-slots factors: AC resistance, losses, NVH and manufacturability are not approved by "
                   "a winding factor; feasibility, balance, phase sequence and parallel-path symmetry are checked"}
    if turns_per_coil is not None:
        n_series = Q * turns_per_coil / (m * parallel_paths)          # Q coils (double layer), Q/m per phase
        out.update({"turns_per_coil": turns_per_coil, "N_series": n_series, "N_eff": kw1 * n_series})
    if not paths_ok:
        out["note"] += f"; {parallel_paths} parallel paths are not symmetric (divisors of {sections} only)"
    empty = [ph for ph, v in sides.items() if not v]
    if empty:
        out["note"] += f"; phase(s) {', '.join(empty)} get no coil side: not a three-phase winding"
    out["valid"] = bool(out["feasible"] and out["balanced"] and paths_ok and kw1 > 1e-9)
    out["identity"] = winding_identity({"Q": Q, "p": p, "y": y, "parallel_paths": parallel_paths,
                                        "turns_per_coil": turns_per_coil})
    return out


def layout_problems(w: dict) -> list:
    """Why a layout cannot carry a turns scaling: infeasible slot / pole combination, unbalanced phases, asymmetric
    parallel paths, no effective turns."""
    out = []
    if not w["feasible"]:
        out.append(f"Q = {w['Q']}, p = {w['p']} is not a feasible three-phase double-layer combination")
    if not w["balanced"]:
        out.append("not a balanced three-phase winding")
    if not w["parallel_paths_ok"]:
        out.append(f"{w['parallel_paths']} parallel paths are not symmetric (divisors of {w['max_parallel_paths']})")
    if not (w.get("N_eff") or 0) > 0:
        out.append("no effective series turns (turns per coil missing or kw1 = 0)")
    return out


def effective_turns_ratio(reference: dict, candidate: dict) -> float:
    """k_N for ScalingSpec from two VALID layouts of the SAME slot / pole / pitch (turns per coil and / or parallel
    paths only): both must be feasible, balanced, with symmetric parallel paths and effective turns (review R2,
    MD-01)."""
    for key in ("Q", "p", "y"):
        if reference[key] != candidate[key]:
            raise InputValidationError(f"a {key} change is a new winding (harmonic leakage and saturation change), "
                                       "not a turns scaling: it needs a new machine map", field=key)
    if "N_eff" not in reference or "N_eff" not in candidate:
        raise InputValidationError("both layouts need turns_per_coil", field="turns_per_coil")
    for tag, w in (("reference", reference), ("candidate", candidate)):
        probs = layout_problems(w)
        if probs:
            raise InputValidationError(f"{tag} layout is not a valid winding: " + "; ".join(probs), field=tag)
    return candidate["N_eff"] / reference["N_eff"]


def winding_change(drive: DriveModel, reference: dict, candidate: dict) -> dict:
    """The gate between the winding calculator and the trade study (core, API and UI alike): a k_N is handed over only
    when both layouts are valid windings of the same Q, p, y, the pole pairs are the machine's, and - when the machine
    declares its winding - the reference IS that winding.  With no declared winding the k_N is a generic thought
    experiment and is labelled so; it is never presented as a redesign of this machine's winding."""
    refusals = []
    for tag, w in (("reference", reference), ("candidate", candidate)):
        refusals += [f"{tag}: {x}" for x in layout_problems(w)]
    for key in ("Q", "p", "y"):
        if reference[key] != candidate[key]:
            refusals.append(f"{key} differs: a new winding needs a new machine map, not a turns scaling")
    p_drive = drive.motor.pole_pairs
    if reference["p"] != p_drive:
        refusals.append(f"winding p = {reference['p']} but the active machine has p = {p_drive}: not this machine")
    declared = drive.motor.winding
    if declared is None:
        binding = "unbound"
    elif winding_identity(declared) == reference["identity"]:
        binding = "declared"
    else:
        binding = "different"
        refusals.append("the entered reference layout is not the winding the active machine declares "
                        f"(Q {declared.Q}, p {declared.p}, y {declared.y}, {declared.parallel_paths} paths, "
                        f"{declared.turns_per_coil} turns / coil)")
    k = None if refusals else effective_turns_ratio(reference, candidate)
    name = f"{candidate.get('turns_per_coil')}t/{candidate.get('parallel_paths')}a"
    if binding == "declared":
        kind = "turns / parallel-path change of the declared winding of this machine"
        spec = {"name": name, "k_turns": k, "basis": kind, "winding_from": reference["identity"],
                "winding_to": {"parallel_paths": candidate["parallel_paths"],
                               "turns_per_coil": candidate["turns_per_coil"]}}
    else:
        kind = ("generic k_N (thought experiment): the active machine declares no winding, so this is not a redesign "
                "of its winding")
        spec = {"name": name + " (generic)", "k_turns": k, "basis": kind}
    return {"sendable": not refusals, "refusals": refusals, "k_turns": k, "binding": binding, "kind": kind,
            "candidate": None if refusals else spec,
            "assumptions": ["same Q, p, y: same slot fill and copper area (conductor area x 1 / k_N)",
                            "same materials and temperatures", "terminal phase current scales 1 / k_N (same ampere-turns)"]}


# --------------------------------------------------------------------------------------------- concept sizing

def concept_sizing(T_Nm: float, sigma_kPa: tuple, aspect_L_over_D: tuple, n_max_rpm: float | None = None,
                   tip_speed_limit_m_s: float | None = None) -> dict:
    """Rotor volume from a declared air-gap shear stress: T = sigma * 2 pi r^2 L = 2 sigma V_r; D, L from the aspect
    ratio.  sigma and the aspect ratio are ranges (declared, e.g. by cooling class) - the result is a concept
    envelope, not a rating (no thermal, demag or mechanical approval)."""
    T = abs(_finite("T_Nm", T_Nm))
    if T <= 0:
        raise InputValidationError("torque must be non-zero", field="T_Nm")
    rows = []
    for s in sigma_kPa:
        s = _finite("sigma_kPa", s) * 1e3
        if s <= 0:
            raise InputValidationError("shear stress must be > 0", field="sigma_kPa")
        for a in aspect_L_over_D:
            a = _finite("aspect", a)
            if a <= 0:
                raise InputValidationError("aspect ratio must be > 0", field="aspect")
            Vr = T / (2 * s)
            D = (4 * Vr / (math.pi * a)) ** (1.0 / 3.0)
            L = a * D
            row = {"sigma_kPa": s / 1e3, "L_over_D": a, "rotor_volume_L": Vr * 1e3, "D_rotor_mm": D * 1e3,
                   "L_stack_mm": L * 1e3, "check_T_Nm": s * 2 * math.pi * (D / 2) ** 2 * L}
            if n_max_rpm:
                v = math.pi * D * n_max_rpm / 60
                row["tip_speed_m_s"] = v
                if tip_speed_limit_m_s:
                    row["tip_speed_ok"] = v <= tip_speed_limit_m_s
            rows.append(row)
    d_max = (tip_speed_limit_m_s * 60 / (math.pi * n_max_rpm) * 1e3) if (n_max_rpm and tip_speed_limit_m_s) else None
    return {"rows": rows, "T_Nm": T, "n_max_rpm": n_max_rpm, "tip_speed_limit_m_s": tip_speed_limit_m_s,
            "D_max_tip_mm": d_max, "meaning": "concept envelope from declared shear stress and aspect ratio; "
                                                "rotor mechanics, thermal, demagnetisation and losses need their own "
                                                "evidence (external / later phases)"}
