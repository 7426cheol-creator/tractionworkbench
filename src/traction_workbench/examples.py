"""The built-in SYNTHETIC project: one set of product data for every page (system review R2).

Every page example used to carry its own copy of the product data - the module in four places, the DC-link
capacitor in three, the dead time as 1.5 us on the loss pages and 1.0 us on the EMI page.  Here each product value
exists once; ``project.Project`` validates it and composes the request fields of every analysis from it, and the
page examples in ``api`` take their product data from that composition.

All values are synthetic demonstration data (no real product).  Scenario inputs - operating points, missions,
requirements, study variants - stay with the analyses; only what is physically the same in every analysis is here.
"""

from __future__ import annotations

from pathlib import Path

INF = float("inf")


def examples_dir() -> Path | None:
    """The shipped ``examples`` folder (case files, drives, datasheets): the PyInstaller bundle or the source tree."""
    import sys
    cands = []
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        cands.append(Path(frozen) / "examples")
    cands.append(Path(__file__).resolve().parents[2] / "examples")
    for c in cands:
        if c.is_dir():
            return c
    return None


def lin_curve(unit, temps, i_max, a_by_t, b_by_t, n=9):
    """Synthetic linear curve value = a(T) + b(T) * I on n currents in [0, i_max] (demonstration stand-in)."""
    cur = [round(i_max * k / (n - 1), 6) for k in range(n)]
    return {"unit": unit, "temps_C": list(temps), "currents_A": cur,
            "values": [[round(a + b * i, 6) for i in cur] for a, b in zip(a_by_t, b_by_t)],
            "source": "synthetic example curve (not a product datasheet)"}


# -- power module: device data + evaluation convention + its own thermal path ------------------------------------
MODULE_RTH_K_PER_W = 0.09
MODULE = {
    "name": "synthetic 750 V / 800 A IGBT half-bridge example (NOT a real product - replace with datasheet curves)",
    "technology": "IGBT", "value_kind": "typical", "energy_basis": "per_device", "v_test_V": 600.0,
    "source": "synthetic example for demonstration; curves are linear stand-ins",
    "test_conditions": {"Rg_on_ohm": 1.8, "Rg_off_ohm": 1.8, "Vge_V": 15.0, "deadtime_test_us": 1.5,
                        "stray_L_nH": 20.0},
    "curves": {
        "v_on": lin_curve("V", (25.0, 150.0), 800.0, (0.80, 0.70), (1.10e-3, 1.60e-3)),
        "v_rev": lin_curve("V", (25.0, 150.0), 800.0, (0.90, 0.80), (1.00e-3, 1.30e-3)),
        "e_on": lin_curve("mJ", (25.0, 150.0), 800.0, (0.3, 0.5), (0.034, 0.045)),
        "e_off": lin_curve("mJ", (25.0, 150.0), 800.0, (0.4, 0.6), (0.040, 0.050)),
        "e_rr": lin_curve("mJ", (25.0, 150.0), 800.0, (0.2, 0.3), (0.015, 0.022)),
    },
    "parallel": 1, "sharing_error_pct": 0.0, "driver_aux_W": 12.0, "aux_from_hv_dc": False, "Tj_eval_C": 150.0,
    "thermal_path": {
        "Rth_K_per_W": MODULE_RTH_K_PER_W, "T_ref_C": 65.0,
        "foster": {"R_K_per_W": [f * MODULE_RTH_K_PER_W for f in (0.12, 0.29, 0.35, 0.24)],
                   "tau_s": [0.005, 0.08, 0.8, 6.0]},
        "basis": "synthetic junction-to-coolant path of the hottest position; the Foster network's steady sum is "
                 "the path's Rth (one thermal path for the loss, efficiency, PWM and lifetime analyses)"},
}

MODULE_SIC = {
    "name": "synthetic 750 V / 800 A SiC MOSFET half-bridge example (NOT a real product - replace with datasheet curves)",
    "technology": "SiC_MOSFET", "value_kind": "typical", "energy_basis": "per_device", "v_test_V": 600.0,
    "source": "synthetic example for demonstration; curves are linear stand-ins",
    "test_conditions": {"Rg_on_ohm": 2.5, "Rg_off_ohm": 1.0, "Vgs_on_V": 18.0, "Vgs_off_V": -4.0,
                        "deadtime_test_us": 0.3, "stray_L_nH": 12.0},
    "curves": {
        "v_on": lin_curve("V", (25.0, 150.0), 800.0, (0.0, 0.0), (1.60e-3, 2.60e-3)),
        "v_channel_rev": lin_curve("V", (25.0, 150.0), 800.0, (0.0, 0.0), (1.65e-3, 2.70e-3)),
        "v_rev": lin_curve("V", (25.0, 150.0), 800.0, (2.90, 2.60), (1.30e-3, 1.50e-3)),
        "e_on": lin_curve("mJ", (25.0, 150.0), 800.0, (0.05, 0.08), (0.012, 0.014)),
        "e_off": lin_curve("mJ", (25.0, 150.0), 800.0, (0.03, 0.04), (0.006, 0.007)),
        "e_rr": lin_curve("mJ", (25.0, 150.0), 800.0, (0.01, 0.02), (0.0008, 0.0012)),
    },
    "parallel": 1, "sharing_error_pct": 0.0, "driver_aux_W": 14.0, "aux_from_hv_dc": False, "Tj_eval_C": 150.0,
    "thermal_path": {"Rth_K_per_W": 0.12, "T_ref_C": 65.0,
                     "basis": "synthetic junction-to-coolant Rth of the SiC design (no transient network declared)"},
}

# -- cooling system and the thermal networks of the thermal page -------------------------------------------------
COOLANT_LOOP = [{"station": "inverter", "losses": {"inverter": 1.0}},
                {"station": "motor", "losses": {"copper": 1.0, "rotational": 1.0}}]

THERMAL = {
    "model_id": "EXAMPLE_THERMAL_UNVALIDATED", "revision": "2", "validated": False, "origin": "synthetic",
    "validation_evidence": "",
    "source": "synthetic example networks for demonstration (not a product model)",
    "coolant": {"glycol_vol_pct": 50.0, "flow_L_per_min": 10.0, "cp_J_per_kgK": None, "rho_kg_per_m3": None,
                "reference": "mean", "loop": COOLANT_LOOP},
    "nodes": [
        {"id": "inverter junction (1 of 6 switches)", "network": "foster",
         "R_K_per_W": [0.010, 0.030, 0.080, 0.080], "tau_s": [0.002, 0.03, 0.4, 2.5],
         "flow_dependent": [False, False, False, True], "flow_ref_L_per_min": 10.0, "flow_exponent": 0.8,
         "limit_C": 150, "loss_share": {"inverter": 1 / 6}, "station": "inverter"},
        {"id": "stator winding (hot spot)", "network": "cauer",
         "R_K_per_W": [0.003, 0.005, 0.006], "C_J_per_K": [1500.0, 6000.0, 30000.0],
         "limit_C": 180, "loss_share": {"copper": 1.0}, "station": "motor"},
    ],
}

# -- FTTI chain and project safe-state rules ---------------------------------------------------------------------
FTTI_CHAIN = {
    "chain_id": "FC-OC-01", "fault": "phase overcurrent", "ftti_ms": 30, "detection_event": "confirmed",
    "fdti_budget_ms": 10, "frti_budget_ms": 15, "safe_event": "safe_state", "endpoint_kind": "physical_safe_state",
    "worst_case_attainable": False,
    "events": ["fault", "sensed", "filtered", "detected", "confirmed", "reaction_request", "gate_off", "safe_state"],
    "items": [
        {"id": "HW_SENSE", "from": "fault", "to": "sensed", "owner": "HW", "min_ms": 0.2, "max_ms": 0.5},
        {"id": "ADC_FILTER", "from": "sensed", "to": "filtered", "owner": "SW", "min_ms": 0.5, "max_ms": 1.0, "period_ms": 0.1},
        {"id": "DETECT", "from": "filtered", "to": "detected", "owner": "SW", "min_ms": 1.0, "max_ms": 2.0, "period_ms": 1.0},
        {"id": "DEBOUNCE", "from": "detected", "to": "confirmed", "owner": "SW", "min_ms": 5.0, "max_ms": 5.0},
        {"id": "SW_REACT", "from": "confirmed", "to": "reaction_request", "owner": "SW", "min_ms": 2.0, "max_ms": 10.0},
        {"id": "SYS_FRTI", "from": "confirmed", "to": "safe_state", "owner": "System", "max_ms": 20.0},
        {"id": "GATE", "from": "reaction_request", "to": "gate_off", "owner": "HW", "min_ms": 0.1, "max_ms": 0.2},
        {"id": "DECAY", "from": "gate_off", "to": "safe_state", "owner": "HW", "min_ms": 1.0, "max_ms": 3.0},
    ],
}

SAFE_STATE_RULES = [{"rule_id": "PRJ-SR-01", "when": {"Vdc_below_V": 60}, "require": "FREEWHEEL",
                     "basis": "project safety concept: HVDC < 60 V -> force the freewheel path (project rule, "
                              "not physics)"}]

# -- gearbox: torsional ROM and reducer efficiency of the same single-speed reducer --------------------------------
REDUCER = {
    "ratio": 9.0, "output_boundary": "single-speed gearbox output shaft (differential input)",
    "speed_rpm": [0.0, 16000.0], "torque_Nm": [0.0, 400.0], "oil_temp_C": [20.0, 120.0],
    "eta_forward": 0.975, "eta_reverse": 0.970, "drag_coeffs": [0.15, 2.0e-4, 0.0],
    "basis": "synthetic example (declare supplier map / directional test data)",
}

DRIVELINE_ROM = {
    "Jm_kgm2": 0.04, "J_out_kgm2": 200.0, "k_out_Nm_per_rad": 12000.0, "c_out_Nms_per_rad": 30.0, "ratio": 9.0,
    "wheel_radius_m": 0.33, "contact": "maintained", "backlash_out_rad": None,
    "basis": "synthetic two-inertia ROM (1800 kg, r 0.33 m, two half-shafts) - replace with an FRF-identified / "
             "validated torsional model",
}

VEHICLE = {
    "mass_kg": 1900.0, "wheel_radius_m": 0.33, "J_wheels_kgm2": 4.0,
    "road_load": {"form": "abc", "A_N": 140.0, "B_N_per_mps": 0.72, "C_N_per_mps2": 0.36, "includes_edrive_drag": False,
                  "basis": "synthetic road load of a 1.9 t passenger car (tyres + aerodynamics; the drive's own drag is "
                           "modelled by the reducer and machine) - replace with coast-down / target values"},
    "axle": {"ratio": 1.0, "eta_forward": 1.0, "eta_reverse": 1.0},
    "regen": {"share": 1.0, "min_speed_kmh": 5.0},
    "aux_hv_W": 300.0, "usable_energy_kWh": 75.0,
    "basis": "synthetic vehicle for the drive-cycle view (test mass incl. driver and payload; the machine inertia "
             "comes from the driveline ROM)",
}

CHARGING_PATH = {
    "neutral_access": True, "L0_uH": 30.0,
    "L0_basis": "synthetic estimate (zero-sequence = mostly leakage inductance, ~15 % of L_d) - measure it: the three "
                "phase terminals joined, against the neutral, L0 = 3 x the measured inductance",
    "neutral_current_max_A": 400.0, "phase_current_peak_max_A": 450.0, "Tj_max_C": 150.0, "winding_loss_max_W": 1500.0,
    "interleave": "120deg",
    "basis": "synthetic integrated-charging path (neutral brought out through a contactor; ratings are examples)",
}

TORQUE_ERRORS = {
    "current_gain_pct": 1.0, "current_offset_A": 2.0, "resolver_offset_deg_e": 0.5, "magnet_temp_dev_K": 15.0,
    "magnet_coeff_per_K": -0.0011, "psi_tol_pct": 1.0, "Ld_tol_pct": 2.0, "Lq_tol_pct": 2.0, "estimator_pct": 1.0,
    "monitor_mismatch_pct": 3.0,
    "magnet_coeff_basis": "typical NdFeB remanence coefficient (-0.11 %/K) - replace with the magnet grade's data",
    "basis": {"current_gain": "sensor total gain error over temperature (= the fault simulation's CS_A/B gain_tol)",
              "current_offset": "residual offset after the power-up calibration (= CS_A/B offset_tol_A)",
              "resolver_offset": "residual angle offset after the end-of-line calibration (= RES offset_tol_deg)",
              "magnet_temperature": "magnet-temperature estimator error (synthetic)",
              "model_tolerance": "unit-to-unit spread of magnets and laminations around the calibrated design "
                                 "(synthetic)",
              "estimator": "torque-table interpolation (synthetic)",
              "monitor_mismatch": "the monitor's independent torque estimate vs the control path (synthetic)"},
    "source": "synthetic error sources of the torque chain",
}

# -- fault simulation: protection architecture and safety requirements (synthetic) --------------------------------
from .extensions.faultsim.example import FAULT_SIM  # noqa: E402  (engine-side data, used by the project below)


SYNTHETIC_PROJECT = {
    "schema": "twb-project/1",
    "project": {
        "id": "SYNTH-TRACTION-200KW", "revision": "A",
        "title": "synthetic 200 kW IPMSM traction drive (single two-level VSI, single-speed reducer)",
        "origin": "synthetic",
        "note": "demonstration data - not a product; replace each section with supplier / measured data and its "
                "evidence",
        "change_log": [{"revision": "A", "change": "initial synthetic project: the product values of the 0.3.0 page "
                                                   "examples, unified (one dead time 1.5 us for losses, sampling, "
                                                   "EMI and PWM transitions)"}],
    },
    "sections": {
        "drive": {
            "provenance": {"origin": "synthetic", "source": "reference/traction_workbench_spec_v1 (manifest-locked)",
                           "revision": "1.0", "qualified": False, "evidence": ""},
            "data": {"builtin": "SYNTH_IPMSM_200KW_REF_V1",
                     "reference_sha256": "0e588727415e51fbf025857ceb0aab9bb17f7c54549b87d9bac3a81e5bfa50e2"}},
        "dc_source": {
            "provenance": {"origin": "synthetic", "source": "spec baseline source limits + example impedance",
                           "revision": "1", "qualified": False, "evidence": ""},
            "data": {"Vdc_nominal_V": 600.0,
                     "limits": {"discharge_power_max_W": 200000.0, "charge_power_max_W": 100000.0,
                                "discharge_current_max_A": 400.0, "charge_current_max_A": 200.0},
                     "impedance": {"R_mohm": 25.0, "L_uH": 2.0,
                                   "basis": "example battery + harness impedance (not measured)"}}},
        "module": {
            "provenance": {"origin": "synthetic", "source": "synthetic example module", "revision": "1",
                           "qualified": False, "evidence": ""},
            "data": MODULE},
        "alternatives": {
            "provenance": {"origin": "synthetic", "source": "synthetic design alternatives for comparison studies",
                           "revision": "1", "qualified": False, "evidence": ""},
            "data": {"modules": [{"key": "sic", "label": "SiC design", "module": MODULE_SIC, "deadtime_us": 0.3,
                                  "basis": "alternative design: its own module, gate drive and dead time"}]}},
        "dc_link": {
            "provenance": {"origin": "synthetic", "source": "synthetic film-capacitor bank example", "revision": "1",
                           "qualified": False, "evidence": ""},
            "data": {"C_uF": 500.0, "ESL_nH": 15.0, "Rth_K_per_W": 0.35, "T_ref_C": 65.0, "T_valid_C": [-40.0, 105.0],
                     "ESR_table": [[100.0, 3.0], [1e3, 2.0], [1e4, 1.6], [1e5, 1.8], [1e6, 3.0]],
                     "ESR_unit": "mohm", "ESR_hf_mohm": 1.0, "life_hours_table": [], "life_voltage_V": None,
                     "life_basis": "", "source": "synthetic film-capacitor bank example (not a product)"}},
        "controller": {
            "provenance": {"origin": "synthetic", "source": "example target settings", "revision": "1",
                           "qualified": False, "evidence": ""},
            "data": {
                "fsw_kHz": 10.0, "modulation": "svpwm", "deadtime_us": 1.5, "carrier": "asynchronous",
                "gate": {"t_rise_ns": 50.0, "t_fall_ns": 50.0, "Rg_on_ohm": 1.8, "Rg_off_ohm": 1.8, "Vge_V": 15.0,
                         "basis": "example gate setting (not a measured switch-node waveform)"},
                "current_loop": {"Ld_uH": 200.0, "Lq_uH": 400.0, "R_mohm": 15.0, "bandwidth_Hz": 450.0,
                                 "gain_mapping": "continuous", "reference_fsw_kHz": 10.0,
                                 "integrator_storage": "output", "on_transition": "keep", "anti_windup": True,
                                 "basis": "example PI per axis, pole-zero cancellation at the declared design L_d / "
                                          "L_q (the plant is the machine's differential inductance at each "
                                          "operating point); bandwidth chosen for >= 45 deg SAMPLED-loop margin at "
                                          "the lowest normal update rate (8 kHz)"},
                "timing": {"sample_to_latch_us": 25.0, "filter_delay_us": 5.0, "updates_per_period": 1,
                           "modulator_delay_fraction": 0.5, "min_pulse_us": 1.5, "min_pulse_policy": "drop",
                           "wcet_source": "declared estimate",
                           "basis": "example target timing (replace with the measured delay chain of the ECU)"},
                "sensing": {"kind": "leg_shunt", "settle_us": 2.0, "aperture_us": 0.6, "sample_points": "valley",
                            "edge_noise": "own_leg", "reconstruct_from_two": True, "channel_skew_ns": 200.0,
                            "invalid_policy": "hold", "max_sample_age_us": 150.0, "current_error_max_A": 15.0,
                            "basis": "example: three Kelvin-connected low-side shunts, simultaneous sampling ADCs, "
                                     "the other legs' edges assumed outside the aperture - replace with the target "
                                     "trigger / ADC timing"},
                "torque_path": {"sample_ms": 1.0, "delay_ms": 2.0, "actuator_tau_ms": 1.5,
                                "basis": "example timing / current-loop ROM (replace with the target delay chain "
                                         "and a validated torque response)"},
            }},
        "thermal": {
            "provenance": {"origin": "synthetic", "source": "synthetic example networks", "revision": "2",
                           "qualified": False, "evidence": ""},
            "data": THERMAL},
        "driveline": {
            "provenance": {"origin": "synthetic", "source": "synthetic vehicle / reducer example", "revision": "1",
                           "qualified": False, "evidence": ""},
            "data": {"rom": DRIVELINE_ROM, "reducer": REDUCER}},
        "vehicle": {
            "provenance": {"origin": "synthetic", "source": "synthetic vehicle (mass, road load, regeneration, "
                                                          "auxiliaries)", "revision": "1", "qualified": False,
                           "evidence": ""},
            "data": VEHICLE},
        "charging": {
            "provenance": {"origin": "synthetic", "source": "synthetic integrated-charging path", "revision": "1",
                           "qualified": False, "evidence": ""},
            "data": CHARGING_PATH},
        "torque_errors": {
            "provenance": {"origin": "synthetic", "source": "synthetic torque-chain error sources", "revision": "1",
                           "qualified": False, "evidence": ""},
            "data": TORQUE_ERRORS},
        "safety": {
            "provenance": {"origin": "synthetic", "source": "example FTTI chain and project rule", "revision": "1",
                           "qualified": False, "evidence": ""},
            "data": {"ftti_chains": [FTTI_CHAIN], "safe_state_rules": SAFE_STATE_RULES}},
        "fault_sim": {
            "provenance": {"origin": "synthetic", "source": "synthetic protection architecture and safety requirements "
                                                          "(demonstration of the causal fault simulation)",
                           "revision": "1", "qualified": False, "evidence": ""},
            "data": FAULT_SIM},
        "emi_setup": {
            "provenance": {"origin": "synthetic", "source": "synthetic HV network and test setup", "revision": "1",
                           "qualified": False, "evidence": ""},
            "data": {"C_y_nF": 100.0, "L_y_nH": 10.0, "R_y_mohm": 5.0, "C_par_nF": 2.0, "R_par_ohm": 1.0,
                     "L_par_nH": 100.0, "R_h_mohm": 5.0, "L_h_uH": 1.0, "L_ch_uH": 0.0, "k_ch": 0.0,
                     "an_L_uH": 5.0, "an_C_coup_nF": 100.0, "an_R_meas_ohm": 50.0, "an_R_par_ohm": 1000.0,
                     "an_C_sup_uF": 1.0, "R_bat_mohm": 10.0, "L_bat_uH": 0.0, "validated_up_to_MHz": None,
                     "basis": "synthetic example network (not characterised); the artificial network and the supply "
                              "behind it are the TEST SETUP, not the vehicle battery impedance of the ripple analysis"}},
    },
}
