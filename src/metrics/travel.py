"""Compatibility wrapper for src.graph.travel."""

import sys
from src.graph import travel as _travel

sys.modules[__name__] = _travel
