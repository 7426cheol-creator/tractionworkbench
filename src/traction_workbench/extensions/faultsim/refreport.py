"""The outputs of a reference-package verification: the evidence traces kept for plotting, the requirement-to-evidence
matrix as rows (CSV), and the whole verification as one self-contained HTML page.

The runner (``refpkg``) keeps every simulated trajectory while it verifies (later items reuse the runs); what a page
or a report needs afterwards is much smaller: per run a decimated trace (the extremes of every bucket are kept, so a
peak never disappears), its events and its six-point timeline.  The HTML report carries the verdicts, the checks with
their reasons, the OPEN parameters with the items waiting for each, the conflicts and the manual items - never a
guessed value.
"""

from __future__ import annotations

import html
import math

import numpy as np

from .refhier import SET_NAMES
from .safestate import fault_timeline

EVIDENCE_CHANNELS = ("t", "T_em", "T_shaft", "T_request", "T_cmd", "T_est_mon", "v_dc", "i_dc", "i_bat", "speed_rpm",
                     "bridge", "i_a", "i_b", "i_c", "tw_hi", "tw_lo", "tw_tol", "tw_int", "sys_permit", "sys_state",
                     "sys_confirmed", "src_LV", "src_HV", "rail_MCU", "rail_GATE", "rail_LOGIC", "itf_pos_limit")
EVENT_KINDS = ("fault", "fault_cleared", "detection", "actuation", "safe_state_request", "bridge", "supervisor",
               "input", "supply", "interface", "opstate", "controller", "selftest", "discharge", "plant")
VERDICT_ORDER = ("FAIL", "CONFLICT", "UNKNOWN", "MANUAL", "PASS", "NOT_APPLICABLE")
COLORS = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f", "CONFLICT": "#8250df", "MANUAL": "#0969da",
          "NOT_APPLICABLE": "#8c959f"}


def decimate_indices(t: np.ndarray, series: list, n_max: int = 3000) -> np.ndarray:
    """Sample indices that keep the first, the last and, per bucket, the extremes of every series."""
    n = len(t)
    if n <= n_max:
        return np.arange(n)
    buckets = max(1, n_max // (2 * max(1, len(series)) + 1))
    edges = np.linspace(0, n, buckets + 1).astype(int)
    keep = {0, n - 1}
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        keep.add(a)
        for y in series:
            seg = y[a:b]
            if not np.isfinite(seg).any():
                continue
            keep.add(a + int(np.nanargmin(seg)))
            keep.add(a + int(np.nanargmax(seg)))
    return np.array(sorted(keep))


def evidence_runs(runner, n_max: int = 3000) -> dict:
    """run key -> {scenario title, status, trace (decimated channels), events, timeline} for every run of a runner."""
    out = {}
    titles = {}
    for sid, s in (runner.package.get("scenarios") or {}).items():
        titles[sid] = s.get("title", sid)
    for key, rec in runner.runs.items():
        res = rec.result
        tr = res.trace
        t = np.asarray(tr["t"], float)
        chans = [c for c in EVIDENCE_CHANNELS if c in tr]
        series = [np.asarray(tr[c], float) for c in chans if c not in ("t", "bridge", "sys_state")]
        idx = decimate_indices(t, series, n_max)
        trace = {c: [None if not math.isfinite(v) else float(v) for v in np.asarray(tr[c], float)[idx]] for c in chans}
        tl = fault_timeline(res)
        tl = {k: v for k, v in tl.items() if k != "safe_state"}
        ev = [{k: e.get(k) for k in ("t", "kind", "source", "text")} for e in res.events if e["kind"] in EVENT_KINDS]
        out[key] = {"status": res.status, "stop_reason": getattr(res, "stop_reason", None), "trace": trace,
                    "events": ev[:400], "timeline": tl, "system": (res.summary or {}).get("system"),
                    "scenario": rec.scenario}
    return out


def _txt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4g}" if math.isfinite(v) else str(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(_txt(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}={_txt(x)}" for k, x in v.items())
    return str(v)


def matrix_rows(summary: dict) -> list:
    """The requirement-to-evidence matrix: one row per check of every item (the item's own verdict repeated)."""
    rows = []
    for r in summary.get("rows", []):
        for c in r.get("checks") or []:
            rows.append({
                "item": r["id"], "group": r.get("group", ""), "kind": r.get("kind", ""), "level": r.get("level", ""),
                "title": r.get("title", ""),
                "provenance": r.get("provenance", ""), "source": "yes" if r.get("customer") else "no",
                "asil_literal": r.get("asil_literal") or "", "agreement": r.get("agreement") or "",
                "traces_to": ", ".join(r.get("traces_to") or []),
                "traces_to_inferred": ", ".join(r.get("traces_to_inferred") or []),
                "proposed": "yes" if r.get("proposed") else "no",
                "item_verdict": r["verdict"], "illustrative": "yes" if r.get("illustrative") else "no",
                "check": c.get("check", ""), "label": c.get("label", ""), "scenario": c.get("scenario") or "",
                "variant": c.get("variant") or "", "check_verdict": c.get("verdict", ""),
                "expected": c.get("expected", ""), "measured": _txt(c.get("measured")),
                "reasons": " | ".join(x for x in c.get("reasons") or [] if x),
                "open_parameters": ", ".join(c.get("open") or []),
                "illustrative_parameters": ", ".join(c.get("illustrative") or [])})
    return rows


MATRIX_COLUMNS = ("item", "group", "kind", "level", "title", "provenance", "source", "asil_literal", "agreement",
                  "traces_to", "traces_to_inferred", "proposed", "item_verdict", "illustrative", "check", "label",
                  "scenario", "variant", "check_verdict", "expected", "measured", "reasons", "open_parameters",
                  "illustrative_parameters")


def matrix_csv(summary: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=MATRIX_COLUMNS)
    w.writeheader()
    for row in matrix_rows(summary):
        w.writerow(row)
    return buf.getvalue()


def _badge(v: str) -> str:
    return (f'<span class="b" style="background:{COLORS.get(v, "#57606a")}">{html.escape(v)}</span>')


def _hierarchy_html(summary: dict) -> list:
    """The hierarchy section: counts per level, the tree (own verdict, subtree verdict; an inferred link marked),
    the gaps and the proposals."""
    from .refhier import LEVEL_NAMES, LEVELS, REQ_LEVELS
    e = html.escape
    h = summary.get("hierarchy") or {}
    if not h:
        return []
    rows = {r["id"]: r for r in summary.get("rows", [])}
    lv, kids, roll = h.get("levels") or {}, h.get("children") or {}, h.get("rollup") or {}
    parts = ["<h2>Hierarchy: SG → TLSR → FSR → TSR → SM → verification</h2>",
             "<p class='m'>Levels and traces as the package states them; <i>inferred</i> links are marked and are "
             "proposals for review. The first badge is the item's own verdict, the second the worst verdict of its "
             "subtree (FAIL &gt; CONFLICT &gt; UNKNOWN &gt; PASS; MANUAL only when nothing was checked). A proposal is "
             "a DERIVED addition, never a source requirement.</p>",
             "<table><tr><th>level</th><th>items</th><th>source</th><th>proposed</th><th>machine-checked</th>"
             "<th>traced up</th><th>inferred links</th></tr>"]
    for k in LEVELS:
        st = (h.get("stats") or {}).get(k)
        if st:
            parts.append(f"<tr><td>{e(LEVEL_NAMES[k][1])}</td><td>{st['items']}</td><td>{st['customer']}</td>"
                         f"<td>{st['proposed']}</td><td>{st['machine_checked']}</td><td>{st['traced']}</td>"
                         f"<td>{st['inferred_links']}</td></tr>")
    parts.append("</table>")

    def node(i, basis, seen, depth):
        r = rows.get(i) or {}
        own = r.get("verdict")
        sub = (roll.get(i) or {}).get("verdict")
        tag = " <i>(inferred link)</i>" if basis == "inferred" else ""
        prop = " <i>[proposal]</i>" if r.get("proposed") else ""
        out = [f"<li><span class='m'>{e(lv.get(i, ''))}</span> {_badge(own) if own else ''}"
               f"{_badge(sub) if sub and sub != own else ''}<b>{e(i)}</b>{prop}{tag} "
               f"{e((r.get('title') or '')[:160])}"]
        ch = [(c, b) for c, b in kids.get(i) or [] if c not in seen and lv.get(c) in REQ_LEVELS]
        if ch and depth < 8:
            out.append("<ul>")
            for c, b in ch:
                out += node(c, b, seen | {i}, depth + 1)
            out.append("</ul>")
        out.append("</li>")
        return out
    parts.append("<ul class='tree'>")
    for i in h.get("roots") or []:
        parts += node(i, "source", set(), 0)
    parts.append("</ul>")
    gaps = h.get("gaps") or []
    if gaps:
        parts.append("<h3>Gaps the structure shows</h3><table><tr><th>item</th><th>level</th><th>gap</th>"
                     "<th>addressed by</th></tr>")
        for g in gaps:
            parts.append(f"<tr><td>{e(g['id'])}{' (source)' if g.get('customer') else ''}</td><td>{e(g['level'])}"
                         f"</td><td>{e(g['text'])}</td><td>{e(', '.join(g.get('proposals') or [])) or '-'}</td></tr>")
        parts.append("</table>")
    props = [r for r in summary.get("rows", []) if r.get("proposed")]
    if props:
        parts.append("<h3>Proposals (DERIVED additions, not source requirements)</h3><ul>")
        for r in props:
            parts.append(f"<li>{_badge(r['verdict'])}<b>{e(r['id'])}</b> ({e(r.get('level', ''))}, traces to "
                         f"{e(', '.join((r.get('traces_to') or []) + (r.get('traces_to_inferred') or [])) or '-')}) "
                         f"{e(r.get('title', ''))}" + (f"<div class='m'>{e(r['rationale'])}</div>"
                                                        if r.get("rationale") else "") + "</li>")
        parts.append("</ul>")
    return parts


def reference_html(summary: dict, project: dict | None = None, code: dict | None = None) -> str:
    """The verification of a reference package as one self-contained HTML page."""
    e = html.escape
    pk = summary.get("package") or {}
    prof = summary.get("profile", "customer")
    prof_name = {"customer": "as given (OPEN stays UNKNOWN)", "illustrative": "illustrative (results marked ILL)"}
    rows = summary.get("rows", [])
    parts = []
    parts.append(f"<h1>{e(pk.get('title') or 'Reference verification')}</h1>")
    parts.append(f"<p class='m'>profile <b>{e(prof_name.get(prof, prof))}</b> · package digest {e(pk.get('digest', '-'))} · product "
                 f"{e((project or {}).get('label', '-'))} ({e((project or {}).get('digest', '-'))})"
                 + (f" · code {e(_txt(code))}" if code else "") + "</p>")
    if prof == "illustrative":
        parts.append("<p class='w'>Illustrative profile: OPEN values take example values - every result that used "
                     "one is marked ILL and is never a verdict on the given values.</p>")
    parts.append("<p class='m'>Every simulation verdict is about the product model named above. OPEN values are never "
                 "guessed: a check that needs one is UNKNOWN and names it.</p>")
    # counts
    parts.append("<h2>Summary</h2><table><tr><th>set</th>" + "".join(f"<th>{v}</th>" for v in VERDICT_ORDER)
                 + "<th>total</th></tr>")
    for key, cnt in (summary.get("counts") or {}).items():
        parts.append(f"<tr><td>{e(SET_NAMES.get(key, (key, key))[1])}</td>"
                     + "".join(f"<td>{cnt.get(v, 0)}</td>" for v in VERDICT_ORDER)
                     + f"<td>{sum(cnt.values())}</td></tr>")
    parts.append("</table>")
    parts += _hierarchy_html(summary)
    # groups
    groups: dict = {}
    for r in rows:
        g = groups.setdefault(r.get("group", ""), {})
        g[r["verdict"]] = g.get(r["verdict"], 0) + 1
    parts.append("<h2>By group</h2><table><tr><th>group</th>" + "".join(f"<th>{v}</th>" for v in VERDICT_ORDER)
                 + "</tr>")
    for g, cnt in groups.items():
        parts.append(f"<tr><td>{e(g)}</td>" + "".join(f"<td>{cnt.get(v, 0) or ''}</td>" for v in VERDICT_ORDER)
                     + "</tr>")
    parts.append("</table>")
    # open report
    orep = summary.get("open_report") or {}
    if orep:
        parts.append("<h2>Unknown report: values the source does not give</h2><table><tr><th>parameter</th>"
                     "<th>provenance</th><th>unit</th><th>note</th><th>items waiting</th></tr>")
        for pid, o in sorted(orep.items(), key=lambda kv: -len(kv[1]["items"])):
            its = o["items"]
            parts.append(f"<tr><td>{e(pid)}</td><td>{e(o.get('provenance', ''))}</td><td>{e(o.get('unit', ''))}</td>"
                         f"<td>{e(o.get('note', ''))}</td><td>{len(its)}: {e(', '.join(its[:20]))}"
                         f"{' ...' if len(its) > 20 else ''}</td></tr>")
        parts.append("</table>")
    conf = summary.get("conflicts") or []
    if conf:
        parts.append("<h2>Conflict report</h2><ul>")
        for c in conf:
            for ch in c["checks"]:
                parts.append(f"<li><b>{e(c['id'])}</b> {e(ch.get('label') or ch.get('check'))}: "
                             f"{e('; '.join(x for x in ch.get('reasons') or [] if x))}</li>")
        parts.append("</ul>")
    if summary.get("manual"):
        parts.append(f"<h2>Manual evidence to record</h2><p>{e(', '.join(summary['manual']))}</p>")
    # the matrix
    parts.append("<h2>Requirement-to-evidence matrix</h2>")
    for r in rows:
        tag = " ILL" if r.get("illustrative") else ""
        meta = " · ".join(x for x in (r.get("level"), r.get("provenance"),
                                      "source requirement" if r.get("customer") else "proposal (DERIVED)"
                                      if r.get("proposed") else "not a source requirement",
                                      r.get("asil_literal") and f"ASIL {r['asil_literal']}", r.get("agreement"),
                                      r.get("traces_to") and "traces to " + ", ".join(r["traces_to"]),
                                      r.get("traces_to_inferred") and "inferred: " + ", ".join(r["traces_to_inferred"]))
                          if x)
        parts.append(f"<div class='it'><div class='h'>{_badge(r['verdict'])}<b>{e(r['id'])}</b>{e(tag)} "
                     f"{e(r.get('title', ''))}</div><div class='m'>{e(r.get('group', ''))} · {e(meta)}"
                     + (f" · OPEN: {e(', '.join(r['open']))}" if r.get("open") else "") + "</div><table>")
        for c in r.get("checks") or []:
            reasons = "<br>".join(e(x) for x in c.get("reasons") or [] if x)
            where = " / ".join(x for x in (c.get("scenario"), c.get("variant")) if x)
            parts.append(f"<tr><td>{_badge(c.get('verdict', ''))}</td><td>{e(c.get('label') or c.get('check', ''))}"
                         f"<div class='m'>{e(c.get('check', ''))}{' · ' + e(where) if where else ''}</div></td>"
                         f"<td>{reasons}</td></tr>")
        parts.append("</table></div>")
    css = ("body{font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1f2328}"
           "h1{font-size:20px}h2{font-size:16px;margin-top:22px}table{border-collapse:collapse;margin:6px 0}"
           "td,th{border:1px solid #d0d7de;padding:3px 6px;vertical-align:top;text-align:left}"
           ".b{color:#fff;border-radius:3px;padding:1px 5px;margin-right:6px;font-size:11px}"
           ".m{color:#57606a;font-size:12px}.w{background:#fff8c5;padding:6px}.it{margin:10px 0;border-top:1px "
           "solid #d0d7de;padding-top:6px}.h{font-size:14px}ul.tree,ul.tree ul{list-style:none;padding-left:18px}"
           "ul.tree li{margin:2px 0}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(pk.get('title') or 'Reference verification')}"
            f"</title><style>{css}</style></head><body>{''.join(parts)}</body></html>")
