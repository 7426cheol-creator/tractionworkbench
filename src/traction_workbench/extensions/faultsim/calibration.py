"""Safety calibration: a parameter set becomes active only as a complete, validated whole (never partially).

A parameter set carries its identity and integrity with its values:

    {"set_id", "version", "em_id", "motor_variant", "sw_compat", "crc_ok": bool, "complete": bool,
     "values": {dotted path into the fault-simulation section: value}, "versions": {part: version} (optional)}

It is ACCEPTED for a target (the product's em_id, motor variant and software compatibility) only if

    identity    em_id, motor variant and software compatibility match the target (a CRC-valid set of the other
                machine or variant is still the wrong set);
    integrity   the CRC is valid and the set is complete (an interrupted update is not a set);
    version     every part of the set carries the same version (no mixed generations between tasks);
    ranges      the data with the set applied passes the section validation (every mechanism parameter finite and
                in its range, every requirement criterion valid);
    constraints the static design review of the data with the set applied has no contradiction (e.g. a debounce
                that makes every detection later than the FDTI budget) and the declared ordering constraints hold.

Activation is atomic: a rejected set leaves the previously active set in place; the evidence lists each check.  A
configurable threshold is not a threshold that may change without these checks - the open-program configurability
and the protected activation are separate properties, both shown.
"""

from __future__ import annotations

import copy

from ...errors import InputValidationError

ACCEPT, REJECT = "ACCEPTED", "REJECTED"


def _get(data: dict, path: str):
    node = data
    for k in path.split("."):
        if isinstance(node, list):
            node = next((it for it in node if isinstance(it, dict) and k in (it.get("id"), it.get("name"))), None)
        elif isinstance(node, dict):
            node = node.get(k)
        else:
            return None
        if node is None:
            return None
    return node


def validate_set(data: dict, pset: dict, target: dict, constraints: tuple = ()) -> dict:
    """Check one parameter set against the target identity and the design data (the fault-simulation section);
    ``constraints``: ({"lt": [path_a, path_b]}, ...) ordering constraints between values after the set is applied."""
    from .configure import apply_overrides, validate_section
    from .review import review_section
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    for k in ("em_id", "motor_variant", "sw_compat"):
        if k in target:
            add(f"identity: {k}", pset.get(k) == target[k], f"set {pset.get(k)!r} vs target {target[k]!r}")
    add("integrity: CRC", bool(pset.get("crc_ok", True)), "CRC valid" if pset.get("crc_ok", True) else "CRC invalid")
    add("integrity: complete", bool(pset.get("complete", True)),
        "complete" if pset.get("complete", True) else "incomplete (interrupted update)")
    vers = set((pset.get("versions") or {}).values())
    if vers:
        add("version: one generation", len(vers | {pset.get("version")}) == 1,
            f"versions {sorted(map(str, vers | {pset.get('version')}))}")
    applied = None
    try:
        applied = apply_overrides(copy.deepcopy(data), dict(pset.get("values") or {}))
        validate_section(applied)
        add("ranges", True, "every value in its declared range")
    except InputValidationError as exc:
        add("ranges", False, f"{exc} ({getattr(exc, 'field', '')})")
    if applied is not None and all(c["ok"] for c in checks if c["check"] == "ranges"):
        rev = review_section(applied)
        bad = [f for f in rev.get("findings", []) if f.get("status") == "INCONSISTENT"]
        add("constraints: design review", not bad,
            "no contradiction" if not bad else "; ".join(f"{f['element']}: {f['check']}" for f in bad[:4]))
        for c in constraints:
            a, b = c["lt"]
            va, vb = _get(applied, a), _get(applied, b)
            ok = va is not None and vb is not None and float(va) < float(vb)
            add(f"constraint {a} < {b}", ok, f"{va!r} < {vb!r}")
    ok = all(c["ok"] for c in checks)
    return {"set_id": pset.get("set_id"), "decision": ACCEPT if ok else REJECT, "checks": checks}


def activation_sequence(data: dict, active: dict, updates: list, target: dict, constraints: tuple = ()) -> dict:
    """Apply candidate sets in order; the active set changes only on an ACCEPTED set (atomic activation)."""
    steps = []
    cur = active
    for u in updates:
        v = validate_set(data, u, target, constraints)
        if v["decision"] == ACCEPT:
            cur = u
        steps.append({"candidate": u.get("set_id"), "decision": v["decision"], "checks": v["checks"],
                      "active_after": cur.get("set_id")})
    return {"steps": steps, "active": cur.get("set_id")}
