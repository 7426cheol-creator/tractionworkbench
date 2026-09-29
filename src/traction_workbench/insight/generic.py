"""Pieces every reading shares: a result's claims, its notes and assumptions, a verdict from a claim status."""

from __future__ import annotations

from ..i18n import tr
from ..plots.labels import claim_label, reason_label, state_label
from . import Insight, Section, esc, status_level
from .texts import engine_parts, engine_text

VERDICT_OF = {"FEASIBLE": "PASS", "INFEASIBLE": "FAIL", "UNKNOWN": "UNKNOWN", "ACCEPTED": "PASS"}


def verdict_of(claim: dict | None) -> str | None:
    return VERDICT_OF.get(str((claim or {}).get("status", ""))) if claim else None


def claim_item(s: Section, claim: dict, name: str | None = None) -> None:
    """One claim: its display name, state, reasons in words; its qualifiers and engine detail underneath."""
    st = claim.get("status", "")
    why = ", ".join(reason_label(r) for r in claim.get("reasons") or [])
    text = f"<b>{name or claim_label(claim.get('name', ''))}</b>: {state_label(st)}" + (f" — {why}" if why else "")
    det = [esc(engine_text(q)) for q in claim.get("qualifiers") or []]
    if claim.get("detail"):
        det.append(esc(engine_parts(claim["detail"])))
    s.add(text, status_level(st), " · ".join(det))


def claims_section(ins: Insight, claims, title: str | None = None) -> None:
    items = list(claims.values()) if isinstance(claims, dict) else list(claims or [])
    items = [c for c in items if isinstance(c, dict) and "status" in c]
    if not items:
        return
    s = ins.section(title or tr("판정 항목", "judged items"))
    for c in items:
        claim_item(s, c)


def notes_section(ins: Insight, *lists, title: str | None = None) -> None:
    """Assumptions, notes and what is not modelled — what the result does not cover."""
    s = ins.section(title or tr("이 결과가 말하지 않는 것", "what this result does not cover"))
    seen = set()
    for lst in lists:
        for x in lst or []:
            if isinstance(x, str) and x and x not in seen:
                seen.add(x)
                s.add(esc(engine_text(x)), "info")


def not_modelled_section(ins: Insight, items, title: str | None = None) -> None:
    if not items:
        return
    s = ins.section(title or tr("모델에 없는 것", "not modelled"))
    s.add(", ".join(esc(engine_text(x)) for x in items), "open")
