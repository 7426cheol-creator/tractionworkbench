"""Exchange package for a MathWorks (MATLAB / Simulink / Simscape) port and cross-tool parity checks.

One versioned JSON document with what a port must preserve item by item: port / boundary identities and sign
conventions, loss ownership, model and data identities with their revisions and domains, mechanical reference
coordinates and referral, the policy / controller identities, the example inputs, and the reference fixtures with
the values THIS implementation computes for them (so the package always matches the code that wrote it).

Python-MATLAB parity is implementation verification (V0-V4); it is not a physical qualification (V5-V6).
"""

from __future__ import annotations

import datetime as _dt
import math

from . import __version__

SCHEMA = "twb-exchange/1"


def conventions() -> dict:
    return {
        "power_ports": {
            "sign": "electrical -> mechanical positive at every port",
            "P_dc": "input at the DECLARED inverter HV DC terminal",
            "P_ac": "input at the motor AC terminals (dq: 1.5 (vd id + vq iq), amplitude-invariant, phase peak)",
            "P_m": "output at the motor shaft = T_shaft * omega_m (T_em * omega_m is conversion power, not shaft power)",
            "P_o": "output at the DECLARED reducer / eDrive output (gearbox output, differential output, half-shaft "
                   "sum and tyre contact are different boundaries)",
        },
        "efficiency_boundaries": {
            "inverter": "P_dc <-> P_ac", "motor": "P_ac <-> P_m", "inverter_motor": "P_dc <-> P_m",
            "reducer": "P_m <-> P_o", "edrive": "P_dc <-> P_o",
            "forward": "eta = P_out / P_in (both > tol)", "reverse": "eta = |P_in| / |P_out| (both < -tol)",
            "N/A": "definition does not apply: mixed flow, no useful output, no throughput",
            "UNKNOWN": "definable but not established: missing port power / loss, denominator within uncertainty",
            "INCONSISTENT": "eta > 1 or power leaving at both ports; reported raw, never clamped",
            "telescoping": "eta_im = eta_inv eta_m, eta_ed = eta_inv eta_m eta_r only for the same point / direction",
            "mission": "direction efficiencies are energy ratios of their own segments; net output / net input is "
                       "not a conversion efficiency",
        },
        "loss_ownership": {
            "inverter": "datasheet module model (conduction + switching per device, driver aux if drawn from HV) OR "
                        "the declared quadratic surrogate - never both summed",
            "motor": "fundamental copper 1.5 Rs |i|^2 + rotational / iron loss-equivalent torque; PWM harmonic copper "
                     "3 sum I_nu^2 R_ac(f_nu) separately; iron / magnet harmonic loss only as a declared bound",
            "reducer": "directional model: drag at the motor side + mesh efficiency per direction, or loss maps",
        },
        "claims": {"FEASIBLE": "evidence that the declared model / conditions / fixed policy meet the requirement",
                   "INFEASIBLE": "a valid witness or bound refutes it (policy failure vs physical impossibility kept "
                                 "apart)",
                   "UNKNOWN": "outside the model, missing input, extrapolation, solver failure, error > margin"},
        "pwm": {"carrier_Hz": "PWM carrier / switching frequency (not the electrical fundamental)",
                "pulse_ratio": "N_p = f_carrier / f_e, f_e = p |omega_m| / (2 pi); N/A at standstill",
                "delay_ledger": "filter + (sample -> applied at the next reload) + declared modulator fraction; "
                                "each delay counted once; a missed deadline is a violation",
                "policy": "stateful causal q_next = policy(q, measurements): first matching rule, hysteresis, "
                          "minimum dwell, protective pre-emption, fallback",
                "chatter": "peak-to-peak measurement noise >= hysteresis on a rule input is a violation; undeclared "
                           "noise is not evaluated",
                "sampling": "a current sample is valid only if its declared window (settle after the last edge + "
                            "aperture) is edge-free: inline / low-side leg shunt (valley) / single DC-link shunt (two "
                            "active vectors per half period); invalid samples are held or predicted, never replaced "
                            "by the true current; held error <= omega_e I_pk age; skew error = skew (V1/L_hf + "
                            "omega_e I_pk)",
                "transition": "one decoupled current-loop axis at a constant point: integrator in volts is bumpless; "
                              "an error-sum integrator under Ki*Ts remapping jumps by (Ts_to/Ts_from - 1) v_ss; a "
                              "reset drops v_ss; fixed discrete gains scale the effective Ki by f_to/f_from"},
        "driveline": {"coordinates": "motor side; ideal gear g = omega_m / omega_gear_out before the elastic element",
                      "referral": "J_l = J_out / g^2, k = k_out / g^2, c = c_out / g^2, T_L = T_L,out / g",
                      "states": "delta = theta_m - theta_l, T_s = k delta + c delta', J_m omega_m' = T_act - T_s, "
                                "J_l omega_l' = T_s - T_L",
                      "energy": "E' = T_act omega_m - T_L omega_l - c delta'^2",
                      "actuator": "T_act is the applied torque after arbitration, clipping, delay and the actuator ROM",
                      "sampled_loop": "ZOH + exact fractional delay (modified z-transform); rigid-body mode excluded",
                      "sensing": "load speed sampled 'skew' before the motor speed; during a dropout the last value is "
                                 "held with its age; beyond the declared stale limit the damping fades out (no "
                                 "declared limit + stale use -> UNKNOWN)",
                      "authority": "an undeclared torque window is UNKNOWN (missing is not unlimited); clipping is "
                                   "attributed per side (negative = regen / charge-acceptance reserve)",
                      "safety": "a safety request is judged on its own reaction time; comfort metrics cover the window "
                                "before the request"},
    }


def fixtures() -> dict:
    """Reference fixtures with the values this implementation computes (inputs, outputs, tolerance)."""
    from .analysis.efficiency import five_boundaries, mission_energy
    from .extensions import driveline as D
    from .extensions.pwm_policy import phase_lag_deg, phase_ripple

    def eff(pdc, pac, pm, po):
        r = five_boundaries(pdc, pac, pm, po)
        return {k: {"status": r[k]["status"], "eta": r[k]["eta"], "loss_W": r[k]["loss_W"]}
                for k in ("inverter", "motor", "inverter_motor", "reducer", "edrive")}
    h = 3600.0
    e06 = mission_energy([{"duration_s": h, "P_dc": 10e3, "P_ac": 9.6e3, "P_m": 9.3e3, "P_o": 9e3},
                          {"duration_s": h, "P_dc": -4e3, "P_ac": -4.3e3, "P_m": -4.7e3, "P_o": -5e3},
                          {"duration_s": h, "P_dc": 1e3, "P_ac": 0.0, "P_m": 0.0, "P_o": 0.0}])
    d01 = D.Driveline(0.2, 2.0, 3000.0, 2.0, 1.0, basis="D-01 synthetic")
    co = D.relative_mode_coefficients(d01, 5.0)
    cr = D.delay_crossings(co["a1"], co["a0"], co["b1"], 0.05)[0]
    d03 = D.rhp_roots_at(co["a1"], co["a0"], co["b1"], 0.016)
    rip = phase_ripple(600.0, 0.8, 0.3, 400.0, 10e3, 150e-6)
    from .extensions.pwm_policy import CurrentLoop, SensingConfig, TimingConfig, sampling_validity, transition_transient
    shunt = sampling_validity(0.8, "svpwm", 10e3, 50.0, SensingConfig("dc_link_shunt", 0.0, 0.0, basis="fixture"))
    lp = dict(L_H=300e-6, R_ohm=15e-3, Kp=2 * math.pi * 500 * 300e-6, Ki=2 * math.pi * 500 * 15e-3,
              gain_mapping="continuous", reference_fsw_Hz=10e3, basis="fixture")
    tcf = TimingConfig(25e-6, 5e-6, 1, 0.5, 1.5e-6, "fixture")
    jumps = {name: transition_transient(CurrentLoop(**lp, **kw), tcf, 10e3, 20e3, -150.0, 180.0, 346.0)["output_jump_V"]
             for name, kw in (("volts_keep", {}), ("error_sum_keep", {"integrator_storage": "error_sum"}),
                              ("volts_reset", {"on_transition": "reset"}))}
    return {
        "E-01": {"inputs_W": [100e3, 97e3, 92e3, 88e3], "result": eff(100e3, 97e3, 92e3, 88e3), "tol_abs": 1e-12},
        "E-02": {"inputs_W": [-87e3, -90e3, -95e3, -100e3], "result": eff(-87e3, -90e3, -95e3, -100e3),
                 "tol_abs": 1e-12},
        "E-03": {"inputs_W": [6300.0, 4500.0, 0.0, 0.0], "result": eff(6300.0, 4500.0, 0.0, 0.0), "tol_abs": 1e-12},
        "E-05": {"note": "synthetic drive, 1000 rpm, id 0 A, iq -1 A", "P_m_W": -84.764307, "P_ac_W": -62.809353,
                 "P_dc_W": 137.198647, "motor_eta": 0.74099, "inverter": "N/A (mixed flow)", "tol_abs": 1e-6},
        "E-06": {"eta_traction": e06["eta_traction"], "eta_regeneration": e06["eta_regeneration"],
                 "E_dc_net_kWh": e06["E_dc_net_J"] / 3.6e6, "E_out_net_kWh": e06["E_out_net_J"] / 3.6e6,
                 "tol_rel": 1e-12},
        "D-01": {"inputs": {"Jm": 0.2, "Jl": 2.0, "k": 3000.0, "c": 2.0}, **{k: d01.modal()[k] for k in ("f_n_Hz", "zeta")},
                 "tol_abs": {"f_n_Hz": 5e-8, "zeta": 5e-9}},
        "D-02": {"Kd": 5.0, "zeta": D.undelayed_damping(d01, 5.0)["zeta"], "tol_abs": 5e-9},
        "D-03": {"characteristic": "s^2 + 11 s + 16500 + 25 s exp(-s tau)", "first_crossing_tau_s": cr["tau_s"],
                 "first_crossing_omega_rad_s": cr["omega_rad_s"], "tau_s": 0.016,
                 "rhp_root": d03["rightmost_root"], "n_rhp": d03["n_rhp"]},
        "D-04": {"base_Nm": 195.0, "amplitude_Nm": 20.0, "window_Nm": [-120.0, 200.0],
                 "mean_correction_Nm": -(40.0 * math.cos(math.asin(0.25)) - 5.0 * (math.pi - 2 * math.asin(0.25)))
                 / (2 * math.pi)},
        "D-05": {"g": 9.0, "J_out": 162.0, "k_out": 243000.0, "c_out": 162.0, "referred_equals": "D-01"},
        "PWM-delay": {"tau_s": {"10k": 1.5 / 10e3, "5k": 1.5 / 5e3},
                      "phase_at_20.4438226_Hz_deg": {"10k": phase_lag_deg(20.4438226, 1.5 / 10e3),
                                                     "5k": phase_lag_deg(20.4438226, 1.5 / 5e3)},
                      "phase_at_1000_Hz_deg": {"10k": phase_lag_deg(1000.0, 1.5 / 10e3),
                                               "5k": phase_lag_deg(1000.0, 1.5 / 5e3)},
                      "note": "tau = 1.5 / fsw is a synthetic timing assumption, not a target model"},
        "PWM-ripple": {"inputs": {"Vdc_V": 600.0, "m": 0.8, "alpha_rad": 0.3, "fe_Hz": 400.0, "fsw_Hz": 10e3,
                                  "L_hf_H": 150e-6, "modulation": "svpwm", "carrier": "synchronous, regular sampled"},
                       "ripple_rms_A_time": rip["ripple_rms_A"], "ripple_rms_A_spectrum": rip["ripple_rms_spectrum_A"],
                       "tol_rel": 1e-3},
        "PWM-single-shunt": {"inputs": {"m": 0.8, "fsw_Hz": 10e3, "fe_Hz": 50.0, "modulation": "svpwm"},
                             "closed_form": "per half period (sqrt3/4) m Ts sin(pi/3 - t) and (sqrt3/4) m Ts sin(t), "
                                            "t = sector-local angle at the carrier centre; window = the shorter",
                             "first_windows_s": [float(x) for x in shunt["window_s"][:6]], "tol_abs_s": 1e-15},
        "PWM-transition": {"inputs": {**{k: v for k, v in lp.items() if k != "basis"}, "i_ref_A": -150.0, "e_V": 180.0,
                                      "V_max_V": 346.0, "fsw_from_Hz": 10e3, "fsw_to_Hz": 20e3,
                                      "timing": {"sample_to_latch_s": 25e-6, "filter_delay_s": 5e-6,
                                                 "modulator_delay_fraction": 0.5}},
                           "output_jump_V": jumps, "expected": {"volts_keep": 0.0, "error_sum_keep": -0.5 * 177.75,
                                                                "volts_reset": -177.75}, "tol_abs_V": 1e-9},
    }


def build_package(include_examples: bool = True) -> dict:
    from . import api
    from . import service as S
    d = S.resolve_drive(None)
    pkg = {
        "schema": SCHEMA, "tool": "traction-workbench", "tool_version": __version__,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "scope": "implementation verification exchange: conventions, identities, domains and fixtures; Python-MATLAB "
                 "parity does not qualify the physics (hardware / bench / vehicle evidence is separate)",
        "conventions": conventions(),
        "model_identity": {"drive_id": d.drive_id, "revision": d.revision, "provenance": d.provenance.to_dict(),
                           "flux_model": d.motor.flux.kind, "pole_pairs": d.motor.pole_pairs},
        "fixtures": fixtures(),
    }
    if include_examples:
        from .decision import jsonable as _jsonable
        pkg["example_inputs"] = _jsonable({"efficiency": api.EXAMPLE_EFFICIENCY, "reducer": api.EXAMPLE_REDUCER,
                                           "module_igbt": api.EXAMPLE_MODULE, "module_sic": api.EXAMPLE_MODULE_SIC,
                                           "pwm": api.EXAMPLE_PWM, "driveline": api.EXAMPLE_DRIVELINE,
                                           "machine": api.EXAMPLE_MACHINE, "winding": api.EXAMPLE_WINDING})
    return pkg
