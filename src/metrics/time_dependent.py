"""Compatibility wrapper for src.graph.time_dependent."""

import sys
from src.graph import time_dependent as _time_dependent

sys.modules[__name__] = _time_dependent
