"""Declared error budget and the robustness of a model margin (review of 63a2b61, section 4).

A model verdict answers "does THIS model meet the requirement".  A design decision also asks whether the margin the
model shows is larger than what the model, its inputs and the numerics can be wrong by.  The two answers are kept
apart and the model verdict is never changed here.  The engineer declares the error budget as named contributions,
each with its kind, the quantity it is an error of, its size and its basis:

    kind      model      - model mismatch (flux-map / torque accuracy, loss model, ...),
              input      - input data and measurement (parameter measurement, current-sensor gain, ...),
              numerical  - numerical error of supplied data beyond this tool's own evidence (e.g. FEA mesh).
    quantity  torque (N*m) - compared with the torque-capability margin of the requirement;
              phase_current (A, fundamental phase peak), dc_power (W), dc_current (A), voltage (V, command phase
              peak) - compared with that constraint's margin at the requirement's witness point.
    size      an absolute value in the quantity's unit, or a percentage of the model value: the modelled
              capability |C| for torque (a relative torque-model error scales the capability), the constraint's
              demand at the witness for a limit (a sensor gain error scales the measured value).
    basis     required: the correlation, datasheet or calculation behind the bound and where it applies - a bound
              is not called a confidence interval, and one without its basis is not evidence.

The contributions of one quantity are stacked worst case, as a plain sum of bounds with no probability (as
everywhere in this tool), to Delta, and the guarded rule of the EMI trace verdict is applied (motoring written out,
braking mirrors it):

    torque, met beyond the error      C_lo - T_edge >= Delta(C_lo)   C_lo = the found capability, a verified witness
    torque, not met beyond the error  T_edge - C_hi >  Delta(C_hi)   C_hi = a certified upper bound of the capability
    a limit y <= y_max at the witness y + Delta(y) <= y_max, i.e. its slack >= Delta (the reviewer's y + D <= y_max)
    otherwise                         within the error (the model answer does not survive the declared error), or
                                      not established.

The capability's own numerical uncertainty needs no budget line: 'met' is judged at the lower end of the model
capability (a witness) and 'not met' at its upper end (a certified bound).  T_edge is the requirement torque: the
target for 'achieve', the band edge nearest to zero on the capability's side for a band (one torque inside it is
enough).  A limit is compared only at a witness that meets the requirement; a voltage limit the witness sits on (field
weakening) is not compared there, because the policy moves the operating point to hold it - a voltage error's effect
is a change of the capability, to be declared in torque or examined as a Vdc range.  A FAIL is judged on the torque
capability.  Conditions (Vdc points, magnet temperatures) aggregate with the requirement's for-all quantifier.  The
comparison covers the static claim only: a duration part keeps its own rating evidence, and operating-condition
uncertainty (Vdc, temperatures) belongs in the requirement as a range.  Parameter intervals have their own corner
analysis (``analysis.uncertainty``).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import InputValidationError
from ..validation import finite as _finite

ERROR_KINDS = ("model", "input", "numerical")
COMBINATION = "worst-case sum of the declared bounds of each quantity (no probability attached)"
# quantity -> (unit, what a percentage refers to, words, the constraints it is compared with at the witness)
QUANTITIES = {
    "torque": ("N*m", "the modelled capability |C|", "torque", ()),
    "phase_current": ("A", "the phase-current demand at the witness", "phase current", ("CURRENT",)),
    "dc_power": ("W", "the DC power at the witness", "DC power", ("DC_DISCHARGE_POWER", "DC_CHARGE_POWER")),
    "dc_current": ("A", "the average DC current at the witness", "DC current",
                   ("DC_DISCHARGE_CURRENT", "DC_CHARGE_CURRENT")),
    "voltage": ("V", "the command voltage at the witness", "command voltage", ("VOLTAGE",)),
}


@dataclass(frozen=True)
class ErrorItem:
    source: str
    kind: str
    quantity: str = "torque"
    value: float | None = None            # in the quantity's unit
    percent: float | None = None          # of the model value (QUANTITIES[quantity][1])
    basis: str = ""

    def __post_init__(self):
        if not str(self.source).strip():
            raise InputValidationError("an error-budget item needs its source (what can be wrong)",
                                       field="error_budget.source")
        if self.kind not in ERROR_KINDS:
            raise InputValidationError(f"error-budget kind must be one of {', '.join(ERROR_KINDS)} (got "
                                       f"{self.kind!r})", field="error_budget.kind")
        if self.quantity not in QUANTITIES:
            raise InputValidationError(f"error-budget quantity must be one of {', '.join(QUANTITIES)} (got "
                                       f"{self.quantity!r})", field="error_budget.quantity")
        if (self.value is None) == (self.percent is None):
            raise InputValidationError(f"error-budget item {self.source!r}: give exactly one of an absolute value "
                                       f"and a percentage", field="error_budget")
        name = "value" if self.value is not None else "percent"
        v = _finite(f"error_budget.{name}", getattr(self, name))
        if v < 0 or (name == "percent" and v > 100):
            raise InputValidationError(f"error-budget item {self.source!r}: the {name} must be >= 0"
                                       + (" and <= 100 %" if name == "percent" else ""),
                                       field=f"error_budget.{name}")
        if not str(self.basis).strip():
            # a bound without its basis is not evidence: a small unexplained error would make a margin look robust
            raise InputValidationError(f"error-budget item {self.source!r}: state its basis and where it applies (the "
                                       f"correlation, datasheet or calculation behind the bound"
                                       + (", and why the error vanishes" if v == 0 else "") + ")",
                                       field="error_budget.basis")
        object.__setattr__(self, name, v)

    @property
    def unit(self) -> str:
        return QUANTITIES[self.quantity][0]

    def delta(self, model_value: float) -> float:
        if self.value is not None:
            return self.value
        return self.percent / 100.0 * abs(model_value)

    def describe(self) -> dict:
        return {"source": self.source, "kind": self.kind, "quantity": self.quantity, "unit": self.unit,
                "value": self.value, "percent": self.percent, "percent_of": QUANTITIES[self.quantity][1],
                "basis": self.basis}


@dataclass(frozen=True)
class ErrorBudget:
    items: tuple[ErrorItem, ...]

    def __post_init__(self):
        if not self.items:
            raise InputValidationError("an error budget needs at least one item (leave it out to not assess)",
                                       field="error_budget")

    def of(self, quantity: str) -> tuple[ErrorItem, ...]:
        return tuple(i for i in self.items if i.quantity == quantity)

    def has(self, quantity: str) -> bool:
        return bool(self.of(quantity))

    def delta(self, quantity: str, model_value: float) -> float:
        return sum(i.delta(model_value) for i in self.of(quantity))

    def parts(self, quantity: str, model_value: float) -> list[dict]:
        return [{**i.describe(), "delta": i.delta(model_value)} for i in self.of(quantity)]

    def describe(self) -> dict:
        return {"items": [i.describe() for i in self.items], "combination": COMBINATION}


def error_budget_from_dict(d) -> ErrorBudget | None:
    """``[{"source", "kind", "quantity", "value" | "percent", "basis"}, ...]`` or ``{"items": [...]}``; a torque item
    may be written ``{"torque_Nm": x}`` / ``{"torque_percent": p}``.  Empty -> None."""
    if not d:
        return None
    items = d.get("items") if isinstance(d, dict) else d
    if not isinstance(items, (list, tuple)):
        raise InputValidationError("error_budget must be a list of items (or {'items': [...]})", field="error_budget")
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise InputValidationError("each error-budget item is an object", field="error_budget")
        known = {"source", "kind", "quantity", "value", "percent", "torque_Nm", "torque_percent", "basis", "unit"}
        unknown = set(it) - known
        if unknown:
            raise InputValidationError(f"unknown error-budget field(s): {', '.join(sorted(unknown))}",
                                       field="error_budget")
        given = lambda k: it.get(k) not in (None, "")            # noqa: E731
        short = given("torque_Nm") or given("torque_percent")
        if short and (given("value") or given("percent") or it.get("quantity", "torque") != "torque"):
            raise InputValidationError("write a torque item either as torque_Nm / torque_percent or as quantity + "
                                       "value / percent, not both", field="error_budget")
        q = "torque" if short else str(it.get("quantity") or "torque")
        if given("unit") and q in QUANTITIES and it["unit"] != QUANTITIES[q][0]:
            raise InputValidationError(f"error-budget unit for {q} is {QUANTITIES[q][0]} (got {it['unit']!r})",
                                       field="error_budget.unit")
        value = it["torque_Nm"] if given("torque_Nm") else it["value"] if given("value") else None
        pct = it["torque_percent"] if given("torque_percent") else it["percent"] if given("percent") else None
        out.append(ErrorItem(str(it.get("source", "")), str(it.get("kind", "")), q, value, pct,
                             str(it.get("basis", "") or "")))
    return ErrorBudget(tuple(out)) if out else None


def torque_robustness(budget: ErrorBudget | None, direction: int, T_edge_Nm: float, C_lo_Nm: float | None,
                      C_hi_Nm: float | None = None, static_status: str | None = None) -> dict:
    """One condition: the torque-capability margin against the declared torque error.

    ``C_lo_Nm``: the found (accepted) capability; ``C_hi_Nm``: a certified upper bound of it (None when there is
    none); ``static_status``: the status of the static claim at the condition (FEASIBLE / INFEASIBLE / UNKNOWN), used
    to refuse a comparison whose margin contradicts the claim (a non-contiguous feasible torque set).
    -> {"met": True / False / None, "robust": True / False / None, "status", "margin_Nm", "delta_Nm", ...}."""
    d = 1 if direction >= 0 else -1
    out = {"T_edge_Nm": T_edge_Nm, "capability_Nm": C_lo_Nm, "capability_bound_Nm": C_hi_Nm,
           "met": None, "robust": None}
    if C_lo_Nm is None:
        return {**out, "status": "NOT_ASSESSED", "reason": "the torque capability is not established at this condition"}
    m = d * (C_lo_Nm - T_edge_Nm)
    out["margin_Nm"] = m
    if static_status == "UNKNOWN":
        return {**out, "status": "NOT_ASSESSED", "reason": "the static claim is not decided at this condition"}
    if (static_status == "FEASIBLE" and m < 0) or (static_status == "INFEASIBLE" and m >= 0):
        return {**out, "status": "NOT_ASSESSED",
                "reason": f"the static claim ({static_status}) and the capability margin ({m:+.6g} N*m) disagree: the "
                          f"feasible torque set is not contiguous here, a single margin does not describe it"}
    out["met"] = m >= 0
    if budget is None:
        return {**out, "status": "NOT_ASSESSED", "reason": "no error budget declared"}
    if not budget.has("torque"):
        return {**out, "status": "NOT_ASSESSED", "reason": "no torque error declared"}
    dl = budget.delta("torque", C_lo_Nm)
    out.update(delta_Nm=dl, parts=budget.parts("torque", C_lo_Nm))
    if m >= 0:
        ok = m >= dl
        return {**out, "robust": ok, "status": "ROBUST_MET" if ok else "MET_WITHIN_ERROR", "surplus_Nm": m - dl}
    short = -m
    if C_hi_Nm is not None:
        dh = budget.delta("torque", C_hi_Nm)
        short_hi = d * (T_edge_Nm - C_hi_Nm)
        out.update(shortfall_at_bound_Nm=short_hi, delta_at_bound_Nm=dh)
        if short_hi > dh:
            return {**out, "robust": True, "status": "ROBUST_NOT_MET", "surplus_Nm": short_hi - dh}
    if short <= dl:
        return {**out, "robust": False, "status": "NOT_MET_WITHIN_ERROR", "surplus_Nm": short - dl}
    return {**out, "status": "NOT_MET_NOT_ESTABLISHED", "surplus_Nm": short - dl,
            "reason": "short by more than the declared error at the found capability, but no certified upper bound "
                      "shows it for the whole model capability"}


def constraint_robustness(budget: ErrorBudget | None, constraints) -> list[dict]:
    """Every declared limit error against that limit's margin at a witness that meets the requirement.

    ``constraints``: the witness point's constraint results (objects with ``name``, ``demand``, ``limit``,
    ``unit``, ``slack``, ``state``).  y + Delta(y) <= y_max is slack >= Delta; a lower limit (charge acceptance) is
    the same with its own slack.  A voltage limit the witness sits on (ACTIVE: field weakening) is not compared - the
    policy moves the point to hold it, so a voltage error changes the capability, not this margin."""
    out = []
    if budget is None:
        return out
    for q, (unit, _of, words, names) in QUANTITIES.items():
        if not names or not budget.has(q):
            continue
        found = [c for c in constraints if c.name in names]
        live = [c for c in found if c.slack is not None]
        if len(live) > 1:                   # discharge and charge limits of one quantity: the one this point is near
            found = [min(live, key=lambda c: c.slack)]
        if not found:
            out.append({"quantity": q, "constraint": None, "unit": unit, "robust": None, "status": "NOT_EVALUATED",
                        "reason": f"no {words} limit at the witness: nothing to compare the declared error with"})
            continue
        for c in found:
            row = {"quantity": q, "constraint": c.name, "unit": unit, "demand": c.demand, "limit": c.limit,
                   "slack": c.slack}
            if c.slack is None or c.state == "NOT_EVALUATED":
                out.append({**row, "robust": None, "status": "NOT_EVALUATED",
                            "reason": f"the {words} limit is not evaluated at the witness"})
                continue
            dl = budget.delta(q, c.demand)
            row.update(delta=dl, parts=budget.parts(q, c.demand), surplus=c.slack - dl)
            if q == "voltage" and c.state == "ACTIVE":
                out.append({**row, "robust": None, "status": "ADAPTED",
                            "reason": "the witness sits on the voltage limit (field weakening): the policy moves the "
                                      "operating point to hold it, so a voltage error changes the torque capability - "
                                      "declare its torque effect or examine a Vdc range"})
                continue
            ok = c.slack >= dl
            out.append({**row, "robust": ok, "status": "ROBUST" if ok else "WITHIN_ERROR"})
    return out


def _check_text(c: dict, where: str) -> str:
    words = QUANTITIES[c["quantity"]][2]
    rel = ">=" if c["robust"] else "<"
    return (f"{words} margin {c['slack']:.4g} {c['unit']} {rel} declared error {c['delta']:.4g} {c['unit']} at the "
            f"witness{where}")


def aggregate_robustness(per: list[dict], names: list[str], budget: ErrorBudget | None, checks=()) -> dict:
    """Every condition must hold (the requirement's for-all quantifier over Vdc points and magnet temperatures).

    ``per``: the torque comparison per condition; ``checks``: the limit comparisons at the witnesses of the
    conditions that meet the requirement, each with its ``condition`` index.  Model met at every condition: ROBUST
    when every declared error is covered (torque margin and every compared limit), WITHIN_ERROR when one is not.
    Model not met somewhere: judged on the torque capability - ROBUST when one counterexample is short beyond the
    torque error at a certified bound, WITHIN_ERROR when every counterexample is short by no more than it.  Anything
    else is NOT_ESTABLISHED; without a budget (or without a comparable item) the layer is NOT_ASSESSED."""
    checks = list(checks)
    fails = [i for i, p in enumerate(per) if p["met"] is False]
    mets = [i for i, p in enumerate(per) if p["met"] is True]
    open_ = [i for i, p in enumerate(per) if p["met"] is None]
    base = {"budget": None if budget is None else budget.describe(), "conditions": per, "names": names,
            "checks": checks, "combination": COMBINATION, "governing_check": None}
    several = len(per) > 1
    torque = budget is not None and budget.has("torque")

    def at(i):
        return f" at {names[i]}" if several and i is not None else ""

    def why(idx):
        return "; ".join(dict.fromkeys(f"{names[i] + ': ' if several else ''}{per[i].get('reason', '')}"
                                       for i in idx))
    if not fails and not mets:
        return {**base, "status": "NOT_ASSESSED", "model_static": "not assessed", "governing": None,
                "meaning": "no condition has a decided static claim with an established torque capability: "
                           + (why(open_) if per else "no condition")}
    model = "not met" if fails else "met" if not open_ else "met at the assessed conditions"
    if budget is None:
        if fails:
            g = max(fails, key=lambda i: -per[i]["margin_Nm"])
            mt = f"model shortfall {-per[g]['margin_Nm']:.4g} N*m{at(g)}"
        else:
            g = min(mets, key=lambda i: per[i]["margin_Nm"])
            mt = f"model margin {per[g]['margin_Nm']:.4g} N*m{at(g)}"
        return {**base, "status": "NOT_ASSESSED", "governing": g, "model_static": model,
                "meaning": f"no error budget declared: {mt}; its sufficiency for a design decision is not assessed"}
    if fails:
        if not torque:
            g = max(fails, key=lambda i: -per[i]["margin_Nm"])
            return {**base, "status": "NOT_ASSESSED", "governing": g, "model_static": model,
                    "meaning": f"model shortfall {-per[g]['margin_Nm']:.4g} N*m{at(g)}: a FAIL is compared with the "
                               f"declared error on the torque capability, and no torque error is declared (limit "
                               f"errors are compared at witnesses that meet the requirement)"}
        rob = [i for i in fails if per[i]["robust"] is True]
        weak = [i for i in fails if per[i]["robust"] is False]
        unsure = [i for i in fails if per[i]["robust"] is None]
        if rob:
            g = max(rob, key=lambda i: per[i]["surplus_Nm"])
            p = per[g]
            status = "ROBUST"
            meaning = (f"short by at least {p['shortfall_at_bound_Nm']:.4g} N*m (certified bound) > declared error "
                       f"{p['delta_at_bound_Nm']:.4g} N*m{at(g)}: not met even with the declared error")
        elif weak and not unsure:
            g = max(weak, key=lambda i: per[i]["surplus_Nm"])
            p = per[g]
            status = "WITHIN_ERROR"
            meaning = (f"short by {-p['margin_Nm']:.4g} N*m <= declared error {p['delta_Nm']:.4g} N*m{at(g)}: not met "
                       f"in the model, but within the declared error - the product may still meet it; the model FAIL "
                       f"alone does not reject the design")
        else:
            g = max(unsure, key=lambda i: -per[i]["margin_Nm"])
            status = "NOT_ESTABLISHED"
            meaning = f"short by {-per[g]['margin_Nm']:.4g} N*m in the model; not established against the declared " \
                      f"error: {why(unsure)}"
        return {**base, "status": status, "model_static": model, "governing": g, "meaning": meaning}
    # met at every assessed condition: the torque margins (when a torque error is declared) and every limit compared
    t_weak = [i for i in mets if torque and per[i]["robust"] is False]
    if torque:
        t_unsure = open_ + [i for i in mets if per[i]["robust"] is None]
    else:                                   # only limits declared: a condition without a compared witness is open
        checked = {c["condition"] for c in checks}
        t_unsure = [i for i in open_ if i not in checked]
    c_weak = [c for c in checks if c["robust"] is False]
    c_unsure = [c for c in checks if c["robust"] is None]
    if not torque and not checks:
        g = min(mets, key=lambda i: per[i]["margin_Nm"])
        return {**base, "status": "NOT_ASSESSED", "governing": g, "model_static": model,
                "meaning": "no declared error can be compared here (no torque error, and no declared limit error "
                           "meets a limit at the witnesses)"}
    g = min(mets, key=lambda i: per[i].get("surplus_Nm", per[i]["margin_Nm"]))
    gc = None
    if t_weak:
        g = min(t_weak, key=lambda i: per[i]["surplus_Nm"])
        p = per[g]
        status = "WITHIN_ERROR"
        meaning = (f"torque margin {p['margin_Nm']:.4g} N*m < declared error {p['delta_Nm']:.4g} N*m{at(g)}: met "
                   f"in the model, but not by more than the declared error - not sufficient for a design decision "
                   f"on this evidence")
    elif c_weak:
        gc = min(c_weak, key=lambda c: c["surplus"] / max(c["delta"], 1e-300))
        status = "WITHIN_ERROR"
        meaning = (_check_text(gc, at(gc["condition"])) + ": met in the model, but the limit is not held by more than "
                   "the declared error - not sufficient for a design decision on this evidence")
    elif t_unsure or c_unsure:
        status = "NOT_ESTABLISHED"
        reasons = [why(t_unsure)] if t_unsure else []
        reasons += [f"{names[c['condition']] + ': ' if several else ''}{c['reason']}" for c in c_unsure]
        gc = c_unsure[0] if c_unsure and not t_unsure else None
        meaning = "not established against the declared error: " + "; ".join(dict.fromkeys(reasons))
    else:
        status = "ROBUST"
        parts = []
        if torque:
            p = per[g]
            parts.append(f"torque margin {p['margin_Nm']:.4g} N*m >= declared error {p['delta_Nm']:.4g} N*m"
                         + (f" at every condition (smallest surplus{at(g)})" if several else ""))
        if checks:
            gc = min(checks, key=lambda c: c["surplus"] / max(c["delta"], 1e-300))
            parts.append("every declared limit error is covered at the witnesses (tightest: "
                         + _check_text(gc, at(gc["condition"])) + ")")
        meaning = "; ".join(parts) + ": met with the declared error"
    return {**base, "status": status, "model_static": model, "governing": g, "governing_check": gc,
            "meaning": meaning}
