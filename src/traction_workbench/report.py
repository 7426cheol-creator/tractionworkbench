"""Human-readable rendering of decision records (Markdown).

Displayed numbers are rounded for reading; every decision was taken on the
full-precision values with the tolerances recorded in the record.
"""

from __future__ import annotations

import math


def fmt(x, digits: int = 6, unit: str = "") -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        if isinstance(x, float) and math.isnan(x):
            return "NaN"
        if isinstance(x, float) and math.isinf(x):
            return "∞" if x > 0 else "−∞"
        if x == 0:
            s = "0"
        elif abs(x) >= 1e6 or abs(x) < 1e-3:
            s = f"{x:.{max(digits - 1, 1)}e}"
        else:
            s = f"{x:.{digits}g}"
        return f"{s} {unit}".rstrip()
    return str(x)


BADGE = {"FEASIBLE": "✅ FEASIBLE", "INFEASIBLE": "⛔ INFEASIBLE", "UNKNOWN": "❔ UNKNOWN"}
VERDICT = {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL", "UNKNOWN": "UNKNOWN"}


def _claims_table(claims) -> list[str]:
    out = ["| Claim | Status | Reasons | Evidence (first items) |", "|---|---|---|---|"]
    for c in claims:
        ev = "<br>".join(f"*{e.kind.value}*: {e.summary}" for e in c.evidence[:3]) or "—"
        rs = ", ".join(r.value for r in c.reasons) or "—"
        q = f"<br>_{'; '.join(c.qualifiers)}_" if c.qualifiers else ""
        out.append(f"| `{c.name}` | {BADGE[c.status.value]}{q} | {rs} | {ev} |")
    return out


def _point_section(pt) -> list[str]:
    if pt is None:
        return ["_No operating point (no electrical solution was selected)._"]
    lines = [
        "| id | iq | Ipk | I rms | Te | Tshaft | τrot |",
        "|---|---|---|---|---|---|---|",
        f"| {fmt(pt.id_A, 7, 'A')} | {fmt(pt.iq_A, 7, 'A')} | {fmt(pt.i_peak_A, 7, 'A')} | {fmt(pt.i_phase_rms_A, 7, 'A')} "
        f"| {fmt(pt.Te_Nm, 7, 'N·m')} | {fmt(pt.Tshaft_Nm, 7, 'N·m')} | {fmt(pt.tau_rot_Nm, 5, 'N·m')} |",
        "",
        f"RMS: {pt.rms_interpretation}. Currents/voltages are fundamental phase-peak dq values.",
        "",
        "**Voltage budget (phase peak)**",
        "",
        "| hardware ceiling Vdc/√3 | reserve | command budget | demand |v| | remaining command margin | V_LL,rms |",
        "|---|---|---|---|---|---|",
        f"| {fmt(pt.voltage_ceiling_V, 7, 'V')} | {fmt(pt.voltage_ceiling_V - pt.voltage_budget_V, 6, 'V')} "
        f"| {fmt(pt.voltage_budget_V, 7, 'V')} | {fmt(pt.v_cmd_peak_V, 7, 'V')} | {fmt(pt.voltage_margin_V, 4, 'V')} "
        f"| {fmt(pt.v_LL_rms_V, 6, 'V')} |",
        "",
        "**Signed power balance** (P_dc > 0: source → inverter; P_shaft > 0: motor → load)",
        "",
        "| P_shaft | P_cu | P_rot | P_ac | P_inv | P_dc | I_dc,avg | mode | efficiency |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| {fmt(pt.Pshaft_W, 7, 'W')} | {fmt(pt.Pcu_W, 6, 'W')} | {fmt(pt.Prot_W, 6, 'W')} | {fmt(pt.Pac_W, 7, 'W')} "
        f"| {fmt(pt.Pinv_W, 6, 'W')} | {fmt(pt.Pdc_W, 7, 'W')} | {fmt(pt.Idc_A, 6, 'A')} | {pt.energy_mode} "
        f"| {fmt(pt.efficiency, 5)} |",
        "",
        f"Efficiency note: {pt.efficiency_note}. Power-identity residuals: "
        f"{fmt(pt.residual_pac_tem_W, 3, 'W')}, {fmt(pt.residual_pac_shaft_W, 3, 'W')}, {fmt(pt.residual_dc_W, 3, 'W')} "
        f"(tolerance {fmt(pt.identity_tolerance_W, 3, 'W')}).",
        "",
        "**Constraints (native units; slack = limit − demand for upper, demand − limit for lower)**",
        "",
        "| constraint | limit | demand | slack | state | kind |",
        "|---|---|---|---|---|---|",
    ]
    for c in pt.constraints:
        lines.append(f"| {c.name} | {fmt(c.limit, 7)} | {fmt(c.demand, 7)} | {fmt(c.slack, 5)} {c.unit} | "
                     f"{c.state} | {c.kind} |")
    if pt.pwm_ratio:
        lines += ["", f"f_sw / f_e = {fmt(pt.pwm_ratio, 4)} (reported only; no universal threshold applied)."]
    return lines


def decision_markdown(rec) -> str:
    v = rec.verdict
    L = [f"# Engineering Decision Record — {rec.requirement.req_id}", "",
         f"`{rec.record_id}` · input SHA-256 `{rec.input_sha256}`", "",
         f"> **Verdict: {VERDICT[v.status.value]}** ({v.status.value})",
         f"> Scope: {rec.verdict_scope}"]
    if v.reasons:
        L.append(f"> Reasons: {', '.join(r.value for r in v.reasons)}")
    for q in rec.qualifiers:
        L.append(f"> Qualifier: {q}")
    lay = rec.layers
    L += ["", "## Claim layers (not one boolean)", "",
          "| layer | status | meaning |", "|---|---|---|",
          f"| mathematical | {lay['mathematical']['status']} | {lay['mathematical']['meaning']} |",
          f"| model | {lay['model']['verdict']} ({lay['model']['status']}) | {lay['model']['meaning']} |",
          f"| requirement | {lay['requirement']['status']} | "
          + ("; ".join(lay['requirement']['open_items']) or "complete for this question") + " |",
          f"| qualification | {lay['qualification']['status']} | {lay['qualification']['meaning']} |",
          f"| robustness | {lay['robustness']['status']} | {lay['robustness']['meaning']} |",
          "", "Simplified sub-models: " + "; ".join(lay["qualification"]["sub_models"])]
    rob = lay["robustness"]
    if rob.get("budget"):
        L += ["", "## Margin vs the declared error budget (the model verdict is unchanged)", "",
              f"Combination: {rob.get('combination', '')}. Requirement torque: {rob.get('T_edge_basis', '')}. "
              f"Scope: {rob.get('scope', '')}.", "",
              "| source | kind | quantity | declared | basis |", "|---|---|---|---|---|"]
        for it in rob["budget"]["items"]:
            unit = it.get("unit", "N*m").replace("N*m", "N·m")
            size = (fmt(it["value"], 4, unit) if it.get("value") is not None else
                    f"{it['percent']:g} % of {it.get('percent_of', 'the model value')}")
            L.append(f"| {it['source']} | {it['kind']} | {it.get('quantity', 'torque')} | {size} | "
                     f"{it.get('basis') or 'not stated'} |")
        L += ["", "**Torque capability** (requirement torque vs the model capability)", "",
              "| condition | capability | margin | declared error | result |", "|---|---|---|---|---|"]
        for name, x in zip(rob.get("names") or [], rob.get("conditions") or []):
            L.append(f"| {name} | {fmt(x.get('capability_Nm'), 6, 'N·m')} | {fmt(x.get('margin_Nm'), 5, 'N·m')} | "
                     f"{fmt(x.get('delta_Nm'), 4, 'N·m')} | {x['status']}"
                     + (f" ({x['reason']})" if x.get("reason") else "") + " |")
        if rob.get("checks"):
            names = rob.get("names") or []
            L += ["", "**Limits at the witness** (y + Δ ≤ y_max)", "",
                  "| condition | limit | demand | limit value | margin | declared error | result |",
                  "|---|---|---|---|---|---|---|"]
            for c in rob["checks"]:
                unit = c.get("unit", "")
                L.append(f"| {names[c['condition']] if c.get('condition') is not None and names else ''} | "
                         f"{c.get('constraint') or c.get('quantity')} | {fmt(c.get('demand'), 6, unit)} | "
                         f"{fmt(c.get('limit'), 6, unit)} | {fmt(c.get('slack'), 5, unit)} | {fmt(c.get('delta'), 4, unit)} | "
                         f"{c['status']}" + (f" ({c['reason']})" if c.get("reason") else "") + " |")
    ms = rec.margin_sensitivity
    if ms:
        L += ["", "## Loss-limited margin (automatic sensitivity; the verdict is unchanged)", "",
              f"Condition {ms['condition']}: margin {fmt(ms['margin_Nm'], 3, 'N·m')} to the capability "
              f"{fmt(ms['capability_Nm'], 5, 'N·m')} limited by {', '.join(ms['active'])}."
              + ("" if ms.get("loss_budget_W") is None else
                 f" Loss budget: {fmt(ms['loss_budget_W'] / 1e3, 3, 'kW')} of additional loss at the witness reaches "
                 f"the DC limit ({ms['loss_budget_basis']}).")
              + " Loss models: " + "; ".join(ms["loss_models"]) + ".", "",
              "| parameter | value | capability change per step | break-even (linear) |", "|---|---|---|---|"]
        for p in ms["parameters"]:
            step = f"{p['step']:+g}" if p["step_kind"] == "absolute" else f"{100 * p['step']:+g} %"
            L.append(f"| {p['label']} | {p['value']:.4g} {p['unit']} | {fmt(p['capability_change_Nm'], 3, 'N·m')} "
                     f"({step}) | {p['text']} |")
        L += ["", f"Method: {ms['method']}."]
    r = rec.requirement
    L += ["", "## Requirement (original wording, unchanged)", "", f"> {r.text}", "",
          "| item | interpretation |", "|---|---|",
          f"| quantity / port | shaft torque at the motor shaft (not electromagnetic torque) |",
          f"| target | {fmt(r.target_Nm, 6, 'N·m')} ({r.operator}{'' if r.operator == 'achieve' else f' ±{r.band_Nm:g} N·m'}) |",
          f"| speed | {fmt(r.speed_rpm, 6, 'rpm')} mechanical |",
          f"| DC terminal voltage | " + (f"{r.Vdc_V[0]:g}…{r.Vdc_V[1]:g} V for all values (examined at sampled points)"
                                        if r.is_range else f"{r.Vdc_V:g} V (single point)") + " |",
          f"| duration | {r.duration_text()} |",
          "| quantifiers | " + "; ".join(f"{k}: {v}" for k, v in lay["requirement"].get("quantifiers", {}).items())
          + " |"]
    if r.exclusions:
        L.append(f"| exclusions | {'; '.join(r.exclusions)} |")
    for cr in rec.conditions:
        sc = cr.scenario
        L += ["", f"## Condition: n = {sc.speed_rpm:g} rpm, Vdc = {sc.Vdc_V:g} V", "",
              f"**At this condition: {BADGE[cr.requirement_claim.status.value]}** — {cr.requirement_claim.detail}", ""]
        prim = cr.primary
        claims = list(prim.claims) + ([cr.duration] if cr.duration is not None else [])
        L += _claims_table(claims)
        L += ["", f"### Operating point (minimum-current policy, T = {fmt(cr.primary_torque_Nm, 6, 'N·m')})", ""]
        L += _point_section(prim.point)
        rc = cr.rejected_centre
        if rc is not None:
            L += ["", f"_Band centre {fmt(rc.T_request_Nm, 6, 'N·m')}: {rc.policy_claim.status.value} - rejected "
                      f"candidate, diagnostic only (P_dc = {fmt(None if rc.point is None else rc.point.Pdc_W, 7, 'W')}); "
                      f"the requirement is answered at the witness above._"]
        if prim.active_loss_candidate is not None:
            a = prim.active_loss_candidate
            L += ["", f"_Active-loss candidate (outside the default policy, not adopted): id = {fmt(a.id_A, 7, 'A')}, "
                      f"iq = {fmt(a.iq_A, 7, 'A')}, P_dc = {fmt(a.Pdc_W, 7, 'W')}._"]
        if cr.witness_torque_Nm is not None and cr.witness_solution is not None \
                and cr.witness_solution is not cr.solution:
            L += ["", f"_Band witness: {fmt(cr.witness_torque_Nm, 6, 'N·m')} - the static, DC and duration parts are "
                      f"evaluated at this one witness._"]
        cap = cr.capability
        if cap is not None and cap.value_Nm is not None and not cap.accepted:
            L += ["", "### Capability at this condition", "",
                  "- Policy capability not established (diagnostic only): " + "; ".join(cap.gate_messages)]
        elif cap is not None and cap.value_Nm is not None:
            L += ["", "### Capability at this condition", "",
                  f"- Policy capability ({'max' if cap.direction > 0 else 'min'}): **{fmt(cap.value_Nm, 7, 'N·m')}** "
                  f"({'certified, bound ' + fmt(cap.bound_Nm, 7, 'N·m') if cap.certified else 'sampled scan'}; "
                  f"gap tolerance {fmt(cap.gap_tolerance_Nm, 3, 'N·m')})",
                  f"- Torque margin vs requirement: **{fmt(cr.torque_margin_Nm, 5, 'N·m')}**",
                  f"- Limited by: {', '.join(cap.active_constraints) or '—'}"]
            for n in cap.notes[1:]:
                L.append(f"- {n}")
        if prim.screens:
            L += ["", "### Necessary-condition screens", ""]
            for s in prim.screens:
                L.append(f"- {'**violated**' if s.violated else 'passed'} — {s.statement} _({s.scope})_")
    L += ["", "## Limiting factors", ""] + ([f"- {x}" for x in rec.limiting_factors] or ["- none identified"])
    L += ["", "## Next actions", ""] + ([f"- {x}" for x in rec.next_actions] or ["- none"])
    L += ["", "## Not evaluated by this record", ""] + [f"- {x}" for x in rec.unevaluated]
    L += ["", "## Assumptions", ""] + [f"- {x}" for x in rec.assumptions]
    p = rec.drive.provenance
    L += ["", "## Model and provenance", "",
          f"- Drive `{rec.drive.drive_id}` rev {rec.drive.revision}, fidelity {rec.drive.fidelity.value}",
          f"- Data origin: {p.origin.value}; source: {p.source}; revision {p.revision}",
          f"- Validation status: {p.validation_status}"]
    if p.sha256:
        L.append(f"- Source SHA-256: `{p.sha256}`")
    L += ["", "## Reproducibility", "",
          f"- Input snapshot SHA-256: `{rec.input_sha256}` (requirement, drive, scenario, ratings, settings, version)",
          "- Displayed values are rounded; decisions use full precision with the recorded tolerances.", ""]
    return "\n".join(L)
