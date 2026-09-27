#!/usr/bin/env python3
"""Build the distributable application folder and zip, then self-test the frozen build.

    pip install -e ".[gui,build]"
    python packaging/build.py            # -> dist/TractionWorkbench/ and dist/TractionWorkbench-<ver>-<os>-<arch>.zip

Steps: Windows version resource -> PyInstaller (one-folder, GUI + console exe) -> frozen self-test
(``twb selftest``: every page, verdicts of the built-in examples, golden acceptance, PDF report) -> zip.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def version_resource(ver: str) -> None:
    parts = [int(x) for x in ver.split(".")[:3]] + [0]
    t = tuple(parts[:4])
    (ROOT / "packaging" / "version_info.txt").write_text(f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Traction Workbench'),
      StringStruct('FileDescription', 'Traction Workbench - inverter/motor feasibility and decision workbench'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'TractionWorkbench'),
      StringStruct('OriginalFilename', 'TractionWorkbench.exe'),
      StringStruct('ProductName', 'Traction Workbench'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def main() -> int:
    from traction_workbench import __version__ as ver
    win = sys.platform.startswith("win")
    if win:
        version_resource(ver)
    dist = ROOT / "dist"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(dist),
                    "--workpath", str(ROOT / "build" / "pyinstaller"), str(ROOT / "packaging" / "traction_workbench.spec")],
                   check=True, cwd=ROOT)
    app_dir = dist / "TractionWorkbench"
    cli = app_dir / ("twb.exe" if win else "twb")
    out = ROOT / "build" / "selftest"
    shutil.rmtree(out, ignore_errors=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([str(cli), "acceptance"], env=env, cwd=ROOT / "build")
    if r.returncode != 0:
        print("frozen acceptance failed", file=sys.stderr)
        return r.returncode
    r = subprocess.run([str(cli), "selftest", str(out)], env=env, cwd=ROOT / "build")
    if r.returncode != 0:
        print(f"frozen self-test failed (see {out / 'selftest.json'})", file=sys.stderr)
        return r.returncode
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine())
    osname = "windows" if win else ("macos" if sys.platform == "darwin" else "linux")
    zpath = dist / f"TractionWorkbench-{ver}-{osname}-{arch}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(app_dir.rglob("*")):
            z.write(p, Path("TractionWorkbench") / p.relative_to(app_dir))
    print(f"built {app_dir} and {zpath} ({zpath.stat().st_size / 2**20:.1f} MB); frozen self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
