"""Static review of the protection architecture and its safety requirements - what an assessor checks before any
simulation: completeness of the SG / FSR / TSR chain, ASIL inheritance, timing budgets against the FTTI, the
detection and reaction latencies the declared parameters allow, traceability of every mechanism, latent-fault tests,
the executability of the declared reaction strategies on their paths and the declared common causes.

Every finding names the element, the check, a status and why:

  OK            the check holds on the declared data;
  INCONSISTENT  the declared data contradict each other (e.g. the budgets exceed the FTTI, a step fault is detected
                later than the FDTI budget allows, an FSR's ASIL is below its goal's without a decomposition);
  MISSING       something the chain needs is not declared (an FTTI, a safe-state requirement, a verification method);
  WARNING       allowed but weak (a strategy that degrades on a hardware path, a mechanism without a latent test, one
                resource removing every mechanism of an FSR);
  NOTE          information (an unallocated mechanism, a declared-only attribute).

The latency figures are BOUNDS from the declared parameters for a gross step fault: a software mechanism detects it
between debounce + sensor delay (the fault just before a task activation) and that plus one task period (just after
it), a hardware comparator after its filter + sensor delay.  Only the lower bound above the FDTI budget is a
contradiction (every such fault is detected too late); the upper bound above it depends on the fault instant (a
warning).  For a reaction strategy the time before its final step (declared exits: time values exactly, ramps at
least their ramp time, other exits up to their maximum time) is compared with the FRTI budget as a warning only - the
safe condition may already hold during an earlier step.  A marginal fault is detected later; that - and the physical
settling that ends the FRTI - only a simulation or a campaign shows.  The review states the declared architecture,
not the product.
"""

from __future__ import annotations

from .configure import mechanisms_from, paths_from, policy_from, sensors_from
from .labels import exit_label, reaction_label
from .protection import BRIDGE_REACTIONS
from .safety import ASIL_LEVELS, requirements_from_dict
from .strategy import MEASURED_EXITS, action_kind, strategies_from

OK, INCONSISTENT, MISSING, WARNING, NOTE = "OK", "INCONSISTENT", "MISSING", "WARNING", "NOTE"
RANK = {INCONSISTENT: 4, MISSING: 3, WARNING: 2, NOTE: 1, OK: 0}
_ASIL_ORDER = {a: i for i, a in enumerate(ASIL_LEVELS)}

# the sensor role each mechanism kind reads (its conversion delay adds to the detection latency)
_READS = {"torque_monitor": "current_mon_a", "current_plausibility": "current_mon_a", "overcurrent_sw": "current_mon_a",
          "overvoltage_sw": "vdc_monitor", "undervoltage_sw": "vdc_monitor", "position_los": "position_monitor",
          "overspeed_sw": "position_control",
          "overcurrent_hw": "current_hw_a", "overvoltage_hw": "vdc_hw"}
_ROLE_DEFAULTS = {"current_hw_a": "current_a", "current_mon_a": "current_a", "position_monitor": "position_control",
                  "vdc_monitor": "vdc_control", "vdc_hw": "vdc_control"}


def _f(status, element, check, detail, **kw) -> dict:
    return {"status": status, "element": element, "check": check, "detail": detail, **kw}


def _ms(s) -> str:
    return "-" if s is None else f"{s * 1e3:.4g} ms"


def detection_latency(mech, sensors: dict, roles: dict) -> tuple[float | None, str, float | None]:
    """Detection latency bounds for a gross step fault (seconds): (upper bound, how it is composed, lower bound)."""
    P = mech.params
    role = _READS.get(mech.kind)
    sen = None
    if role:
        sen = sensors.get(roles.get(role) or roles.get(_ROLE_DEFAULTS.get(role, ""), ""))
    d_sen = sen.delay_s if sen is not None else 0.0
    if mech.hardware:
        if mech.kind == "watchdog":
            t = float(P.get("timeout_s", 0.0))
            return t, f"watchdog timeout {_ms(t)}", t
        if mech.kind == "gate_uvlo":
            t = float(P.get("delay_s", 2e-6))
            return t, f"report delay {_ms(t)}", t
        if mech.kind == "desat":
            t = float(P.get("turnoff_s", 2e-6))
            return t, f"desaturation turn-off {_ms(t)}", t
        f = float(P.get("filter_s", 0.0))
        return f + d_sen, f"glitch filter {_ms(f)} + sensor delay {_ms(d_sen)}", f + d_sen
    per = float(mech.period_s or 0.0)
    deb = float(P.get("debounce_s", 0.0) or 0.0)
    extra, why, window = 0.0, "", 0.0
    if mech.kind == "torque_monitor":
        window = float(P.get("delay_s", 0.0) or 0.0)       # the window follows a request change this much later
        why = f" (+ window delay allowance {_ms(window)} after a request change)"
    if mech.kind == "command_timeout":
        extra = float(P.get("timeout_s", 0.0))
        why = f" + timeout {_ms(extra)}"
    if mech.kind == "position_los" and sen is not None and sen.los_detect_s:
        extra = float(sen.los_detect_s)
        why = f" + converter loss-of-signal detection {_ms(extra)}"
    lo = deb + d_sen + extra
    return (lo + per + window, f"debounce {_ms(deb)} + sensor delay {_ms(d_sen)}{why} + up to one task period "
                               f"{_ms(per)}", lo)


def review_section(data: dict, independence: dict | None = None) -> dict:
    """Findings of the static review of one ``fault_sim`` section (see the module note)."""
    out = []
    reqs = requirements_from_dict(data.get("requirements") or {})
    mechs = {m.mech_id: m for m in mechanisms_from(data)}
    paths = paths_from(data, {})
    sensors = {s.name: s for s in sensors_from(data, {})}
    roles = dict(data.get("roles") or {})
    strategies = strategies_from(data)
    policy = policy_from(data)
    raw_mech = {str(m.get("id")): m for m in data.get("mechanisms") or []}
    goal = {g.sg_id: g for g in reqs.goals}
    tsr_by_fsr: dict = {}
    for t in reqs.tsrs:
        tsr_by_fsr.setdefault(t.fsr, []).append(t)
    fsr_by_sg: dict = {}
    for f in reqs.fsrs:
        for g in f.sg:
            fsr_by_sg.setdefault(g, []).append(f.fsr_id)

    # -- safety goals -------------------------------------------------------------------------------------------
    for g in reqs.goals:
        el = g.sg_id
        out.append(_f(OK if g.asil in ASIL_LEVELS else MISSING, el, "ASIL",
                      f"ASIL {g.asil}" if g.asil else "no ASIL declared"))
        out.append(_f(OK if g.ftti_s else MISSING, el, "FTTI",
                      f"FTTI {_ms(g.ftti_s)}" if g.ftti_s else "no FTTI declared: the timing chain has no bound"))
        out.append(_f(OK if g.safe_state else MISSING, el, "safe state",
                      g.safe_state or "no safe state declared for the goal"))
        out.append(_f(OK if fsr_by_sg.get(el) else MISSING, el, "refined by an FSR",
                      ", ".join(fsr_by_sg.get(el, [])) or "no functional safety requirement serves this goal"))

    # -- functional safety requirements ---------------------------------------------------------------------------
    latency_rows = []
    for f in reqs.fsrs:
        el = f.fsr_id
        sg_asil = max((goal[s].asil for s in f.sg if goal.get(s) and goal[s].asil in _ASIL_ORDER),
                      key=lambda a: _ASIL_ORDER[a], default="")
        if not f.asil:
            out.append(_f(MISSING, el, "ASIL", f"no ASIL declared (its goals: ASIL {sg_asil or '-'})"))
        elif sg_asil and _ASIL_ORDER.get(f.asil, 0) < _ASIL_ORDER[sg_asil]:
            out.append(_f(INCONSISTENT, el, "ASIL inheritance", f"ASIL {f.asil} below its goals' ASIL {sg_asil} "
                                                                 f"(no decomposition declared)"))
        else:
            out.append(_f(OK, el, "ASIL inheritance", f"ASIL {f.asil} (goals {sg_asil or '-'})"))
        fttis = [goal[s].ftti_s for s in f.sg if goal.get(s) and goal[s].ftti_s]
        ftti = min(fttis) if fttis else None
        if f.fdti_budget_s is None or f.frti_budget_s is None:
            out.append(_f(MISSING, el, "FDTI / FRTI budgets", "budget(s) not declared: FDTI "
                                                              f"{_ms(f.fdti_budget_s)}, FRTI {_ms(f.frti_budget_s)}"))
        elif ftti is None:
            out.append(_f(MISSING, el, "budgets vs FTTI", "its goals declare no FTTI"))
        else:
            tot = f.fdti_budget_s + f.frti_budget_s
            out.append(_f(OK if tot <= ftti + 1e-12 else INCONSISTENT, el, "budgets vs FTTI",
                          f"FDTI {_ms(f.fdti_budget_s)} + FRTI {_ms(f.frti_budget_s)} = {_ms(tot)} "
                          f"{'<=' if tot <= ftti + 1e-12 else '>'} FTTI {_ms(ftti)}"))
        if not f.mechanisms:
            out.append(_f(MISSING, el, "allocated mechanisms", "no safety mechanism allocated"))
        for m in f.mechanisms:
            spec = mechs.get(m)
            if spec is None:
                out.append(_f(INCONSISTENT, el, "allocated mechanisms", f"{m} is not declared"))
                continue
            if not spec.enabled:
                out.append(_f(WARNING, el, "allocated mechanisms", f"{m} is allocated but disabled"))
            lat, how, lat_lo = detection_latency(spec, sensors, roles)
            path = paths.get(spec.path)
            p_del = path.delay_s if path else 0.0
            row = {"fsr": el, "mechanism": m, "kind": spec.kind, "path": spec.path, "detection_s": lat,
                   "detection_min_s": lat_lo, "detection_basis": how, "path_delay_s": p_del,
                   "fdti_budget_s": f.fdti_budget_s, "frti_budget_s": f.frti_budget_s}
            latency_rows.append(row)
            if f.fdti_budget_s is not None and lat is not None:
                b = f.fdti_budget_s + 1e-12
                if lat_lo > b:
                    out.append(_f(INCONSISTENT, f"{el}/{m}", "detection latency vs FDTI budget",
                                  f"gross step fault: detected no earlier than {_ms(lat_lo)} ({how}) > FDTI budget "
                                  f"{_ms(f.fdti_budget_s)} - every such fault is detected too late"))
                elif lat > b:
                    out.append(_f(WARNING, f"{el}/{m}", "detection latency vs FDTI budget",
                                  f"gross step fault: {_ms(lat_lo)} ... {_ms(lat)} ({how}); above the FDTI budget "
                                  f"{_ms(f.fdti_budget_s)} when the fault falls just after a task activation"))
                else:
                    out.append(_f(OK, f"{el}/{m}", "detection latency vs FDTI budget",
                                  f"gross step fault: <= {_ms(lat)} ({how}) <= FDTI budget {_ms(f.fdti_budget_s)}"))
        items = tsr_by_fsr.get(el, [])
        out.append(_f(OK if items else MISSING, el, "refined by TSRs",
                      ", ".join(t.tsr_id for t in items) or "no technical safety requirement"))
        hz = [t for t in items if t.criterion["type"] in ("torque_window", "bound")]
        out.append(_f(OK if hz else MISSING, el, "hazard-related TSR",
                      ", ".join(t.tsr_id for t in hz) or "no TSR judges the physical hazard (torque / bound)"))
        torque_goal = any(goal.get(s) and goal[s].hazard in ("accel", "decel") for s in f.sg)
        if f.safe_state:
            out.append(_f(OK, el, "safe-state TSR", f.safe_state))
        else:
            out.append(_f(MISSING if torque_goal else NOTE, el, "safe-state TSR",
                          "no safe-state TSR: FRTI / FHTI cannot be measured" + (
                              "" if torque_goal else " (component goal: bounds judge it)")))
        out.append(_f(OK if f.warning else NOTE, el, "warning / degradation",
                      f.warning or "no driver warning or degradation concept declared"))
        out.append(_f(OK if f.verification else MISSING, el, "verification methods",
                      ", ".join(f.verification) or "no verification method declared"))
        if f.fdti_budget_s is not None or f.frti_budget_s is not None:
            timing = [t.tsr_id for t in items if t.criterion["type"] == "timing"]
            out.append(_f(OK if timing else NOTE, el, "budgets judged on trajectories",
                          ("measured FDTI / FRTI / FHTI judged by " + ", ".join(timing)) if timing else
                          "no timing TSR: the measured FDTI / FRTI are reported but not judged on the trajectories "
                          "(only the static latency bounds check the budgets)"))

    # -- technical safety requirements ----------------------------------------------------------------------------
    fsr = {f.fsr_id: f for f in reqs.fsrs}
    for t in reqs.tsrs:
        el = t.tsr_id
        f = fsr[t.fsr]
        if not t.asil:
            out.append(_f(MISSING, el, "ASIL", f"no ASIL declared (FSR {t.fsr}: ASIL {f.asil or '-'})"))
        elif f.asil and t.criterion["type"] != "no_false_reaction" and \
                _ASIL_ORDER.get(t.asil, 0) < _ASIL_ORDER.get(f.asil, 0):
            out.append(_f(INCONSISTENT, el, "ASIL inheritance", f"ASIL {t.asil} below its FSR's ASIL {f.asil}"))
        else:
            out.append(_f(OK, el, "ASIL", f"ASIL {t.asil}" + (" (availability)" if t.criterion["type"] ==
                                                               "no_false_reaction" else "")))
        out.append(_f(OK if t.allocation else MISSING, el, "allocation",
                      t.allocation or "not allocated to a hardware / software element"))
        out.append(_f(OK if t.verification else MISSING, el, "verification methods",
                      ", ".join(t.verification) or "no verification method declared"))
        out.append(_f(OK if t.rationale else NOTE, el, "rationale of the value",
                      t.rationale or "no rationale for the limit declared"))
        c = t.criterion
        fttis = [goal[s].ftti_s for s in f.sg if goal.get(s) and goal[s].ftti_s]
        ftti = min(fttis) if fttis else None
        if c["type"] == "torque_window" and ftti is not None:
            tol = float(c.get("tolerance_s") or 0.0)
            out.append(_f(OK if tol < ftti else INCONSISTENT, el, "tolerance vs FTTI",
                          f"excursion tolerated {_ms(tol)} {'<' if tol < ftti else '>='} FTTI {_ms(ftti)}"))
        if c["type"] == "safe_state":
            w = c.get("within_s")
            if f.safe_state == t.tsr_id and f.frti_budget_s is not None and w is not None:
                out.append(_f(OK if w <= f.frti_budget_s + 1e-12 else INCONSISTENT, el, "within vs FRTI budget",
                              f"safe condition within {_ms(w)} {'<=' if w <= f.frti_budget_s + 1e-12 else '>'} "
                              f"FRTI budget {_ms(f.frti_budget_s)} of {f.fsr_id}"))
            if not c.get("hold_s"):
                out.append(_f(WARNING, el, "hold time", "no hold time: a momentary pass counts as the safe state"))

    # -- mechanisms ---------------------------------------------------------------------------------------------
    alloc = {m for f in reqs.fsrs for m in f.mechanisms}
    high = {m for f in reqs.fsrs if _ASIL_ORDER.get(f.asil, 0) >= _ASIL_ORDER["B"] for m in f.mechanisms}
    for mid, m in mechs.items():
        if not m.enabled:
            continue
        if mid not in alloc:
            out.append(_f(NOTE, mid, "traced to an FSR", "not allocated to any FSR (its detections do not count for "
                                                         "FDTI)"))
        raw = raw_mech.get(mid, {})
        if mid in high:
            lt = raw.get("latent_test")
            out.append(_f(OK if lt else WARNING, mid, "latent-fault test",
                          lt or "no latent-fault test declared (a silently failed mechanism stays undetected: "
                                "multiple-point fault)"))
        cov = raw.get("coverage")
        out.append(_f(OK if cov else NOTE, mid, "diagnostic coverage claim",
                      f"declared {cov} (qualitative, ISO 26262-5 Annex D levels)" if cov else "not declared"))

    # -- reactions and strategies ----------------------------------------------------------------------------------
    rule_targets = [r.get("then") or r.get("else") for r in policy.rules]
    for sid, st in strategies.items():
        users = [m.mech_id for m in mechs.values() if m.reaction == sid]
        hw_users = [m for m in users if "MCU" not in paths[mechs[m].path].resources]
        via_policy = sid in rule_targets
        hw_policy = [m.mech_id for m in mechs.values() if m.reaction == "safe_state" and via_policy
                     and "MCU" not in paths[m.path].resources]
        soft = st.needs_software()
        if soft and (hw_users or hw_policy):
            out.append(_f(WARNING, sid, "executable on its paths",
                          f"software steps; hardware-path request(s) from {', '.join(hw_users + hw_policy)} degrade "
                          f"to the fallback {reaction_label(st.fallback_state)}"))
        elif users or via_policy:
            out.append(_f(OK, sid, "executable on its paths", "used by " + (", ".join(users) or "the policy")))
        else:
            out.append(_f(NOTE, sid, "used", "declared but not used by a mechanism or the policy (candidate only)"))
        for i, s in enumerate(st.steps[:-1]):
            if s.exit not in ("time",) and s.max_s is None:
                out.append(_f(WARNING, f"{sid}/step {i + 1}", "bounded step",
                              f"exit '{exit_label(s.exit)}' without a maximum time: the step may never end"))
            if s.exit in MEASURED_EXITS and action_kind(s.action) == "bridge" and (hw_users or hw_policy):
                out.append(_f(WARNING, f"{sid}/step {i + 1}", "measured exit on a hardware path",
                              "a hardware path cannot evaluate it"))
    for f in reqs.fsrs:
        if f.frti_budget_s is None:
            continue
        for m in f.mechanisms:
            spec = mechs.get(m)
            if spec is None:
                continue
            reacts = [spec.reaction] if spec.reaction != "safe_state" else [x for x in rule_targets if x]
            path = paths.get(spec.path)
            hw = path is not None and "MCU" not in path.resources
            for r in reacts:
                st = strategies.get(r)
                if st is None or (hw and st.needs_software()):
                    continue            # a primitive reaction, or a software strategy degrading at once on HW
                d_path = path.delay_s if path else 0.0
                lo, hi = d_path + st.min_duration_s(), st.static_duration_s()
                hi = None if hi is None else d_path + hi
                b = f.frti_budget_s + 1e-12
                if lo > b or (hi is not None and hi > b):
                    out.append(_f(WARNING, f"{f.fsr_id}/{m}", "reaction latency vs FRTI budget",
                                  f"path {_ms(d_path)} + the steps before the final state of {r}: "
                                  f"{'at least' if lo > b else 'up to'} {_ms(lo if lo > b else hi)} > FRTI budget "
                                  f"{_ms(f.frti_budget_s)} - the budget holds only if the safe condition is reached "
                                  f"before the final step (the simulation shows it)"))
    if not any("else" in r for r in policy.rules):
        out.append(_f(NOTE, "policy", "default rule", "no 'else' rule: an unmatched decision is six-switch-off"))
    used = {m.reaction for m in mechs.values()} | set(x for x in rule_targets if x)
    unlisted = sorted(x for x in used if x not in policy.priority and x not in ("safe_state", "report_only")
                      and x not in BRIDGE_REACTIONS + ("torque_zero",))
    if unlisted:
        out.append(_f(NOTE, "policy", "priority list", f"{', '.join(unlisted)} not in the priority list: ranked as "
                                                       f"their fallback states"))

    # -- common cause (declared dependencies) --------------------------------------------------------------------
    for row in (independence or {}).get("fsr", []):
        out.append(_f(WARNING if row["single_points"] else OK, row["fsr"], "no single declared common cause",
                      row["statement"]))

    counts = {k: sum(1 for x in out if x["status"] == k) for k in RANK}
    worst = max((x["status"] for x in out), key=lambda s: RANK[s], default=OK)
    return {"findings": sorted(out, key=lambda x: -RANK[x["status"]]), "counts": counts, "worst": worst,
            "latency": latency_rows, "strategies": list(strategies),
            "trace": [{"sg": g.sg_id, "asil": g.asil, "ftti_s": g.ftti_s,
                       "fsr": [{"id": f.fsr_id, "asil": f.asil, "mechanisms": list(f.mechanisms),
                                "tsr": [t.tsr_id for t in tsr_by_fsr.get(f.fsr_id, [])],
                                "timing_tsr": [t.tsr_id for t in tsr_by_fsr.get(f.fsr_id, [])
                                               if t.criterion["type"] == "timing"]}
                               for f in reqs.fsrs if g.sg_id in f.sg]} for g in reqs.goals],
            "statement": "static review of the DECLARED architecture and requirements: latencies are bounds for a "
                         "gross step fault; marginal faults and the physical settling need the simulation"}
