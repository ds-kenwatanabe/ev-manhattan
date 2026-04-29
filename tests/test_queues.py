import json

import pandas as pd

from src.experiments.queues import simulate_queues_and_reprice


def test_single_plug_charger_waits_for_previous_session(tmp_path):
    inst_path = tmp_path / "instance.json"
    inst_path.write_text(json.dumps({"prices_hourly": [0.25] * 24}))
    chargers = pd.DataFrame([{"id": 1, "power_kw": 10.0, "plugs": 1}])
    sessions = [
        {"station_id": "CH1", "start_min": 480, "end_min": 510, "energy_kwh": 5.0, "cost_usd": 1.25},
        {"station_id": "CH1", "start_min": 490, "end_min": 520, "energy_kwh": 2.0, "cost_usd": 0.50},
    ]

    adjusted = simulate_queues_and_reprice(str(inst_path), chargers, sessions)

    assert adjusted[0]["actual_start"] == 480
    assert adjusted[0]["actual_end"] == 510
    assert adjusted[0]["wait_min"] == 0
    assert adjusted[1]["requested_start"] == 490
    assert adjusted[1]["actual_start"] == 510
    assert adjusted[1]["wait_min"] == 20
