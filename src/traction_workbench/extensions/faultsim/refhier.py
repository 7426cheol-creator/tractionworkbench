"""The requirement hierarchy of a reference package: every item's level (vehicle safety goal, top-level safety
requirement, functional and technical safety requirement, safety mechanism, verification - and the kinds that are not
requirements: definitions, method rules, actions, questions, open inputs, outputs, simulation modules, research
examples, study questions), its trace to the items above it (as the source states it, or inferred - kept apart and
marked), the verdict rolled up over each subtree and the gaps the structure shows (a requirement never refined, a
mechanism never demonstrated, a technical requirement nothing verifies, a top-level requirement whose text is
missing, failing evidence below a requirement).

Level and traces are package content (``level``, ``traces_to``, ``traces_to_inferred``); an item without a level gets
one inferred from its kind (and is listed as such).  A proposal (``proposed``: true) is a DERIVED addition: it sits in
the tree where it belongs but is never counted as a source requirement; a gap it addresses names it.
"""

from __future__ import annotations

from collections import Counter

REQ_LEVELS = ("SG", "TLSR", "FSR", "TSR", "SM", "VER")
OTHER_LEVELS = ("DEF", "RULE", "ACT", "Q", "OPEN", "OUT", "MOD", "RES", "STUDY")
LEVELS = REQ_LEVELS + OTHER_LEVELS
LEVEL_NAMES = {
    "SG": ("차량 안전목표 (SG)", "vehicle safety goal (SG)"),
    "TLSR": ("최상위 안전요구 (TLSR)", "top-level safety requirement (TLSR)"),
    "FSR": ("기능 안전요구 (FSR)", "functional safety requirement (FSR)"),
    "TSR": ("기술 안전요구 (TSR)", "technical safety requirement (TSR)"),
    "SM": ("안전 메커니즘 (SM)", "safety mechanism (SM)"),
    "VER": ("검증·시나리오", "verification / scenario"),
    "DEF": ("정의·범위", "definition / scope"),
    "RULE": ("방법 규칙", "method rule"),
    "ACT": ("추가 업무", "action"),
    "Q": ("확인 질문", "question"),
    "OPEN": ("OPEN 입력", "open input"),
    "OUT": ("산출물", "output"),
    "MOD": ("시뮬레이션 모듈", "simulation module"),
    "RES": ("연구 예시", "research example"),
    "STUDY": ("연구 질문", "study question"),
}
KIND_LEVEL = {"goal": "STUDY", "tsr": "TSR", "mechanism": "SM", "safety_mechanism": "SM", "verification": "VER",
              "scenario": "VER", "action": "ACT", "question": "Q", "open": "OPEN", "output": "OUT", "module": "MOD",
              "research": "RES", "rule": "RULE", "timing": "DEF", "definition": "DEF", "requirement": "FSR"}
RANK = {lv: i for i, lv in enumerate(LEVELS)}
ROLL_ORDER = ("FAIL", "CONFLICT", "UNKNOWN", "PASS", "MANUAL", "NOT_APPLICABLE")
# the provenance tags whose items are the source's own requirements when a package does not name them (the
# package field keeps its historical name ``customer_tags``; CUSTOMER-PAST is the older spelling of PAST-PROJECT)
DEFAULT_SOURCE_TAGS = ("CONFIRMED", "PAST-PROJECT", "CUSTOMER-PAST", "PROJECT")
# the result sets of a run (``ReferenceRunner.summary()["counts"]`` keys; "customer" is the historical key of
# the source requirements)
SET_NAMES = {"customer": ("원문 요구", "source requirements"),
             "internal": ("내부 (DERIVED·RESEARCH·OPEN 등)", "internal (derived, research, open …)"),
             "illustrative": ("예시 값으로 판정 (ILL)", "judged with illustrative values (ILL)"),
             "proposed": ("제안 (DERIVED)", "proposals (DERIVED)")}


def level_of(item: dict) -> tuple[str, bool]:
    """The item's level and whether it was inferred (no ``level`` in the package: from its kind)."""
    lv = item.get("level")
    if lv in LEVELS:
        return lv, False
    return KIND_LEVEL.get(item.get("kind") or "", "FSR"), True


def roll(counts: Counter) -> str | None:
    """The verdict of a subtree: FAIL > CONFLICT > UNKNOWN > PASS; MANUAL only when nothing machine-checked."""
    return next((v for v in ROLL_ORDER if counts.get(v)), None)


def _machine(item: dict) -> bool:
    return any(c.get("check") not in ("manual", "question") for c in item.get("checks") or [])


def hierarchy(pkg: dict, rows: list | None = None) -> dict:
    """The hierarchy of ``pkg`` with the verdicts of ``rows`` (``ReferenceRunner.summary()["rows"]``; None: the
    structure only)."""
    items = pkg.get("items") or []
    by_id = {it["id"]: it for it in items}
    verdict = {r["id"]: r["verdict"] for r in rows or []}
    cust = set(pkg.get("customer_tags") or DEFAULT_SOURCE_TAGS)
    level, inferred_level = {}, []
    for it in items:
        lv, inf = level_of(it)
        level[it["id"]] = lv
        if inf:
            inferred_level.append(it["id"])
    parents, unknown = {}, []
    for it in items:
        ps = []
        for key, basis in (("traces_to", "source"), ("traces_to_inferred", "inferred")):
            for p in it.get(key) or []:
                if p not in by_id:
                    unknown.append([it["id"], p])
                elif p != it["id"] and p not in [x for x, _b in ps]:
                    ps.append((p, basis))
        parents[it["id"]] = ps
    children = {i: [] for i in by_id}
    for c, ps in parents.items():
        for p, basis in ps:
            children[p].append((c, basis))
    for p in children:                     # by level; within a level the items whose text is missing last
        children[p].sort(key=lambda cb: (RANK[level[cb[0]]], by_id[cb[0]].get("provenance") == "OPEN", cb[0]))

    def descendants(i: str) -> set:
        out, stack = set(), [i]
        while stack:
            for c, _b in children[stack.pop()]:
                if c not in out:
                    out.add(c)
                    stack.append(c)
        out.discard(i)
        return out
    desc = {i: descendants(i) for i in by_id}
    rollup = {}
    for i in by_id:
        counts = Counter(verdict[d] for d in desc[i] | {i} if d in verdict and level[d] in REQ_LEVELS)
        rollup[i] = {"verdict": roll(counts), "counts": dict(counts)}
    req_parent = {i: [p for p, _b in parents[i] if level[p] in REQ_LEVELS] for i in by_id}
    roots = sorted((i for i in by_id if level[i] in REQ_LEVELS and not req_parent[i]),
                   key=lambda i: (RANK[level[i]], i))
    loose = sorted((i for i in by_id if level[i] in OTHER_LEVELS and not parents[i]),
                   key=lambda i: (RANK[level[i]], i))
    proposals_for = {}
    for it in items:
        if it.get("proposed"):
            for p, _b in parents[it["id"]]:
                proposals_for.setdefault(p, []).append(it["id"])
    gaps = []

    def gap(i, code, text):
        gaps.append({"id": i, "level": level[i], "code": code, "text": text, "customer": by_id[i].get("provenance")
                     in cust, "proposals": sorted(proposals_for.get(i, []))})
    for it in items:
        i, lv = it["id"], level[it["id"]]
        if it.get("proposed") or lv not in REQ_LEVELS:
            continue
        below = {level[c] for c, _b in children[i]}
        if it.get("provenance") == "OPEN" and lv in ("SG", "TLSR", "FSR"):
            gap(i, "text_missing", "the requirement text is not provided (OPEN): nothing below it can be derived")
            continue                                     # (refinement cannot be asked of a text that is missing)
        if lv == "SG" and not below & {"TLSR", "FSR"}:
            gap(i, "not_broken_down", "no top-level or functional requirement traces to this goal")
        if lv == "TLSR" and not below & {"FSR", "TSR"}:
            gap(i, "not_refined", "no functional or technical requirement refines it")
        if lv == "FSR" and not below & {"TSR", "SM"} and not _machine(it):
            gap(i, "not_refined", "not refined into a technical requirement or a mechanism, and not checked itself")
        if lv == "TSR" and not below & {"SM", "VER"} and not _machine(it):
            gap(i, "not_verified", "no mechanism allocated, no verification traces to it, no check of its own")
        if lv == "SM":
            demo = any(c.get("check") == "sm_spec" and (c.get("params") or {}).get("demonstrated_by")
                       for c in it.get("checks") or [])
            if not demo and not _machine(it):
                gap(i, "not_demonstrated", "the mechanism is not demonstrated (detection -> reaction) by a check")
            elif not demo and all(c.get("check") == "sm_spec" for c in it.get("checks") or []):
                gap(i, "not_demonstrated", "specification only: no detection -> reaction demonstration")
        if lv in ("SG", "TLSR", "FSR", "TSR") and rollup[i]["verdict"] == "FAIL":
            bad = sorted(d for d in desc[i] | {i} if verdict.get(d) == "FAIL" and level[d] in REQ_LEVELS)
            gap(i, "failing_below", "failing evidence in its subtree: " + ", ".join(bad[:8])
                + (" …" if len(bad) > 8 else ""))
    stats = {}
    for lv in LEVELS:
        ids = [i for i in by_id if level[i] == lv]
        if not ids:
            continue
        stats[lv] = {"items": len(ids), "customer": sum(1 for i in ids if by_id[i].get("provenance") in cust),
                     "proposed": sum(1 for i in ids if by_id[i].get("proposed")),
                     "machine_checked": sum(1 for i in ids if _machine(by_id[i])),
                     "traced": sum(1 for i in ids if parents[i]),
                     "inferred_links": sum(1 for i in ids for _p, b in parents[i] if b == "inferred"),
                     "verdicts": dict(Counter(verdict[i] for i in ids if i in verdict))}
    return {"levels": level, "inferred_level": inferred_level,
            "parents": {i: [[p, b] for p, b in ps] for i, ps in parents.items()},
            "children": {i: [[c, b] for c, b in cs] for i, cs in children.items()},
            "roots": roots, "loose": loose, "rollup": rollup, "gaps": gaps, "stats": stats,
            "unknown_refs": unknown}


def level_name(lv: str) -> str:
    from ...i18n import tr
    ko, en = LEVEL_NAMES.get(lv, (lv, lv))
    return tr(ko, en)


def set_name(key: str) -> str:
    from ...i18n import tr
    ko, en = SET_NAMES.get(key, (key, key))
    return tr(ko, en)
