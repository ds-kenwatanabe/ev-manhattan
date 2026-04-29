"""Compatibility wrapper for src.solver.rcsp."""

import sys
from src.solver import rcsp as _rcsp

sys.modules[__name__] = _rcsp
