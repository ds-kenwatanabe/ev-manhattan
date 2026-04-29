import os
import time

import pytest

from src.solver import rcsp as planner


pytestmark = pytest.mark.performance


@pytest.mark.skipif(os.environ.get("RUN_PERF_TESTS") != "1", reason="set RUN_PERF_TESTS=1 to run performance tests")
def test_monkeypatched_planner_runtime_guard(monkeypatch, tmp_path):
    # This is a lightweight guard for accidental route-loop explosions. It avoids
    # the Manhattan graph so it can isolate Python planner overhead.
    from tests.test_planner_greedy_recharge import test_low_battery_route_inserts_recharge_and_continues

    start = time.perf_counter()
    test_low_battery_route_inserts_recharge_and_continues(monkeypatch, tmp_path)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5
