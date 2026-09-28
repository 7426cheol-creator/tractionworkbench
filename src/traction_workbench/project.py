"""Project data package (system review R2): the product data every analysis refers to, with identity.

A project describes ONE product (one drive system) once::

    schema     "twb-project/1"
    project    id, revision, title, origin, note, change_log
    sections   name -> {"data": {...}, "provenance": {"origin", "source", "revision", "qualified", "evidence"}}

Sections hold product data only - what is physically the same in every analysis (the module and its thermal path,
the DC-link capacitor, the controller's switching frequency / dead time / current loop, the gearbox, ...).
Scenarios, requirements and study assumptions stay with the analyses.  Each section is validated with the same
parser as a request body; each has a SHA-256 digest of its canonical content, so a result can name exactly the
product data it was computed from, and a revision change names exactly the analyses whose inputs changed
(``diff_projects``).  ``check_project`` states cross-section consistency: the same physical quantity must not have
two values, and relations between sections (a thermal path and its network, a torque path and the current loop it
approximates, a reducer and the torsional model of the same gearbox) are checked, not assumed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import parsers as P
from .errors import InputValidationError
from .io import drive_from_dict
from .modulation import MODULATIONS
from .scenario import DcSourceLimits

SCHEMA = "twb-project/1"
ORIGINS = ("synthetic", "estimated", "supplier", "measured", "mixed")
UNLIMITED = "unlimited"          # file token of a declared unlimited DC limit (math.inf in memory)
LIMIT_KEYS = ("discharge_power_max_W", "charge_power_max_W", "discharge_current_max_A", "charge_current_max_A")

# -- canonical content and digests ----------------------------------------------------------------------------


def _canon(x):
    if isinstance(x, dict):
        return {str(k): _canon(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_canon(v) for v in x]
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        v = float(x)
        return 0.0 if v == 0.0 else v      # 1 == 1.0 and -0.0 == 0.0 have one digest
    raise InputValidationError(f"project data must be JSON-like, got {type(x).__name__}", field="project")


def canonical_json(x) -> str:
    return json.dumps(_canon(x), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=True)


def digest(x) -> str:
    return hashlib.sha256(canonical_json(x).encode("utf-8")).hexdigest()


def short(d: str | None) -> str:
    return "-" if not d else d[:12]


# -- sections -------------------------------------------------------------------------------------------------


def _pos(d: dict, key: str, where: str, allow_zero: bool = False) -> float:
    v = d.get(key)
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise InputValidationError(f"{where}.{key} must be a number", field=f"{where}.{key}") from None
    if not math.isfinite(x) or x < 0 or (x == 0 and not allow_zero):
        raise InputValidationError(f"{where}.{key} must be {'>= 0' if allow_zero else '> 0'} and finite",
                                   field=f"{where}.{key}")
    return x


def _v_drive(d: dict):
    drv = drive_from_dict(copy.deepcopy(d))
    want = d.get("reference_sha256")
    if "builtin" in d and want and drv.provenance.sha256 != want:
        raise InputValidationError(f"the referenced built-in drive {d['builtin']!r} is not the one this project was "
                                   f"made with (sha256 {short(drv.provenance.sha256)} != {short(want)})",
                                   field="sections.drive.reference_sha256")


def _v_dc_source(d: dict):
    _pos(d, "Vdc_nominal_V", "dc_source")
    lim = limits_from(d.get("limits") or {})
    DcSourceLimits(*(lim[k] for k in LIMIT_KEYS))
    imp = d.get("impedance")
    if imp:
        _pos(imp, "R_mohm", "dc_source.impedance", True)
        _pos(imp, "L_uH", "dc_source.impedance", True)


def _thermal_path(tp: dict | None, where: str):
    if not tp:
        raise InputValidationError(f"{where}.thermal_path (junction-to-coolant Rth) is required", field=where)
    _pos(tp, "Rth_K_per_W", f"{where}.thermal_path")
    fo = tp.get("foster")
    if fo:
        r, t = list(fo.get("R_K_per_W") or []), list(fo.get("tau_s") or [])
        if not r or len(r) != len(t) or any(float(x) <= 0 for x in r + t):
            raise InputValidationError(f"{where}.thermal_path.foster needs R_K_per_W and tau_s of equal length, > 0",
                                       field=f"{where}.thermal_path.foster")


def _v_module(d: dict, where: str = "module"):
    P.module_model_from_dict({**d, "fsw_kHz": 10.0, "modulation": "svpwm", "deadtime_us": 0.0})
    tj = d.get("Tj_eval_C")
    if tj is None or not math.isfinite(float(tj)):
        raise InputValidationError(f"{where}.Tj_eval_C (evaluation junction temperature) is required",
                                   field=f"{where}.Tj_eval_C")
    _thermal_path(d.get("thermal_path"), where)


def _v_alternatives(d: dict):
    keys = set()
    for i, a in enumerate(d.get("modules") or []):
        k = str(a.get("key") or "")
        if not k or k in keys:
            raise InputValidationError("each alternative needs a unique key", field=f"alternatives.modules[{i}].key")
        keys.add(k)
        _v_module(a.get("module") or {}, f"alternatives.modules[{i}].module")
        _pos(a, "deadtime_us", f"alternatives.modules[{i}]", True)


def _v_dc_link(d: dict):
    P.capacitor_bank_from_dict({"capacitor": d, "source": None})
    if d.get("ESR_hf_mohm") is not None:
        _pos(d, "ESR_hf_mohm", "dc_link", True)


def _v_controller(d: dict):
    _pos(d, "fsw_kHz", "controller")
    if d.get("modulation") not in MODULATIONS:
        raise InputValidationError(f"controller.modulation must be one of {MODULATIONS}", field="controller.modulation")
    _pos(d, "deadtime_us", "controller", True)
    g = d.get("gate") or {}
    for k in ("t_rise_ns", "t_fall_ns"):
        if k in g:
            _pos(g, k, "controller.gate")
    from .extensions.emi import CARRIERS, MIN_PULSE_POLICIES
    if d.get("carrier", "asynchronous") not in CARRIERS:
        raise InputValidationError(f"controller.carrier must be one of {CARRIERS}", field="controller.carrier")
    pol = (d.get("timing") or {}).get("min_pulse_policy")
    if pol is not None and pol not in MIN_PULSE_POLICIES:
        raise InputValidationError(f"controller.timing.min_pulse_policy must be one of {MIN_PULSE_POLICIES}",
                                   field="controller.timing.min_pulse_policy")
    if d.get("current_loop"):
        P.current_loop_from_dict(d["current_loop"])
    if d.get("timing"):
        P.pwm_timing_from_dict(d["timing"])
    if d.get("sensing"):
        P.sensing_from_dict(d["sensing"])
    tp = d.get("torque_path")
    if tp:
        _pos(tp, "sample_ms", "controller.torque_path")
        _pos(tp, "delay_ms", "controller.torque_path", True)
        _pos(tp, "actuator_tau_ms", "controller.torque_path", True)


def _v_thermal(d: dict):
    P.thermal_model_from_dict(d, 65.0)


def _v_driveline(d: dict):
    if d.get("rom"):
        P.driveline_from_dict(d["rom"])
    if d.get("reducer"):
        P.reducer_from_dict(d["reducer"])


def _v_safety(d: dict):
    for c in d.get("ftti_chains") or []:
        P.timing_chain_from_dict(c)
    for i, r in enumerate(d.get("safe_state_rules") or []):
        if not r.get("rule_id") or not r.get("require"):
            raise InputValidationError("a safe-state rule needs rule_id and require",
                                       field=f"safety.safe_state_rules[{i}]")


def _v_emi(d: dict):
    P.emi_network_from_dict({"C_dc_uF": 1.0, **d})


@dataclass(frozen=True)
class SectionSpec:
    name: str
    title: str
    required: bool
    validate: object


SECTIONS = {s.name: s for s in (
    SectionSpec("drive", "drive model (machine and inverter electrical model of the core)", True, _v_drive),
    SectionSpec("dc_source", "DC source: nominal voltage, average limits, source impedance", True, _v_dc_source),
    SectionSpec("module", "power module (datasheet curves) and its junction-to-coolant path", False, _v_module),
    SectionSpec("alternatives", "design alternatives for comparison studies (own module, gate drive, dead time)",
                False, _v_alternatives),
    SectionSpec("dc_link", "DC-link capacitor bank", False, _v_dc_link),
    SectionSpec("controller", "control and modulation: fsw, modulation, dead time, gate edges, current loop, "
                              "timing, current sensing, torque path", False, _v_controller),
    SectionSpec("thermal", "cooling system and thermal networks of the thermal page", False, _v_thermal),
    SectionSpec("driveline", "gearbox: reducer efficiency and torsional ROM", False, _v_driveline),
    SectionSpec("safety", "FTTI chains and project safe-state rules", False, _v_safety),
    SectionSpec("emi_setup", "HV network parasitics and the EMI test setup (artificial network)", False, _v_emi),
)}

# analysis -> (title, sections it reads); the basis of revision impact and stale marking
ANALYSES = {
    "decision": ("requirement decision and claims", ("drive", "dc_source")),
    "operating": ("operating points, maps, sweeps, envelopes, design views", ("drive", "dc_source")),
    "thermal_duration": ("thermal duration and torque availability", ("drive", "dc_source", "thermal")),
    "ftti": ("FTTI timing chain", ("safety",)),
    "safe_state": ("safe-state screening", ("drive", "safety")),
    "discharge": ("active / passive discharge and regeneration overvoltage", ("drive", "dc_link")),
    "protection": ("protection thresholds (DC-link overvoltage example)", ("dc_link",)),
    "asc": ("ASC fault transient", ("drive", "dc_source")),
    "module_losses": ("datasheet module losses", ("drive", "dc_source", "module", "controller")),
    "dc_link_ripple": ("DC-link ripple and capacitor current", ("drive", "dc_source", "dc_link", "controller")),
    "lifetime": ("module thermal cycling and lifetime", ("drive", "dc_source", "module", "controller")),
    "efficiency": ("efficiency boundaries, maps and mission energy",
                   ("drive", "dc_source", "module", "controller", "driveline")),
    "module_ab": ("module A/B comparison", ("drive", "dc_source", "module", "alternatives", "controller", "driveline")),
    "pwm": ("variable PWM policy, sampling and transitions",
            ("drive", "dc_source", "module", "alternatives", "controller", "dc_link")),
    "driveline": ("anti-jerk / active driveline damping", ("drive", "dc_source", "driveline", "controller")),
    "oew": ("open-end winding dual inverter", ("drive", "dc_source", "module", "controller")),
    "emi": ("conducted EMI", ("drive", "dc_source", "dc_link", "controller", "emi_setup")),
    "hev": ("HEV joint torque, cranking, load rejection", ("drive",)),
    "machine_design": ("motor design study", ("drive",)),
}


def limits_from(d: dict) -> dict:
    """File limits -> memory: null = not declared (never unlimited), 'unlimited' / Infinity = declared unlimited."""
    out = {}
    for k in LIMIT_KEYS:
        v = d.get(k)
        if v is None or v == "":
            out[k] = None
        elif isinstance(v, str) and v.strip().lower() in (UNLIMITED, "inf", "infinity"):
            out[k] = math.inf
        else:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                raise InputValidationError(f"dc_source.limits.{k} must be a number, null or 'unlimited'",
                                           field=f"dc_source.limits.{k}") from None
    unknown = set(d) - set(LIMIT_KEYS)
    if unknown:
        raise InputValidationError(f"unknown DC limit(s) {sorted(unknown)}", field="dc_source.limits")
    return out


@dataclass(frozen=True)
class Section:
    name: str
    data: dict
    provenance: dict = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return digest(self.data)


def _normalize_section(name: str, data: dict) -> dict:
    data = copy.deepcopy(data)
    if name == "dc_source":
        data["limits"] = limits_from(data.get("limits") or {})
    return data


@dataclass(frozen=True)
class Project:
    id: str
    revision: str
    title: str
    origin: str
    sections: dict
    note: str = ""
    change_log: tuple = ()
    modified: bool = False          # a working copy whose sections differ from the stated revision

    # -- construction -----------------------------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict, validate: bool = True) -> "Project":
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            raise InputValidationError(f"not a project file (schema must be {SCHEMA!r})", field="schema")
        meta = d.get("project") or {}
        pid, rev = str(meta.get("id") or "").strip(), str(meta.get("revision") or "").strip()
        if not pid or not rev:
            raise InputValidationError("project.id and project.revision are required", field="project")
        origin = str(meta.get("origin") or "")
        if origin not in ORIGINS:
            raise InputValidationError(f"project.origin must be one of {ORIGINS}", field="project.origin")
        raw = d.get("sections") or {}
        unknown = sorted(set(raw) - set(SECTIONS))
        if unknown:
            raise InputValidationError(f"unknown section(s) {unknown}; known: {list(SECTIONS)}", field="sections")
        missing = [n for n, s in SECTIONS.items() if s.required and n not in raw]
        if missing:
            raise InputValidationError(f"required section(s) missing: {missing}", field="sections")
        secs = {}
        for name in SECTIONS:
            if name not in raw:
                continue
            s = raw[name]
            if not isinstance(s, dict) or not isinstance(s.get("data"), dict):
                raise InputValidationError(f"section {name!r} needs a 'data' object", field=f"sections.{name}")
            secs[name] = Section(name, _normalize_section(name, s["data"]), dict(s.get("provenance") or {}))
        p = cls(pid, rev, str(meta.get("title") or ""), origin, secs, str(meta.get("note") or ""),
                tuple(meta.get("change_log") or ()), bool(meta.get("modified", False)))
        if validate:
            p.validate()
        return p

    def validate(self) -> None:
        """Every section through the parser of its analyses (errors name the section)."""
        for name, s in self.sections.items():
            try:
                SECTIONS[name].validate(s.data)
            except InputValidationError as exc:
                raise InputValidationError(f"section {name!r}: {exc}", field=f"sections.{name}") from None
            except (KeyError, TypeError, ValueError) as exc:
                raise InputValidationError(f"section {name!r}: malformed data ({exc!r})",
                                           field=f"sections.{name}") from None

    def to_dict(self) -> dict:
        secs = {}
        for name, s in self.sections.items():
            data = copy.deepcopy(s.data)
            if name == "dc_source":
                data["limits"] = {k: (UNLIMITED if v is not None and math.isinf(v) else v)
                                  for k, v in data["limits"].items()}
            secs[name] = {"provenance": copy.deepcopy(s.provenance), "data": data}
        return {"schema": SCHEMA,
                "project": {"id": self.id, "revision": self.revision, "title": self.title, "origin": self.origin,
                            "note": self.note, "change_log": list(self.change_log), "modified": self.modified},
                "sections": secs}

    # -- identity ---------------------------------------------------------------------------------------------
    def has(self, name: str) -> bool:
        return name in self.sections

    def data(self, name: str) -> dict:
        if name not in self.sections:
            raise InputValidationError(f"the project has no {name!r} section ({SECTIONS[name].title})",
                                       field=f"sections.{name}")
        return copy.deepcopy(self.sections[name].data)

    def digest(self) -> str:
        return digest({"id": self.id, "revision": self.revision,
                       "sections": {n: s.digest for n, s in self.sections.items()}})

    @property
    def label(self) -> str:
        return f"{self.id} rev {self.revision}" + (" (modified)" if self.modified else "")

    def identity(self, sections=None) -> dict:
        names = list(self.sections) if sections is None else [n for n in sections]
        return {"project_id": self.id, "revision": self.revision, "modified": self.modified,
                "project_digest": self.digest(),
                "sections": {n: (self.sections[n].digest if n in self.sections else None) for n in names}}

    def usage(self, analysis: str, effective: dict | None = None) -> dict:
        """What an analysis ran on: the project identity for its sections, and which of them the page changed
        locally (``effective``: section -> the data actually used, when it differs from the project)."""
        title, names = ANALYSES[analysis]
        ident = self.identity(names)
        local = {}
        for n, data in (effective or {}).items():
            d = digest(data)
            if d != ident["sections"].get(n):
                local[n] = d
        ident.update(analysis=analysis, analysis_title=title, local_changes=local)
        return ident

    # -- editing ----------------------------------------------------------------------------------------------
    def with_section(self, name: str, data: dict, provenance: dict | None = None) -> "Project":
        """A working copy with one section replaced (validated); unchanged content keeps the project unmodified."""
        if name not in SECTIONS:
            raise InputValidationError(f"unknown section {name!r}", field="sections")
        new = Section(name, _normalize_section(name, data),
                      dict(provenance if provenance is not None else
                           (self.sections[name].provenance if name in self.sections else {})))
        try:
            SECTIONS[name].validate(new.data)
        except InputValidationError as exc:
            raise InputValidationError(f"section {name!r}: {exc}", field=f"sections.{name}") from None
        old = self.sections.get(name)
        if old is not None and old.digest == new.digest and old.provenance == new.provenance:
            return self
        secs = {**self.sections, name: new}
        return replace(self, sections={n: secs[n] for n in SECTIONS if n in secs}, modified=True)

    def as_revision(self, revision: str, change: str) -> "Project":
        rev = str(revision).strip()
        if not rev or not str(change).strip():
            raise InputValidationError("a new revision needs its name and a change note", field="revision")
        if rev == self.revision and not self.modified:
            return self
        return replace(self, revision=rev, modified=False,
                       change_log=self.change_log + ({"revision": rev, "change": str(change).strip(),
                                                      "from": f"{self.revision}" + (" (modified)" if self.modified
                                                                                    else "")},))

    # -- composition: product data -> request fields of the analyses --------------------------------------------
    def dc_limits(self) -> DcSourceLimits:
        lim = self.data("dc_source")["limits"]
        return DcSourceLimits(*(lim[k] for k in LIMIT_KEYS), source=f"project {self.label} dc_source")

    def limits_dict(self) -> dict:
        return dict(self.data("dc_source")["limits"])

    def module_spec(self, alternative: str | None = None) -> dict:
        """The module description the loss analyses take: device data + the switching frequency, modulation and
        dead time of the controller (an alternative design keeps its own dead time) + its thermal path."""
        c = self.data("controller")
        if alternative is None:
            m, dead = self.data("module"), c["deadtime_us"]
        else:
            alt = next((a for a in self.data("alternatives").get("modules", []) if a.get("key") == alternative), None)
            if alt is None:
                raise InputValidationError(f"no alternative {alternative!r} in the project", field="alternatives")
            m, dead = copy.deepcopy(alt["module"]), alt["deadtime_us"]
        tp = m.pop("thermal_path")
        out = {**m, "fsw_kHz": c["fsw_kHz"], "modulation": c["modulation"], "deadtime_us": dead,
               "Rth_K_per_W": tp["Rth_K_per_W"]}
        if tp.get("T_ref_C") is not None:
            out["T_ref_C"] = tp["T_ref_C"]
        return out

    def first_alternative(self) -> str | None:
        keys = self.alternative_keys()
        return keys[0][0] if keys else None

    def alternative_keys(self) -> list:
        if not self.has("alternatives"):
            return []
        return [(a["key"], a.get("label", a["key"])) for a in self.data("alternatives").get("modules", [])]

    def junction_network(self) -> dict | None:
        tp = self.data("module")["thermal_path"]
        fo = tp.get("foster")
        if not fo:
            return None
        return {"R_K_per_W": list(fo["R_K_per_W"]), "tau_s": list(fo["tau_s"]),
                "note": "synthetic junction-to-coolant Foster network of the example module: its steady sum is the "
                        "module's Rth (the same thermal path as the efficiency / PWM pages)"
                if self.origin == "synthetic" else f"junction-to-coolant Foster network of project {self.label}"}

    def capacitor(self) -> dict:
        d = self.data("dc_link")
        return {k: d[k] for k in ("C_uF", "ESL_nH", "Rth_K_per_W", "T_ref_C", "ESR_table", "ESR_unit",
                                  "ESR_temp_coeff_per_K", "ESR_table_T_C", "T_valid_C", "Rth_basis", "count",
                                  "symmetric_layout", "life_hours_table", "life_voltage_V", "life_basis", "source")
                if k in d}

    def source_impedance(self) -> dict | None:
        imp = self.data("dc_source").get("impedance")
        return None if not imp else copy.deepcopy(imp)

    def emi_source(self) -> dict:
        """The EMI switching source of THIS controller: fsw, modulation, dead time, gate edges, carrier mode and
        the minimum pulse with its handling (the same minimum pulse the PWM analysis checks)."""
        c = self.data("controller")
        g = c.get("gate") or {}
        t = c.get("timing") or {}
        mp = float(t.get("min_pulse_us") or 0.0)
        return {"fsw_kHz": c["fsw_kHz"], "t_rise_ns": g.get("t_rise_ns"), "t_fall_ns": g.get("t_fall_ns"),
                "t_dead_us": c["deadtime_us"], "modulation": c["modulation"],
                "carrier": c.get("carrier", "asynchronous"), "min_pulse_us": mp,
                "min_pulse_policy": t.get("min_pulse_policy") or ("unknown" if mp > 0 else "none"),
                "basis": g.get("basis", "")}

    def emi_network(self) -> dict:
        dl = self.data("dc_link")
        return {"C_dc_uF": dl["C_uF"], "ESR_dc_mohm": dl.get("ESR_hf_mohm"), "ESL_dc_nH": dl.get("ESL_nH"),
                **self.data("emi_setup")}

    def torque_path(self) -> dict:
        return self.data("controller")["torque_path"]

    def reducer(self) -> dict:
        return self.data("driveline")["reducer"]

    def driveline_rom(self) -> dict:
        return self.data("driveline")["rom"]

    def thermal_spec(self) -> dict:
        return self.data("thermal")

    def ftti_chain(self, i: int = 0) -> dict:
        return self.data("safety")["ftti_chains"][i]

    def safe_state_rules(self) -> list:
        return self.data("safety").get("safe_state_rules") or []


# -- files ----------------------------------------------------------------------------------------------------


def load_project(path) -> Project:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputValidationError(f"project file is not valid JSON: {exc}", field="project") from None
    return Project.from_dict(d)


def save_project(project: Project, path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(project.to_dict(), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return p


_BUILTIN: Project | None = None


def builtin_project() -> Project:
    """The synthetic project of ``examples`` (validated once)."""
    global _BUILTIN
    if _BUILTIN is None:
        from .examples import SYNTHETIC_PROJECT
        _BUILTIN = Project.from_dict(SYNTHETIC_PROJECT)
    return _BUILTIN


# -- cross-section consistency --------------------------------------------------------------------------------

OK, INCONSISTENT, WARNING, NOTE, NOT_CHECKED = "OK", "INCONSISTENT", "WARNING", "NOTE", "NOT_CHECKED"
_RANK = {INCONSISTENT: 4, WARNING: 3, NOT_CHECKED: 2, NOTE: 1, OK: 0}


@dataclass(frozen=True)
class Finding:
    rule: str
    title: str
    status: str
    sections: tuple
    detail: str
    values: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"rule": self.rule, "title": self.title, "status": self.status, "sections": list(self.sections),
                "detail": self.detail, "values": self.values}


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def _modules(p: Project):
    """(label, module data, operating dead time) of the design and of every alternative."""
    out = []
    if p.has("module"):
        out.append(("module", p.data("module"), p.data("controller")["deadtime_us"] if p.has("controller") else None))
    if p.has("alternatives"):
        for a in p.data("alternatives").get("modules", []):
            out.append((f"alternative {a['key']}", a["module"], a.get("deadtime_us")))
    return out


def _r_thermal_path(p):
    rows = []
    for label, m, _ in _modules(p):
        tp = m["thermal_path"]
        fo = tp.get("foster")
        if not fo:
            rows.append(Finding("PRJ-01", f"thermal path of the {label}", NOTE, ("module",),
                                "only the steady Rth is declared (no transient network): lifetime and transient "
                                "junction temperatures need a Foster/Cauer network", {"Rth_K_per_W": tp["Rth_K_per_W"]}))
            continue
        s = float(sum(fo["R_K_per_W"]))
        ok = _rel(s, float(tp["Rth_K_per_W"])) <= 1e-6
        rows.append(Finding("PRJ-01", f"thermal path of the {label}", OK if ok else INCONSISTENT, ("module",),
                            "the Foster network's steady sum equals the declared Rth" if ok else
                            "the Foster network's steady sum differs from the declared Rth: the steady analyses "
                            "(losses, efficiency, PWM) and the transient ones (lifetime) would use two different "
                            "thermal paths of the same module", {"Rth_K_per_W": tp["Rth_K_per_W"], "foster_sum": s}))
    return rows


def _r_deadtime(p):
    rows = []
    for label, m, dead in _modules(p):
        test = (m.get("test_conditions") or {}).get("deadtime_test_us")
        if dead is None or test is None:
            rows.append(Finding("PRJ-02", f"dead time of the {label}'s switching data", NOT_CHECKED, ("module", "controller"),
                                "the operating or the test dead time is not declared"))
            continue
        ok = _rel(float(dead), float(test)) <= 1e-9 or float(dead) == float(test)
        rows.append(Finding("PRJ-02", f"dead time of the {label}'s switching data", OK if ok else WARNING,
                            ("module", "controller"),
                            "operating dead time = test dead time of the switching / recovery energies" if ok else
                            "the switching / recovery energies were measured at another dead time: the loss model "
                            "uses them as measured (a correction needs a basis)",
                            {"operating_us": dead, "test_us": test}))
    return rows


def _r_gate(p):
    if not (p.has("module") and p.has("controller")):
        return [Finding("PRJ-03", "gate drive vs switching test conditions", NOT_CHECKED, ("module", "controller"),
                        "module or controller section missing")]
    tc = p.data("module").get("test_conditions") or {}
    g = p.data("controller").get("gate") or {}
    pairs = [(k, g.get(k), tc.get(k)) for k in ("Rg_on_ohm", "Rg_off_ohm", "Vge_V") if k in g or k in tc]
    if not pairs or any(a is None or b is None for _, a, b in pairs):
        return [Finding("PRJ-03", "gate drive vs switching test conditions", NOT_CHECKED, ("module", "controller"),
                        "gate resistances / voltage not declared on both sides", {k: [a, b] for k, a, b in pairs})]
    diff = [k for k, a, b in pairs if float(a) != float(b)]
    return [Finding("PRJ-03", "gate drive vs switching test conditions", WARNING if diff else OK, ("module", "controller"),
                    ("the gate drive differs from the datasheet test conditions in " + ", ".join(diff) +
                     ": switching energies depend on R_g / V_GE - a correction needs a basis (DPT)") if diff else
                    "gate resistances and voltage equal the datasheet test conditions",
                    {k: {"gate": a, "test": b} for k, a, b in pairs})]


def _r_current_range(p):
    if not p.has("module"):
        return [Finding("PRJ-04", "module data vs inverter current limit", NOT_CHECKED, ("drive", "module"),
                        "no module section")]
    drv = drive_from_dict(p.data("drive"))
    m = p.data("module")
    imax = drv.inverter.current_limit_A_peak / int(m.get("parallel", 1)) * (1 + float(m.get("sharing_error_pct") or 0) / 100)
    tops = {k: max(c["currents_A"]) for k, c in (m.get("curves") or {}).items()}
    lo = min(tops.values())
    ok = imax <= lo * (1 + 1e-12)
    return [Finding("PRJ-04", "module data vs inverter current limit", OK if ok else WARNING, ("drive", "module"),
                    "the curves cover the inverter current limit per position" if ok else
                    "the inverter current limit per position exceeds the module curves: losses (and every DC claim "
                    "with the module model) are UNKNOWN above the data (no extrapolation)",
                    {"I_peak_per_position_A": imax, "curve_max_A": tops})]


def _r_tj_eval(p):
    rows = []
    for label, m, _ in _modules(p):
        tj = float(m["Tj_eval_C"])
        temps = [t for c in (m.get("curves") or {}).values() for t in c["temps_C"]]
        ok = min(temps) <= tj <= max(temps)
        rows.append(Finding("PRJ-05", f"evaluation junction temperature of the {label}", OK if ok else WARNING,
                            ("module",), "inside the curves' temperature range" if ok else
                            "outside the curves' temperature range: the module losses are not established anywhere",
                            {"Tj_eval_C": tj, "curve_temps_C": [min(temps), max(temps)]}))
    return rows


def _r_test_voltage(p):
    if not p.has("module"):
        return [Finding("PRJ-06", "switching test voltage vs nominal DC voltage", NOT_CHECKED, ("module", "dc_source"),
                        "no module section")]
    m = p.data("module")
    v, vt = float(p.data("dc_source")["Vdc_nominal_V"]), float(m["v_test_V"])
    sc = m.get("vdc_scaling") or {}
    if v == vt:
        return [Finding("PRJ-06", "switching test voltage vs nominal DC voltage", OK, ("module", "dc_source"),
                        "the nominal DC voltage is the switching test voltage; away from it the switching losses are "
                        "UNKNOWN without a declared scaling law" + (" (declared)" if sc else " (none declared)"),
                        {"Vdc_nominal_V": v, "v_test_V": vt, "scaling": bool(sc)})]
    covered = bool(sc) and sc.get("valid_V") and sc["valid_V"][0] <= v <= sc["valid_V"][1]
    return [Finding("PRJ-06", "switching test voltage vs nominal DC voltage", OK if covered else WARNING,
                    ("module", "dc_source"),
                    "the declared scaling law covers the nominal voltage" if covered else
                    "the nominal DC voltage differs from the switching test voltage and no scaling law covers it: "
                    "switching losses at the nominal voltage are UNKNOWN", {"Vdc_nominal_V": v, "v_test_V": vt})]


def _r_loop_design(p):
    if not p.has("controller") or not p.data("controller").get("current_loop"):
        return [Finding("PRJ-07", "current-loop design inductance vs machine", NOT_CHECKED, ("controller", "drive"),
                        "no current loop declared")]
    lp = p.data("controller")["current_loop"]
    flux = drive_from_dict(p.data("drive")).motor.flux
    if not hasattr(flux, "Ld_H"):
        return [Finding("PRJ-07", "current-loop design inductance vs machine", NOTE, ("controller", "drive"),
                        "flux-map machine: the differential inductance depends on the operating point; the PWM "
                        "evaluation judges each point with it")]
    dec = ({"d": lp.get("Ld_uH"), "q": lp.get("Lq_uH")} if lp.get("Ld_uH") not in (None, "")
           else {"d": lp.get("L_uH"), "q": lp.get("L_uH")})
    mach = {"d": flux.Ld_H * 1e6, "q": flux.Lq_H * 1e6}
    ratio = {a: float(dec[a]) / mach[a] for a in ("d", "q")}
    ok = all(abs(r - 1) <= 0.05 for r in ratio.values())
    return [Finding("PRJ-07", "current-loop design inductance vs machine", OK if ok else NOTE, ("controller", "drive"),
                    "design inductances within 5 % of the machine's" if ok else
                    "the gains are designed for other inductances than the machine's: the loop margins differ from "
                    "the design intent (the PWM evaluation judges with the machine's differential inductance)",
                    {"design_uH": dec, "machine_uH": mach, "ratio": ratio})]


def _r_torque_path(p):
    c = p.data("controller") if p.has("controller") else {}
    tp, lp = c.get("torque_path"), c.get("current_loop")
    if not tp or not lp:
        return [Finding("PRJ-08", "torque-path lag vs current-loop bandwidth", NOT_CHECKED, ("controller",),
                        "torque path or current loop not declared")]
    tau_loop = 1.0 / (2 * math.pi * float(lp["bandwidth_Hz"]))
    tau = float(tp["actuator_tau_ms"]) * 1e-3
    if tau + 1e-15 < tau_loop:
        return [Finding("PRJ-08", "torque-path lag vs current-loop bandwidth", WARNING, ("controller",),
                        "the torque path of the driveline analysis is faster than the declared current loop can "
                        "deliver: its comfort / damping results are optimistic",
                        {"actuator_tau_ms": tau * 1e3, "current_loop_tau_ms": tau_loop * 1e3})]
    return [Finding("PRJ-08", "torque-path lag vs current-loop bandwidth", NOTE, ("controller",),
                    "the declared torque-path lag is not faster than the current loop (conservative; it may include "
                    "other lags) - it is a declaration, not derived from the loop",
                    {"actuator_tau_ms": tau * 1e3, "current_loop_tau_ms": tau_loop * 1e3})]


def _r_loop_reference(p):
    c = p.data("controller") if p.has("controller") else {}
    lp = c.get("current_loop")
    if not lp:
        return []
    ref = lp.get("reference_fsw_kHz")
    if ref in (None, "") or float(ref) == float(c["fsw_kHz"]):
        return [Finding("PRJ-09", "current-loop reference switching frequency", OK, ("controller",),
                        "the gains refer to the controller's switching frequency",
                        {"reference_fsw_kHz": ref, "fsw_kHz": c["fsw_kHz"]})]
    return [Finding("PRJ-09", "current-loop reference switching frequency",
                    WARNING if lp.get("gain_mapping") == "fixed_discrete" else NOTE, ("controller",),
                    "the discrete gains were tuned at another switching frequency" +
                    (" and are not remapped: the loop at the controller's fsw is not the designed one"
                     if lp.get("gain_mapping") == "fixed_discrete" else " (remapped continuously)"),
                    {"reference_fsw_kHz": ref, "fsw_kHz": c["fsw_kHz"]})]


def _r_gear(p):
    if not p.has("driveline"):
        return [Finding("PRJ-10", "reducer ratio vs torsional model", NOT_CHECKED, ("driveline",), "no driveline section")]
    d = p.data("driveline")
    if not d.get("rom") or not d.get("reducer"):
        return [Finding("PRJ-10", "reducer ratio vs torsional model", NOT_CHECKED, ("driveline",),
                        "reducer or torsional model missing")]
    a, b = float(d["rom"]["ratio"]), float(d["reducer"]["ratio"])
    ok = a == b
    return [Finding("PRJ-10", "reducer ratio vs torsional model", OK if ok else INCONSISTENT, ("driveline",),
                    "one gearbox: the torsional model and the reducer efficiency use the same ratio" if ok else
                    "the torsional model and the reducer efficiency describe the same gearbox with two ratios",
                    {"rom_ratio": a, "reducer_ratio": b})]


def _r_esr(p):
    if not p.has("dc_link"):
        return []
    d = p.data("dc_link")
    if d.get("ESR_hf_mohm") is None:
        return []
    k = {"mohm": 1.0, "ohm": 1e3}[d.get("ESR_unit", "mohm")]
    f_hi, r_hi = d["ESR_table"][-1]
    return [Finding("PRJ-11", "DC-link ESR: EMI constant vs frequency table", NOTE, ("dc_link",),
                    "the EMI network uses one constant ESR, the ripple analysis the frequency table: two abstractions "
                    "of the same capacitor, stated side by side",
                    {"ESR_hf_mohm": d["ESR_hf_mohm"], "table_highest_f_Hz": f_hi, "table_ESR_there_mohm": r_hi * k})]


def _r_thermal_node(p):
    if not (p.has("thermal") and p.has("module")):
        return []
    rows = []
    rth = float(p.data("module")["thermal_path"]["Rth_K_per_W"])
    for n in p.data("thermal").get("nodes", []):
        if n.get("station") != "inverter":
            continue
        rows.append(Finding("PRJ-12", f"thermal-page node {n['id']!r} vs the module path", NOTE, ("thermal", "module"),
                            "the thermal page's inverter node is a per-switch average network fed with its loss share; "
                            "the module path is the hottest position: two abstractions, not one value",
                            {"node_R_sum_K_per_W": float(sum(n["R_K_per_W"])), "loss_share": n.get("loss_share"),
                             "module_Rth_K_per_W": rth}))
    return rows


RULES = (_r_thermal_path, _r_deadtime, _r_gate, _r_current_range, _r_tj_eval, _r_test_voltage, _r_loop_design,
         _r_torque_path, _r_loop_reference, _r_gear, _r_esr, _r_thermal_node)


def check_project(p: Project) -> dict:
    """Cross-section consistency: INCONSISTENT (one quantity, two values / a violated relation), WARNING (a
    combination that makes analyses UNKNOWN or optimistic), NOTE (different abstractions or declarations stated
    side by side), NOT_CHECKED (data missing)."""
    rows = []
    for rule in RULES:
        try:
            rows.extend(rule(p))
        except InputValidationError as exc:
            rows.append(Finding(rule.__name__, rule.__name__, NOT_CHECKED, (), str(exc)))
    worst = max((f.status for f in rows), key=lambda s: _RANK[s], default=OK)
    status = {INCONSISTENT: INCONSISTENT, WARNING: WARNING}.get(worst, OK)
    return {"status": status, "identity": p.identity(), "findings": [f.to_dict() for f in rows],
            "counts": {s: sum(f.status == s for f in rows) for s in _RANK}}


# -- revisions ------------------------------------------------------------------------------------------------


def _paths(a, b, prefix="") -> list:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            p = f"{prefix}.{k}" if prefix else str(k)
            if k not in a:
                out.append((p, None, b[k]))
            elif k not in b:
                out.append((p, a[k], None))
            else:
                out.extend(_paths(a[k], b[k], p))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(_paths(x, y, f"{prefix}[{i}]"))
        return out
    return [] if canonical_json(a) == canonical_json(b) else [(prefix, a, b)]


def _brief(v) -> str:
    s = canonical_json(v)
    return s if len(s) <= 60 else s[:57] + "..."


def diff_projects(a: Project, b: Project) -> dict:
    """Changed sections (with the changed paths), the analyses they feed, and the analyses left unaffected."""
    changed = {}
    for n in SECTIONS:
        sa, sb = a.sections.get(n), b.sections.get(n)
        if sa is None and sb is None:
            continue
        if sa is None or sb is None:
            changed[n] = {"change": "added" if sa is None else "removed", "paths": []}
        elif sa.digest != sb.digest or sa.provenance != sb.provenance:
            paths = _paths(sa.data, sb.data)
            changed[n] = {"change": "modified" if paths else "provenance", "from": short(sa.digest),
                          "to": short(sb.digest),
                          "paths": [{"path": p, "from": _brief(x), "to": _brief(y)} for p, x, y in paths[:60]],
                          "n_paths": len(paths)}
    affected = {aid: {"title": t, "sections": [s for s in secs if s in changed]}
                for aid, (t, secs) in ANALYSES.items() if any(s in changed for s in secs)}
    return {"from": a.identity(), "to": b.identity(), "changed_sections": changed, "affected_analyses": affected,
            "unaffected_analyses": [aid for aid in ANALYSES if aid not in affected]}


# -- what a request used: the project's composition or local edits ----------------------------------------------

# desktop task keys / CLI names -> analysis
TASK_ANALYSIS = {
    "decision": "decision", "decision-env": "operating", "explorer": "operating", "trajectory": "operating",
    "performance": "operating", "design-sweep": "operating", "design-dom": "operating",
    "thermal": "thermal_duration", "ftti": "ftti", "safe_state": "safe_state", "discharge": "discharge",
    "passive": "discharge", "overvoltage": "discharge", "protection": "protection", "asc": "asc",
    "module": "module_losses", "ripple": "dc_link_ripple", "lifetime": "lifetime",
    "efficiency": "efficiency", "efficiency_map": "efficiency", "efficiency_mission": "efficiency",
    "module_compare": "module_ab", "pwm_policies": "pwm", "pwm_timing": "pwm", "pwm_ripple": "pwm",
    "pwm_transients": "pwm", "driveline": "driveline", "driveline_stability": "driveline", "oew": "oew",
    "oew_compare": "oew", "emi_oew": "oew", "hev_joint": "hev", "hev_crank": "hev", "hev_rejection": "hev",
    "hev_planetary": "hev", "emi": "emi", "machine_trade": "machine_design", "winding": "machine_design",
    "concept_sizing": "machine_design",
}

_ABSENT = object()


def _at(body, *path):
    for k in path:
        if not isinstance(body, dict) or k not in body:
            return _ABSENT
        body = body[k]
    return body


def _drive_identity(spec) -> str | None:
    from .identity import content_sha256
    try:
        return content_sha256(drive_from_dict(copy.deepcopy(spec)))
    except (InputValidationError, KeyError, TypeError, ValueError):
        return None


DESCRIPTIVE = frozenset(("basis", "source", "description", "note", "name", "label"))   # provenance text, not physics
_PARSERS = {
    "module": lambda v: P.module_model_from_dict(v),
    "capacitor": lambda v: P.capacitor_bank_from_dict({"capacitor": v, "source": None})[0],
    "reducer": lambda v: P.reducer_from_dict(v),
    "loop": lambda v: P.current_loop_from_dict(v),
    "timing": lambda v: P.pwm_timing_from_dict(v),
    "sensing": lambda v: P.sensing_from_dict(v),
    "rom": lambda v: P.driveline_from_dict(v),
    "emi_network": lambda v: P.emi_network_from_dict(v),
}


def _strip(x):
    """JSON-like form without provenance text and without null-valued keys (null = not declared)."""
    if isinstance(x, dict):
        return {str(k): _strip(v) for k, v in x.items() if k not in DESCRIPTIVE and v is not None}
    if isinstance(x, (list, tuple)):
        return [_strip(v) for v in x]
    return x


def _same(a, b, rel=1e-12) -> bool:
    """Structural equality with numbers equal to a relative 1e-12 (table widgets round the last bits)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[k], b[k], rel) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y, rel) for x, y in zip(a, b))
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return math.isnan(fa) and math.isnan(fb)
        return fa == fb or abs(fa - fb) <= rel * max(abs(fa), abs(fb))
    return a == b


def _engine_form(kind: str, v):
    """What the engine uses: the parsed object's full content (identity.semantic) for a parsed component, the data
    itself otherwise - both without provenance text."""
    from .identity import semantic
    if kind in _PARSERS:
        v = semantic(_PARSERS[kind](copy.deepcopy(v)))
    return _strip(v)


def _without(d, *keys):
    return {k: v for k, v in d.items() if k not in keys} if isinstance(d, dict) else d


def _pick(d, *keys):
    return {k: d.get(k) for k in keys} if isinstance(d, dict) else d


def _limits_file(d):
    """Limits in one comparable form (None = not declared, 'unlimited' = declared unlimited)."""
    if not isinstance(d, dict):
        return d
    return {k: (UNLIMITED if isinstance(v, (int, float)) and math.isinf(v) else v) for k, v in d.items()}


# analysis -> components: (label, project composition, the part of a request body it corresponds to, comparison form)
_CORE = (("drive", lambda p: p.data("drive"), lambda b: _at(b, "drive"), "drive"),
         ("DC limits", lambda p: _limits_file(p.limits_dict()), lambda b: _limits_file(_at(b, "limits")), "data"))
COMPONENTS = {
    "decision": _CORE, "operating": _CORE, "asc": _CORE,
    "safe_state": _CORE[:1] + (("safe-state rules", lambda p: p.safe_state_rules(), lambda b: _at(b, "rules"), "data"),),
    "discharge": _CORE[:1] + (("DC-link capacitance", lambda p: p.data("dc_link")["C_uF"], lambda b: _at(b, "C_uF"),
                               "data"),),
    "hev": _CORE[:1], "machine_design": _CORE[:1], "thermal_duration": _CORE,
    "module_losses": _CORE + (("module", lambda p: p.module_spec(), lambda b: _at(b, "module"), "module"),),
    "dc_link_ripple": _CORE + (
        ("DC-link capacitor", lambda p: p.capacitor(), lambda b: _at(b, "ripple", "capacitor"), "capacitor"),
        ("source impedance", lambda p: p.source_impedance(), lambda b: _at(b, "ripple", "source"), "data"),
        ("switching", lambda p: _pick(p.data("controller"), "fsw_kHz", "modulation"),
         lambda b: _pick(_at(b, "ripple"), "fsw_kHz", "modulation"), "data")),
    "lifetime": _CORE + (
        ("module", lambda p: p.module_spec(), lambda b: _at(b, "module"), "module"),
        ("junction network", lambda p: _pick(p.junction_network(), "R_K_per_W", "tau_s"),
         lambda b: _pick(_at(b, "junction_network"), "R_K_per_W", "tau_s"), "data")),
    "efficiency": _CORE + (("module", lambda p: p.module_spec(), lambda b: _at(b, "module"), "module"),
                           ("reducer", lambda p: p.reducer(), lambda b: _at(b, "reducer"), "reducer")),
    "module_ab": _CORE + (("module A", lambda p: p.module_spec(), lambda b: _at(b, "compare", "A", "module"),
                           "module"),
                          ("module B (first alternative)", lambda p: p.module_spec(p.first_alternative()),
                           lambda b: _at(b, "compare", "B", "module"), "module"),
                          ("reducer", lambda p: p.reducer(), lambda b: _at(b, "reducer"), "reducer")),
    "pwm": _CORE + (("module", lambda p: p.module_spec(), lambda b: _at(b, "module"), "module"),
                    ("current loop", lambda p: p.data("controller")["current_loop"], lambda b: _at(b, "loop"), "loop"),
                    ("controller timing", lambda p: p.data("controller")["timing"], lambda b: _at(b, "timing"),
                     "timing"),
                    ("current sensing", lambda p: p.data("controller")["sensing"], lambda b: _at(b, "sensing"),
                     "sensing"),
                    ("switching", lambda p: _pick(p.data("controller"), "fsw_kHz", "deadtime_us"),
                     lambda b: {"fsw_kHz": _at(b, "baseline_fsw_kHz"), "deadtime_us": _at(b, "transition", "deadtime_us")},
                     "data")),
    "driveline": _CORE + (("torsional ROM", lambda p: p.driveline_rom(), lambda b: _at(b, "driveline"), "rom"),
                          ("torque path", lambda p: p.torque_path(), lambda b: _at(b, "controller"), "data")),
    "oew": _CORE + (("module", lambda p: p.module_spec(), lambda b: _without(_at(b, "module"), "vdc_scaling"),
                     "module"),),
    "emi": _CORE + (("EMI source", lambda p: p.emi_source(), lambda b: _at(b, "source"), "data"),
                    ("EMI network", lambda p: p.emi_network(), lambda b: _at(b, "network"), "emi_network")),
    "protection": (("DC-link capacitance", lambda p: p.data("dc_link")["C_uF"],
                    lambda b: _at(b, "plant", "C_uF") if _at(b, "plant", "kind") == "capacitor_energy" else _ABSENT,
                    "data"),),
    "ftti": (("FTTI chain", lambda p: p.ftti_chain(0), lambda b: b, "data"),),
}


def request_usage(project: Project, task: str, body: dict | None) -> dict:
    """What a request ran on: the project identity for the sections of its analysis and, per product component,
    whether the request used the project's composition or a local edit (never silently one for the other)."""
    analysis = TASK_ANALYSIS.get(task, task)
    if analysis not in ANALYSES:
        return {"analysis": None, "task": task}
    use = project.usage(analysis)
    comps = {}
    for label, compose, extract, kind in COMPONENTS.get(analysis, ()):
        got = extract(body or {})
        if got is _ABSENT:
            continue
        try:
            want = compose(project)
        except (InputValidationError, KeyError, TypeError) as exc:
            comps[label] = {"from_project": False, "digest": None, "missing_in_project": str(exc)}
            continue
        if kind == "drive":
            d_got = _drive_identity(got)
            same = d_got is not None and d_got == _drive_identity(want)
        else:
            try:
                fg = _engine_form(kind, got)
                same = _same(fg, _engine_form(kind, want))
                d_got = digest(fg)
            except (InputValidationError, KeyError, TypeError, ValueError):
                same, d_got = False, None                # data the engine would refuse is never "the project's"
        comps[label] = {"from_project": same, "digest": d_got}
    use.update(task=task, components=comps,
               local_edits=[k for k, v in comps.items() if not v["from_project"]])
    return use


def stale_sections(used: dict | None, project: Project) -> list:
    """Sections whose digest changed since a result was computed (``used`` = the result's ``usage`` identity)."""
    if not used:
        return []
    now = project.identity(list((used.get("sections") or {}).keys()))["sections"]
    out = [n for n, d in (used.get("sections") or {}).items() if now.get(n) != d]
    if used.get("project_id") != project.id:
        out = ["<project>"] + out
    return out
