"""Compatibility path of the datasheet module loss model, which is part of the drive model.

The model moved to ``models.module_loss``: the core evaluates it (``InverterModel.module_loss``, forward evaluation,
DC claims, capability, sizing), so it belongs to the model layer, not to the optional analyses.  Code in this
package imports it from ``models``; this module only keeps saved scripts that use the former path working.
"""

from ..models import module_loss as _model
from ..models.module_loss import *  # noqa: F401,F403


def __getattr__(name):
    """Every other name of the model module (PEP 562), e.g. former private helpers used by old scripts."""
    return getattr(_model, name)
