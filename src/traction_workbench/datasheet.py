"""Datasheet import - the datasheet tier of roadmap P1-A/B (typical / max datasheet values; no measured data).

Digitized datasheet curves (points per curve and junction temperature, e.g. a WebPlotDigitizer export) and
tabulated datasheet values become PROJECT SECTIONS with units, provenance and findings:

* ``module_section``    - switching energies and on-state curves of a power module, its test conditions and (when
  the datasheet gives it) the junction-to-fluid Foster network of a direct-cooled module;
* ``capacitor_section`` - the DC-link capacitor: C, ESL, ESR(f), thermal resistance, useful-life table;
* ``gate_edges``        - the switch-node VOLTAGE edges of the EMI source from the datasheet dv/dt.

Rules (the same discipline as the analyses):

* nothing is extrapolated - a curve is resampled on the currents EVERY temperature was digitized over; a range only
  some temperatures cover is dropped and reported, so the engine answers UNKNOWN there instead of an estimate;
* nothing is invented - a zero-current point is added to an energy curve only when the import declares it
  (E(0) = 0, recorded as an assumption); a missing curve stays missing; current rise / fall times (tr, tf are
  CURRENT transitions per IEC 60747-9) are never used as voltage edges;
* each section is validated by the parser of its analyses (``project.SECTIONS``) before it can enter a project;
* the provenance says what it is: origin "supplier", the document and revision, typical or max values, the
  digitisation method, qualified = False (a datasheet is not a validation of this product's operating point).

The import is a pure function of its spec (JSON-like; CSV text inline or as files next to the spec), so the same
spec gives the same section and digest - the desktop dialog and ``twb datasheet`` call the same functions.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import math
import re
from pathlib import Path

import numpy as np

from .errors import InputValidationError

CURVES = {"v_on": "V", "v_rev": "V", "e_on": "J", "e_off": "J", "e_rr": "J", "v_channel_rev": "V"}
REQUIRED_CURVES = ("v_on", "v_rev", "e_on", "e_off")
ENERGY_UNITS = ("J", "mJ", "uJ")
VALUE_KINDS = ("typical", "max")


class Finding(dict):
    """{"level": ERROR | WARNING | NOTE, "item", "detail"} - ERROR blocks the section, the others are reported."""

    def __init__(self, level: str, item: str, detail: str):
        super().__init__(level=level, item=item, detail=detail)


# -- points ---------------------------------------------------------------------------------------------------

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _temperature_of(name: str) -> float:
    m = _NUM.search(name or "")
    if not m:
        raise InputValidationError(f"dataset {name!r}: no junction temperature in its name (e.g. '25C', 'Tj=150')",
                                   field="csv")
    return float(m.group())


def parse_points_csv(text: str, curve: str | None = None) -> dict:
    """CSV -> {curve: {T_C: [(I_A, value), ...]}}.

    Long format (one file for several curves): a header with ``curve``, ``T_C`` (or ``Tj_C``), ``I_A`` and ``value``.
    WebPlotDigitizer "all datasets" format (one file per curve, ``curve`` given): a first row of dataset names that
    carry the junction temperature ('25C', 'Tj=150 °C'), a second row ``X, Y, X, Y, ...``, then the points."""
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("\ufeff"))) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise InputValidationError("the CSV holds no points", field="csv")
    head = [c.strip().lower() for c in rows[0]]
    out: dict = {}
    if "value" in head and ("i_a" in head or "current_a" in head):
        ic = head.index("i_a") if "i_a" in head else head.index("current_a")
        tc = next((head.index(k) for k in ("t_c", "tj_c", "temperature_c") if k in head), None)
        cc = head.index("curve") if "curve" in head else None
        vc = head.index("value")
        if tc is None or (cc is None and curve is None):
            raise InputValidationError("long-format CSV needs T_C and (unless the curve is given) curve columns",
                                       field="csv")
        for n, r in enumerate(rows[1:], start=2):
            try:
                key = r[cc].strip() if cc is not None else curve
                T, i, v = float(r[tc]), float(r[ic]), float(r[vc])
            except (ValueError, IndexError):
                raise InputValidationError(f"CSV line {n}: not a number", field="csv") from None
            out.setdefault(key, {}).setdefault(T, []).append((i, v))
        return out
    if curve is None:
        raise InputValidationError("a WebPlotDigitizer export holds one curve: name it (e.g. e_on)", field="csv")
    sub = [c.strip().upper() for c in rows[1]]
    if not sub or any(s not in ("X", "Y") for s in sub if s):
        raise InputValidationError("unknown CSV layout: use the long format (curve, T_C, I_A, value) or a "
                                   "WebPlotDigitizer export (dataset names, then X, Y columns)", field="csv")
    for j in range(0, len(sub) - 1, 2):
        name = rows[0][j] if j < len(rows[0]) else ""
        T = _temperature_of(name)
        pts = out.setdefault(curve, {}).setdefault(T, [])
        for n, r in enumerate(rows[2:], start=3):
            if j + 1 >= len(r) or not r[j].strip() or not r[j + 1].strip():
                continue
            try:
                pts.append((float(r[j]), float(r[j + 1])))
            except ValueError:
                raise InputValidationError(f"CSV line {n}: not a number", field="csv") from None
    return out


# -- curves ---------------------------------------------------------------------------------------------------

def resample_curve(name: str, by_T: dict, unit: str, anchor_zero: bool = False, source: str = "") -> tuple:
    """Per-temperature digitized points -> the engine's grid curve {temps_C, currents_A, values, unit, source}.

    The grid is every digitized current inside the range ALL temperatures cover (each temperature interpolated
    linearly between its own points - the engine's own interpolation - never beyond them)."""
    findings = []
    if name not in CURVES:
        raise InputValidationError(f"unknown curve {name!r}; known: {sorted(CURVES)}", field=name)
    energy = CURVES[name] == "J"
    if (unit in ENERGY_UNITS) != energy or (not energy and unit != "V"):
        raise InputValidationError(f"curve {name}: unit {unit!r} is not a {'energy' if energy else 'voltage'} unit",
                                   field=f"{name}.unit")
    if not by_T:
        raise InputValidationError(f"curve {name}: no points", field=name)
    temps = sorted(float(T) for T in by_T)
    series = {}
    for T in temps:
        pts = sorted((float(i), float(v)) for i, v in by_T[T] if math.isfinite(float(i)) and math.isfinite(float(v)))
        if len(pts) < 2:
            raise InputValidationError(f"curve {name} at {T:g} degC: at least two points are needed", field=name)
        cur = np.array([p[0] for p in pts])
        val = np.array([p[1] for p in pts])
        dup = np.where(np.diff(cur) <= 0)[0]
        if dup.size:
            same = [k for k in dup if abs(val[k + 1] - val[k]) <= 1e-12 * max(1.0, abs(val[k]))]
            if len(same) != dup.size:
                raise InputValidationError(f"curve {name} at {T:g} degC: two different values at {cur[dup[0]]:g} A "
                                           "(re-digitize or remove one)", field=name)
            keep = np.ones(cur.size, bool)
            keep[np.array(same) + 1] = False
            cur, val = cur[keep], val[keep]
            findings.append(Finding("NOTE", f"{name} {T:g} degC", f"{len(same)} repeated point(s) removed"))
        if cur[0] < 0:
            raise InputValidationError(f"curve {name} at {T:g} degC: negative current", field=name)
        if np.any(val < 0):
            raise InputValidationError(f"curve {name} at {T:g} degC: negative {('energy' if energy else 'voltage')}",
                                       field=name)
        if np.any(np.diff(val) < -1e-9 * max(1.0, float(np.max(np.abs(val))))):
            findings.append(Finding("WARNING", f"{name} {T:g} degC",
                                    "the digitized curve decreases somewhere with current (digitisation noise or a "
                                    "real non-monotone curve): check the points; the engine interpolates as given"))
        series[T] = (cur, val)
    lo = max(s[0][0] for s in series.values())
    hi = min(s[0][-1] for s in series.values())
    if hi <= lo:
        raise InputValidationError(f"curve {name}: the temperatures share no current range ({lo:g}..{hi:g} A)",
                                   field=name)
    for T, (cur, _v) in series.items():
        if cur[0] < lo - 1e-9 or cur[-1] > hi + 1e-9:
            findings.append(Finding("NOTE", f"{name} {T:g} degC",
                                    f"digitized over {cur[0]:g}..{cur[-1]:g} A; the curve keeps only the range every "
                                    f"temperature covers ({lo:g}..{hi:g} A) - no extrapolation"))
    grid = sorted({round(float(c), 9) for s in series.values() for c in s[0] if lo - 1e-9 <= c <= hi + 1e-9})
    grid = [g for g in grid if lo - 1e-9 <= g <= hi + 1e-9]
    values = [[float(np.interp(g, *series[T])) for g in grid] for T in temps]
    if anchor_zero:
        if not energy:
            raise InputValidationError(f"curve {name}: the zero-current anchor is only for switching energies",
                                       field=f"{name}.anchor_zero")
        if grid[0] > 0:
            grid = [0.0] + grid
            values = [[0.0] + row for row in values]
            findings.append(Finding("NOTE", name, f"declared anchor E(0) = 0 below the first digitized current "
                                                  f"({lo:g} A): an assumption of this import, not datasheet data"))
    elif grid[0] > 0:
        findings.append(Finding("NOTE", name, f"the curve starts at {grid[0]:g} A: below it the losses are not given "
                                              "(the engine reports UNKNOWN there; declare E(0) = 0 for energies if "
                                              "that is the program's assumption)"))
    if len(temps) == 1:
        findings.append(Finding("WARNING", name, f"one junction temperature only ({temps[0]:g} degC): the curve "
                                                 "is valid at that temperature only"))
    curve = {"temps_C": temps, "currents_A": grid, "values": values, "unit": unit,
             "source": source or "datasheet (digitized)"}
    return curve, findings


# -- specs ----------------------------------------------------------------------------------------------------

def _points_of(spec: dict, name: str, base_dir: Path | None) -> dict:
    """Points of one curve from the spec: inline ``points`` [[T_C, I_A, value], ...], ``csv`` text or ``file``."""
    if spec.get("points") is not None:
        by_T: dict = {}
        for n, p in enumerate(spec["points"]):
            if len(p) != 3:
                raise InputValidationError(f"{name}.points[{n}] must be [T_C, I_A, value]", field=name)
            by_T.setdefault(float(p[0]), []).append((float(p[1]), float(p[2])))
        return by_T
    text = spec.get("csv")
    if text is None and spec.get("file"):
        path = Path(spec["file"])
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InputValidationError(f"{name}: cannot read {path} ({exc})", field=f"{name}.file") from None
    if text is None:
        raise InputValidationError(f"curve {name}: give points, csv or file", field=name)
    got = parse_points_csv(text, name)
    if name not in got:
        raise InputValidationError(f"curve {name} not found in its CSV (curves there: {sorted(got)})", field=name)
    return got[name]


def _document(part: dict) -> str:
    bits = [str(part.get(k) or "").strip() for k in ("manufacturer", "part_number")]
    doc = " ".join(b for b in bits if b) or "datasheet"
    rev = str(part.get("revision") or "").strip()
    return doc + (f" datasheet rev {rev}" if rev else " datasheet") + (
        f" ({part['document']})" if part.get("document") else "")


def _provenance(part: dict, value_kind: str, method: str, extra: str = "") -> dict:
    if not str(part.get("part_number") or "").strip():
        raise InputValidationError("the datasheet part number is required (provenance)", field="part.part_number")
    if not str(part.get("revision") or "").strip():
        raise InputValidationError("the datasheet revision is required (provenance)", field="part.revision")
    return {"origin": "supplier", "source": _document(part), "revision": str(part.get("revision")),
            "qualified": False,
            "evidence": f"{value_kind} datasheet values; {method}" + (f"; {extra}" if extra else "")
                        + " - not a validation at this product's operating points"}


def module_section(spec: dict, base: dict | None = None, base_dir: Path | None = None) -> dict:
    """Datasheet spec -> {"data": module section, "provenance", "findings"}.

    ``base`` (the current project module) supplies only what is not device data and not in the spec - parallel
    count, driver auxiliary loss, the evaluation temperature and the thermal path - each named in the findings."""
    from .project import SECTIONS
    base = copy.deepcopy(base or {})
    part = spec.get("part") or {}
    value_kind = spec.get("value_kind", "typical")
    if value_kind not in VALUE_KINDS:
        raise InputValidationError(f"value_kind must be one of {VALUE_KINDS}", field="value_kind")
    tech = spec.get("technology", "IGBT")
    if tech not in ("IGBT", "SiC_MOSFET"):
        raise InputValidationError("technology must be IGBT or SiC_MOSFET", field="technology")
    findings = []
    curves = {}
    method = str(spec.get("digitization") or "digitized from the datasheet figures")
    for name, cs in (spec.get("curves") or {}).items():
        unit = cs.get("unit") or ("mJ" if CURVES.get(name) == "J" else "V")
        cv, f = resample_curve(name, _points_of(cs, name, base_dir), unit,
                               anchor_zero=bool(cs.get("anchor_zero", spec.get("anchor_zero_energy", False)))
                               and CURVES.get(name) == "J",
                               source=f"{_document(part)}, {cs.get('figure') or 'figure not named'}")
        curves[name] = cv
        findings += f
    missing = [c for c in REQUIRED_CURVES if c not in curves]
    if missing:
        raise InputValidationError(f"required curve(s) missing: {missing} (the loss model needs v_on, v_rev, e_on, "
                                   "e_off; e_rr when the diode recovery is not inside e_on)", field="curves")
    if "e_rr" not in curves and tech == "IGBT":
        findings.append(Finding("WARNING", "e_rr", "no reverse-recovery energy: the diode switching loss is 0 in the "
                                                   "model unless E_on already contains it (state the energy basis)"))
    tc = dict(spec.get("test_conditions") or {})
    if "v_test_V" not in spec:
        raise InputValidationError("v_test_V (the switching test voltage of the energies) is required",
                                   field="v_test_V")
    for k in ("Rg_on_ohm", "Rg_off_ohm", "Vge_V"):
        if k not in tc:
            findings.append(Finding("WARNING", f"test_conditions.{k}", "not given: the gate-drive comparison with "
                                                                         "the controller (PRJ-03) stays NOT_CHECKED"))
    data = {"name": str(spec.get("name") or _document(part)), "technology": tech, "value_kind": value_kind,
            "energy_basis": spec.get("energy_basis", "per_device"), "v_test_V": float(spec["v_test_V"]),
            "source": _document(part), "test_conditions": tc, "curves": curves}
    if spec.get("vdc_scaling"):
        data["vdc_scaling"] = copy.deepcopy(spec["vdc_scaling"])
    for key, default in (("parallel", 1), ("sharing_error_pct", 0.0), ("driver_aux_W", 0.0), ("aux_from_hv_dc", False),
                         ("Tj_eval_C", 150.0)):
        if key in spec:
            data[key] = spec[key]
        elif key in base:
            data[key] = base[key]
            findings.append(Finding("NOTE", key, f"kept from the project ({base[key]!r}): not datasheet data"))
        else:
            data[key] = default
            findings.append(Finding("NOTE", key, f"default {default!r}: not datasheet data - declare it"))
    zth = spec.get("zth_foster")
    if zth:
        r = [float(x) for x in zth["R_K_per_W"]]
        tau = [float(x) for x in zth["tau_s"]]
        extra = float(zth.get("extra_Rth_K_per_W") or 0.0)
        ref = str(zth.get("reference") or "").strip()
        if not ref:
            raise InputValidationError("zth_foster.reference is required (junction-to-case or junction-to-fluid "
                                       "and at which coolant flow)", field="zth_foster.reference")
        if extra:
            r = r + [extra]
            tau = tau + [float(zth.get("extra_tau_s") or 30.0)]
            findings.append(Finding("NOTE", "thermal_path", f"declared case-to-coolant resistance {extra:g} K/W added "
                                                            "as one Foster stage (its time constant is declared)"))
        data["thermal_path"] = {"Rth_K_per_W": float(sum(r)), "T_ref_C": zth.get("T_ref_C", 65.0),
                                "foster": {"R_K_per_W": r, "tau_s": tau},
                                "basis": f"{_document(part)} Zth ({ref})" + (" + declared case-to-coolant R" if extra
                                                                             else "")}
    elif base.get("thermal_path"):
        data["thermal_path"] = base["thermal_path"]
        findings.append(Finding("NOTE", "thermal_path", "kept from the project: the datasheet gives no junction-to-"
                                                        "coolant path for this cooling system"))
    else:
        raise InputValidationError("no thermal path: give zth_foster or import into a project that has one",
                                   field="zth_foster")
    try:
        SECTIONS["module"].validate(data)
    except InputValidationError as exc:
        raise InputValidationError(f"the imported module is not valid for the analyses: {exc}", field="module") from None
    prov = _provenance(part, value_kind, method)
    return {"data": data, "provenance": prov, "findings": findings}


def capacitor_section(spec: dict, base: dict | None = None, base_dir: Path | None = None) -> dict:
    """Capacitor datasheet spec -> {"data": dc_link section, "provenance", "findings"}."""
    from .project import SECTIONS
    base = copy.deepcopy(base or {})
    part = spec.get("part") or {}
    findings = []
    for k in ("C_uF", "ESR_table"):
        if k not in spec:
            raise InputValidationError(f"{k} is required", field=k)
    esr = spec["ESR_table"]
    if isinstance(esr, dict):                       # {"csv": ...} / {"file": ...}: columns f_Hz, ESR
        text = esr.get("csv")
        if text is None:
            path = Path(esr["file"])
            text = (base_dir / path if base_dir and not path.is_absolute() else path).read_text(encoding="utf-8")
        esr = []
        for n, r in enumerate(csv.reader(io.StringIO(text.lstrip("\ufeff"))), start=1):
            if len(r) < 2 or not r[0].strip():
                continue
            try:
                esr.append([float(r[0]), float(r[1])])
            except ValueError:
                if esr:                                   # only a header line may be non-numeric
                    raise InputValidationError(f"ESR CSV line {n}: not a number", field="ESR_table") from None
    esr = sorted([float(f), float(v)] for f, v in esr)
    if len(esr) < 2:
        raise InputValidationError("ESR(f) needs at least two points", field="ESR_table")
    data = {"C_uF": float(spec["C_uF"]), "ESR_table": esr, "ESR_unit": spec.get("ESR_unit", "mohm"),
            "source": _document(part)}
    for k in ("ESL_nH", "Rth_K_per_W", "T_ref_C", "T_valid_C", "ESR_temp_coeff_per_K", "ESR_table_T_C", "count",
              "symmetric_layout", "life_hours_table", "life_voltage_V", "life_basis", "ESR_hf_mohm", "Rth_basis"):
        if k in spec:
            data[k] = copy.deepcopy(spec[k])
        elif k in base and k in ("Rth_K_per_W", "T_ref_C", "count", "symmetric_layout", "Rth_basis"):
            data[k] = copy.deepcopy(base[k])
            findings.append(Finding("NOTE", k, f"kept from the project ({base[k]!r}): mounting / assembly data, not "
                                               "from the capacitor datasheet"))
    if "ESL_nH" not in data:
        findings.append(Finding("WARNING", "ESL_nH", "not given: the EMI network and the ripple resonance need it"))
    if not data.get("life_hours_table"):
        findings.append(Finding("NOTE", "life_hours_table", "no useful-life table: the capacitor life stays UNKNOWN"))
    if spec.get("rated_voltage_V"):
        data["rated_voltage_V"] = float(spec["rated_voltage_V"])
    if "ESR_hf_mohm" not in data:
        data["ESR_hf_mohm"] = float(esr[-1][1]) * (1e3 if data["ESR_unit"] == "ohm" else 1.0)
        findings.append(Finding("NOTE", "ESR_hf_mohm", f"EMI constant ESR taken as the highest-frequency datasheet "
                                                       f"value ({data['ESR_hf_mohm']:g} mOhm at {esr[-1][0]:g} Hz)"))
    try:
        SECTIONS["dc_link"].validate(data)
    except InputValidationError as exc:
        raise InputValidationError(f"the imported capacitor is not valid for the analyses: {exc}",
                                   field="dc_link") from None
    return {"data": data, "provenance": _provenance(part, spec.get("value_kind", "typical"), "tabulated values"),
            "findings": findings}


def gate_edges(spec: dict, controller: dict, Vdc_V: float) -> dict:
    """Datasheet switch-node dv/dt -> the EMI source's linear voltage edges t = Vdc / (dv/dt) at the project's
    nominal DC voltage.  Current rise / fall times are refused (a current transition is not the voltage edge)."""
    part = spec.get("part") or {}
    if any(k in spec for k in ("tr_ns", "tf_ns")) and not any(k in spec for k in ("dv_dt_on_V_per_ns",
                                                                                   "dv_dt_off_V_per_ns")):
        raise InputValidationError("tr / tf are CURRENT transition times (IEC 60747-9): the EMI source needs the "
                                   "switch-node voltage edge - give dv/dt (datasheet or double-pulse test)",
                                   field="dv_dt_on_V_per_ns")
    findings = []
    out = copy.deepcopy(controller)
    g = dict(out.get("gate") or {})
    cond = str(spec.get("condition") or "").strip()
    if not cond:
        raise InputValidationError("the dv/dt test condition is required (Vcc, Ic, Rg, Tj of the datasheet)",
                                   field="condition")
    rates = {}
    for key in ("dv_dt_on_V_per_ns", "dv_dt_off_V_per_ns"):
        if spec.get(key) is not None:
            dvdt = float(spec[key])
            if not (dvdt > 0 and math.isfinite(dvdt)):
                raise InputValidationError(f"{key} must be > 0", field=key)
            rates[key] = dvdt
    if not rates:
        raise InputValidationError("give dv_dt_on_V_per_ns and / or dv_dt_off_V_per_ns", field="dv_dt_on_V_per_ns")
    # which device switches - and so whether a turn-on or a turn-off dv/dt shapes a rising or a falling node edge -
    # depends on the current sign: both node edges take the faster datasheet slope (conservative for emission)
    fastest = max(rates.values())
    g["t_rise_ns"] = g["t_fall_ns"] = Vdc_V / fastest
    if len(rates) < 2:
        findings.append(Finding("WARNING", "gate edges", "only one dv/dt given: the other switching transition is "
                                                         "assumed not faster"))
    for k in ("Rg_on_ohm", "Rg_off_ohm"):
        if spec.get(k) is not None:
            g[k] = float(spec[k])
    g["basis"] = (f"{_document(part)} dv/dt at {cond}; linear edge t = Vdc / (dv/dt) at Vdc = {Vdc_V:g} V with the "
                  f"faster slope {fastest:g} V/ns on both node edges (edge direction depends on the current sign); "
                  "dv/dt taken as independent of voltage and current: other operating points unverified")
    findings.append(Finding("NOTE", "gate edges", "a datasheet dv/dt is a test-condition value: the EMI screening at "
                                                  "other currents / temperatures inherits that assumption"))
    out["gate"] = g
    from .project import SECTIONS
    SECTIONS["controller"].validate(out)
    return {"data": out, "provenance": _provenance(part, spec.get("value_kind", "typical"), "tabulated dv/dt"),
            "findings": findings}


# -- one entry point for the CLI and the desktop ----------------------------------------------------------------

KINDS = {"module": "module", "capacitor": "dc_link", "gate_edges": "controller"}


def load_spec(path) -> tuple[dict, Path]:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8")), p.parent
    except (OSError, json.JSONDecodeError) as exc:
        raise InputValidationError(f"cannot read the datasheet spec {p}: {exc}", field="spec") from None


def apply(project, spec: dict, base_dir: Path | None = None):
    """Import ``spec`` (``kind``: module | capacitor | gate_edges) into ``project`` -> (working copy, result).

    The result is {"section", "data", "provenance", "findings"}; the project comes back MODIFIED (a new revision is
    the user's decision), with the section's provenance naming the datasheet."""
    kind = spec.get("kind")
    if kind not in KINDS:
        raise InputValidationError(f"datasheet spec kind must be one of {sorted(KINDS)}", field="kind")
    section = KINDS[kind]
    if kind == "module":
        res = module_section(spec, project.data("module") if project.has("module") else None, base_dir)
    elif kind == "capacitor":
        res = capacitor_section(spec, project.data("dc_link") if project.has("dc_link") else None, base_dir)
    else:
        res = gate_edges(spec, project.data("controller"), float(project.data("dc_source")["Vdc_nominal_V"]))
    new = project.with_section(section, res["data"], res["provenance"])
    return new, {"section": section, **res}
