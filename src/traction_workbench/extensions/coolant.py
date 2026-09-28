"""Coolant loop for the thermal screening: EG/water properties, mass flow and temperature rise along the loop.

The coolant enters at the scenario's coolant temperature (inlet) and picks up the heat of each station in the
declared order (e.g. inverter cold plate, then motor water jacket):

    m_dot = rho * Q,     C_dot = m_dot * c_p     [W/K]
    T_in(k) = T_inlet + sum_{j<k} P_j / C_dot,    T_out(k) = T_in(k) + P_k / C_dot

A thermal node references the fluid temperature of its station (inlet, mean or outlet of that station).
Default fluid properties are typical generic ethylene-glycol/water values interpolated from a small table
(approximate; replace them with the coolant supplier's data for sign-off).  The coolant transit time and the
fluid's own heat capacity are neglected (the rise is applied immediately, which is conservative for heating).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..errors import InputValidationError
from ..validation import finite as _finite

LOSS_KEYS = ("inverter", "copper", "rotational")
REFERENCES = ("inlet", "mean", "outlet")

# ethylene glycol / water, % by volume; typical generic values (approximate)
GLYCOL_VOL_PCT = np.array([0.0, 30.0, 40.0, 50.0, 60.0])
TEMPS_C = np.array([0.0, 20.0, 40.0, 60.0, 80.0, 100.0])
CP_KJ_PER_KGK = np.array([
    [4.22, 4.18, 4.18, 4.18, 4.20, 4.22],
    [3.70, 3.75, 3.80, 3.85, 3.90, 3.95],
    [3.50, 3.56, 3.62, 3.68, 3.74, 3.80],
    [3.28, 3.35, 3.42, 3.49, 3.56, 3.63],
    [3.06, 3.14, 3.22, 3.30, 3.38, 3.46],
])
RHO_KG_PER_M3 = np.array([
    [1000.0, 998.0, 992.0, 983.0, 972.0, 958.0],
    [1045.0, 1040.0, 1032.0, 1022.0, 1010.0, 997.0],
    [1060.0, 1054.0, 1045.0, 1035.0, 1023.0, 1010.0],
    [1075.0, 1069.0, 1059.0, 1048.0, 1036.0, 1022.0],
    [1090.0, 1083.0, 1073.0, 1062.0, 1049.0, 1035.0],
])
PROPERTY_SOURCE = ("typical generic ethylene-glycol/water data by volume (approximate, interpolated); "
                   "replace with the coolant supplier's data for sign-off")


def _bilinear(table: np.ndarray, g: float, t: float) -> float:
    gi = np.clip(g, GLYCOL_VOL_PCT[0], GLYCOL_VOL_PCT[-1])
    ti = np.clip(t, TEMPS_C[0], TEMPS_C[-1])
    col = np.array([np.interp(ti, TEMPS_C, row) for row in table])
    return float(np.interp(gi, GLYCOL_VOL_PCT, col))


def eg_water_properties(glycol_vol_pct: float, T_C: float) -> dict:
    """Specific heat and density of an ethylene-glycol/water mixture (typical values, approximate)."""
    g = _finite("glycol_vol_pct", glycol_vol_pct)
    t = _finite("coolant_temp_C", T_C)
    if not (0.0 <= g <= 100.0):
        raise InputValidationError("glycol fraction must be 0..100 % by volume", field="glycol_vol_pct")
    clamped = not (GLYCOL_VOL_PCT[0] <= g <= GLYCOL_VOL_PCT[-1] and TEMPS_C[0] <= t <= TEMPS_C[-1])
    return {"cp_J_per_kgK": 1000.0 * _bilinear(CP_KJ_PER_KGK, g, t), "rho_kg_per_m3": _bilinear(RHO_KG_PER_M3, g, t),
            "glycol_vol_pct": g, "T_C": t, "clamped_to_table": clamped, "source": PROPERTY_SOURCE}


@dataclass(frozen=True)
class CoolantStation:
    name: str
    losses: tuple            # (("inverter", 1.0),): share of each loss category rejected into the fluid here

    def __post_init__(self):
        if not str(self.name).strip():
            raise InputValidationError("coolant station needs a name", field="coolant.loop")
        for k, v in self.losses:
            if k not in LOSS_KEYS or not (0.0 <= float(v) <= 1.0):
                raise InputValidationError(f"station {self.name}: loss share {k}={v} invalid (keys {LOSS_KEYS}, 0..1)",
                                           field="coolant.loop")

    def heat(self, losses: dict) -> float:
        return sum(float(v) * max(0.0, losses.get(k, 0.0)) for k, v in self.losses)


@dataclass(frozen=True)
class CoolantLoop:
    flow_L_per_min: float
    cp_J_per_kgK: float
    rho_kg_per_m3: float
    stations: tuple
    reference: str = "mean"
    glycol_vol_pct: float | None = None
    property_source: str = ""

    def __post_init__(self):
        for name in ("flow_L_per_min", "cp_J_per_kgK", "rho_kg_per_m3"):
            v = _finite(name, getattr(self, name))
            if v <= 0:
                raise InputValidationError(f"{name} must be > 0", field=f"coolant.{name}")
            object.__setattr__(self, name, v)
        if self.reference not in REFERENCES:
            raise InputValidationError(f"coolant reference must be one of {REFERENCES}", field="coolant.reference")
        names = [s.name for s in self.stations]
        if len(set(names)) != len(names):
            raise InputValidationError("coolant station names must be unique", field="coolant.loop")

    @property
    def mass_flow_kg_s(self) -> float:
        return self.rho_kg_per_m3 * self.flow_L_per_min / 60000.0

    @property
    def capacity_rate_W_per_K(self) -> float:
        return self.mass_flow_kg_s * self.cp_J_per_kgK

    def station_names(self) -> tuple:
        return tuple(s.name for s in self.stations)

    def fluid_temperatures(self, inlet_C: float, losses: dict) -> dict:
        cdot = self.capacity_rate_W_per_K
        t = float(inlet_C)
        out = {}
        for s in self.stations:
            p = s.heat(losses)
            t_out = t + p / cdot
            ref = {"inlet": t, "mean": 0.5 * (t + t_out), "outlet": t_out}[self.reference]
            out[s.name] = {"P_W": p, "T_in_C": t, "T_out_C": t_out, "T_ref_C": ref, "rise_K": t_out - t}
            t = t_out
        out["_loop"] = {"T_inlet_C": float(inlet_C), "T_outlet_C": t, "capacity_rate_W_per_K": cdot,
                        "mass_flow_kg_s": self.mass_flow_kg_s, "reference": self.reference}
        return out

    def describe(self) -> dict:
        return {"flow_L_per_min": self.flow_L_per_min, "glycol_vol_pct": self.glycol_vol_pct,
                "cp_J_per_kgK": self.cp_J_per_kgK, "rho_kg_per_m3": self.rho_kg_per_m3,
                "mass_flow_kg_s": self.mass_flow_kg_s, "capacity_rate_W_per_K": self.capacity_rate_W_per_K,
                "reference": self.reference, "stations": [{"name": s.name, "losses": dict(s.losses)} for s in self.stations],
                "property_source": self.property_source}
