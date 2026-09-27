"""Requested-shaft-torque solve under the minimum-current operating policy.

Four claims are kept apart (Blueprint 1.2 #4, Handoff H5.2/H5.3):

* electrical_existence       - some (id, iq) in the declared domain gives the
                               requested shaft torque within voltage/current limits;
* policy_static              - the minimum-current policy (min Ipk^2 subject to
                               torque, voltage, current, domain) achieves the
                               request statically AND its operating point meets
                               the DC source limits (evaluated afterwards);
* dc_source                  - the DC-limit part of the above, at the policy point;
* physical_existence_with_dc - diagnostic: any control in the allowed domain
                               meeting every constraint incl. DC.  A candidate
                               that is DC-compatible only because losses are
                               raised on purpose is reported as an active-loss
                               candidate outside the default policy and is
                               never adopted.

The requested torque is never reduced or clipped.  A solver that finds nothing
is not evidence of infeasibility: INFEASIBLE comes only from exact
enumeration (constant model), rigorous cell bounds (flux maps) or analytic
necessary conditions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..errors import OutsideModelDomain
from ..models.components import DriveModel
from ..physics import DriveKernel, OperatingPoint, evaluate_point
from ..scenario import Scenario
from ..settings import DEFAULT_SETTINGS, NumericalSettings
from ..status import Claim, Evidence, EvidenceKind, Reason, Status
from .bounds import CellBounds
from .common import TIE_RULE, CurveAnalysis, CurvePoint, dc_band_I2, dc_ok
from .exact import analyze_constant, point_on_curve_with_I2
from .gate import DC_GROUPS, HARD_GROUPS, check_witness, missing_relevant_dc_limits
from .sampled import SampledCurveTracer
from .screens import ScreenResult, run_screens

POLICY_NAME = "minimum_current"
POLICY_TEXT = ("minimum-current policy: min Ipk^2 subject to shaft torque, voltage, current and declared domain; "
               "DC source limits evaluated at that point afterwards; losses are never raised deliberately")


def scope_text(k: DriveKernel) -> str:
    d = k.drive
    dom = d.domain
    return (f"model {d.drive_id} rev {d.revision} ({d.fidelity.value}, {d.provenance.origin.value} data); "
            f"declared domain id in [{dom.id_A[0]:g}, {dom.id_A[1]:g}] A, iq in [{dom.iq_A[0]:g}, {dom.iq_A[1]:g}] A, "
            f"|i| <= {k.Imax:g} A ({dom.kind}); n = {k.speed_rpm:g} rpm, Vdc = {k.Vdc:g} V; "
            f"static fundamental steady state")


@dataclass(frozen=True)
class PolicySolution:
    T_request_Nm: float
    scenario_id: str
    policy: str
    curve: CurveAnalysis | None
    point: OperatingPoint | None
    electrical: Claim
    policy_claim: Claim
    dc_claim: Claim | None
    physical_dc: Claim
    active_loss_candidate: OperatingPoint | None
    screens: tuple[ScreenResult, ...]
    certificates: tuple = ()
    acceptance: tuple = ()
    notes: tuple[str, ...] = ()
    model_issues: tuple = ()

    @property
    def claims(self) -> tuple[Claim, ...]:
        out = [self.electrical, self.policy_claim]
        if self.dc_claim is not None:
            out.append(self.dc_claim)
        out.append(self.physical_dc)
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "T_request_Nm": self.T_request_Nm,
            "scenario_id": self.scenario_id,
            "policy": self.policy,
            "policy_definition": POLICY_TEXT,
            "tie_rule": TIE_RULE,
            "claims": [c.to_dict() for c in self.claims],
            "operating_point": None if self.point is None else self.point.to_dict(),
            "active_loss_candidate": None if self.active_loss_candidate is None else {
                "status": "outside the default policy (deliberate loss increase is an MVP non-goal); not adopted",
                "operating_point": self.active_loss_candidate.to_dict()},
            "torque_curve_analysis": None if self.curve is None else self.curve.to_dict(),
            "necessary_condition_screens": [s.to_dict() for s in self.screens],
            "certificates": dict(self.certificates),
            "numerical_acceptance": dict(self.acceptance),
            "model_issues": [i.to_dict() for i in self.model_issues],
            "notes": list(self.notes),
        }


class PolicyEvaluator:
    """Minimum-current policy at one (drive, scenario); reusable for capability scans."""

    def __init__(self, drive: DriveModel, scenario: Scenario, settings: NumericalSettings = DEFAULT_SETTINGS):
        self.drive = drive
        self.scenario = scenario
        self.settings = settings
        self.k = DriveKernel(drive, scenario, settings)
        self._tracer = None
        self._bounds = None
        self._cache: dict[float, CurveAnalysis] = {}

    # -- lazily built helpers ------------------------------------------------

    @property
    def tracer(self) -> SampledCurveTracer:
        if self._tracer is None:
            self._tracer = SampledCurveTracer(self.k)
        return self._tracer

    @property
    def bounds(self) -> CellBounds:
        if self._bounds is None:
            self._bounds = CellBounds(self.k)
        return self._bounds

    @property
    def speed_in_domain(self) -> bool:
        lo, hi = self.drive.domain.speed_rpm
        tol = max(self.settings.speed_abs_tol_rpm, self.settings.constraint_rel_tol * max(abs(lo), abs(hi)))
        return lo - tol <= self.k.speed_rpm <= hi + tol

    def curve(self, T: float) -> CurveAnalysis:
        T = float(T)
        if T not in self._cache:
            if self.k.kind == "constant_dq":
                self._cache[T] = analyze_constant(self.k, T)
            else:
                self._cache[T] = self.tracer.analyze(T)
        return self._cache[T]

    # -- certification of the minimum-current point (shared by solve and quick_status) ----------

    def certify_min_point(self, T: float, curve: CurveAnalysis) -> dict:
        """Is the found point the *global* minimum-current point of the declared control domain?

        The control domain (declared id/iq/|i| limits) and the data domain (covered map cells or
        declared parameter validity) are different sets.  When the data do not cover the whole
        control domain, a lower-current point may lie where the model says nothing; the found point
        is certified only if every point with a smaller current is covered (|i_found| <= distance
        to the nearest uncovered allowed point).  For sampled tracing the cell-bound lower bound
        must also close the gap.  Same rule for the exact and the sampled method.
        """
        k = self.k
        mp = curve.min_point
        i_found = math.sqrt(mp.I2)
        cov_ok = (not curve.coverage_limited) or i_found <= curve.coverage_distance_A
        if curve.exact:
            i2_lb = mp.I2 if cov_ok else 0.0
            return {"method": "exact boundary enumeration", "certified": cov_ok, "coverage_ok": cov_ok,
                    "found_I_A": i_found, "lower_bound_I_A": math.sqrt(i2_lb), "i2_lb": i2_lb,
                    "coverage_distance_A": None if math.isinf(curve.coverage_distance_A) else curve.coverage_distance_A}
        lb = self.bounds.min_current_lower_bound(T + k.tau_rot_or_zero, mp.I2)
        i2_lb = lb.bound if lb.bound is not None else 0.0
        if not cov_ok:
            i2_lb = 0.0
        gap_A = i_found - math.sqrt(max(i2_lb, 0.0))
        certified = cov_ok and gap_A <= max(1e-3, 1e-3 * i_found)
        return {"method": "sampled tracing + cell-bound lower bound", "found_I_A": i_found,
                "lower_bound_I_A": math.sqrt(max(i2_lb, 0.0)), "gap_A": gap_A, "coverage_ok": cov_ok,
                "certified": certified, "i2_lb": i2_lb, "bnb": lb.to_dict(),
                "coverage_distance_A": None if math.isinf(curve.coverage_distance_A) else curve.coverage_distance_A}

    def _pdc_at(self, T: float, I2: float) -> float:
        k = self.k
        return (T + k.tau_rot_or_zero) * k.omega_m + (1.5 * k.Rs + k.inv_loss.ipk2_coeff_W_per_A2) * I2 \
            + k.inv_loss.offset_W

    def dc_status(self, T: float, I2_found: float, i2_lb: float, certified: bool) -> tuple[str, str]:
        """DC-limit status of the (possibly uncertified) policy point; P_dc is monotone in I^2 on the curve."""
        k = self.k
        if not k.limits.any_declared:
            return "UNKNOWN", "no DC source limits declared"
        pf = self._pdc_at(T, I2_found)
        ends = [pf] if certified else [pf, self._pdc_at(T, i2_lb)]
        oks = [bool(dc_ok(k, np.array([p]))[0]) for p in ends]
        if not any(oks):
            same_side = all(p > 0 for p in ends) or all(p < 0 for p in ends)
            if certified or same_side:
                return "INFEASIBLE", "violated at every admissible policy point"
            return "UNKNOWN", "violation not established for every admissible policy point"
        if not all(oks):
            return "UNKNOWN", "DC compatibility depends on where the uncertified policy point lies"
        miss = sorted({m for p in ends for m in missing_relevant_dc_limits(k, p)})
        if miss:
            return "UNKNOWN", ("DC limit(s) that can bind are not declared: " + ", ".join(miss)
                               + " (missing is not unlimited)")
        return "FEASIBLE", "within the declared DC limits"

    # -- fast status used by capability scans -----------------------------

    def quick_status(self, T: float) -> tuple[str, CurvePoint | None]:
        """FEASIBLE / INFEASIBLE / UNKNOWN of the policy incl. DC (no claims built).

        The same gate as ``solve``: model-validity issues, a minimum-current point that is not
        certified (uncovered data or an open cell-bound gap) and undeclared DC limits that can
        bind all give UNKNOWN, never FEASIBLE.
        """
        k = self.k
        if not self.speed_in_domain or not k.evaluable or k.issues:
            return "UNKNOWN", None
        try:
            c = self.curve(T)
        except Exception:  # noqa: BLE001 - a numerical failure is UNKNOWN, never a verdict
            return "UNKNOWN", None
        if c.empty:
            return ("INFEASIBLE" if c.exact and not c.coverage_limited else "UNKNOWN"), None
        p = c.min_point
        if k.inv_loss is None or k.tau_rot is None:
            return "UNKNOWN", p
        cert = self.certify_min_point(T, c)
        if not cert["certified"]:
            return "UNKNOWN", p
        st, _why = self.dc_status(T, p.I2, cert["i2_lb"], True)
        return st, p

    # -- full solve ----------------------------------------------------------

    def solve(self, T_request: float) -> PolicySolution:
        k = self.k
        s = self.settings
        T = float(T_request)
        scope = scope_text(k)
        notes: list[str] = list(k.notes)
        issues = tuple(k.issues)
        quantity = f"shaft torque {T:g} N*m at n = {k.speed_rpm:g} rpm"

        # speed outside the declared domain: no solve
        if not self.speed_in_domain:
            lo, hi = self.drive.domain.speed_rpm
            allowed = self.drive.domain.kind == "allowed_operating_limit"
            st = Status.INFEASIBLE if allowed else Status.UNKNOWN
            reason = Reason.OUTSIDE_ALLOWED_OPERATING_DOMAIN if allowed else Reason.OUTSIDE_MODEL_DOMAIN
            ev = Evidence.make(EvidenceKind.DIRECT_EVALUATION,
                               f"speed {k.speed_rpm:g} rpm outside declared [{lo:g}, {hi:g}] rpm", domain_kind=self.drive.domain.kind)
            detail = ("outside the declared allowed operating domain (a declared restriction, not a rotor-strength "
                      "or demagnetisation certification)" if allowed else "outside the model validity domain")
            mk = lambda name, q: Claim(name, st, q, scope, POLICY_NAME, reasons=(reason,), evidence=(ev,), detail=detail)
            return PolicySolution(T, self.scenario.scenario_id, POLICY_NAME, None, None,
                                  mk("electrical_existence", quantity), mk("policy_static", quantity), None,
                                  mk("physical_existence_with_dc", quantity), None, (), notes=tuple(notes),
                                  model_issues=issues)

        if not k.evaluable:
            reasons = tuple(dict.fromkeys(i.reason for i in issues)) or (Reason.OUTSIDE_MODEL_DOMAIN,)
            ev = Evidence.make(EvidenceKind.DIRECT_EVALUATION, "; ".join(i.message for i in issues))
            mk = lambda name: Claim(name, Status.UNKNOWN, quantity, scope, POLICY_NAME, reasons=reasons,
                                    evidence=(ev,), detail="model not available at this scenario")
            return PolicySolution(T, self.scenario.scenario_id, POLICY_NAME, None, None, mk("electrical_existence"),
                                  mk("policy_static"), None, mk("physical_existence_with_dc"), None, (),
                                  notes=tuple(notes), model_issues=issues)

        screens = run_screens(k, T)
        curve = self.curve(T)
        notes += list(curve.notes)
        conditional = bool(issues) or k.tau_rot is None
        cond_reasons = tuple(dict.fromkeys([i.reason for i in issues] +
                                           ([Reason.MISSING_INPUT] if k.tau_rot is None else [])))
        cond_q = ("assumed-data conditional",) if conditional else ()
        if k.tau_rot is None:
            notes.append("rotational loss model missing: the curve was traced for T_em = requested torque "
                         "(electromagnetic-torque screening); shaft-torque claims stay UNKNOWN")

        certs: dict = {}
        # ---------------- electrical existence ----------------
        elec_ev: list[Evidence] = []
        if not curve.empty:
            mp = curve.min_point
            elec_ev.append(Evidence.make(
                EvidenceKind.NUMERICAL_WITNESS, f"feasible point id = {mp.id_A:.6f} A, iq = {mp.iq_A:.6f} A",
                id_A=mp.id_A, iq_A=mp.iq_A))
            if curve.exact:
                elec_ev.append(Evidence.make(
                    EvidenceKind.EXACT_ENUMERATION,
                    "feasible set on the torque curve established from all constraint-polynomial roots",
                    feasible_id_intervals_A=[[sg.start.id_A, sg.end.id_A] for sg in curve.segments]))
            st = Status.UNKNOWN if conditional else Status.FEASIBLE
            electrical = Claim("electrical_existence", st, quantity + " within voltage/current/domain limits", scope,
                               None, reasons=cond_reasons, evidence=tuple(elec_ev), qualifiers=cond_q,
                               detail="a statically feasible electrical solution exists")
        else:
            electrical = self._empty_electrical(curve, screens, quantity, scope, conditional, cond_reasons, cond_q, certs)

        # ---------------- policy point ----------------
        point = None
        i2_lb = None
        policy_certified = False
        if not curve.empty:
            mp = curve.min_point
            gate = check_witness(k, mp.id_A, mp.iq_A)
            point = gate.point
            cert = self.certify_min_point(T, curve)
            policy_certified = cert["certified"]
            i2_lb = cert["i2_lb"]
            certs["minimum_current"] = {kk: v for kk, v in cert.items() if kk != "i2_lb"}
            if not cert["coverage_ok"]:
                notes.append("control domain != data domain: a lower-current solution may exist where the model data "
                             "do not reach; the found point is the minimum within covered data only and the policy "
                             "claim stays UNKNOWN")
            if point is None:
                # the curve method returned a point the forward evaluation does not cover: never a witness
                policy_certified = False
                notes.append("minimum-current point rejected by the forward evaluation: " + "; ".join(gate.messages))

        # ---------------- DC claim & policy claim ----------------
        dc_claim = None
        if point is not None:
            dc_claim = self._dc_claim(T, point, i2_lb, policy_certified, scope, conditional, cond_reasons, cond_q)
        policy_claim = self._policy_claim(T, electrical, dc_claim, point, policy_certified, curve, scope,
                                          quantity, conditional, cond_reasons, cond_q)

        # ---------------- physical existence with DC ----------------
        physical, active = self._physical_dc(T, curve, point, screens, scope, quantity, conditional,
                                             cond_reasons, cond_q, certs)
        if active is not None:
            notes.append("a DC-compatible point exists only with deliberately increased losses (active-loss "
                         "operation); it is outside the default energy-recovering policy and is not adopted")

        # ---------------- numerical acceptance ----------------
        acc = {}
        if point is None and not curve.empty:
            electrical = _downgrade(electrical, Reason.NUMERICAL_UNRESOLVED, "the curve point failed the forward gate")
            policy_claim = _downgrade(policy_claim, Reason.NUMERICAL_UNRESOLVED, "the curve point failed the forward gate")
        if point is not None and point.Tshaft_Nm is not None:
            tscale = k.torque_scale()
            tres = abs(point.Tshaft_Nm - T)
            acc = {
                "torque_residual_Nm": tres,
                "torque_residual_tolerance_Nm": max(s.torque_residual_abs_Nm, s.torque_residual_rel * tscale),
                "torque_scale_Nm": tscale,
                "max_normalized_hard_violation": point.max_normalized_violation(("VOLTAGE", "CURRENT", "DOMAIN")),
                "hard_violation_tolerance": s.hard_violation_norm_max,
                "power_identities_ok": point.identities_ok,
            }
            acc["passed"] = (tres <= acc["torque_residual_tolerance_Nm"]
                             and acc["max_normalized_hard_violation"] <= s.hard_violation_norm_max
                             and point.identities_ok)
            if not acc["passed"]:
                # never report a numerically unconverged point as a solution
                electrical = _downgrade(electrical, Reason.NUMERICAL_UNRESOLVED, "numerical acceptance failed")
                policy_claim = _downgrade(policy_claim, Reason.NUMERICAL_UNRESOLVED, "numerical acceptance failed")
        return PolicySolution(T, self.scenario.scenario_id, POLICY_NAME, curve, point, electrical, policy_claim,
                              dc_claim, physical, active, screens, tuple(certs.items()), tuple(acc.items()),
                              tuple(notes), issues)

    # -- claim builders -------------------------------------------------------

    def _empty_electrical(self, curve, screens, quantity, scope, conditional, cond_reasons, cond_q, certs) -> Claim:
        k = self.k
        q = quantity + " within voltage/current/domain limits"
        elec_screens = [sc for sc in screens if sc.violated and sc.name in
                        ("torque_exceeds_current_limited_maximum", "d_axis_voltage_exceeds_budget")]
        ev = [Evidence.make(EvidenceKind.ANALYTIC_BOUND, sc.statement, scope=sc.scope, **dict(sc.values))
              for sc in elec_screens]
        if curve.exact and not curve.coverage_limited:
            ev.insert(0, Evidence.make(EvidenceKind.EXACT_ENUMERATION,
                                       "no feasible point on the torque curve: every constraint-polynomial root and "
                                       "every sub-interval was checked"))
            st = Status.UNKNOWN if conditional else Status.INFEASIBLE
            return Claim("electrical_existence", st, q, scope, None,
                         reasons=cond_reasons or (Reason.CONSTRAINT_VIOLATION,), evidence=tuple(ev), qualifiers=cond_q,
                         detail="no statically feasible electrical solution inside the declared domain "
                                "(a statement about this model and domain, not about other motors or domains)")
        # flux map / partial coverage: try the rigorous cell bounds
        tem = curve.target_Tem_Nm
        pe = self.bounds.prove_empty(tem, include_dc=False)
        certs["electrical_cell_bounds"] = pe.to_dict()
        if pe.status == "PROVEN_EMPTY":
            ev.insert(0, Evidence.make(EvidenceKind.BOUNDED_SEARCH,
                                       "interval bounds exclude every covered cell (depth %d, %d cells evaluated)"
                                       % (pe.depth, pe.cells_evaluated)))
            if curve.coverage_limited:
                return Claim("electrical_existence", Status.UNKNOWN, q, scope, None,
                             reasons=(Reason.OUTSIDE_MODEL_DOMAIN,), evidence=tuple(ev), qualifiers=("covered data only",),
                             detail="infeasible within the covered map data; the allowed domain extends beyond the "
                                    "data, where nothing is claimed (incomplete coverage is not a physical limit)")
            st = Status.UNKNOWN if conditional else Status.INFEASIBLE
            return Claim("electrical_existence", st, q, scope, None, reasons=cond_reasons or (Reason.CONSTRAINT_VIOLATION,),
                         evidence=tuple(ev), qualifiers=cond_q,
                         detail="no statically feasible electrical solution in the declared domain (fully covered)")
        if elec_screens and not conditional:
            return Claim("electrical_existence", Status.INFEASIBLE, q, scope, None,
                         reasons=(Reason.NECESSARY_CONDITION_VIOLATED,), evidence=tuple(ev),
                         detail="an analytic necessary condition is violated")
        ev.append(Evidence.make(EvidenceKind.SAMPLED, "sampled tracing found no feasible point; cell bounds unresolved",
                                cells_remaining=pe.cells_remaining))
        return Claim("electrical_existence", Status.UNKNOWN, q, scope, None, reasons=(Reason.NUMERICAL_UNRESOLVED,),
                     evidence=tuple(ev), detail="no solution found, but infeasibility is not proven (solver failure "
                                                "is not physical infeasibility)")

    def _dc_claim(self, T, point: OperatingPoint, i2_lb, certified, scope, conditional, cond_reasons, cond_q) -> Claim:
        k = self.k
        lim = k.limits
        q = f"DC source limits at the policy point (Vdc = {k.Vdc:g} V)"
        declared = [n for n, v in (("discharge power", lim.discharge_power_max_W), ("charge power", lim.charge_power_max_W),
                                   ("discharge current", lim.discharge_current_max_A),
                                   ("charge current", lim.charge_current_max_A)) if v is not None]
        missing = [n for n in ("discharge power", "charge power", "discharge current", "charge current") if n not in declared]
        if not lim.any_declared:
            return Claim("dc_source", Status.UNKNOWN, q, scope, POLICY_NAME, reasons=(Reason.MISSING_INPUT,),
                         detail="no DC source limits declared for this scenario")
        relevant_missing = set()
        if point.Pdc_W is not None:
            relevant_missing.update(missing_relevant_dc_limits(k, point.Pdc_W))
            if not certified and i2_lb is not None and k.inv_loss is not None:
                relevant_missing.update(missing_relevant_dc_limits(k, self._pdc_at(T, i2_lb)))
        extra_q = tuple(f"{m} limit not declared (cannot bind at this point)" for m in missing
                        if m not in relevant_missing)
        if k.inv_loss is None or point.Pdc_W is None:
            # P_inv in [0, inf): P_dc >= P_ac only
            pac = point.Pac_W
            viol = k.P_dis_eff is not None and pac > k.P_dis_eff
            chg_ok = k.P_chg_eff is None or pac >= -k.P_chg_eff
            if viol:
                return Claim("dc_source", Status.INFEASIBLE, q, scope, POLICY_NAME,
                             reasons=(Reason.CONSTRAINT_VIOLATION,),
                             evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                                                     f"P_ac = {pac:.6g} W already exceeds the discharge cap; P_inv >= 0"),),
                             detail="discharge limit exceeded for any passive inverter loss")
            return Claim("dc_source", Status.UNKNOWN, q, scope, POLICY_NAME, reasons=(Reason.MISSING_INPUT,),
                         evidence=(Evidence.make(EvidenceKind.ANALYTIC_BOUND,
                                                 f"inverter loss unknown: P_dc in [{pac:.6g}, inf) W",
                                                 charge_side_satisfied=chg_ok),),
                         detail="inverter loss model missing; electrical-only claims are not promoted to DC claims")
        dcs = [c for c in point.constraints if c.group in ("DISCHARGE_SOURCE", "CHARGE_SOURCE")]
        viol = [c for c in dcs if c.state == "VIOLATED"]
        active = [c for c in dcs if c.state == "ACTIVE"]
        ev = [Evidence.make(EvidenceKind.DIRECT_EVALUATION, f"{c.name}: demand {c.demand:.6g} vs limit {c.limit:.6g} "
                            f"{c.unit} (slack {c.slack:.6g})") for c in dcs]
        c2 = 1.5 * k.Rs + k.inv_loss.ipk2_coeff_W_per_A2
        tem = T + k.tau_rot_or_zero
        if not certified and i2_lb is not None:
            pdc_lb = tem * k.omega_m + c2 * i2_lb + k.inv_loss.offset_W
            dis_viol = any(c.group == "DISCHARGE_SOURCE" for c in viol)
            chg_viol = any(c.group == "CHARGE_SOURCE" for c in viol)
            certain_violation = chg_viol or (dis_viol and not bool(dc_ok(k, np.array([pdc_lb]))[0]))
            certain_ok = (not viol) and bool(dc_ok(k, np.array([pdc_lb]))[0])
            if viol and not certain_violation:
                return Claim("dc_source", Status.UNKNOWN, q, scope, POLICY_NAME, reasons=(Reason.NUMERICAL_UNRESOLVED,),
                             evidence=tuple(ev), detail="violation at the found point, but the certified current lower "
                                                        "bound does not exclude a DC-compatible policy point")
            if not viol and not certain_ok:
                return Claim("dc_source", Status.UNKNOWN, q, scope, POLICY_NAME, reasons=(Reason.NUMERICAL_UNRESOLVED,),
                             evidence=tuple(ev), detail="satisfied at the found point; lower-current policy points "
                                                        "allowed by the bound may violate the charge limit")
        if viol:
            return Claim("dc_source", Status.UNKNOWN if conditional else Status.INFEASIBLE, q, scope, POLICY_NAME,
                         reasons=cond_reasons or (Reason.CONSTRAINT_VIOLATION,), evidence=tuple(ev),
                         qualifiers=cond_q + extra_q,
                         detail="violated at the policy operating point: " + ", ".join(c.name for c in viol))
        if relevant_missing:
            return Claim("dc_source", Status.UNKNOWN, q, scope, POLICY_NAME,
                         reasons=tuple(dict.fromkeys(cond_reasons + (Reason.MISSING_INPUT,))), evidence=tuple(ev),
                         qualifiers=cond_q + extra_q,
                         detail="DC limit(s) that can bind at the policy point are not declared: "
                                + ", ".join(sorted(relevant_missing))
                                + " - a missing limit is not 'unlimited' (declare Infinity explicitly for no limit)")
        quals = cond_q + extra_q + (("boundary: active within numerical tolerance",) if active else ())
        return Claim("dc_source", Status.UNKNOWN if conditional else Status.FEASIBLE, q, scope, POLICY_NAME,
                     reasons=cond_reasons, evidence=tuple(ev), qualifiers=quals,
                     detail="average DC power/current within the declared limits at the policy point")

    def _policy_claim(self, T, electrical, dc_claim, point, certified, curve, scope, quantity, conditional,
                      cond_reasons, cond_q) -> Claim:
        q = f"static achievement of {quantity} under the minimum-current policy incl. DC source limits"
        if electrical.status is Status.INFEASIBLE:
            return Claim("policy_static", Status.INFEASIBLE, q, scope, POLICY_NAME, reasons=electrical.reasons,
                         evidence=electrical.evidence, detail="no electrical solution exists for the policy to select")
        if electrical.status is Status.UNKNOWN or point is None:
            return Claim("policy_static", Status.UNKNOWN, q, scope, POLICY_NAME, reasons=electrical.reasons,
                         evidence=electrical.evidence, qualifiers=electrical.qualifiers, detail=electrical.detail)
        ev = [Evidence.make(EvidenceKind.EXACT_ENUMERATION if curve.exact else EvidenceKind.SAMPLED,
                            f"policy point id = {point.id_A:.6f} A, iq = {point.iq_A:.6f} A, |i| = {point.i_peak_A:.6f} A")]
        if not certified:
            return Claim("policy_static", Status.UNKNOWN, q, scope, POLICY_NAME,
                         reasons=(Reason.NUMERICAL_UNRESOLVED,) if not curve.coverage_limited else (Reason.OUTSIDE_MODEL_DOMAIN,),
                         evidence=tuple(ev) + (dc_claim.evidence if dc_claim else ()),
                         qualifiers=("policy point not certified as the global minimum-current point",),
                         detail="the minimum-current operating point could not be certified")
        st = dc_claim.status
        return Claim("policy_static", st, q, scope, POLICY_NAME, reasons=dc_claim.reasons,
                     evidence=tuple(ev) + dc_claim.evidence, qualifiers=dc_claim.qualifiers,
                     detail=("the policy operating point meets every constraint incl. DC" if st is Status.FEASIBLE else
                             dc_claim.detail))

    def _physical_dc(self, T, curve, point, screens, scope, quantity, conditional, cond_reasons, cond_q, certs):
        k = self.k
        q = quantity + " by any control in the allowed domain incl. DC limits (diagnostic)"
        scr = [sc for sc in screens if sc.violated and sc.name in
               ("shaft_power_exceeds_discharge_cap", "charge_cap_unreachable_even_with_maximum_loss")]
        scr_ev = tuple(Evidence.make(EvidenceKind.ANALYTIC_BOUND, sc.statement, scope=sc.scope, **dict(sc.values))
                       for sc in scr)
        if scr and k.tau_rot is not None:
            return Claim("physical_existence_with_dc", Status.INFEASIBLE, q, scope, "any control",
                         reasons=(Reason.NECESSARY_CONDITION_VIOLATED,), evidence=scr_ev,
                         detail="an analytic bound excludes every control, independent of the optimiser"), None
        if curve.empty:
            return Claim("physical_existence_with_dc", Status.UNKNOWN if curve.coverage_limited or not curve.exact
                         else Status.INFEASIBLE, q, scope, "any control",
                         reasons=(Reason.CONSTRAINT_VIOLATION,) if curve.exact and not curve.coverage_limited
                         else (Reason.NUMERICAL_UNRESOLVED,),
                         detail="no electrical solution, hence none with DC limits"), None
        band = dc_band_I2(k, curve.target_Tem_Nm) if k.tau_rot is not None else None
        if band is None or (k.P_dis_eff is None and k.P_chg_eff is None):
            return Claim("physical_existence_with_dc", Status.UNKNOWN, q, scope, "any control",
                         reasons=(Reason.MISSING_INPUT,), detail="loss model or DC limits missing"), None
        lo, hi = band
        cands = []
        for seg in curve.segments:
            if seg.max_point.I2 >= lo and seg.min_point.I2 <= hi:
                if seg.min_point.I2 >= lo:
                    cands.append(seg.min_point)
                elif curve.exact:
                    w = point_on_curve_with_I2(k, T, seg, lo)
                    if w is not None:
                        cands.append(w)
                elif seg.max_point.I2 <= hi:
                    cands.append(seg.max_point)
        if not curve.segments:
            for cand in (curve.min_point, curve.max_point):
                if cand is not None and lo <= cand.I2 <= hi:
                    cands.append(cand)
        rejected = []
        for cand in cands:
            if not curve.exact:
                cand = self._snap_to_curve(T, cand)
            # every candidate is re-verified against the ORIGINAL request (torque residual, all limits
            # incl. DC, coverage); a sampled estimate that answers a different torque is never a witness
            chk = check_witness(k, cand.id_A, cand.iq_A, T_request=T, require_dc=True, include_validity=False)
            if not chk.accepted:
                rejected.append("; ".join(chk.messages))
                continue
            wpt = chk.point
            active = None
            if point is not None and (wpt.id_A != point.id_A or wpt.iq_A != point.iq_A) and \
                    not point.all_satisfied():
                active = wpt
            st = Status.UNKNOWN if conditional else Status.FEASIBLE
            detail = ("the policy point itself is DC-compatible" if active is None else
                      "DC-compatible only by raising losses above the minimum-current point "
                      "(active-loss candidate, outside the default policy)")
            return Claim("physical_existence_with_dc", st, q, scope, "any control", reasons=cond_reasons,
                         evidence=(Evidence.make(EvidenceKind.NUMERICAL_WITNESS,
                                                 f"witness id = {wpt.id_A:.6f} A, iq = {wpt.iq_A:.6f} A, "
                                                 f"P_dc = {wpt.Pdc_W:.6g} W, T_shaft = {wpt.Tshaft_Nm:.6g} N*m",
                                                 **chk.to_dict()),),
                         qualifiers=cond_q, detail=detail), active
        if rejected:
            notes_rej = Evidence.make(EvidenceKind.SAMPLED, "candidate(s) rejected by the witness gate",
                                      rejected=rejected[:5])
            if curve.exact:
                return Claim("physical_existence_with_dc", Status.UNKNOWN, q, scope, "any control",
                             reasons=(Reason.NUMERICAL_UNRESOLVED,), evidence=(notes_rej,),
                             detail="the enumeration found DC-compatible curve points but none passed the witness "
                                    "gate: not proven either way"), None
        else:
            notes_rej = None
        if curve.exact:
            st = Status.UNKNOWN if conditional else Status.INFEASIBLE
            return Claim("physical_existence_with_dc", st, q, scope, "any control",
                         reasons=cond_reasons or (Reason.CONSTRAINT_VIOLATION,),
                         evidence=(Evidence.make(EvidenceKind.EXACT_ENUMERATION,
                                                 f"I^2 range of every feasible curve segment misses the DC band "
                                                 f"[{lo:.6g}, {hi:.6g}] A^2 (P_dc is monotone in I^2 along the curve)"),)
                         + scr_ev, qualifiers=cond_q,
                         detail="no control meets the DC limits at this torque"), None
        pe = self.bounds.prove_empty(curve.target_Tem_Nm, include_dc=True)
        certs["dc_cell_bounds"] = pe.to_dict()
        if pe.status == "PROVEN_EMPTY" and not curve.coverage_limited:
            return Claim("physical_existence_with_dc", Status.UNKNOWN if conditional else Status.INFEASIBLE, q, scope,
                         "any control", reasons=cond_reasons or (Reason.CONSTRAINT_VIOLATION,),
                         evidence=(Evidence.make(EvidenceKind.BOUNDED_SEARCH, "cell bounds exclude every covered cell "
                                                 "with DC limits"),), qualifiers=cond_q,
                         detail="no control meets the DC limits at this torque"), None
        ev_tail = (Evidence.make(EvidenceKind.SAMPLED, "no DC-compatible sample found"),)
        if notes_rej is not None:
            ev_tail += (notes_rej,)
        return Claim("physical_existence_with_dc", Status.UNKNOWN, q, scope, "any control",
                     reasons=(Reason.NUMERICAL_UNRESOLVED,) if not curve.coverage_limited else (Reason.OUTSIDE_MODEL_DOMAIN,),
                     evidence=ev_tail, detail="not established either way (a coarse search may end UNKNOWN; it never "
                                              "reports a point that answers a different request)"), None

    def _snap_to_curve(self, T: float, cp: CurvePoint) -> CurvePoint:
        """Re-locate a sampled curve estimate on the torque curve (root solve at fixed id)."""
        tr = self.tracer
        tem = T + self.k.tau_rot_or_zero
        try:
            q = tr.solve_q(cp.id_A, tem, cp.iq_A)
        except Exception:  # noqa: BLE001 - unresolved: keep the estimate; the gate rejects it if off-curve
            q = None
        if q is None:
            return cp
        return CurvePoint(cp.id_A, q, cp.id_A * cp.id_A + q * q, cp.tag + "+snapped")


def _downgrade(c: Claim, reason: Reason, why: str) -> Claim:
    if c.status is Status.UNKNOWN:
        return c
    return Claim(c.name, Status.UNKNOWN, c.quantity, c.scope, c.policy, c.time_horizon,
                 tuple(dict.fromkeys(c.reasons + (reason,))), c.evidence, c.qualifiers, f"{c.detail} ({why})")


def solve_policy(drive: DriveModel, scenario: Scenario, T_request: float,
                 settings: NumericalSettings = DEFAULT_SETTINGS) -> PolicySolution:
    return PolicyEvaluator(drive, scenario, settings).solve(T_request)
