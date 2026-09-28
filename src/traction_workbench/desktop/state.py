"""Application-wide state: the active PROJECT (system review R2) - one set of product data for every page - and, from
it, the active drive model and DC source limits.

Editing the drive or the limits on the model page does not detach them from the project: the edit becomes a
modified working copy of the project (``Project.with_section``), so every result still names the product data it
was computed from and results computed before the edit are marked stale.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import QObject, Signal

from .. import api
from .. import service as S
from ..errors import InputValidationError
from ..io import drive_from_dict
from ..models.components import DriveModel
from ..project import LIMIT_KEYS, Project, builtin_project
from ..scenario import DcSourceLimits  # noqa: F401 - the type of AppState.limits

BUILTIN_DRIVES = ("SYNTH_IPMSM_200KW_REF_V1", "MANUFACTURED_FLUX_MAP_TEST_DRIVE")


class AppState(QObject):
    drive_changed = Signal()
    project_changed = Signal()

    def __init__(self):
        super().__init__()
        self.project: Project = builtin_project()
        self.fallbacks: dict = {}               # example -> why the built-in example stood in (section missing)
        self._adopt(self.project)
        self.drive_source = "project"

    def _adopt(self, p: Project) -> None:
        spec = p.data("drive")
        self.drive: DriveModel = drive_from_dict(copy.deepcopy(spec))   # validates (units, definitions) first
        self.drive_spec = spec
        self.drive_label = spec.get("builtin") or self.drive.drive_id
        self.limits_dict = p.limits_dict()
        self.limits: DcSourceLimits = p.dc_limits()

    # -- project --------------------------------------------------------------
    def set_project(self, p: Project) -> None:
        """Switch the product data of every page (pages reload their product inputs; older results go stale)."""
        self._adopt(p)
        self.project = p
        self.fallbacks = {}
        self.drive_source = "project"
        self.project_changed.emit()
        self.drive_changed.emit()

    def example(self, name: str) -> dict:
        """Page example ``name`` with the ACTIVE project's product data.  A project without the section it needs
        gets the built-in example, and the fallback is recorded (results then report the component as not from the
        project)."""
        try:
            ex = api.example(name, self.project)
            self.fallbacks.pop(name, None)
            return ex
        except InputValidationError as exc:
            self.fallbacks[name] = str(exc)
            return api.example(name)

    # -- drive / limits (edits of the project's drive and dc_source sections) ---
    def set_drive(self, spec: dict, label: str | None = None, source: str = "builtin") -> None:
        p = self.project.with_section("drive", spec)             # validates the drive as a project section
        self._adopt(p)
        self.project = p
        self.drive_label = label or self.drive.drive_id
        self.drive_source = source
        self.project_changed.emit()
        self.drive_changed.emit()

    def set_limits(self, d: dict) -> None:
        clean = {k: (None if d.get(k) in (None, "") else float(d[k])) for k in LIMIT_KEYS}
        p = self.project.with_section("dc_source", {**self.project.data("dc_source"), "limits": clean})
        self._adopt(p)
        self.project = p
        self.project_changed.emit()
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
