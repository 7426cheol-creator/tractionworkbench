"""Command-line interface: ``twb <command>`` (or ``python -m traction_workbench``).

    twb serve [--port 8765] [--open]      local web UI
    twb evaluate CASE.json [--out DIR]    decision record (JSON + Markdown)
    twb demo [--out DIR]                  representative engineering questions on the synthetic drive
    twb solve --n 12000 --torque 150 --vdc 600
    twb forward --n 3000 --vdc 600 --id -200 --iq 400
    twb capability --n 12000 --vdc 600 [--direction -1] [--kind policy|physical|electrical]
    twb curve --vdc 600
    twb acceptance                        production output vs the golden fixtures
    twb export-static --out site.html     self-contained UI snapshot
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import service as S
from . import spec_fixtures as sf
from .decision import _jsonable
from .errors import InputValidationError


def _drive(args):
    return S.resolve_drive({"builtin": args.drive})


def _print_json(obj):
    print(json.dumps(_jsonable(obj), indent=2, ensure_ascii=False))


def cmd_serve(args):
    from .web.server import serve
    serve(args.host, args.port, args.open, args.verbose)


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
    from .web.api import PRESETS
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


def cmd_export_static(args):
    from .web.export import build_static_html
    p = build_static_html(Path(args.out), progress=lambda m: print(m, file=sys.stderr))
    print(f"static UI written to {p} ({p.stat().st_size // 1024} KB)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="twb", description="Traction engineering feasibility workbench")
    ap.add_argument("--version", action="version", version=f"traction-workbench {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, json_flag=True):
        p.add_argument("--drive", default="SYNTH_IPMSM_200KW_REF_V1", help="builtin drive id")
        if json_flag:
            p.add_argument("--json", action="store_true", help="print JSON")
        return p

    p = sub.add_parser("serve", help="run the local web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open a browser")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_serve)
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
    p = sub.add_parser("acceptance", help="compare with the golden fixtures")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_acceptance)
    p = sub.add_parser("export-static", help="write a self-contained HTML snapshot of the UI")
    p.add_argument("--out", default="out/traction_workbench.html")
    p.set_defaults(fn=cmd_export_static)
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
