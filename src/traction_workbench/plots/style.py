"""Shared plot style: palette, fonts (Korean-capable fallback), light/dark themes."""

from __future__ import annotations

import logging
from contextlib import contextmanager

import matplotlib as mpl

# equal-aspect id-iq views use adjustable="datalim"; matplotlib logs a notice every time it widens a fixed limit
logging.getLogger("matplotlib.axes._base").setLevel(logging.ERROR)
# a Korean fallback font without a bold face makes the font manager log a notice for every bold label
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

PHASE = ("#1f77b4", "#ff7f0e", "#2ca02c")          # a, b, c
GROUP = {
    "VOLTAGE": "#7b3294",
    "CURRENT": "#d7191c",
    "DOMAIN": "#6b6b6b",
    "DISCHARGE_SOURCE": "#e08214",
    "CHARGE_SOURCE": "#2c7bb6",
}
CONSTRAINT = {
    "VOLTAGE": "#7b3294", "CURRENT": "#d7191c",
    "DC_DISCHARGE_POWER": "#e08214", "DC_DISCHARGE_CURRENT": "#b35806",
    "DC_CHARGE_POWER": "#2c7bb6", "DC_CHARGE_CURRENT": "#08519c",
    "ID_MIN": "#6b6b6b", "ID_MAX": "#6b6b6b", "IQ_MIN": "#6b6b6b", "IQ_MAX": "#6b6b6b",
    "SPEED_MIN": "#6b6b6b", "SPEED_MAX": "#6b6b6b",
}
STATE = {"SATISFIED": "#1a9850", "ACTIVE": "#fdae61", "VIOLATED": "#d73027", "NOT_EVALUATED": "#9e9e9e"}
STATUS = {0: "#1a9850", 1: "#f46d43", 2: "#b2182b", 3: "#9e9e9e"}     # OK, DC, NONE, UNKNOWN
VERDICT = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f"}
CLAIM = {"FEASIBLE": "#1a7f37", "INFEASIBLE": "#cf222e", "UNKNOWN": "#b7791f"}
ACCENT = "#0969da"
REQUEST = "#e36209"
MTPA = "#0b7285"
MTPV = "#8c564b"

KOREAN_FONTS = ["Malgun Gothic", "Noto Sans CJK KR", "NanumGothic", "Apple SD Gothic Neo", "AppleGothic",
                "Noto Sans KR", "Gulim"]

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#1f2328", "muted": "#57606a", "grid": "#d0d7de", "panel": "#f6f8fa",
              "feasible": "#b7e4c7", "feasible_all": "#52b788", "cmap": "viridis"},
    "dark": {"bg": "#1e1f22", "fg": "#e6edf3", "muted": "#9da7b3", "grid": "#3d444d", "panel": "#2b2d31",
             "feasible": "#1f4d36", "feasible_all": "#2d6a4f", "cmap": "viridis"},
}

_CURRENT = {"theme": "light"}


def theme() -> dict:
    return THEMES[_CURRENT["theme"]]


def theme_name() -> str:
    return _CURRENT["theme"]


@contextmanager
def using(theme_name_: str):
    """Build figures in ``theme_name_`` and give the previous theme and rcParams back afterwards (a light report
    must not restyle the application's plots)."""
    prev = _CURRENT["theme"]
    with mpl.rc_context():
        apply(theme_name_)
        try:
            yield
        finally:
            _CURRENT["theme"] = prev


def apply(theme_name_: str = "light", base_size: float = 9.0) -> None:
    """Set matplotlib rcParams for the given theme (call before building figures)."""
    _CURRENT["theme"] = theme_name_ if theme_name_ in THEMES else "light"
    t = theme()
    from matplotlib import font_manager
    available = {f.name for f in font_manager.fontManager.ttflist}
    kfonts = [f for f in KOREAN_FONTS if f in available]
    mpl.rcParams.update({
        # explicit family list = per-glyph fallback (Latin from DejaVu Sans, Hangul from the Korean font)
        "font.family": ["DejaVu Sans"] + kfonts,
        "axes.unicode_minus": False,
        "font.size": base_size,
        "axes.titlesize": base_size + 1,
        "axes.labelsize": base_size,
        "legend.fontsize": base_size - 1,
        "xtick.labelsize": base_size - 1,
        "ytick.labelsize": base_size - 1,
        "figure.facecolor": t["bg"],
        "axes.facecolor": t["bg"],
        "savefig.facecolor": t["bg"],
        "text.color": t["fg"],
        "axes.labelcolor": t["fg"],
        "axes.edgecolor": t["muted"],
        "xtick.color": t["muted"],
        "ytick.color": t["muted"],
        "axes.grid": True,
        "grid.color": t["grid"],
        "grid.linewidth": 0.6,
        "grid.alpha": 0.8,
        "legend.frameon": True,
        "legend.facecolor": t["bg"],
        "legend.edgecolor": t["grid"],
        "legend.framealpha": 0.9,
        "hatch.color": t["muted"],
        "hatch.linewidth": 0.6,
        "lines.linewidth": 1.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 100,
        "savefig.dpi": 150,
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
    })
