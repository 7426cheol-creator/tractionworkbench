"""Numerical settings.

Numerical tolerances are kept separate from design reserves (r_v) and from
input uncertainty.  Every result records the settings it was produced with,
so the same inputs + settings reproduce the same result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class NumericalSettings:
    # Constraint classification: |slack| <= max(abs_floor, rel_tol*|limit|)
    # is ACTIVE (on the boundary within numerical tolerance), not a violation
    # and not a comfortable pass.
    constraint_rel_tol: float = 1e-9
    voltage_abs_tol_V: float = 1e-9
    current_abs_tol_A: float = 1e-9
    power_abs_tol_W: float = 1e-6
    torque_abs_tol_Nm: float = 1e-9
    speed_abs_tol_rpm: float = 1e-9

    # Zero thresholds used for energy-mode classification (units stated).
    power_zero_tol_W: float = 1e-6
    speed_zero_tol_rad_s: float = 1e-12

    # Acceptance numbers (02_Implementation_Handoff, H11).
    torque_residual_abs_Nm: float = 1e-3
    torque_residual_rel: float = 1e-6
    hard_violation_norm_max: float = 1e-7
    power_identity_abs_W: float = 0.01
    power_identity_rel: float = 1e-9
    capability_gap_abs_Nm: float = 0.1
    capability_gap_rel: float = 5e-4

    # Declared torque scale for relative tolerances.  None -> derived from the
    # drive (1.5*p*|psi|*Imax for constant models, map torque range otherwise).
    torque_scale_Nm: float | None = None

    # Search / sampling resolution.
    capability_scan_samples: int = 161
    capability_bisection_rel_tol: float = 1e-12
    physical_grid_points: int = 2001
    sampled_id_points: int = 801
    sampled_iq_points: int = 801
    bnb_max_depth: int = 7
    bnb_max_cells: int = 400_000

    def to_dict(self) -> dict:
        return asdict(self)

    def with_(self, **changes) -> "NumericalSettings":
        return replace(self, **changes)


DEFAULT_SETTINGS = NumericalSettings()
