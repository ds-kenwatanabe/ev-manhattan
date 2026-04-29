"""Compatibility wrapper for src.solver.greedy."""

import sys
from src.solver import greedy as _greedy

sys.modules[__name__] = _greedy
