from src.solve.route_optimizer import build_charging_aware_routes, build_ortools_vrptw_routes


def test_charging_aware_order_prefers_customer_near_charger_when_battery_is_tight():
    depot = {"id": "DEPOT1", "lat": 0.0, "lon": 0.0}
    customers = [
        {"cust_id": "C_ISOLATED", "lat": 0.0, "lon": -1.0},
        {"cust_id": "C_NEAR_CHARGER", "lat": 0.0, "lon": 1.5},
    ]
    chargers = [{"id": 1, "lat": 0.0, "lon": 1.5}]
    vehicle_specs = {
        "V1": {
            "battery_kwh": 170.0,
            "initial_soc_pct": 1.0,
            "reserve_kwh": 0.0,
            "cons_kwh_per_km": 1.0,
        }
    }

    routes = build_charging_aware_routes(
        depot,
        customers,
        chargers,
        vehicle_ids=["V1"],
        customers_per_vehicle=1,
        vehicle_specs=vehicle_specs,
        allow_depot_charging=False,
    )

    assert routes["V1"] == ["DEPOT1", "C_NEAR_CHARGER", "DEPOT1"]


def test_ortools_vrptw_baseline_assigns_requested_customer_counts():
    depot = {"id": "DEPOT1", "lat": 40.0, "lon": -73.0, "start_min": 480, "end_min": 1020}
    customers = [
        {
            "cust_id": f"C{i:03d}",
            "lat": 40.0 + i * 0.001,
            "lon": -73.0,
            "tw_start_min": 480,
            "tw_end_min": 1020,
            "demand_kg": 1,
        }
        for i in range(6)
    ]

    routes = build_ortools_vrptw_routes(
        depot,
        customers,
        vehicle_ids=["V1", "V2"],
        customers_per_vehicle=3,
        vehicle_capacities=[10, 10],
        time_limit_seconds=1,
    )

    assert {
        vehicle: len([stop for stop in route if stop.startswith("C")])
        for vehicle, route in routes.items()
    } == {"V1": 3, "V2": 3}
