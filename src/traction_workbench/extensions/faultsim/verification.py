"""Verification evidence over a scenario catalog: every scenario run on ONE design (the project's architecture with
the study's design variant), judged against every requirement - the requirement x scenario matrix, the coverage of
each requirement (in how many scenarios it was exercised, i.e. not NOT_APPLICABLE), the worst FDTI / FRTI / FHTI of
each FSR with the scenario that produced it, and a simulation-based failure-mode table (per fault: which mechanism
detected it and when, what reacted, the final actual bridge, the peaks and the requirements it failed).

A catalog scenario that exists to demonstrate a DIFFERENT design (its own ``overrides``: a mis-tuned monitor, a
wrong policy, another restart mode) is run with the design under evaluation instead - its fault and operating point
are kept, its design overrides are not - and a scenario that then duplicates an earlier one is skipped.  A PASS in
the matrix is a statement about these trajectories only (see the campaign module for explored sets); a FAIL is a
reproducible counterexample.
"""

from __future__ import annotations

import copy

from ... import progress
from .campaign import code_identity, project_identity, run_one
from .labels import fault_text
from .safety import FAIL, NA, PASS, UNKNOWN

ORDER = {FAIL: 3, UNKNOWN: 2, PASS: 1, NA: 0}


def catalog_for_design(scenarios: list, overrides: dict | None) -> tuple[list, list]:
    """(runs, skipped): the catalog scenarios with the design variant ``overrides`` instead of their own design
    overrides, duplicates removed."""
    from .study import scenario_digest
    runs, seen, skipped = [], {}, []
    for s in scenarios:
        sc = copy.deepcopy(s["scenario"])
        own = sc.pop("overrides", None)
        sc.pop("reaction_override", None)
        sc.pop("protection", None)
        if overrides:
            sc["overrides"] = dict(overrides)
        d = scenario_digest(sc)
        if d in seen:
            skipped.append({"key": s["key"], "title": s.get("title", {}), "same_as": seen[d],
                            "why": "its own design overrides removed, it repeats an earlier scenario"})
            continue
        seen[d] = s["key"]
        runs.append({"key": s["key"], "title": s.get("title", {}), "category": s.get("category", ""),
                     "scenario": sc, "own_overrides_removed": bool(own)})
    return runs, skipped


def _fault_row(run, res, verdicts) -> dict:
    ev = res.events
    faults = [f for f in res.setup_echo.get("faults", [])]
    det = next((e for e in ev if e["kind"] == "detection"), None)
    act = next((e for e in ev if e["kind"] == "actuation"), None)
    s = res.summary
    return {"fault": "; ".join(f"{fault_text(f['kind'], f.get('params'))} at {f['t_s'] * 1e3:.4g} ms" for f in faults)
                     or "no fault",
            "detected_by": det["source"] if det else None, "t_detect_ms": det["t"] * 1e3 if det else None,
            "detection": det["text"] if det else "not detected",
            "reaction": act.get("reaction") if act else None, "t_reaction_ms": act["t"] * 1e3 if act else None,
            "reaction_text": act["text"] if act else "no reaction",
            "final_bridge": s["final_bridge"], "final_actual": s["final_actual"],
            "i_phase_peak_A": s["i_phase_peak_A"], "v_dc_max_V": s["v_dc_max_V"], "i_d_min_A": s["i_d_min_A"],
            "T_brake_max_Nm": s["T_brake_max_Nm"],
            "failing": sorted(k for k, v in verdicts.items() if v == FAIL),
            "unknown": sorted(k for k, v in verdicts.items() if v == UNKNOWN),
            "status": res.status}


def verification_matrix(product, scenarios: list, overrides: dict | None = None) -> dict:
    """Run the catalog on one design and collect the evidence (see the module note)."""
    runs, skipped = catalog_for_design(scenarios, overrides)
    rows = []
    with progress.span(len(runs), "verification scenarios") as sp:
        for r in runs:
            out = run_one(product, r["scenario"], keep_trace=True)
            res, ev = out["result"], out["evaluation"]
            rows.append({**{k: r[k] for k in ("key", "title", "category", "own_overrides_removed")},
                         "verdicts": out["verdicts"], "metrics": out["metrics"],
                         "timelines": {f["id"]: f["timeline"] for f in ev["fsr"]},
                         "fmea": _fault_row(r, res, out["verdicts"])})
            sp.step(r["key"])
    req_ids = []
    if rows:
        req_ids = list(rows[0]["verdicts"])
    cells = {q: {r["key"]: r["verdicts"].get(q, NA) for r in rows} for q in req_ids}
    coverage = {}
    for q in req_ids:
        vs = list(cells[q].values())
        coverage[q] = {"exercised": sum(1 for v in vs if v != NA), "pass": vs.count(PASS), "fail": vs.count(FAIL),
                       "unknown": vs.count(UNKNOWN), "not_applicable": vs.count(NA),
                       "worst": max(vs, key=lambda v: ORDER[v]) if vs else NA,
                       "failing_in": [k for k, v in cells[q].items() if v == FAIL]}
    timing = {}
    for r in rows:
        for fid, tl in r["timelines"].items():
            d = timing.setdefault(fid, {})
            for key in ("FDTI", "FRTI", "FHTI"):
                v = tl.get(key)
                if v is not None and (key not in d or v > d[key]["value_s"]):
                    d[key] = {"value_s": v, "scenario": r["key"]}
    not_exercised = [q for q, c in coverage.items() if c["exercised"] == 0]
    return {"rows": rows, "requirements": req_ids, "cells": cells, "coverage": coverage, "timing": timing,
            "skipped": skipped, "overrides": dict(overrides or {}), "not_exercised": not_exercised,
            "project": project_identity(product.project), "code": code_identity(),
            "statement": f"{len(rows)} trajectories on one design: a PASS holds for these trajectories only; every "
                         f"FAIL is a reproducible counterexample" + (
                             f"; never exercised: {', '.join(not_exercised)}" if not_exercised else "")}
