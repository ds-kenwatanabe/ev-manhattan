from src.run.web_app import _build_routes


def _customers(n):
    return [{"cust_id": f"C{i:03d}", "lat": 40.0 + i * 0.001, "lon": -73.0} for i in range(n)]


def test_manual_selection_respects_customers_per_vehicle_limit():
    inst = {
        "depot": {"id": "DEPOT1", "lat": 40.0, "lon": -73.0},
        "customers": _customers(45),
    }
    form = {
        "customer_selection": ["manual"],
        "selected_customer_ids": [",".join(f"C{i:03d}" for i in range(45))],
    }

    routes = _build_routes(inst, vehicle_count=2, customers_per_vehicle=5, run_index=0, form=form)

    customer_counts = {
        vehicle: len([stop for stop in route if stop.startswith("C")])
        for vehicle, route in routes.items()
    }
    assert customer_counts == {"V1": 5, "V2": 5}


def test_random_selection_respects_customers_per_vehicle_limit():
    inst = {
        "depot": {"id": "DEPOT1", "lat": 40.0, "lon": -73.0},
        "customers": _customers(45),
    }
    form = {"customer_selection": ["random"]}

    routes = _build_routes(inst, vehicle_count=2, customers_per_vehicle=7, run_index=0, form=form)

    customer_counts = {
        vehicle: len([stop for stop in route if stop.startswith("C")])
        for vehicle, route in routes.items()
    }
    assert customer_counts == {"V1": 7, "V2": 7}
