#!/usr/bin/env python3
"""Run the CI workflow's jobs on this machine, for when GitHub Actions cannot run them.

The steps of .github/workflows/build.yml, from a clean clone of one commit (never the working tree) into a fresh
virtual environment per job, with the CI's Python (3.12 unless --python says otherwise):

    test       pip install -e ".[gui,test]"; the independent fixture check; QT_QPA_PLATFORM=offscreen pytest -q
    mathworks  pip install -e ".[test]"; twb mathworks export, then run --runtime octave; pytest tests/test_mathworks.py
    package    pip install -e ".[gui,build]"; python packaging/build.py (PyInstaller, then acceptance and the desktop
               self-test inside the frozen application).  These are the Windows job's steps on THIS operating system:
               off Windows they check the spec, hidden imports, data files and the frozen self-test, not the .exe.

The system packages the workflow installs with apt (Qt runtime libraries, CJK fonts, GNU Octave) must already be
present; a job whose tool is missing is reported NOT_RUN, never passed.

    python verification/local_ci.py                                  # HEAD, every job, one after another
    python verification/local_ci.py --ref 616e67d --jobs test,mathworks --parallel 2

Writes build/local_ci/<commit>/summary.md and summary.json (job, result, duration, the key line of every step), the
full log of every step, and the workflow's artifacts (the MathWorks package, the frozen self-test output).  Exit
status 0 only when every requested job passed.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIN = os.name == "nt"

# job -> (title, pip extras, steps, timeout in minutes, required tool); a step is (label, command, extra env) and
# "python" / "twb" mean the job's own virtual environment.  Keep in step with .github/workflows/build.yml.
JOBS = {
    "test": ("tests (Linux, headless Qt)", "gui,test", [
        ("independent fixture check", ["python", "verification/independent_fixture_check.py"], {}),
        ("pytest", ["python", "-m", "pytest", "-q"], {"QT_QPA_PLATFORM": "offscreen"}),
    ], 30, None),
    "mathworks": ("MathWorks transfer package (GNU Octave runs the MATLAB code)", "test", [
        ("export the package", ["twb", "mathworks", "export", "build/mathworks"], {}),
        ("run twb.runAll in Octave", ["twb", "mathworks", "run", "build/mathworks", "--runtime", "octave"], {}),
        ("transfer tests", ["python", "-m", "pytest", "-q", "tests/test_mathworks.py"], {}),
    ], 30, "octave-cli"),
    "package": ("frozen application: the Windows job's steps on this OS", "gui,build", [
        ("PyInstaller build and frozen self-test", ["python", "packaging/build.py"], {}),
    ], 60, None),
}
ARTIFACTS = ("build/mathworks", "build/selftest")
KEY = re.compile(r"\d+ (passed|failed)|independent fixture checks passed|parity|frozen self-test|built |error|Error")


def _git(*args, cwd=ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _key_line(log: str) -> str:
    lines = [ln.strip() for ln in log.splitlines() if ln.strip()]
    hits = [ln for ln in lines if KEY.search(ln)]
    return (hits or lines or [""])[-1][:200]


def run_job(name: str, sha: str, python: str, out: Path, keep: bool) -> dict:
    title, extras, steps, minutes, tool = JOBS[name]
    jdir = out / name
    shutil.rmtree(jdir, ignore_errors=True)
    jdir.mkdir(parents=True)
    res = {"job": name, "title": title, "result": "PASS", "steps": []}
    if tool and shutil.which(tool) is None:
        return {**res, "result": "NOT_RUN", "reason": f"{tool} is not installed on this machine", "duration_s": 0.0}
    t0 = time.perf_counter()
    work = out / "work" / name
    shutil.rmtree(work, ignore_errors=True)
    # a clean clone of the commit with its git metadata, like actions/checkout (the working tree is never used)
    _git("clone", "--quiet", "--no-checkout", str(ROOT), str(work))
    _git("checkout", "--quiet", "--detach", sha, cwd=work)
    venv = work / ".venv"
    bindir = venv / ("Scripts" if WIN else "bin")
    exe = lambda n: str(bindir / (n + (".exe" if WIN else "")))       # noqa: E731
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "QT_QPA_PLATFORM", "VIRTUAL_ENV")}
    env.update(PATH=str(bindir) + os.pathsep + env.get("PATH", ""), VIRTUAL_ENV=str(venv), PYTHONUNBUFFERED="1")
    setup = [("create the virtual environment", [python, "-m", "venv", str(venv)], {}),
             ("upgrade pip", ["python", "-m", "pip", "install", "--upgrade", "pip"], {}),
             (f"install .[{extras}]", ["python", "-m", "pip", "install", "-e", f".[{extras}]"], {})]
    deadline = t0 + 60.0 * minutes
    for i, (label, cmd, extra) in enumerate(setup + steps, start=1):
        cmd = [exe(c) if k == 0 and c in ("python", "twb") else c for k, c in enumerate(cmd)]
        s0 = time.perf_counter()
        try:
            p = subprocess.run(cmd, cwd=work, env={**env, **extra}, capture_output=True, text=True,
                               timeout=max(1.0, deadline - s0))
            rc, log = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            rc = "timeout"
            log = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            log += f"\n[local CI] the job's {minutes}-minute limit ran out during this step"
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        (jdir / f"{i:02d}_{slug}.log").write_text(f"$ {' '.join(cmd)}\n\n{log}", encoding="utf-8")
        res["steps"].append({"step": label, "rc": rc, "duration_s": round(time.perf_counter() - s0, 1),
                             "key": _key_line(log.replace(str(work) + os.sep, ""))})
        if rc != 0:
            res["result"] = "FAIL"
            break
    for rel in ARTIFACTS:                                    # the workflow's uploaded artifacts
        if (work / rel).exists():
            shutil.copytree(work / rel, jdir / Path(rel).name, dirs_exist_ok=True)
    res["duration_s"] = round(time.perf_counter() - t0, 1)
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return res


def _summary_md(meta: dict, results: list) -> str:
    rows = [f"# Local CI — {meta['commit'][:12]}", "",
            f"{meta['subject']}", "",
            f"{meta['finished_utc']} · {meta['host']} · Python {meta['python']} · {meta['octave'] or 'no GNU Octave'}",
            "",
            "The steps of `.github/workflows/build.yml`, from a clean clone of the commit into a fresh virtual "
            "environment per job (`python verification/local_ci.py`). The package job runs the Windows job's steps "
            "on this operating system.", "",
            "| job | result | time | key output |", "|---|---|---|---|"]
    for r in results:
        key = r.get("reason") or (r["steps"][-1]["key"] if r["steps"] else "")
        rows.append(f"| {r['title']} | **{r['result']}** | {r['duration_s'] / 60:.1f} min | {key.replace('|', '/')} |")
    rows += ["", "## Steps", ""]
    for r in results:
        for s in r["steps"]:
            ok = "ok" if s["rc"] == 0 else f"FAILED ({s['rc']})"
            rows.append(f"- {r['job']} / {s['step']}: {ok}, {s['duration_s']:.0f} s — {s['key'] or '-'}")
    return "\n".join(rows) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="HEAD", help="commit to test (default HEAD; the working tree is never used)")
    ap.add_argument("--jobs", default=",".join(JOBS), help=f"comma-separated, from {', '.join(JOBS)}")
    ap.add_argument("--python", default="python3.12" if not WIN else sys.executable,
                    help="interpreter for the virtual environments (the workflow uses 3.12)")
    ap.add_argument("--parallel", type=int, default=1, help="jobs run at the same time (default 1)")
    ap.add_argument("--out", default=None, help="output folder (default build/local_ci/<commit>)")
    ap.add_argument("--keep", action="store_true", help="keep the clones and virtual environments")
    a = ap.parse_args()
    jobs = [j.strip() for j in a.jobs.split(",") if j.strip()]
    bad = [j for j in jobs if j not in JOBS]
    if bad:
        ap.error(f"unknown job(s) {bad}; jobs: {', '.join(JOBS)}")
    if shutil.which(a.python) is None and not Path(a.python).exists():
        ap.error(f"interpreter {a.python!r} not found (the workflow uses Python 3.12; pass --python)")
    sha = _git("rev-parse", "--verify", f"{a.ref}^{{commit}}")
    out = Path(a.out) if a.out else ROOT / "build" / "local_ci" / sha[:12]
    out.mkdir(parents=True, exist_ok=True)
    pyver = subprocess.run([a.python, "--version"], capture_output=True, text=True).stdout.strip().split()[-1]
    octave = shutil.which("octave-cli")
    if octave:
        octave = subprocess.run([octave, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    meta = {"commit": sha, "subject": _git("log", "-1", "--format=%s", sha), "python": pyver, "octave": octave,
            "host": f"{platform.system()} {platform.release()} {platform.machine()}", "jobs": jobs,
            "started_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
    print(f"local CI of {sha[:12]} ({meta['subject']}): {', '.join(jobs)} with Python {pyver} -> {out}", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
        results = list(ex.map(lambda j: run_job(j, sha, a.python, out, a.keep), jobs))
    meta["finished_utc"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    (out / "summary.json").write_text(json.dumps({**meta, "results": results}, indent=2), encoding="utf-8")
    (out / "summary.md").write_text(_summary_md(meta, results), encoding="utf-8")
    if not a.keep:
        shutil.rmtree(out / "work", ignore_errors=True)
    for r in results:
        key = r.get("reason") or (r["steps"][-1]["key"] if r["steps"] else "")
        print(f"{r['result']:8s} {r['job']:10s} {r['duration_s'] / 60:5.1f} min  {key}")
    print(f"summary: {out / 'summary.md'}")
    return 0 if all(r["result"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
