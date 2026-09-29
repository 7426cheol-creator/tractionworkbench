"""Desktop entry point.

    traction-workbench [--lang ko|en] [--theme light|dark] [--open CASE.json]
    traction-workbench --self-test OUT_DIR      headless check of every page (used on the packaged exe)
"""

from __future__ import annotations

import argparse
import os
import sys


def _parse(argv):
    ap = argparse.ArgumentParser(prog="TractionWorkbench", add_help=True)
    ap.add_argument("--lang", choices=("ko", "en"))
    ap.add_argument("--theme", choices=("light", "dark"))
    ap.add_argument("--open", dest="open_case", help="case JSON to evaluate, or a workspace (*.twb-workspace.json) to "
                                                   "open, at start")
    ap.add_argument("--self-test", dest="self_test", metavar="OUT_DIR", help="headless self-test, writes a report")
    args, _unknown = ap.parse_known_args(argv)
    return args


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parse(argv)
    if args.self_test and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import matplotlib
    matplotlib.use("QtAgg")
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtWidgets import QApplication

    from ..i18n import set_language
    from . import theme

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication([sys.argv[0]] + argv)
    app.setApplicationName("Traction Workbench")
    app.setOrganizationName("TractionWorkbench")
    settings = QSettings("TractionWorkbench", "TractionWorkbench")
    set_language(args.lang or settings.value("language", "ko"))
    # the self-test starts light (deterministic screenshots) and never reads or writes the user's theme
    theme.apply(app, args.theme or ("light" if args.self_test else settings.value("theme", "light")))
    icon = _icon_path()
    if icon:
        app.setWindowIcon(QIcon(str(icon)))
    if args.self_test:
        from .selftest import run_self_test
        return run_self_test(app, args.self_test)
    from .main_window import MainWindow
    win = MainWindow()
    win.show()
    from PySide6.QtCore import QTimer
    from .workspace import SUFFIX
    if args.open_case and str(args.open_case).endswith(SUFFIX):
        QTimer.singleShot(0, lambda: win.open_workspace(args.open_case))
    elif args.open_case:
        win.open_case(args.open_case)
    else:
        QTimer.singleShot(0, win.offer_recovery)       # the last session's unsaved work, if any
    return app.exec()


def _icon_path():
    from pathlib import Path
    here = Path(__file__).resolve().parent / "resources" / "icon.png"
    if here.is_file():
        return here
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        p = Path(frozen) / "traction_workbench" / "desktop" / "resources" / "icon.png"
        if p.is_file():
            return p
    return None


if __name__ == "__main__":
    sys.exit(main())
