"""Entry point of the packaged application.

One PyInstaller bundle, two executables sharing the same files:
``TractionWorkbench.exe`` (windowed desktop app) and ``twb.exe`` (console CLI, batch use and self-test).
"""

import multiprocessing
import sys
from pathlib import Path


def main() -> int:
    exe = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).stem.lower()
    if exe.startswith("twb"):
        from traction_workbench.cli import main as cli_main
        return cli_main()
    from traction_workbench.desktop.app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
