"""Second independent review (R2, baseline f6f166b): acceptance cases of the handoff contracts.

Each test states the CORRECT behaviour the handoff requires (the review's evidence scripts assert the observed
defects and are not merged).  Values are synthetic; they check equations, semantics and identity, not hardware.
"""

import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from traction_workbench import api
from traction_workbench import spec_fixtures as sf
from traction_workbench.errors import InputValidationError
from traction_workbench.models.components import TemperatureDependence
from traction_workbench.models.flux import CurrentBox
from traction_workbench.physics import DriveKernel, evaluate_point
from traction_workbench.scenario import DcSourceLimits, Scenario
from traction_workbench.solvers.capability import physical_capability, policy_capability
from traction_workbench.solvers.gate import check_witness
from traction_workbench.solvers.policy import PolicyEvaluator
from traction_workbench.status import Reason, Status

INF = math.inf
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verification"))


@pytest.fixture(scope="module")
def drive():
    return sf.synthetic_drive()


def _partial(d, id_box=(-500.0, -300.0), iq_box=(-600.0, 600.0)):
    return replace(d, motor=replace(d.motor, flux=replace(d.motor.flux, validity=CurrentBox(id_box, iq_box))))


def _claims(d, sc, T):
    r = PolicyEvaluator(d, sc).solve(T)
    return {c.name: c.status for c in r.claims}, r


# ---------------------------------------------------------------------------------------------- A1: C01 / C02 / C03


def test_c01_data_only_reduction_never_creates_a_full_domain_exclusion(drive):
    """Covered-data exclusion is not any-control exclusion: the partial-data case is UNKNOWN, the full data keep
    their FEASIBLE witness (16,316.8043 W)."""
    sc = Scenario("c01", 3000.0, 600.0, DcSourceLimits(18000.0, INF, INF, INF))
    full, rf = _claims(drive, sc, 50.0)
    assert full["physical_existence_with_dc"] is Status.FEASIBLE
    assert rf.point.Pdc_W == pytest.approx(16316.8043275, abs=1e-3)
    part, rp = _claims(_partial(drive), sc, 50.0)
    assert part["physical_existence_with_dc"] is Status.UNKNOWN
    assert Reason.OUTSIDE_MODEL_DOMAIN in rp.physical_dc.reasons
    assert part["policy_static"] is Status.UNKNOWN


@pytest.mark.parametrize("id_box,iq_box", [((-500.0, -300.0), (-600.0, 600.0)), ((-100.0, 0.0), (-600.0, 600.0)),
                                           ((-500.0, 0.0), (0.0, 100.0)), ((-500.0, 0.0), (-600.0, -50.0))])
@pytest.mark.parametrize("n,T", [(3000.0, 50.0), (3000.0, -40.0), (-3000.0, -50.0), (-3000.0, 40.0)])
def test_c01_invariant_over_partial_boxes_motoring_regen_and_reverse(drive, id_box, iq_box, n, T):
    """Whatever part of the data is removed, a full-data FEASIBLE never becomes an any-control INFEASIBLE."""
    sc = Scenario("c01inv", n, 600.0, DcSourceLimits(18000.0, 9000.0, INF, INF))
    full, _ = _claims(drive, sc, T)
    part, _ = _claims(_partial(drive, id_box, iq_box), sc, T)
    for name in ("physical_existence_with_dc", "electrical_existence"):
        if full[name] is Status.FEASIBLE:
            assert part[name] is not Status.INFEASIBLE, (name, id_box, iq_box, n, T)


def test_c02_zero_width_control_set_is_not_covered_by_area(drive):
    """iq fixed at 0 (a line): coverage is set inclusion; the origin is allowed but outside the data."""
    dd = replace(drive, domain=replace(drive.domain, iq_A=(0.0, 0.0)))
    sc = Scenario("c02", 0.0, 600.0, DcSourceLimits(1000.0, INF, INF, INF))
    full, rf = _claims(dd, sc, 0.0)
    assert full["policy_static"] is Status.FEASIBLE and full["physical_existence_with_dc"] is Status.FEASIBLE
    assert rf.point.Pdc_W == pytest.approx(200.0, abs=1e-6)
    part, rp = _claims(_partial(dd), sc, 0.0)
    assert rp.curve.coverage_limited and rp.curve.coverage_distance_A == pytest.approx(0.0, abs=1e-12)
    assert part["policy_static"] is Status.UNKNOWN and part["physical_existence_with_dc"] is Status.UNKNOWN


@pytest.mark.parametrize("line,limited", [(-300.0, False), (-500.0, False), (-299.999, True), (-500.001, True)])
def test_c02_control_line_on_and_just_outside_the_data_edge(drive, line, limited):
    from traction_workbench.models.flux import uncovered_distance
    part = _partial(drive)
    dd = replace(part, domain=replace(part.domain, id_A=(line, line)))
    k = DriveKernel(dd, Scenario("edge", 3000.0, 600.0, DcSourceLimits(INF, INF, INF, INF)))
    cov = uncovered_distance(k.uncovered_rectangles(), dd.domain.current_box, k.Imax)
    assert math.isfinite(cov) is limited


def test_c02_single_point_and_invalid_cell_edge():
    from traction_workbench.models.flux import complement_half_planes, uncovered_distance
    rects = complement_half_planes(-500.0, -300.0, -600.0, 600.0)
    assert uncovered_distance(rects, CurrentBox((0.0, 0.0), (0.0, 0.0)), 600.0) == 0.0          # outside point
    assert uncovered_distance(rects, CurrentBox((-400.0, -400.0), (0.0, 0.0)), 600.0) == INF     # inside point
    cell = [(-400.0, -350.0, 10.0, 20.0, False)]                                                # invalid cell
    assert uncovered_distance(cell, CurrentBox((-350.0, -350.0), (0.0, 30.0)), 600.0) == pytest.approx(math.hypot(350, 10))
    # an uncovered set exactly tangent to the current disk still counts (conservative)
    assert uncovered_distance(complement_half_planes(-600.0, 600.0, -600.0, 500.0),
                              CurrentBox((-600.0, 0.0), (0.0, 600.0)), 500.0) == pytest.approx(500.0)


@pytest.mark.parametrize("key,T", [("discharge_power_max_W", 50.0), ("discharge_current_max_A", 50.0),
                                   ("charge_power_max_W", -50.0), ("charge_current_max_A", -50.0)])
@pytest.mark.parametrize("m", [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0])
def test_c03_one_acceptance_set_for_gate_band_and_bounds(drive, key, T, m):
    """Around the boundary (in units of the gate tolerance): policy FEASIBLE implies physical FEASIBLE, and the
    forward point's constraint state agrees with both."""
    from traction_workbench.settings import DEFAULT_SETTINGS as S
    base = Scenario("c03", 3000.0, 600.0, DcSourceLimits(INF, INF, INF, INF))
    p = PolicyEvaluator(drive, base).solve(T).point.Pdc_W
    val = abs(p) if "power" in key else abs(p) / 600.0
    tol = max(S.power_abs_tol_W if "power" in key else S.current_abs_tol_A, S.constraint_rel_tol * val)
    lim = {"discharge_power_max_W": INF, "charge_power_max_W": INF, "discharge_current_max_A": INF,
           "charge_current_max_A": INF, key: val + m * tol}
    c, r = _claims(drive, Scenario("c03", 3000.0, 600.0, DcSourceLimits(**lim)), T)
    if c["policy_static"] is Status.FEASIBLE:
        assert c["physical_existence_with_dc"] is Status.FEASIBLE
    if c["physical_existence_with_dc"] is Status.INFEASIBLE:
        assert c["policy_static"] is not Status.FEASIBLE


def test_c03_zero_and_unlimited_caps(drive):
    zero = Scenario("z", 3000.0, 600.0, DcSourceLimits(0.0, INF, INF, INF))
    c, _ = _claims(drive, zero, 50.0)
    assert c["policy_static"] is Status.INFEASIBLE and c["physical_existence_with_dc"] is Status.INFEASIBLE
    unl = Scenario("u", 3000.0, 600.0, DcSourceLimits(INF, INF, INF, INF))
    c, _ = _claims(drive, unl, 50.0)
    assert c["policy_static"] is Status.FEASIBLE and c["physical_existence_with_dc"] is Status.FEASIBLE


def test_c03_certificate_reports_the_numerical_allowance_separately(drive):
    from traction_workbench.solvers.certificate import certify_torque
    k = DriveKernel(drive, Scenario("cert", 6000.0, 600.0, sf.synthetic_limits()))
    ev = PolicyEvaluator(drive, k.scenario)
    cap = physical_capability(ev, +1, include_dc=True)
    cert = certify_torque(k, +1, (cap.witness.id_A, cap.witness.iq_A))
    assert cert.valid and cert.numerical_allowance > 0
    assert cap.bound_Nm == pytest.approx(cert.upper_bound + cert.numerical_allowance, abs=1e-12)
    assert cap.value_Nm <= cap.bound_Nm


# ---------------------------------------------------------------------------------------------- A2 (part): C04


def test_c04_non_passive_temperature_law_is_rejected_at_input(drive):
    with pytest.raises(InputValidationError, match="non-passive"):
        replace(drive, motor=replace(drive.motor, reference_winding_temp_C=25.0,
                                     rs_temperature=TemperatureDependence(-0.02, (25.0, 150.0), "declared fit")))


def test_c04_normal_copper_law_and_ideal_fixture_keep_working(drive):
    ok = replace(drive, motor=replace(drive.motor, reference_winding_temp_C=25.0,
                                      rs_temperature=TemperatureDependence(0.00393, (-40.0, 180.0), "copper alpha")))
    c, r = _claims(ok, Scenario("cu", 3000.0, 600.0, sf.synthetic_limits(), winding_temp_C=150.0), 200.0)
    assert c["policy_static"] is Status.FEASIBLE and r.point.Pcu_W > 0
    ideal = replace(drive, motor=replace(drive.motor, Rs_ohm=0.0, reference_winding_temp_C=25.0,
                                         rs_temperature=TemperatureDependence(0.00393, (-40.0, 180.0), "ideal")))
    assert DriveKernel(ideal, Scenario("i", 3000.0, 600.0, sf.synthetic_limits(), winding_temp_C=100.0)).Rs == 0.0


def test_c04_gate_rejects_a_non_passive_energy_state(drive):
    k = DriveKernel(drive, Scenario("g", 3000.0, 600.0, sf.synthetic_limits()))
    pt = evaluate_point(k, -50.0, 200.0)
    assert check_witness(k, pt.id_A, pt.iq_A, point=pt).accepted
    bad = replace(pt, Pcu_W=-10.0)
    chk = check_witness(k, pt.id_A, pt.iq_A, point=bad)
    assert not chk.accepted and any("non-passive" in m for m in chk.messages)
    worse = replace(pt, energy_mode="ACCOUNTING_INCONSISTENCY")
    assert not check_witness(k, pt.id_A, pt.iq_A, point=worse).accepted


# ---------------------------------------------------------------------------------------------- A1.4 module witness


def test_a1_module_physical_capability_keeps_the_accepted_policy_witness():
    from make_module_anchor import module_drive
    md, _ = module_drive()
    ev = PolicyEvaluator(md, Scenario("m", 3000.0, 600.0, DcSourceLimits(18000.0, INF, INF, INF)))
    pol = policy_capability(ev, +1, certify=False)
    phys = physical_capability(ev, +1, include_dc=True)
    assert pol.value_Nm is not None and not pol.gate_messages
    assert phys.value_Nm is not None and phys.value_Nm >= pol.value_Nm - 1e-9
    assert not phys.gate_messages and not phys.certified
    assert any("achieved lower bound" in n for n in phys.notes)
    assert phys.bound_Nm is None or phys.bound_Nm >= phys.value_Nm


# ---------------------------------------------------------------------------------------------- A2: D-R2-01


def _env(approval=None, conditions=(), status="text is not approval", origin=None):
    from traction_workbench.analysis.rating import RatingEnvelope
    from traction_workbench.models.provenance import DataOrigin, Provenance
    return RatingEnvelope("AUDIT", "A", 10.0, (-16000.0, 16000.0), (200.0, 200.0),
                          Provenance(origin or DataOrigin.SUPPLIER, "rating sheet", "A", status),
                          min_braking_torque_Nm=(-120.0, -120.0), conditions=conditions, approval=approval)


@pytest.mark.parametrize("state", ["rejected", "not_approved", "unknown", None])
def test_d_r2_01_only_a_typed_approval_with_evidence_rates(state):
    from traction_workbench.analysis.rating import RatingApproval, duration_claim
    appr = None if state is None else RatingApproval(state, "RS-7", "B", "10 s peak rating")
    c = duration_claim((_env(appr, status="supplier-rated, APPROVED"),), 10.0, 12000.0, 150.0, {})
    assert c.status is Status.UNKNOWN and Reason.UNVALIDATED_DURATION in c.reasons


def test_d_r2_01_blank_evidence_identity_does_not_approve_and_approved_does():
    from traction_workbench.analysis.rating import RatingApproval, approval, duration_claim
    blank = RatingApproval("approved", "", "", "10 s peak rating")
    assert not approval(_env(blank))[0]
    assert duration_claim((_env(blank),), 10.0, 12000.0, 150.0, {}).status is Status.UNKNOWN
    ok = RatingApproval("approved", "RS-7", "B", "10 s peak rating")
    c = duration_claim((_env(ok, status="REJECTED"),), 10.0, 12000.0, 150.0, {})
    assert c.status is Status.FEASIBLE            # the typed state decides; the free text only describes
    assert duration_claim((_env(ok),), math.inf, 12000.0, 150.0, {}).status is Status.UNKNOWN   # 10 s != continuous


def test_d_r2_01_nan_condition_never_matches():
    from traction_workbench.analysis.rating import RatingApproval, duration_claim
    ok = RatingApproval("approved", "RS-7", "B", "10 s peak rating")
    e = _env(ok, conditions=(("coolant_temp_C", 65.0),))
    assert duration_claim((e,), 10.0, 3000.0, 150.0, {"coolant_temp_C": 65.0}).status is Status.FEASIBLE
    assert duration_claim((e,), 10.0, 3000.0, 150.0, {"coolant_temp_C": math.nan}).status is Status.UNKNOWN
    with pytest.raises(InputValidationError):
        _env(ok, conditions=(("coolant_temp_C", math.nan),))


def test_d_r2_01_signed_quadrants_at_negative_speed():
    from traction_workbench.analysis.rating import RatingApproval, duration_claim
    e = _env(RatingApproval("approved", "RS-7", "B", "10 s peak rating"))
    # -3000 rpm with -150 N*m is MOTORING (P > 0): the 200 N*m motoring table applies
    assert duration_claim((e,), 10.0, -3000.0, -150.0, {}).status is Status.FEASIBLE
    # -3000 rpm with +150 N*m is BRAKING: the 120 N*m braking table applies
    assert duration_claim((e,), 10.0, -3000.0, 150.0, {}).status is Status.INFEASIBLE


# ---------------------------------------------------------------------------------------------- A3: D-R2-02 / 03


def test_d_r2_02_record_identity_covers_the_loss_tables(drive):
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    mod = api.module_model_from_dict(api.EXAMPLE_MODULE)
    mod2 = replace(mod, device=replace(mod.device, e_on=replace(
        mod.device.e_on, values=tuple(tuple(v * 10 for v in row) for row in mod.device.e_on.values))))
    lim = replace(sf.synthetic_limits(), discharge_power_max_W=33500.0, discharge_current_max_A=INF)
    rr = Requirement("R-hash", "100 Nm", 100.0, 3000.0, 600.0)
    recs = [evaluate_requirement(rr, replace(drive, inverter=replace(drive.inverter, loss=None, module_loss=m,
                                                                   module_Tj_C=100.0)),
                                 source_limits=lim, with_capability=False) for m in (mod, mod2)]
    assert [r.verdict.status for r in recs] == [Status.FEASIBLE, Status.INFEASIBLE]
    assert recs[0].input_sha256 != recs[1].input_sha256 and recs[0].record_id != recs[1].record_id
    assert recs[0].to_dict()["implementation"]["software_version"]
    # the same semantics in another dict order keep the identity
    spec = dict(reversed(list(api.EXAMPLE_MODULE.items())))
    from traction_workbench.identity import content_sha256
    assert content_sha256(api.module_model_from_dict(spec)) == content_sha256(mod)


def test_d_r2_03_the_accepted_band_witness_is_the_primary_output(drive):
    from traction_workbench.decision import evaluate_requirement
    from traction_workbench.requirement import Requirement
    lim = replace(sf.synthetic_limits(), discharge_power_max_W=0.0, discharge_current_max_A=0.0)
    r = Requirement("band-zero", "Any shaft torque in [-10,10] Nm", 0.0, 12000.0, 600.0, operator="band",
                    band_Nm=10.0)
    rec = evaluate_requirement(r, drive, source_limits=lim)
    c = rec.conditions[0]
    assert rec.verdict.status is Status.FEASIBLE
    assert c.primary_torque_Nm == pytest.approx(-5.0)
    assert c.primary.point.Pdc_W == pytest.approx(-2015.771, abs=1e-3)
    d = c.to_dict()
    assert d["policy_solution"]["operating_point"]["Pdc_W"] == pytest.approx(-2015.771, abs=1e-3)
    assert d["rejected_band_centre"]["torque_Nm"] == 0.0 and d["rejected_band_centre"]["policy_claim"] == "INFEASIBLE"
    assert not any("raise the source limit" in a for a in rec.next_actions)
    assert any("rejected candidate" in x for x in rec.limiting_factors)
    md = rec.to_markdown()
    assert "rejected candidate" in md and "-2015.77" in md.replace(" ", "")


# ---------------------------------------------------------------------------------------------- A3: D-R2-04 / 05


def test_d_r2_04_data_edge_is_never_labelled_a_minimum():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from traction_workbench.analysis.sizing import size_parameter
    from traction_workbench.i18n import set_language
    from traction_workbench.plots.figures import fig_capability_vs_parameter
    d = sf.synthetic_drive()
    d = replace(d, inverter=replace(d.inverter, loss=replace(d.inverter.loss, valid_Vdc_V=(520.0, 700.0))))
    s = sf.synthetic_scenario(12000.0, 450.0, source_limits=sf.synthetic_limits())
    r = size_parameter(d, s, 150.0, "Vdc_V", (400.0, 700.0), samples=31).to_dict()
    assert r["minimal_label"] == "smallest witnessed" and not r["minimal_is_bracketed"]
    assert [x["status"] for x in r["regions"]] == ["UNKNOWN", "FEASIBLE"]
    cv = {"values": np.array([400.0, 520.0, 700.0]), "capability_Nm": np.array([np.nan, 150.0, 155.0]),
          "parameter": r["parameter"], "T_request_Nm": 150.0, "direction": 1, "baseline": 450.0,
          "speed_rpm": 12000.0, "Vdc_V": 450.0}
    set_language("en")
    f = Figure()
    fig_capability_vs_parameter(f, cv, r)
    labels = " | ".join(f.axes[0].get_legend_handles_labels()[1])
    assert "smallest witnessed 520" in labels and "minimal" not in labels and "unresolved" in labels


def test_d_r2_04_feasible_island_between_unknown_regions_has_no_bracket():
    from traction_workbench.analysis.sizing import size_parameter
    d = sf.synthetic_drive()
    d = replace(d, inverter=replace(d.inverter, loss=replace(d.inverter.loss, valid_Vdc_V=(520.0, 600.0))))
    s = sf.synthetic_scenario(12000.0, 450.0, source_limits=sf.synthetic_limits())
    r = size_parameter(d, s, 150.0, "Vdc_V", (400.0, 700.0), samples=31)
    assert [st for *_, st in r.regions] == ["UNKNOWN", "FEASIBLE", "UNKNOWN"]
    assert not r.minimal_is_bracketed and not r.maximal_is_bracketed


def test_d_r2_05_zero_charge_acceptance_stays_in_the_diagnosis(drive):
    from traction_workbench.analysis.dominance import capability_dominance
    sc = Scenario("z", 3000.0, 600.0, DcSourceLimits(200000.0, 0.0, 400.0, 0.0))
    r = capability_dominance(drive, sc, direction=-1).to_dict()
    assert r["base_policy_capability_Nm"] == pytest.approx(-1.26504764, abs=1e-6)
    names = {row["constraint"]: row for row in r["single"]}
    assert "DC_CHARGE_POWER" in names and "absolute" in names["DC_CHARGE_POWER"]["perturbation"]
    tied = [j for j in r["joint"] if set(j["constraints"]) == {"DC_CHARGE_POWER", "DC_CHARGE_CURRENT"}]
    assert tied and tied[0]["classification"] == "joint bottleneck"
    assert r["base_policy_capability_Nm"] - tied[0]["gain_Nm"] == pytest.approx(-4.45197934, abs=1e-5)


# ---------------------------------------------------------------------------------------------- B1: PT-01 / PT-02

def _resistive(tech, **kw):
    from traction_workbench.models.module_loss import ModuleLossModel, SwitchDevice, linear_table
    v = linear_table(0.0, 0.002, 1000.0, (25.0, 200.0))
    z = linear_table(0.0, 0.0, 1000.0, (25.0, 200.0), "mJ")
    dev = SwitchDevice(tech, v, v if tech == "IGBT" else linear_table(3.0, 0.0, 1000.0, (25.0, 200.0)), z, z, z,
                       **kw)
    return ModuleLossModel(dev, 20000.0, modulation="spwm", deadtime_s=0.0)


def test_pt01_sic_channel_and_body_diode_heat_one_die():
    from traction_workbench.models.module_loss import inverter_losses
    r = inverter_losses(_resistive("SiC_MOSFET"), 300.0, 0.0, 0.0, 240.0, 600.0, 25.0)
    assert r["semiconductor_W"] == pytest.approx(270.0, rel=1e-9)          # 3 phases x 2 dies x R I^2 / 4
    assert set(r["per_die_W"]) == {"upper_mosfet", "lower_mosfet"}
    assert all(w == pytest.approx(45.0, rel=1e-9) for w in r["per_die_W"].values())
    assert r["hottest_position_W"] == pytest.approx(45.0, rel=1e-9)
    assert 3.0 * sum(r["per_die_W"].values()) == pytest.approx(r["semiconductor_W"], rel=1e-12)   # dies sum to total


def test_pt02_standstill_uses_the_actual_duty_and_the_same_data_gate():
    from dataclasses import replace as rep
    from traction_workbench.models.module_loss import (ModuleLossModel, linear_table, point_losses,
                                                        standstill_hotspot)
    h = standstill_hotspot(_resistive("SiC_MOSFET"), 300.0, 600.0, 25.0)
    assert h["module_total_W"] == pytest.approx(270.0, rel=1e-6)          # sum_k R i_k^2 at every angle
    igbt = _resistive("IGBT")
    igbt = ModuleLossModel(rep(igbt.device, e_on=linear_table(0.0, 0.1, 1000.0, (25.0, 200.0), "mJ")), 20000.0)
    moving = point_losses(igbt, 300.0, 0.0, 100.0, 0.0, 400.0, 25.0, standstill=False)
    still = point_losses(igbt, 300.0, 0.0, 0.0, 0.0, 400.0, 25.0, standstill=True)
    assert not moving["established"] and not still["established"]          # 400 V != 600 V test voltage: no scaling


# ---------------------------------------------------------------------------------------------- B2: PT-03 / 04 / 05

def _cap(**kw):
    from traction_workbench.extensions.dclink_ripple import CapacitorBank
    return CapacitorBank(500e-6, ((100.0, 0.002), (1e7, 0.002)), **kw)


def test_pt03_divergent_iteration_is_never_converged_nor_settled():
    from traction_workbench.extensions.dclink_ripple import ripple_analysis
    bank = _cap(Rth_K_per_W=10.0, ESR_temp_coeff_per_K=0.1, T_ref_C=65.0, T_valid_C=(60.0, 125.0),
                life_hours_table=((60.0, 1e5), (125.0, 1e3)), life_basis="test table")
    r = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, bank, T_ref_C=65.0)
    h = r["hotspot"]
    assert not h["converged"] and h["T_hot_C"] is None and h["termination"] == "no_equilibrium_in_domain"
    assert h["analytic_fixed_current"]["slope"] >= 1.0 and not h["analytic_fixed_current"]["equilibrium_exists"]
    assert r["claims"]["capacitor_loss"]["status"] == "UNKNOWN" and r["P_cap_W"] is None
    assert r["life"]["status"] == "UNKNOWN" and r["claims"]["capacitor_life"]["status"] == "UNKNOWN"


def test_pt03_closed_forms_and_explicit_termination():
    from traction_workbench.extensions.dclink_ripple import ripple_analysis
    const = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _cap(Rth_K_per_W=5.0), T_ref_C=65.0)
    P0 = const["P_cap_W"]
    assert const["hotspot"]["termination"] == "closed_form"
    assert const["hotspot"]["T_hot_C"] == pytest.approx(65.0 + 5.0 * P0, abs=1e-9)          # constant ESR
    a, R = 0.004, 0.5
    aff = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0,
                          _cap(Rth_K_per_W=R, ESR_temp_coeff_per_K=a, T_ref_C=65.0, T_valid_C=(-40.0, 300.0)),
                          T_ref_C=65.0)
    dT = R * P0 / (1.0 - R * P0 * a)                                        # fixed current, affine ESR(T)
    assert aff["hotspot"]["converged"] and aff["hotspot"]["T_hot_C"] == pytest.approx(65.0 + dT, abs=1e-6)
    assert aff["hotspot"]["analytic_fixed_current"]["T_hot_C"] == pytest.approx(65.0 + dT, abs=1e-9)
    assert abs(aff["hotspot"]["residual_K"]) <= 1e-6
    ex = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0,
                         _cap(Rth_K_per_W=R, ESR_temp_coeff_per_K=a, T_ref_C=65.0, T_valid_C=(-40.0, 300.0)),
                         T_ref_C=65.0, max_iter=3)
    assert ex["hotspot"]["termination"] == "max_iter" and ex["hotspot"]["T_hot_C"] is None
    out = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _cap(Rth_K_per_W=5.0, T_valid_C=(-40.0, 70.0)),
                          T_ref_C=65.0)
    assert out["hotspot"]["termination"] == "out_of_domain" and out["claims"]["capacitor_loss"]["status"] == "UNKNOWN"
    with pytest.raises(InputValidationError):
        _cap(Rth_K_per_W=5.0, ESR_temp_coeff_per_K=0.01)                   # a temperature law needs its domain
    with pytest.raises(InputValidationError):
        _cap(ESR_temp_coeff_per_K=0.1, T_ref_C=65.0, T_valid_C=(-40.0, 125.0))   # law not positive on the domain


def test_pt04_current_split_and_heat_are_solved_at_one_temperature():
    from scipy.optimize import brentq
    from traction_workbench.extensions.dclink_ripple import (SourceImpedance, ripple_analysis, spectrum,
                                                              switching_waveform)
    bank = _cap(Rth_K_per_W=5.0, ESR_temp_coeff_per_K=0.01, T_ref_C=65.0, T_valid_C=(-30.0, 150.0))
    src = SourceImpedance(0.005, 1e-9, "synthetic")
    r = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, bank, src, T_ref_C=65.0)
    h = r["hotspot"]
    assert h["converged"] and h["T_hot_C"] == pytest.approx(110.5491, abs=1e-4)       # not the frozen 120.4158
    w = switching_waveform(350.0, 0.8, 0.3, 200.0, 10e3)
    sp = spectrum(w["i_inv_A"], 200.0)
    f, x = sp["f_Hz"][1:], sp["complex"][1:]
    wt = np.full(f.size, 2.0)
    wt[-1] = 1.0                                                          # Nyquist bin counted once

    def g(T):                                                            # independent scalar root, network re-solved
        zs = src.z(f)
        ic = x * zs / (zs + bank.impedance(f, T))
        return T - 65.0 - 5.0 * float(np.sum(wt * np.abs(ic) ** 2 * bank.esr(f, T)[0]))
    assert h["T_hot_C"] == pytest.approx(brentq(g, 65.0, 150.0, xtol=1e-12), abs=1e-7)
    assert r["state"]["T_C"] == h["T_hot_C"] and r["P_cap_W"] == pytest.approx(h["P_W"], rel=1e-12)
    assert abs(h["residual_K"]) <= 1e-6 and r["kcl_residual_rel"] < 1e-12


def test_pt04_bank_rth_basis_is_explicit():
    from traction_workbench.extensions.dclink_ripple import CapacitorBank, SourceImpedance, ripple_analysis
    tab = ((100.0, 0.004), (1e7, 0.004))
    src = SourceImpedance(0.02, 1e-9, "synthetic")
    with pytest.raises(InputValidationError):
        CapacitorBank(250e-6, tab, Rth_K_per_W=5.0, count=2, symmetric_layout=True)          # basis not stated
    per = CapacitorBank(250e-6, tab, Rth_K_per_W=5.0, count=2, symmetric_layout=True, Rth_basis="per_capacitor")
    bank = CapacitorBank(250e-6, tab, Rth_K_per_W=5.0, count=2, symmetric_layout=True, Rth_basis="bank")
    rp = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, per, src, T_ref_C=65.0)
    rb = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, bank, src, T_ref_C=65.0)
    assert rp["P_cap_W"] == pytest.approx(rb["P_cap_W"], rel=1e-12)
    assert rp["hotspot"]["T_hot_C"] - 65.0 == pytest.approx((rb["hotspot"]["T_hot_C"] - 65.0) / 2.0, rel=1e-12)


def test_pt05_capacitor_branch_aliases_agree_and_mappings_are_explicit():
    from traction_workbench.extensions.dclink_ripple import SourceImpedance, ripple_analysis
    src = SourceImpedance(0.02, 1e-9, "synthetic")

    def run(loc, qty, lim=140.0, bw=1e5, bank=None):
        return ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, bank or _cap(), src,
                               requirement={"location": loc, "quantity": qty, "limit": lim, "bandwidth_Hz": bw})
    a = run("capacitor_branch", "current_ac_rms")
    b = run("capacitor_branch", "capacitor_current_rms")
    assert a["requirement_value"] == pytest.approx(110.7757, abs=1e-4) == b["requirement_value"]
    assert a["claims"]["ripple_requirement"]["status"] == b["claims"]["ripple_requirement"]["status"] == "FEASIBLE"
    assert a["requirement"]["branch"] == "capacitor_current"
    inv = run("inverter_dc_input", "current_ac_rms")
    assert inv["requirement"]["branch"] == "inverter_current" and inv["requirement_value"] > a["requirement_value"]
    node = run("dc_link_bus", "current_ac_rms")
    assert node["claims"]["ripple_requirement"]["reasons"] == ["REQUIREMENT_INCOMPLETE"]
    batt = run("battery_terminal", "voltage_pp", lim=50.0)
    assert batt["claims"]["ripple_requirement"]["reasons"] == ["OUTSIDE_MODEL_DOMAIN"]


def test_pt05_narrow_esr_evidence_cannot_pass_outside_its_band():
    from traction_workbench.extensions.dclink_ripple import CapacitorBank, SourceImpedance, ripple_analysis
    narrow = CapacitorBank(500e-6, ((100.0, 0.002), (5000.0, 0.002)))
    r = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, narrow, SourceImpedance(0.02, 1e-9, "synthetic"),
                        requirement={"location": "capacitor_branch", "quantity": "capacitor_current_rms",
                                     "limit": 140.0, "bandwidth_Hz": 1e5})
    c = r["claims"]["ripple_requirement"]
    assert c["status"] == "UNKNOWN" and c["reasons"] == ["BOUND_INCONCLUSIVE"]
    lo, hi = r["requirement"]["bounds"]
    assert lo < 140.0 < hi and r["requirement_value"] is None and r["P_cap_W"] is None
    assert r["I_cap_rms_A"] is None and r["I_cap_rms_bounds_A"][1] > r["I_cap_rms_bounds_A"][0]


def test_pt05_frequency_wise_kcl_nyquist_band_edge_and_sampling_resolution():
    from traction_workbench.extensions.dclink_ripple import (SourceImpedance, _Network, _rms_weights, ripple_analysis,
                                                              switching_waveform)
    src = SourceImpedance(0.02, 2e-6, "synthetic")
    w = switching_waveform(350.0, 0.8, 0.3, 200.0, 10e3)
    net = _Network(w, _cap(), src)
    st = net.at(65.0)
    assert np.max(np.abs(net.X - st["Ic"] - st["Is"])) <= 1e-12 * np.max(np.abs(net.X))
    x = w["i_inv_A"]
    assert np.sum(net.inv_rms_h[1:] ** 2) == pytest.approx(np.mean(x ** 2) - np.mean(x) ** 2, rel=1e-12)
    assert list(_rms_weights(8, 5)) == [1.0, math.sqrt(2), math.sqrt(2), math.sqrt(2), 1.0]
    req = {"location": "capacitor_branch", "quantity": "capacitor_current_rms", "limit": 1e3}
    at = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _cap(), src, requirement={**req, "bandwidth_Hz": 1e4})
    below = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _cap(), src,
                            requirement={**req, "bandwidth_Hz": 1e4 - 1.0})
    assert at["requirement_value"] > below["requirement_value"]            # the harmonic at the band edge counts
    fine = ripple_analysis(350.0, 0.8, 0.3, 200.0, 10e3, 600.0, _cap(), src, requirement={**req, "bandwidth_Hz": 1e4},
                           samples_per_carrier=256)
    assert at["requirement"]["resolution_delta"] == pytest.approx(abs(fine["requirement_value"] -
                                                                      at["requirement_value"]), rel=1e-9)
    assert 0.0 < at["requirement"]["resolution_delta"] < 0.05 * at["requirement_value"]


def test_pt05_passive_bounds_contain_every_passive_capacitor_impedance():
    from traction_workbench.extensions.dclink_ripple import CapacitorBank, SourceImpedance, _Network, switching_waveform
    net = _Network(switching_waveform(350.0, 0.8, 0.3, 200.0, 10e3), CapacitorBank(500e-6, ((100.0, 0.002),)),
                   SourceImpedance(0.02, 2e-6, "synthetic"))
    rng = np.random.default_rng(7)
    X, Zs, wt, ac = net.X, net.Zs, net.wt, net.ac
    for _ in range(300):
        zc = 10 ** rng.uniform(-6, 1) * (rng.random() < 0.9) + 1j * rng.choice([-1, 1]) * 10 ** rng.uniform(-6, 1)
        ic, is_ = X * Zs / (Zs + zc), X * zc / (Zs + zc)
        for br, m in (("capacitor_current", ic), ("source_current", is_), ("bus_voltage", ic * zc)):
            lo, hi = net.bounds(br)
            val = np.abs(m) * wt
            assert np.all(val[ac] <= hi[ac] * (1 + 1e-9) + 1e-12) and np.all(val[ac] >= lo[ac] * (1 - 1e-9) - 1e-12)


# ---------------------------------------------------------------------------------------------- B3: PT-06 / PT-07

def test_pt06_alternating_motoring_regen_cycles_every_physical_die():
    from traction_workbench.extensions.lifetime import cycle_analysis, die_mission
    from traction_workbench.models.module_loss import ModuleLossModel, inverter_losses
    mod = ModuleLossModel(_resistive("IGBT").device, 10000.0, modulation="spwm")
    seg = [inverter_losses(mod, 300.0 * math.cos(p), 300.0 * math.sin(p), 240.0, 0.0, 600.0, 25.0)["per_die_thermal_W"]
           for p in (0.0, math.pi)]
    assert seg[0]["upper_igbt"] == pytest.approx(37.7789, abs=1e-4) == seg[1]["upper_diode"]
    assert seg[0]["upper_diode"] == pytest.approx(7.2211, abs=1e-4) == seg[1]["upper_igbt"]
    assert max(seg[0].values()) == pytest.approx(max(seg[1].values()), rel=1e-12)     # the max heat is constant
    nets = {d: ((0.05, 0.1), (0.1, 1.0)) for d in seg[0]}
    ms = die_mission([2.0, 2.0], seg, nets, 65.0, 0.01, "periodic", repeat=5)
    ranges = {d: cycle_analysis(ms["t_s"], ms["T_C"][d], repeating_mission=True)["max_range_K"] for d in ms["dies"]}
    assert all(v > 3.0 for v in ranges.values())                          # every die cycles; none is lost
    rev = die_mission([2.0, 2.0], [dict(reversed(list(s.items()))) for s in seg], nets, 65.0, 0.01, "periodic", 5)
    assert all(np.array_equal(ms["T_C"][d], rev["T_C"][d]) for d in ms["dies"])   # reporting order is irrelevant


def test_pt06_startup_once_periodic_steady_state_and_api_devices():
    from traction_workbench.extensions.lifetime import cycle_analysis, die_mission
    nets = {"a_igbt": ((0.1,), (1.0,))}
    one = die_mission([5.0, 5.0], [{"a_igbt": 100.0}, {"a_igbt": 20.0}], nets, 65.0, 0.05, "finite", 1)
    three = die_mission([5.0, 5.0], [{"a_igbt": 100.0}, {"a_igbt": 20.0}], nets, 65.0, 0.05, "finite", 3)
    c1 = cycle_analysis(one["t_s"], one["T_C"]["a_igbt"], repeating_mission=True)["cycles"]
    c3 = cycle_analysis(three["t_s"], three["T_C"]["a_igbt"], repeating_mission=True)["cycles"]
    cold = [sum(c["count"] for c in cs if c["min"] < 65.0 + 1e-3) for cs in (c1, c3)]
    assert cold == [1.0, 1.0]                                        # the cold start-up / shutdown cycle: once
    assert sum(c["count"] for c in c3) == pytest.approx(sum(c["count"] for c in c1) + 2.0)   # inner cycles repeat
    per = die_mission([5.0, 5.0], [{"a_igbt": 100.0}, {"a_igbt": 20.0}], nets, 65.0, 0.05, "periodic", 1)
    assert per["closure_K"] < 1e-9 and per["T_C"]["a_igbt"][0] > 65.0 + 1.0      # steady state, not a warm-up
    r = api.lifetime({})
    assert r["governing_device"] in r["devices"] and len(r["devices"]) == 4
    assert r["damage"]["claim"]["status"] == "UNKNOWN"


def test_pt06_screening_chain_and_unachieved_mission_never_rate_damage():
    law = {"mechanism": "synthetic", "A": 1e12, "a": -5.0, "b_K": 0.0, "dT_valid_K": [0.0, 200.0],
           "Tref_valid_C": [-50.0, 250.0], "basis": "synthetic law (not a product)"}
    r = api.lifetime({"cycling_model": law, "D_allow": 1.0})
    assert r["damage"]["claim"]["status"] == "UNKNOWN" and r["damage"]["claim"]["reasons"] == ["SCREENING_ONLY"]
    q = api.lifetime({"trace": {"t_s": [0, 1, 2, 3, 4], "T_C": [60, 100, 60, 100, 60], "qualified": True,
                                "basis": "measured, device U1 lower IGBT", "device": "U1 lower IGBT"},
                      "cycling_model": law, "D_allow": 1.0})
    assert q["damage"]["claim"]["status"] == "FEASIBLE" and q["governing_device"] == "U1 lower IGBT"
    low = api.lifetime({"limits": {"discharge_power_max_W": 5000.0, "charge_power_max_W": 5000.0,
                                   "discharge_current_max_A": 1000.0, "charge_current_max_A": 1000.0}})
    assert any(not s["achieved"] for s in low["segments"])
    assert low["damage"]["claim"]["reasons"] == ["OUTSIDE_ALLOWED_OPERATING_DOMAIN"]


def test_pt07_heating_time_is_the_heating_interval():
    from traction_workbench.extensions.lifetime import CyclingModel, cycle_analysis
    cm = CyclingModel("synthetic timing law", 1000.0, 0.0, 0.0, c=1.0, dT_valid_K=(1.0, 100.0),
                      ton_valid_s=(0.0, 1000.0), basis="synthetic (not a supplier law)", scatter_factor=1.0)
    for periodic in (False, True):
        r = cycle_analysis([0.0, 1.0, 10.0], [40.0, 100.0, 40.0], cm, "rise_time", repeating_mission=periodic)
        assert r["damage"]["D"] == pytest.approx(1.0 / 1000.0, rel=1e-12)          # 1 s heating, not 9 s cooling
    t = np.array([0.0, 1.0, 10.0, 11.0, 20.0])
    x = np.array([40.0, 100.0, 40.0, 100.0, 40.0])
    base = cycle_analysis(t, x, cm, "rise_time", repeating_mission=True)["damage"]["D"]
    rot = cycle_analysis(np.concatenate([t[2:], t[1:3] + 20.0]), np.concatenate([x[2:], x[1:3]]), cm, "rise_time",
                         repeating_mission=True)["damage"]["D"]
    shifted = cycle_analysis(t + 1234.5, x, cm, "rise_time", repeating_mission=True)["damage"]["D"]
    assert base == pytest.approx(rot, rel=1e-12) == shifted
    nested = cycle_analysis([0, 2, 5, 6, 7, 9, 12, 20.0], [40, 100, 100, 80, 90, 70, 100, 40.0], cm, "rise_time",
                            repeating_mission=True)
    ton = {(c["min"], c["max"]): c["t_heating_s"] for c in nested["cycles"]}
    assert ton == {(80.0, 90.0): pytest.approx(1.0), (70.0, 100.0): pytest.approx(3.0), (40.0, 100.0): pytest.approx(2.0)}
    zero = cm.nf({"range": 60.0, "min": 40.0, "mean": 70.0, "max": 100.0}, 0.0)
    missing = cm.nf({"range": 60.0, "min": 40.0, "mean": 70.0, "max": 100.0}, None)
    assert zero[0] is None and missing[0] is None                           # never replaced by 1 s
    with pytest.raises(InputValidationError):
        cycle_analysis([0.0, 1.0, 10.0], [40.0, 100.0, 70.0], repeating_mission=True)   # a warm-up is not periodic


def test_pt09_fixed_policy_comparison_fixes_the_modulation():
    b = api.EXAMPLE_EFFICIENCY["compare"]
    mix = {**b, "B": {**b["B"], "module": {**b["B"]["module"], "modulation": "dpwm1"}},
           "requests": [[6000.0, 150.0, 600.0]]}
    with pytest.raises(InputValidationError):
        api.module_compare({"compare": mix, "mission": None})
    r = api.module_compare({"compare": {**b, "requests": [[6000.0, 150.0, 600.0]]}, "mission": None})
    assert r["common_modulation"] == "svpwm" and "semiconductor" in r["ranking_scope"]
    assert not any("common to both" in s for s in r["not_evaluated"])
    assert any("not asserted to cancel" in s for s in r["not_evaluated"])
