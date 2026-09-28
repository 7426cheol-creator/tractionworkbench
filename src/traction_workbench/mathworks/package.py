"""The MathWorks transfer package: export, integrity check, local run and report re-import.

A package is a directory of plain JSON files (ASCII, canonical: sorted keys, shortest round-trip floats) plus the
MATLAB sources that read them.  Every file is listed in ``manifest.json`` with its SHA-256; the semantic fingerprint
is the SHA-256 over those (path, digest) pairs, so a byte-equal package is the semantically equal one and the
fingerprint does not move with the generation time or the machine.  Reports written by the target name the
fingerprint and every consumed file digest; ``verify_report`` links a report only to that package and - given the
current project - only to that design revision (a report for another revision is never current evidence).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from .. import __version__
from .. import spec_fixtures as sf
from ..errors import InputValidationError
from ..identity import implementation
from . import cases as CS
from . import oracle as O
from .architecture import candidates, profile_template
from .contract import CONVENTIONS, MODEL_SCHEMA, QUANTITIES, contract_document

SCHEMA = "twb-mathworks/1"
REPORT_SCHEMA = "twb-mathworks-report/1"
GENERATOR = "traction_workbench.mathworks r1"


def _matlab_dir() -> Path:
    """The shipped MATLAB sources: next to this module, or in the PyInstaller bundle."""
    import sys
    here = Path(__file__).with_name("matlab")
    frozen = getattr(sys, "_MEIPASS", None)
    if not (here / "+twb").is_dir() and frozen:
        return Path(frozen) / "traction_workbench" / "mathworks" / "matlab"
    return here


MATLAB_DIR = _matlab_dir()
DOC_FILES = ("README.md", "AGENT_TASKS.md")          # human text: integrity-checked, not part of the fingerprint
SEMANTIC_SOURCES = ("physics.py", "settings.py", "status.py", "identity.py", "models/components.py",
                    "models/flux.py", "solvers/gate.py", "mathworks/contract.py", "mathworks/cases.py",
                    "mathworks/oracle.py", "mathworks/package.py")


class PackageConflict(InputValidationError):
    """The target directory holds edited generated files or is not a package directory."""


# -- canonical files ------------------------------------------------------------------------------------------


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(files: list) -> str:
    h = hashlib.sha256()
    for f in sorted(files, key=lambda f: f["path"]):
        if f["path"] in DOC_FILES:
            continue
        h.update(f"{f['path']}\0{f['sha256']}\n".encode("utf-8"))
    return h.hexdigest()


def matlab_sources() -> dict:
    """Published path -> bytes of the MATLAB templates shipped with this generator."""
    files = {f"matlab/+twb/{p.name}": p.read_bytes() for p in sorted((MATLAB_DIR / "+twb").glob("*.m"))}
    if not files:
        raise FileNotFoundError(f"the MATLAB sources of the transfer package are missing ({MATLAB_DIR})")
    return files


def _source_digests() -> dict:
    root = Path(__file__).resolve().parents[1]
    out = {}
    for rel in SEMANTIC_SOURCES:
        p = root / rel
        out[f"traction_workbench/{rel}"] = _sha(p.read_bytes()) if p.is_file() else None
    if all(v is None for v in out.values()):
        return {"note": "source files not readable in this build (frozen application): see implementation"}
    return out


# -- content --------------------------------------------------------------------------------------------------


def default_requirement_cases() -> list:
    """The example requirement cases shipped with the Workbench (examples/cases)."""
    from ..examples import examples_dir
    out = []
    ex = examples_dir()
    if ex is None:
        return out
    for p in sorted((ex / "cases").glob("*.json")):
        out.append((p.stem, json.loads(p.read_text(encoding="utf-8"))))
    return out


def _gap_report(project, models: dict, counts: dict) -> dict:
    prod = models["PRODUCT"]
    loss = prod["inverter"]["loss"]["kind"]
    fam = prod["family"]
    items = [
        {"capability": "forward evaluation, constant dq (D1)", "status": "exported_executable",
         "target": "native MATLAB twb.evaluatePoint / twb.staticPoint",
         "detail": "voltages, torques, powers, losses, voltage budget, constraint states, energy mode, identities "
                   "and the evidence gate; checked by the forward cases"},
        {"capability": "forward evaluation, flux map (D2)", "status": "exported_executable",
         "target": "native MATLAB twb.fluxMapLookup (bilinear inside valid cells, no extrapolation)",
         "detail": "static use only: the interpolant is not qualified for dynamic (current-state) use"},
        {"capability": "flux-map plane selection and declared temperature interpolation", "status":
         "exported_executable", "target": "twb.selectPlane", "detail": "no temperature extrapolation"},
        {"capability": "requirement witness re-check (FEASIBLE claims)", "status": "exported_executable",
         "target": "twb.checkWitness", "detail": "the witness is checked against the ORIGINAL request; INFEASIBLE "
                                                  "and UNKNOWN claims stay layer-2 evidence"},
        {"capability": "Simulink static evaluation harness", "status": "exported_recipe",
         "target": "twb.buildEvaluationHarness / twb.runHarness (MATLAB Function block calling twb.staticPoint)",
         "detail": "generated and run on a Simulink installation; NOT_RUN until then. A static evaluation block, "
                   "not a dynamic plant"},
        {"capability": "System Composer / SLDD candidate mapping and conflict detection", "status":
         "exported_candidates", "target": "architecture/candidates.json, twb.buildArchitectureCandidate, "
                                         "twb.checkDictionary, twb.compareDataItems",
         "detail": "generic candidates; the company profile (names, stereotypes, namespaces) is missing until "
                   "the company supplies it"},
        {"capability": "minimum-current policy (MTPA / field weakening), capability, exclusion certificates",
         "status": "exported_data_only", "target": "cases/requirement_witness.json (claims, witnesses, evidence "
                                                    "kinds)",
         "detail": "the optimiser and its certificates are not ported: a native optimiser would not inherit the "
                   "global certificates"},
        {"capability": "inverter loss: datasheet module model", "status":
         "unsupported" if loss == "datasheet_module" else "exported_data_only",
         "target": "source/project.json section 'module'",
         "detail": ("the PRODUCT drive uses it: its DC-side quantities are NOT_SUPPORTED natively"
                    if loss == "datasheet_module" else
                    "module curves travel as project data; the core drive uses the quadratic surrogate")},
        {"capability": "thermal networks, DC-link ripple, lifetime, PWM policy, anti-jerk, protection / ASC, EMI, "
                       "efficiency boundaries, machine design", "status": "exported_data_only",
         "target": "source/project.json (sections with digests and provenance), source/exchange.json (conventions "
                   "and fixture values)",
         "detail": "not in the executable port list of this revision"},
        {"capability": "dynamic models: initial states, ZOH, events, delays (X07)", "status": "not_applicable",
         "target": "-", "detail": "no dynamic model is exported in this revision; nothing dynamic is invented"},
        {"capability": "physical ports and energy conservation of a network model (X08)", "status":
         "not_applicable", "target": "-", "detail": "no physical-network model is exported; the physical "
                                                    "connections are candidates only"},
        {"capability": "OEW / HEV topologies (X11)", "status": "not_applicable", "target": "-",
         "detail": "the product drive is a single VSI; OEW / HEV are never reduced to a single VSI"},
        {"capability": "Requirements Toolbox native links", "status": "missing", "target": "-",
         "detail": "the trace (requirement -> case -> evidence) is exported as data; native links are not "
                   "generated"},
        {"capability": "code generation, HIL, FMU", "status": "not_applicable", "target": "-",
         "detail": "running in Simulink is not code-generation, real-time or HIL fitness"},
    ]
    return {"schema": "twb-mathworks-gaps/1", "product_family": fam, "product_loss_model": loss,
            "case_counts": counts, "items": items,
            "status_meaning": {"exported_executable": "native target code + parity cases",
                               "exported_recipe": "target-side generator; runs where the product exists",
                               "exported_candidates": "candidates for mapping, no company data changed",
                               "exported_data_only": "data / contract / evidence transfer, no native execution",
                               "not_applicable": "does not arise for this package",
                               "missing": "needed later, not provided", "unsupported": "cannot be ported as is"}}


def build_contents(project=None, requirement_cases=None) -> tuple:
    """(files: path -> bytes, info) - everything computed before anything is written."""
    from ..exchange import build_package as exchange_package
    from ..io import drive_from_dict
    from ..project import builtin_project
    from .texts import agent_tasks, readme
    project = project or builtin_project()
    ref = O.load_reference(sf.spec_dir())
    b = CS.Builder(ref)
    CS.reference_cases(b)
    CS.verification_cases(b)
    CS.product_cases(b, drive_from_dict(project.data("drive")), project)
    req = CS.requirement_cases(b, default_requirement_cases() if requirement_cases is None else requirement_cases,
                               "PRODUCT")
    files: dict[str, bytes] = {}
    files["contract.json"] = _json_bytes(contract_document())
    for key, doc in b.models.items():
        files[f"models/{key}.json"] = _json_bytes(doc)
    for name, cs in b.cases.items():
        files[f"cases/{name}.json"] = _json_bytes({"schema": CS.CASES_SCHEMA, "set": name, "cases": cs})
    arch = candidates(project, req)
    files["architecture/candidates.json"] = _json_bytes(arch)
    files["architecture/profile_template.json"] = _json_bytes(profile_template(arch))
    files["source/project.json"] = _json_bytes(project.to_dict())
    ex = exchange_package(include_examples=True)
    ex.pop("created_utc", None)                     # the package generation time lives in the manifest only
    files["source/exchange.json"] = _json_bytes(ex)
    files["source/reference_manifest.json"] = _json_bytes(
        {"package": sf.SPEC_DIRNAME, "version": ref["version"],
         "files_used_as_oracle": {n: f["sha256"] for n, f in ref["files"].items()}})
    counts = {k: len(v) for k, v in b.cases.items()}
    disagree = [c["case_id"] for k in ("forward", "flux_lookup") for c in b.cases[k]
                if c["python_vs_oracle"]["status"] != "PASS"]
    files["gap_report.json"] = _json_bytes(_gap_report(project, b.models, counts))
    files.update(matlab_sources())
    info = {"project": project, "models": {k: {"role": d["role"], "family": d["family"],
                                              "content_sha256": d["identity"]["content_sha256"],
                                              "drive_id": d["identity"]["drive_id"]} for k, d in b.models.items()},
            "case_counts": counts, "oracle_disagreements": disagree}
    files["README.md"] = readme(info).encode("utf-8")
    files["AGENT_TASKS.md"] = agent_tasks(info).encode("utf-8")
    return files, info


# -- export ---------------------------------------------------------------------------------------------------


def _existing_guard(out: Path, force: bool) -> dict:
    """Generated files of a previous export: path -> sha.  Edited generated files stop the export (never a silent
    overwrite); a non-package directory with content is refused; files the generator did not write are kept."""
    man = out / "manifest.json"
    if not man.is_file():
        if out.exists() and any(out.iterdir()) and not force:
            raise PackageConflict(f"{out} is not empty and holds no transfer package: choose an empty folder",
                                  field="out_dir")
        return {}
    try:
        old = json.loads(man.read_text(encoding="utf-8"))
    except ValueError:
        raise PackageConflict(f"{man} is not readable JSON", field="out_dir") from None
    if old.get("schema") != SCHEMA:
        raise PackageConflict(f"{out} holds a package of schema {old.get('schema')!r}, not {SCHEMA}",
                              field="out_dir")
    listed = {f["path"]: f["sha256"] for f in old.get("files", [])}
    edited = [p for p, h in listed.items() if (out / p).is_file() and _sha((out / p).read_bytes()) != h]
    if edited and not force:
        raise PackageConflict("generated files were edited after the export (keep your changes elsewhere or "
                              "export to a new folder): " + ", ".join(sorted(edited)), field="out_dir")
    return {p: h for p, h in listed.items() if p not in edited}


def export_package(out_dir, project=None, requirement_cases=None, *, force: bool = False) -> dict:
    """Write the transfer package into ``out_dir`` and return its manifest (plus the export summary)."""
    out = Path(out_dir)
    previous = _existing_guard(out, force)
    files, info = build_contents(project, requirement_cases)
    project = info["project"]
    entries = [{"path": p, "sha256": _sha(data), "bytes": len(data)} for p, data in sorted(files.items())]
    fp = fingerprint(entries)
    mfiles = [f for f in entries if f["path"].startswith("matlab/")]
    manifest = {
        "schema": SCHEMA,
        "generator": {"name": GENERATOR, "tool_version": __version__,
                      "matlab_templates_sha256": fingerprint(mfiles)},
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "implementation": implementation(),
        "implementation_sources_sha256": _source_digests(),
        "project": project.identity(),
        "project_label": project.label,
        "product_model": "PRODUCT",
        "models": info["models"],
        "case_counts": info["case_counts"],
        "oracle_disagreements": info["oracle_disagreements"],
        "files": entries,
        "semantic_fingerprint": fp,
        "semantic_fingerprint_basis": "SHA-256 over (path, SHA-256) of every listed file except the human-readable "
                                      "docs; files are canonical JSON, so byte-equal <=> semantically equal; the "
                                      "generation time and the implementation identity are not part of it",
        "statuses": {"package_check": "PASS", "target_environment": "NOT_CHECKED", "model_generation": "NOT_RUN",
                     "parity": "NOT_RUN", "physical_validation": "NOT_CLAIMED"},
        "entry_points": {
            "matlab": "addpath(fullfile('<package>', 'matlab')); report = twb.runAll('<package>');",
            "batch": "matlab -batch \"addpath(fullfile('<package>','matlab')); r = twb.runAll('<package>'); "
                     "exit(double(~r.ok))\"",
            "octave": "octave-cli --eval \"addpath(fullfile('<package>','matlab')); r = twb.runAll('<package>'); "
                      "exit(double(~r.ok))\"",
            "workbench": "twb mathworks run <package> | twb mathworks verify <package> [--project P]"},
    }
    out.mkdir(parents=True, exist_ok=True)
    for p in previous:                            # unmodified generated files the new export no longer contains
        if p not in files and (out / p).is_file():
            (out / p).unlink()
    for p, data in files.items():
        target = out / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (out / "results").mkdir(exist_ok=True)
    (out / "manifest.json").write_bytes(_json_bytes(manifest))
    return {**manifest, "dir": str(out)}


# -- package check --------------------------------------------------------------------------------------------


def _load(pkg: Path, rel: str):
    return json.loads((pkg / rel).read_text(encoding="utf-8"))


def check_package(pkg_dir) -> dict:
    """Schema, integrity (every listed file present with its digest), fingerprint, model and case structure."""
    pkg = Path(pkg_dir)
    problems, warnings = [], []
    try:
        man = _load(pkg, "manifest.json")
    except (OSError, ValueError) as exc:
        return {"status": "FAIL", "problems": [f"manifest.json unreadable: {exc}"], "warnings": []}
    if man.get("schema") != SCHEMA:
        return {"status": "FAIL", "problems": [f"unknown package schema {man.get('schema')!r} (this Workbench reads "
                                               f"{SCHEMA}; a later schema is not guessed)"], "warnings": []}
    listed = {f["path"]: f for f in man.get("files", [])}
    for p, f in listed.items():
        fp = pkg / p
        if not fp.is_file():
            problems.append(f"missing file {p}")
        elif _sha(fp.read_bytes()) != f["sha256"]:
            problems.append(f"{p}: content differs from the manifest (edited or damaged)")
    if fingerprint(list(listed.values())) != man.get("semantic_fingerprint"):
        problems.append("semantic fingerprint does not match the listed files")
    if problems:
        return {"status": "FAIL", "problems": problems, "warnings": warnings, "fingerprint": man.get(
            "semantic_fingerprint")}
    contract = _load(pkg, "contract.json")
    for q, spec in contract.get("quantities", {}).items():
        if not spec.get("unit"):
            problems.append(f"contract quantity {q} has no unit")
    if contract.get("conventions") != CONVENTIONS:
        problems.append("contract conventions differ from the ones this Workbench computes")
    models = {}
    for p in listed:
        if p.startswith("models/"):
            m = _load(pkg, p)
            models[m.get("model_key")] = m
            problems += [f"{p}: {x}" for x in _model_problems(m)]
    ids = set()
    for p in ("cases/forward.json", "cases/flux_lookup.json", "cases/requirement_witness.json"):
        if p not in listed:
            problems.append(f"missing case set {p}")
            continue
        doc = _load(pkg, p)
        if doc.get("schema") != CS.CASES_SCHEMA:
            problems.append(f"{p}: unknown schema {doc.get('schema')!r}")
            continue
        for c in doc["cases"]:
            if c["case_id"] in ids:
                problems.append(f"duplicate case id {c['case_id']}")
            ids.add(c["case_id"])
            if c["model"] not in models:
                problems.append(f"{c['case_id']}: unknown model {c['model']}")
            if c["kind"] == "forward":
                missing = [q for q in QUANTITIES if q not in c["python"]]
                if missing:
                    problems.append(f"{c['case_id']}: reference values missing for {missing}")
    if man.get("oracle_disagreements"):
        warnings.append("layer 2 disagrees with layer 1 on: " + ", ".join(man["oracle_disagreements"]))
    return {"status": "PASS" if not problems else "FAIL", "problems": problems, "warnings": warnings,
            "fingerprint": man["semantic_fingerprint"], "manifest": man}


def _model_problems(m: dict) -> list:
    out = []
    if m.get("schema") != MODEL_SCHEMA:
        return [f"unknown model schema {m.get('schema')!r}"]
    if m.get("conventions") != CONVENTIONS:
        out.append("conventions differ from the implemented ones")
    p = m["motor"].get("pole_pairs")
    if not isinstance(p, int) or p < 1:
        out.append("pole_pairs must be a positive integer (pole PAIRS)")
    f = m["motor"]["flux"]
    if f["kind"] == "flux_map":
        if f.get("psi_temperature") is not None:
            out.append("a flux map with a psi_PM temperature law would correct temperature twice")
        for i, pl in enumerate(f["planes"]):
            nd, nq = len(pl["id_axis_A"]), len(pl["iq_axis_A"])
            for key in ("psi_d_Wb", "psi_q_Wb", "valid"):
                arr = pl[key]
                if len(arr) != nd or any(len(r) != nq for r in arr):
                    out.append(f"plane {i} {key}: shape is not [numel(id_axis) numel(iq_axis)] = [{nd} {nq}]")
            for ax in ("id_axis_A", "iq_axis_A"):
                a = pl[ax]
                if any(b <= x for x, b in zip(a, a[1:])):
                    out.append(f"plane {i} {ax} is not strictly increasing")
    elif f["kind"] != "constant_dq":
        out.append(f"unsupported family {f['kind']!r}")
    return out


# -- report re-import -----------------------------------------------------------------------------------------


def _num(a, b, atol, rtol) -> bool:
    a = None if a is None or (isinstance(a, float) and math.isnan(a)) else a
    b = None if b is None or (isinstance(b, float) and math.isnan(b)) else b
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= atol + rtol * max(abs(a), abs(b))


def _txt(x):
    return None if x in (None, "", []) else x


def _set(x) -> set:
    return set(x or [])


def compare_case(case: dict, native: dict, contract: dict) -> list:
    """The failures of one case recomputed from the target's raw values (the report's own verdict is not
    trusted): the same rules as twb.compareCase."""
    cls = contract["parity_tolerance"]["classes"]
    qd = contract["quantities"]
    f = []
    kind = case["kind"]
    if kind == "forward":
        py, uns = case["python"], set(case.get("unsupported", []))
        for q in qd:
            if q not in uns and not _num(native.get(q), py.get(q), *cls[qd[q]["class"]]):
                f.append(f"python:{q}")
        for key in ("evaluable",) + (() if "accepted" in uns else ("accepted",)):
            if bool(native.get(key)) != bool(py.get(key)):
                f.append(key)
        if _txt(native.get("reason")) != _txt(py.get("reason")):
            f.append("reason")
        for key in ("issues", "violated_groups") + (() if "gate_reasons" in uns else ("gate_reasons",)):
            if _set(native.get(key)) != _set(py.get(key)):
                f.append(key)
        if "energy_mode" not in uns and _txt(native.get("energy_mode")) != _txt(py.get("energy_mode")):
            f.append("energy_mode")
        if py.get("identities_ok") is not None and bool(native.get("identities_ok")) != bool(py["identities_ok"]):
            f.append("identities_ok")
        skip_dc = "constraints:DC" in uns
        got = {k: v for k, v in (native.get("constraints") or {}).items() if not (skip_dc and k.startswith("DC_"))}
        want = {k: v for k, v in (py.get("constraints") or {}).items() if not (skip_dc and k.startswith("DC_"))}
        if got != want:
            f.append("constraints")
        for o in case.get("oracle", []):
            for q, v in (o.get("values") or {}).items():
                if q in uns:
                    continue
                if o["kind"] == "golden":
                    atol, rtol = o["tolerance"]["atol"], o["tolerance"]["rtol"]
                else:
                    atol, rtol = cls[qd[q]["class"]][0] * o["tolerance"]["atol_scale"], o["tolerance"]["rtol"]
                if not _num(native.get(q), v, atol, rtol):
                    f.append(f"oracle({o['kind']}):{q}")
            if "covered" in o and bool(native.get("evaluable")) != bool(o["covered"]):
                f.append(f"oracle({o['kind']}):covered")
    elif kind == "flux_lookup":
        py = case["python"]
        if bool(native.get("covered")) != bool(py["covered"]) or _txt(native.get("reason")) != _txt(py["reason"]):
            f.append("coverage")
        for q in ("psi_d_Wb", "psi_q_Wb"):
            if not _num(native.get(q), py[q], *cls["Wb"]):
                f.append(f"python:{q}")
    else:
        py = case["python"]
        if bool(native.get("witness_accepted")) != bool(py["witness_accepted"]):
            f.append("witness_accepted")
        if native.get("claim_support") != py["claim_support"] or _set(native.get("reasons")) != _set(py["reasons"]):
            f.append("claim_support/reasons")
        if not _num(native.get("Tshaft_Nm"), py["Tshaft_Nm"], *cls["Nm"]):
            f.append("python:Tshaft_Nm")
    return f


def verify_report(pkg_dir, report_path=None, project=None) -> dict:
    """Re-import a target report: identity checks first (X12), then every case recomputed from raw values.

    Returns the status block the Workbench shows - kept apart, never merged into one green light:
    package check / target environment / model generation / parity / physical validation.
    """
    pkg = Path(pkg_dir)
    chk = check_package(pkg)
    out = {"package_check": chk["status"], "package_problems": chk["problems"],
           "target_environment": "NOT_CHECKED", "model_generation": "NOT_RUN", "parity": "NOT_RUN",
           "physical_validation": "NOT_CLAIMED (implementation verification V0-V4; not a physical qualification)",
           "linked_as_current_evidence": False, "problems": [], "cases": {}}
    if chk["status"] != "PASS":
        return out
    man = chk["manifest"]
    rp = Path(report_path) if report_path else pkg / "results" / "parity_report.json"
    if project is not None and project.digest() != man["project"]["project_digest"]:
        out["problems"].append(f"the package was exported from {man['project_label']} (digest "
                               f"{man['project']['project_digest'][:12]}); the current design is {project.label} "
                               f"({project.digest()[:12]}): results of this package are not current evidence")
    if not rp.is_file():
        out["problems"].append("no target report yet: every target stage is NOT_RUN")
        return out
    rep = json.loads(rp.read_text(encoding="utf-8"))
    if rep.get("schema") != REPORT_SCHEMA:
        out["problems"].append(f"unknown report schema {rep.get('schema')!r}")
        return out
    env = rep.get("runtime") or {}
    prod = env.get("product") or "unknown runtime"
    out["target_environment"] = (f"{prod} {env.get('version', '')}".strip()
                                 + (f" ({env['release']})" if env.get("release") else "")
                                 + (" - MATLAB-language proxy; MATLAB / Simulink not run" if prod == "GNU Octave"
                                    else ""))
    pk = rep.get("package") or {}
    consumed = {f["path"]: f["sha256"] for f in (pk.get("consumed_files") or [])}
    listed = {f["path"]: f["sha256"] for f in man["files"]}
    if pk.get("semantic_fingerprint") != man["semantic_fingerprint"] or consumed != listed:
        out["problems"].append("the report was produced for another package (fingerprint / consumed file digests "
                               "differ): it is not linked to this package")
        out["parity"] = "FOREIGN_REPORT"
        return out
    stages = rep.get("stages") or {}
    out["model_generation"] = (stages.get("simulink_harness") or {}).get("status", "NOT_RUN")
    out["stages"] = {k: v.get("status") for k, v in stages.items()}
    contract = _load(pkg, "contract.json")
    by_id = {c["case_id"]: c for c in rep.get("cases") or []}
    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "NOT_SUPPORTED": 0, "MISSING": 0}
    inconsistent, failed = [], []
    for name in ("forward", "flux_lookup", "requirement_witness"):
        for case in _load(pkg, f"cases/{name}.json")["cases"]:
            r = by_id.get(case["case_id"])
            if r is None:
                counts["MISSING"] += 1
                continue
            if r.get("status") in ("ERROR", "NOT_SUPPORTED"):
                counts[r["status"]] += 1
                if r["status"] == "NOT_SUPPORTED" and case.get("python") is not None:
                    inconsistent.append(case["case_id"])
                continue
            fails = compare_case(case, r.get("native") or {}, contract)
            status = "FAIL" if fails else "PASS"
            counts[status] += 1
            if status == "FAIL":
                failed.append({"case_id": case["case_id"], "failures": fails})
            if status != r.get("status"):
                inconsistent.append(case["case_id"])
    out["cases"] = counts
    out["failed_cases"] = failed
    if inconsistent:
        out["problems"].append("the report's own verdicts differ from the recomputation for: "
                               + ", ".join(inconsistent))
    ok = counts["FAIL"] == 0 and counts["ERROR"] == 0 and counts["MISSING"] == 0 and not inconsistent
    out["parity"] = "PASS" if ok and counts["PASS"] > 0 else "FAIL"
    out["linked_as_current_evidence"] = out["parity"] == "PASS" and not any(
        "not current evidence" in p for p in out["problems"])
    out["report"] = str(rp)
    return out


# -- local execution ------------------------------------------------------------------------------------------


def find_runtimes() -> list:
    """MATLAB / GNU Octave executables on this PC (PATH), MATLAB first."""
    out = []
    for kind, names in (("matlab", ("matlab",)), ("octave", ("octave-cli", "octave"))):
        for n in names:
            p = shutil.which(n)
            if p:
                out.append({"kind": kind, "path": p})
                break
    return out


def _quote(p: Path) -> str:
    return str(p.resolve()).replace("'", "''")


def run_local(pkg_dir, runtime: str | None = None, timeout_s: float = 1800.0) -> dict:
    """Run twb.runAll in a local MATLAB (-batch) or GNU Octave, then re-import its report."""
    pkg = Path(pkg_dir)
    rts = find_runtimes()
    if runtime is not None:
        rts = [r for r in rts if r["kind"] == runtime]
    if not rts:
        raise InputValidationError("no MATLAB or GNU Octave executable found on PATH" if runtime is None else
                                   f"no {runtime} executable found on PATH", field="runtime")
    rt = rts[0]
    q = _quote(pkg)
    cmd = f"addpath(fullfile('{q}', 'matlab')); r = twb.runAll('{q}'); exit(double(~r.ok));"
    argv = ([rt["path"], "-batch", cmd] if rt["kind"] == "matlab" else
            [rt["path"], "--no-gui", "--quiet", "--norc", "--eval", cmd])
    (pkg / "results").mkdir(exist_ok=True)
    log = pkg / "results" / f"run_{rt['kind']}.log"
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, cwd=str(pkg))
        code, text = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, text = None, f"timed out after {timeout_s:g} s\n{exc.stdout or ''}{exc.stderr or ''}"
    log.write_text(text if isinstance(text, str) else str(text), encoding="utf-8")
    return {"runtime": rt, "exit_code": code, "log": str(log), "verification": verify_report(pkg)}
