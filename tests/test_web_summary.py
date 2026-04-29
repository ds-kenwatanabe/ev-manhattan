import pytest

from src.web.app import _build_routes, _route_events, _svg_curve, _vehicle_summary, _write_summary_csv


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
    assert events[1]["served_min"] == 500
    assert events[3]["served_min"] == 580


def test_route_events_use_end_of_customer_segment_as_served_time():
    timeline = [
        ("DEPOT1", 480, 5.0, 0.0),
        ("C001", 490, 4.0, 0.0),
        ("C001", 500, 4.0, 0.0),
        ("C001", 505, 4.0, 0.0),
        ("DEPOT1", 520, 3.8, 0.0),
    ]

    events = _route_events(timeline, [], "DEPOT1")

    assert events[1]["event"] == "delivery"
    assert events[1]["arrival_min"] == 490
    assert events[1]["served_min"] == 505


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
    assert summary["route"][1]["served_at"] == "08:20"
    assert summary["route"][3]["served_at"] == "09:40"
    assert summary["customer_served_times"] == ["C001 08:20", "C002 09:40"]
    recharge_row = summary["route"][2]
    assert recharge_row["id"] == "CH10"
    assert "2.00 kWh" in recharge_row["note"]
    assert "$0.50" in recharge_row["note"]
    assert summary["charges"][0]["station"] == "CH10 - Fast Charger"


def test_svg_curve_renders_timeline_path():
    html = _svg_curve(
        [{"time": 480, "soc": 5.0, "cost": 0.0}, {"time": 540, "soc": 3.5, "cost": 1.2}],
        "soc",
        "#0f766e",
        "SoC kWh",
    )

    assert "<svg" in html
    assert "SoC kWh" in html
    assert "<path" in html


def test_summary_csv_export(tmp_path, monkeypatch):
    monkeypatch.setattr("src.web.app.OUTPUT_DIR", tmp_path)
    path = _write_summary_csv(
        "stamp",
        1,
        [{
            "vehicle_id": "V1",
            "completed": False,
            "status_text": "Did not complete route in the given time",
            "drive_energy_kwh": 2.0,
            "charge_energy_kwh": 1.0,
            "drive_energy_cost_usd": 0.5,
            "charge_cost_usd": 0.25,
            "total_energy_cost": 0.75,
            "end_soc": 3.0,
            "end_time": 540,
            "elapsed_min": 60,
            "billable_min": 50,
            "remaining_route_ids": ["C002"],
            "customer_served_times": ["C001 08:20"],
        }],
    )

    text = path.read_text()
    assert "infeasibility_reason" in text
    assert "customer_served_times" in text
    assert "C001 08:20" in text
    assert "Did not complete route in the given time" in text


def test_manual_order_optimizer_preserves_dragged_selection_order():
    inst = {
        "depot": {"id": "DEPOT1", "lat": 40.0, "lon": -73.0},
        "customers": [
            {"cust_id": "C000", "lat": 40.0, "lon": -73.0},
            {"cust_id": "C001", "lat": 40.1, "lon": -73.0},
            {"cust_id": "C002", "lat": 40.2, "lon": -73.0},
        ],
    }
    form = {
        "customer_selection": ["manual"],
        "optimizer_mode": ["manual_order"],
        "selected_customer_ids": ["C002,C000,C001"],
    }

    routes = _build_routes(inst, vehicle_count=1, customers_per_vehicle=3, run_index=0, form=form)

    assert routes["V1"] == ["DEPOT1", "C002", "C000", "C001", "DEPOT1"]
