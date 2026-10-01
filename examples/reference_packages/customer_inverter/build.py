"""Builds the inverter FuSa reference package from the reference document (included by agreement).
The scenario library is the application's neutral one (refexample), its parameter references renamed to the
document's parameter names; the items are the document's items with their provenance, ASIL literal, agreement status
and the checks that verify them.

    python build.py  ->  src/traction_workbench/extensions/faultsim/packages/customer_inverter_reference.json
"""
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from traction_workbench.extensions.faultsim import refexample as X  # noqa: E402

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / "doc_extract.json").read_text(encoding="utf-8"))


def C(check, scenario=None, variant=None, variants=None, label="", mode=None, **params):
    c = {"check": check, "params": params}
    if scenario:
        c["scenario"] = scenario
    if variant:
        c["variant"] = variant
    if variants:
        c["variants"] = list(variants)
    if mode:
        c["mode"] = mode
    if label:
        c["label"] = label
    return c


def M(reason):
    return C("manual", reason=reason)


# ================================================================================================= parameters
# the application speaks of the source's requirements, never of "customer requirements": the document's tag for a
# requirement recovered from an earlier project (CUSTOMER-PAST) is written PAST-PROJECT
TAG = {"CUSTOMER-PAST": "PAST-PROJECT"}


def P(pid, unit, prov, value=None, ill=None, note="", **kw):
    d = {"id": pid, "unit": unit, "provenance": TAG.get(prov, prov), "value": value, "note": note}
    if ill is not None:
        d["illustrative"] = ill
    d.update(kw)
    return d


TOLMAP_ILL = [[0.0, 5.0], [6000.0, 7.5], [12000.0, 10.0]]
PARAMETERS = [
    P("VW_MAX_ASIL", "ASIL", "OPEN", note="FUSA_Param에서 해석. MAX를 C/D로 임의 치환 금지"),
    P("VW_MAX_ASIL_P1", "ASIL", "OPEN", note="P1 torque path"),
    P("VW_MAX_ASIL_P2", "ASIL", "OPEN", note="P2 torque path"),
    P("VW_MAX_PMHF_TORQUE", "FIT", "OPEN", ill=100.0, note="TORQUE metric"),
    P("VW_MIN_SPFM_TORQUE", "-", "OPEN", ill=0.97, note="TORQUE metric (fraction)"),
    P("VW_MIN_LFM_TORQUE", "-", "OPEN", ill=0.8, note="TORQUE metric (fraction)"),
    P("VW_MAX_PMHF_DESTAB", "FIT", "OPEN", ill=100.0, note="DESTAB metric"),
    P("VW_MIN_SPFM_DESTAB", "-", "OPEN", ill=0.97, note="DESTAB metric (fraction)"),
    P("VW_MIN_LFM_DESTAB", "-", "OPEN", ill=0.8, note="DESTAB metric (fraction)"),
    P("VW_T_SHUTOFF", "ms", "OPEN", ill=50.0, note="14714 triggering criterion -> safe state"),
    P("VW_MAX_FTTI_TORQUE", "ms", "OPEN", ill=100.0, note="torque window deviation -> default error response; "
                                                            "exact origin to be confirmed"),
    P("VW_FTTI_STD", "ms", "OPEN", ill=50.0, note="standby / no-torque"),
    P("VW_MAX_SAFETY_TASK_TIME", "ms", "OPEN", ill=10.0, note="14713 safety task upper bound"),
    P("VW_T_KL15_SW_SHUTOFF", "ms", "OPEN", ill=20.0, note="14730 SW KL15 OFF"),
    P("VW_T_KL15_HW_SHUTOFF", "ms", "OPEN", ill=20.0, note="14731 HW KL15 OFF"),
    P("VW_TORQUE_TOL_P1", "(rpm, N*m)", "OPEN", ill=TOLMAP_ILL, note="14741 actual-speed dependent tolerance map"),
    P("VW_TORQUE_TOL_P2", "(rpm, N*m)", "OPEN", ill=TOLMAP_ILL, note="14741 actual-speed dependent tolerance map"),
    P("C_t_Zustandsubergang_max", "ms", "OPEN", ill=50.0, note="controlled changeover, purchaser agreement"),
    P("C_M_Min", "N*m", "CUSTOMER-PAST", ill=150.0,
      note="Extended Torque minimum + torque; recovered as about 50 % of the maximum (absolute value not given)"),
    P("C_T_Imax", "ms", "CUSTOMER-PAST", ill=100.0, note="Extended Torque maximum duration / reversion (value not given)"),
    P("V_DISCHARGE_LIMIT", "V", "PROJECT", 60.0, note="past project requirement: active discharge < 60 V"),
    P("T_ACTIVE_DISCHARGE", "ms", "PROJECT", 2000.0, note="past project requirement: < 2 s"),
    P("T_PASSIVE_DISCHARGE", "s", "PROJECT", 120.0,
      note="past project condition: < 120 s; never promoted to the FuSi text automatically"),
    P("X_Low", "V", "CUSTOMER-PAST", ill=60.0, note="HW safe-state threshold: nominal xx V (OPEN), minimum 60 V; "
                                                      "agreed / adjustable"),
    P("X_Low_min", "V", "CUSTOMER-PAST", 60.0, note="the minimum of X_Low"),
    P("X_Upp", "V", "CUSTOMER-PAST", ill=100.0, note="HW safe-state threshold: nominal yy V, max yyy V; exact value "
                                                       "OPEN"),
    P("GDE_to_3PS", "us", "CUSTOMER-PAST", 150.0, note="GDE -> 3PS <= 150 us"),
    P("FOV_REACTION", "us", "CUSTOMER-PAST", 20.0, note="typical safe-state reaction <= 20 us"),
    P("ACFOC_DETECT_REACTION", "us", "CUSTOMER-PAST", 20.0, note="<= 20 us"),
    P("ACFOC_THRESHOLD_FACTOR", "x Imax", "CUSTOMER-PAST", 1.5, note="typical > 1.5 x Imax +-10 %, outside normal range"),
    P("ACFOC_THRESHOLD_TOL", "-", "CUSTOMER-PAST", 0.1, note="+-10 %"),
    P("DCFOC_DETECT", "us", "CUSTOMER-PAST", 20.0, note="max detection 20 us; reaction not specified"),
    P("DCFOC_THRESHOLD_FACTOR", "x Imax", "CUSTOMER-PAST", 1.5, note="typical > 1.5 x Imax +-10 %"),
    P("HV_SUPPLY_NO_LV_MIN", "V", "CUSTOMER-PAST", 40.0, note="power path fully functional at HV >= 40 V without LV"),
    P("T_STNDBY_IDLE_GUARD", "ms", "CUSTOMER-PAST", 50.0, note="STNDBY -> IDLE enabled only 50 ms after PowerOff -> "
                                                               "STNDBY"),
    P("T_STNDBY_IDLE_COMPLETE", "ms", "CUSTOMER-PAST", 150.0, note="with valid signals IDLE within 150 ms"),
    P("T_IDLE_STNDBY_PERSIST", "ms", "CUSTOMER-PAST", 1000.0, note="low HV / low speed held 1 s"),
    P("STANDSTILL_DISTANCE_MAX", "m", "CUSTOMER-PAST", 2.0, note="unintended acceleration: <= 2 m"),
    P("BRAKE_DECEL", "m/s^2", "CUSTOMER-PAST", 5.0, note="5 m/s^2 braking after the reaction"),
    P("T_DRIVER_REACTION", "s", "CUSTOMER-PAST", 1.0, note="1 s reaction"),
    P("TORQUE_SG_PMHF_PROJECT", "FIT", "PROJECT", 20.0, note="about 20 FIT given in the past; not in the current "
                                                           "photos"),
    P("VEHICLE_ACCEL_TARGET_CURVE", "(km/h, m/s^2)", "CUSTOMER-PAST", ill=[[0, 2.0], [40, 2.0], [120, 1.0]],
      note="vehicle acceleration target curve (values not given)"),
    P("VEHICLE_SPEED_HAF", "km/h", "CUSTOMER-PAST", 40.0, note="HAF: continuous braking / braking-capability failure "
                                                             "above 40 km/h"),
    P("W_TORQUE_LIM_FACT_P1", "-", "CUSTOMER-PAST", ill=1.1, note="LIMITFACTOR P1 (FUSA_Param; value not given)"),
    P("W_TORQUE_LIM_FACT_P2", "-", "CUSTOMER-PAST", ill=1.1, note="LIMITFACTOR P2 (FUSA_Param; value not given)"),
    P("uASR_SAFE_VALUE", "N*m", "CUSTOMER-PAST", ill=50.0, note="calibratable µASR Safe Value (value not given)"),
    P("uASR_AXLE_TORQUE", "N*m", "CUSTOMER-PAST", ill=900.0, note="µASR Safe Value_e2e: calibratable axle torque"),
    P("WHEEL_TRQ_RATIO", "-", "CUSTOMER-PAST", ill=9.0, note="transmission wheelTrqratio"),
    # the safe-state acceptance (Q05): the document gives the four conditions, not their numbers
    P("SS_TORQUE_TOL", "N*m", "OPEN", ill=5.0, note="torque below which no torque counts as generated (C1..C3)"),
    P("SS_POWER_TOL", "W", "OPEN", ill=500.0, note="DC power below which no energy exchange counts (C1 / C2)"),
    P("SS_TRANSITION", "ms", "OPEN", ill=20.0, note="transition allowance before the conditions are judged"),
    P("TLSR_TORQUE_MIN", "N*m", "OPEN", ill=-450.0, note="lowest torque the TLSR allow in the safe state (C4); the "
                                                         "full TLSR text is not available"),
    P("TLSR_TORQUE_MAX", "N*m", "OPEN", ill=450.0, note="highest torque the TLSR allow in the safe state (C4)"),
    P("GDE_SEQUENCE", "-", "CONFLICT", variants={"SEQ_A": "6SO for 100 us, then lower/upper 3PS",
                                                  "SEQ_B": "3PS with the lower three devices, after 100 us 6SO"},
      note="the recovered records disagree: both kept as variants until the source binds one"),
    # internal (DERIVED) contract values the TSR drafts name - OPEN until decided
    P("MAX_RX_AGE", "ms", "DERIVED", ill=40.0, note="TSR-ADD-002 MAX_RX_AGE"),
    P("TEST_OUTPUT_ENVELOPE_T", "N*m", "DERIVED", ill=20.0, note="TSR-ADD-020 TEST_OUTPUT_ENVELOPE"),
    P("VDC_SAFETY_LIMIT", "V", "DERIVED", ill=850.0, note="TSR-ADD-036 VDC_SAFETY_LIMIT (absolute maximum of the "
                                                          "DC link)"),
    P("ASC_CURRENT_LIMIT", "A", "DERIVED", ill=1600.0, note="TSR-ADD-023 ASC_ENVELOPE current"),
    P("EST_ERROR_BOUND", "N*m", "DERIVED", ill=15.0, note="ACT-06 error budget of the safety torque estimate"),
    P("CHANGEOVER_TORQUE_ENVELOPE", "N*m", "OPEN", ill=250.0, note="E-01 transient torque envelope of a transition"),
    P("REGEN_POWER_MIN", "W", "DERIVED", ill=-30000.0, note="TSR-ADD-035 REGEN_ACCEPTANCE_ENVELOPE (lowest DC power)"),
    P("T_OVERSPEED_RESPONSE", "ms", "DERIVED", ill=50.0, note="TSR-ADD-040"),
    P("T_LATENT_TEST", "ms", "DERIVED", ill=100.0, note="TSR-ADD-019 latent test interval"),
    P("FDTI_BUDGET", "ms", "DERIVED", ill=10.0, note="internal detection budget (never assumed to be a source value)"),
    P("FRTI_BUDGET", "ms", "DERIVED", ill=40.0, note="internal reaction + actuation + settling budget"),
]
PIDS = {p["id"] for p in PARAMETERS}

# ================================================================================================= scenarios
RENAME = {"X_UPP": "X_Upp", "X_LOW": "X_Low", "SAFE_VALUE": "uASR_SAFE_VALUE", "C_M_MIN": "C_M_Min",
          "C_T_IMAX": "C_T_Imax", "V_DISCHARGE": "V_DISCHARGE_LIMIT", "LIMIT_FACTOR": "W_TORQUE_LIM_FACT_P1"}


def rename(obj):
    if isinstance(obj, str) and obj.startswith("$") and obj[1:] in RENAME:
        return "$" + RENAME[obj[1:]]
    if isinstance(obj, dict):
        return {k: rename(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rename(v) for v in obj]
    return obj


def customer_window(obj):
    """The customer-style window monitors take the document's OPEN limit factor and tolerance map."""
    if isinstance(obj, dict):
        if obj.get("kind") in ("torque_window_signed", "torque_integral") and isinstance(obj.get("params"), dict):
            p = dict(obj["params"])
            if p.get("limit_source", "factor") in ("factor", "both"):
                p["limit_factor"] = "$W_TORQUE_LIM_FACT_P1"
            if "tol_map" in p:
                p["tol_map"] = "$VW_TORQUE_TOL_P1"
            p["tol_rule"] = "add"                  # the recovered inequality (T + tol > high / T - tol < low)
            return dict(obj, params=p)
        return {k: customer_window(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [customer_window(v) for v in obj]
    return obj


SC = rename(copy.deepcopy(X.SCENARIOS))
for sid in list(SC):
    if sid in ("S-ENV",):              # a contradiction trips at once: the tolerance does not enter the verdict
        continue
    SC[sid] = customer_window(SC[sid])
# the oscillation monitors judged on their own (the window monitors are other items)
SC["S-OSC"]["scenario"]["additions"] = {"mechanisms": copy.deepcopy(X.MONITORS[2:])}
# the window monitors with the widened tolerance rule of the example are replaced; a common-source variant
SC["S-DEV"]["variants"]["common_source"] = {"faults": [{"kind": "torque_command", "t_ms": 10, "params": {
    "mode": "offset", "value": 35.0, "paths": "both"}}]}
SC["S-ITF"]["variants"]["fallback_e2e"] = {"system": {"interface": {
    "envelope": X.ENVELOPE, "fallback": {"axle_torque_Nm": "$uASR_AXLE_TORQUE", "wheel_ratio": "$WHEEL_TRQ_RATIO"}},
    "inputs": [{"t_ms": 200, "signal": "wheel_speed_valid", "value": 0}]}}
SC["S-VEH"]["variants"]["k0_closed_p1"] = {"vehicle": {"position": "P1", "k0_closed": True},
                                            "faults": copy.deepcopy(SC["S-VEH"]["variants"]["monitor_off"]["faults"])}
SC["S-KL15"]["variants"]["both_off_async"] = {"system": {"inputs": [
    {"t_ms": 10, "signal": "kl15_sw", "value": 0}, {"t_ms": 25, "signal": "kl15_hw", "value": 0},
    {"t_ms": 60, "signal": "kl15_hw", "value": 1}, {"t_ms": 90, "signal": "kl15_sw", "value": 1}]}}
SC["S-DEFERR"]["variants"]["hw_only"] = {"system": {"inputs": [
    {"t_ms": 60, "signal": "kl15_hw", "value": 0}, {"t_ms": 70, "signal": "kl15_hw", "value": 1}]}}
SC["S-MODE"]["variants"]["standby_with_latch"] = {
    "faults": [{"kind": "sensor", "t_ms": 5, "params": {"target": "CS_A", "mode": "offset", "value": 150.0,
                                                          "duration_ms": 5.0}}],
    "system": {"inputs": [{"t_ms": 20, "signal": "target_mode", "value": "standby"},
                          {"t_ms": 80, "signal": "target_mode", "value": "run"}]}}
SC["S-MODE"]["variants"]["t30_ov_with_fault"] = {
    "faults": [{"kind": "sensor", "t_ms": 25, "params": {"target": "CS_A", "mode": "offset", "value": 150.0}}],
    "system": {"inputs": [{"t_ms": 20, "signal": "t30_state", "value": "ov"},
                          {"t_ms": 60, "signal": "t30_state", "value": "normal"}]}}
SC["S-INPUT"]["variants"]["current_lost"] = {"faults": [{"kind": "sensor", "t_ms": 10, "params": {
    "target": "CS_A", "mode": "lost"}}]}
SC["S-INPUT"]["variants"]["current_stuck"] = {"faults": [{"kind": "sensor", "t_ms": 10, "params": {
    "target": "CS_A", "mode": "stuck", "value": 0.0}}]}
SC["S-RESET"]["variants"]["rotating_long"] = {"speed_rpm": 12000.0, "faults": [
    {"kind": "mcu_reset", "t_ms": 10, "params": {"duration_ms": 20.0}}]}

# ================================================================================================= items
ITEMS = []
CUST = ("CONFIRMED", "PAST-PROJECT", "PROJECT")


def I(iid, group, kind, prov, title, checks=None, manual=None, **kw):
    prov = TAG.get(prov, prov)
    it = {"id": iid, "group": group, "kind": kind, "provenance": prov, "title": title, "checks": checks or []}
    if manual:
        it["manual"] = manual
    if kind == "requirement" and prov in CUST:
        it["customer_requirement"] = True
    it.update(kw)
    ITEMS.append(it)
    return it


SS = {"torque_tol_Nm": "$SS_TORQUE_TOL", "power_tol_W": "$SS_POWER_TOL", "tlsr_min_Nm": "$TLSR_TORQUE_MIN",
      "tlsr_max_Nm": "$TLSR_TORQUE_MAX", "transition_ms": "$SS_TRANSITION"}
SS3 = {"torque_tol_Nm": "$SS_TORQUE_TOL", "power_tol_W": "$SS_POWER_TOL"}
QUADS = ("motoring", "regen", "reverse_motoring", "reverse_regen", "step")

# -- reusable check sets ------------------------------------------------------------------------------------------
CK = {
    "ss_asc": C("safe_state", "S-REACT", "asc_12000", origin="gate", **SS, label="ASC at 12,000 rpm"),
    "ss_fw_cx": C("safe_state", "S-REACT", "fw_12000", origin="gate", expect_reached=False, **SS,
                  label="counterexample: 6SO at 12,000 rpm rectifies into the DC link (C2)"),
    "ss_fw_lowhv_cx": C("safe_state", "S-REACT", "fw_low_hv_3000", origin="gate", expect_reached=False, **SS,
                        label="counterexample: 6SO at 50 V / 3000 rpm (HV < 60 V is not automatically safe)"),
    "ss_soft": C("safe_state", "S-REACT", "soft_asc_6000", origin="gate", **SS, label="soft ASC: pulsing steps (C3)"),
    "ss_asc_rev": C("safe_state", "S-REACT", "asc_reverse", origin="gate", **SS, label="ASC in reverse rotation"),
    "ss_fw_3000": C("safe_state", "S-REACT", "fw_3000", origin="gate", **SS, label="6SO at 3000 rpm"),
    "ss_asc_3000": C("safe_state", "S-REACT", "asc_3000", origin="gate", **SS, label="ASC at 3000 rpm"),
    "ss_fw_hvoff": C("safe_state", "S-REACT", "fw_12000_hv_off", origin="gate", **SS,
                     label="6SO at 12,000 rpm, HV disconnected"),
    "timeline": C("timeline", "S-FAULT", expect_recorded=True, ftti_ms="$VW_MAX_FTTI_TORQUE",
                  shutoff_ms="$VW_T_SHUTOFF", torque_tol_Nm="$SS_TORQUE_TOL", power_tol_W="$SS_POWER_TOL",
                  tlsr_min_Nm="$TLSR_TORQUE_MIN", tlsr_max_Nm="$TLSR_TORQUE_MAX"),
    "quad": C("no_detection", "S-QUAD", variants=QUADS, mode="all", label="no false trip in the four quadrants "
                                                                         "and on a healthy step"),
    "dev_time": C("detected", "S-DEV", "small_long", by=["SM-TWIN"], label="time / debounce monitor: small long "
                                                                           "deviation"),
    "dev_int": C("detected", "S-DEV", "chatter", by=["SM-TINT"], label="integral monitor: repeated short deviation"),
    "dev_time_blind": C("detected", "S-DEV", "chatter", by=["SM-TWIN"], expect=False,
                        label="the time monitor alone misses the chatter (why both are needed)"),
    "dev_large": C("detected", "S-DEV", "large_short", label="large short deviation"),
    "dev_sign": C("detected", "S-DEV", "sign_flip", by=["SM-TWIN", "SM-TINT"], label="sign flip of the torque"),
    "dev_ftti": C("timeline", "S-DEV", "small_long", mechanisms=["SM-TWIN", "SM-TINT"], ftti_ms="$VW_MAX_FTTI_TORQUE",
                  torque_tol_Nm="$SS_TORQUE_TOL", power_tol_W="$SS_POWER_TOL",
                  label="deviation -> default error response within VW_MAX_FTTI_TORQUE (fault-onset clock)"),
    "sweep_lf": C("sweep", "S-DEVF", param=["W_TORQUE_LIM_FACT_P1", "VW_TORQUE_TOL_P1"],
                  values=[[1.05, 1.1, 1.3, 1.6], [[[0.0, 0.0]], [[0.0, 5.0], [12000.0, 10.0]],
                                                  [[0.0, 20.0]]]],
                  inner="detected", params={"by": ["SM-TWIN"]},
                  label="break-even: for which LIMITFACTOR / tolerance the small long deviation is detected"),
    "sweep_quad": C("sweep", "S-QUAD", "step", param=["W_TORQUE_LIM_FACT_P1", "VW_TORQUE_TOL_P1"],
                    values=[[1.05, 1.1, 1.3], [[[0.0, 0.0]], [[0.0, 5.0], [12000.0, 10.0]], [[0.0, 20.0]]]],
                    inner="no_detection", params={},
                    label="break-even: for which LIMITFACTOR / tolerance a healthy step does not trip"),
    "window": C("window_semantics", high="max(T*F, T/F) + A", low="min(T*F, T/F) - A", rule="add",
                label="the implemented signed window equals the recovered formula (every F / tolerance)"),
    "env_contra": C("detected", "S-ENV", by=["SM-TWIN"], label="Rx maximum <= minimum -> default error response"),
    "env_recv": C("detected", "S-ENVX", "received", by=["SM-TWIN"], label="actual torque above the received maximum"),
    "env_widened_cx": C("detected", "S-ENVX", "widened_no_bound", by=["SM-TWIN"], expect=False,
                        label="counterexample: a widened envelope input hides the deviation"),
    "env_bounded": C("detected", "S-ENVX", "widened_bounded", by=["SM-TWIN"],
                     label="independent bounds keep the window (normal limits cannot widen it)"),
    "osc": C("detected", "S-OSC", variants=("f5", "f20", "f80"), mode="all", by=["SM-OSCP", "SM-OSCE"],
             label="oscillation 5 / 20 / 80 Hz detected by the power and energy monitors"),
    "est": C("bound", "S-QUAD", variants=("motoring", "regen", "reverse_motoring", "reverse_regen"), mode="all",
             quantity="est_error_abs", max="$EST_ERROR_BOUND", **{"from": 20.0},
             label="safety torque estimate vs the transmission-side torque"),
    "e2e_normal": C("e2e", "S-E2E", "normal", expect_detection=False, label="normal reception: no receive fault"),
    "e2e_pitfall": C("e2e", "S-E2E", "per_task_check", expect_detection=True,
                     label="pitfall: a per-task repetition check flags the normal half-rate reception"),
    "e2e_faults": C("e2e", "S-E2E", variants=("counter_repeat", "crc", "data_id", "em_swap", "loss"), mode="all",
                    within_ms="$MAX_RX_AGE", label="CRC / counter / data id / EM id / loss detected"),
    "gde_time": C("state_time", "S-GDE", variants=("SEQ_A", "SEQ_B"), origin="fault", max_us="$GDE_to_3PS",
                  label="GDE withdrawn -> 3PS within GDE_to_3PS (both recorded sequences)"),
    "gde_ss": C("safe_state", "S-GDE", variants=("SEQ_A", "SEQ_B"), origin="fault", deadline_us="$GDE_to_3PS",
                **{k: v for k, v in SS.items() if k != "transition_ms"},
                label="the physical safe state within GDE_to_3PS and held (both recorded sequences)"),
    "gde_reset": C("state_time", "S-GDE", variants=("SEQ_A_mcu_reset", "SEQ_B_mcu_reset"),
                   origin="event:fault:gde_disable", max_us="$GDE_to_3PS",
                   label="the hardware sequence works while the MCU is in reset"),
    "gde_lowhv": C("safe_state", "S-GDE", variants=("SEQ_A_low_hv", "SEQ_B_low_hv"), origin="fault", **SS,
                   label="GDE withdrawn at 50 V / 3000 rpm (X_Low / X_Upp boundary)"),
    "fov": C("hw_timing", "S-FOV", mech="SM-OV", quantity="v_dc", threshold=780.0, detect_max_us="$FOV_REACTION",
             react_max_us="$FOV_REACTION", label="fast over-voltage: detection and reaction"),
    "fov_bound": C("bound", "S-FOV", quantity="v_dc", max="$VDC_SAFETY_LIMIT",
                   label="the DC link stays below its absolute maximum"),
    "acfoc": C("hw_timing", "S-ACFOC", variants=("thr_nominal", "thr_low", "thr_high"), mode="all", mech="SM-OC",
               quantity="i_phase_abs", detect_max_us="$ACFOC_DETECT_REACTION",
               react_max_us="$ACFOC_DETECT_REACTION", label="fast AC over-current with the threshold tolerance"),
    "acmax": C("no_detection", "S-ACMAX", variants=("thr_low", "thr_high"), mode="all",
               label="no over-current trip at the highest normal current (threshold outside the normal range)"),
    "dcfoc": C("hw_timing", "S-DCFOC", mech="SM-DCOC", quantity="i_bat", threshold=300.0,
               detect_max_us="$DCFOC_DETECT", label="fast DC over-current detection"),
    "dcfoc_noreact": C("bound", "S-DCFOC", quantity="bridge", max=0.0,
                       label="no reaction invented for DCFOC (the bridge stays in PWM)"),
    "lv_ss": C("safe_state", "S-LV", "redundant", origin="fault", **SS, label="LV loss with the redundant supply"),
    "lv_cx": C("safe_state", "S-LV", "lv_gates", origin="fault", expect_reached=False, **SS,
               label="counterexample: LV-only gate supply"),
    "estop": C("equivalent", "S-LV", "estop", other="S-LV", other_variant="redundant",
               label="E-stop by KL30 loss = the same safe state as LV loss"),
    "lv_short": C("detected", "S-LV", "short_during_lv_loss", by=["SM-DSAT"], origin=13.0,
                  label="short detected during the LV loss"),
    "hv40": C("safe_state", "S-HV40", "hv_45V", origin="fault", **SS, label="HV 45 V without LV: functional"),
    "hv38": C("safe_state", "S-HV40", "hv_38V", origin="fault", expect_reached=False, **SS,
              label="HV 38 V without LV: below the 40 V boundary (counterexample)"),
    "transfer": C("bound", "S-SUPPLY", "transfer_seamless", quantity="rail_GATE", min=1.0,
                  label="seamless HV <-> LV transfer"),
    "transfer_cx": C("bound", "S-SUPPLY", "transfer_gap", quantity="rail_GATE", min=1.0, expect_violation=True,
                     label="counterexample: a transfer gap longer than the hold-up"),
    "hvsrc_flag": C("event", "S-SUPPLY", "hv_source_fault", kind="supply", source="HV_fault", origin="fault",
                    label="redundant-supply fault flagged"),
    "hvsrc_keep": C("no_detection", "S-SUPPLY", "hv_source_fault", label="the drive keeps running on the other source"),
    "kl15sw_ss": C("safe_state", "S-KL15", "sw", origin="input:kl15_sw=0", to="input:kl15_sw=1",
                   deadline_ms="$VW_T_KL15_SW_SHUTOFF", torque_tol_Nm="$SS_TORQUE_TOL", power_tol_W="$SS_POWER_TOL",
                   tlsr_min_Nm="$TLSR_TORQUE_MIN", tlsr_max_Nm="$TLSR_TORQUE_MAX",
                   label="SW KL15 OFF: safe state within VW_T_KL15_SW_SHUTOFF"),
    "kl15sw_hold": C("permit", "S-KL15", "sw", **{"from": "input:kl15_sw=0", "to": "input:kl15_sw=1"},
                     label="held until the next SW KL15 ON"),
    "kl15hw_ss": C("safe_state", "S-KL15", "hw", origin="input:kl15_hw=0", to="input:kl15_hw=1",
                   deadline_ms="$VW_T_KL15_HW_SHUTOFF", torque_tol_Nm="$SS_TORQUE_TOL", power_tol_W="$SS_POWER_TOL",
                   tlsr_min_Nm="$TLSR_TORQUE_MIN", tlsr_max_Nm="$TLSR_TORQUE_MAX",
                   label="HW KL15 OFF: safe state within VW_T_KL15_HW_SHUTOFF"),
    "kl15hw_hold": C("permit", "S-KL15", "hw", **{"from": "input:kl15_hw=0", "to": "input:kl15_hw=1"},
                     label="held until the next HW KL15 ON"),
    "kl15_async": C("permit", "S-KL15", "both_off_async", **{"from": "input:kl15_sw=0", "to": "input:kl15_sw=1"},
                    label="asynchronous SW / HW toggles: no enable before both are back"),
    "kl15_fast": C("safe_state", "S-KL15", "sw_12000", origin="input:kl15_sw=0", to="input:kl15_sw=1",
                   **SS3, label="key-off while rotating at 12,000 rpm"),
    "rearm_no": C("rearm", "S-DEFERR", "no_rearm", expect_release=False, label="no KL15 toggle: latch kept"),
    "rearm_sw": C("rearm", "S-DEFERR", "sw_only", expect_release=False, label="SW KL15 toggle only: latch kept"),
    "rearm_hw": C("rearm", "S-DEFERR", "hw_only", expect_release=False, label="HW KL15 toggle only: latch kept"),
    "rearm_both": C("rearm", "S-DEFERR", "both", qualified="input:kl15_hw=1",
                    label="SW and HW KL15 OFF -> ON: released after the qualified event"),
    "rearm_gap": C("rearm", "S-DEFERR", "both_gap", expect_release=False,
                   label="SW and HW toggles too far apart (a declared maximum gap): latch kept"),
    "rearm_dtc": C("rearm", "S-DEFERR", "dtc_clear", expect_release=False, label="DTC clear: latch kept"),
    "rearm_reset": C("rearm", "S-DEFERR", "reset_nvm", expect_release=False, label="reset with NVM retention: kept"),
    "rearm_level_cx": C("rearm", "S-DEFERR", "level_and", expect_premature=True,
                        label="non-approved: a level AND of two HIGH inputs releases at once"),
    "rearm_dtc_cx": C("rearm", "S-DEFERR", "dtc_clear_releases", expect_premature=True,
                      label="non-approved: DTC clear releases"),
    "rearm_ram_cx": C("rearm", "S-DEFERR", "reset_ram", expect_premature=True,
                      label="non-approved: RAM latch lost by a reset"),
    "latch_prot": C("rearm", "S-DEFERR", "corrupt_protected", expect_release=False,
                    label="a corrupted latch word is detected and kept"),
    "latch_unprot_cx": C("rearm", "S-DEFERR", "corrupt_unprotected", expect_premature=True,
                         label="counterexample: an unprotected latch word is lost"),
    "standby_ss": C("safe_state", "S-MODE", "standby", origin="input:target_mode=standby",
                    to="input:target_mode=run", deadline_ms="$VW_FTTI_STD", **SS3,
                    label="bus standby: full safe state within VW_FTTI_STD, held while standby"),
    "standby_exit": C("permit", "S-MODE", "standby_with_latch", **{"from": "input:target_mode=run"},
                      label="standby exit with a default latch pending: torque stays blocked"),
    "notorque_c3": C("safe_state", "S-MODE", "no_torque", origin="input:target_mode=no_torque",
                     to="input:target_mode=run", conditions=["C3"], deadline_ms="$VW_FTTI_STD",
                     transition_ms="$SS_TRANSITION", **SS3,
                     label="no-torque mode: no torque by active pulsing (C3), not the full safe state"),
    "t30_class": C("rearm", "S-MODE", "t30_uv", expect_latch=False,
                   label="isolated t.30 UV: supply reason, no default-error latch"),
    "t30_back": C("permit", "S-MODE", "t30_uv", **{"from": "input:t30_state=uv", "to": "input:t30_state=normal"},
                  restored_within_ms=50.0, label="released when the supply is normal again"),
    "t30_fault": C("rearm", "S-MODE", "t30_ov_with_fault", expect_release=False,
                   label="t.30 OV with a real drive fault: the fault still latches"),
    "reset_ss": C("safe_state", "S-RESET", "rotating", origin="event:fault:mcu_reset", **SS3,
                  label="reset while rotating: safe state throughout"),
    "reset_long": C("safe_state", "S-RESET", "rotating_long", origin="event:fault:mcu_reset", **SS3,
                    label="a 20 ms reset while rotating"),
    "reset_nopwm": C("no_pwm", "S-RESET", "rotating", **{"from": "event:fault:mcu_reset",
                                                          "to": "event::MCU booted"},
                     label="no PWM authority through the reset (software unavailable)"),
    "reset_still": C("no_pwm", "S-RESET", "standstill", **{"from": "event:fault:mcu_reset"},
                     label="reset at standstill: no PWM"),
    "opstate": C("opstate", "S-OPSTATE", sequence=[["POWER_OFF", "STNDBY"], ["STNDBY", "IDLE"], ["IDLE", "RUN"],
                                                   ["RUN", "IDLE"], ["IDLE", "STNDBY"]],
                 timing=[{"from": "STNDBY", "to": "IDLE", "origin": "event:opstate:POWER_OFF -> STNDBY",
                          "min_ms": "$T_STNDBY_IDLE_GUARD", "max_ms": "$T_STNDBY_IDLE_COMPLETE"}],
                 label="power-up / idle / run / roll-out / standby with guard and completion times"),
    "opstate_persist": C("opstate", "S-OPSTATE", timing=[{"from": "IDLE", "to": "STNDBY",
                                                          "origin": "input:ignition=0",
                                                          "min_ms": "$T_IDLE_STNDBY_PERSIST"}],
                         label="IDLE -> STNDBY only after the low-HV / low-speed persistence"),
    "g_idle_run": C("guard_table", guard="IDLE->RUN", formula="hv_enable & spt_done & aps_release & trq_gen_rq",
                    label="IDLE -> RUN guard"),
    "g_run_idle": C("guard_table", guard="RUN->IDLE", formula="~hv_enable | rol_actv | pwr_stg_ctrl | ~trq_gen_rq",
                    label="RUN -> IDLE guard"),
    "g_idle_stndby": C("guard_table", guard="IDLE->STNDBY", formula="~ignition & hv_low & spd_low",
                       label="IDLE -> STNDBY guard"),
    "no_run": C("opstate", "S-OPSTATE", "kl15_off", absent=[["IDLE", "RUN"]],
                label="no active state while torque production is not allowed"),
    "tables": C("model_tables", allow={"STNDBY": ["ASO", "APS"], "IDLE": ["ASO", "APS"],
                                       "DIAG": ["ASO", "APS", "PWM"], "RUN": ["ASO", "PWM"], "LHOM": ["ASO", "PWM"],
                                       "FAULT": ["ASO", "APS"]},
                stages={"six_switch_off": "ASO", "asc_low": "APS", "asc_high": "APS", "pwm": "PWM"},
                label="stage allow-list per state and the ASO / APS / PWM names"),
    "efb": C("state_time", "S-EFB", "above", origin="input:energy_flow_block=1", states=["asc_low"], max_us=2000.0,
             label="NO_AC_DC_ENERGY_FLOW above the speed threshold: APS"),
    "efb_low": C("state_time", "S-EFB", "below", origin="input:energy_flow_block=1", states=["six_switch_off"],
                 max_us=2000.0, label="NO_AC_DC_ENERGY_FLOW at low speed: ASO"),
    "change": C("changeover", "S-CHANGE", **{"from": "RUN", "to": "IDLE"}, max_ms="$C_t_Zustandsubergang_max",
                torque_abs_max_Nm="$CHANGEOVER_TORQUE_ENVELOPE", label="RUN -> IDLE at speed: transient and time"),
    "veh": C("vehicle", "S-VEH", "monitor_on", distance_max_m="$STANDSTILL_DISTANCE_MAX",
             t_react_s="$T_DRIVER_REACTION", a_brake_mps2="$BRAKE_DECEL", label="standstill-to-standstill distance"),
    "veh_cx": C("vehicle", "S-VEH", "monitor_off", distance_max_m="$STANDSTILL_DISTANCE_MAX", expect_violation=True,
                t_react_s="$T_DRIVER_REACTION", a_brake_mps2="$BRAKE_DECEL",
                label="counterexample: the undetected fault exceeds 2 m"),
    "veh_accel": C("vehicle", "S-VEH", "monitor_on", accel_curve="$VEHICLE_ACCEL_TARGET_CURVE",
                   t_react_s="$T_DRIVER_REACTION", a_brake_mps2="$BRAKE_DECEL", label="acceleration target curve"),
    "veh60": C("vehicle", "S-VEH60", v0_kph=60.0, accel_curve="$VEHICLE_ACCEL_TARGET_CURVE",
               t_react_s="$T_DRIVER_REACTION", a_brake_mps2="$BRAKE_DECEL",
               label="above 40 km/h: the acceleration of an unintended torque"),
    "start": C("no_detection", "S-START", label="legitimate start torque at standstill (clutch open): no false trip"),
    "start_still": C("vehicle", "S-START", expect_no_motion=True, label="... and the vehicle does not move"),
    "k0_closed_cx": C("vehicle", "S-VEH", "k0_closed_p1", distance_max_m="$STANDSTILL_DISTANCE_MAX",
                      expect_violation=True, t_react_s="$T_DRIVER_REACTION", a_brake_mps2="$BRAKE_DECEL",
                      label="P1 with K0 closed and the monitor off: the vehicle moves (counterexample)"),
    "arb": C("arbitration", label="T_sum = desired + intervention, clamped by its sign"),
    "itf_int": C("bound", "S-ITF", "intervention", quantity="torque", max=100.0 + 5.0,
                 label="intervention torque: the envelope still clamps T_sum"),
    "ext": C("extended", "S-ITF", "extension", c_t_imax_ms="$C_T_Imax",
             label="extended torque: active flag, reverted after C_T_Imax"),
    "ext_vcm": C("extended", "S-ITF", "extension_voltage_mode", expect_active=False,
                 label="no extension in voltage-control mode"),
    "ext_neg": C("extended", "S-ITF", "negative", neg_limit_Nm=-150.0, label="no negative-side extension"),
    "fb": C("fallback", "S-ITF", variants=("fallback_qualifier", "fallback_wheel_invalid", "fallback_request_invalid",
                                           "fallback_over_limit"), mode="all", origin="t0",
            safe_value_Nm="$uASR_SAFE_VALUE", settle_ms=220.0,
            label="invalid wheel speed / request / qualifier LIMITED / over the limit -> µASR safe value"),
    "fb_e2e": C("fallback", "S-ITF", "fallback_e2e", origin="t0", axle_torque_Nm="$uASR_AXLE_TORQUE",
                wheel_ratio="$WHEEL_TRQ_RATIO", settle_ms=220.0,
                label="µASR Safe Value_e2e = axle torque / wheelTrqratio"),
    "dis": C("discharge", "S-DISCH", "nominal", v_limit_V="$V_DISCHARGE_LIMIT", deadline_ms="$T_ACTIVE_DISCHARGE",
             label="active discharge: measured Vdc below the limit in time"),
    "dis_false": C("discharge", "S-DISCH", "sensor_stuck_low", v_limit_V="$V_DISCHARGE_LIMIT",
                   expect_false_complete=True, label="sensor stuck low: a false completion is caught by the true Vdc"),
    "dis_rot": C("discharge", "S-DISCH", "rotating", v_limit_V="$V_DISCHARGE_LIMIT", expect_recharge=True,
                 label="a rotating machine recharges the link"),
    "dis_passive": C("passive_discharge", v_limit_V="$V_DISCHARGE_LIMIT", deadline_s="$T_PASSIVE_DISCHARGE",
                     label="passive discharge by the bleeder alone"),
    "dual_g": C("bound", "S-DUAL", "global", quantity="v_dc", max="$VDC_SAFETY_LIMIT",
                label="shared DC link, global contract"),
    "dual_l_cx": C("bound", "S-DUAL", "local", quantity="v_dc", max="$VDC_SAFETY_LIMIT", expect_violation=True,
                   label="counterexample: local contract, the other EM charges the open link"),
    "cal": C("calibration", updates=[
        {"set_id": "good", "em_id": "EM1", "crc_ok": True, "complete": True,
         "values": {"mechanisms.SM-TQ.params.debounce_ms": 2.0}},
        {"set_id": "wrong_em", "em_id": "EM2", "crc_ok": True, "complete": True, "values": {}},
        {"set_id": "partial", "em_id": "EM1", "crc_ok": True, "complete": False, "values": {}},
        {"set_id": "out_of_range", "em_id": "EM1", "crc_ok": True, "complete": True,
         "values": {"mechanisms.SM-TQ.params.debounce_ms": -1.0}},
        {"set_id": "contradiction", "em_id": "EM1", "crc_ok": True, "complete": True,
         "values": {"mechanisms.SM-TQ.params.debounce_ms": 25.0}}],
             target={"em_id": "EM1"}, expect=["ACCEPTED", "REJECTED", "REJECTED", "REJECTED", "REJECTED"],
             label="only a complete, validated set for this EM becomes active"),
    "task": C("task_cycle", tx_cycles_ms=[10.0], rx_cycles_ms=[10.0, 20.0], max_task_ms="$VW_MAX_SAFETY_TASK_TIME",
              mechanisms=["SM-TQ", "SM-SUM", "SM-LOS", "SM-OVSW"], label="Tcycle <= min(Tx, Rx/2, VW_MAX_SAFETY_TASK)"),
    "selftest": C("selftest", "S-SELFTEST", "standstill", expect_refused=False,
                  torque_abs_max_Nm="$TEST_OUTPUT_ENVELOPE_T", label="gate test within its envelope, no PWM enable"),
    "selftest_rot": C("selftest", "S-SELFTEST", "rotating", expect_refused=True,
                      label="gate test refused while rotating"),
    "pathtest": C("detected", "S-PATHTEST", by=["SYS:path_test"], origin="t0",
                  label="latent loss of the HW path found by the path test"),
    "pathtest_block": C("permit", "S-PATHTEST", **{"from": 2.0}, label="torque permit blocked while the path is lost"),
    "in_cs_off": C("detected", "S-INPUT", "current_offset", by=["SM-SUM"], label="current offset"),
    "in_cs_gain_cx": C("detected", "S-INPUT", "current_common_gain", by=["SM-SUM"], expect=False,
                       label="blind spot: a common gain passes the sum check"),
    "in_res_off": C("detected", "S-INPUT", "resolver_offset", by=["SM-RP"], label="resolver angle offset"),
    "in_res_freeze": C("detected", "S-INPUT", "resolver_freeze", label="resolver angle frozen"),
    "in_res_loss": C("detected", "S-INPUT", "resolver_loss", by=["SM-LOS", "SM-RP"], label="resolver signal loss"),
    "in_vdc": C("detected", "S-INPUT", "vdc_offset", by=["SM-VP"], label="in-range Vdc offset"),
    "in_supply": C("detected", "S-INPUT", "sensor_supply", label="shared sensor supply lost"),
    "in_delay": C("detected", "S-INPUT", "current_delay", label="current sample delayed (time alignment)"),
    "in_domain": C("detected", "S-INPUT", "out_of_domain", by=["SM-EST"], origin="t0",
                   label="estimate outside its qualified domain"),
    "pwm_leg": C("detected", "S-PWM", "leg_off", by=["SM-PWMF"], label="PWM of a leg missing"),
    "pwm_stuck": C("detected", "S-PWM", "leg_stuck", by=["SM-PWMF"], label="PWM duty stuck"),
    "gate_lost": C("detected", "S-PWM", "gate_lower_lost", label="lower gate supply lost"),
    "short": C("bound", "S-PWM", "switch_short", quantity="i_phase_abs", max="$ASC_CURRENT_LIMIT",
               label="switch short handled by desaturation"),
    "short_cx": C("bound", "S-PWM", "switch_short_no_desat", quantity="i_phase_abs", max="$ASC_CURRENT_LIMIT",
                  expect_stop=True, label="counterexample: without desaturation the short is uncontrolled"),
    "wd_ctrl_cx": C("detected", "S-TASK", "wd_by_control", by=["SM-WD"], expect=False,
                    label="blind spot: a watchdog serviced by the control task misses a stopped safety task"),
    "wd_safety": C("detected", "S-TASK", "wd_by_safety", by=["SM-WD"],
                   label="a watchdog serviced by the safety task's checkpoints catches it"),
    "clk_stop": C("detected", "S-CLOCK", "stop", by=["SM-WD"], label="MCU clock stopped: external watchdog"),
    "clk_drift_cx": C("detected", "S-CLOCK", "drift_half", by=["SM-WD"], expect=False,
                      label="blind spot: a 50 % clock drift passes a timeout watchdog"),
    "conf_lost": C("confirmation", "S-CONFIRM", "gate_lost", expect_confirmed=False,
                   label="requested ASC with the lower gate supply lost: not confirmed"),
    "conf_ok": C("confirmation", "S-CONFIRM", "healthy", expect_confirmed=True, label="healthy reaction confirmed"),
    "normal_first": C("normal_first", "S-NORMAL1ST", "healthy", failed_variant="app_failed",
                      deadline_ms="$VW_MAX_FTTI_TORQUE", label="application limit first, monitor silent"),
    "normal_failed": C("detected", "S-NORMAL1ST", "app_failed", by=["SM-TWIN"],
                       label="application limit failed: the monitor still reacts"),
    "overspeed": C("detected", "S-COUPLING", "runaway", by=["SM-OS"], label="overspeed after a coupling opens"),
    "overspeed_cx": C("detected", "S-COUPLING", "runaway_position_frozen", by=["SM-OS"], expect=False,
                      label="blind spot: frozen position hides the overspeed"),
    "regen": C("bound", "S-REGEN", quantity="p_dc", min="$REGEN_POWER_MIN", **{"from": 30.0},
               label="regeneration follows the lowered minimum torque"),
    "feas": C("feasibility", speeds_rpm=[-6000.0, 3000.0, 9000.0, 12000.0], vdcs_V=[250.0, 400.0, 600.0],
              torque_tol_Nm="$SS_TORQUE_TOL", power_tol_W="$SS_POWER_TOL", tlsr_min_Nm="$TLSR_TORQUE_MIN",
              tlsr_max_Nm="$TLSR_TORQUE_MAX", expect_counterexample={"reaction": "six_switch_off"},
              label="FW / ASC feasibility over speed x Vdc (incl. reverse)"),
    "feas_temp": C("feasibility", speeds_rpm=[3000.0, 12000.0], vdcs_V=[400.0], temperatures_C=[-20.0, 120.0],
                   torque_tol_Nm="$SS_TORQUE_TOL", power_tol_W="$SS_POWER_TOL", tlsr_min_Nm="$TLSR_TORQUE_MIN",
                   tlsr_max_Nm="$TLSR_TORQUE_MAX", label="... over the temperature"),
    "feas_hvoff": C("feasibility", speeds_rpm=[3000.0, 12000.0], vdcs_V=[400.0], hv_state="disconnected",
                    torque_tol_Nm="$SS_TORQUE_TOL", power_tol_W="$SS_POWER_TOL", tlsr_min_Nm="$TLSR_TORQUE_MIN",
                    tlsr_max_Nm="$TLSR_TORQUE_MAX", label="... with the HV disconnected"),
    "metrics": C("metrics", sets={"TORQUE": {"requirements": ["TQ"], "SPFM_min": "$VW_MIN_SPFM_TORQUE",
                                             "LFM_min": "$VW_MIN_LFM_TORQUE", "PMHF_max_fit": "$VW_MAX_PMHF_TORQUE"},
                                  "DESTAB": {"requirements": ["DS"], "SPFM_min": "$VW_MIN_SPFM_DESTAB",
                                             "LFM_min": "$VW_MIN_LFM_DESTAB", "PMHF_max_fit": "$VW_MAX_PMHF_DESTAB"}},
                 label="TORQUE and DESTAB judged per set, never added"),
    "asil": C("asil_binding", label="ASIL literals kept, resolved only through FUSA_Param"),
    "prov": C("provenance", label="source statements / study assumptions kept apart"),
    "coverage": C("package_coverage", label="a verdict row for every item"),
    "trace": C("trace_channels", "S-KL15", "sw", channels=["t", "T_em", "T_shaft", "v_dc", "i_dc", "bridge",
                                                           "sys_permit", "sys_reasons", "sys_confirmed"],
               events=["input", "safe_state_request", "actuation", "supervisor"],
               label="synchronized trace with the supervisor channels"),
    "tw_dash": C("trace_channels", "S-DEV", "small_long", channels=["T_request", "T_cmd", "T_est_mon", "T_shaft",
                                                                    "tw_hi", "tw_lo", "tw_tol", "tw_int"],
                 label="torque-window dashboard channels"),
    "power_map": C("trace_channels", "S-LV", "redundant", channels=["src_LV", "src_HV", "rail_MCU", "rail_GATE",
                                                                    "rail_LOGIC"],
                   events=["supply"], label="power-path availability channels"),
    "ov_counter": C("detected", "S-FOV", by=["SM-OV", "SM-OVSW"], label="over-voltage detected"),
    "acfoc_thr": C("threshold_ratio", mech="SM-OC", reference="inverter_current_limit",
                   ratio="$ACFOC_THRESHOLD_FACTOR", tol="$ACFOC_THRESHOLD_TOL",
                   label="ACFOC threshold = 1.5 x Imax +-10 %, outside the normal range"),
    "dcfoc_thr": C("threshold_ratio", "S-DCFOC", mech="SM-DCOC", reference="dc_discharge_current_limit",
                   ratio="$DCFOC_THRESHOLD_FACTOR", tol="$ACFOC_THRESHOLD_TOL",
                   label="DCFOC threshold = 1.5 x Imax +-10 %, outside the normal range"),
    "asc_drag": C("bound", "S-REACT", "asc_3000", quantity="torque_abs", max=1.0, expect_violation=True,
                  **{"from": 10.0}, label="the ASC drag torque at speed is not zero (it belongs in the available "
                                          "torque)"),
    "powerup": C("state_time", "S-OPSTATE", origin="t0", states=["six_switch_off"], max_us=1.0,
                 label="power-up default: 6SO from the first instant"),
    "asc_current": C("bound", "S-REACT", "asc_12000", quantity="i_phase_abs", max="$ASC_CURRENT_LIMIT",
                     label="ASC phase current within the ASC envelope"),
    "in_cs_lost": C("detected", "S-INPUT", "current_lost", label="phase-current signal lost (open)"),
    "in_cs_stuck": C("detected", "S-INPUT", "current_stuck", label="phase-current signal stuck at 0 A"),
}


def K(*names):
    return [copy.deepcopy(CK[n]) for n in names]


# -- §0 rules and hard rules ---------------------------------------------------------------------------------
RULE_CHECKS = [
    ("prov",), ("prov", "coverage"), ("timeline",), ("ss_fw_cx", "ss_fw_lowhv_cx", "conf_lost"),
    ("window", "quad"), ("arb", "itf_int", "env_bounded"), ("feas",), ("in_cs_gain_cx", "in_supply", "wd_ctrl_cx"),
    ("prov", "gde_ss"), ("coverage", "trace"),
]
for k, (text, names) in enumerate(zip(D["rules"], RULE_CHECKS), 1):
    I(f"RULE-{k:02d}", "0. 구현 규칙", "rule", "DERIVED", text, K(*names), source="§0 구현 규칙")
HARD = [("prov",), ("prov",), ("ss_fw_cx", "conf_lost"), ("timeline",), ("window", "arb"), ("gde_ss", "prov")]
for k, (text, names) in enumerate(zip(D["hard_rules"], HARD), 1):
    I(f"HARD-{k:02d}", "0. hard rules", "rule", "DERIVED", text, K(*names), source="manifest hard_rules")

# -- §1 questions the simulation must answer -----------------------------------------------------------------------
QCH = {"Detect": ("dev_time", "dev_int", "osc", "in_res_off", "fov", "e2e_faults"),
       "Decide": ("feas", "efb", "efb_low", "gde_ss"),
       "Execute": ("lv_ss", "reset_ss", "gde_reset", "lv_short", "conf_lost"),
       "Reach physical safe state": ("ss_asc", "ss_fw_cx", "ss_soft"),
       "Hold & recover": ("rearm_both", "rearm_sw", "rearm_dtc", "kl15sw_hold", "standby_exit"),
       "Evidence": ("timeline", "trace", "coverage")}
for q in D["questions"]:
    I(f"GOAL-{q['mark']}", "1. 최종 질문", "goal", "DERIVED", f"{q['mark']} {q['name']}: {q['text']}",
      K(*QCH[q["name"]]), source="§1")

# -- §2 the registry itself ------------------------------------------------------------------------------------------
I("REG-01", "2. Parameter registry", "rule", "DERIVED",
  "모든 숫자·symbolic parameter를 source tag와 함께 관리하고 OPEN 값은 채우지 않는다",
  K("prov") + [C("open_kept", params=[p["id"] for p in PARAMETERS if p["provenance"] == "OPEN"],
                 label="every OPEN registry value stays empty")], source="§2")

# -- groups A..I --------------------------------------------------------------------------------------------------
GROUP_CHECKS = {
    "A": [("env_recv", "env_contra", "quad", "window"), ("veh_accel", "veh60"), ("veh", "veh_cx"),
          ("feas", "ss_fw_cx", "ss_asc"), ("fb",), ("fb_e2e",), ("veh60",),
          ("e2e_faults", "env_recv", "env_widened_cx", "env_bounded")],
    "B": [("quad", "dev_time", "env_recv"), ("dev_ftti", "timeline"), ("window", "sweep_lf"), ("est",),
          ("window", "est"), ("window",), ("window",), ("dev_time", "dev_int", "dev_time_blind", "sweep_lf"),
          ("osc",), ("in_res_off", "in_cs_off", "in_domain")],
    "C": [None, ("gde_time", "gde_reset", "wd_safety", "clk_stop"), ("pathtest", "pathtest_block", "pwm_leg"),
          ("lv_short", "short"), None, ("powerup", "reset_still"), ("gde_lowhv",), ("gde_time", "gde_ss"),
          ("gde_time", "gde_ss"), ("gde_reset",), ("lv_ss", "hvsrc_keep"), ("lv_ss",), ("transfer", "transfer_cx"),
          ("hv40", "hv38"), ("estop",), ("hvsrc_flag",), ("fov", "fov_bound"), ("acfoc", "acmax", "acfoc_thr"),
          ("dcfoc", "dcfoc_noreact", "dcfoc_thr")],
    "D": [("tables",), ("tables", "opstate"), None, ("efb", "efb_low"), ("opstate",), ("opstate_persist",
                                                                                       "g_idle_stndby"),
          ("g_idle_run",), ("g_run_idle", "opstate"), None],
    "E": [("change", "opstate"), ("opstate", "no_run"), ("change",)],
    "F": [("arb", "itf_int"), None, None, ("asc_drag",), ("arb",), ("itf_int",), ("ext", "ext_vcm"),
          ("ext",), ("ext_neg",)],
    "G": [("dis", "dis_false", "dis_rot"), ("dis_passive",), None, ("asil",)],
    "H": [("ss_fw_lowhv_cx", "gde_lowhv"), ("feas", "feas_hvoff"), None, ("ss_fw_cx", "conf_lost", "conf_ok")],
}
GROUP_MANUAL = {
    ("C", 0): "번호 family 목록: 개별 번호↔문구 매핑이 복원되지 않은 항목은 번호를 추정 배정하지 않는다 (원문 확인 필요)",
    ("C", 4): "Per-phase blanking은 HW 고정: 모델의 HW 보호(DESAT/OC 비교기)는 보정·SW 설정으로 끌 수 없는 요소로 모델링됨 - "
              "실제 회로의 blanking 구현은 회로/데이터시트 근거로 기록",
    ("D", 2): "STNDBY/IDLE/FAULT의 reaction 우선순위 표(6th/1st/7th/3rd)는 원문 세부가 불완전: 우선순위는 설정 가능한 "
              "데이터로 두고 원문 표 확보 후 binding",
    ("D", 8): "Tx_MachStat 보고: 모델은 상태(sys_state)를 기록 - 실제 신호 매핑은 통신 DB로 확인",
    ("F", 1): "EM1_Max/Min 계산 입력(Vdc, I_Max_neu, 속도, 전압 한계, 손실, derating, 과부하, c_max_moment): 앱의 "
              "capability 계산으로 산출 가능 - 원문 계산식/입력 정의 확보 필요",
    ("F", 2): "I_Max_neu / I_Min_neu 고속 영역 보간: 전류 한계 곡선은 제품 데이터 - 원문 정의 확보 필요",
    ("G", 2): "SiC 전력 모듈을 이용한 active discharge: 현재 모델에 없음 (구현 시 torque/phase current 발생과 고ASIL "
              "경로 상호작용을 추가 검증)",
    ("H", 2): "OC/단락 반응과 OV 반응의 우선순위는 아키텍처 가설 (원문 표 binding 전 DERIVED)",
    ("I", 0): None,
}
RESEARCH_CHECKS = [
    [C("research", "S-FAULT", metric="impulse_before_detection", limit_Nm=180.0,
       reference="0.8835 N*m*s positive torque before recognition at 10 ms (another study case)")],
    [C("research", "S-REACT", "asc_3000", metric="gate_vs_terminal", half_width_Nm=20.0,
       reference="a 20 us ASC command did not establish terminal torque completion")],
    [C("research", "S-REACT", "asc_3000", metric="time_to_window", half_width_Nm=20.0, hold_ms=1.0,
       reference="3000 rpm: +-20 N*m window reached and held at 48.416 ms (bound 73.227 ms) in the study")],
    [C("feasibility", speeds_rpm=[3000.0, 6000.0, 9000.0, 12000.0], vdcs_V=[300.0], torque_tol_Nm=10.0,
       power_tol_W=500.0, research=True, reference="9 of 62 sampled 300 V states met neither FW nor ASC at +-10 N*m")],
]
for g, grp in D["groups"].items():
    for k, it in enumerate(grp["items"]):
        iid = f"{g}-{k + 1:02d}"
        prov = it.get("provenance", "CUSTOMER-PAST")
        kind = "research" if prov == "RESEARCH" else "requirement"
        if g == "I":
            checks = RESEARCH_CHECKS[k]
        else:
            names = GROUP_CHECKS[g][k]
            checks = K(*names) if names else []
            if g == "C" and k == 8:              # the conflicting 100 us sequence: both variants, one verdict
                checks = [C("safe_state", "S-GDE", variants=("SEQ_A", "SEQ_B"), origin="fault", **SS,
                            label="SEQ_A vs SEQ_B: physical outcome (kept as variants)"),
                          C("state_time", "S-GDE", variants=("SEQ_A", "SEQ_B"), origin="fault",
                            max_us="$GDE_to_3PS", label="SEQ_A vs SEQ_B: time to 3PS")]
        man = GROUP_MANUAL.get((g, k))
        if man and not checks:
            checks = [M(man)]
        elif man:
            checks.append(M(man))
        I(iid, f"{g}. {grp['title']}", kind, prov, it["text"], checks, source=f"§{g}", sim_note=it.get("sim", ""))

# -- §3 modules, §4 scenarios ---------------------------------------------------------------------------------------
MOD = {
    "SIM-01": [C("trace_channels", "S-FAULT", channels=["T_em", "T_shaft", "i_a", "i_b", "i_c", "v_dc", "i_dc",
                                                        "speed_rpm", "i_bat"]), CK["veh"]],
    "SIM-02": [C("trace_channels", "S-FAULT", channels=["T_request", "T_cmd", "i_d", "i_q", "d_a", "d_b", "d_c",
                                                        "bridge"])],
    "SIM-03": K("window", "tw_dash") + [C("trace_channels", "S-ITF", "fallback_qualifier",
                                          channels=["itf_pos_limit", "itf_fallback"])],
    "SIM-04": K("est", "in_domain"),
    "SIM-05": K("dev_time", "dev_int", "tw_dash"),
    "SIM-06": K("osc"),
    "SIM-07": K("trace", "rearm_both", "conf_ok"),
    "SIM-08": K("gde_time", "fov", "acfoc", "dcfoc", "pwm_leg", "wd_safety"),
    "SIM-09": K("ss_asc", "ss_fw_cx", "ss_soft", "feas"),
    "SIM-10": K("power_map", "lv_ss", "transfer"),
    "SIM-11": K("dis", "dis_false", "dis_rot"),
    "SIM-12": K("opstate", "tables", "change"),
    "SIM-13": K("arb", "ext", "fb"),
    "SIM-14": [C("model_interfaces", interfaces={
        "sensor": ["fault:sensor"], "comm": ["fault:torque_command", "fault:e2e", "fault:envelope"],
        "estimator": ["mech:estimator_domain", "fault:sensor"], "MCU": ["fault:mcu_reset", "fault:safety_task_stop",
                                                                       "fault:control_task_stop"],
        "clock": ["fault:clock"], "reset": ["fault:mcu_reset"], "supply": ["fault:lv_loss", "fault:hv_supply_fault",
                                                                          "fault:resource_loss"],
        "gate driver": ["fault:gate_supply_loss", "fault:gde_disable", "fault:pwm_output"],
        "semiconductor": ["fault:switch_short", "fault:switch_open", "fault:diode_open"],
        "bus": ["fault:e2e", "fault:torque_command"], "mechanical coupling": ["fault:coupling"]})],
    "SIM-15": K("trace", "timeline"),
}
for r in D["modules"]:
    I(r[0], "3. Simulation module", "module", "DERIVED", f"{r[1]}: {r[2]} (관측: {r[3]})", MOD[r[0]], source="§3")
SCN = {
    "SCN-01": ("quad", "sweep_quad", "start"), "SCN-02": ("env_contra", "e2e_faults", "e2e_normal"),
    "SCN-03": ("dev_time", "dev_int", "dev_time_blind", "dev_large", "sweep_lf"), "SCN-04": ("osc",),
    "SCN-05": ("feas", "feas_temp", "feas_hvoff"), "SCN-06": ("ss_fw_cx", "ss_fw_lowhv_cx"),
    "SCN-07": ("ss_asc", "ss_asc_rev", "ss_soft", "short"), "SCN-08": ("gde_time", "gde_ss", "gde_lowhv", "gde_reset"),
    "SCN-09": ("fov", "fov_bound"), "SCN-10": ("acfoc", "acmax", "acfoc_thr", "dcfoc", "dcfoc_noreact",
                                                    "dcfoc_thr"),
    "SCN-11": ("lv_ss", "lv_cx", "hv40", "hv38", "transfer", "transfer_cx", "estop"),
    "SCN-12": ("reset_ss", "reset_long", "reset_nopwm", "reset_still"),
    "SCN-13": ("rearm_no", "rearm_sw", "rearm_hw", "rearm_both", "rearm_gap", "rearm_dtc", "rearm_reset",
               "kl15_async"),
    "SCN-14": ("opstate", "opstate_persist", "g_idle_run", "g_run_idle", "g_idle_stndby", "no_run", "tables"),
    "SCN-15": ("veh", "veh_cx", "veh_accel", "k0_closed_cx"), "SCN-16": ("fb", "fb_e2e"),
    "SCN-17": ("arb", "itf_int"), "SCN-18": ("ext", "ext_vcm", "ext_neg"), "SCN-19": ("dis", "dis_false", "dis_rot"),
    "SCN-20": ("dual_g", "dual_l_cx"), "SCN-21": ("cal",), "SCN-22": ("selftest", "selftest_rot", "pathtest"),
}
for r in D["scenarios"]:
    I(r[0], "4. Scenario matrix", "scenario", "DERIVED", f"{r[1]} — sweep/fault: {r[2]}; 판정: {r[3]}",
      K(*SCN[r[0]]), source="§4")

# -- §5 timing, §6 OPEN, §7 outputs ---------------------------------------------------------------------------------
TIM = [
    [C("timeline", "S-FAULT", expect_recorded=True, fdti_ms="$FDTI_BUDGET", torque_tol_Nm="$SS_TORQUE_TOL",
       power_tol_W="$SS_POWER_TOL", label="FDTI = t_detect - t_fault (internal budget, not a source value)")],
    [C("timeline", "S-FAULT", frti_ms="$FRTI_BUDGET", torque_tol_Nm="$SS_TORQUE_TOL",
       power_tol_W="$SS_POWER_TOL", label="FRTI = t_physical_safe - t_detect (gate time kept apart)")],
    [C("timeline", "S-FAULT", ftti_ms="$VW_MAX_FTTI_TORQUE", torque_tol_Nm="$SS_TORQUE_TOL",
       power_tol_W="$SS_POWER_TOL", label="FTTI from the fault onset (never restarted at the detection)")],
    [C("timeline", "S-FAULT", shutoff_ms="$VW_T_SHUTOFF", torque_tol_Nm="$SS_TORQUE_TOL",
       power_tol_W="$SS_POWER_TOL", label="VW_T_SHUTOFF from the triggering criterion (14714)")],
    K("dev_ftti"),
]
for k, r in enumerate(D["timing"]):
    I(f"TIM-{k + 1:02d}", "5. Timing / FTTI", "timing", "DERIVED", f"{r[0]}: {r[1]} ({r[2]})", TIM[k], source="§5")
OPEN_PARAMS = [
    ["TLSR_TORQUE_MIN", "TLSR_TORQUE_MAX"], ["W_TORQUE_LIM_FACT_P1", "VW_TORQUE_TOL_P1"],
    ["VW_MAX_ASIL", "VW_MAX_ASIL_P1", "VW_MAX_ASIL_P2", "VW_MAX_PMHF_TORQUE", "VW_MIN_SPFM_TORQUE",
     "VW_MIN_LFM_TORQUE", "VW_MAX_PMHF_DESTAB", "VW_MIN_SPFM_DESTAB", "VW_MIN_LFM_DESTAB"],
    ["VW_T_SHUTOFF", "VW_MAX_FTTI_TORQUE", "VW_FTTI_STD", "VW_T_KL15_SW_SHUTOFF", "VW_T_KL15_HW_SHUTOFF",
     "VW_MAX_SAFETY_TASK_TIME"],
    ["W_TORQUE_LIM_FACT_P1", "W_TORQUE_LIM_FACT_P2"], ["VW_TORQUE_TOL_P1", "VW_TORQUE_TOL_P2"], [],
    ["X_Upp", "GDE_SEQUENCE"], [], [], ["SS_TORQUE_TOL", "SS_POWER_TOL", "SS_TRANSITION"], ["EST_ERROR_BOUND"],
    ["ASC_CURRENT_LIMIT"],
]
OPEN_MANUAL = {6: "P1/P2 ↔ EM1/EM2 ↔ P0/P1 물리 매핑: 모델은 한 기계와 차량 위치(P1/P2, K0)를 선언값으로 둔다 - 매핑 확정 "
                  "전 자동 치환 금지",
               8: "HWIO 개별 번호↔문장 매핑: 추정 배정 금지 (원문 확인)",
               9: "14714의 next Rx_KL15_Sw + KL15_Hw OFF→ON 순서·시간차·validity: 재허가 계약은 변형(variant)으로 "
                  "구현되어 있고 승인 계약은 답변 후 binding"}
for k, text in enumerate(D["open"]):
    checks = []
    if OPEN_PARAMS[k]:
        checks.append(C("open_kept", params=OPEN_PARAMS[k], label="kept OPEN (no guessed value)"))
    if k in OPEN_MANUAL:
        checks.append(M(OPEN_MANUAL[k]))
    if k == 0:
        checks.append(M("전체 TLSR 05/07/08/11/BRS_01 원문과 14679 이후 잘린 항목: 원문 확보 필요"))
    if k == 9:
        checks += K("rearm_both", "rearm_level_cx")
    I(f"OPEN-{k + 1:02d}", "6. OPEN으로 남길 것", "open", "OPEN", text, checks, source="§6")
OUT = [("coverage", "prov"), ("timeline", "trace"), ("feas", "feas_temp"), ("tw_dash", "window"), ("power_map",),
       ("opstate", "g_idle_run", "g_run_idle", "g_idle_stndby", "tables"), ("prov", "coverage")]
for k, text in enumerate(D["outputs"]):
    I(f"OUT-{k + 1:02d}", "7. 자동 산출물", "output", "DERIVED", text, K(*OUT[k]), source="§7")

# -- Appendix A: the customer WI rows (direct reading of the photos) ----------------------------------------------
WI_IFACE = C("model_interfaces", interfaces={
    "HW terminal 15": ["signal:kl15_hw"], "rotor sensor": ["role:position_control", "sensor:position"],
    "ESC 2 x HW wheel speed": ["signal:wheel_speed_kph", "signal:wheel_speed_valid"],
    "motor phases": ["channel:i_a", "channel:i_b", "channel:i_c"], "HV": ["channel:v_dc", "signal:t30_state"],
    "LV terminal 30": ["signal:t30_state", "fault:lv_loss"], "bus / data basis": ["fault:e2e", "signal:target_mode"]},
    label="the listed interfaces exist in the simulation (circuit / DBC separately)")
WIROWS = {r[1]: r for r in D["wi"]}


def wi(key, iid, title, asil, agreement, checks, manual=None):
    r = WIROWS[key]
    it = I(iid, "Appendix A. 고객 WI 14656-14741", "requirement", "CONFIRMED", title, checks, manual=manual,
           asil_literal=asil, agreement=agreement, source=f"P-INVASPICE-{key} ({r[0]})", text=r[3], status_note=r[4])
    return it


wi("14656–14657", "WI-14656", "Drive 범위: P1/P2, K0, INV + rotor sensor", None, "직접 판독",
   K("start", "start_still", "k0_closed_cx"), manual="P1/P2 ↔ EM1/EM2 매핑 확정 필요 (ACT-02, Q02)")
wi("14660–14661", "WI-14660", "다른 문서와의 경계 (K0·변속기·차동기어 FuSa는 다른 문서)", None, "직접 판독",
   [M("범위 경계: 기록으로 관리 (시뮬레이션 대상 아님)")])
wi("14662–14663", "WI-14662", "부분 공급사의 도출 책임 (motor/rotor sensor 공차가 감시 허용오차와 safety actual value에 "
   "필요)", "[ASIL]", "Agreed", K("est", "in_res_off"), manual="motor/rotor sensor 공차 데이터 확보 (Q12)")
wi("14664–14672", "WI-14664", "안전 관련 인터페이스 (HW KL15, rotor sensor, ESC 2xHW wheel speed, phases, HV, LV KL30, "
   "bus)", None, "직접 판독 (실제 회로/DBC 반영 미확인)", [copy.deepcopy(WI_IFACE)],
   manual="실제 회로·pin·DBC 반영 여부와 OLE diagram은 별도 확인 (ACT-07, Q11)")
wi("14673", "WI-14673", "TLSR는 상위 torque-safety objectives에서 도출 (차량 SG로 부르지 않음)", None, "직접 판독",
   [M("TLSR/SG 구분: 요구 관리 규칙 (시뮬레이션 대상 아님)")])
wi("14677–14678", "WI-14677", "TLSR 01: 정차 시 비의도 토크 방지 (ASIL B)", "B", "Partially Agreed",
   K("veh", "veh_cx", "start", "asil"))
wi("14677–14678", "WI-14678", "TLSR 03: 비의도 과도한 높은 motor torque 방지 (MAX)", "MAX", "Partially Agreed",
   K("dev_time", "env_recv", "env_contra", "asil"))
wi("14689–14693", "WI-14689", "TORQUE metric 집합 (TLSR 01/03/05/07/08)", None, "Partially Agreed",
   [C("metrics", sets={"TORQUE": {"requirements": ["TQ"], "SPFM_min": "$VW_MIN_SPFM_TORQUE",
                                  "LFM_min": "$VW_MIN_LFM_TORQUE", "PMHF_max_fit": "$VW_MAX_PMHF_TORQUE"}})],
   manual="drive/INV 할당 경계와 FMEDA 입력 확보 (ACT-04)")
wi("14694–14696", "WI-14694", "DESTAB metric 집합 (TLSR 08/11/BRS_01)", None, "Partially Agreed",
   [C("metrics", sets={"DESTAB": {"requirements": ["DS"], "SPFM_min": "$VW_MIN_SPFM_DESTAB",
                                  "LFM_min": "$VW_MIN_LFM_DESTAB", "PMHF_max_fit": "$VW_MAX_PMHF_DESTAB"}})],
   manual="TLSR 11/BRS_01 본문 확보; TLSR 08은 두 집합에 모두 - 합산 금지")
wi("14697", "WI-14697", "FuSa 관련 diagnoses를 공급사 시험에 포함", None, "Not Agreed",
   [M("Not Agreed 사유 확인 후 시험 범위 합의 (ACT-16/17, Q14)")] + K("coverage"))
wi("14698", "WI-14698", "정상 기능이 monitoring reaction보다 먼저 개입", None, "Agreed",
   K("normal_first", "normal_failed"))
wi("14699–14700", "WI-14699", "FuSa calibration은 공급사 책임; application label·error path·reaction 목록", None,
   "14699 Agreed / 14700 Not Agreed", K("cal"), manual="label/error-path catalog 형식 합의 (ACT-16)")
wi("14701", "WI-14701", "small/large는 절댓값이 아닌 수학적 대소 (signed)", None, "설명/해석 규칙",
   K("window", "quad", "dev_sign"))
wi("14702–14706", "WI-14702", "MAX / MAX_P1 / MAX_P2는 FUSA_Param 참조; torque path는 요구 torque 생성에 필요한 HW/SW 전체",
   "MAX", "Agreed", K("asil"), manual="VW_MAX_ASIL / _P1 / _P2 값 확보 (Q03)")
wi("14707", "WI-14707", "torque = electric machine이 transmission에 전달하는 torque", "N/A", "Agreed", K("est"))
wi("14708–14710", "WI-14708", "TSC 참조; VW_ parameter와 s_/Rx_/Tx_ 신호는 FUSA_Param 정의를 따름", None, "14708 Agreed",
   K("prov"), manual="FUSA_Param / data basis 확보 (Q01)")
wi("14711", "WI-14711", "안전 기능·감시 threshold가 open program에서 configurable", "QM", "Agreed", K("cal"))
wi("14712", "WI-14712", "안전 관련 입력의 검증 또는 중복 산출 (QM adaptation 값 포함)", None, "직접 판독",
   K("in_cs_off", "in_cs_gain_cx", "in_res_off", "in_res_loss", "in_vdc", "in_supply", "in_domain"))
wi("14713", "WI-14713", "Torque safety 연산 주기 <= min(Tx, Rx/2, VW_MAX_SAFETY_TASK_TIME) + debounce/FTTI", "MAX",
   "Agreed", K("task", "e2e_normal", "e2e_pitfall"))
wi("14714", "WI-14714", "Default error response: criterion부터 VW_T_SHUTOFF 안에 safe state, 다음 SW+HW KL15 ON까지 "
   "유지", "N/A", "Agreed (rearm 시퀀스 확인 필요)",
   K("timeline", "dev_ftti", "rearm_no", "rearm_sw", "rearm_hw", "rearm_both", "rearm_dtc", "rearm_reset",
     "rearm_level_cx"))
wi("14715–14717", "WI-14715", "전원에 독립적인 default response", "MAX", "Agreed", K("lv_ss", "lv_cx", "hv40", "transfer"))
wi("14715–14717", "WI-14716", "ECU HW 고장에 대한 대응", "MAX", "Partially Agreed",
   K("short", "short_cx", "conf_lost", "clk_stop", "wd_safety"), manual="Partially Agreed 쟁점 확인 (Q09)")
wi("14715–14717", "WI-14717", "단독 t.30 UV/OV는 drive fault가 아니며 그 safe state는 error response가 아님", None,
   "직접 판독", K("t30_class", "t30_back", "t30_fault"))
wi("14720–14721", "WI-14721", "Safe state: C1..C4 네 가지 torque 발생을 모두 방지", "N/A", "Agreed",
   K("ss_asc", "ss_fw_cx", "ss_soft", "ss_asc_rev", "feas"))
wi("14722–14725", "WI-14722", "운전조건별 출력단 activation 전략은 구매자와 합의", "N/A", "Agreed",
   K("feas", "feas_hvoff"), manual="FW/ASC 선택 전략의 구매자 합의 (ACT-08, Q05)")
wi("14722–14725", "WI-14723", "safe-state threshold는 configurable", "N/A", "Agreed", K("cal"))
wi("14722–14725", "WI-14724", "ECU HW 고장 시에도 safe state 가정 가능", "MAX", "Partially Agreed",
   K("short", "short_cx", "conf_lost", "reset_ss"))
wi("14722–14725", "WI-14725", "drive 전원 조건에서도 safe state 가정 가능", "MAX", "Agreed",
   K("lv_ss", "lv_cx", "hv40", "hv38"))
wi("14726–14729", "WI-14726", "FKT_LAH가 safe state를 요구하는 mode에서 safe state", "MAX", "Agreed",
   K("standby_ss"), manual="FKT_LAH target↔operating mode mapping 확보 (Q01)")
wi("14726–14729", "WI-14727", "bus standby: full safe state (VW_FTTI_STD)", "MAX", "Agreed",
   K("standby_ss", "standby_exit"))
wi("14726–14729", "WI-14728", "no-torque target mode: active pulsing torque 금지 (VW_FTTI_STD)", "MAX", "Agreed",
   K("notorque_c3"))
wi("14730–14732", "WI-14730", "Rx_KL15_Sw OFF: VW_T_KL15_SW_SHUTOFF 안에 safe state, 다음 SW ON까지 유지", "MAX", "Agreed",
   K("kl15sw_ss", "kl15sw_hold", "kl15_fast"))
wi("14730–14732", "WI-14731", "KL15_Hw OFF: VW_T_KL15_HW_SHUTOFF 안에 safe state, 다음 HW ON까지 유지", "QM", "Agreed",
   K("kl15hw_ss", "kl15hw_hold"))
wi("14730–14732", "WI-14732", "reset 동안 safe state 유지", "MAX", "Agreed",
   K("reset_ss", "reset_long", "reset_nopwm", "reset_still"))
wi("14736–14741", "WI-14737", "P1 upper/lower torque window (MAX_P1)", "MAX_P1", "Agreed",
   K("window", "quad", "dev_time", "dev_int", "sweep_lf"))
wi("14736–14741", "WI-14738", "P2 upper/lower torque window (MAX_P2)", "MAX_P2", "Agreed",
   K("window") + [M("P2 path: the model has one machine - the P2 window uses the same implementation with "
                    "W_TORQUE_LIM_FACT_P2 / VW_TORQUE_TOL_P2 (values OPEN)")])
wi("14736–14741", "WI-14741", "VW_TORQUE_TOL_P1/P2는 실제 speed의 함수", None, "Agreed",
   K("window", "sweep_lf"))

# -- C1..C4 -----------------------------------------------------------------------------------------------------------
COND_TXT = {r[0]: r for r in D["cond"]}
COND = {
    "C1": [C("safe_state", "S-REACT", "asc_12000", origin="gate", conditions=["C1"], **SS),
           C("safe_state", "S-KL15", "sw", origin="input:kl15_sw=0", to="input:kl15_sw=1", conditions=["C1"], **SS3)],
    "C2": [C("safe_state", "S-REACT", "fw_12000", origin="gate", conditions=["C2"], expect_reached=False, **SS,
             label="counterexample: 6SO at 12,000 rpm"),
           C("safe_state", "S-REACT", "asc_12000", origin="gate", conditions=["C2"], **SS)],
    "C3": [C("safe_state", "S-REACT", "soft_asc_6000", origin="gate", conditions=["C3"], **SS),
           copy.deepcopy(CK["notorque_c3"])],
    "C4": [C("safe_state", "S-REACT", "asc_12000", origin="gate", conditions=["C4"], **SS,
             label="ASC braking torque against the TLSR band (full TLSR OPEN)")],
}
for c, checks in COND.items():
    r = COND_TXT[c]
    I(f"COND-{c}", "Appendix A. Safe state C1-C4", "requirement", "CONFIRMED", f"{c}: {r[1]}", checks,
      asil_literal="N/A", agreement="14721 Agreed", source="P-INVASPICE-14721", text=r[2])

# -- the reason table (KL15 / reset / standby) ----------------------------------------------------------------------
KLC = [K("timeline", "rearm_both", "rearm_sw", "rearm_dtc"),
       [M("FKT_LAH safe mode: target ↔ operating mode mapping 확보 후 opstate에 선언")] + K("opstate"),
       K("standby_ss", "standby_exit"), K("notorque_c3"), K("kl15sw_ss", "kl15sw_hold", "kl15_async"),
       K("kl15hw_ss", "kl15hw_hold"), K("reset_ss", "reset_long", "reset_nopwm"),
       K("t30_class", "t30_back", "t30_fault")]
for k, r in enumerate(D["kl15"]):
    I(f"RSN-{k + 1:02d}", "Appendix A. 진입 이유별 상태계약", "requirement", "CONFIRMED", f"{r[0]} ({r[1]}): {r[2]}",
      KLC[k], source=f"P-INVASPICE-{r[1]}", text=f"시간 기준: {r[3]}; 유지/해제: {r[4]}")

# -- the 26 safety mechanisms --------------------------------------------------------------------------------------
SM_DEMO = {
    "01": ("e2e_faults", "e2e_normal", "e2e_pitfall"), "02": ("in_cs_off", "in_cs_gain_cx", "in_cs_lost", "in_supply"),
    "03": ("in_res_loss",), "04": ("in_res_off", "in_res_freeze", "overspeed_cx"), "05": ("in_vdc", "dis_false"),
    "06": ("est", "in_domain"), "07": ("dev_time", "dev_time_blind", "sweep_lf"), "08": ("dev_int",),
    "09": ("osc",), "10": ("osc",), "11": ("wd_ctrl_cx", "wd_safety"), "12": (), "13": ("latch_prot",
                                                                                         "latch_unprot_cx", "cal"),
    "14": ("clk_stop", "clk_drift_cx"), "15": ("in_delay", "pwm_leg"), "16": ("pwm_leg", "pwm_stuck", "conf_lost"),
    "17": ("short_cx",), "18": ("acfoc", "short", "lv_short"), "19": ("gde_reset", "wd_safety", "clk_stop", "lv_ss"),
    "20": ("gate_lost",), "21": (), "22": ("cal",), "23": ("pathtest", "pathtest_block", "selftest"),
    "24": ("conf_lost", "conf_ok"), "25": ("lv_ss", "lv_cx", "transfer", "transfer_cx", "hv40"),
    "26": ("rearm_both", "rearm_dtc", "rearm_level_cx", "standby_exit", "reset_nopwm"),
}
SM_NOSIM = {"12": "CPU 연산 무결성 (lockstep 등): MCU 내부 - 제조사 fault injection으로 alarm→gate 반응 확인",
            "13": "ECC/CRC의 메모리 고장 자체는 모델 밖 (latch 보호와 보정 활성화만 모델링)",
            "15": "ADC/GTM register·DMA 수준 고장은 모델 밖 (관측 가능한 결과만: 샘플 지연, PWM 누락)",
            "17": "상·하단 interlock은 브리지 모델에서 구조적으로 보장 - 명령 경로 고장 주입은 회로 수준 시험",
            "20": "GD SPI 설정·상태 진단은 모델 밖 (bias 상실만 모델링)",
            "21": "QM/다른 EM 간섭(MPU, DMA, partition)은 모델 밖 - FFI 분석으로 증거"}
for r in D["sm"]:
    n = r["id"][-2:]
    spec = {"target_fault": r.get("대상 고장"), "principle": r.get("검출·예방 원리"), "reaction": r.get("반응·실행 계약"),
            "timing": r.get("시간 계약"), "asil": r.get("ASIL 적용"),
            "allocation": f"{r.get('logical')} / {r.get('physical')}", "verification": r.get("검증 및 관측"),
            "shared": r.get("공유 의존성·사각지대")}
    checks = [C("sm_spec", **({"not_simulable": SM_NOSIM[n]} if n in SM_NOSIM and not SM_DEMO[n] else {}),
                label="the mechanism's design specification is complete")]
    checks += K(*SM_DEMO[n])
    if n in SM_NOSIM and SM_DEMO[n]:
        checks.append(M(SM_NOSIM[n]))
    I(r["id"], "Appendix A. Safety Mechanism 26", "mechanism", "DERIVED", f"{r['title']} ({r['kind']})", checks,
      spec=spec, source="Appendix A SM-I", basis=r.get("근거/적용성"))

# -- the 21 actions ------------------------------------------------------------------------------------------------
ACT_CH = {
    "ACT-01": (), "ACT-02": ("start", "start_still", "k0_closed_cx"), "ACT-03": ("asil",), "ACT-04": ("metrics",),
    "ACT-05": ("window", "quad", "est"), "ACT-06": ("est", "in_cs_gain_cx"), "ACT-07": (),
    "ACT-08": ("feas", "feas_hvoff", "ss_fw_cx", "ss_asc"),
    "ACT-09": ("rearm_no", "rearm_sw", "rearm_hw", "rearm_both", "rearm_gap", "rearm_dtc", "rearm_reset",
               "rearm_level_cx"),
    "ACT-10": ("standby_ss", "notorque_c3", "kl15sw_ss", "kl15hw_ss", "reset_ss"),
    "ACT-11": ("lv_ss", "lv_cx", "t30_class", "t30_fault"), "ACT-12": ("short", "short_cx", "conf_lost"),
    "ACT-13": ("task", "e2e_pitfall"), "ACT-14": ("normal_first", "normal_failed"), "ACT-15": ("cal",),
    "ACT-16": (), "ACT-17": ("coverage",), "ACT-18": ("trace",), "ACT-19": ("window", "sweep_lf", "osc", "quad"),
    "ACT-20": (), "ACT-21": ("coverage",),
}
for a in D["actions"]:
    checks = [M(f"조직 업무: 산출물 '{a.get('산출물', '')[:90]}' / 완료 기준 기록")] + K(*ACT_CH[a["id"]])
    if a["id"] == "ACT-07":
        checks.append(copy.deepcopy(WI_IFACE))
    I(a["id"], "Appendix A. 추가 업무 21", "action", "DERIVED", f"[{a['priority']}] {a['title']}", checks,
      source=a.get("basis"), text=a.get("해야 할 일"), done_when=a.get("완료 기준"))

# -- the 18 front TSR ----------------------------------------------------------------------------------------------
FR_CH = {
    1: ("ss_asc", "ss_fw_cx", "ss_soft", "feas"), 2: ("timeline", "dev_ftti"),
    3: ("rearm_no", "rearm_sw", "rearm_hw", "rearm_both", "rearm_gap", "rearm_dtc", "rearm_reset", "rearm_level_cx",
        "kl15_async"),
    4: ("standby_ss", "standby_exit"), 5: ("notorque_c3",), 6: ("kl15sw_ss", "kl15sw_hold", "kl15_fast"),
    7: ("kl15hw_ss", "kl15hw_hold"), 8: ("reset_ss", "reset_long", "reset_nopwm", "reset_still"),
    9: ("lv_ss", "lv_cx", "hv40", "transfer"), 10: ("t30_class", "t30_back", "t30_fault"),
    11: ("short", "short_cx", "conf_lost", "clk_stop", "wd_safety"), 12: ("task",),
    13: ("dev_ftti", "timeline", "e2e_faults"), 14: ("normal_first", "normal_failed"),
    15: ("in_cs_off", "in_cs_gain_cx", "in_res_off", "in_res_freeze", "in_vdc", "in_supply"), 16: ("est",),
    17: ("window", "sweep_lf"), 18: ("cal",),
}
STMT = re.compile(r"^(고객 [\d–/]+) · (.*?)((?:While|When|Following|Throughout|For each|For an|For the|The inverter|"
                  r"Before|The safety)\b.*)$")
for k, f in enumerate(D["front"], 1):
    m = STMT.match(f.get("statement", ""))
    src, alloc, stmt = (m.group(1), m.group(2).strip(), m.group(3)) if m else ("", "", f.get("statement", ""))
    asil = {"원문 N/A": "N/A"}.get((f.get("ASIL 처리") or "").split(";")[0].strip(), None)
    lit = "MAX" if "MAX → VW_MAX_ASIL" in (f.get("ASIL 처리") or "") else (
        "QM" if (f.get("ASIL 처리") or "").startswith("원문 QM") else asil)
    I(f["id"], "Appendix A. TSR-FRONT 18", "tsr", "DERIVED", f"{f['title']}: {stmt}", K(*FR_CH[k]),
      source=src, allocation=alloc, asil_literal=lit, text=f.get("검증 기준"), inputs_needed=f.get("확정 전 필요한 입력"))

# -- the 16 verification scenarios, the 14 questions ------------------------------------------------------------------
V_CH = {
    "V01": ("start", "start_still", "k0_closed_cx", "veh"), "V02": ("dual_g", "dual_l_cx"),
    "V03": ("standby_ss", "notorque_c3", "standby_exit"), "V04": ("kl15sw_ss", "kl15hw_ss", "kl15_async", "kl15sw_hold"),
    "V05": ("rearm_no", "rearm_sw", "rearm_hw", "rearm_both", "rearm_dtc", "rearm_reset", "rearm_level_cx"),
    "V06": ("reset_ss", "reset_long", "reset_nopwm", "reset_still"),
    "V07": ("lv_ss", "lv_cx", "t30_class", "t30_fault", "transfer", "transfer_cx"),
    "V08": ("short", "short_cx", "conf_lost", "clk_stop"), "V09": ("feas", "feas_temp", "ss_fw_cx", "gde_lowhv"),
    "V10": ("in_cs_off", "in_cs_gain_cx", "in_res_off", "in_supply", "in_vdc"),
    "V11": ("task", "e2e_normal", "e2e_pitfall", "e2e_faults"), "V12": ("normal_first", "normal_failed"),
    "V13": ("window", "est", "quad"), "V14": ("cal",), "V15": ("pathtest", "pathtest_block", "selftest",
                                                               "selftest_rot"),
    "V16": ("dual_g", "metrics"),
}
V_MAN = {"V02": "P1 발전·K0 전이 조합: 두 번째 기계는 축약 모델(DC 전력 일정표) - K0 전이의 기계적 결합은 선언값",
         "V14": "service/진단 명령에 의한 안전기능 우회는 모델 밖 (TSR-ADD-028)"}
for r in D["verification"]:
    checks = K(*V_CH[r[0]]) + ([M(V_MAN[r[0]])] if r[0] in V_MAN else [])
    I(r[0], "Appendix A. 검증 16", "verification", "DERIVED", f"{r[1]} — 축: {r[2]}; 기준: {r[3]}", checks,
      source="Appendix A 검증 16")
Q_CLOSES = {
    "Q01": ["VW_T_SHUTOFF", "VW_MAX_FTTI_TORQUE", "VW_FTTI_STD", "VW_T_KL15_SW_SHUTOFF", "VW_T_KL15_HW_SHUTOFF",
            "VW_MAX_SAFETY_TASK_TIME", "TLSR_TORQUE_MIN", "TLSR_TORQUE_MAX"],
    "Q02": [], "Q03": ["VW_MAX_ASIL", "VW_MAX_ASIL_P1", "VW_MAX_ASIL_P2"],
    "Q04": ["VW_MAX_PMHF_TORQUE", "VW_MIN_SPFM_TORQUE", "VW_MIN_LFM_TORQUE", "VW_MAX_PMHF_DESTAB",
            "VW_MIN_SPFM_DESTAB", "VW_MIN_LFM_DESTAB"],
    "Q05": ["SS_TORQUE_TOL", "SS_POWER_TOL", "SS_TRANSITION", "TLSR_TORQUE_MIN", "TLSR_TORQUE_MAX"],
    "Q06": [], "Q07": [], "Q08": [], "Q09": ["ASC_CURRENT_LIMIT"], "Q10": [], "Q11": [],
    "Q12": ["VW_TORQUE_TOL_P1", "VW_TORQUE_TOL_P2", "EST_ERROR_BOUND", "W_TORQUE_LIM_FACT_P1"],
    "Q13": [], "Q14": [],
}
for r in D["confirm"]:
    I(r[0], "Appendix A. 확인 질문 14", "question", "DERIVED", r[1],
      [C("question", closes=Q_CLOSES[r[0]])], source="Appendix A 확인 질문", decides=r[2])

# -- Appendix B: the 42 additional TSR drafts -----------------------------------------------------------------------
ADD_CH = {
    1: ("e2e_faults",), 2: ("e2e_faults", "e2e_pitfall"), 3: ("no_run", "notorque_c3"),
    4: ("in_cs_lost", "in_cs_stuck"), 5: ("in_cs_off", "in_cs_gain_cx"), 6: (), 7: ("in_res_loss",),
    8: ("in_res_loss", "in_res_off"), 9: ("in_res_off", "in_res_freeze"), 10: ("in_vdc", "dis_false"),
    11: ("in_supply",), 12: ("in_delay",), 13: ("in_domain",), 14: ("pwm_leg", "pwm_stuck"), 15: ("short_cx",),
    16: ("acfoc", "short", "lv_short"), 17: ("gate_lost", "conf_lost"), 18: (), 19: ("pathtest", "pathtest_block"),
    20: ("selftest", "selftest_rot"), 21: ("lv_ss", "lv_cx", "transfer", "transfer_cx"),
    22: ("feas", "ss_fw_cx", "ss_fw_lowhv_cx", "gde_lowhv"), 23: ("ss_asc", "ss_asc_rev", "ss_soft", "asc_current"),
    24: ("kl15_fast", "kl15sw_hold"), 25: ("cal",), 26: (), 27: ("env_widened_cx", "env_bounded"), 28: (),
    29: ("rearm_dtc", "rearm_dtc_cx"), 30: ("dual_l_cx",), 31: ("dual_g", "dual_l_cx"),
    32: ("short", "short_cx", "conf_lost"), 33: ("est",), 34: ("conf_lost", "conf_ok"), 35: ("regen",),
    36: ("fov", "fov_bound"), 37: ("dis", "dis_false", "dis_rot"), 38: (), 39: (), 40: ("overspeed", "overspeed_cx"),
    41: (), 42: ("start",),
}
ADD_MAN = {3: "COMMAND_COMPATIBILITY_TABLE (방향·mode·EM 조합)은 통신 계약 확보 후 표로 선언",
           6: "전류 영점 학습(offset 활성화 조건)은 모델 밖 - 보정 활성화 규칙은 calibration 검사로 대체 확인 불가",
           15: "상·하단 동시 도통 방지는 브리지 모델에서 구조적 - 명령 경로 고장은 회로 수준 시험",
           18: "GD 설정 무결성(SPI/CRC/readback)은 모델 밖", 26: "비안전 기능 간섭(MPU/DMA/partition)은 모델 밖 - FFI 분석",
           28: "service/개발 명령의 안전 우회 차단은 모델 밖 - 진단 세션 명세로 검증",
           30: "EM별 상태·재기동 격리: 모델은 한 인버터 + 두 번째 기계의 축약 모델",
           33: "차량 제공 torque/speed 데이터의 형식·age·EM swap은 통신 계약 확인 필요 (조건부 후보)",
           38: "열 과부하 제한 (조건부 후보): 열 모델은 앱의 열 해석 페이지 - 안전 할당 확인 후 연결",
           39: "열 보호 입력·모델 신뢰성 상실 (조건부 후보)",
           41: "구동·회생 토크 상실의 가용성 보고 (조건부 후보)",
           42: "Disconnector·Parking lock interlock (조건부 후보; K0는 context로만 확정)"}
for a in D["add"]:
    n = int(a["id"][-3:])
    checks = K(*ADD_CH[n]) + ([M(ADD_MAN[n])] if n in ADD_MAN else [])
    I(a["id"], f"Appendix B. TSR-ADD 42 · {a['area']}", "tsr", "DERIVED",
      f"[{a['class']} {a['priority']}] {a['title']}: {a['statement']}", checks, source="Appendix B",
      text=a.get("검증·합격 기준"), inputs_needed=a.get("확정할 값/조건"), note=a.get("설계·검증 시 주의"))

# ================================================================================================= the hierarchy
# The source mixes vehicle-level requirements, top-level safety requirements (TLSR), functional and technical
# requirements, mechanisms, verification, actions, questions and open inputs.  Every item gets its level.  Its traces
# upward are the customer numbers / TLSRs the source cites (``traces_to``: document-given) or links inferred by topic
# (``traces_to_inferred``: for review).  Cited customer numbers without an item in the source and the TLSRs whose text
# the source does not give become OPEN placeholders, so the tree shows what is missing.  The proposals (PROP-*) are
# DERIVED additions the simulation shows are missing - never customer requirements.
LEVEL_BY_GROUP = {
    "0. 구현 규칙": "RULE", "0. hard rules": "RULE", "1. 최종 질문": "STUDY", "2. Parameter registry": "RULE",
    "A. Unintended torque / vehicle-level recovered requirements": "FSR",
    "B. Torque window / time-integral monitoring recovered requirements": "FSR",
    "C. Mercedes eATS2.X HWIO recovered customer requirements": "TSR",
    "D. eATS operating-state / power-stage recovered customer requirements": "FSR",
    "E. Controlled changeover recovered customer requirement": "FSR",
    "F. EM1 available torque envelope / intervention / extended torque": "FSR",
    "G. Active/passive discharge and HV energy": "TSR",
    "H. Reaction-selection project logic and known caveats": "RULE",
    "I. Research-only numerical examples for study regression": "RES",
    "3. Simulation module": "MOD", "4. Scenario matrix": "VER", "5. Timing / FTTI": "DEF", "6. OPEN으로 남길 것": "OPEN",
    "7. 자동 산출물": "OUT", "Appendix A. 고객 WI 14656-14741": "FSR", "Appendix A. Safe state C1-C4": "DEF",
    "Appendix A. 진입 이유별 상태계약": "FSR", "Appendix A. Safety Mechanism 26": "SM", "Appendix A. 추가 업무 21": "ACT",
    "Appendix A. TSR-FRONT 18": "TSR", "Appendix A. 검증 16": "VER", "Appendix A. 확인 질문 14": "Q"}
LEVEL_BY_ID = {
    # the vehicle-level requirements the TLSRs serve, and the two TLSRs the source states
    "A-02": "SG", "A-03": "SG", "A-07": "SG", "WI-14677": "TLSR", "WI-14678": "TLSR",
    # scope, definitions, responsibilities, metric sets
    **{f"WI-{n}": "DEF" for n in (14656, 14660, 14662, 14664, 14673, 14689, 14694, 14699, 14701, 14702, 14707,
                                  14708)},
    "WI-14697": "VER", "B-04": "DEF", "B-05": "DEF", "B-10": "RULE", "C-01": "DEF", "D-01": "DEF", "F-06": "DEF",
    "F-09": "DEF", "G-03": "RULE", "G-04": "DEF", "H-01": "TSR",
    # technical detail inside a functional section
    "A-06": "TSR", "B-03": "TSR", "B-06": "TSR", "B-07": "TSR", "B-08": "TSR", "B-09": "TSR", "D-05": "TSR",
    "D-06": "TSR", "D-07": "TSR", "D-08": "TSR", "D-09": "TSR", "E-03": "TSR", "F-03": "TSR", "F-05": "TSR",
    "F-07": "TSR", "F-08": "TSR"}
NUM = re.compile(r"(?<!\d)(14[67]\d\d)(?!\d)")
TLSR_REF = re.compile(r"TLSR ?(0[1-9]|1[01])|BRS_01")
TLSR_ID = {"01": "WI-14677", "03": "WI-14678"}


def _text(it) -> str:
    return json.dumps({k: v for k, v in it.items() if k not in ("checks", "id")}, ensure_ascii=False)


# the TLSRs the source names without their text, and the cited customer numbers without an item
for name, tid in (("TLSR 05", "TLSR-05"), ("TLSR 07", "TLSR-07"), ("TLSR 08", "TLSR-08"), ("TLSR 11", "TLSR-11"),
                  ("BRS_01", "BRS-01")):
    I(tid, "TLSR (text not provided)", "requirement", "OPEN",
      f"{name}: named by the source (metric sets), its text is not provided (OPEN-01)", level="TLSR",
      traces_to_inferred=["A-02", "A-07"],
      manual="the text is not provided: ask for it (OPEN-01) before anything is derived from it")
    TLSR_ID[name.split()[-1] if name.startswith("TLSR") else "BRS"] = tid
known = {it["id"] for it in ITEMS}
cited = sorted({n for it in ITEMS for n in NUM.findall(_text(it))})
for n in cited:
    if f"WI-{n}" not in known:
        I(f"WI-{n}", "Appendix A. WI (cited, not in the source)", "requirement", "OPEN",
          f"requirement {n}: cited by the source, its text is not in it", level="FSR",
          traces_to_inferred=["WI-14677", "WI-14678"],
          manual="the text is not in the source: ask for it before it is refined")
by_id = {it["id"]: it for it in ITEMS}
TORQUE_FSR = [f"WI-{n}" for n in (14698, 14711, 14712, 14713, 14714, 14715, 14716, 14717, 14721, 14722, 14723,
                                  14724, 14725, 14726, 14727, 14728, 14730, 14731, 14732, 14737, 14738, 14741)]
INFERRED = {
    # TLSR -> the vehicle-level requirement it serves
    "WI-14677": ["A-03"], "WI-14678": ["A-02", "A-07"],
    **{w: ["WI-14677", "WI-14678"] for w in TORQUE_FSR},
    **{a: ["WI-14678"] for a in ("A-01", "A-04", "A-05", "A-08")}, "A-06": ["A-05"],
    "B-01": ["WI-14737", "WI-14738"], "B-02": ["WI-14714", "B-01"], "B-09": ["TLSR-08"], "B-10": ["B-01"],
    "SM-I-07": ["B-08"], "SM-I-08": ["B-08"], "SM-I-09": ["B-09"], "SM-I-10": ["B-09"],
    **{c: ["WI-14716", "WI-14724"] for c in ("C-02", "C-03", "C-05", "C-07", "C-08", "C-09", "C-10", "C-17", "C-18",
                                             "C-19")},
    "C-04": ["WI-14715", "WI-14725"], "C-06": ["WI-14732"],
    **{c: ["WI-14715", "WI-14725"] for c in ("C-11", "C-12", "C-13", "C-14", "C-15", "C-16")},
    **{d: ["WI-14722"] for d in ("D-02", "D-03", "D-04", "E-01", "E-02")},
    **{d: ["D-02"] for d in ("D-05", "D-06", "D-07", "D-08", "D-09", "E-03")},
    **{f: ["A-01"] for f in ("F-01", "F-02", "F-04")}, **{f: ["F-01"] for f in ("F-03", "F-05", "F-07", "F-08")},
    "G-01": ["PROP-SG-HV"], "G-02": ["PROP-SG-HV"], "H-01": ["G-01", "WI-14722"],
    "V02": ["WI-14656"], "V03": ["WI-14727", "WI-14728"], "V04": ["WI-14730", "WI-14731"], "V05": ["WI-14714"],
    "V06": ["WI-14732"], "V07": ["WI-14715", "WI-14725", "WI-14717"], "V08": ["WI-14716", "WI-14724"],
    "V09": ["WI-14721", "WI-14722"], "V10": ["WI-14712"], "V11": ["WI-14713"], "V12": ["WI-14698"],
    "V13": ["WI-14741", "WI-14707"], "V14": ["WI-14711", "WI-14699"], "V15": ["WI-14697"],
    "V16": ["WI-14689", "WI-14694"],
    "SCN-01": ["B-01"], "SCN-02": ["A-01"], "SCN-03": ["B-08"], "SCN-04": ["B-09"], "SCN-05": ["WI-14721"],
    "SCN-06": ["WI-14721"], "SCN-07": ["WI-14721"], "SCN-08": ["C-07", "C-08", "C-09"], "SCN-09": ["C-17"],
    "SCN-10": ["C-18", "C-19"], "SCN-11": ["C-13", "C-14", "C-15"], "SCN-12": ["WI-14732"], "SCN-13": ["WI-14714"],
    "SCN-14": ["E-01", "D-02"], "SCN-15": ["A-03"], "SCN-16": ["A-05"], "SCN-17": ["F-05"], "SCN-18": ["F-07"],
    "SCN-19": ["G-01"], "SCN-20": ["TSR-ADD-031"], "SCN-21": ["TSR-ADD-025"], "SCN-22": ["TSR-ADD-020"],
}
ADD_TOPIC = [(1, 13, ["WI-14712"]), (14, 20, ["WI-14716", "WI-14724"]), (21, 21, ["WI-14715", "WI-14725"]),
             (22, 23, ["WI-14721", "WI-14722"]), (24, 24, ["WI-14730", "WI-14731"]), (25, 28, ["WI-14711"]),
             (29, 29, ["WI-14714"]), (30, 32, ["WI-14716"]), (33, 33, ["WI-14712"]), (34, 34, ["WI-14714"]),
             (35, 35, ["A-01"]), (36, 36, ["WI-14716"]), (37, 37, ["G-01"]), (38, 39, ["WI-14716"]),
             (40, 40, ["WI-14678"]), (42, 42, ["WI-14677"])]
for a, b, refs in ADD_TOPIC:
    for n in range(a, b + 1):
        INFERRED.setdefault(f"TSR-ADD-{n:03d}", refs)
DOC_GIVEN = {**{f"B-0{k}": ["B-01"] for k in (3, 4, 5, 6, 7, 8)}}


def classify():
    for it in ITEMS:
        iid = it["id"]
        it.setdefault("level", LEVEL_BY_ID.get(iid) or (
            "TSR" if it["group"].startswith("Appendix B. TSR-ADD") else LEVEL_BY_GROUP.get(it["group"])))
        if not it["level"]:
            raise SystemExit(f"no level for {iid} ({it['group']})")
        text = _text(it)
        up = list(it.get("traces_to") or []) + list(DOC_GIVEN.get(iid, []))
        if not iid.startswith("WI-"):                        # a customer number the item cites: it serves it
            up += [f"WI-{n}" for n in NUM.findall(text)]
        for m in TLSR_REF.finditer(text):                    # a TLSR the item names (a TLSR naming another
            key = m.group(1) or "BRS"                        # is not below it)
            if TLSR_ID.get(key) and TLSR_ID[key] != iid and it["level"] != "TLSR":
                up.append(TLSR_ID[key])
        up = [u for i_, u in enumerate(up) if u != iid and u in by_id and u not in up[:i_]]
        if up:
            it["traces_to"] = up
        inf = [u for u in list(it.get("traces_to_inferred") or []) + INFERRED.get(iid, [])
               if u != iid and u not in up]
        inf = [u for i_, u in enumerate(inf) if u not in inf[:i_]]
        if inf:
            it["traces_to_inferred"] = inf


# the proposals: what the simulation shows is missing (DERIVED, never customer requirements)
PROPOSALS = [
    I("PROP-SG-HV", "Proposals (DERIVED)", "requirement", "DERIVED",
      "a vehicle-level goal for the stored high-voltage energy: no electric shock from the DC link after the drive is "
      "switched off or disconnected (the discharge requirements have no goal in the source)",
      level="SG", proposed=True, manual="a goal: judged through the requirements traced to it (the roll-up)",
      rationale="the discharge requirements (G-01, G-02, TSR-ADD-037) trace to no goal of the source: the source "
                "covers torque safety only"),
    I("PROP-01", "Proposals (DERIVED)", "requirement", "DERIVED",
      "with the processor lost while the machine turns and the battery disconnected, the hardware selection by the "
      "DC voltage (X_Low / X_Upp, C-07) shall not cycle between freewheeling and 3PS: where the back-EMF exceeds X_Low "
      "it shall hold 3PS (a speed- or back-EMF-dependent latch), so the link stays below V_DISCHARGE_LIMIT",
      level="TSR", proposed=True, traces_to_inferred=["C-07", "G-01", "H-01"],
      checks=[C("event", "S-HWVDC", "cycling", kind="strategy", text="hardware HV selection", min_count=6,
                label="the selection by the DC voltage alone cycles (demonstration)"),
              C("bound", "S-HWVDC", "cycling", quantity="v_dc", max="$V_DISCHARGE_LIMIT", expect_violation=True,
                label="... the link never stays below the discharge limit (counterexample)", **{"from": 80.0}),
              C("event", "S-HWVDC", "latched", kind="strategy", text="hardware HV selection", expect=False,
                label="3PS held: no cycling"),
              C("bound", "S-HWVDC", "latched", quantity="v_dc", max="$V_DISCHARGE_LIMIT",
                label="3PS held: the link stays below the discharge limit", **{"from": 80.0})],
      rationale="at 6000 rpm with the MCU in reset and the contactor open, the active discharge pulls the link to "
                "X_Low, 6SO lets the back-EMF recharge it to X_Upp within tens of microseconds, 3PS again - a cycle of "
                "about 8 ms: every 6SO interval feeds energy back (C2) and the link never stays below 60 V; H-01 "
                "(HVDC < 60 V -> freewheeling) makes it worse; no requirement of the source asks about it"),
    I("PROP-02", "Proposals (DERIVED)", "requirement", "DERIVED",
      "the hardware GDE sequence shall end in 3PS (not in 6SO) whenever the back-EMF exceeds the DC-link voltage: of "
      "the two recorded sequences (C-09) only the one ending in the HV-dependent selection reaches the physical safe "
      "state within GDE_to_3PS at 12,000 rpm",
      level="TSR", proposed=True, traces_to_inferred=["C-08", "C-09"], checks=K("gde_ss"),
      rationale="SEQ_B (3PS for 100 us, then 6SO) rectifies into the battery at 12,000 rpm (C2) until the BMS opens "
                "the contactor and the over-voltage comparator selects 3PS about 11 ms later; SEQ_A is safe within "
                "103 us: the conflict C-09 decides the safety of the GDE path, it is not an editorial one"),
    I("PROP-03", "Proposals (DERIVED)", "requirement", "DERIVED",
      "the fast DC over-current threshold (DCFOC, C-19) shall lie above the highest DC current of normal operation "
      "with its tolerance (or be qualified), so a legitimate torque step never trips it",
      level="TSR", proposed=True, traces_to_inferred=["C-19"], checks=K("dcfoc_thr"),
      rationale="the document gives the rule (1.5 x Imax +-10 %), not a value; the DC over-current comparator of "
                "the product model (300 A) is 0.75 x its DC current limit (400 A): inside the normal range - the "
                "ratio check fails and a torque step of normal operation (50 -> 250 N*m at 9,000 rpm, S-DCFOC) "
                "trips it; with the rule the threshold lies at 540-660 A"),
    I("PROP-04", "Proposals (DERIVED)", "requirement", "DERIVED",
      "the safety task's timebase shall be monitored against an independent clock (a window watchdog or a clock "
      "monitor): a timeout watchdog alone does not see a slow clock",
      level="SM", proposed=True, traces_to_inferred=["SM-I-11", "SM-I-14"], checks=K("clk_stop", "clk_drift_cx"),
      rationale="a stopped MCU clock is caught by the external watchdog, a 50 % clock drift is not (the tasks still "
                "run, only too slowly): the blind spot is reproduced"),
    I("PROP-05", "Proposals (DERIVED)", "requirement", "DERIVED",
      "an overspeed shall be detected from a speed source independent of the rotor-position path (e.g. the wheel "
      "speeds): a frozen position hides it",
      level="SM", proposed=True, traces_to_inferred=["SM-I-04", "TSR-ADD-040"],
      checks=K("overspeed", "overspeed_cx"),
      rationale="after a coupling opens the overspeed is detected - unless the rotor position is frozen, which the "
                "same path would need to see it: reproduced"),
    I("PROP-06", "Proposals (DERIVED)", "requirement", "DERIVED",
      "the default-error latch shall be released only by the edge-qualified sequence of both KL15 signals (OFF then "
      "ON, in the agreed order and gap); a level AND, a DTC clear or a RAM-held latch are not a re-arm",
      level="TSR", proposed=True, traces_to_inferred=["WI-14714", "TSR-ADD-029"],
      checks=K("rearm_both", "rearm_level_cx", "rearm_dtc_cx", "rearm_ram_cx"),
      rationale="the non-approved readings of 'the next SW + HW KL15 ON' release the latch too early: reproduced "
                "(OPEN-10 asks for the exact order)"),
    I("PROP-07", "Proposals (DERIVED)", "requirement", "DERIVED",
      "the reaction shall be reported as confirmed only from observations of its effect (gate supply, device states, "
      "currents), never from the command: with the lower gate supply lost a requested 3PS is not confirmed",
      level="TSR", proposed=True, traces_to_inferred=["TSR-ADD-034"], checks=K("conf_lost", "conf_ok"),
      rationale="requested, applied and confirmed kept apart: the loss of the lower gate supply keeps the requested "
                "3PS from happening - a confirmation from the command would be false"),
]
classify()

# the section names as the application shows them (navigation labels; the item texts stay verbatim)
GROUP_SHOWN = {"Appendix A. 고객 WI 14656-14741": "Appendix A. WI 14656-14741",
               "Appendix A. 고객 WI (cited, not in the source)": "Appendix A. WI (cited, not in the source)",
               "Appendix B. TSR-ADD 42 · 고객 범위 확인": "Appendix B. TSR-ADD 42 · 범위 확인"}
for it in ITEMS:
    g = it.get("group") or ""
    it["group"] = GROUP_SHOWN.get(g, g.replace("recovered customer requirement", "recovered requirement"))

# ================================================================================================= the package
used = set()
for it in ITEMS:
    for c in it["checks"]:
        if c.get("scenario"):
            used.add(c["scenario"])
        if c.get("params", {}).get("failed_scenario"):
            used.add(c["params"]["failed_scenario"])
PACKAGE = {
    "schema": "twb-reference/1",
    "meta": {"title": "Traction Inverter FuSa simulation reference",
             "date": "2026-09-30", "source": "Traction_Inverter_Functional_Safety_Simulation_Reference (input package, "
                                              "2026-09-30)",
             "note": "Not an approved specification. Provenance tags kept as in the source; OPEN values "
                     "stay empty (the illustrative profile uses flagged example values). Every simulation verdict is "
                     "about the product model loaded in the application."},
    "provenance": {"CONFIRMED": "원문/사진에서 직접 복원·재확인", "PAST-PROJECT": "과거 프로젝트 요구 (이번 원본에서 "
                   "재검증 못함)", "PROJECT": "프로젝트 조건/적용값", "DERIVED": "내부 도출 FSR/TSR/아키텍처/검증 제안",
                   "RESEARCH": "스터디/논문 예시값 (원문 요구 아님)", "OPEN": "미확정 (값 없음)",
                   "CONFLICT": "회수 기록 충돌 (variant 유지)"},
    "customer_tags": list(CUST),
    "parameters": PARAMETERS,
    "asil_binding": {"MAX": "VW_MAX_ASIL", "MAX_P1": "VW_MAX_ASIL_P1", "MAX_P2": "VW_MAX_ASIL_P2"},
    "fmeda": X.FMEDA,
    "scenarios": {k: v for k, v in SC.items() if k in used},
    "items": ITEMS,
}
if __name__ == "__main__":
    out = (HERE.parents[2] / "src" / "traction_workbench" / "extensions" / "faultsim" / "packages"
           / "customer_inverter_reference.json")
    out.write_text(json.dumps(PACKAGE, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {}
    for it in ITEMS:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print(len(ITEMS), "items", kinds, len(PACKAGE["scenarios"]), "scenarios", len(PARAMETERS), "parameters")
