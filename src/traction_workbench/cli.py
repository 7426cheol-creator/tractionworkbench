"""Command-line interface: ``twb <command>`` (or ``python -m traction_workbench``).

    twb gui [--open CASE.json|W.twb-workspace.json]   desktop application (same as TractionWorkbench.exe)
    twb evaluate CASE.json [--out DIR]    decision record (JSON + Markdown)
    twb report CASE.json --pdf out.pdf    PDF engineering report with graphs (no GUI needed)
    twb demo [--out DIR]                  representative engineering questions on the synthetic drive
    twb solve --n 12000 --torque 150 --vdc 600
    twb forward --n 3000 --vdc 600 --id -200 --iq 400
    twb capability --n 12000 --vdc 600 [--direction -1] [--kind policy|physical|electrical]
    twb curve --vdc 600
    twb acceptance                        production output vs the golden fixtures
    twb exchange [OUT.json]               MathWorks-port exchange package (conventions, identities, fixtures)
    twb selftest OUT_DIR [--lang en]      headless check of the desktop application (screenshots + report)
    twb project show|check [PROJECT.json] project data package: identity / cross-section consistency (default:
                                          the built-in synthetic project)
    twb project diff A.json B.json        changed sections, paths and the analyses they feed
    twb project export OUT.json           write the built-in synthetic project (a template to edit)
    twb reqset REQS.csv [--project P.json] [--candidates C.txt] [--out RESULTS.csv]
                                          requirement set on one product: verdict, class, margin, limiting cause,
                                          next data per requirement; candidates re-judged against every requirement
    twb reqset --template OUT.csv         write the requirement CSV template
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import service as S
from . import spec_fixtures as sf
from .decision import jsonable as _jsonable
from .errors import InputValidationError


def _drive(args):
    return S.resolve_drive({"builtin": args.drive})


def _print_json(obj):
    print(json.dumps(_jsonable(obj), indent=2, ensure_ascii=False))


def cmd_gui(args):
    from .desktop.app import main as gui_main
    argv = []
    if args.open:
        argv += ["--open", args.open]
    if args.lang:
        argv += ["--lang", args.lang]
    return gui_main(argv)


def cmd_selftest(args):
    from .desktop.app import main as gui_main
    return gui_main(["--self-test", args.out] + (["--lang", args.lang] if getattr(args, "lang", None) else []))


def cmd_report(args):
    import matplotlib
    matplotlib.use("Agg")
    from .i18n import set_language
    from .io import load_json_file
    from .report_pdf import build_pdf
    set_language(args.lang)
    rec, obj, case = S.evaluate_case_full(load_json_file(args.case))
    out = Path(args.pdf or f"{rec['record_id']}.pdf")
    build_pdf(out, rec, obj, case, envelope=not args.no_envelope,
              progress=lambda f, m="": print(f"  {f * 100:5.1f}% {m}", file=sys.stderr))
    print(f"{rec['verdict']['verdict']}: report written to {out}")
    return 0


def _write_record(rec: dict, out: Path | None):
    if out is None:
        return None
    out.mkdir(parents=True, exist_ok=True)
    md = rec.pop("markdown", "")
    j = out / f"{rec['record_id']}.json"
    j.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / f"{rec['record_id']}.md").write_text(md, encoding="utf-8")
    rec["markdown"] = md
    return j


def cmd_evaluate(args):
    from .io import load_json_file
    rec = S.evaluate_case(load_json_file(args.case))
    path = _write_record(rec, Path(args.out) if args.out else None)
    if args.json:
        _print_json({k: v for k, v in rec.items() if k != "markdown"})
    else:
        print(rec["markdown"])
    if path:
        print(f"\nrecord written: {path} (+ .md)", file=sys.stderr)
    return {"PASS": 0, "FAIL": 2, "UNKNOWN": 3}[rec["verdict"]["verdict"]] if args.exit_code else 0


def cmd_demo(args):
    from .api import PRESETS
    out = Path(args.out) if args.out else None
    rows = []
    for p in PRESETS:
        r = p["req"]
        case = {"drive": {"builtin": "SYNTH_IPMSM_200KW_REF_V1"},
                "requirement": {"id": r["id"], "text": r["text"],
                                "target": {"value": r["torque_Nm"], "unit": "N*m", "torque": "shaft"},
                                "conditions": {"speed": {"value": r["speed_rpm"], "unit": "rpm", "kind": "mechanical"},
                                               "Vdc": {"value": r["Vdc_V"], "unit": "V"}}},
                "analyses": p.get("analyses", {})}
        if r.get("duration_s"):
            case["requirement"]["duration"] = {"value": r["duration_s"], "unit": "s"}
        rec = S.evaluate_case(case)
        _write_record(rec, out)
        c = rec["conditions"][0]
        rows.append((r["id"], rec["verdict"]["verdict"], ", ".join(rec["verdict"]["reasons"]) or "-",
                     c["torque_capability_margin_Nm"], p["hint"]["en"]))
    w = max(len(x[0]) for x in rows)
    print(f"{'requirement':<{w}}  verdict  margin[N*m]  reasons / insight")
    for rid, v, reasons, margin, hint in rows:
        m = "   -" if margin is None else f"{margin:9.4f}"
        print(f"{rid:<{w}}  {v:<7}  {m:>11}  {reasons}  |  {hint}")
    if out:
        print(f"\ndecision records written to {out}/", file=sys.stderr)


def cmd_solve(args):
    d = _drive(args)
    r = S.solve(d, sf.synthetic_limits(), args.n, args.vdc, args.torque)
    if args.json:
        return _print_json(r)
    print(f"minimum-current policy at n = {args.n:g} rpm, Vdc = {args.vdc:g} V, T_shaft = {args.torque:g} N*m")
    for c in r["claims"]:
        print(f"  {c['name']:<28} {c['status']:<10} {', '.join(c['reasons']) or ''}  {c['detail']}")
    op = r["operating_point"]
    if op:
        print(f"  id = {op['id_A_peak']:.6f} A, iq = {op['iq_A_peak']:.6f} A, |i| = {op['i_peak_A']:.4f} A, "
              f"Pdc = {op['Pdc_W']:.3f} W, voltage margin = {op['voltage']['remaining_command_margin_V']:.4g} V")


def cmd_forward(args):
    _print_json(S.forward(_drive(args), sf.synthetic_limits(), args.n, args.vdc, args.id, args.iq))


def cmd_capability(args):
    r = S.capability(_drive(args), sf.synthetic_limits(), args.n, args.vdc, args.direction, args.kind)
    if args.json:
        return _print_json(r)
    print(f"{args.kind} capability ({r['direction']}) at n = {args.n:g} rpm, Vdc = {args.vdc:g} V: "
          f"{r['achieved_value_Nm']} N*m; bound {r['certified_opposite_bound_Nm']}; certified={r['certified']}; "
          f"limited by {', '.join(r['active_constraints_at_witness'])}")
    for n in r["notes"]:
        print("  note:", n)


def cmd_curve(args):
    r = S.capability_curve(_drive(args), sf.synthetic_limits(), args.vdc)
    if args.json:
        return _print_json(r)
    print(f"{'n[rpm]':>8} {'policy max':>11} {'elec max':>10} {'policy min':>11} {'elec min':>10}  limited by (max)")
    for row in r["rows"]:
        f = lambda x: "      -" if x is None else f"{x:10.3f}"
        print(f"{row['speed_rpm']:8.0f} {f(row['policy_max_Nm']):>11} {f(row['electrical_max_Nm']):>10} "
              f"{f(row['policy_min_Nm']):>11} {f(row['electrical_min_Nm']):>10}  {', '.join(row['policy_max_limited_by'])}")


def cmd_exchange(args):
    from .exchange import build_package
    pkg = build_package(include_examples=not args.no_examples)
    out = Path(args.out)
    out.write_text(json.dumps(_jsonable(pkg), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"exchange package ({pkg['schema']}, {len(pkg['fixtures'])} fixtures) written to {out}")
    return 0


def cmd_acceptance(args):
    a = S.acceptance_summary()
    if args.json:
        _print_json(a)
    else:
        for r in a["rows"]:
            v = "-" if r["value"] is None else f"{r['value']:.3e}"
            tol = "-" if r["tolerance"] is None else f"{r['tolerance']:.1e}"
            print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['group']:<10} {r['case']:<40} {r['metric']:<28} {v:>10} <= {tol}")
        print(f"\nmanifest {'OK' if a['manifest_ok'] else 'MISMATCH'}; {sum(r['pass'] for r in a['rows'])}/{len(a['rows'])} pass")
        print(a["scope"])
    return 0 if (a["all_pass"] and a["manifest_ok"]) else 1


def _project(path):
    from .project import builtin_project, load_project
    return builtin_project() if not path else load_project(path)


def cmd_project(args):
    from .project import check_project, diff_projects, save_project, short
    if args.action == "export":
        if not args.files:
            raise InputValidationError("project export needs the output file", field="files")
        out = save_project(_project(None), args.files[0])
        print(f"built-in project written to {out}")
        return 0
    if args.action == "diff":
        if len(args.files) != 2:
            raise InputValidationError("project diff needs two project files", field="files")
        d = diff_projects(_project(args.files[0]), _project(args.files[1]))
        if args.json:
            _print_json(d)
            return 0
        f, t = d["from"], d["to"]
        print(f"{f['project_id']} rev {f['revision']} -> {t['project_id']} rev {t['revision']}")
        for name, c in d["changed_sections"].items():
            print(f"  {name}: {c['change']}" + (f" ({c.get('n_paths', 0)} path(s))" if c.get("paths") else ""))
            for p in c.get("paths", [])[:12]:
                print(f"      {p['path']}: {p['from']} -> {p['to']}")
        print("affected analyses: " + (", ".join(f"{k} ({', '.join(v['sections'])})"
                                                 for k, v in d["affected_analyses"].items()) or "none"))
        print("unaffected: " + (", ".join(d["unaffected_analyses"]) or "none"))
        return 0
    prj = _project(args.files[0] if args.files else None)
    if args.action == "show":
        ident = prj.identity()
        if args.json:
            _print_json({**ident, "title": prj.title, "origin": prj.origin, "note": prj.note,
                         "change_log": list(prj.change_log),
                         "provenance": {n: s.provenance for n, s in prj.sections.items()}})
            return 0
        print(f"{prj.label}: {prj.title}")
        print(f"origin {prj.origin}; project digest {short(ident['project_digest'])}")
        for n, s in prj.sections.items():
            pv = s.provenance
            print(f"  {n:<13} {short(s.digest)}  {pv.get('origin', '?'):<10} "
                  f"{'qualified' if pv.get('qualified') else 'not qualified'}  {pv.get('source', '')}")
        return 0
    res = check_project(prj)
    if args.json:
        _print_json(res)
    else:
        for f in res["findings"]:
            print(f"{f['status']:<13} {f['rule']:<7} {f['title']}: {f['detail']}")
        print(f"\n{prj.label}: {res['status']} " + ", ".join(f"{k} {v}" for k, v in res["counts"].items() if v))
    return {"OK": 0, "WARNING": 1}.get(res["status"], 2)


def cmd_reqset(args):
    import copy
    from . import requirement_set as RS
    from .io import drive_from_dict
    if args.template:
        Path(args.template).write_text(RS.CSV_TEMPLATE, encoding="utf-8")
        print(f"requirement CSV template written to {args.template}")
        return 0
    if not args.csv:
        raise InputValidationError("reqset needs the requirement CSV (or --template OUT.csv)", field="csv")
    prj = _project(args.project)
    drive = drive_from_dict(copy.deepcopy(prj.data("drive")))
    lim = prj.dc_limits()
    reqs = RS.parse_requirements_csv(Path(args.csv).read_text(encoding="utf-8-sig"), drive.motor.pole_pairs)
    cands = RS.parse_candidates(Path(args.candidates).read_text(encoding="utf-8")) if args.candidates else []
    res = RS.evaluate_set(reqs, drive, lim, with_capability=not args.fast)
    cres = RS.evaluate_candidates(reqs, drive, lim, cands, baseline=res) if cands else None
    if args.out:
        Path(args.out).write_text(RS.rows_to_csv(res["rows"]), encoding="utf-8")
    if args.json:
        _print_json({"project": prj.label, "drive": res["drive"], "summary": res["summary"], "rows": res["rows"],
                     "priorities": res["priorities"], "candidates": cres, "note": res["note"]})
    else:
        sm = res["summary"]
        print(f"{prj.label} - {res['drive']['drive_id']} ({res['drive']['origin']}, {res['drive']['fidelity']}): "
              f"{sm['total']} requirement(s), PASS {sm['PASS']}, FAIL {sm['FAIL']}, UNKNOWN {sm['UNKNOWN']}")
        for r in res["rows"]:
            m = "" if r["margin_Nm"] is None else f" margin {r['margin_Nm']:.4g} N*m"
            print(f"  {r['id']:<12} {r['verdict']:<8} {r['class_label_en']}{m}")
            if r["verdict"] != "PASS":
                print(f"      limiting: {r['limiting'] or '-'}")
                for x in r["next_data"][:2]:
                    print(f"      next: {x}")
        for e in res["priorities"]:
            print(f"  next data [{e['effort']}] {e['label_en']}: {', '.join(e['requirements'])}")
        for c in (cres or {}).get("candidates", []):
            print(f"  candidate {c['name']}: improves {', '.join(c['improves']) or '-'}; worsens "
                  f"{', '.join(c['worsens']) or '-'}; all met: {'yes' if c['all_met'] else 'no'}"
                  + (" [diagnostic]" if c["diagnostic_only"] else ""))
        print(res["note"])
    if not args.exit_code:
        return 0
    v = {r["verdict"] for r in res["rows"]}
    return 2 if "FAIL" in v else 3 if "UNKNOWN" in v else 0


def cmd_datasheet(args):
    """Import a datasheet spec (module curves or representative values, capacitor, gate dv/dt, motor) into a project:
    findings, the new section's digest and - with --out - the modified project file (a new revision stays the user's
    decision).  The desktop value-entry dialog saves the same specs."""
    from . import datasheet as DS
    from .project import save_project, short
    spec, base_dir = DS.load_spec(args.spec)
    prj = _project(args.project)
    new, res = DS.apply(prj, spec, base_dir)
    if args.json:
        _print_json({"section": res["section"], "digest": new.sections[res["section"]].digest,
                     "provenance": res["provenance"], "findings": res["findings"], "project": new.identity()})
    else:
        print(f"{res['section']}: {res['provenance']['source']} -> digest {short(new.sections[res['section']].digest)}")
        for f in res["findings"]:
            print(f"  {f['level']:<8} {f['item']}: {f['detail']}")
    if args.out:
        if args.revision:
            new = new.as_revision(args.revision, args.change or f"datasheet import: {res['provenance']['source']}")
        print(f"project written to {save_project(new, args.out)} ({new.label})")
    return 0


def _mw_status(v: dict) -> None:
    print(f"  package check       {v['package_check']}")
    print(f"  target environment  {v['target_environment']}")
    print(f"  model generation    {v['model_generation']} (Simulink static evaluation harness)")
    print(f"  parity              {v['parity']}" + (f"  {v['cases']}" if v.get("cases") else ""))
    for k, st in (v.get("stages") or {}).items():
        print(f"    stage {k:<20} {st}")
    print(f"  physical validation {v['physical_validation']}")
    print(f"  current evidence    {'yes' if v['linked_as_current_evidence'] else 'no'}")
    for pr in v["package_problems"] + v["problems"]:
        print(f"  ! {pr}")
    for f in v.get("failed_cases", [])[:20]:
        print(f"  FAIL {f['case_id']}: {', '.join(f['failures'][:6])}")


def cmd_mathworks(args):
    """MathWorks transfer package: export (Python reference -> package), check (integrity), run (local MATLAB /
    GNU Octave), verify (re-import a target report against this package and, with --project, this design)."""
    from . import mathworks as MW
    if args.action == "export":
        man = MW.export_package(args.dir, _project(args.project), force=args.force)
        if args.json:
            _print_json({k: man[k] for k in ("dir", "semantic_fingerprint", "case_counts", "models",
                                             "oracle_disagreements", "statuses")})
        else:
            print(f"{MW.SCHEMA} package written to {man['dir']}")
            print(f"  fingerprint {man['semantic_fingerprint'][:16]}  project {man['project_label']}")
            print(f"  models {', '.join(man['models'])}")
            print(f"  cases {man['case_counts']}  layer-2 vs layer-1 disagreements: "
                  f"{man['oracle_disagreements'] or 'none'}")
            print(f"  run on the target: {man['entry_points']['matlab']}")
        return 0
    if args.action == "check":
        r = MW.check_package(args.dir)
        if args.json:
            _print_json({k: v for k, v in r.items() if k != "manifest"})
        else:
            print(f"package check {r['status']}")
            for pr in r["problems"] + r["warnings"]:
                print(f"  ! {pr}")
        return 0 if r["status"] == "PASS" else 1
    if args.action == "run":
        r = MW.run_local(args.dir, args.runtime)
        v = r["verification"]
        if args.json:
            _print_json(r)
        else:
            print(f"{r['runtime']['kind']} ({r['runtime']['path']}) exit {r['exit_code']}, log {r['log']}")
            _mw_status(v)
        return 0 if v["parity"] == "PASS" else 1
    v = MW.verify_report(args.dir, args.report, _project(args.project) if args.project else None)
    if args.json:
        _print_json(v)
    else:
        _mw_status(v)
    return 0 if v["parity"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="twb", description="Traction engineering feasibility workbench")
    ap.add_argument("--version", action="version", version=f"traction-workbench {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, json_flag=True):
        p.add_argument("--drive", default="SYNTH_IPMSM_200KW_REF_V1", help="builtin drive id")
        if json_flag:
            p.add_argument("--json", action="store_true", help="print JSON")
        return p

    p = sub.add_parser("gui", help="start the desktop application")
    p.add_argument("--open", help="case JSON to evaluate, or a workspace (*.twb-workspace.json) to open, at start")
    p.add_argument("--lang", choices=("ko", "en"))
    p.set_defaults(fn=cmd_gui)
    p = sub.add_parser("report", help="PDF engineering report (graphs + decision record) for a case file")
    p.add_argument("case")
    p.add_argument("--pdf", help="output PDF path (default: <record id>.pdf)")
    p.add_argument("--lang", default="ko", choices=("ko", "en"))
    p.add_argument("--no-envelope", action="store_true", help="skip the T-n envelope page (faster)")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("exchange", help="MathWorks-port exchange package: conventions, identities, fixtures (JSON)")
    p.add_argument("out", nargs="?", default="twb_exchange.json")
    p.add_argument("--no-examples", action="store_true", help="omit the example inputs")
    p.set_defaults(fn=cmd_exchange)
    p = sub.add_parser("selftest", help="headless check of the desktop application")
    p.add_argument("out", nargs="?", default="selftest_out")
    p.add_argument("--lang", choices=("ko", "en"), help="the language of the pages (default: the saved setting)")
    p.set_defaults(fn=cmd_selftest)
    p = sub.add_parser("evaluate", help="evaluate a case file into a decision record")
    p.add_argument("case")
    p.add_argument("--out", help="directory for the JSON + Markdown record")
    p.add_argument("--json", action="store_true")
    p.add_argument("--exit-code", action="store_true", help="exit 0 PASS / 2 FAIL / 3 UNKNOWN")
    p.set_defaults(fn=cmd_evaluate)
    p = sub.add_parser("demo", help="representative synthetic engineering questions")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_demo)
    p = common(sub.add_parser("solve", help="minimum-current policy at one request"))
    p.add_argument("--n", type=float, required=True, help="mechanical speed [rpm]")
    p.add_argument("--vdc", type=float, required=True, help="DC terminal voltage [V]")
    p.add_argument("--torque", type=float, required=True, help="shaft torque [N*m]")
    p.set_defaults(fn=cmd_solve)
    p = common(sub.add_parser("forward", help="forward evaluation of a given id/iq"), json_flag=False)
    p.add_argument("--n", type=float, required=True)
    p.add_argument("--vdc", type=float, required=True)
    p.add_argument("--id", type=float, required=True)
    p.add_argument("--iq", type=float, required=True)
    p.set_defaults(fn=cmd_forward)
    p = common(sub.add_parser("capability", help="shaft-torque capability at one speed"))
    p.add_argument("--n", type=float, required=True)
    p.add_argument("--vdc", type=float, required=True)
    p.add_argument("--direction", type=int, default=1, choices=(1, -1))
    p.add_argument("--kind", default="policy", choices=("policy", "physical", "electrical"))
    p.set_defaults(fn=cmd_capability)
    p = common(sub.add_parser("curve", help="torque-speed envelope"))
    p.add_argument("--vdc", type=float, required=True)
    p.set_defaults(fn=cmd_curve)
    p = sub.add_parser("project", help="project data package: show, check, diff, export")
    p.add_argument("action", choices=("show", "check", "diff", "export"))
    p.add_argument("files", nargs="*", help="project file(s); none = the built-in synthetic project")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_project)
    p = sub.add_parser("reqset", help="requirement set (CSV) on one product; candidates against every requirement")
    p.add_argument("csv", nargs="?", help="requirement CSV (columns: see --template)")
    p.add_argument("--project", help="project file (default: the built-in synthetic project)")
    p.add_argument("--candidates", help="text file, one candidate per line: 'name: parameter=value, ...'")
    p.add_argument("--out", help="write the result rows as CSV")
    p.add_argument("--template", help="write the requirement CSV template and exit")
    p.add_argument("--fast", action="store_true", help="skip the capability margins (verdicts only)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--exit-code", action="store_true", help="exit 0 all PASS / 2 any FAIL / 3 otherwise UNKNOWN")
    p.set_defaults(fn=cmd_reqset)
    p = sub.add_parser("datasheet", help="import a datasheet spec (module curves or values, capacitor, dv/dt, motor) "
                                         "into a project")
    p.add_argument("spec", help="datasheet spec JSON (kind: module | capacitor | gate_edges | motor); CSV paths "
                                "relative to it")
    p.add_argument("--project", help="project file to import into (default: the built-in synthetic project)")
    p.add_argument("--out", help="write the modified project here")
    p.add_argument("--revision", help="make the result a new revision with this name (needs --out)")
    p.add_argument("--change", help="change note of that revision")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_datasheet)
    p = sub.add_parser("mathworks", help="MathWorks transfer package: export | check | run | verify")
    p.add_argument("action", choices=("export", "check", "run", "verify"))
    p.add_argument("dir", help="package directory")
    p.add_argument("--project", help="project file (export: the design to transfer; verify: the CURRENT design "
                                     "the report must belong to); default: the built-in synthetic project")
    p.add_argument("--report", help="verify: report file (default <dir>/results/parity_report.json)")
    p.add_argument("--runtime", choices=("matlab", "octave"), help="run: which local runtime (default: MATLAB, "
                                                                     "else GNU Octave)")
    p.add_argument("--force", action="store_true", help="export: write even over edited generated files")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_mathworks)
    p = sub.add_parser("acceptance", help="compare with the golden fixtures")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_acceptance)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rc = args.fn(args)
    except InputValidationError as exc:
        print(f"INVALID_INPUT: {exc}", file=sys.stderr)
        return 4
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
