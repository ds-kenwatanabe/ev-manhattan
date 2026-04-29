from src.eval.metrics import evaluate_plan


def test_evaluate_plan_reports_algorithm_metrics():
    inst = {"depot": {"id": "DEPOT1"}, "prices_hourly": [0.20] * 24}
    route = ["DEPOT1", "C001", "C002", "DEPOT1"]
    plan = {
        "timeline": [
            ("DEPOT1", 480, 5.0, 0.0),
            ("C001", 500, 4.0, 0.0),
            ("CH1", 520, 3.0, 0.0),
            ("CH1", 540, 4.0, 0.5),
            ("C002", 560, 3.5, 0.5),
        ],
        "end_time": 560,
        "completed_route_ids": ["DEPOT1", "C001", "C002"],
        "late_delivery_ids": ["C002"],
        "drive_legs": [
            {"from": "DEPOT1", "to": "C001", "depart_min": 480, "distance_km": 4.0, "energy_kwh": 1.0},
            {"from": "C001", "to": "CH1", "depart_min": 500, "distance_km": 2.0, "energy_kwh": 1.0},
            {"from": "CH1", "to": "C002", "depart_min": 540, "distance_km": 3.0, "energy_kwh": 0.5},
        ],
        "runtime_sec": 0.25,
    }

    metrics = evaluate_plan(inst, route, plan)

    assert metrics["customers_served_pct"] == 100.0
    assert metrics["total_distance_km"] == 9.0
    assert metrics["total_time_min"] == 80
    assert metrics["driving_energy_value_usd"] == 0.5
    assert metrics["recharge_cost_usd"] == 0.5
    assert metrics["energy_cost_usd"] == 1.0
    assert metrics["charging_time_min"] == 20
    assert metrics["charging_stops"] == 1
    assert metrics["late_deliveries"] == 1
    assert metrics["min_soc_kwh"] == 3.0
    assert metrics["runtime_sec"] == 0.25
