"""Qt palette and style sheet (light/dark), kept in step with the matplotlib theme."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ..plots import style as mpl_style

COLORS = {
    "light": {"bg": "#ffffff", "window": "#f6f8fa", "panel": "#ffffff", "fg": "#1f2328", "muted": "#57606a",
              "border": "#d0d7de", "accent": "#0969da", "accent_bg": "#ddf4ff", "nav": "#f6f8fa",
              "header": "#ffffff", "input": "#ffffff"},
    "dark": {"bg": "#1e1f22", "window": "#18191c", "panel": "#232428", "fg": "#e6edf3", "muted": "#9da7b3",
             "border": "#3d444d", "accent": "#4493f8", "accent_bg": "#1f3a5f", "nav": "#1b1c1f",
             "header": "#232428", "input": "#2b2d31"},
}

VERDICT_STYLE = {
    "light": {"PASS": ("#dafbe1", "#1a7f37"), "FAIL": ("#ffebe9", "#cf222e"), "UNKNOWN": ("#fff8c5", "#9a6700"),
              "NONE": ("#f6f8fa", "#57606a")},
    "dark": {"PASS": ("#12261e", "#3fb950"), "FAIL": ("#2d1517", "#f85149"), "UNKNOWN": ("#2b2111", "#d29922"),
             "NONE": ("#232428", "#9da7b3")},
}

STATUS_TEXT = {"FEASIBLE": "#1a7f37", "INFEASIBLE": "#cf222e", "UNKNOWN": "#9a6700", "ACCEPTED": "#1a7f37",
               "DIAGNOSTIC ONLY": "#9a6700"}
STATUS_TEXT_DARK = {"FEASIBLE": "#3fb950", "INFEASIBLE": "#f85149", "UNKNOWN": "#d29922", "ACCEPTED": "#3fb950",
                    "DIAGNOSTIC ONLY": "#d29922"}

_STATE = {"theme": "light"}


def current() -> str:
    return _STATE["theme"]


def colors() -> dict:
    return COLORS[_STATE["theme"]]


def verdict_colors(v: str) -> tuple[str, str]:
    return VERDICT_STYLE[_STATE["theme"]].get(v, VERDICT_STYLE[_STATE["theme"]]["NONE"])


def status_color(status: str) -> str:
    return (STATUS_TEXT if _STATE["theme"] == "light" else STATUS_TEXT_DARK).get(status, colors()["muted"])


def app_font() -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI", "Malgun Gothic", "Noto Sans CJK KR", "NanumGothic", "Apple SD Gothic Neo", "sans-serif"])
    f.setPointSizeF(9.5)
    return f


def apply(app: QApplication, name: str = "light") -> None:
    _STATE["theme"] = name if name in COLORS else "light"
    mpl_style.apply(_STATE["theme"])
    c = colors()
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["window"]))
    pal.setColor(QPalette.WindowText, QColor(c["fg"]))
    pal.setColor(QPalette.Base, QColor(c["input"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["window"]))
    pal.setColor(QPalette.Text, QColor(c["fg"]))
    pal.setColor(QPalette.Button, QColor(c["panel"]))
    pal.setColor(QPalette.ButtonText, QColor(c["fg"]))
    pal.setColor(QPalette.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(c["panel"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["fg"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    pal.setColor(QPalette.Link, QColor(c["accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(c["muted"]))
    app.setPalette(pal)
    app.setFont(app_font())
    app.setStyleSheet(stylesheet())


def stylesheet() -> str:
    c = colors()
    return f"""
    QMainWindow {{ background: {c['window']}; }}
    QWidget#Header {{ background: {c['header']}; border-bottom: 1px solid {c['border']}; }}
    QLabel#AppTitle {{ font-size: 13pt; font-weight: 600; color: {c['fg']}; }}
    QLabel#AppSubtitle {{ color: {c['muted']}; }}
    QListWidget#Nav {{ background: {c['nav']}; border: none; border-right: 1px solid {c['border']};
                       font-size: 10pt; outline: 0; }}
    QListWidget#Nav::item {{ padding: 9px 12px; border-left: 3px solid transparent; color: {c['fg']}; }}
    QListWidget#Nav::item:selected {{ background: {c['accent_bg']}; border-left: 3px solid {c['accent']};
                                      color: {c['fg']}; font-weight: 600; }}
    QListWidget#Nav::item:hover:!selected {{ background: {c['panel']}; }}
    QLabel[badge="model"] {{ background: {c['accent_bg']}; color: {c['fg']}; border: 1px solid {c['border']};
                             border-radius: 9px; padding: 2px 9px; }}
    QLabel[badge="warn"] {{ background: #fff8c5; color: #7d4e00; border: 1px solid #d4a72c;
                            border-radius: 9px; padding: 2px 9px; }}
    QLabel[badge="info"] {{ background: {c['panel']}; color: {c['muted']}; border: 1px solid {c['border']};
                            border-radius: 9px; padding: 2px 9px; }}
    QGroupBox {{ font-weight: 600; border: 1px solid {c['border']}; border-radius: 6px; margin-top: 12px;
                 padding: 10px 8px 8px 8px; background: {c['panel']}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['fg']}; }}
    QPushButton {{ padding: 5px 12px; border: 1px solid {c['border']}; border-radius: 5px; background: {c['panel']}; }}
    QPushButton:hover {{ border-color: {c['accent']}; }}
    QPushButton#Primary {{ background: {c['accent']}; color: #ffffff; border: 1px solid {c['accent']}; font-weight: 600;
                           padding: 7px 14px; }}
    QPushButton#Primary:disabled {{ background: {c['border']}; border-color: {c['border']}; color: {c['muted']}; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 4px; background: {c['bg']}; top: -1px; }}
    QTabBar::tab {{ padding: 6px 12px; border: 1px solid transparent; border-bottom: none; color: {c['muted']}; }}
    QTabBar::tab:selected {{ color: {c['fg']}; border-color: {c['border']}; background: {c['bg']};
                             border-top-left-radius: 4px; border-top-right-radius: 4px; font-weight: 600; }}
    QFrame#Card {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; }}
    QLabel#Hint {{ color: {c['muted']}; }}
    QLabel#Readout {{ color: {c['muted']}; font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 8.5pt; }}
    QTableWidget, QTreeWidget, QTextBrowser, QPlainTextEdit {{ background: {c['bg']}; border: 1px solid {c['border']};
                                                               border-radius: 4px; }}
    QHeaderView::section {{ background: {c['window']}; padding: 4px; border: none;
                            border-bottom: 1px solid {c['border']}; font-weight: 600; }}
    QStatusBar {{ background: {c['header']}; border-top: 1px solid {c['border']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QToolTip {{ padding: 4px; }}
    """
