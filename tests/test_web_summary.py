import pytest

from src.run.web_app import _route_events, _vehicle_summary


@pytest.fixture
def tiny_instance():
    return {
        "depot": {"id": "DEPOT1", "lat": 40.0, "lon": -73.0, "start_min": 480, "end_min": 720},
        "customers": [
            {"cust_id": "C001", "lat": 40.01, "lon": -73.01},
            {"cust_id": "C002", "lat": 40.02, "lon": -73.02},
        ],
        "chargers": [
            {"id": 10, "name": "Fast Charger", "lat": 40.015, "lon": -73.015, "power_kw": 50.0, "plugs": 1}
        ],
        "prices_hourly": [0.25] * 24,
    }


def test_route_events_include_one_clean_recharge_step():
    timeline = [
        ("DEPOT1", 480, 5.0, 0.0),
        ("C001", 500, 4.0, 0.0),
        ("CH10", 520, 3.0, 0.0),
        ("CH10", 540, 4.2, 0.30),
        ("CH10", 560, 5.0, 0.50),
        ("C002", 580, 4.1, 0.50),
    ]
    charges = [{"station_id": "CH10", "start_min": 520, "end_min": 560, "energy_kwh": 2.0, "cost_usd": 0.50}]

    events = _route_events(timeline, charges, "DEPOT1")

    assert [(event["event"], event["loc_id"]) for event in events] == [
        ("depot", "DEPOT1"),
        ("delivery", "C001"),
        ("recharge", "CH10"),
        ("delivery", "C002"),
    ]


@pytest.mark.integration
def test_vehicle_summary_formats_recharge_in_stop_order(tiny_instance):
    plan = {
        "timeline": [
            ("DEPOT1", 480, 5.0, 0.0),
            ("C001", 500, 4.0, 0.0),
            ("CH10", 520, 3.0, 0.0),
            ("CH10", 540, 4.2, 0.30),
            ("CH10", 560, 5.0, 0.50),
            ("C002", 580, 4.1, 0.50),
        ],
        "end_soc": 4.1,
        "end_time": 580,
        "completed": True,
        "completion_reason": "completed",
        "remaining_route_ids": [],
    }
    run_config = {"use_break": False, "break_billable": False, "break_start_min": 0, "break_end_min": 0}

    summary = _vehicle_summary(tiny_instance, "V1", ["DEPOT1", "C001", "C002"], plan, run_config)

    assert summary["status_text"] == "Completed route"
    assert [row["action"] for row in summary["route"]] == ["Depot", "Delivery", "Recharge", "Delivery"]
    recharge_row = summary["route"][2]
    assert recharge_row["id"] == "CH10"
    assert "2.00 kWh" in recharge_row["note"]
    assert "$0.50" in recharge_row["note"]
    assert summary["charges"][0]["station"] == "CH10 - Fast Charger"
