# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: one-folder bundle with TractionWorkbench(.exe) (GUI) and twb(.exe) (console CLI).
#   pyinstaller --noconfirm --clean packaging/traction_workbench.spec
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
WIN = sys.platform.startswith("win")

datas = [
    (str(ROOT / "reference" / "traction_workbench_spec_v1"), "reference/traction_workbench_spec_v1"),
    (str(ROOT / "examples"), "examples"),
    (str(ROOT / "src" / "traction_workbench" / "desktop" / "resources"), "traction_workbench/desktop/resources"),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "docs"), "docs"),
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=[
        "traction_workbench.desktop.app", "traction_workbench.cli",
        "matplotlib.backends.backend_qtagg", "matplotlib.backends.backend_agg",
        "matplotlib.backends.backend_pdf", "matplotlib.backends.backend_svg",
    ],
    excludes=[
        "tkinter", "_tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "jupyter", "notebook", "pytest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml",
        "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = str(ROOT / "packaging" / "icon.ico") if WIN else None
version = str(ROOT / "packaging" / "version_info.txt") if WIN and (ROOT / "packaging" / "version_info.txt").exists() else None

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TractionWorkbench", console=False, icon=icon,
          version=version, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="twb", console=True, icon=icon, version=version,
          upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="TractionWorkbench", upx=False)
