"""Campaigns: many causal runs from one base scenario, and what they establish - and what they do not.

A campaign varies declared axes of the scenario (operating point, request, fault size and time, initial electrical
angle, tolerance corners, design variants through ``overrides``) on a grid or a seeded random sample.  Every run is a
complete causal simulation judged against the project's requirements.  The campaign reports

* per run: the verdict of every requirement, the peaks (|i_phase|, v_dc, charging current, torque), the first
  detection and FDTI / FRTI / FHTI of the FSRs - each from ONE trajectory;
* worst cases per quantity WITH the run that produced it (a worst FHTI is one run's FHTI; the sum of the worst FDTI
  and the worst FRTI from different runs is reported only as a bound, labelled as such, never as a trajectory);
* failure boundaries: between two neighbouring grid points with different verdicts the boundary is bracketed by
  bisection along that axis (optional, extra runs), reported as an interval, not a point;
* sensitivity: per axis, how much each quantity moves along that axis (grid: the mean over the other axes of the
  range along it; random: Spearman rank correlation);
* counterexamples: every failing run as a record that can be saved, reloaded and re-run: the full scenario, the
  project identity (section digests), the code version, the verdicts and the metrics.  A re-run on a project whose
  relevant sections changed is marked stale before its verdict is compared.

Scope.  A PASS over the explored set is a statement about those runs only; the continuous region between the
samples is not established by sampling (the behaviour is discontinuous at reaction thresholds, as the boundary
search shows).  A FAIL in any run is a valid counterexample for the scenario it names.
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import itertools
import json
import math
import subprocess
from pathlib import Path

import numpy as np

from ...errors import InputValidationError
from ... import progress
from .safety import FAIL, NA, PASS, UNKNOWN, evaluate

SCHEMA = "twb-fault-counterexamples/1"
RELEVANT_SECTIONS = ("drive", "dc_source", "dc_link", "controller", "driveline", "fault_sim")
METRICS = ("i_phase_peak_A", "v_dc_max_V", "v_dc_min_V", "i_bat_charge_max_A", "T_shaft_max_Nm", "T_shaft_min_Nm",
           "first_detection_ms", "FDTI_ms", "FRTI_ms", "FHTI_ms")


# ------------------------------------------------------------------------------------------ scenario paths

def set_path(sc: dict, path: str, value) -> dict:
    """A copy of the scenario with one value set: ``speed_rpm``, ``request.T1_Nm``, ``faults.0.t_ms``,
    ``faults.0.params.value``, ``tolerances.CS_A.gain_err`` (the key after tolerances / overrides is taken whole)."""
    sc = copy.deepcopy(sc)
    keys = str(path).split(".")
    if keys[0] in ("tolerances", "overrides"):
        sc.setdefault(keys[0], {})[".".join(keys[1:])] = value
        return sc
    node = sc
    for i, k in enumerate(keys):
        last = i == len(keys) - 1
        if isinstance(node, list):
            if not k.isdigit() or int(k) >= len(node):
                raise InputValidationError(f"campaign axis {path}: no list item {k}", field=f"axes.{path}")
            if last:
                node[int(k)] = value
            else:
                node = node[int(k)]
        else:
            if last:
                node[k] = value
            else:
                node = node.setdefault(k, {})
    return sc


def get_path(sc: dict, path: str):
    keys = str(path).split(".")
    if keys[0] in ("tolerances", "overrides"):
        return (sc.get(keys[0]) or {}).get(".".join(keys[1:]))
    node = sc
    for k in keys:
        if isinstance(node, list):
            node = node[int(k)]
        else:
            node = (node or {}).get(k)
    return node


def axis_values(ax: dict) -> list:
    if ax.get("values") is not None:
        vals = list(ax["values"])
    elif ax.get("range") is not None:
        lo, hi = (float(v) for v in ax["range"])
        n = int(ax.get("n") or 5)
        vals = [lo + (hi - lo) * i / max(1, n - 1) for i in range(n)]
    else:
        raise InputValidationError(f"axis {ax.get('path')} needs values or range", field="axes")
    if not vals:
        raise InputValidationError(f"axis {ax.get('path')} has no values", field="axes")
    return vals


# ------------------------------------------------------------------------------------------ identity

def code_identity() -> dict:
    from ... import __version__
    commit = None
    try:
        root = Path(__file__).resolve().parents[4]
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                           timeout=5)
        if r.returncode == 0:
            commit = r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"version": __version__, "commit": commit}


def project_identity(project) -> dict:
    return project.identity([n for n in RELEVANT_SECTIONS if project.has(n)] + (
        ["fault_sim"] if not project.has("fault_sim") else []))


def stale_against(record: dict, project) -> list:
    """Sections (and the project itself) whose content changed since the record was made."""
    used = (record.get("project") or {})
    if not used:
        return ["<no identity>"]
    out = []
    if used.get("project_id") != project.id:
        out.append("<project>")
    for n, d in (used.get("sections") or {}).items():
        now = project.sections[n].digest if project.has(n) else None
        if now != d:
            out.append(n)
    return out


# ------------------------------------------------------------------------------------------ one run

def run_one(product, scenario: dict, keep_trace: bool = False) -> dict:
    """Build, simulate and judge one scenario on a product (``configure.ProductData``); a compact result (the full
    result on request)."""
    from .configure import build_setup
    from .engine import simulate
    setup, reqs, info = build_setup(product, scenario)
    res = simulate(setup)
    ev = evaluate(res, reqs, setup)
    s = res.summary
    fsr_t = next((f["timeline"] for f in ev["fsr"] if f["timeline"].get("FHTI") is not None),
                 next((f["timeline"] for f in ev["fsr"] if f["timeline"].get("t_D") is not None), None))
    ms = lambda v: None if v is None else 1e3 * float(v)          # noqa: E731
    first = s.get("first_detection")
    metrics = {"i_phase_peak_A": s["i_phase_peak_A"], "v_dc_max_V": s["v_dc_max_V"], "v_dc_min_V": s["v_dc_min_V"],
               "i_bat_charge_max_A": s["i_bat_charge_max_A"], "T_shaft_max_Nm": s["T_shaft_max_Nm"],
               "T_shaft_min_Nm": s["T_shaft_min_Nm"], "first_detection_ms": ms(first["t"]) if first else None,
               "FDTI_ms": ms(fsr_t.get("FDTI")) if fsr_t else None, "FRTI_ms": ms(fsr_t.get("FRTI")) if fsr_t else None,
               "FHTI_ms": ms(fsr_t.get("FHTI")) if fsr_t else None}
    verdicts = {r["id"]: r["verdict"] for r in ev["tsr"]}
    verdicts.update({f["id"]: f["verdict"] for f in ev["fsr"]})
    verdicts.update({g["id"]: g["inverter_evidence"] for g in ev["sg"]})
    details = {r["id"]: r.get("detail", "") for r in ev["tsr"]}
    out = {"status": res.status, "stop_reason": res.stop_reason, "verdicts": verdicts, "details": details,
           "metrics": metrics, "first_detection": first, "zeno": s.get("zeno"),
           "energy_relative_residual": s["energy"]["relative_residual"]}
    if keep_trace:
        out.update(result=res, evaluation=ev, setup=setup, info=info)
    return out


def _overall(verdicts: dict) -> str:
    vs = [v for v in verdicts.values() if v != NA]
    if FAIL in vs:
        return FAIL
    if UNKNOWN in vs:
        return UNKNOWN
    return PASS if vs else NA


def counterexample_record(product, scenario: dict, run: dict, note: str = "") -> dict:
    body = {"scenario": scenario, "verdicts": run["verdicts"], "metrics": run["metrics"]}
    rid = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return {"id": rid, "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "scenario": copy.deepcopy(scenario), "project": project_identity(product.project),
            "code": code_identity(),
            "verdicts": dict(run["verdicts"]), "failing": sorted(k for k, v in run["verdicts"].items() if v == FAIL),
            "metrics": dict(run["metrics"]), "status": run["status"], "stop_reason": run["stop_reason"],
            "note": note or "failing run of a fault-simulation campaign / study"}


def rerun_record(product, record: dict) -> dict:
    """Re-run a saved counterexample on the current project and code: stale inputs are reported first, then the
    verdicts and metrics are compared (a reproduction is the same verdicts and metrics within 1e-6 relative)."""
    stale = stale_against(record, product.project)
    code_now = code_identity()
    run = run_one(product, record["scenario"])
    same_v = run["verdicts"] == record.get("verdicts")
    diffs = {}
    for k, v in (record.get("metrics") or {}).items():
        w = run["metrics"].get(k)
        if v is None or w is None:
            if v != w:
                diffs[k] = (v, w)
        elif abs(w - v) > 1e-6 * max(1.0, abs(v)):
            diffs[k] = (v, w)
    return {"record": record["id"], "stale_sections": stale, "code_then": record.get("code"), "code_now": code_now,
            "code_changed": (record.get("code") or {}).get("commit") != code_now.get("commit")
            or (record.get("code") or {}).get("version") != code_now.get("version"),
            "reproduced": same_v and not diffs, "verdicts_now": run["verdicts"], "metric_changes": diffs,
            "still_failing": sorted(k for k, v in run["verdicts"].items() if v == FAIL),
            "statement": ("reproduced on the same inputs" if same_v and not diffs and not stale else
                          "inputs changed since the record (" + ", ".join(stale) + "): the comparison shows the "
                          "effect of the change" if stale else
                          "not reproduced on the same project inputs: the code changed" if not same_v or diffs
                          else "reproduced")}


def save_records(path, records: list) -> None:
    Path(path).write_text(json.dumps({"schema": SCHEMA, "records": records}, indent=1, default=str),
                          encoding="utf-8")


def load_records(path) -> list:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA:
        raise InputValidationError(f"not a counterexample file ({SCHEMA})", field="file")
    return list(d.get("records") or [])


# ------------------------------------------------------------------------------------------ the campaign

def run_campaign(product, spec: dict) -> dict:
    """Run a campaign: ``{"base": scenario, "axes": [{"path", "values" | "range" + "n"}], "mode": "grid" |
    "random", "n_random", "seed", "boundary_refinements", "requirements": [ids to track, default all]}``."""
    base = copy.deepcopy(spec.get("base") or {})
    axes = list(spec.get("axes") or [])
    if not axes:
        raise InputValidationError("a campaign needs at least one axis", field="axes")
    mode = spec.get("mode", "grid")
    vals = [axis_values(a) for a in axes]
    if mode == "grid":
        points = [dict(zip([a["path"] for a in axes], combo)) for combo in itertools.product(*vals)]
    elif mode == "random":
        rng = np.random.default_rng(int(spec.get("seed", 1)))
        n = int(spec.get("n_random") or 20)
        points = []
        for _ in range(n):
            p = {}
            for a, v in zip(axes, vals):
                if a.get("range") is not None and a.get("values") is None:
                    lo, hi = (float(x) for x in a["range"])
                    p[a["path"]] = float(lo + (hi - lo) * rng.random())
                else:
                    p[a["path"]] = v[int(rng.integers(len(v)))]
            points.append(p)
    else:
        raise InputValidationError("campaign mode must be grid or random", field="mode")
    if len(points) > int(spec.get("max_runs", 400)):
        raise InputValidationError(f"{len(points)} runs exceed the campaign limit {spec.get('max_runs', 400)}",
                                   field="axes")
    runs = []
    with progress.span(len(points), "fault campaign") as sp:
        for i, p in enumerate(points):
            sc = base
            for path, v in p.items():
                sc = set_path(sc, path, v)
            try:
                r = run_one(product, sc)
                r["overall"] = _overall(r["verdicts"])
            except InputValidationError as exc:
                r = {"status": "invalid", "stop_reason": str(exc), "verdicts": {}, "details": {}, "metrics": {},
                     "overall": UNKNOWN}
            r.update(index=i, point=p, scenario=sc)
            runs.append(r)
            sp.step(f"{i + 1}/{len(points)}")
    out = {"runs": runs, "axes": [{"path": a["path"], "values": v} for a, v in zip(axes, vals)], "mode": mode,
           "base": base, "project": project_identity(product.project), "code": code_identity()}
    out["summary"] = summarize(runs)
    out["worst"] = worst_cases(runs)
    out["sensitivity"] = sensitivity(runs, axes, vals, mode)
    out["boundaries"] = boundaries(product, runs, axes, vals, mode, int(spec.get("boundary_refinements") or 0))
    out["counterexamples"] = [counterexample_record(product, r["scenario"], r) for r in runs
                              if r.get("overall") == FAIL]
    n = len(runs)
    nf = sum(1 for r in runs if r.get("overall") == FAIL)
    nu = sum(1 for r in runs if r.get("overall") == UNKNOWN)
    out["scope"] = {
        "explored_set": FAIL if nf else (UNKNOWN if nu else PASS),
        "explored_text": (f"{nf} of {n} runs fail: each is a counterexample for its scenario" if nf else
                          f"{nu} of {n} runs undecided (see their reasons)" if nu else
                          f"all {n} explored runs pass"),
        "region": "NOT_ESTABLISHED",
        "region_text": "sampling does not bound the behaviour between the samples (reaction thresholds make it "
                       "discontinuous); a PASS over this set is not a guarantee for the continuous region"}
    return out


def summarize(runs: list) -> dict:
    req = {}
    for r in runs:
        for k, v in r["verdicts"].items():
            d = req.setdefault(k, {PASS: 0, FAIL: 0, UNKNOWN: 0, NA: 0})
            d[v] = d.get(v, 0) + 1
    return {"n": len(runs), "overall": {v: sum(1 for r in runs if r.get("overall") == v)
                                        for v in (PASS, FAIL, UNKNOWN, NA)},
            "per_requirement": req}


def worst_cases(runs: list) -> dict:
    """Per metric the worst value and the ONE run that produced it (never a combination of runs)."""
    out = {}
    hi = ("i_phase_peak_A", "v_dc_max_V", "i_bat_charge_max_A", "T_shaft_max_Nm", "first_detection_ms", "FDTI_ms",
          "FRTI_ms", "FHTI_ms")
    lo = ("v_dc_min_V", "T_shaft_min_Nm")
    for k in hi + lo:
        cand = [(r["metrics"].get(k), r) for r in runs if r.get("metrics") and r["metrics"].get(k) is not None]
        if not cand:
            continue
        v, r = (max if k in hi else min)(cand, key=lambda c: c[0])
        out[k] = {"value": v, "run": r["index"], "point": r["point"]}
    if "FDTI_ms" in out and "FRTI_ms" in out:
        out["FDTI_plus_FRTI_bound_ms"] = {
            "value": out["FDTI_ms"]["value"] + out["FRTI_ms"]["value"], "run": None, "point": None,
            "note": "sum of worst values from possibly different runs: a bound, not a trajectory; compare with the "
                    "worst FHTI of one run"}
    return out


def _spearman(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or np.all(x == x[0]) or np.all(y == y[0]):
        return None
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


def sensitivity(runs: list, axes: list, vals: list, mode: str) -> list:
    out = []
    for j, a in enumerate(axes):
        path = a["path"]
        num = all(isinstance(r["point"].get(path), (int, float)) for r in runs)
        for mk in METRICS:
            pts = [(r["point"][path], r["metrics"].get(mk), r) for r in runs
                   if r.get("metrics") and r["metrics"].get(mk) is not None]
            if len(pts) < 2:
                continue
            if mode == "grid":
                groups = {}
                for p, v, r in pts:
                    key = tuple((k, str(r["point"][k])) for k in r["point"] if k != path)
                    groups.setdefault(key, []).append(v)
                ranges = [max(g) - min(g) for g in groups.values() if len(g) > 1]
                if not ranges:
                    continue
                out.append({"axis": path, "metric": mk, "mean_range": float(np.mean(ranges)),
                            "max_range": float(np.max(ranges)), "kind": "range along the axis (others fixed)"})
            elif num:
                rho = _spearman([p for p, _, _ in pts], [v for _, v, _ in pts])
                if rho is not None:
                    out.append({"axis": path, "metric": mk, "spearman": rho, "kind": "rank correlation"})
    return out


def boundaries(product, runs: list, axes: list, vals: list, mode: str, refinements: int) -> list:
    """Verdict changes between neighbouring grid points, bracketed by bisection along the axis (numeric axes)."""
    if mode != "grid":
        return []
    out = []
    by_point = {tuple(str(r["point"][a["path"]]) for a in axes): r for r in runs}
    for j, a in enumerate(axes):
        path = a["path"]
        v = vals[j]
        if not all(isinstance(x, (int, float)) for x in v):
            continue
        for r in runs:
            key = [str(r["point"][ax["path"]]) for ax in axes]
            i = v.index(r["point"][path]) if r["point"][path] in v else None
            if i is None or i + 1 >= len(v):
                continue
            key[j] = str(v[i + 1])
            nb = by_point.get(tuple(key))
            if nb is None or nb.get("overall") == r.get("overall"):
                continue
            lo_v, hi_v = float(r["point"][path]), float(v[i + 1])
            lo_o, hi_o = r.get("overall"), nb.get("overall")
            n_extra = 0
            for _ in range(refinements):
                mid = 0.5 * (lo_v + hi_v)
                sc = set_path(r["scenario"], path, mid)
                try:
                    m = run_one(product, sc)
                    o = _overall(m["verdicts"])
                except InputValidationError:
                    break
                n_extra += 1
                if o == lo_o:
                    lo_v = mid
                else:
                    hi_v, hi_o = mid, o
            out.append({"axis": path, "others": {k: r["point"][k] for k in r["point"] if k != path},
                        "between": [lo_v, hi_v], "verdicts": [lo_o, hi_o], "extra_runs": n_extra,
                        "statement": f"the verdict changes from {lo_o} to {hi_o} for {path} in "
                                     f"[{lo_v:.6g}, {hi_v:.6g}] (others fixed)"})
    return out
