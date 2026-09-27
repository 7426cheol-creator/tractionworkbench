import json
import math
from pathlib import Path

import pytest

from traction_workbench import spec_fixtures as sf

SPEC = Path(__file__).resolve().parents[1] / "reference" / "traction_workbench_spec_v1"


def golden(name: str) -> dict:
    """Read an immutable reference fixture directly from disk (never from production output)."""
    return json.loads((SPEC / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def drive():
    return sf.synthetic_drive()


@pytest.fixture(scope="session")
def limits():
    return sf.synthetic_limits()


def scenario(n, vdc, scenario_id=None, **kw):
    return sf.synthetic_scenario(n, vdc, scenario_id, **kw)
