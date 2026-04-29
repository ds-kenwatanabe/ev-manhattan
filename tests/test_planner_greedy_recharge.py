import json

import pytest

from src.solver import rcsp as planner


@pytest.mark.integration
def test_low_battery_route_inserts_recharge_and_continues(monkeypatch, tmp_path):
    inst = {
        "depot": {"id": "DEPOT1", "lat": 0.0, "lon": 0.0, "start_min": 480, "end_min": 720},
        "customers": [
            {"cust_id": "C001", "lat": 0.0, "lon": 1.0},
            {"cust_id": "C002", "lat": 0.0, "lon": 2.0},
        ],
        "chargers": [{"id": 1, "lat": 0.0, "lon": 1.5, "power_kw": 6.0, "plugs": 1}],
        "vehicles": [{"id": "V1", "battery_kwh": 2.0, "initial_soc_pct": 1.0, "cons_kwh_per_km": 1.0}],
        "prices_hourly": [0.25] * 24,
    }
    inst_path = tmp_path / "instance.json"
    inst_path.write_text(json.dumps(inst))

    distances = {
        ("DEPOT1", "C001"): 1.0,
        ("C001", "C002"): 1.0,
        ("C001", "CH1"): 0.5,
        ("CH1", "C002"): 0.5,
        ("C002", "DEPOT1"): 2.0,
        ("C002", "CH1"): 0.5,
        ("CH1", "DEPOT1"): 1.5,
    }

    def fake_drive_leg(_graph, origin, destination, minute, soc, cost, cons_kwh_per_km, dt, energy_model=None, travel_matrix=None):
        km = distances[(origin["id"], destination["id"])]
        energy = km * cons_kwh_per_km
        arrive = minute + 10
        return (destination["id"], arrive, soc - energy, cost), energy, arrive, km

    def fake_best_charge(_inst, _graph, location, _chargers_df, include_depot):
        if location["id"] == "DEPOT1":
            return {"id": "DEPOT1", "lat": 0.0, "lon": 0.0, "power_kw": 6.0, "km": 0.0}
        return {"id": "CH1", "lat": 0.0, "lon": 1.5, "power_kw": 6.0, "km": distances.get((location["id"], "CH1"), 0.5)}

    def fake_charge_sites(_inst, _graph, location, _chargers_df, include_depot):
        return [{"id": "CH1", "lat": 0.0, "lon": 1.5, "power_kw": 6.0, "km": distances.get((location["id"], "CH1"), 0.5)}]

    monkeypatch.setattr(planner, "_load_graph", lambda: object())
    monkeypatch.setattr(planner, "_drive_leg", fake_drive_leg)
    monkeypatch.setattr(planner, "_best_charge_site_from", fake_best_charge)
    monkeypatch.setattr(planner, "_charge_sites_from", fake_charge_sites)

    result = planner.plan_route_with_charging(
        instance_json=str(inst_path),
        route_ids_in_order=["DEPOT1", "C001", "C002", "DEPOT1"],
        vehicle_spec=inst["vehicles"][0],
        start_minute=480,
        dt=10,
        allow_depot_charging=True,
        depot_power_kw=6.0,
    )

    stops = [row[0] for row in result["timeline"]]

    assert result["completed"] is True
    assert stops.index("CH1") < stops.index("C002")
    assert "C002" in stops
    assert stops[-1] == "DEPOT1"
    assert result["end_soc"] >= 0
