"""Requirement sets (engineering review 6198099, user features 1 - 3).

Many shaft-torque requirements are judged on ONE product with the same conditions and the same evidence; each gets
one row - verdict, torque-capability margin, limiting cause, the class of an open answer and the next data that
could change it - and candidate design changes are re-evaluated against EVERY requirement, so an improvement on one
requirement that breaks another is shown, never averaged away (no weighted score, no cost claim without cost data).

An open answer is classed by its cause, because each cause is closed by a different kind of work:

* missing input        - a declaration (usually cheap: state the value);
* applicability        - evidence not bound to this product / no rating for the question (documents);
* continuous range     - examined at samples only (a certificate or a narrower range, not a denser grid);
* outside the model    - the question leaves the model data (new data or a model extension);
* numerically open     - at a boundary within tolerance / unresolved (analysis);
* conflicting evidence - equally authoritative sources disagree (declare the authority);
* policy limitation    - the minimum-current policy limits here (another policy needs its own evaluation).

A FAIL is either a proven violation or 'not rated' (outside a validated rating - not physical impossibility).
"""

from __future__ import annotations

import copy
import csv
import io
import math

from .analysis.variation import PARAMETERS, apply, describe_parameter
from .decision import evaluate_requirement
from .errors import InputValidationError
from .models.components import DriveModel
from .scenario import DcSourceLimits, Scenario
from .settings import NumericalSettings
from .status import Reason

R = Reason
REASON_CLASSES = (
    ("missing_input", "입력 결측", "missing input", "declaration",
     (R.MISSING_INPUT, R.REQUIREMENT_INCOMPLETE, R.INVALID_INPUT),
     "state the missing input named in the actions (a declaration usually costs nothing)"),
    ("applicability", "적용성 미확인·근거 없음", "applicability unconfirmed / no evidence", "documents",
     (R.APPLICABILITY_UNCONFIRMED, R.UNVALIDATED_DURATION, R.SCREENING_ONLY),
     "bind the evidence to this product and its conditions, or supply evidence for this exact question (e.g. a "
     "rating of the requirement's own duration)"),
    ("continuous_range", "연속 범위 미입증", "continuous range not proven", "analysis",
     (R.SAMPLED_COVERAGE,),
     "prove the range (a monotonicity certificate whose conditions hold) or narrow it; a denser grid is not a proof"),
    ("outside_model", "모델 범위 밖", "outside the model", "new data",
     (R.OUTSIDE_MODEL_DOMAIN, R.OUTSIDE_ALLOWED_OPERATING_DOMAIN, R.OUT_OF_SCOPE, R.COUPLED_MODEL_REQUIRED),
     "extend the model data (coverage, temperature planes, domain) or state conditions inside it"),
    ("numerical", "수치 미해결·경계", "numerically open / at a boundary", "analysis",
     (R.NUMERICAL_UNRESOLVED, R.BOUNDARY_WITHIN_TOLERANCE, R.UNCERTAINTY_OVERLAP, R.BOUND_INCONCLUSIVE),
     "the answer sits within the numerical tolerance of a boundary: tighten the tolerance or examine the boundary"),
    ("conflict", "근거 충돌", "conflicting evidence", "documents", (R.CONFLICTING_EVIDENCE,),
     "declare which evidence is authoritative (priority); the list order never decides"),
    ("policy", "정책 한계", "policy limitation", "analysis", (R.POLICY_LIMITATION,),
     "the minimum-current policy limits here; another control policy needs its own evaluation"),
)
CLASS_ORDER = [c[0] for c in REASON_CLASSES]


def classify(status: str, reasons) -> dict:
    """Verdict class of one requirement: pass / violation / not_rated / one of the open-answer classes."""
    reasons = [Reason(r) if not isinstance(r, Reason) else r for r in reasons]
    if status == "FEASIBLE":
        return {"class": "pass", "label_ko": "만족", "label_en": "met", "effort": None, "hint": "", "classes": []}
    if status == "INFEASIBLE":
        if reasons and all(r is R.RATING_NOT_MET for r in reasons):
            return {"class": "not_rated", "label_ko": "정격 밖 (물리적 불가능 증명 아님)",
                    "label_en": "not rated (not a proof of physical impossibility)", "effort": "documents",
                    "hint": "a rating covering the request (or a qualified thermal model) would be needed",
                    "classes": ["not_rated"]}
        return {"class": "violation", "label_ko": "증명된 위반", "label_en": "proven violation", "effort": None,
                "hint": "a design or requirement change is needed (see the limiting cause)", "classes": ["violation"]}
    hits = [c for c in REASON_CLASSES if any(r in c[4] for r in reasons)]
    if not hits:
        return {"class": "open", "label_ko": "미확정", "label_en": "open", "effort": None, "hint": "", "classes": []}
    key, ko, en, effort, _members, hint = hits[0]
    # an open answer can have several causes: closing only the primary one does not settle it
    return {"class": key, "label_ko": ko, "label_en": en, "effort": effort, "hint": hint,
            "classes": [c[0] for c in hits], "hints": [c[5] for c in hits]}


# constraint that binds -> the candidate parameters that relax it (Vdc is each requirement's own condition)
LEVERS = {"VOLTAGE": ("voltage_reserve_fraction",), "CURRENT": ("I_peak_max_A",),
          "DC_DISCHARGE_POWER": ("discharge_power_max_W",), "DC_CHARGE_POWER": ("charge_power_max_W",),
          "DC_DISCHARGE_CURRENT": ("discharge_current_max_A",), "DC_CHARGE_CURRENT": ("charge_current_max_A",),
          "ID_MIN": ("id_min_A",)}


def binding_constraints(rec) -> list[str]:
    """Constraints violated or active at the requirement's witness points and at the capability witness."""
    names = []
    for c in rec.conditions:
        pt = c.primary.point
        if pt is not None:
            names += [x.name for x in pt.violations()] + [x.name for x in pt.active()]
        if c.capability is not None and c.capability.accepted:
            names += list(c.capability.active_constraints)
    return list(dict.fromkeys(names))


def levers(rec) -> list[dict]:
    """What could be changed for this requirement: the parameters that relax a binding constraint (a candidate
    must still be re-judged against EVERY requirement - see ``evaluate_candidates``)."""
    out, seen = [], set()
    for name in binding_constraints(rec):
        for par in LEVERS.get(name, ()):
            if par not in seen:
                seen.add(par)
                out.append({"constraint": name, **describe_parameter(par)})
    return out


def _w(speed_rpm: float) -> float:
    return speed_rpm * 2.0 * math.pi / 60.0


def _exceeds(need: float, limit: float, floor: float) -> bool:
    s = NumericalSettings()
    return need > limit + max(floor, s.constraint_rel_tol * abs(limit))


def spec_check(req, limits: DcSourceLimits) -> dict:
    """Model-free necessary condition from the customer numbers alone (the entry point without a motor model).

    At ONE operating point - this requirement's own torque and speed, never the maximum torque of one requirement
    times the maximum speed of another - motoring needs P_dc >= P_shaft = T*omega (losses are never negative).  A
    shaft power above the DC discharge-power limit, or P_shaft / Vdc above the DC discharge-current limit at the
    lowest Vdc the requirement covers, fails for ANY motor and inverter with these source limits.  Regeneration
    delivers at most |P_shaft| to the DC side, so a charge limit at or above |P_shaft| cannot bind; above it only
    losses could absorb the difference, which needs the model."""
    st = NumericalSettings()
    b = req.band_Nm if req.operator == "band" else 0.0
    t_lo, t_hi = req.target_Nm - b, req.target_Nm + b
    w = _w(req.speed_rpm)
    v_low = req.Vdc_V[0] if req.is_range else req.Vdc_V
    base = {"P_shaft_min_W": None, "Vdc_low_V": v_low, "checks": []}
    if w == 0.0:
        return {**base, "status": "not_applicable", "text": "standstill: no shaft power, no model-free power bound"}
    p = sorted((t_lo * w, t_hi * w))
    if p[0] <= 0.0 <= p[1]:
        return {**base, "status": "not_applicable",
                "text": "the band contains zero shaft power: no model-free power bound"}
    motoring = p[0] > 0.0
    pmin = p[0] if motoring else -p[1]           # the least shaft-power magnitude the requirement accepts
    base["P_shaft_min_W"] = pmin if motoring else -pmin
    checks = []
    if motoring:
        pl, il = limits.discharge_power_max_W, limits.discharge_current_max_A
        if pl is not None and math.isfinite(pl):
            checks.append({"limit": "discharge_power_max_W", "need": pmin, "limit_value": pl,
                           "holds": not _exceeds(pmin, pl, st.power_abs_tol_W)})
        if il is not None and math.isfinite(il):
            checks.append({"limit": "discharge_current_max_A", "need": pmin / v_low, "limit_value": il,
                           "holds": not _exceeds(pmin / v_low, il, st.current_abs_tol_A)})
        undeclared = [n for n, x in (("discharge_power_max_W", pl), ("discharge_current_max_A", il)) if x is None]
        if any(not c["holds"] for c in checks):
            bad = [c for c in checks if not c["holds"]]
            return {**base, "status": "violated", "checks": checks, "undeclared": undeclared,
                    "text": "; ".join(f"{c['limit']}: needs at least {c['need']:.6g} > {c['limit_value']:.6g}"
                                      for c in bad) + " - fails for any drive with these source limits (P_dc >= "
                                                      "P_shaft when motoring)"}
        return {**base, "status": "necessary_ok", "checks": checks, "undeclared": undeclared,
                "text": (f"P_shaft {pmin / 1e3:.4g} kW within the declared DC discharge limits (necessary only; "
                         f"the model decides)" if checks else "no DC discharge limit declared: nothing to check")}
    pl, il = limits.charge_power_max_W, limits.charge_current_max_A
    if pl is not None and math.isfinite(pl):
        checks.append({"limit": "charge_power_max_W", "need": pmin, "limit_value": pl,
                       "holds": not _exceeds(pmin, pl, st.power_abs_tol_W)})
    if il is not None and math.isfinite(il):
        checks.append({"limit": "charge_current_max_A", "need": pmin / v_low, "limit_value": il,
                       "holds": not _exceeds(pmin / v_low, il, st.current_abs_tol_A)})
    if any(not c["holds"] for c in checks):
        return {**base, "status": "needs_model", "checks": checks,
                "text": f"regeneration: |P_shaft| {pmin / 1e3:.4g} kW exceeds a charge limit - met only if losses "
                        f"absorb the difference (the model decides; raising losses on purpose is not an "
                        f"energy-recovering policy)"}
    return {**base, "status": "necessary_ok", "checks": checks,
            "text": "regeneration: |P_dc| <= |P_shaft| stays within the declared charge limits" if checks else
                    "no DC charge limit declared: nothing to check"}


def interpretation(req) -> dict:
    """How the requirement is read, before any calculation (port, torque definition, speed kind, Vdc port and
    quantifier, operator meaning, duration, stated temperatures)."""
    op = ("achieve: the requested shaft torque is delivered at this speed and Vdc (minimum-current policy)"
          if req.operator == "achieve" else
          f"band: SOME shaft torque within {req.target_Nm:g} +/- {req.band_Nm:g} N*m is feasible at this point "
          f"(existence - not control accuracy and not tracking over the whole band)")
    vdc = (f"Vdc {req.Vdc_V[0]:g}..{req.Vdc_V[1]:g} V for ALL values (inverter DC terminal)" if req.is_range else
           f"Vdc {req.Vdc_V:g} V (inverter DC terminal)")
    dur = ("static (no duration stated: the duration aspect is not decided)" if req.duration_s is None else
           f"{req.duration_text()} from {req.initial_state or 'an unstated initial state'}")
    temps = ", ".join(f"{k} {v:g} degC" for k, v in (("coolant", req.coolant_temp_C), ("magnet", req.magnet_temp_C),
                                                      ("winding", req.winding_temp_C)) if v is not None)
    return {"torque": f"shaft torque {req.target_Nm:g} N*m at the motor shaft", "operator": op,
            "speed": f"{req.speed_rpm:g} rpm mechanical", "Vdc": vdc, "duration": dur,
            "temperatures": temps or "no temperature stated",
            "text": f"{req.target_Nm:g} N*m shaft @ {req.speed_rpm:g} rpm (mechanical), {vdc}; {op}; {dur}"
                    + (f"; {temps}" if temps else "")}


def summarize(rec, limits: DcSourceLimits | None = None) -> dict:
    """One row of the requirement-set table from a decision record (object)."""
    v = rec.verdict
    cls = classify(v.status.value, v.reasons)
    margins = [c.torque_margin_Nm for c in rec.conditions if c.torque_margin_Nm is not None]
    req = rec.requirement
    lay = rec.layers
    next_data = list(cls.get("hints") or ([cls["hint"]] if cls["hint"] else [])) + list(dict.fromkeys(rec.next_actions))
    row = {"id": req.req_id, "text": req.text, "torque_Nm": req.target_Nm, "speed_rpm": req.speed_rpm,
           "Vdc_V": list(req.Vdc_V) if req.is_range else req.Vdc_V,
           "duration": None if req.duration_s is None else req.duration_text(),
           "status": v.status.value, "verdict": v.status.verdict, "reasons": [r.value for r in v.reasons],
           "class": cls["class"], "classes": cls["classes"], "class_label_ko": cls["label_ko"],
           "class_label_en": cls["label_en"], "effort": cls["effort"],
           "margin_Nm": None if not margins else min(margins),
           "limiting": rec.limiting_factors[0] if rec.limiting_factors else "",
           "limiting_all": list(rec.limiting_factors), "levers": levers(rec),
           "next_data": next_data[:5], "open_items": lay["requirement"]["open_items"],
           "qualification": lay["qualification"]["status"], "record_id": rec.record_id,
           "conditions": len(rec.conditions), "scope": rec.verdict_scope,
           "interpretation": interpretation(req)}
    if limits is not None:
        row["spec_check"] = spec_check(req, limits)
    return row


EFFORT_ORDER = {"declaration": 0, "documents": 1, "analysis": 2, "new data": 3}


def next_data_priorities(rows: list) -> list[dict]:
    """Across the set: which kind of data or work to get first.  One entry per open-answer class, with the
    requirements it holds open; ordered by the effort of that kind of work (a declaration before documents before
    analysis before new data) and then by how many requirements it could settle.  A requirement with several causes
    is listed under each - closing one cause alone does not settle it (``also_needs``)."""
    by = {}
    for r in rows:
        if r["verdict"] != "UNKNOWN":
            continue
        for k in r.get("classes") or [r["class"]]:
            by.setdefault(k, []).append(r)
    out = []
    for key, ko, en, effort, _m, hint in REASON_CLASSES:
        rs = by.get(key)
        if not rs:
            continue
        out.append({"class": key, "label_ko": ko, "label_en": en, "effort": effort, "hint": hint,
                    "requirements": [r["id"] for r in rs],
                    "settles": [r["id"] for r in rs if len(r.get("classes") or [key]) == 1],
                    "also_needs": {r["id"]: [c for c in r["classes"] if c != key] for r in rs
                                   if len(r.get("classes") or [key]) > 1}})
    out.sort(key=lambda e: (EFFORT_ORDER.get(e["effort"], 9), -len(e["requirements"])))
    return out


def _with_spec_verdict(row: dict) -> dict:
    """A model-free violation holds for EVERY drive with these source limits: it decides a requirement the model
    left open (e.g. outside the model data), and a model PASS against it would be a contradiction - never shown."""
    sc = row.get("spec_check") or {}
    if sc.get("status") != "violated":
        return row
    if row["verdict"] == "PASS":
        raise AssertionError(f"{row['id']}: the model verdict PASS contradicts the model-free necessary condition "
                             f"({sc['text']})")
    if row["verdict"] == "UNKNOWN":
        row.update(model_verdict="UNKNOWN", model_reasons=row["reasons"], verdict="FAIL", status="INFEASIBLE",
                   reasons=[Reason.NECESSARY_CONDITION_VIOLATED.value], **{
                       "class": "violation", "classes": ["violation"], "class_label_ko": "증명된 위반 (모델 무관)",
                       "class_label_en": "proven violation (model-free)", "effort": None},
                   decided_by="model-free necessary condition (spec_check)",
                   limiting=f"model-free: {sc['text']}", next_data=["a design or requirement change is needed: the "
                                                                     "customer numbers alone exceed the source limits"])
    return row


def evaluate_set(requirements, drive: DriveModel, limits: DcSourceLimits, *, ratings=(), scenario=None,
                 range_samples: int = 5, with_capability: bool = True, progress=None) -> dict:
    """Every requirement on the same drive, limits, ratings and scenario template -> rows + summary + records."""
    rows, records = [], []
    n = max(1, len(requirements))
    for i, req in enumerate(requirements):
        if progress is not None:
            progress(i / n, f"{req.req_id}")
        rec = evaluate_requirement(req, drive, scenario=scenario, source_limits=limits, ratings=tuple(ratings),
                                   range_samples=range_samples, with_capability=with_capability)
        records.append(rec)
        rows.append(_with_spec_verdict(summarize(rec, limits)))
    summary = {"PASS": sum(r["verdict"] == "PASS" for r in rows), "FAIL": sum(r["verdict"] == "FAIL" for r in rows),
               "UNKNOWN": sum(r["verdict"] == "UNKNOWN" for r in rows), "total": len(rows),
               "by_class": {k: sum(r["class"] == k for r in rows) for k in
                            ["pass", "violation", "not_rated"] + CLASS_ORDER + ["open"]}}
    return {"rows": rows, "summary": summary, "records": records, "priorities": next_data_priorities(rows),
            "drive": {"drive_id": drive.drive_id, "revision": drive.revision, "fidelity": drive.fidelity.value,
                      "origin": drive.provenance.origin.value},
            "limits": limits.describe(),
            "note": "one product, one set of conditions and evidence for every requirement; a verdict is a model "
                    "verdict for this data (qualification is a separate layer)"}


CANDIDATE_KINDS = ("hardware", "design", "boundary", "data", "diagnostic")


def candidate_changes(changes: dict) -> list[dict]:
    out = []
    for name, value in changes.items():
        if name not in PARAMETERS:
            raise InputValidationError(f"unknown candidate parameter {name!r}; known: {sorted(PARAMETERS)}",
                                       field="candidate")
        if name == "Vdc_V":
            raise InputValidationError("Vdc is a condition of each requirement, not a design candidate (state it in "
                                       "the requirements)", field="candidate")
        v = float(value)
        if not math.isfinite(v):
            raise InputValidationError(f"candidate value of {name} must be finite", field="candidate")
        out.append({**describe_parameter(name), "value": v})
    return out


def parse_candidates(text: str) -> list[dict]:
    """One candidate per line: ``name: parameter=value, parameter=value`` (a line starting with # is a comment)."""
    out = []
    for n, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise InputValidationError(f"line {n}: use 'name: parameter=value, ...'", field="candidates")
        name, rest = line.split(":", 1)
        changes = {}
        for part in rest.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise InputValidationError(f"line {n}: {part!r} has no '='", field="candidates")
            k, v = (x.strip() for x in part.split("=", 1))
            try:
                changes[k] = float(v)
            except ValueError:
                raise InputValidationError(f"line {n}: value of {k} {v!r} is not a number",
                                           field="candidates") from None
        candidate_changes(changes)                         # refuse unknown parameters and Vdc before any run
        out.append({"name": name.strip(), "changes": changes})
    return out


def evaluate_candidates(requirements, drive: DriveModel, limits: DcSourceLimits, candidates, *, ratings=(),
                        baseline: dict | None = None, range_samples: int = 5, with_capability: bool = False,
                        progress=None) -> dict:
    """Every candidate re-evaluated against EVERY requirement.  ``candidates``: [{"name", "changes": {param:
    value}}].  Per candidate: the verdict of each requirement, what improves and what worsens against the baseline
    (the unchanged design), and whether every requirement is met - no weighted sum, no ranking by a score."""
    base = baseline or evaluate_set(requirements, drive, limits, ratings=ratings, range_samples=range_samples,
                                    with_capability=with_capability)
    rank = {"PASS": 2, "UNKNOWN": 1, "FAIL": 0}
    out = []
    total = max(1, len(candidates))
    for k, cand in enumerate(candidates):
        changes = candidate_changes(cand.get("changes") or {})
        d = drive
        sc = Scenario("candidate", 0.0, 600.0, limits)       # template: speed / Vdc come from each requirement
        for ch in changes:
            d, sc = apply(d, sc, ch["parameter"], ch["value"])
        res = evaluate_set(requirements, d, sc.source_limits, ratings=ratings, range_samples=range_samples,
                           with_capability=with_capability,
                           progress=None if progress is None else (lambda f, m, k=k: progress((k + f) / total, m)))
        verdicts = {r["id"]: r["verdict"] for r in res["rows"]}
        improves = [r["id"] for r in base["rows"] if rank[verdicts[r["id"]]] > rank[r["verdict"]]]
        worsens = [r["id"] for r in base["rows"] if rank[verdicts[r["id"]]] < rank[r["verdict"]]]
        out.append({"name": str(cand.get("name") or f"candidate {k + 1}"), "changes": changes, "rows": res["rows"],
                    "summary": res["summary"], "improves": improves, "worsens": worsens,
                    "all_met": all(v == "PASS" for v in verdicts.values()),
                    "diagnostic_only": any(ch["change_kind"] == "diagnostic" for ch in changes)})
    return {"baseline": {"rows": base["rows"], "summary": base["summary"]}, "candidates": out,
            "note": "each candidate is re-judged against every requirement; improvements and regressions are listed, "
                    "never traded against each other (no weighted score); 'diagnostic' parameters are not design "
                    "knobs; no cost is implied"}


# -- CSV ------------------------------------------------------------------------------------------------------------

# torque is shaft torque at the motor shaft; speed is mechanical rpm unless speed_kind = electrical (converted with
# the pole pairs); Vdc is at the inverter DC terminal (Vdc_port - a battery-side value needs a source model)
CSV_COLUMNS = ("id", "text", "torque_Nm", "speed_rpm", "Vdc_V", "Vdc_max_V", "duration_s", "initial_state",
               "coolant_temp_C", "magnet_temp_C", "winding_temp_C", "operator", "band_Nm", "speed_kind", "Vdc_port")
CSV_TEMPLATE = (
    "id,text,torque_Nm,speed_rpm,Vdc_V,Vdc_max_V,duration_s,initial_state,coolant_temp_C,magnet_temp_C,"
    "winding_temp_C,operator,band_Nm,speed_kind,Vdc_port\n"
    "REQ-A,600 V 12000 rpm 150 N*m,150,12000,600,,,,,,,achieve,,mechanical,inverter_dc_terminal\n"
    "REQ-B,regen 100 N*m at 12000 rpm,-100,12000,600,,,,,,,achieve,,mechanical,inverter_dc_terminal\n"
    "REQ-C,550-650 V 100 N*m at 12000 rpm,100,12000,550,650,,,,,,achieve,,mechanical,inverter_dc_terminal\n"
    "REQ-D,300 N*m at 3000 rpm for 10 s,300,3000,600,,10,equilibrium_at_coolant,65,,,achieve,,mechanical,"
    "inverter_dc_terminal\n"
    "REQ-E,140 +/- 10 N*m at 13000 rpm,140,13000,600,,,,,,,band,10,mechanical,inverter_dc_terminal\n")


def _num(row: dict, key: str, line: int, required: bool = False):
    v = (row.get(key) or "").strip()
    if v == "":
        if required:
            raise InputValidationError(f"line {line}: {key} is required", field=key)
        return None
    try:
        x = float(v)
    except ValueError:
        raise InputValidationError(f"line {line}: {key} {v!r} is not a number", field=key) from None
    if not math.isfinite(x):
        raise InputValidationError(f"line {line}: {key} must be finite", field=key)
    return x


def requirement_dict(row: dict, line: int = 0) -> dict:
    """A CSV row -> the case-file requirement dict (declared units), parsed by the same requirement parser."""
    rid = (row.get("id") or "").strip()
    if not rid:
        raise InputValidationError(f"line {line}: id is required", field="id")
    t = _num(row, "torque_Nm", line, True)
    n = _num(row, "speed_rpm", line, True)
    v = _num(row, "Vdc_V", line, True)
    v2 = _num(row, "Vdc_max_V", line)
    kind = (row.get("speed_kind") or "").strip() or "mechanical"
    if kind not in ("mechanical", "electrical"):
        raise InputValidationError(f"line {line}: speed_kind must be mechanical or electrical (got {kind!r})",
                                   field="speed_kind")
    port = (row.get("Vdc_port") or "").strip() or "inverter_dc_terminal"
    cond = {"speed": {"value": n, "unit": "rpm", "kind": kind},
            "Vdc": {"value": [v, v2] if v2 is not None else v, "unit": "V", "port": port}}
    for key, name in (("coolant_temp_C", "coolant_temp"), ("magnet_temp_C", "magnet_temp"),
                      ("winding_temp_C", "winding_temp")):
        x = _num(row, key, line)
        if x is not None:
            cond[name] = {"value": x, "unit": "degC"}
    st = (row.get("initial_state") or "").strip()
    if st:
        cond["initial_state"] = st
    d = {"id": rid, "text": (row.get("text") or "").strip() or rid,
         "target": {"value": t, "unit": "N*m", "torque": "shaft"}, "conditions": cond}
    dur = (row.get("duration_s") or "").strip()
    if dur:
        d["duration"] = "continuous" if dur.lower() in ("continuous", "cont", "inf") else {
            "value": _num(row, "duration_s", line), "unit": "s"}
    op = (row.get("operator") or "achieve").strip() or "achieve"
    if op not in ("achieve", "band"):
        raise InputValidationError(f"line {line}: operator must be achieve or band", field="operator")
    d["operator"] = op
    if op == "band":
        b = _num(row, "band_Nm", line, True)
        d["band"] = {"value": b, "unit": "N*m"}
    return d


def parse_requirements_csv(text: str, pole_pairs: int | None = None, dicts_out: list | None = None) -> list:
    """CSV (header row with the CSV_COLUMNS names; unknown columns are refused) -> Requirement objects, each parsed
    by the case-file requirement parser (declared units; ambiguous definitions refused before any calculation).
    ``dicts_out`` (optional) receives the case-file requirement dict of every row (to open one as a case)."""
    from .io import requirement_from_dict
    from .units import Conversions
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise InputValidationError("the requirement CSV holds no rows", field="csv")
    unknown = sorted(set(rows[0]) - set(CSV_COLUMNS) - {None, ""})
    if unknown:
        raise InputValidationError(f"unknown column(s) {unknown}; columns: {', '.join(CSV_COLUMNS)}", field="csv")
    out, seen = [], set()
    for i, row in enumerate(rows, start=2):
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        d = requirement_dict(row, i)
        if d["id"] in seen:
            raise InputValidationError(f"line {i}: duplicate requirement id {d['id']!r}", field="id")
        seen.add(d["id"])
        try:
            out.append(requirement_from_dict(copy.deepcopy(d), Conversions(), pole_pairs))
        except InputValidationError as exc:
            raise InputValidationError(f"line {i} ({d['id']}): {exc}", field=exc.field) from None
        if dicts_out is not None:
            dicts_out.append(d)
    return out


def rows_to_csv(rows: list) -> str:
    cols = ("id", "verdict", "status", "class", "classes", "margin_Nm", "limiting", "levers", "next_data", "reasons",
            "spec_check", "torque_Nm", "speed_rpm", "Vdc_V", "duration", "decided_by", "record_id")

    def cell(r, c):
        v = r.get(c)
        if c == "Vdc_V" and isinstance(v, (list, tuple)):
            return f"{v[0]:g}..{v[1]:g}"
        if c == "levers":
            return " | ".join(x["parameter"] for x in v or ())
        if c == "spec_check":
            return (v or {}).get("status", "")
        if isinstance(v, list):
            return " | ".join(str(x) for x in v)
        return "" if v is None else v

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([cell(r, c) for c in cols])
    return buf.getvalue()
