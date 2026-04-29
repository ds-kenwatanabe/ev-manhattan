"""Compatibility wrapper for src.energy.model."""

import sys
from src.energy import model as _model

sys.modules[__name__] = _model
