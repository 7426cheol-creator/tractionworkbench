"""Design variants of the protection architecture as reviewable changes.

An engineer edits a working copy of the project's ``fault_sim`` section (thresholds, debounce times, the FTTI, a
TSR's limit, a reaction strategy, the policy).  The variant a study runs is the DIFFERENCE to the project, as the
dotted-path ``overrides`` the configuration applies (``configure.apply_overrides``): every change is one line -
path, project value, study value - so a result, a scenario file and a counterexample carry exactly what differs
from the approved data, and the variant can later be written into the project as a new revision.

Paths: mapping keys joined by dots; list items addressed by their ``id`` (or ``name``) when both lists hold the same
ids in the same order, otherwise the whole list is one change (the policy rules, a strategy's steps); a removed key
is the value None.  An absent key, None and an empty mapping or list mean the same in this data (no parameters, no
conditions, no resources), so adding one of them is not a change.
"""

from __future__ import annotations

import copy

from ...errors import InputValidationError


def _item_id(x):
    if isinstance(x, dict):
        v = x.get("id", x.get("name"))
        return None if v is None else str(v)
    return None


def _empty(v) -> bool:
    return v is None or (isinstance(v, (dict, list)) and not v)


def _strip(v):
    """``v`` without the keys whose value is empty (see the module note), for comparing."""
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if not _empty(x)}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def diff_overrides(base, new, prefix: str = "") -> dict:
    """The dotted-path changes that turn ``base`` into ``new`` (see the module note)."""
    out: dict = {}
    if isinstance(base, dict) and isinstance(new, dict):
        for k in list(base) + [k for k in new if k not in base]:
            if "." in str(k):
                raise InputValidationError(f"a key with a dot cannot be addressed: {k!r}", field=prefix or "design")
            p = f"{prefix}.{k}" if prefix else str(k)
            if k not in new:
                if not _empty(base[k]):
                    out[p] = None
            elif k not in base:
                if not _empty(new[k]):
                    out[p] = copy.deepcopy(new[k])
            elif _empty(base[k]) and _empty(new[k]):
                continue
            else:
                out.update(diff_overrides(base[k], new[k], p))
        return out
    if isinstance(base, list) and isinstance(new, list):
        ib, inew = [_item_id(x) for x in base], [_item_id(x) for x in new]
        if (ib == inew and all(i is not None for i in ib) and len(set(ib)) == len(ib)
                and not any("." in i for i in ib)):
            for x, y, i in zip(base, new, ib):
                out.update(diff_overrides(x, y, f"{prefix}.{i}"))
            return out
        if _strip(base) != _strip(new):
            out[prefix] = copy.deepcopy(new)
        return out
    if base != new:
        out[prefix] = copy.deepcopy(new)
    return out


def get_at(data, path: str):
    """The value at a dotted path (list items by id / name or index); a missing key reads as None."""
    node = data
    for k in str(path).split("."):
        if isinstance(node, list):
            hit = None
            if k.isdigit() and int(k) < len(node):
                hit = node[int(k)]
            else:
                hit = next((it for it in node if isinstance(it, dict) and k in (it.get("id"), it.get("name"))), None)
            node = hit
        elif isinstance(node, dict):
            node = node.get(k)
        else:
            return None
        if node is None:
            return None
    return node


def _expand(path: str, old, new) -> list:
    """A change as rows: a whole-list change item by item - by id (added / removed / the changed fields of a kept
    item, recursively) or, for lists without ids, by position; anything else one row."""
    if not (isinstance(old, list) and isinstance(new, list)):
        return [{"path": path, "project": copy.deepcopy(old), "study": copy.deepcopy(new), "change": "changed"}]
    io, inew = [_item_id(x) for x in old], [_item_id(x) for x in new]
    rows = []
    if old and new and all(i is not None for i in io + inew):
        om, nm = dict(zip(io, old)), dict(zip(inew, new))
        for i in io + [i for i in inew if i not in om]:
            ip = f"{path}.{i}"
            if i not in nm:
                rows.append({"path": ip, "project": copy.deepcopy(om[i]), "study": None, "change": "removed"})
            elif i not in om:
                rows.append({"path": ip, "project": None, "study": copy.deepcopy(nm[i]), "change": "added"})
            else:
                for q, v in diff_overrides(om[i], nm[i], ip).items():
                    rows += _expand(q, get_at(om[i], q[len(ip) + 1:]), v)
        kept_old = [i for i in io if i in nm]
        kept_new = [i for i in inew if i in om]
        if kept_old != kept_new:
            rows.append({"path": path, "project": kept_old, "study": kept_new, "change": "reordered"})
        return rows
    for k in range(max(len(old), len(new))):
        a = old[k] if k < len(old) else None
        b = new[k] if k < len(new) else None
        if a != b:
            rows.append({"path": f"{path}[{k + 1}]", "project": copy.deepcopy(a), "study": copy.deepcopy(b),
                         "change": "added" if a is None else "removed" if b is None else "changed"})
    return rows


def change_rows(base: dict, overrides: dict | None) -> list:
    """The variant as rows people read: path, project value, study value (a whole-list change expanded item by
    item).  The overrides stay the variant a study runs; these rows only display it."""
    rows = []
    for p, v in (overrides or {}).items():
        rows += _expand(p, get_at(base, p), v)
    return rows
