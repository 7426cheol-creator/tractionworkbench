"""Causal fault and functional-safety simulation of a three-phase two-level PMSM / IPMSM traction inverter.

fault -> sensors (measured) -> control and safety mechanisms (estimated, decided) -> reaction paths (commanded) ->
bridge (actual, given device health and gate supplies) -> machine, DC link and battery (truth) -> safety requirements
(judged on the truth, with scope and evidence).

Modules: ``plant`` (switched-leg bridge with ideal-diode complementarity, dq machine with isolated star point, DC link
and battery branch, located events, energy ledger), ``sensors``, ``control`` (command path, discrete FOC),
``protection`` (mechanisms, paths, safe-state policy, resources), ``engine`` (the causal event loop), ``safety``
(SG / FSR / TSR verdicts, FDTI / FRTI / FHTI), ``configure`` (project data -> setup), ``campaign`` (sweeps,
boundaries, sensitivity, counterexamples), ``reference`` (independent references for validation).
"""

from .engine import FAULT_KINDS, FaultSpec, RequestProfile, SimResult, SimSetup, simulate  # noqa: F401
