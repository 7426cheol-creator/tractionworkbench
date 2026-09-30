"""What a loss-limited margin is worth: its loss budget and the parameter changes that erase it.

A FEASIBLE requirement whose policy capability is limited by a DC source limit (power or current) rests on the loss
models: the margin is decided by the declared inverter loss (often the quadratic surrogate a0 + a2 I^2), the
speed-only rotational / iron loss and Rs.  Their plausible error can exceed the margin (engineering review 2 of
63a2b61, F-03: 2.56 N*m at 12 000 rpm is about 3.2 kW of loss).  This analysis states it automatically, next to the
loss-model kinds:

* loss budget - the DC slack at the witness in watts (extra loss the operating point can take before the DC limit
  binds; current limits as slack x Vdc);
* sensitivity - one-sided finite differences of the policy capability to each parameter the margin depends on (the
  adverse direction; +10 % for loss parameters and Rs, +/-5 % for psi_PM, Ld, Lq, +0.01 for the voltage reserve), and
  the linear break-even change margin / sensitivity.

It never changes a verdict: the declared error budget (``analysis.error_budget``) is the decision layer; this is
the automatic estimate that tells which declaration matters.  The break-even is a linear estimate from one step.
"""

from __future__ import annotations

import math
from dataclasses import replace

from ..models.flux import ConstantFluxModel
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator

DC_LIMITS = ("DC_DISCHARGE_POWER", "DC_DISCHARGE_CURRENT", "DC_CHARGE_POWER", "DC_CHARGE_CURRENT")
LOSS_STEP = 0.10            # relative step of loss parameters and Rs (adverse: +)
MAG_STEP = 0.05             # relative step of psi_PM, Ld, Lq (both signs; the adverse one is kept)
RV_STEP = 0.01              # absolute step of the voltage reserve fraction (adverse: +)


def is_loss_limited(active) -> bool:
    return any(a in DC_LIMITS for a in active or ())


def _variants(drive) -> list[tuple[str, str, float, str, list]]:
    """(key, label, value, unit, [(relative or absolute step, drive with the step)])."""
    m, inv = drive.motor, drive.inverter
    out = []

    def motor(**kw):
        return replace(drive, motor=replace(m, **kw))

    def inverter(**kw):
        return replace(drive, inverter=replace(inv, **kw))
    out.append(("Rs", "Rs", m.Rs_ohm, "ohm", [(LOSS_STEP, motor(Rs_ohm=m.Rs_ohm * (1 + LOSS_STEP)))]))
    if inv.module_loss is None and inv.loss is not None:
        ls = inv.loss
        if ls.offset_W > 0:
            out.append(("a0", "inverter loss a0", ls.offset_W, "W",
                        [(LOSS_STEP, inverter(loss=replace(ls, offset_W=ls.offset_W * (1 + LOSS_STEP))))]))
        if ls.ipk2_coeff_W_per_A2 > 0:
            out.append(("a2", "inverter loss a2", ls.ipk2_coeff_W_per_A2, "W/A^2",
                        [(LOSS_STEP, inverter(loss=replace(ls, ipk2_coeff_W_per_A2=ls.ipk2_coeff_W_per_A2
                                                           * (1 + LOSS_STEP))))]))
    rot = m.rotational_loss
    if rot is not None:
        if rot.viscous_Nm_per_rad_s > 0:
            out.append(("b", "rotational / iron loss b", rot.viscous_Nm_per_rad_s, "N*m*s/rad",
                        [(LOSS_STEP, motor(rotational_loss=replace(
                            rot, viscous_Nm_per_rad_s=rot.viscous_Nm_per_rad_s * (1 + LOSS_STEP))))]))
        if rot.quadratic_Nm_per_rad2_s2 > 0:
            out.append(("c", "rotational / iron loss c", rot.quadratic_Nm_per_rad2_s2, "N*m*s^2/rad^2",
                        [(LOSS_STEP, motor(rotational_loss=replace(
                            rot, quadratic_Nm_per_rad2_s2=rot.quadratic_Nm_per_rad2_s2 * (1 + LOSS_STEP))))]))
    rv = inv.voltage.reserve_fraction
    if rv + RV_STEP < 1.0:
        out.append(("r_v", "voltage reserve", rv, "", [(RV_STEP, inverter(voltage=replace(
            inv.voltage, reserve_fraction=rv + RV_STEP)))]))
    if isinstance(m.flux, ConstantFluxModel):
        f = m.flux
        for key, label, attr, unit in (("psi", "PM flux linkage", "psi_pm_Wb", "Wb"), ("Ld", "Ld", "Ld_H", "H"),
                                       ("Lq", "Lq", "Lq_H", "H")):
            v = getattr(f, attr)
            if v > 0:
                out.append((key, label, v, unit, [(s, motor(flux=replace(f, **{attr: v * (1 + s)})))
                                                  for s in (MAG_STEP, -MAG_STEP)]))
    return out


def loss_limited_margin(drive, scenario, direction: int, target_Nm: float, capability, witness_point,
                        settings: NumericalSettings = DEFAULT_SETTINGS) -> dict | None:
    """The loss budget and parameter break-even of a loss-limited FEASIBLE margin; None when not loss-limited."""
    if capability is None or not capability.accepted or not is_loss_limited(capability.active_constraints):
        return None
    C = capability.value_Nm
    margin = direction * (C - target_Nm)
    if not math.isfinite(C) or margin < 0:
        return None
    w_m = scenario.speed_rpm * 2.0 * math.pi / 60.0
    budget, basis = None, ""
    if witness_point is not None:
        slacks = []
        for c in witness_point.constraints:
            if c.name in ("DC_DISCHARGE_POWER", "DC_CHARGE_POWER") and math.isfinite(c.slack):
                slacks.append((c.slack, f"{c.name} slack"))
            elif c.name in ("DC_DISCHARGE_CURRENT", "DC_CHARGE_CURRENT") and math.isfinite(c.slack):
                slacks.append((c.slack * scenario.Vdc_V, f"{c.name} slack x Vdc"))
        if slacks:
            budget, basis = min(slacks)
    inv = drive.inverter
    rot = drive.motor.rotational_loss
    kinds = [("inverter loss: datasheet module model" if inv.module_loss is not None else
              "inverter loss: not modelled" if inv.loss is None else
              "inverter loss: quadratic surrogate a0 + a2 I^2 (no Vdc / fsw / Tj dependence)"),
             "rotational / iron loss: " + ("not modelled" if rot is None else "speed-only b w + c w|w| (no load / "
                                                                                "flux dependence)")]
    params = []
    # the perturbed capabilities sit next to C: a coarser scan (the transitions are still bisected) keeps it cheap
    coarse = replace(settings, capability_scan_samples=min(settings.capability_scan_samples, 41))
    for key, label, value, unit, steps in _variants(drive):
        worst = None
        for step, d2 in steps:
            try:
                c2 = policy_capability(PolicyEvaluator(d2, scenario, coarse), direction)
            except Exception:  # noqa: BLE001 - a failed variant is not a sensitivity
                continue
            if not c2.accepted or not math.isfinite(c2.value_Nm):
                continue
            dC = direction * (c2.value_Nm - C)            # capability change toward failure is negative
            if worst is None or dC < worst[1]:
                worst = (step, dC)
        if worst is None:
            continue
        step, dC = worst
        # break-even in the adverse direction (the step that lowered the capability): margin / (capability loss
        # per unit change); none when no step lowers it
        brk = None if dC >= 0 else margin * abs(step) / -dC
        magnetic = key in ("psi", "Ld", "Lq")
        if brk is not None and magnetic and brk > 0.5:
            brk_s, text = None, f"{label}: not erased within +/-50 % (linear estimate)"
        elif brk is None:
            brk_s, text = None, f"{label}: no adverse effect for a +/-{100 * abs(step):g} % step" if key != "r_v" \
                else f"{label}: no adverse effect"
        else:
            brk_s = math.copysign(brk, step)
            sign = "+" if step > 0 else "-"
            text = (f"voltage reserve +{brk:.2g} (from {value:g})" if key == "r_v" else
                    f"inverter loss offset a0 +{brk * value:.3g} W" if key == "a0" else
                    f"{label} {sign}{100 * brk:.3g} %")
        params.append({"key": key, "label": label, "value": value, "unit": unit, "step": step,
                       "step_kind": "absolute" if key == "r_v" else "relative", "capability_change_Nm": dC,
                       "break_even": brk_s, "text": text})
    # rank by the relative size of the change (the voltage reserve as its share of the voltage budget)
    params.sort(key=lambda p: math.inf if p["break_even"] is None else
                abs(p["break_even"]) / (1.0 - p["value"] if p["key"] == "r_v" else 1.0))
    erased = [p["text"] for p in params if p["break_even"] is not None][:3]
    return {"margin_Nm": margin, "capability_Nm": C, "active": list(capability.active_constraints),
            "loss_model_basis": {"inverter": None if inv.loss is None else inv.loss.kind,
                                 "rotational": None if rot is None else rot.basis},
            "loss_budget_W": budget, "loss_budget_basis": basis, "Nm_per_kW": (1e3 / abs(w_m)) if w_m else None,
            "loss_models": kinds, "parameters": params, "erased_by": erased,
            "method": (f"one-sided finite differences of the policy capability (+{100 * LOSS_STEP:g} % loss "
                       f"parameters and Rs, +/-{100 * MAG_STEP:g} % psi_PM, Ld, Lq, +{RV_STEP:g} voltage reserve); "
                       f"break-even = margin / sensitivity (linear estimate from one step)")}
