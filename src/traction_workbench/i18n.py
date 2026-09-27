"""Minimal two-language support (Korean default, English) for the desktop app and reports."""

from __future__ import annotations

_STATE = {"lang": "ko"}


def set_language(lang: str) -> None:
    _STATE["lang"] = "en" if str(lang).lower().startswith("en") else "ko"


def language() -> str:
    return _STATE["lang"]


def tr(ko: str, en: str) -> str:
    return ko if _STATE["lang"] == "ko" else en
