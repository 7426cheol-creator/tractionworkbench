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
    r = rec.requirement
    L += ["", "## Requirement (original wording, unchanged)", "", f"> {r.text}", "",
          "| item | interpretation |", "|---|---|",
          f"| quantity / port | shaft torque at the motor shaft (not electromagnetic torque) |",
          f"| target | {fmt(r.target_Nm, 6, 'N·m')} ({r.operator}{'' if r.operator == 'achieve' else f' ±{r.band_Nm:g} N·m'}) |",
          f"| speed | {fmt(r.speed_rpm, 6, 'rpm')} mechanical |",
          f"| DC terminal voltage | " + (f"{r.Vdc_V[0]:g}…{r.Vdc_V[1]:g} V for all values (examined at sampled points)"
                                        if r.is_range else f"{r.Vdc_V:g} V (single point)") + " |",
          f"| duration | {r.duration_text()} |"]
    if r.exclusions:
        L.append(f"| exclusions | {'; '.join(r.exclusions)} |")
    for cr in rec.conditions:
        sc = cr.scenario
        L += ["", f"## Condition: n = {sc.speed_rpm:g} rpm, Vdc = {sc.Vdc_V:g} V", "",
              f"**At this condition: {BADGE[cr.requirement_claim.status.value]}** — {cr.requirement_claim.detail}", ""]
        claims = list(cr.solution.claims) + ([cr.duration] if cr.duration is not None else [])
        L += _claims_table(claims)
        L += ["", "### Operating point (minimum-current policy)", ""]
        L += _point_section(cr.solution.point)
        if cr.solution.active_loss_candidate is not None:
            a = cr.solution.active_loss_candidate
            L += ["", f"_Active-loss candidate (outside the default policy, not adopted): id = {fmt(a.id_A, 7, 'A')}, "
                      f"iq = {fmt(a.iq_A, 7, 'A')}, P_dc = {fmt(a.Pdc_W, 7, 'W')}._"]
        cap = cr.capability
        if cap is not None and cap.value_Nm is not None:
            L += ["", "### Capability at this condition", "",
                  f"- Policy capability ({'max' if cap.direction > 0 else 'min'}): **{fmt(cap.value_Nm, 7, 'N·m')}** "
                  f"({'certified, bound ' + fmt(cap.bound_Nm, 7, 'N·m') if cap.certified else 'sampled scan'}; "
                  f"gap tolerance {fmt(cap.gap_tolerance_Nm, 3, 'N·m')})",
                  f"- Torque margin vs requirement: **{fmt(cr.torque_margin_Nm, 5, 'N·m')}**",
                  f"- Limited by: {', '.join(cap.active_constraints) or '—'}"]
            for n in cap.notes[1:]:
                L.append(f"- {n}")
        if cr.solution.screens:
            L += ["", "### Necessary-condition screens", ""]
            for s in cr.solution.screens:
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
