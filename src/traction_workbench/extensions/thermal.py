"""Thermal screening linked to torque availability (roadmap C preview).

P_loss(operating point) -> T_node(t) -> time-to-limit and T_max(t):
each thermal node (e.g. inverter junction, stator winding) has a Foster
network Z_th(t) = sum R_i (1 - exp(-t / tau_i)) above the coolant, a
temperature limit and declared shares of the loss components (inverter,
copper, rotational) that heat it.  Starting from equilibrium at the coolant,
a constant loss P gives  T(t) = T_coolant + P * Z_th(t).

With a declared coolant loop (``extensions.coolant``) the reference is the
local fluid temperature of the node's station (the coolant heats up along the
loop by P / (m_dot * c_p)); without one it is the coolant inlet temperature
(infinite-flow assumption, reported as such).  Networks can be entered as
Foster (R_i, tau_i) or Cauer (R_i, C_i; converted exactly to Foster), and
stages may be declared flow-dependent: R_i(Q) = R_i,ref * (Q_ref / Q)^n.

Loss-temperature feedback, changing losses during the transient and
non-equilibrium initial states are not modelled here (``thermal_cycle``
adds repeated loads, hot starts and R_s(T) / T_j feedback on the same nodes).  A duration claim is
FEASIBLE/INFEASIBLE only when the thermal model is *qualified* for the stated
question (independent review F07): a ``validated`` flag is not evidence by
itself - it needs a validation-evidence reference, a declared validity domain
that covers the stated conditions (coolant, flow, speed, torque, ...), a start
that matches the model's initial state (equilibrium at the coolant) and a node
for every heat source that is present.  An empty network is invalid input.
Otherwise the numbers are a screening estimate and the duration claim stays
UNKNOWN.  The thermally available torque is scanned over the static policy
torque set and reported as a (possibly disconnected) set: a failure at zero
torque does not remove the rest of the set (F07b).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.linalg import eigh

from ..errors import InputValidationError
from ..models.components import DriveModel
from ..validation import finite as _finite
from ..models.provenance import Provenance
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..solvers.capability import policy_capability
from ..solvers.policy import PolicyEvaluator
from ..status import Claim, Evidence, EvidenceKind, Reason, Status
from .coolant import CoolantLoop

LOSS_KEYS = ("inverter", "copper", "rotational", "inverter_hottest_device")
COLD_STARTS = ("equilibrium_at_coolant", "coolant_equilibrium", "cold", "ambient")


@dataclass(frozen=True)
class FosterNetwork:
    R_K_per_W: tuple
    tau_s: tuple

    def __post_init__(self):
        r = tuple(_finite("R_K_per_W", x) for x in self.R_K_per_W)
        t = tuple(_finite("tau_s", x) for x in self.tau_s)
        if not r or len(r) != len(t) or any(x < 0 for x in r) or any(x <= 0 for x in t):
            raise InputValidationError("Foster network needs matching R >= 0 and tau > 0", field="foster")
        object.__setattr__(self, "R_K_per_W", r)
        object.__setattr__(self, "tau_s", t)

    def zth(self, t: float) -> float:
        if math.isinf(t):
            return sum(self.R_K_per_W)
        return sum(r * (1.0 - math.exp(-t / tau)) for r, tau in zip(self.R_K_per_W, self.tau_s))

    @classmethod
    def one_node(cls, R_K_per_W: float, C_J_per_K: float) -> "FosterNetwork":
        return cls((R_K_per_W,), (R_K_per_W * C_J_per_K,))

    def zth_array(self, t) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        out = np.zeros_like(t)
        for r, tau in zip(self.R_K_per_W, self.tau_s):
            out = out + r * (1.0 - np.exp(-t / tau))
        return out


def flow_scaled(R_K_per_W, flags, flow_ref_L_per_min, flow_L_per_min, exponent: float = 0.8) -> tuple:
    """R_i(Q) = R_i,ref * (Q_ref / Q)^n for the flagged stages (convective film; Dittus-Boelter-type h ~ Q^n)."""
    if not flags or not any(flags) or flow_ref_L_per_min is None or flow_L_per_min is None:
        return tuple(R_K_per_W)
    qr, q, n = (_finite("flow_ref_L_per_min", flow_ref_L_per_min), _finite("flow_L_per_min", flow_L_per_min),
                _finite("flow_exponent", exponent))
    if qr <= 0 or q <= 0 or not (0.0 <= n <= 2.0):
        raise InputValidationError("flow scaling needs Q_ref > 0, Q > 0 and 0 <= n <= 2", field="flow_exponent")
    k = (qr / q) ** n
    return tuple(r * k if f else r for r, f in zip(R_K_per_W, list(flags) + [False] * len(R_K_per_W)))


@dataclass(frozen=True)
class CauerNetwork:
    """Ladder from the junction (node 1) to the fluid: C_i from node i to the reference, R_i from node i to
    node i+1 (R_n to the fluid).  ``to_foster`` is exact: Z(s) = e1^T (sC + G)^-1 e1 diagonalised by the
    generalised eigenproblem G v = lambda C v."""

    R_K_per_W: tuple
    C_J_per_K: tuple

    def __post_init__(self):
        r = tuple(_finite("R_K_per_W", x) for x in self.R_K_per_W)
        c = tuple(_finite("C_J_per_K", x) for x in self.C_J_per_K)
        if not r or len(r) != len(c) or any(x <= 0 for x in r) or any(x <= 0 for x in c):
            raise InputValidationError("Cauer network needs matching R > 0 and C > 0", field="cauer")
        object.__setattr__(self, "R_K_per_W", r)
        object.__setattr__(self, "C_J_per_K", c)

    def modal(self) -> tuple:
        """(lambda, V, C) of G v = lambda C v with V^T C V = I, columns ordered by increasing tau = 1 / lambda."""
        n = len(self.R_K_per_W)
        G = np.zeros((n, n))
        for i, r in enumerate(self.R_K_per_W):
            g = 1.0 / r
            G[i, i] += g
            if i + 1 < n:
                G[i + 1, i + 1] += g
                G[i, i + 1] -= g
                G[i + 1, i] -= g
        C = np.diag(self.C_J_per_K)
        lam, V = eigh(G, C)                      # V^T C V = I
        order = np.argsort(1.0 / lam)
        return lam[order], V[:, order], C

    def to_foster(self) -> FosterNetwork:
        lam, V, _C = self.modal()
        return FosterNetwork(tuple(float(x) for x in V[0, :] ** 2 / lam), tuple(float(x) for x in 1.0 / lam))

    def foster_state(self, node_rise_K) -> np.ndarray:
        """Foster-term states (the to_foster ordering) of PHYSICAL node temperature rises above the fluid: the modal
        coordinates z = V^T C T, term i = V[0, i] z_i.  A Foster network alone has no physical inner nodes, so a
        hot start from measured layer temperatures needs this (Cauer) form."""
        T = np.asarray(node_rise_K, float)
        if T.shape != (len(self.R_K_per_W),) or not np.all(np.isfinite(T)):
            raise InputValidationError(f"a Cauer ladder with {len(self.R_K_per_W)} nodes needs that many node "
                                       f"temperatures (junction first)", field="node_temperatures")
        lam, V, C = self.modal()
        return V[0, :] * (V.T @ C @ T)


@dataclass(frozen=True)
class ThermalNode:
    node_id: str
    network: FosterNetwork
    limit_C: float
    loss_share: tuple            # (("inverter", 1/6), ("copper", 0.0), ...)
    station: str | None = None   # coolant-loop station whose fluid temperature is this node's reference
    cauer: CauerNetwork | None = None   # the declared ladder when entered as Cauer (physical inner nodes)

    def __post_init__(self):
        object.__setattr__(self, "limit_C", _finite("limit_C", self.limit_C))
        for k, v in self.loss_share:
            if k not in LOSS_KEYS or not (0 <= float(v) <= 1):
                raise InputValidationError(f"loss share {k}={v} invalid (keys {LOSS_KEYS}, 0..1)", field=self.node_id)

    def power(self, losses: dict) -> float:
        """Heat into the node; NaN when a declared heat source is not available (never read as zero)."""
        total = 0.0
        for k, v in self.loss_share:
            if float(v) == 0.0:
                continue
            if k not in losses or losses[k] is None or (isinstance(losses[k], float) and math.isnan(losses[k])):
                return math.nan
            total += float(v) * losses[k]
        return total


@dataclass(frozen=True)
class ThermalModel:
    model_id: str
    revision: str
    nodes: tuple
    provenance: Provenance
    validated: bool = False
    validity: tuple = ()          # (("coolant_temp_C", (60, 70)), ("coolant_flow_L_per_min", (8, 12)), ...)
    coolant: CoolantLoop | None = None
    validation_evidence: str = ""  # test report / document id + revision behind "validated"
    initial_state: str = "equilibrium_at_coolant"   # the only start the step response represents

    def __post_init__(self):
        if not self.nodes:
            raise InputValidationError("a thermal model needs at least one node: an empty network cannot support a "
                                       "duration claim (it would read as 'never exceeds')", field="nodes")
        if self.coolant is not None:
            names = set(self.coolant.station_names())
            for nd in self.nodes:
                if nd.station is not None and nd.station not in names:
                    raise InputValidationError(f"node {nd.node_id!r} references unknown coolant station {nd.station!r}",
                                               field="nodes.station")

    def reference_C(self, node: "ThermalNode", inlet_C: float, losses: dict, fluid: dict | None = None) -> float:
        """Fluid temperature the node's Z_th is referred to (inlet temperature without a coolant loop)."""
        if self.coolant is None or node.station is None:
            return float(inlet_C)
        fluid = fluid or self.coolant.fluid_temperatures(inlet_C, losses)
        return fluid[node.station]["T_ref_C"]

    def stated_conditions(self) -> dict:
        if self.coolant is None:
            return {}
        return {"coolant_flow_L_per_min": self.coolant.flow_L_per_min, "glycol_vol_pct": self.coolant.glycol_vol_pct}

    def conditions_ok(self, stated: dict) -> tuple[bool, list[str]]:
        problems = []
        for key, rng in self.validity:
            v = stated.get(key)
            if v is None:
                problems.append(f"{key} not stated")
            elif not (rng[0] <= v <= rng[1]):
                problems.append(f"{key}={v:g} outside {list(rng)}")
        return not problems, problems

    def qualification(self, stated: dict, initial_state: str | None, losses: dict | None = None) -> list[str]:
        """Everything that prevents a definite (FEASIBLE/INFEASIBLE) duration claim; empty list = qualified."""
        problems = []
        if not self.validated:
            problems.append("thermal model not declared validated")
        else:
            if not self.validation_evidence.strip():
                problems.append("'validated' is declared without a validation-evidence reference (a flag is not evidence)")
            if not self.validity:
                problems.append("no validity domain declared: a validated model must state where it was validated")
        ok, cond = self.conditions_ok(stated)
        problems += cond
        start = str(initial_state or "").strip().lower()
        if not start:
            problems.append("initial thermal state not stated (the model starts from equilibrium at the coolant)")
        elif start not in COLD_STARTS:
            problems.append(f"initial state {initial_state!r} is not modelled (only a start from equilibrium at the "
                            f"coolant; hot starts need the node temperatures as initial state)")
        if losses:
            for key, val in losses.items():
                if key == "inverter_hottest_device":
                    continue            # a subset of 'inverter', monitored through a device node when present
                if val > 0 and not any(dict(nd.loss_share).get(key, 0.0) > 0 or
                                       (key == "inverter" and dict(nd.loss_share).get("inverter_hottest_device", 0) > 0)
                                       for nd in self.nodes):
                    problems.append(f"{key} loss {val:.4g} W heats no node: the heat source is unmonitored")
            for nd in self.nodes:
                if dict(nd.loss_share).get("inverter_hottest_device", 0.0) > 0 and "inverter_hottest_device" not in losses:
                    problems.append(f"node {nd.node_id!r} needs the hottest-device loss, which only a device-level "
                                    f"(datasheet module) loss model provides - total/6 is not substituted")
        return problems


def temperature(node: ThermalNode, P_W: float, t_s: float, coolant_C: float) -> float:
    return coolant_C + P_W * node.network.zth(t_s)


def time_to_limit(node: ThermalNode, P_W: float, coolant_C: float) -> float:
    allow = node.limit_C - coolant_C
    if allow <= 0:
        return 0.0
    if P_W <= 0 or P_W * node.network.zth(math.inf) <= allow:
        return math.inf
    lo, hi = 0.0, max(node.network.tau_s)
    while P_W * node.network.zth(hi) < allow:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if P_W * node.network.zth(mid) < allow:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def _losses(pt) -> dict:
    """Heat sources at the operating point.  'inverter_hottest_device' exists only with a device-level (datasheet
    module) loss model; the total inverter loss divided by six is never substituted for it."""
    out = {"inverter": pt.Pinv_W or 0.0, "copper": pt.Pcu_W, "rotational": pt.Prot_W or 0.0}
    det = getattr(pt, "inverter_loss_detail", None)
    if det and det.get("established"):
        out["inverter_hottest_device"] = det["hottest_position_W"]
    return out


def thermal_duration(drive: DriveModel, scenario: Scenario, model: ThermalModel, T_request: float,
                     duration_s: float, settings: NumericalSettings = DEFAULT_SETTINGS) -> dict:
    if scenario.coolant_temp_C is None:
        raise InputValidationError("coolant temperature must be stated for a thermal evaluation",
                                   field="coolant_temp_C")
    d = _finite("duration_s", duration_s) if not (isinstance(duration_s, float) and math.isinf(duration_s)) else duration_s
    if d <= 0:
        raise InputValidationError("duration must be > 0 s", field="duration_s")
    sol = PolicyEvaluator(drive, scenario, settings).solve(T_request)
    q = f"{T_request:g} N*m at {scenario.speed_rpm:g} rpm for {duration_s:g} s"
    if sol.point is None or sol.policy_claim.status is not Status.FEASIBLE:
        claim = Claim("thermal_duration", sol.policy_claim.status if sol.policy_claim.status is Status.INFEASIBLE
                      else Status.UNKNOWN, q, "static policy not established", reasons=sol.policy_claim.reasons,
                      detail="the static operating point is not FEASIBLE, so no duration is evaluated")
        return {"claim": claim.to_dict(), "nodes": []}
    losses = _losses(sol.point)
    fluid = model.coolant.fluid_temperatures(scenario.coolant_temp_C, losses) if model.coolant is not None else None
    rows = []
    worst_t = math.inf
    violated = False
    missing = []
    for nd in model.nodes:
        p = nd.power(losses)
        ref = model.reference_C(nd, scenario.coolant_temp_C, losses, fluid)
        if math.isnan(p):
            missing.append(nd.node_id)
            rows.append({"node": nd.node_id, "power_W": None, "temperature_at_duration_C": None, "limit_C": nd.limit_C,
                         "time_to_limit_s": None, "steady_state_C": None, "fluid_reference_C": ref,
                         "station": nd.station, "note": "heat source not available at this point"})
            continue
        tt = time_to_limit(nd, p, ref)
        temp = temperature(nd, p, duration_s, ref)
        rows.append({"node": nd.node_id, "power_W": p, "temperature_at_duration_C": temp, "limit_C": nd.limit_C,
                     "time_to_limit_s": tt, "steady_state_C": temperature(nd, p, math.inf, ref),
                     "fluid_reference_C": ref, "station": nd.station})
        worst_t = min(worst_t, tt)
        violated |= temp > nd.limit_C
    stated = {"coolant_temp_C": scenario.coolant_temp_C, "Vdc_V": scenario.Vdc_V,
              "switching_frequency_Hz": scenario.switching_frequency_Hz, "speed_rpm": scenario.speed_rpm,
              "torque_Nm": T_request, **model.stated_conditions()}
    problems = model.qualification(stated, scenario.initial_state, losses)
    if missing:
        problems.append("heat source not available for node(s) " + ", ".join(missing)
                        + ": their temperature is not evaluated (never read as zero heat)")
    qualified = not problems
    ev = Evidence.make(EvidenceKind.VALIDATED_DOMAIN if qualified else EvidenceKind.SAMPLED,
                       f"{model.model_id} rev {model.revision}: sustainable for {worst_t:.4g} s at constant loss",
                       validated=model.validated, validation_evidence=model.validation_evidence,
                       provenance=model.provenance.to_dict())
    if qualified:
        st = Status.INFEASIBLE if violated else Status.FEASIBLE
        claim = Claim("thermal_duration", st, q, "qualified thermal model at matching conditions", None,
                      f"{duration_s:g} s", reasons=(Reason.CONSTRAINT_VIOLATION,) if violated else (), evidence=(ev,),
                      detail=f"time to the first node limit {worst_t:.4g} s")
    else:
        claim = Claim("thermal_duration", Status.UNKNOWN, q, "thermal screening estimate", None, f"{duration_s:g} s",
                      reasons=(Reason.UNVALIDATED_DURATION,), evidence=(ev,),
                      qualifiers=(f"screening estimate: {'exceeds' if violated else 'within'} limits "
                                  f"(first limit after {worst_t:.4g} s)",),
                      detail="; ".join(problems) + ": the estimate is not a duration rating")
    return {"claim": claim.to_dict(), "nodes": rows, "time_to_first_limit_s": worst_t,
            "qualification_problems": problems,
            "operating_point": {"id_A": sol.point.id_A, "iq_A": sol.point.iq_A, "losses_W": losses},
            "coolant": _coolant_report(model, fluid)}


def _coolant_report(model: ThermalModel, fluid: dict | None) -> dict:
    if model.coolant is None:
        return {"declared": False, "note": "no coolant loop declared: the node references are the coolant inlet "
                                           "temperature (infinite flow; the coolant temperature rise is ignored)"}
    return {"declared": True, **model.coolant.describe(), "fluid": fluid}


def torque_availability(drive: DriveModel, scenario: Scenario, model: ThermalModel,
                        durations_s=(1.0, 10.0, 30.0, 60.0, math.inf), direction: int = 1,
                        settings: NumericalSettings = DEFAULT_SETTINGS, samples: int = 41) -> dict:
    """Static-policy torques whose node temperatures stay within limits, per duration (sampled set).

    The torque axis is scanned over the static policy-feasible set; every duration gets the set of
    thermally feasible torques (possibly several segments) and its extreme value in ``direction``.
    Losses are not monotonic in torque (field-weakening current at zero torque), so a failure at one
    torque - zero included - says nothing about the others.
    """
    if scenario.coolant_temp_C is None:
        raise InputValidationError("coolant temperature must be stated", field="coolant_temp_C")
    ev = PolicyEvaluator(drive, scenario, settings)
    cap = policy_capability(ev, direction, certify=False)
    base = {"coolant_temp_C": scenario.coolant_temp_C, "validated": model.validated,
            "status_note": ("qualified thermal model" if not model.qualification(
                {"coolant_temp_C": scenario.coolant_temp_C, **model.stated_conditions()}, scenario.initial_state)
                            else "screening estimate: not a duration rating (model not qualified for this question)"),
            "coolant": _coolant_report(model, None)}
    if not cap.segments:
        return {**base, "static_capability_Nm": None, "static_segments_Nm": [], "rows": [],
                "note": "no static policy-feasible torque"}
    span = sum(b - a for a, b in cap.segments) or 1.0
    # one grid PER static segment (engineering review 6198099, F3): thermally feasible samples of two different static
    # segments are never joined - the torque between them was not statically feasible and was not examined
    seg_grids = []
    for a, b in cap.segments:
        n = max(3, int(math.ceil(samples * (b - a) / span)) + 1)
        seg_grids.append(sorted({float(x) for x in np.linspace(a, b, n)}))
    grid = sorted({T for g in seg_grids for T in g})
    cache: dict[float, dict | None] = {}

    def losses_at(T):
        if T not in cache:
            s = ev.solve(T)
            cache[T] = _losses(s.point) if (s.point is not None and s.policy_claim.status is Status.FEASIBLE) else None
        return cache[T]

    def ok_for(T, t):
        losses = losses_at(T)
        if losses is None:
            return None, None
        fluid = model.coolant.fluid_temperatures(scenario.coolant_temp_C, losses) if model.coolant is not None else None
        for nd in model.nodes:
            ref = model.reference_C(nd, scenario.coolant_temp_C, losses, fluid)
            pw = nd.power(losses)
            if math.isnan(pw):
                return None, nd.node_id          # heat source not available: not established, not "ok"
            if temperature(nd, pw, t, ref) > nd.limit_C:
                return False, nd.node_id
        return True, None

    def refine(a, b, t):
        """a thermally feasible, b not (both statically feasible, same static segment): bisect the boundary."""
        for _ in range(40):
            if abs(b - a) <= 1e-6 * max(1.0, abs(a)):
                break
            m = 0.5 * (a + b)
            g, _n = ok_for(m, t)
            if g is None:
                break
            a, b = (m, b) if g else (a, m)
        return a

    cap_ext = max((x for seg in cap.segments for x in seg), key=lambda x: direction * x)
    rows = []
    for t in durations_s:
        segs, owners, limiting, zero = [], [], None, None
        for k, (sg_grid, (s_lo, s_hi)) in enumerate(zip(seg_grids, cap.segments)):
            flags = [ok_for(T, t) for T in sg_grid]
            cur = None
            for i, (T, (g, node)) in enumerate(zip(sg_grid, flags)):
                if abs(T) <= 1e-9 and zero is None:
                    zero = g
                if g is False and node:
                    limiting = limiting or node
                if g:
                    if cur is None:
                        lo_T = T
                        if i > 0 and flags[i - 1][0] is False:
                            lo_T = refine(T, sg_grid[i - 1], t)
                        cur = [lo_T, T]
                    else:
                        cur[1] = T
                else:                              # False ends a segment at its bisected edge, None (UNKNOWN) at
                    if cur is not None:            # the last established sample - an UNKNOWN is never bridged
                        if g is False:
                            cur[1] = refine(sg_grid[i - 1], T, t)
                        segs.append(tuple(cur))
                        owners.append(k)
                        cur = None
            if cur is not None:
                segs.append(tuple(cur))
                owners.append(k)
        # invariant: thermal_feasible_set within static_feasible_set (each piece inside its own static segment)
        for (lo, hi), k in zip(segs, owners):
            a, b = cap.segments[k]
            tol = 1e-9 * max(1.0, abs(a), abs(b))
            if not (a - tol <= min(lo, hi) and max(lo, hi) <= b + tol):
                raise AssertionError(f"thermal segment [{lo}, {hi}] leaves static segment [{a}, {b}]")
        if not segs:
            rows.append({"duration_s": t, "torque_Nm": None, "feasible_segments_Nm": [],
                         "feasible_segment_static_index": [],
                         "limited_by": f"thermal node {limiting} at every examined torque" if limiting else
                                       "no statically feasible torque examined",
                         "zero_torque_feasible": zero})
            continue
        ext = max((x for seg in segs for x in seg), key=lambda x: direction * x)
        at_cap = abs(ext - cap_ext) <= 1e-6 * max(1.0, abs(cap_ext))
        rows.append({"duration_s": t, "torque_Nm": ext, "feasible_segments_Nm": [list(sg) for sg in segs],
                     "feasible_segment_static_index": owners,
                     "limited_by": "static capability" if at_cap else f"thermal node {limiting}",
                     "zero_torque_feasible": zero,
                     "disconnected": len(segs) > 1})
    return {
        **base,
        "static_capability_Nm": cap_ext,
        "static_segments_Nm": [list(sg) for sg in cap.segments],
        "rows": rows,
        "method": (f"sampled scan of {len(grid)} torques, per static policy segment (never joined across segments or "
                   f"UNKNOWN samples) + bisection of thermal boundaries inside a segment"),
        "assumptions": ["start from equilibrium at the coolant", "constant losses at the minimum-current point",
                        "no loss-temperature feedback", "declared loss shares per node",
                        ("coolant rise along the declared loop (m_dot*c_p)" if model.coolant is not None
                         else "no coolant loop: inlet temperature reference (infinite flow)"),
                        "sampled: narrow features between samples can be missed; the set may be disconnected"],
    }
