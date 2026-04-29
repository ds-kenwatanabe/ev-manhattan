import json

from src.solve import rcsp_one_vehicle as planner


def test_vehicle_capacity_constraint_fails_before_dispatch(monkeypatch, tmp_path):
    inst = {
        "depot": {"id": "DEPOT1", "lat": 0.0, "lon": 0.0, "start_min": 480, "end_min": 720},
        "customers": [{"cust_id": "C001", "lat": 0.0, "lon": 1.0, "demand_kg": 20}],
        "chargers": [],
        "prices_hourly": [0.25] * 24,
    }
    inst_path = tmp_path / "instance.json"
    inst_path.write_text(json.dumps(inst))

    monkeypatch.setattr(planner, "_load_graph", lambda: object())

    result = planner.plan_route_with_charging(
        instance_json=str(inst_path),
        route_ids_in_order=["DEPOT1", "C001", "DEPOT1"],
        vehicle_spec={"battery_kwh": 10.0, "initial_soc_pct": 1.0, "cons_kwh_per_km": 1.0, "cap_kg": 10.0},
        start_minute=480,
    )

    assert result["completed"] is False
    assert result["completion_reason"] == "capacity"
    assert result["completed_route_ids"] == ["DEPOT1"]


def test_customer_time_window_wait_and_service_time(monkeypatch, tmp_path):
    inst = {
        "depot": {"id": "DEPOT1", "lat": 0.0, "lon": 0.0, "start_min": 480, "end_min": 720},
        "customers": [{"cust_id": "C001", "lat": 0.0, "lon": 1.0, "tw_start_min": 500, "tw_end_min": 540}],
        "chargers": [],
        "prices_hourly": [0.25] * 24,
    }
    inst_path = tmp_path / "instance.json"
    inst_path.write_text(json.dumps(inst))

    def fake_drive_leg(_graph, origin, destination, minute, soc, cost, cons_kwh_per_km, dt, energy_model=None, travel_matrix=None):
        arrive = minute + 10
        return (destination["id"], arrive, soc - 1.0, cost), 1.0, arrive, 1.0

    monkeypatch.setattr(planner, "_load_graph", lambda: object())
    monkeypatch.setattr(planner, "_drive_leg", fake_drive_leg)
    monkeypatch.setattr(planner, "_best_charge_site_from", lambda *_args, **_kwargs: {"id": "DEPOT1", "km": 0.0})

    result = planner.plan_route_with_charging(
        instance_json=str(inst_path),
        route_ids_in_order=["DEPOT1", "C001", "DEPOT1"],
        vehicle_spec={
            "battery_kwh": 10.0,
            "initial_soc_pct": 1.0,
            "cons_kwh_per_km": 1.0,
            "cap_kg": 100.0,
            "service_time_min": 10,
        },
        start_minute=480,
        dt=10,
    )

    assert result["completed"] is True
    assert ("C001", 500, 9.0, 0.0) in result["timeline"]
    assert ("C001", 510, 9.0, 0.0) in result["timeline"]


def test_partial_charging_stops_when_next_leg_is_feasible(monkeypatch, tmp_path):
    inst = {
        "depot": {"id": "DEPOT1", "lat": 0.0, "lon": 0.0, "start_min": 480, "end_min": 720},
        "customers": [{"cust_id": "C001", "lat": 0.0, "lon": 1.0}],
        "chargers": [{"id": 1, "lat": 0.0, "lon": 0.5, "power_kw": 6.0, "plugs": 1}],
        "prices_hourly": [0.25] * 24,
    }
    inst_path = tmp_path / "instance.json"
    inst_path.write_text(json.dumps(inst))

    distances = {
        ("DEPOT1", "C001"): 2.0,
        ("DEPOT1", "CH1"): 0.5,
        ("CH1", "C001"): 1.0,
        ("C001", "DEPOT1"): 1.0,
        ("C001", "CH1"): 0.5,
        ("CH1", "DEPOT1"): 0.5,
    }

    def fake_drive_leg(_graph, origin, destination, minute, soc, cost, cons_kwh_per_km, dt, energy_model=None, travel_matrix=None):
        energy = distances[(origin["id"], destination["id"])]
        arrive = minute + 10
        return (destination["id"], arrive, soc - energy, cost), energy, arrive, energy

    monkeypatch.setattr(planner, "_load_graph", lambda: object())
    monkeypatch.setattr(planner, "_drive_leg", fake_drive_leg)
    def fake_best_charge(_inst, _graph, location, _chargers_df, include_depot):
        return {"id": "CH1", "km": distances.get((location["id"], "CH1"), 0.0)}

    monkeypatch.setattr(planner, "_best_charge_site_from", fake_best_charge)
    monkeypatch.setattr(
        planner,
        "_charge_sites_from",
        lambda *_args, **_kwargs: [{"id": "CH1", "lat": 0.0, "lon": 0.5, "power_kw": 6.0, "km": 0.5}],
    )

    result = planner.plan_route_with_charging(
        instance_json=str(inst_path),
        route_ids_in_order=["DEPOT1", "C001", "DEPOT1"],
        vehicle_spec={"battery_kwh": 2.0, "initial_soc_pct": 1.0, "cons_kwh_per_km": 1.0, "cap_kg": 100.0},
        start_minute=480,
        dt=10,
    )

    charge_rows = [row for row in result["timeline"] if row[0] == "CH1" and row[2] >= 1.5]
    assert result["completed"] is True
    assert charge_rows
    assert max(row[2] for row in charge_rows) < 2.0


def test_charger_plug_filter_and_queue_wait(monkeypatch, tmp_path):
    inst = {
        "depot": {"id": "DEPOT1", "lat": 0.0, "lon": 0.0, "start_min": 480, "end_min": 720},
        "customers": [{"cust_id": "C001", "lat": 0.0, "lon": 1.0}],
        "chargers": [
            {"id": 1, "lat": 0.0, "lon": 0.5, "power_kw": 6.0, "plugs": 1, "plug_type": "J1772"},
            {"id": 2, "lat": 0.0, "lon": 0.6, "power_kw": 6.0, "plugs": 1, "plug_type": "CCS"},
        ],
        "prices_hourly": [0.25] * 24,
    }
    inst_path = tmp_path / "instance.json"
    inst_path.write_text(json.dumps(inst))

    distances = {
        ("DEPOT1", "C001"): 2.0,
        ("DEPOT1", "CH2"): 0.5,
        ("CH2", "C001"): 1.0,
        ("C001", "DEPOT1"): 1.0,
        ("C001", "CH2"): 0.5,
        ("CH2", "DEPOT1"): 0.5,
    }

    def fake_drive_leg(_graph, origin, destination, minute, soc, cost, cons_kwh_per_km, dt, energy_model=None, travel_matrix=None):
        energy = distances[(origin["id"], destination["id"])]
        arrive = minute + 10
        return (destination["id"], arrive, soc - energy, cost), energy, arrive, energy

    def fake_charge_sites(_inst, _graph, _location, chargers_df, include_depot):
        return [
            {"id": f"CH{row['id']}", "lat": row["lat"], "lon": row["lon"], "power_kw": row["power_kw"], "km": 0.5}
            for _, row in chargers_df.iterrows()
        ]

    monkeypatch.setattr(planner, "_load_graph", lambda: object())
    monkeypatch.setattr(planner, "_drive_leg", fake_drive_leg)
    monkeypatch.setattr(planner, "_best_charge_site_from", lambda *_args, **_kwargs: {"id": "CH2", "km": 0.5})
    monkeypatch.setattr(planner, "_charge_sites_from", fake_charge_sites)

    result = planner.plan_route_with_charging(
        instance_json=str(inst_path),
        route_ids_in_order=["DEPOT1", "C001", "DEPOT1"],
        vehicle_spec={
            "battery_kwh": 2.0,
            "initial_soc_pct": 1.0,
            "cons_kwh_per_km": 1.0,
            "cap_kg": 100.0,
            "required_plug_type": "CCS",
            "charger_queue_wait_min": 15,
        },
        start_minute=480,
        dt=10,
    )

    assert result["completed"] is True
    assert "CH1" not in [row[0] for row in result["timeline"]]
    assert ("CH2", 525, 0.0, 0.0) in result["timeline"]


def test_late_customer_window_is_recorded_and_route_continues(monkeypatch, tmp_path):
    inst = {
        "depot": {"id": "DEPOT1", "lat": 0.0, "lon": 0.0, "start_min": 480, "end_min": 720},
        "customers": [{"cust_id": "C001", "lat": 0.0, "lon": 1.0, "tw_start_min": 480, "tw_end_min": 485}],
        "chargers": [],
        "prices_hourly": [0.25] * 24,
    }
    inst_path = tmp_path / "instance.json"
    inst_path.write_text(json.dumps(inst))

    def fake_drive_leg(_graph, origin, destination, minute, soc, cost, cons_kwh_per_km, dt, energy_model=None, travel_matrix=None):
        arrive = minute + 10
        return (destination["id"], arrive, soc - 1.0, cost), 1.0, arrive, 1.0

    monkeypatch.setattr(planner, "_load_graph", lambda: object())
    monkeypatch.setattr(planner, "_drive_leg", fake_drive_leg)
    monkeypatch.setattr(planner, "_best_charge_site_from", lambda *_args, **_kwargs: None)

    result = planner.plan_route_with_charging(
        instance_json=str(inst_path),
        route_ids_in_order=["DEPOT1", "C001", "DEPOT1"],
        vehicle_spec={
            "battery_kwh": 5.0,
            "initial_soc_pct": 1.0,
            "cons_kwh_per_km": 1.0,
            "cap_kg": 100.0,
            "service_time_min": 5,
        },
        start_minute=480,
        dt=10,
    )

    assert result["completed"] is True
    assert result["late_delivery_ids"] == ["C001"]
    assert result["timeline"][-1][0] == "DEPOT1"
