"""Independent review P0-B: data contract (frame fixed at import, symmetry seam, data-package audit)."""

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from traction_workbench import service
from traction_workbench import spec_fixtures as sf
from traction_workbench.errors import InputValidationError
from traction_workbench.io import drive_from_dict
from traction_workbench.models.flux import FluxMapModel, FluxMapPlane
from traction_workbench.units import Conversions

EX = Path(__file__).resolve().parents[1] / "examples" / "cases"


def _map_drive_dict(order="row=id, column=iq", transpose=False, **fm_extra):
    base = json.loads((EX / "req_ts_012_json_drive.json").read_text(encoding="utf-8"))["drive"]
    d = copy.deepcopy(base)
    ax = [-200.0, -100.0, 0.0]                      # square 3x3 map: shape cannot reveal a transpose
    D, Q = np.meshgrid(ax, [-100.0, 0.0, 100.0], indexing="ij")
    psd = 0.1 + 0.0002 * D + 0.00001 * Q            # asymmetric in (id, iq)
    psq = 0.0004 * Q + 0.00002 * D
    a, b = (psd.T, psq.T) if transpose else (psd, psq)
    plane = {"array_order": order,
             "id_axis": {"value": ax, "unit": "A", "basis": "fundamental_peak"},
             "iq_axis": {"value": [-100.0, 0.0, 100.0], "unit": "A", "basis": "fundamental_peak"},
             "psi_d": {"value": a.tolist(), "unit": "Wb"}, "psi_q": {"value": b.tolist(), "unit": "Wb"}}
    fm = {"planes": [plane], "axis_convention": "d_on_PM", "park": "amplitude_invariant"}
    fm.update(fm_extra)
    d["motor"] = {k: v for k, v in d["motor"].items() if k not in ("Ke", "Ld", "Lq")}
    d["motor"].update({"model": "flux_map", "flux_map": fm})
    return d, psd, psq


def test_square_map_in_row_iq_layout_is_transposed_explicitly():
    ref, psd, psq = _map_drive_dict()
    alt, _, _ = _map_drive_dict(order="row=iq, column=id", transpose=True)
    conv = Conversions()
    a = drive_from_dict(ref).motor.flux.planes[0]
    b = drive_from_dict(alt, conv).motor.flux.planes[0]
    assert np.array_equal(a.psi_d_Wb, b.psi_d_Wb) and np.array_equal(a.psi_q_Wb, b.psi_q_Wb)
    assert np.array_equal(a.psi_d_Wb, psd)
    assert any("transposed" in r["rule"] for r in conv.records)


@pytest.mark.parametrize("bad", [{"axis_convention": None}, {"axis_convention": "d_on_max_permeance"},
                                 {"park": None}, {"park": "power_invariant"}])
def test_frame_must_be_declared(bad):
    d, _, _ = _map_drive_dict()
    for k, v in bad.items():
        if v is None:
            d["motor"]["flux_map"].pop(k)
        else:
            d["motor"]["flux_map"][k] = v
    with pytest.raises(InputValidationError):
        drive_from_dict(d)


def test_undeclared_array_order_is_rejected():
    d, _, _ = _map_drive_dict(order="rows are id")
    with pytest.raises(InputValidationError):
        drive_from_dict(d)


def test_q_odd_symmetry_zero_seam():
    p = sf.manufactured_flux_plane()
    j0 = int(np.argmin(np.abs(p.iq_axis_A)))
    half = FluxMapPlane(p.id_axis_A, p.iq_axis_A[j0:], p.psi_d_Wb[:, j0:], p.psi_q_Wb[:, j0:], p.valid[:, j0:])
    FluxMapModel((half,), symmetry="q_odd")                       # psi_q(iq = 0) = 0: accepted
    bad_q = np.array(half.psi_q_Wb)
    bad_q[:, 0] += 0.01                                           # a seam: psi_q jumps at iq = 0
    bad = FluxMapPlane(half.id_axis_A, half.iq_axis_A, half.psi_d_Wb, bad_q, half.valid)
    with pytest.raises(InputValidationError, match="seam"):
        FluxMapModel((bad,), symmetry="q_odd")


def test_data_audit_states_supported_uses_separately():
    a = service.data_audit(sf.manufactured_map_drive())
    uses = {u["use"].split(" (")[0]: u["status"] for u in a["supported_uses"]}
    assert uses["static steady-state torque / voltage / current"].startswith("SUPPORTED")
    assert uses["dynamic transients"] == "NOT QUALIFIED"
    assert uses["demagnetisation"] == "NOT SUPPORTED" and uses["extrapolation outside the data"] == "NEVER"
    assert any("synthetic data" in g for g in a["qualification_gaps"])
