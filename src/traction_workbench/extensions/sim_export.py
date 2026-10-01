"""Exports for vehicle simulators (system view, item 12): the drive's maps for a vehicle model.

On a speed x shaft-torque grid at one or more DC voltages, each cell is the minimum-current policy point of the
model: DC power, the power at the motor terminals, the shaft and the declared output, the losses per component
(inverter, motor, reducer - the items the model evaluates) and the d/q currents.  Per speed and voltage the full-load
torque (largest motoring and generating torque the policy delivers).  Written as

  * a long CSV (one row per cell, every quantity in a column),
  * grid CSVs (one table per quantity and voltage: speeds across, torques down - a spreadsheet's 2-D lookup),
  * a MATLAB .mat file (breakpoints and [torque x speed x Vdc] tables for 2-D / n-D lookup blocks),
  * an FMI 2.0 FMU (Model Exchange and Co-Simulation) that clamps the torque request to the full-load curve and
    interpolates the tables - the same numbers in a vehicle simulator.

The honesty rules of the tool hold: a cell outside the delivered envelope or with an undecided policy is NaN with its
status (never a number made up); the loss items the model does not evaluate are listed in the metadata.  Inside the
envelope a quantity can still be empty where one part has no loss there - a reducer outside its declared torque or
speed range: its loss, the output power and the total loss stay NaN in every format (the total is never the
subtotal without that part), counted per quantity in the metadata.  Only the FMU needs values in the cells next to
the envelope an interpolation touches: those cells OUTSIDE the envelope are filled with the nearest feasible cell's
value (in grid-index distance), marked in the mask and stated in the FMU's description - the request is clamped to
the full-load curve first, so a filled cell only shapes the value within one grid cell of the boundary.  An empty
cell inside the envelope stays NaN in the FMU too (the interpolation never reads a corner of zero weight).
"""

from __future__ import annotations

import csv
import json
import math
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import progress
from ..errors import InputValidationError

STATUS_CODE = {"FEASIBLE": 0, "UNKNOWN": 1, "INFEASIBLE": 2, "BEYOND": 3}
QUANTITIES = {   # key: (unit, meaning)
    "P_dc_W": ("W", "DC power at the inverter terminals (> 0 drawn from the battery)"),
    "P_ac_W": ("W", "electrical power at the motor terminals"),
    "P_shaft_W": ("W", "mechanical power at the motor shaft"),
    "P_out_W": ("W", "power at the declared reducer output (NaN without a reducer)"),
    "loss_inverter_W": ("W", "inverter loss (semiconductors as modelled)"),
    "loss_motor_W": ("W", "motor loss evaluated by the model (copper + rotational)"),
    "loss_reducer_W": ("W", "reducer loss (NaN without a reducer)"),
    "loss_total_W": ("W", "every evaluated loss between the DC terminals and the declared output"),
    "id_A": ("A", "d-axis current (peak, amplitude-invariant)"),
    "iq_A": ("A", "q-axis current (peak, amplitude-invariant)"),
}
FMU_OUTPUTS = ("P_dc_W", "loss_total_W", "loss_inverter_W", "loss_motor_W", "loss_reducer_W", "P_out_W")


def _grid(values, name) -> list:
    v = [float(x) for x in values]
    if len(v) < 2 or any(not math.isfinite(x) for x in v) or any(b <= a for a, b in zip(v, v[1:])):
        raise InputValidationError(f"{name}: at least two finite values in increasing order", field=name)
    return v


def compute_maps(drive, *, Vdc_list, limits, speeds_rpm, torques_Nm=None, torque_step_Nm: float | None = None,
                 reducer=None, oil_temp_C=None, temps: dict | None = None, source: dict | None = None) -> dict:
    """The maps on the grid (see the module text).  Tables are [Vdc][torque][speed].  Without ``torques_Nm`` the
    torque grid spans the full-load curves found (multiples of ``torque_step_Nm``, zero included)."""
    from ..analysis.efficiency import DEFINED, NA, point_ledger
    from ..scenario import Scenario
    from ..solvers.capability import policy_capability
    from ..solvers.policy import PolicyEvaluator
    sp = _grid(speeds_rpm, "speeds_rpm")
    vd = _grid(Vdc_list, "Vdc_V") if len(Vdc_list) > 1 else [float(Vdc_list[0])]
    if any(n < 0 for n in sp):
        raise InputValidationError("speeds_rpm: one direction (>= 0); the generating side is the negative torque",
                                   field="speeds_rpm")
    temps = {k: v for k, v in (temps or {}).items() if v is not None}
    t_max = np.full((len(vd), len(sp)), np.nan)
    t_min = np.full((len(vd), len(sp)), np.nan)
    evs = {}
    with progress.span(len(vd) * len(sp), "full-load curves") as span:
        for a, V in enumerate(vd):
            for j, n in enumerate(sp):
                span.step()
                ev = evs[a, j] = PolicyEvaluator(drive, Scenario("export", float(n), float(V), limits, **temps))
                cmax = policy_capability(ev, 1, certify=False).value_Nm
                cmin = policy_capability(ev, -1, certify=False).value_Nm
                t_max[a, j] = np.nan if cmax is None else float(cmax)
                t_min[a, j] = np.nan if cmin is None else float(cmin)
    if torques_Nm is None:
        step = float(torque_step_Nm or 25.0)
        if not (step > 0) or not np.isfinite(t_max).any():
            raise InputValidationError("no full-load torque established: give the torque grid", field="torques_Nm")
        hi = math.ceil(np.nanmax(t_max) / step) * step
        lo = math.floor(np.nanmin(t_min) / step) * step if np.isfinite(t_min).any() else -hi
        torques_Nm = list(np.round(np.arange(lo, hi + 0.5 * step, step), 9))
    tq = _grid(torques_Nm, "torques_Nm")
    shape = (len(vd), len(tq), len(sp))
    tables = {k: np.full(shape, np.nan) for k in QUANTITIES}
    status = np.full(shape, STATUS_CODE["UNKNOWN"], dtype=int)
    unevaluated: set = set()
    parts = ("loss_inverter_W", "loss_motor_W") + (("loss_reducer_W",) if reducer is not None else ())
    with progress.span(len(vd) * len(sp), "simulator maps") as span:
        for a, V in enumerate(vd):
            for j, n in enumerate(sp):
                span.step()
                ev = evs[a, j]
                cmax = None if not np.isfinite(t_max[a, j]) else t_max[a, j]
                cmin = None if not np.isfinite(t_min[a, j]) else t_min[a, j]
                for i, T in enumerate(tq):
                    if (cmax is not None and T > cmax + 1e-9) or (cmin is not None and T < cmin - 1e-9):
                        status[a, i, j] = STATUS_CODE["BEYOND"]
                        continue
                    sol = ev.solve(float(T))
                    st = sol.policy_claim.status.value
                    status[a, i, j] = STATUS_CODE.get(st, STATUS_CODE["UNKNOWN"])
                    if sol.point is None or st != "FEASIBLE":
                        continue
                    led = point_ledger(sol.point, drive, reducer, oil_temp_C)
                    ports, bnd = led["ports_W"], led["boundaries"]
                    for key, port in (("P_dc_W", "P_dc"), ("P_ac_W", "P_ac"), ("P_shaft_W", "P_m"), ("P_out_W", "P_o")):
                        if ports.get(port) is not None:
                            tables[key][a, i, j] = ports[port]
                    for key, b in (("loss_inverter_W", "inverter"), ("loss_motor_W", "motor"),
                                   ("loss_reducer_W", "reducer")):
                        # the loss is defined where the efficiency is not (standstill holding torque, zero torque):
                        # only an UNKNOWN boundary has no loss
                        r = bnd.get(b) or {}
                        if r.get("loss_W") is not None and r.get("status") in (DEFINED, NA):
                            tables[key][a, i, j] = r["loss_W"]
                    # the total only where every modelled part has its loss: the subtotal without the reducer
                    # (outside its declared range) read as a LOWER loss right where the reducer is loaded hardest
                    if all(np.isfinite(tables[k][a, i, j]) for k in parts):
                        tables["loss_total_W"][a, i, j] = led["loss_known_subtotal_W"]
                    tables["id_A"][a, i, j] = sol.point.id_A
                    tables["iq_A"][a, i, j] = sol.point.iq_A
                    unevaluated.update(led.get("loss_unknown_items") or [])
    if reducer is None:
        tables["P_out_W"][:] = np.nan
        tables["loss_reducer_W"][:] = np.nan
    inside = status == STATUS_CODE["FEASIBLE"]
    skip = ("P_out_W", "loss_reducer_W") if reducer is None else ()
    holes = {k: int((inside & ~np.isfinite(t)).sum()) for k, t in tables.items() if k not in skip}
    holes = {k: n for k, n in holes.items() if n}
    why = ""
    if holes and reducer is not None:
        r = reducer.describe()
        rng = "; ".join(f"{k} {v[0]:g} to {v[1]:g}" for k, v in r.items()
                        if k in ("torque_Nm", "speed_rpm", "oil_temp_C") and isinstance(v, (list, tuple)) and len(v) == 2)
        why = f"the reducer model gives no loss outside its declared range ({rng})"
    from .. import __version__
    meta = {"schema": "twb-simmaps/1", "software": f"traction-workbench {__version__}",
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": dict(source or {}), "temperatures": temps, "oil_temp_C": oil_temp_C,
            "reducer": None if reducer is None else reducer.describe(),
            "conventions": {"torque": "motor shaft torque, > 0 motoring, < 0 generating",
                            "speed": "motor speed >= 0 (rpm)", "power": "> 0 flows from the battery to the wheels",
                            "table_order": "[Vdc][torque][speed]"},
            "status_codes": STATUS_CODE,
            "not_evaluated": sorted(unevaluated),
            "empty_in_envelope": holes,
            "empty_in_envelope_why": why,
            "policy": "minimum-current policy point (steady state) at each cell",
            "quantities": {k: {"unit": u, "meaning": m} for k, (u, m) in QUANTITIES.items()}}
    return {"speeds_rpm": sp, "torques_Nm": tq, "Vdc_V": vd, "tables": tables, "status": status,
            "T_max_Nm": t_max, "T_min_Nm": t_min, "meta": meta,
            "counts": {k: int((status == c).sum()) for k, c in STATUS_CODE.items()}}


def fill_nearest(table: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A copy with every invalid cell set to the nearest valid cell of the same voltage slice (grid-index distance);
    the mask is True where a value was filled.  A slice without a valid cell stays NaN."""
    out = np.array(table, dtype=float)
    filled = np.zeros(out.shape, dtype=bool)
    for a in range(out.shape[0]):
        ok = np.argwhere(valid[a])
        if ok.size == 0:
            continue
        for i, j in np.argwhere(~valid[a]):
            d = (ok[:, 0] - i) ** 2 + (ok[:, 1] - j) ** 2
            k = int(np.argmin(d))
            out[a, i, j] = table[a, ok[k, 0], ok[k, 1]]
            filled[a, i, j] = True
    return out, filled


def _nan_to_none(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def maps_jsonable(maps: dict) -> dict:
    """The maps as JSON (NaN as null) - what the API returns and the page draws."""
    def arr(a):
        return [[_nan_to_none(float(v)) for v in row] for row in a] if np.ndim(a) == 2 else \
            [[[_nan_to_none(float(v)) for v in row] for row in s] for s in a]
    return {"speeds_rpm": maps["speeds_rpm"], "torques_Nm": maps["torques_Nm"], "Vdc_V": maps["Vdc_V"],
            "tables": {k: arr(v) for k, v in maps["tables"].items()},
            "status": np.asarray(maps["status"]).tolist(), "T_max_Nm": arr(maps["T_max_Nm"]),
            "T_min_Nm": arr(maps["T_min_Nm"]), "meta": maps["meta"], "counts": maps["counts"]}


def maps_from_json(d: dict) -> dict:
    """The JSON form back to arrays (null as NaN)."""
    def arr(x):
        return np.array([[np.nan if v is None else v for v in row] for row in x], dtype=float) if \
            not (x and x[0] and isinstance(x[0][0], list)) else \
            np.array([[[np.nan if v is None else v for v in row] for row in s] for s in x], dtype=float)
    return {"speeds_rpm": list(d["speeds_rpm"]), "torques_Nm": list(d["torques_Nm"]), "Vdc_V": list(d["Vdc_V"]),
            "tables": {k: arr(v) for k, v in d["tables"].items()}, "status": np.array(d["status"], dtype=int),
            "T_max_Nm": arr(d["T_max_Nm"]), "T_min_Nm": arr(d["T_min_Nm"]), "meta": d["meta"],
            "counts": d.get("counts", {})}


# ------------------------------------------------------------------------------------------------- writers

def write_csv_long(maps: dict, path) -> Path:
    path = Path(path)
    keys = list(QUANTITIES)
    names = {v: k for k, v in STATUS_CODE.items()}
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Vdc_V", "speed_rpm", "torque_Nm", "status", "T_max_Nm", "T_min_Nm"] + keys)
        for a, V in enumerate(maps["Vdc_V"]):
            for j, n in enumerate(maps["speeds_rpm"]):
                for i, T in enumerate(maps["torques_Nm"]):
                    row = [V, n, T, names[int(maps["status"][a, i, j])], maps["T_max_Nm"][a, j],
                           maps["T_min_Nm"][a, j]]
                    row += [maps["tables"][k][a, i, j] for k in keys]
                    w.writerow(["" if isinstance(x, float) and not math.isfinite(x) else
                                (f"{x:.10g}" if isinstance(x, float) else x) for x in row])
    return path


def write_csv_grids(maps: dict, folder) -> list[Path]:
    """One file per quantity and voltage: first row the speeds, first column the torques (blank = no value)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for a, V in enumerate(maps["Vdc_V"]):
        for k in list(QUANTITIES) + ["status"]:
            p = folder / f"{k}_{V:g}V.csv"
            tab = maps["status"][a] if k == "status" else maps["tables"][k][a]
            with p.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["torque_Nm \\ speed_rpm"] + [f"{n:g}" for n in maps["speeds_rpm"]])
                for i, T in enumerate(maps["torques_Nm"]):
                    w.writerow([f"{T:g}"] + ["" if (isinstance(x, float) and not math.isfinite(x)) else f"{x:.10g}"
                                             for x in (float(v) for v in tab[i])])
            out.append(p)
        p = folder / f"full_load_{V:g}V.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["speed_rpm", "T_max_Nm", "T_min_Nm"])
            for j, n in enumerate(maps["speeds_rpm"]):
                w.writerow([f"{n:g}"] + ["" if not math.isfinite(x) else f"{x:.10g}"
                                         for x in (maps["T_max_Nm"][a, j], maps["T_min_Nm"][a, j])])
        out.append(p)
    (folder / "README.txt").write_text(_readme(maps), encoding="utf-8")
    out.append(folder / "README.txt")
    return out


def _readme(maps: dict) -> str:
    m = maps["meta"]
    lines = [f"Traction Workbench simulator maps ({m['schema']}), {m['software']}, {m['created']}",
             f"source: {json.dumps(m.get('source') or {}, ensure_ascii=False)}",
             "cells: minimum-current policy points (steady state); blank = no value (status INFEASIBLE / UNKNOWN / "
             "BEYOND the full-load curve) - clamp the torque request to full_load_*.csv first",
             "conventions: " + "; ".join(f"{k}: {v}" for k, v in m["conventions"].items()),
             "losses not evaluated by the model (not in the tables): " + (", ".join(m["not_evaluated"]) or "none")]
    if m.get("empty_in_envelope"):
        lines.append("blank inside the delivered envelope (a part without a loss there - "
                     f"{m.get('empty_in_envelope_why') or 'see the metadata'}): "
                     + ", ".join(f"{k} {n} cell(s)" for k, n in m["empty_in_envelope"].items()))
    for k, q in m["quantities"].items():
        lines.append(f"  {k} [{q['unit']}]: {q['meaning']}")
    return "\n".join(lines) + "\n"


def write_mat(maps: dict, path) -> Path:
    """MATLAB v5 .mat: ``twb_maps`` struct - breakpoints, [torque x speed x Vdc] tables (NaN = no value), status,
    full-load curves [Vdc x speed], and the metadata as JSON text."""
    from scipy.io import savemat
    path = Path(path)
    def tsv(a):                                          # [Vdc][T][n] -> [T][n][Vdc] (MATLAB lookup order)
        return np.moveaxis(np.asarray(a, dtype=float), 0, -1)
    s = {"speed_rpm": np.asarray(maps["speeds_rpm"], dtype=float),
         "torque_Nm": np.asarray(maps["torques_Nm"], dtype=float),
         "Vdc_V": np.asarray(maps["Vdc_V"], dtype=float),
         "status": tsv(maps["status"]),
         "T_max_Nm": np.asarray(maps["T_max_Nm"], dtype=float),
         "T_min_Nm": np.asarray(maps["T_min_Nm"], dtype=float),
         "meta_json": json.dumps(maps["meta"], ensure_ascii=False),
         "table_order": "[torque x speed x Vdc]; T_max_Nm / T_min_Nm: [Vdc x speed]"}
    for k in QUANTITIES:
        s[k] = tsv(maps["tables"][k])
    savemat(str(path), {"twb_maps": s}, do_compression=True)
    return path


# ------------------------------------------------------------------------------------------------- FMU

def _c_array(name: str, a) -> str:
    flat = np.asarray(a, dtype=float).ravel()
    body = ",".join("NAN" if math.isnan(v) else f"{v:.17g}" for v in flat)
    return f"static const double {name}[{max(1, flat.size)}] = {{{body if flat.size else '0'}}};\n"


_C_TEMPLATE = r"""/* Generated by Traction Workbench: a map-based drive model (FMI 2.0, Model Exchange and Co-Simulation).
   The torque request is clamped to the full-load curve of the present speed and DC voltage, then the tables are
   interpolated (linear in speed, torque and voltage; outside the grid the edge value).  Table cells outside the
   delivered envelope were filled with the nearest feasible cell (see the model description); a cell inside it
   without a value is NAN and so is every output that interpolates with it.  */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "fmi2Functions.h"

#define NS %(ns)d
#define NT %(nt)d
#define NV %(nv)d
#define NOUT %(nout)d
#define NVARS %(nvars)d

%(arrays)s
static const double *OUT_TABLES[NOUT] = {%(out_tables)s};

typedef struct {
    double x[NVARS];
    fmi2CallbackFunctions cb;
    fmi2String name;
    int dirty;
} Inst;

/* value references: 0 speed_rpm, 1 torque_request_Nm, 2 Vdc_V (inputs); 3 torque_Nm, 4 T_max_Nm, 5 T_min_Nm,
   6 limited, 7.. the table outputs */

static void bracket(const double *g, int n, double v, int *k, double *w) {
    if (n < 2 || v <= g[0]) { *k = 0; *w = 0.0; return; }
    if (v >= g[n - 1]) { *k = n - 2; *w = 1.0; return; }
    int lo = 0, hi = n - 1;
    while (hi - lo > 1) { int m = (lo + hi) / 2; if (g[m] <= v) lo = m; else hi = m; }
    *k = lo; *w = (v - g[lo]) / (g[lo + 1] - g[lo]);
}

/* w = 0 or 1 never reads the other end: it may be NAN (a cell without a value) and 0 * NAN is NAN */
static double lerp(double y0, double y1, double w) { return w <= 0.0 ? y0 : (w >= 1.0 ? y1 : y0 + w * (y1 - y0)); }

static double curve(const double *c, int a, int j, double ws) {       /* [NV][NS] */
    return lerp(c[a * NS + j], c[a * NS + (NS > 1 ? j + 1 : j)], ws);
}

static double table(const double *t, int a, int i, int j, double wt, double ws) {   /* [NV][NT][NS] */
    int i1 = NT > 1 ? i + 1 : i, j1 = NS > 1 ? j + 1 : j;
    double y0 = lerp(t[(a * NT + i) * NS + j], t[(a * NT + i) * NS + j1], ws);
    double y1 = lerp(t[(a * NT + i1) * NS + j], t[(a * NT + i1) * NS + j1], ws);
    return lerp(y0, y1, wt);
}

static void update(Inst *s) {
    int js, a; double ws, wv;
    double n = s->x[0], v = s->x[2];
    /* the tables are for forward rotation; reverse rotation is the mirror (-n, -T) <-> (n, T): powers and losses
       keep their sign (T * omega), the full-load limits swap */
    double sg = n < 0.0 ? -1.0 : 1.0;
    double treq = sg * s->x[1];
    bracket(SPEED, NS, fabs(n), &js, &ws);
    bracket(VDC, NV, v, &a, &wv);
    int a1 = NV > 1 ? a + 1 : a;
    double tmax = lerp(curve(TMAX, a, js, ws), curve(TMAX, a1, js, ws), wv);
    double tmin = lerp(curve(TMIN, a, js, ws), curve(TMIN, a1, js, ws), wv);
    double t = treq > tmax ? tmax : (treq < tmin ? tmin : treq);
    int it; double wt;
    bracket(TORQUE, NT, t, &it, &wt);
    s->x[3] = sg * t; s->x[4] = sg > 0 ? tmax : -tmin; s->x[5] = sg > 0 ? tmin : -tmax;
    s->x[6] = (t != treq) ? 1.0 : 0.0;
    for (int k = 0; k < NOUT; k++) {
        s->x[7 + k] = lerp(table(OUT_TABLES[k], a, it, js, wt, ws), table(OUT_TABLES[k], a1, it, js, wt, ws), wv);
    }
    s->dirty = 0;
}

const char* fmi2GetTypesPlatform(void) { return fmi2TypesPlatform; }
const char* fmi2GetVersion(void) { return fmi2Version; }
fmi2Status fmi2SetDebugLogging(fmi2Component c, fmi2Boolean on, size_t n, const fmi2String cat[]) { return fmi2OK; }

fmi2Component fmi2Instantiate(fmi2String name, fmi2Type type, fmi2String guid, fmi2String res,
                              const fmi2CallbackFunctions* cb, fmi2Boolean vis, fmi2Boolean log) {
    if (!guid || strcmp(guid, "%(guid)s") != 0) return NULL;
    Inst *s = (Inst*)calloc(1, sizeof(Inst));
    if (!s) return NULL;
    if (cb) s->cb = *cb;
    s->name = name;
    s->x[0] = %(n0)s; s->x[1] = 0.0; s->x[2] = %(v0)s;
    s->dirty = 1;
    return (fmi2Component)s;
}
void fmi2FreeInstance(fmi2Component c) { free(c); }
fmi2Status fmi2SetupExperiment(fmi2Component c, fmi2Boolean tol, fmi2Real tl, fmi2Real t0, fmi2Boolean stop, fmi2Real t1)
{ return fmi2OK; }
fmi2Status fmi2EnterInitializationMode(fmi2Component c) { return fmi2OK; }
fmi2Status fmi2ExitInitializationMode(fmi2Component c) { ((Inst*)c)->dirty = 1; return fmi2OK; }
fmi2Status fmi2Terminate(fmi2Component c) { return fmi2OK; }
fmi2Status fmi2Reset(fmi2Component c) { Inst *s = (Inst*)c; s->x[0] = %(n0)s; s->x[1] = 0.0; s->x[2] = %(v0)s;
    s->dirty = 1; return fmi2OK; }

fmi2Status fmi2GetReal(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, fmi2Real value[]) {
    Inst *s = (Inst*)c;
    if (s->dirty) update(s);
    for (size_t i = 0; i < nvr; i++) { if (vr[i] >= NVARS) return fmi2Error; value[i] = s->x[vr[i]]; }
    return fmi2OK;
}
fmi2Status fmi2SetReal(fmi2Component c, const fmi2ValueReference vr[], size_t nvr, const fmi2Real value[]) {
    Inst *s = (Inst*)c;
    for (size_t i = 0; i < nvr; i++) { if (vr[i] > 2) return fmi2Error; s->x[vr[i]] = value[i]; }
    s->dirty = 1;
    return fmi2OK;
}
fmi2Status fmi2GetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Integer v[]) { return n ? fmi2Error : fmi2OK; }
fmi2Status fmi2GetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2Boolean v[]) { return n ? fmi2Error : fmi2OK; }
fmi2Status fmi2GetString(fmi2Component c, const fmi2ValueReference vr[], size_t n, fmi2String v[]) { return n ? fmi2Error : fmi2OK; }
fmi2Status fmi2SetInteger(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Integer v[]) { return n ? fmi2Error : fmi2OK; }
fmi2Status fmi2SetBoolean(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Boolean v[]) { return n ? fmi2Error : fmi2OK; }
fmi2Status fmi2SetString(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2String v[]) { return n ? fmi2Error : fmi2OK; }
fmi2Status fmi2GetFMUstate(fmi2Component c, fmi2FMUstate* st) { return fmi2Error; }
fmi2Status fmi2SetFMUstate(fmi2Component c, fmi2FMUstate st) { return fmi2Error; }
fmi2Status fmi2FreeFMUstate(fmi2Component c, fmi2FMUstate* st) { return fmi2Error; }
fmi2Status fmi2SerializedFMUstateSize(fmi2Component c, fmi2FMUstate st, size_t *n) { return fmi2Error; }
fmi2Status fmi2SerializeFMUstate(fmi2Component c, fmi2FMUstate st, fmi2Byte b[], size_t n) { return fmi2Error; }
fmi2Status fmi2DeSerializeFMUstate(fmi2Component c, const fmi2Byte b[], size_t n, fmi2FMUstate* st) { return fmi2Error; }
fmi2Status fmi2GetDirectionalDerivative(fmi2Component c, const fmi2ValueReference u[], size_t nu,
    const fmi2ValueReference z[], size_t nz, const fmi2Real dv[], fmi2Real df[]) { return fmi2Error; }
/* Model Exchange: no continuous states, no event indicators - every output is an algebraic function of the inputs */
fmi2Status fmi2EnterEventMode(fmi2Component c) { return fmi2OK; }
fmi2Status fmi2NewDiscreteStates(fmi2Component c, fmi2EventInfo* e) {
    e->newDiscreteStatesNeeded = fmi2False; e->terminateSimulation = fmi2False;
    e->nominalsOfContinuousStatesChanged = fmi2False; e->valuesOfContinuousStatesChanged = fmi2False;
    e->nextEventTimeDefined = fmi2False; e->nextEventTime = 0.0; return fmi2OK; }
fmi2Status fmi2EnterContinuousTimeMode(fmi2Component c) { return fmi2OK; }
fmi2Status fmi2CompletedIntegratorStep(fmi2Component c, fmi2Boolean nse, fmi2Boolean* em, fmi2Boolean* ts) {
    *em = fmi2False; *ts = fmi2False; return fmi2OK; }
fmi2Status fmi2SetTime(fmi2Component c, fmi2Real t) { return fmi2OK; }
fmi2Status fmi2SetContinuousStates(fmi2Component c, const fmi2Real x[], size_t nx) { return nx ? fmi2Error : fmi2OK; }
fmi2Status fmi2GetDerivatives(fmi2Component c, fmi2Real d[], size_t nx) { return nx ? fmi2Error : fmi2OK; }
fmi2Status fmi2GetEventIndicators(fmi2Component c, fmi2Real ei[], size_t ni) { return ni ? fmi2Error : fmi2OK; }
fmi2Status fmi2GetContinuousStates(fmi2Component c, fmi2Real x[], size_t nx) { return nx ? fmi2Error : fmi2OK; }
fmi2Status fmi2GetNominalsOfContinuousStates(fmi2Component c, fmi2Real x[], size_t nx) { return nx ? fmi2Error : fmi2OK; }
/* Co-Simulation: a step only advances time (the outputs follow the inputs) */
fmi2Status fmi2SetRealInputDerivatives(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Integer o[],
    const fmi2Real v[]) { return fmi2Error; }
fmi2Status fmi2GetRealOutputDerivatives(fmi2Component c, const fmi2ValueReference vr[], size_t n, const fmi2Integer o[],
    fmi2Real v[]) { return fmi2Error; }
fmi2Status fmi2DoStep(fmi2Component c, fmi2Real t, fmi2Real h, fmi2Boolean nsp) { return fmi2OK; }
fmi2Status fmi2CancelStep(fmi2Component c) { return fmi2Error; }
fmi2Status fmi2GetStatus(fmi2Component c, const fmi2StatusKind k, fmi2Status* v) { return fmi2Error; }
fmi2Status fmi2GetRealStatus(fmi2Component c, const fmi2StatusKind k, fmi2Real* v) { return fmi2Error; }
fmi2Status fmi2GetIntegerStatus(fmi2Component c, const fmi2StatusKind k, fmi2Integer* v) { return fmi2Error; }
fmi2Status fmi2GetBooleanStatus(fmi2Component c, const fmi2StatusKind k, fmi2Boolean* v) { return fmi2Error; }
fmi2Status fmi2GetStringStatus(fmi2Component c, const fmi2StatusKind k, fmi2String* v) { return fmi2Error; }
"""


def _headers_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "fmi2"


def _platform() -> tuple[str, str]:
    if sys.platform.startswith("win"):
        return "win64", ".dll"
    if sys.platform == "darwin":
        return "darwin64", ".dylib"
    return "linux64", ".so"


def find_compiler() -> str | None:
    for c in (("gcc", "cc", "clang") if not sys.platform.startswith("win") else ("gcc", "clang", "cl")):
        p = shutil.which(c)
        if p:
            return p
    return None


def _compile(src: Path, out: Path, compiler: str) -> str:
    inc = str(_headers_dir())
    if Path(compiler).name.lower().startswith("cl"):
        cmd = [compiler, "/nologo", "/O2", "/LD", f"/I{inc}", str(src), f"/Fe:{out}"]
    elif sys.platform == "darwin":
        cmd = [compiler, "-dynamiclib", "-O2", f"-I{inc}", str(src), "-o", str(out)]
    else:
        cmd = [compiler, "-shared", "-fPIC", "-O2", f"-I{inc}", str(src), "-o", str(out), "-lm"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"compiling the FMU binary failed: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return " ".join(Path(x).name if i == 0 else x for i, x in enumerate(cmd))


def write_fmu(maps: dict, path, model_name: str = "TwbDriveMaps", compile_binary: bool = True,
              n0: float | None = None) -> dict:
    """An FMI 2.0 FMU (Model Exchange + Co-Simulation) of the maps (see the module text).  Returns what was written:
    the platform binary when a C compiler is found (otherwise a source FMU - the importing tool compiles it)."""
    path = Path(path)
    ident = "".join(ch for ch in model_name if ch.isalnum() or ch == "_") or "TwbDriveMaps"
    if ident[0].isdigit():
        ident = "M" + ident
    sp, tq, vd = (np.asarray(maps[k], dtype=float) for k in ("speeds_rpm", "torques_Nm", "Vdc_V"))
    valid = np.asarray(maps["status"]) == STATUS_CODE["FEASIBLE"]
    tmax, tmin = np.asarray(maps["T_max_Nm"], dtype=float), np.asarray(maps["T_min_Nm"], dtype=float)
    if not valid.any() or not np.isfinite(tmax).any():
        raise InputValidationError("no feasible cell: nothing to put in an FMU", field="maps")
    for c in (tmax, tmin):                                # a speed without a curve takes the nearest speed's
        for a in range(c.shape[0]):
            ok = np.where(np.isfinite(c[a]))[0]
            for j in np.where(~np.isfinite(c[a]))[0]:
                c[a, j] = c[a, ok[np.argmin(np.abs(ok - j))]] if ok.size else 0.0
    outs, filled_any, holes = [], {}, {}
    for k in FMU_OUTPUTS:
        t = np.asarray(maps["tables"][k], dtype=float)
        ok = valid & np.isfinite(t)
        if not ok.any():
            continue
        f, mask = fill_nearest(t, ok)
        hole = valid & ~np.isfinite(t)              # inside the envelope without a value: stays NaN, never filled
        f[hole] = np.nan
        mask &= ~hole
        outs.append((k, f))
        filled_any[k] = int(mask.sum())
        if hole.any():
            holes[k] = int(hole.sum())
    guid = "{" + str(uuid.uuid4()) + "}"
    arrays = (_c_array("SPEED", sp) + _c_array("TORQUE", tq) + _c_array("VDC", vd) + _c_array("TMAX", tmax)
              + _c_array("TMIN", tmin) + "".join(_c_array(f"TAB_{i}", f) for i, (_k, f) in enumerate(outs)))
    src = _C_TEMPLATE % {"ns": len(sp), "nt": len(tq), "nv": len(vd), "nout": len(outs), "nvars": 7 + len(outs),
                         "arrays": arrays, "out_tables": ", ".join(f"TAB_{i}" for i in range(len(outs))),
                         "guid": guid, "n0": repr(float(n0 if n0 is not None else sp[0])), "v0": repr(float(vd[0]))}
    m = maps["meta"]
    hole_txt = ""
    if holes:
        why = m.get("empty_in_envelope_why") or "see the metadata"
        hole_txt = (f"NaN inside the envelope where the model gives no value ({why}): "
                    + ", ".join(f"{k} {n} cell(s)" for k, n in holes.items()) + ". ")
    desc = (f"Map-based drive model from Traction Workbench ({m['software']}, {m['created']}). Inputs: speed [rpm], "
            f"torque request [N*m], DC voltage [V]. The request is clamped to the full-load curve of the present speed "
            f"and voltage, then the tables (minimum-current policy points) are interpolated. Cells outside the "
            f"delivered envelope were filled with the nearest feasible cell for interpolation only. {hole_txt}"
            f"Losses not evaluated by the model: {', '.join(m['not_evaluated']) or 'none'}. Source: "
            f"{json.dumps(m.get('source') or {}, ensure_ascii=False)}")
    var = [("speed_rpm", "input", "rpm", "motor speed (< 0: reverse rotation, the mirrored map)", float(sp[0])),
           ("torque_request_Nm", "input", "N.m", "shaft torque request (> 0 motoring)", 0.0),
           ("Vdc_V", "input", "V", "DC-link voltage", float(vd[0])),
           ("torque_Nm", "output", "N.m", "delivered shaft torque (request clamped to the full-load curve)", None),
           ("T_max_Nm", "output", "N.m", "full-load motoring torque at this speed and voltage", None),
           ("T_min_Nm", "output", "N.m", "full-load generating torque at this speed and voltage", None),
           ("limited", "output", "1", "1 when the request was clamped", None)]
    var += [(k, "output", "W", maps["meta"]["quantities"][k]["meaning"], None) for k, _f in outs]
    import html
    sv = []
    for vr, (name, caus, unit, d, start) in enumerate(var):
        st = f' start="{start!r}"' if start is not None else ""
        sv.append(f'    <ScalarVariable name="{name}" valueReference="{vr}" causality="{caus}" '
                  f'variability="continuous" description="{html.escape(d)}"'
                  + (' initial="calculated"' if caus == "output" else "") + f'>\n      <Real unit="{unit}"{st}/>\n'
                  "    </ScalarVariable>")
    units = sorted({u for _n, _c, u, _d, _s in var})
    unit_defs = "\n".join(f'    <Unit name="{u}"/>' for u in units)
    outputs = "\n".join(f'      <Unknown index="{i + 1}" dependencies="1 2 3"/>' for i in range(3, len(var)))
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<fmiModelDescription fmiVersion="2.0" modelName="{ident}" guid="{guid}"
  description="{html.escape(desc)}" generationTool="{html.escape(m['software'])}"
  generationDateAndTime="{m['created']}" variableNamingConvention="flat" numberOfEventIndicators="0">
  <ModelExchange modelIdentifier="{ident}" canNotUseMemoryManagementFunctions="true">
    <SourceFiles><File name="{ident}.c"/></SourceFiles>
  </ModelExchange>
  <CoSimulation modelIdentifier="{ident}" canHandleVariableCommunicationStepSize="true"
    canNotUseMemoryManagementFunctions="true">
    <SourceFiles><File name="{ident}.c"/></SourceFiles>
  </CoSimulation>
  <UnitDefinitions>
{unit_defs}
  </UnitDefinitions>
  <ModelVariables>
{chr(10).join(sv)}
  </ModelVariables>
  <ModelStructure>
    <Outputs>
{outputs}
    </Outputs>
    <InitialUnknowns>
{outputs}
    </InitialUnknowns>
  </ModelStructure>
</fmiModelDescription>
"""
    built = None
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / f"{ident}.c").write_text(src, encoding="utf-8")
        plat, ext = _platform()
        compiler = find_compiler() if compile_binary else None
        cmd = None
        if compiler:
            (td / "bin").mkdir()
            cmd = _compile(td / f"{ident}.c", td / "bin" / f"{ident}{ext}", compiler)
            built = f"binaries/{plat}/{ident}{ext}"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("modelDescription.xml", xml)
            z.write(td / f"{ident}.c", f"sources/{ident}.c")
            z.writestr("documentation/README.txt", _readme(maps) + "\nFMU: " + desc + "\n")
            z.writestr("resources/maps.json", json.dumps(maps_jsonable(maps), ensure_ascii=False))
            if built:
                z.write(td / "bin" / f"{ident}{ext}", built)
    return {"path": str(path), "model_identifier": ident, "guid": guid, "binary": built,
            "compiler": cmd, "source_fmu": built is None, "outputs": [k for k, _f in outs],
            "filled_cells": filled_any, "empty_cells": holes,
            "note": None if built else "no C compiler found: a source FMU (the importing tool compiles sources/)"}
