"""Application-wide state: the active drive model and DC source limits."""

from __future__ import annotations

import copy

from PySide6.QtCore import QObject, Signal

from .. import service as S
from .. import spec_fixtures as sf
from ..io import drive_from_dict
from ..models.components import DriveModel
from ..scenario import DcSourceLimits

BUILTIN_DRIVES = ("SYNTH_IPMSM_200KW_REF_V1", "MANUFACTURED_FLUX_MAP_TEST_DRIVE")


class AppState(QObject):
    drive_changed = Signal()

    def __init__(self):
        super().__init__()
        self.drive_spec: dict = {"builtin": BUILTIN_DRIVES[0]}
        self.drive: DriveModel = drive_from_dict(self.drive_spec)
        self.drive_label = BUILTIN_DRIVES[0]
        self.drive_source = "builtin"
        base = sf.synthetic_limits()
        self.limits_dict = {"discharge_power_max_W": base.discharge_power_max_W,
                            "charge_power_max_W": base.charge_power_max_W,
                            "discharge_current_max_A": base.discharge_current_max_A,
                            "charge_current_max_A": base.charge_current_max_A}
        self.limits: DcSourceLimits = base

    # -- drive ----------------------------------------------------------------
    def set_drive(self, spec: dict, label: str | None = None, source: str = "builtin") -> None:
        drive = drive_from_dict(copy.deepcopy(spec))          # validates (units, definitions) before switching
        self.drive_spec = spec
        self.drive = drive
        self.drive_label = label or drive.drive_id
        self.drive_source = source
        self.drive_changed.emit()

    def set_limits(self, d: dict) -> None:
        clean = {k: (None if v in (None, "") else float(v)) for k, v in d.items()}
        self.limits = DcSourceLimits(clean.get("discharge_power_max_W"), clean.get("charge_power_max_W"),
                                     clean.get("discharge_current_max_A"), clean.get("charge_current_max_A"),
                                     source="desktop input")
        self.limits_dict = clean
        self.drive_changed.emit()

    # -- helpers ----------------------------------------------------------------
    def info(self) -> dict:
        return S.drive_info(self.drive)

    def speed_max(self) -> float:
        lo, hi = self.drive.domain.speed_rpm
        return max(abs(lo), abs(hi))

    def is_flux_map(self) -> bool:
        return type(self.drive.motor.flux).__name__ == "FluxMapModel"

    def body(self, **extra) -> dict:
        """Request body for ``traction_workbench.api`` functions (same validation path as case files)."""
        b = {"drive": self.drive_spec, "limits": self.limits_dict}
        b.update(extra)
        return b
