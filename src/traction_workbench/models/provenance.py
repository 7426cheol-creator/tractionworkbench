"""Data provenance and model fidelity.

Fidelity (D0..D3) and data origin (measured / supplier / FEA / estimated /
synthetic) are independent axes: having a D2 flux map does not mean the data
were validated on hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DataOrigin(str, Enum):
    MEASURED = "measured"
    SUPPLIER = "supplier"
    FEA = "fea"
    ESTIMATED = "estimated"
    SYNTHETIC = "synthetic"


class Fidelity(str, Enum):
    D0 = "D0"  # nameplate / a few points: necessary-condition screening only
    D1 = "D1"  # constant p, Rs, Ld, Lq, psi_PM with validity conditions
    D2 = "D2"  # dq flux maps + loss / allowed-region data
    D3 = "D3"  # independently validated dynamic / thermal models


FIDELITY_ALLOWED_CLAIMS = {
    Fidelity.D0: "necessary-condition checks between requirements; no detailed matching at new voltage/temperature",
    Fidelity.D1: "static dq calculation with constant parameters; not a hardware match over the saturated region",
    Fidelity.D2: "nonlinear static capability and matching; no time response, fault transition or thermal duration",
    Fidelity.D3: "duty/thermal/transient analysis inside the approved validation domain only",
}


@dataclass(frozen=True)
class Provenance:
    origin: DataOrigin
    source: str
    revision: str
    validation_status: str
    sha256: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        out = {
            "origin": self.origin.value,
            "source": self.source,
            "revision": self.revision,
            "validation_status": self.validation_status,
        }
        if self.sha256:
            out["sha256"] = self.sha256
        if self.notes:
            out["notes"] = list(self.notes)
        return out
