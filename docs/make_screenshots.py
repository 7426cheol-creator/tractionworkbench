#!/usr/bin/env python3
"""Refresh the README screenshots from the desktop self-test.

    python docs/make_screenshots.py                          # runs `twb selftest out/readme_selftest --lang en`
    python docs/make_screenshots.py --lang ko                # the same gallery in Korean
    python docs/make_screenshots.py --from out/selftest      # converts an existing self-test output
    python docs/make_screenshots.py --prune                  # also deletes docs/screenshots/*.jpg no longer mapped

The self-test drives every page through its real code path in the light theme at 1600 x 1000 (deterministic, the
same run CI does; English by default: many readers of the README are not Korean); this script copies the chosen window captures to docs/screenshots/<name>.jpg, at most 1280 px
wide.  A failing self-test publishes nothing.  SHOTS is the README gallery: change it together with README.md.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "docs" / "screenshots"
WIDTH = 1280

# README image -> self-test capture (the names in desktop/selftest.py)
SHOTS = {
    "decision": "01_decision_summary",
    "reading_decision_fail": "04a_decision_fail_reading",
    "reading_overvoltage": "15c_safety_overvoltage_reading",
    "reading_efficiency": "43a_efficiency_reading",
    "reading_pwm": "47a_pwm_reading",
    "system_overview": "02_decision_view_0",
    "idiq_map": "02_decision_view_1",
    "waveforms": "02_decision_view_2",
    "phasor_hexagon": "02_decision_view_3",
    "decision_inverse": "04_decision_analyses",
    "trajectory": "06_trajectory_speed",
    "envelope": "09_envelope",
    "efficiency_map": "10_map_eta",
    "requirement_set": "12a_requirement_set",
    "requirement_candidates": "12c_requirement_candidates",
    "ftti": "14_safety_ftti",
    "passive_discharge": "15a_safety_passive",
    "dclink_overvoltage": "15b_safety_overvoltage",
    "safe_state": "16_safety_state",
    "thermal": "17_thermal",
    "thermal_network": "17b_thermal_network",
    "thermal_repeated_load": "17e_thermal_repeated_load",
    "project": "18b_project",
    "datasheet_import": "18d_datasheet_import",
    "mathworks_package": "18e_mathworks_package",
    "datasheet_entry": "18f_datasheet_entry_motor",
    "verification": "19_verification",
    "protection_timeline": "22_protection_timeline",
    "asc_transient": "25_asc_transient",
    "fault_waveforms": "25b_fault_waveforms",
    "fault_timeline": "25c_fault_timeline",
    "fault_candidates": "25f_fault_candidates",
    "fault_campaign": "25g_fault_campaign",
    "fault_strategies": "25j_fault_strategies",
    "fault_design_editor": "25k_fault_design_editor",
    "fault_verification_matrix": "25l_fault_verification_matrix",
    "fault_safety_case": "25m_fault_safety_case",
    "power_module": "26_power_module",
    "power_ripple": "27_power_ripple",
    "power_life": "28_power_life",
    "oew": "29_oew_sets",
    "hev": "34_hev_joint",
    "emi_spectrum": "37_emi_spectrum",
    "machine_trade": "40_machine_trade",
    "machine_winding": "41_machine_winding",
    "efficiency_ledger": "43_efficiency_point",
    "efficiency_boundaries": "44_efficiency_maps",
    "efficiency_mission": "45_efficiency_mission",
    "module_ab": "46_module_ab",
    "pwm_policies": "47_pwm_policies",
    "pwm_timing": "48_pwm_timing",
    "antijerk": "49_antijerk_variants",
    "fault_form": "25q_fault_form",
    "reference_overview": "25p_reference_overview",
    "drive_cycle": "60_drive_cycle",
    "drive_cycle_energy": "60b_drive_cycle_energy",
    "charging_waveforms": "61_charging_waveforms",
    "charging_capability": "61b_charging_capability",
    "budget_torque": "62_budget_torque",
    "budget_fusa": "62b_budget_fusa",
    "sim_export_map": "63_sim_export_map",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", default=None, help="an existing self-test output folder")
    ap.add_argument("--prune", action="store_true", help="delete docs/screenshots/*.jpg that SHOTS no longer maps")
    ap.add_argument("--lang", choices=("en", "ko"), default="en", help="the language of the captured pages")
    a = ap.parse_args()
    from PIL import Image                       # matplotlib's dependency, present with the [gui] extra

    src = Path(a.src) if a.src else ROOT / "out" / "readme_selftest"
    if a.src is None:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        rc = subprocess.run([sys.executable, "-m", "traction_workbench.cli", "selftest", str(src), "--lang", a.lang],
                            cwd=ROOT, env=env).returncode
        if rc != 0:
            print(f"the self-test failed (see {src / 'selftest.json'}): no screenshot was changed", file=sys.stderr)
            return rc
    missing = [cap for cap in SHOTS.values() if not (src / f"{cap}.png").is_file()]
    if missing:
        print(f"captures missing in {src}: {missing}", file=sys.stderr)
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    for name, cap in SHOTS.items():
        im = Image.open(src / f"{cap}.png").convert("RGB")
        if im.width > WIDTH:
            im = im.resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS)
        im.save(DEST / f"{name}.jpg", quality=85, optimize=True, progressive=True)
    print(f"{len(SHOTS)} screenshots -> {DEST}")
    # fusa_*.jpg are the README showcase (docs/make_fusa_showcase.py), not self-test captures
    stale = sorted(p.name for p in DEST.glob("*.jpg") if p.stem not in SHOTS and not p.stem.startswith("fusa_"))
    if stale and a.prune:
        for n in stale:
            (DEST / n).unlink()
        print(f"deleted (no longer in the gallery): {', '.join(stale)}")
    elif stale:
        print(f"not in the gallery (use --prune to delete): {', '.join(stale)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
