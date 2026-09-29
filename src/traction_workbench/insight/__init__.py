"""Engineering reading of computed results: what the numbers mean, item by item.

The engines decide (PASS / FAIL / UNKNOWN, claims, margins); this package reads their results back to a person:
the conclusion in one line, the judged items one by one, the mechanism that limits, where the power goes, which
limit moves the answer most and what would change it.  Rules that keep the reading as trustworthy as the result:

* every number comes from the result (or is an identity of reported numbers, shown with its terms);
* nothing the engines did not compute is estimated; an item that was not evaluated is said to be not evaluated;
* no new pass/fail threshold is introduced: a margin is reported with its size and share, the reader judges it;
* the identifiers stay in the records; the reading uses display names (``plots.labels``).

``Insight`` renders to HTML (desktop panel, with theme colours) and Markdown (reports).
"""

from __future__ import annotations

import html as _html
import math
from dataclasses import dataclass, field

from ..i18n import tr

LEVELS = ("ok", "warn", "bad", "open", "info")
SYMBOL = {"ok": "✓", "warn": "▲", "bad": "✗", "open": "?", "info": "•"}


@dataclass
class Item:
    text: str                      # the statement (may hold <b>…</b>)
    level: str = "info"            # ok | warn | bad | open | info
    detail: str = ""               # the numbers or source behind it, shown smaller


@dataclass
class Section:
    title: str
    items: list[Item] = field(default_factory=list)
    note: str = ""

    def add(self, text: str, level: str = "info", detail: str = "") -> Item:
        it = Item(text, level if level in LEVELS else "info", detail)
        self.items.append(it)
        return it


@dataclass
class Insight:
    headline: str
    verdict: str | None = None                     # PASS | FAIL | UNKNOWN | None
    metrics: list[tuple[str, str, str]] = field(default_factory=list)   # (label, value, level)
    sections: list[Section] = field(default_factory=list)

    def section(self, title: str, note: str = "") -> Section:
        s = Section(title, note=note)
        self.sections.append(s)
        return s

    def nonempty(self) -> Insight:
        self.sections = [s for s in self.sections if s.items]
        return self

    # ------------------------------------------------------------------ rendering
    def html(self, colors: dict | None = None) -> str:
        c = {"fg": "#1f2328", "muted": "#57606a", "border": "#d0d7de", "panel": "#f6f8fa",
             "ok": "#1a7f37", "warn": "#9a6700", "bad": "#cf222e", "open": "#9a6700", "info": "#57606a"}
        c.update(colors or {})
        vcol = {"PASS": c["ok"], "FAIL": c["bad"], "UNKNOWN": c["warn"]}.get(self.verdict or "", c["fg"])
        out = [f"<div style='color:{c['fg']}'>",
               f"<p style='font-size:12pt; font-weight:600; color:{vcol}; margin:2px 0 8px 0'>{self.headline}</p>"]
        if self.metrics:
            out.append("<table cellspacing='6' cellpadding='6' style='margin-bottom:6px'><tr>")
            for label, value, level in self.metrics:
                col = c.get(level, c["fg"]) if level in ("ok", "warn", "bad", "open") else c["fg"]
                out.append(f"<td style='background:{c['panel']}; border:1px solid {c['border']}'>"
                           f"<span style='color:{c['muted']}; font-size:8.5pt'>{label}</span><br>"
                           f"<span style='font-size:11pt; font-weight:600; color:{col}'>{value}</span></td>")
            out.append("</tr></table>")
        for s in self.sections:
            out.append(f"<p style='font-weight:600; font-size:10.5pt; margin:10px 0 2px 0'>{s.title}</p>")
            if s.note:
                out.append(f"<p style='color:{c['muted']}; margin:0 0 2px 0'>{s.note}</p>")
            out.append("<table cellspacing='0' cellpadding='2'>")
            for it in s.items:
                sym = SYMBOL.get(it.level, "•")
                col = c.get(it.level, c["muted"])
                det = (f"<br><span style='color:{c['muted']}; font-size:8.5pt'>{it.detail}</span>" if it.detail else "")
                out.append(f"<tr><td valign='top' style='color:{col}; font-weight:700; padding-right:6px'>{sym}</td>"
                           f"<td>{it.text}{det}</td></tr>")
            out.append("</table>")
        out.append("</div>")
        return "".join(out)

    def markdown(self) -> str:
        def plain(t: str) -> str:
            return t.replace("<b>", "**").replace("</b>", "**").replace("<br>", " ")
        lines = [f"**{plain(self.headline)}**", ""]
        if self.metrics:
            lines += ["| " + " | ".join(plain(m[0]) for m in self.metrics) + " |",
                      "|" + "---|" * len(self.metrics),
                      "| " + " | ".join(plain(m[1]) for m in self.metrics) + " |", ""]
        for s in self.sections:
            lines.append(f"### {plain(s.title)}")
            if s.note:
                lines.append(f"_{plain(s.note)}_")
            lines.append("")
            for it in s.items:
                lines.append(f"- {SYMBOL.get(it.level, '•')} {plain(it.text)}"
                             + (f" — {plain(it.detail)}" if it.detail else ""))
            lines.append("")
        return "\n".join(lines)

    def plain_lines(self) -> list[str]:
        """Headline and every item as plain text (tests, logs)."""
        import re
        strip = lambda t: _html.unescape(re.sub(r"<[^>]+>", "", t))       # noqa: E731
        return [strip(self.headline)] + [strip(f"{s.title}: {it.text} {it.detail}".strip())
                                         for s in self.sections for it in s.items]


# ---------------------------------------------------------------------------- number formatting
def num(v, sig: int = 4) -> str:
    """A number for a sentence: ``sig`` significant digits, thousands grouped, no exponent in the usual range."""
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "∞" if x > 0 else "−∞"
    if x == 0:
        return "0"
    if abs(x) >= 1000 and float(x).is_integer():
        return f"{x:,.0f}".replace("-", "−")
    if abs(x) >= 10 ** sig:
        s = f"{x:,.0f}"
    elif abs(x) >= 1000:                               # grouped like the integers: 1737.18 -> 1,737 (sig 4)
        digits = sig - 1 - int(math.floor(math.log10(abs(x))))
        s = f"{round(x, digits):,.0f}" if digits <= 0 else f"{x:,.{digits}f}"
    elif abs(x) < 1e-3:
        s = f"{x:.{max(1, sig - 1)}e}"
    else:
        s = f"{x:.{sig}g}"
        if "e" in s:
            s = f"{x:.{max(0, sig - 1 - int(math.floor(math.log10(abs(x)))))}f}"
        if abs(float(s)) >= 1000:                      # rounded up into the thousands (999.99 -> 1,000)
            s = f"{float(s):,.0f}"
    return s.replace("-", "−")


def q(v, unit: str, sig: int = 4) -> str:
    return f"{num(v, sig)} {unit}".strip() if v is not None else "—"


def kw(watts, sig: int = 4) -> str:
    return "—" if watts is None else f"{num(watts / 1e3, sig)} kW"


def pct(part, whole, digits: int = 1) -> str:
    try:
        if whole in (None, 0) or part is None:
            return "—"
        return f"{100.0 * float(part) / float(whole):.{digits}f} %".replace("-", "−")
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"


def esc(text) -> str:
    return _html.escape(str(text), quote=False)


def status_level(status: str | None) -> str:
    return {"FEASIBLE": "ok", "PASS": "ok", "INFEASIBLE": "bad", "FAIL": "bad", "UNKNOWN": "open",
            "ACCEPTED": "ok"}.get(str(status or ""), "info")


def verdict_word(v: str | None) -> str:
    return {"PASS": tr("만족", "met"), "FAIL": tr("불만족 (증명됨)", "not met (proven)"),
            "UNKNOWN": tr("미확정", "undecided")}.get(v or "", v or "—")
