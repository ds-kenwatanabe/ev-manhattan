from __future__ import annotations

import math
from typing import Dict, Iterable, List

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from src.energy.model import kwh_needed, model_from_vehicle_spec, usable_battery_kwh


def haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    radius_km = 6371.0
    d_lat = math.radians(float(b_lat) - float(a_lat))
    d_lon = math.radians(float(b_lon) - float(a_lon))
    lat1 = math.radians(float(a_lat))
    lat2 = math.radians(float(b_lat))
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(h))


def nearest_neighbor_order(depot: Dict, customers: Iterable[Dict]) -> List[Dict]:
    remaining = list(customers)
    ordered = []
    current = depot
    while remaining:
        next_customer = min(
            remaining,
            key=lambda c: haversine_km(current["lat"], current["lon"], c["lat"], c["lon"]),
        )
        ordered.append(next_customer)
        remaining.remove(next_customer)
        current = next_customer
    return ordered


def split_round_robin(customers: List[Dict], vehicle_count: int) -> List[List[Dict]]:
    chunks = [[] for _ in range(vehicle_count)]
    for idx, customer in enumerate(customers):
        chunks[idx % vehicle_count].append(customer)
    return chunks


def build_nearest_neighbor_routes(
        depot: Dict,
        customers: List[Dict],
        vehicle_ids: List[str],
        customers_per_vehicle: int,
) -> Dict[str, List[str]]:
    ordered = nearest_neighbor_order(depot, customers)
    chunks = split_round_robin(ordered, len(vehicle_ids))
    routes = {}
    for idx, vehicle_id in enumerate(vehicle_ids):
        chunk = nearest_neighbor_order(depot, chunks[idx])[:customers_per_vehicle]
        routes[vehicle_id] = [depot["id"]] + [c["cust_id"] for c in chunk] + [depot["id"]]
    return routes


def build_ortools_vrptw_routes(
        depot: Dict,
        customers: List[Dict],
        vehicle_ids: List[str],
        customers_per_vehicle: int,
        vehicle_capacities: List[float] | None = None,
        service_minutes: int = 5,
        base_speed_kmph: float = 30.0,
        time_limit_seconds: int = 5,
) -> Dict[str, List[str]]:
    if not customers:
        return {vehicle_id: [depot["id"], depot["id"]] for vehicle_id in vehicle_ids}

    points = [{"id": depot["id"], "lat": depot["lat"], "lon": depot["lon"], "demand_kg": 0}]
    points.extend({**customer, "id": customer.get("id", customer["cust_id"])} for customer in customers)
    ids = [p["id"] for p in points]
    n = len(points)
    vehicle_count = len(vehicle_ids)
    depot_idx = 0

    time_matrix = [[0] * n for _ in range(n)]
    for i, a in enumerate(points):
        for j, b in enumerate(points):
            if i == j:
                continue
            km = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
            time_matrix[i][j] = int(math.ceil((km / base_speed_kmph) * 60.0))

    manager = pywrapcp.RoutingIndexManager(n, vehicle_count, depot_idx)
    routing = pywrapcp.RoutingModel(manager)

    def time_cb(from_idx, to_idx):
        i = manager.IndexToNode(from_idx)
        j = manager.IndexToNode(to_idx)
        service = service_minutes if i != depot_idx else 0
        return int(time_matrix[i][j] + service)

    time_cb_idx = routing.RegisterTransitCallback(time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(time_cb_idx)

    routing.AddDimension(time_cb_idx, 12 * 60, 24 * 60, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    depot_window = (int(depot.get("start_min", 0)), int(depot.get("end_min", 24 * 60)))
    for node, point in enumerate(points):
        start = int(point.get("tw_start_min", depot_window[0]))
        end = int(point.get("tw_end_min", depot_window[1]))
        start = max(0, min(start, 24 * 60))
        end = max(start, min(end, 24 * 60))
        time_dim.CumulVar(manager.NodeToIndex(node)).SetRange(start, end)

    def count_cb(from_idx):
        return 0 if manager.IndexToNode(from_idx) == depot_idx else 1

    count_cb_idx = routing.RegisterUnaryTransitCallback(count_cb)
    routing.AddDimensionWithVehicleCapacity(
        count_cb_idx,
        0,
        [int(customers_per_vehicle)] * vehicle_count,
        True,
        "CustomerCount",
    )

    capacities = vehicle_capacities or [10 ** 9] * vehicle_count
    demands = [0] + [int(float(c.get("demand_kg", 0))) for c in customers]

    def demand_cb(from_idx):
        return demands[manager.IndexToNode(from_idx)]

    demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_idx,
        0,
        [int(max(0, cap)) for cap in capacities],
        True,
        "Capacity",
    )

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = int(time_limit_seconds)

    solution = routing.SolveWithParameters(params)
    if not solution:
        return build_nearest_neighbor_routes(depot, customers, vehicle_ids, customers_per_vehicle)

    routes = {}
    for v_idx, vehicle_id in enumerate(vehicle_ids):
        idx = routing.Start(v_idx)
        route_ids = [depot["id"]]
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node != depot_idx:
                route_ids.append(ids[node])
            idx = solution.Value(routing.NextVar(idx))
        route_ids.append(depot["id"])
        routes[vehicle_id] = route_ids
    return routes


def build_charging_aware_routes(
        depot: Dict,
        customers: List[Dict],
        chargers: List[Dict],
        vehicle_ids: List[str],
        customers_per_vehicle: int,
        vehicle_specs: Dict[str, Dict],
        allow_depot_charging: bool = True,
) -> Dict[str, List[str]]:
    remaining = list(customers)
    routes = {}
    charge_sites = _charge_sites(depot, chargers, allow_depot_charging)

    for vehicle_id in vehicle_ids:
        spec = vehicle_specs.get(vehicle_id, {})
        energy_model = model_from_vehicle_spec(spec)
        cap = usable_battery_kwh(float(spec.get("battery_kwh", 0.0) or 0.0), energy_model)
        soc = cap * float(spec.get("initial_soc_pct", 1.0) or 0.0)
        reserve = float(spec.get("reserve_kwh", 0.0) or 0.0)
        cons = float(spec.get("cons_kwh_per_km", 0.2) or 0.2)
        current = depot
        route = [depot["id"]]

        for _ in range(customers_per_vehicle):
            if not remaining:
                break
            next_customer = _best_ev_customer(current, remaining, charge_sites, soc, cap, reserve, cons, energy_model)
            route.append(next_customer["cust_id"])
            drive_km = haversine_km(current["lat"], current["lon"], next_customer["lat"], next_customer["lon"])
            soc -= kwh_needed(drive_km, cons, energy_model)
            if cap > 0 and charge_sites:
                nearest_charge_kwh = _nearest_charge_kwh(next_customer, charge_sites, cons, energy_model)
                if soc < reserve + nearest_charge_kwh:
                    soc = cap
            current = next_customer
            remaining.remove(next_customer)

        route.append(depot["id"])
        routes[vehicle_id] = route

    return routes


def optimize_routes(
        inst: Dict,
        selected_customers: List[Dict],
        vehicle_ids: List[str],
        customers_per_vehicle: int,
        mode: str,
        vehicle_specs: Dict[str, Dict] | None = None,
        allow_depot_charging: bool = True,
) -> Dict[str, List[str]]:
    vehicle_specs = vehicle_specs or {}
    mode = (mode or "evrptw_greedy").strip()
    selected_customers = list(selected_customers)[:len(vehicle_ids) * customers_per_vehicle]
    if mode == "nearest_neighbor":
        return build_nearest_neighbor_routes(inst["depot"], selected_customers, vehicle_ids, customers_per_vehicle)
    if mode == "vrptw_ortools":
        capacities = [float(vehicle_specs.get(vid, {}).get("cap_kg", 10 ** 9)) for vid in vehicle_ids]
        service_minutes = int(next(iter(vehicle_specs.values()), {}).get("service_time_min", 5) or 0)
        return build_ortools_vrptw_routes(
            inst["depot"],
            selected_customers,
            vehicle_ids,
            customers_per_vehicle,
            vehicle_capacities=capacities,
            service_minutes=service_minutes,
        )
    return build_charging_aware_routes(
        inst["depot"],
        selected_customers,
        inst.get("chargers", []),
        vehicle_ids,
        customers_per_vehicle,
        vehicle_specs,
        allow_depot_charging=allow_depot_charging,
    )


def _charge_sites(depot: Dict, chargers: List[Dict], include_depot: bool) -> List[Dict]:
    sites = []
    if include_depot:
        sites.append({"id": depot["id"], "lat": depot["lat"], "lon": depot["lon"]})
    for charger in chargers:
        sites.append({"id": f"CH{charger['id']}", "lat": charger["lat"], "lon": charger["lon"]})
    return sites


def _nearest_charge_kwh(location: Dict, charge_sites: List[Dict], cons_kwh_per_km: float, energy_model=None) -> float:
    if not charge_sites:
        return 0.0
    km = min(haversine_km(location["lat"], location["lon"], site["lat"], site["lon"]) for site in charge_sites)
    return kwh_needed(km, cons_kwh_per_km, energy_model)


def _best_ev_customer(
        current: Dict,
        candidates: List[Dict],
        charge_sites: List[Dict],
        soc_kwh: float,
        battery_kwh: float,
        reserve_kwh: float,
        cons_kwh_per_km: float,
        energy_model=None,
) -> Dict:
    def score(customer: Dict) -> tuple:
        drive_km = haversine_km(current["lat"], current["lon"], customer["lat"], customer["lon"])
        drive_kwh = kwh_needed(drive_km, cons_kwh_per_km, energy_model)
        nearest_charge_kwh = _nearest_charge_kwh(customer, charge_sites, cons_kwh_per_km, energy_model)
        needed = reserve_kwh + nearest_charge_kwh
        after_arrival = soc_kwh - drive_kwh
        recharge_penalty = 0 if after_arrival >= needed else battery_kwh + max(0.0, needed - after_arrival)
        tw_start = int(customer.get("tw_start_min", 0))
        tw_end = int(customer.get("tw_end_min", 24 * 60))
        tw_width_penalty = max(0, 24 * 60 - (tw_end - tw_start)) / (24 * 60)
        return (recharge_penalty, drive_km, tw_width_penalty, customer["cust_id"])

    return min(candidates, key=score)
