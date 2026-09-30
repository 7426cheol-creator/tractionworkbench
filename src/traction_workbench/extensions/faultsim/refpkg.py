"""Reference packages: a requirement / scenario catalogue with provenance, verified item by item on the product.

A reference package (schema ``twb-reference/1``, JSON) is what a study or a customer specification asks the
simulation to show.  It keeps apart what the customer states, what a project assumes, what is derived internally and
what a study found - and never lets a missing value become a number:

``provenance``     tag -> meaning (e.g. CONFIRMED, CUSTOMER-PAST, PROJECT, DERIVED, RESEARCH, OPEN, CONFLICT);
``customer_tags``  the tags that are customer truth (the others are never presented as customer requirements);
``parameters``     the registry: id, unit, provenance, value (None for OPEN), an ``illustrative`` value used only by
                   the illustrative profile (results flagged, never a customer verdict), ``variants`` for a CONFLICT;
``asil_binding``   ASIL literal -> the parameter that resolves it (MAX -> the project's maximum ASIL parameter);
``scenarios``      id -> {title, scenario (the fault-simulation scenario, incl. ``system`` and ``vehicle``),
                   variants: name -> a patch merged into the scenario};
``items``          everything the package lists: requirements, scenario groups, safety mechanisms, verification
                   points, modules, actions, questions - each with its provenance, ASIL literal, agreement status and
                   the CHECKS that verify it (a check of ``refcheck`` with a scenario, a variant or the variants of a
                   conflict, and parameters; ``"$ID"`` refers to the registry).

Verdicts: PASS, FAIL, UNKNOWN (an OPEN value, an undeclared limit, the run left the model), CONFLICT (the variants of
an unresolved source disagree), MANUAL (organisational evidence to be recorded), NOT_APPLICABLE.  An item's verdict
is the worst of its checks.  The runner caches every simulated trajectory by the digest of its resolved scenario.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from ...errors import InputValidationError
from .refcheck import CHECKS, CONFLICT, MANUAL, ORDER, Params, worst
from .safestate import FAIL, NA, PASS, UNKNOWN

SCHEMA = "twb-reference/1"
VERDICTS = (PASS, FAIL, UNKNOWN, CONFLICT, MANUAL, NA)
DEFAULT_CUSTOMER_TAGS = ("CONFIRMED", "CUSTOMER-PAST", "PROJECT")


class OpenParameter(Exception):
    def __init__(self, pid: str):
        super().__init__(pid)
        self.pid = pid


# ------------------------------------------------------------------------------------------------- loading

def load_package(src) -> dict:
    if isinstance(src, dict):
        pkg = copy.deepcopy(src)
    else:
        pkg = json.loads(Path(src).read_text(encoding="utf-8"))
    problems = validate_package(pkg)
    hard = [p for p in problems if p["level"] == "error"]
    if hard:
        raise InputValidationError("reference package: " + "; ".join(p["text"] for p in hard[:5]), field="package")
    pkg["_problems"] = problems
    return pkg


def validate_package(pkg: dict) -> list:
    out = []

    def err(t):
        out.append({"level": "error", "text": t})

    def warn(t):
        out.append({"level": "warning", "text": t})
    if pkg.get("schema") != SCHEMA:
        err(f"schema must be {SCHEMA}")
    pids = [p.get("id") for p in pkg.get("parameters", [])]
    if len(set(pids)) != len(pids):
        err("parameter ids must be unique")
    tags = set(pkg.get("provenance") or {})
    for p in pkg.get("parameters", []):
        if tags and p.get("provenance") not in tags:
            err(f"parameter {p.get('id')}: provenance {p.get('provenance')!r} is not a declared tag")
    ids = [i.get("id") for i in pkg.get("items", [])]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        err(f"item ids must be unique ({', '.join(dup[:5])})")
    sc = pkg.get("scenarios") or {}
    for it in pkg.get("items", []):
        if tags and it.get("provenance") and it.get("provenance") not in tags:
            err(f"item {it.get('id')}: provenance {it.get('provenance')!r} is not a declared tag")
        for c in it.get("checks") or []:
            if c.get("check") not in CHECKS:
                err(f"item {it.get('id')}: unknown check {c.get('check')!r}")
            s = c.get("scenario")
            if s is not None and s not in sc:
                err(f"item {it.get('id')}: unknown scenario {s!r}")
            for v in ([c["variant"]] if c.get("variant") else []) + list(c.get("variants") or []):
                if s is not None and v not in (sc.get(s, {}).get("variants") or {}):
                    err(f"item {it.get('id')}: scenario {s} has no variant {v!r}")
        if not it.get("checks") and not it.get("manual"):
            warn(f"item {it.get('id')}: no check and no manual reason")
    return out


def _merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            out[k] = _merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(patch)


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class RunRecord:
    def __init__(self, key, scenario, result, setup, reqs, info):
        self.key, self.scenario, self.result, self.setup, self.reqs, self.info = key, scenario, result, setup, reqs, info


# ------------------------------------------------------------------------------------------------- the runner

class ReferenceRunner:
    """Verifies a package's items on a product (``configure.ProductData``).

    ``profile``: ``customer`` (OPEN values stay unknown) or ``illustrative`` (OPEN values take their illustrative
    value; every result that used one is flagged and kept apart from the customer verdicts).  ``values``: parameter
    values entered by the user for this run (id -> value; their provenance becomes USER)."""

    def __init__(self, product, package: dict, profile: str = "customer", values: dict | None = None,
                 progress=None, cancel=None):
        if profile not in ("customer", "illustrative"):
            raise InputValidationError("profile must be customer or illustrative", field="profile")
        self.product, self.package, self.profile = product, package, profile
        self.values = dict(values or {})
        self.progress, self.cancel = progress, cancel
        self.params = {p["id"]: p for p in package.get("parameters", [])}
        self.items = {i["id"]: i for i in package.get("items", [])}
        self.runs: dict = {}
        self.cache: dict = {}
        self.results: dict = {}
        self._used_illustrative = set()
        self._used_open = set()

    # -- parameters ------------------------------------------------------------------------------------------
    def param_value(self, pid: str):
        if pid in self.values:
            return self.values[pid]
        p = self.params.get(pid)
        if p is None:
            raise InputValidationError(f"unknown parameter ${pid}", field="package.parameters")
        if p.get("value") is not None:
            return p["value"]
        if self.profile == "illustrative" and p.get("illustrative") is not None:
            self._used_illustrative.add(pid)
            return p["illustrative"]
        self._used_open.add(pid)
        return None

    def resolve(self, obj, path="", open_refs=None, strict=False):
        """Replace "$ID" references; OPEN ones become None (``strict``: raise OpenParameter)."""
        open_refs = {} if open_refs is None else open_refs
        if isinstance(obj, str) and obj.startswith("$") and len(obj) > 1 and not obj.startswith("$$"):
            v = self.param_value(obj[1:])
            if v is None:
                if strict:
                    raise OpenParameter(obj[1:])
                open_refs[path.split(".")[-1] or path] = obj[1:]
            return v
        if isinstance(obj, dict):
            return {k: self.resolve(v, f"{path}.{k}" if path else k, open_refs, strict) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.resolve(v, f"{path}.{i}", open_refs, strict) for i, v in enumerate(obj)]
        return obj

    # -- data access for the checks ----------------------------------------------------------------------------
    def design_data(self) -> dict:
        from .configure import FAULT_SIM_EXAMPLE
        pr = self.product.project
        return copy.deepcopy(pr.data("fault_sim")) if pr.has("fault_sim") else copy.deepcopy(FAULT_SIM_EXAMPLE)

    def item(self, iid) -> dict:
        return self.items.get(iid) or {}

    def item_result(self, iid) -> dict:
        if iid not in self.results:
            self.results[iid] = self.check_item(self.items[iid])
        return self.results[iid]

    def cached(self, key, fn):
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]

    def scenario_dict(self, sid: str, variant: str | None = None, extra: dict | None = None) -> dict:
        s = (self.package.get("scenarios") or {}).get(sid)
        if s is None:
            raise InputValidationError(f"unknown scenario {sid}", field="scenario")
        sc = copy.deepcopy(s.get("scenario") or {})
        if variant:
            sc = _merge(sc, (s.get("variants") or {})[variant])
        if extra:
            sc = _merge(sc, extra)
        return self.resolve(sc, strict=True)

    def run(self, spec: dict) -> RunRecord:
        from .configure import build_setup
        from .engine import simulate
        sc = self.scenario_dict(spec["scenario"], spec.get("variant"), spec.get("patch"))
        key = digest(sc)
        if key not in self.runs:
            if self.cancel is not None and self.cancel():
                raise InterruptedError("cancelled")
            setup, reqs, info = build_setup(self.product, sc)
            res = simulate(setup)
            self.runs[key] = RunRecord(key, sc, res, setup, reqs, info)
        return self.runs[key]

    # -- items ---------------------------------------------------------------------------------------------------
    def _one(self, item, c, variant=None):
        spec = {"scenario": c.get("scenario"), "variant": variant if variant is not None else c.get("variant"),
                "patch": c.get("patch"), "_item": item["id"]}
        open_refs = {}
        self._used_illustrative = set()
        self._used_open = set()
        raw = dict(c.get("params") or {})
        resolved = self.resolve(raw, open_refs=open_refs)
        P = Params(raw, resolved, open_refs, set())
        try:
            r = CHECKS[c["check"]](self, spec, P)
        except OpenParameter as op:
            p = self.params.get(op.pid, {})
            r = {"verdict": UNKNOWN, "measured": {}, "expected": "",
                 "reasons": [f"the scenario needs {op.pid} ({p.get('provenance', 'OPEN')}) - no value"],
                 "evidence": {}}
            self._used_open.add(op.pid)
        except InterruptedError:
            raise
        except InputValidationError as exc:
            r = {"verdict": UNKNOWN, "measured": {}, "expected": "", "reasons": [f"input: {exc}"], "evidence": {}}
        r["check"] = c["check"]
        r["label"] = c.get("label", "")
        r["scenario"] = c.get("scenario")
        r["variant"] = spec["variant"]
        r["open"] = sorted(set(open_refs.values()) | self._used_open)
        r["illustrative"] = sorted(self._used_illustrative)
        return r

    def check_item(self, item: dict) -> dict:
        checks = []
        for c in item.get("checks") or []:
            vs = c.get("variants")
            if vs:
                per = [self._one(item, c, v) for v in vs]
                verd = [p["verdict"] for p in per]
                definite = {v for v in verd if v in (PASS, FAIL)}
                if c.get("mode", "conflict") == "conflict" and len(definite) > 1:
                    v = CONFLICT
                    reasons = [f"{p['variant']}: {p['verdict']}" for p in per] + [
                        "the variants of an unresolved source disagree - keep both until the source is bound"]
                else:
                    v = worst(verd)
                    reasons = [f"{p['variant']}: {p['verdict']}" for p in per] + (
                        ["the same verdict under every variant"] if len(set(verd)) == 1 else [])
                    if c.get("mode", "conflict") == "conflict" and len(set(verd)) > 1:
                        reasons.append("no two variants give different definite verdicts: not a conflict (yet)")
                checks.append({"check": c["check"], "label": c.get("label", ""), "scenario": c.get("scenario"),
                               "variant": "/".join(vs), "verdict": v, "reasons": reasons, "variants": per,
                               "measured": {p["variant"]: p["measured"] for p in per}, "expected": per[0]["expected"],
                               "open": sorted({o for p in per for o in p["open"]}),
                               "illustrative": sorted({o for p in per for o in p["illustrative"]}),
                               "evidence": {}})
            else:
                checks.append(self._one(item, c))
        if not checks:
            checks.append({"check": "manual", "verdict": MANUAL, "reasons": [item.get("manual") or
                                                                              "no machine check declared"],
                           "measured": {}, "expected": "", "open": [], "illustrative": [], "evidence": {}})
        v = worst([c["verdict"] for c in checks])
        illu = sorted({o for c in checks for o in c.get("illustrative", [])})
        return {"id": item["id"], "verdict": v, "checks": checks, "illustrative": illu,
                "open": sorted({o for c in checks for o in c.get("open", [])})}

    def run_all(self, ids=None) -> dict:
        items = [i for i in self.package.get("items", []) if ids is None or i["id"] in ids]
        for k, it in enumerate(items):
            if self.cancel is not None and self.cancel():
                raise InterruptedError("cancelled")
            if self.progress is not None:
                self.progress(k, len(items), it["id"])
            self.item_result(it["id"])
        return self.summary(ids)

    # -- outputs -------------------------------------------------------------------------------------------------
    def summary(self, ids=None) -> dict:
        cust = set(self.package.get("customer_tags") or DEFAULT_CUSTOMER_TAGS)
        rows = []
        for it in self.package.get("items", []):
            if ids is not None and it["id"] not in ids:
                continue
            r = self.results.get(it["id"])
            if r is None:
                continue
            rows.append({"id": it["id"], "group": it.get("group", ""), "kind": it.get("kind", ""),
                         "title": it.get("title", ""), "provenance": it.get("provenance", ""),
                         "customer": it.get("provenance") in cust, "asil_literal": it.get("asil_literal"),
                         "agreement": it.get("agreement"), "verdict": r["verdict"],
                         "illustrative": bool(r["illustrative"]), "open": r["open"],
                         "scenarios": sorted({c.get("scenario") for c in r["checks"] if c.get("scenario")}),
                         "checks": r["checks"]})
        counts: dict = {}
        for row in rows:
            key = ("illustrative" if row["illustrative"] else "customer" if row["customer"] else "internal")
            counts.setdefault(key, {}).setdefault(row["verdict"], 0)
            counts[key][row["verdict"]] += 1
        open_report: dict = {}
        for row in rows:
            for pid in row["open"]:
                p = self.params.get(pid, {})
                e = open_report.setdefault(pid, {"provenance": p.get("provenance", ""), "unit": p.get("unit", ""),
                                                 "note": p.get("note", ""), "items": []})
                e["items"].append(row["id"])
        conflicts = [{"id": r["id"], "checks": [c for c in r["checks"] if c["verdict"] == CONFLICT]}
                     for r in rows if r["verdict"] == CONFLICT]
        manual = [r["id"] for r in rows if r["verdict"] == MANUAL]
        return {"rows": rows, "counts": counts, "open_report": open_report, "conflicts": conflicts, "manual": manual,
                "runs": {k: {"scenario": v.scenario, "status": v.result.status} for k, v in self.runs.items()},
                "profile": self.profile, "package": {"title": (self.package.get("meta") or {}).get("title", ""),
                                                     "digest": digest({k: v for k, v in self.package.items()
                                                                       if not k.startswith("_")})}}


def verdict_order(v: str) -> int:
    return ORDER.get(v, 3)


def fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4g}" if math.isfinite(v) else str(v)
    return str(v)
