"""MathWorks transfer: the Python Workbench as the executable reference, its models, parameters, scenarios,
calculation semantics and results carried to MATLAB / Simulink / System Composer reproducibly.

Three layers are kept apart: (1) requirements and the accepted source (reference package, contract equations),
(2) this Python reference, (3) the MathWorks side.  The package lets 3 be checked against 2 (parity) and against
1 (oracle values) with the same cases, so a defect 2 and 3 share does not pass unnoticed.  See ``package`` for the
format, ``contract`` for what must be preserved and ``cases`` for what is compared.
"""

from .package import (REPORT_SCHEMA, SCHEMA, PackageConflict, check_package, export_package, find_runtimes,
                      run_local, verify_report)

__all__ = ["REPORT_SCHEMA", "SCHEMA", "PackageConflict", "check_package", "export_package", "find_runtimes",
           "run_local", "verify_report"]
