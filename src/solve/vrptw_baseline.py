"""Compatibility wrapper for src.solver.evrptw."""

import sys
from src.solver import evrptw as _evrptw

sys.modules[__name__] = _evrptw
