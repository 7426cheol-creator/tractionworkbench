"""Screening extensions beyond the v1.0 MVP (roadmap C / D / G previews).

These modules are reduced-order *screening* tools added on request:

* ``timing``      - fault-reaction timing chain vs FTTI, duplicate-budget detection;
* ``dclink``      - resistive active discharge and regenerative overvoltage after
                    a battery disconnect (capacitor energy balance);
* ``safe_state``  - steady-state ASC / freewheel (6SO) screening with a separate
                    project-rule layer;
* ``thermal``     - Foster-network temperature rise linked to torque availability.

They follow the same claim semantics as the core but have a different
intended use and V&V status: none of them is a transient simulation, a
device SOA check or a functional-safety approval.  Transient aspects are left
UNKNOWN, and a thermal duration claim becomes FEASIBLE/INFEASIBLE only with a
thermal model declared validated for the stated conditions.
"""
