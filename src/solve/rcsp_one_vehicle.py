from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from pathlib import Path
import pandas as pd
import osmnx as ox
import networkx as nx
import json
import math
from src.metrics.energy import EnergyModel, kwh_needed, model_from_vehicle_spec, usable_battery_kwh
from src.metrics.time_dependent import TimeDependentTravelMatrix, shortest_path_km, speed_kmph, travel_minutes_for_departure

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"


# Data types
@dataclass
class TEState:
    loc: str  # location id (origin, dest, or charger_id like "CH123")
    t: int  # minute from day start
    b: float  # battery kWh at this node
    cost: float  # accumulated $ cost (charging only)
    pred: Optional[Tuple[str, int, float, float]]  # (loc,t,b,cost) predecessor


@dataclass
class LegPlan:
    path: List[Tuple[str, int, float, float]]  # (loc, minute, kWh, cost)
    end_soc: float
    total_cost: float


# IO helpers
def _load_instance(instance_json: str):
    return json.load(open(instance_json))


def _load_graph(graphml_path: str | Path = PROCESSED_DIR / "manhattan_drive.graphml"):
    """Load OSMnx graph and keep only largest weakly-connected component."""
    G = ox.load_graphml(Path(graphml_path))
    ug = G.to_undirected()
    largest_cc = max(nx.connected_components(ug), key=len)
    return G.subgraph(largest_cc).copy()


# Geometry / travel helpers
def _nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    return ox.distance.nearest_nodes(G, lon, lat)  # (x=lon, y=lat)


def _path_length_km(G: nx.MultiDiGraph, path: List[int]) -> float:
    """Sum edge 'length' along a path, picking the shortest parallel edge per hop."""
    total_m = 0.0
    for u, v in zip(path[:-1], path[1:]):
        data = G.get_edge_data(u, v)
        if not data:
            continue
        if isinstance(data, dict):
            lens = [attrs.get("length", 0.0) for attrs in data.values()]
            total_m += min(lens) if lens else 0.0
        else:
            total_m += data.get("length", 0.0)
    return total_m / 1000.0


def _shortest_km(G: nx.MultiDiGraph, na: int, nb: int) -> float:
    """Try directed shortest path; if no path, fallback to undirected."""
    try:
        path = nx.shortest_path(G, na, nb, weight="length")
    except nx.NetworkXNoPath:
        path = nx.shortest_path(G.to_undirected(), na, nb, weight="length")
    return _path_length_km(G, path)


def _haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    radius_km = 6371.0
    d_lat = math.radians(b_lat - a_lat)
    d_lon = math.radians(b_lon - a_lon)
    lat1 = math.radians(a_lat)
    lat2 = math.radians(b_lat)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(h))


def _travel_minutes(km: float, minute: int, dt: int, base_speed_kmph: float = 30.0) -> int:
    return travel_minutes_for_departure(km, minute, dt, base_speed_kmph)


def _speed_kmph(minute: int, base_speed_kmph: float = 30.0) -> float:
    return speed_kmph(minute, base_speed_kmph)


def _minute_to_hour_idx(minute: int) -> int:
    return max(0, min(23, (minute // 60) % 24))


def _price_at_minute(prices_hourly: List[float], minute: int) -> float:
    return prices_hourly[_minute_to_hour_idx(minute)]


def _snap_up_to_grid(minutes: int, dt: int) -> int:
    """Round minutes up to the nearest multiple of dt."""
    return int(math.ceil(minutes / float(dt)) * dt)


def _best_charge_site_from(
        inst: Dict,
        G: nx.MultiDiGraph,
        location: Dict,
        chargers_df: pd.DataFrame,
        include_depot: bool,
) -> Optional[Dict]:
    """Return the closest real charging site from a location."""
    candidates = []
    for _, row in chargers_df.iterrows():
        power_kw = float(row.get("power_kw", 0.0) or 0.0)
        lat = float(row["lat"])
        lon = float(row["lon"])
        candidates.append({
            "id": f"CH{row['id']}",
            "lat": lat,
            "lon": lon,
            "power_kw": power_kw if power_kw > 0.1 else 7.2,
            "km": _haversine_km(float(location["lat"]), float(location["lon"]), lat, lon) * 1.35,
        })
    if include_depot:
        depot = inst["depot"]
        depot_lat = float(depot["lat"])
        depot_lon = float(depot["lon"])
        candidates.append({
            "id": depot["id"],
            "lat": depot_lat,
            "lon": depot_lon,
            "power_kw": 11.0,
            "km": _haversine_km(float(location["lat"]), float(location["lon"]), depot_lat, depot_lon) * 1.35,
        })
    if not candidates:
        return None
    return min(candidates, key=lambda c: c["km"])


def _charge_sites_from(
        inst: Dict,
        G: nx.MultiDiGraph,
        location: Dict,
        chargers_df: pd.DataFrame,
        include_depot: bool,
) -> List[Dict]:
    sites = []
    for _, row in chargers_df.iterrows():
        power_kw = float(row.get("power_kw", 0.0) or 0.0)
        lat = float(row["lat"])
        lon = float(row["lon"])
        sites.append({
            "id": f"CH{row['id']}",
            "lat": lat,
            "lon": lon,
            "power_kw": power_kw if power_kw > 0.1 else 7.2,
            "km": _haversine_km(float(location["lat"]), float(location["lon"]), lat, lon) * 1.35,
        })
    if include_depot:
        depot = inst["depot"]
        depot_lat = float(depot["lat"])
        depot_lon = float(depot["lon"])
        sites.append({
            "id": depot["id"],
            "lat": depot_lat,
            "lon": depot_lon,
            "power_kw": 11.0,
            "km": _haversine_km(float(location["lat"]), float(location["lon"]), depot_lat, depot_lon) * 1.35,
        })
    sites.sort(key=lambda c: c["km"])
    return sites


def _charger_matches(row, required_plug_type: str) -> bool:
    plugs = row.get("plugs", 1)
    try:
        if int(plugs) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if required_plug_type:
        plug_type = str(row.get("plug_type", row.get("connection_type", ""))).lower()
        if required_plug_type.lower() not in plug_type:
            return False
    return True


def _drive_leg(
        G: nx.MultiDiGraph,
        origin: Dict,
        destination: Dict,
        minute: int,
        soc: float,
        cost: float,
        cons_kwh_per_km: float,
        dt: int,
        energy_model: EnergyModel | None = None,
        travel_matrix: TimeDependentTravelMatrix | None = None,
) -> Tuple[Tuple[str, int, float, float], float, int]:
    if travel_matrix is not None:
        km, travel_min = travel_matrix.travel(origin["id"], destination["id"], minute)
    else:
        na = _nearest_node(G, float(origin["lat"]), float(origin["lon"]))
        nb = _nearest_node(G, float(destination["lat"]), float(destination["lon"]))
        km = _shortest_km(G, na, nb)
        travel_min = _travel_minutes(km, minute, dt)
    energy = kwh_needed(km, cons_kwh_per_km, energy_model, speed_kmph=_speed_kmph(minute))
    arrive = minute + travel_min
    return (destination["id"], arrive, soc - energy, cost), energy, arrive


# Label handling (dominance)
def _push_label(labels: Dict[Tuple[str, int], List[TEState]], key: Tuple[str, int], new: TEState, cap: int = 24):
    """Insert label if non-dominated; prune dominated; keep at most `cap` labels by quality."""
    cur = labels.get(key, [])
    # discard if dominated by existing
    for L in cur:
        if (L.b >= new.b - 1e-9) and (L.cost <= new.cost + 1e-9) and (L.t <= new.t + 1e-9):
            return
    # prune labels dominated by 'new'
    kept = []
    for L in cur:
        if (new.b >= L.b - 1e-9) and (new.cost <= L.cost + 1e-9) and (new.t <= L.t + 1e-9):
            continue
        kept.append(L)
    kept.append(new)
    # small cap to avoid explosion
    if len(kept) > cap:
        kept.sort(key=lambda s: (round(s.cost, 6), -round(s.b, 3), s.t))
        kept = kept[:cap]
    labels[key] = kept


# Time-expanded RCSP (single leg)
def rcsp_leg(
        instance_json: str,
        origin: Dict,  # {"id","lat","lon"}
        destination: Dict,  # {"id","lat","lon"}
        chargers_df: pd.DataFrame,  # id,name,lat,lon,power_kw,plugs
        start_minute: int,
        soc_start_kwh: float,
        soc_max_kwh: float,
        cons_kwh_per_km: float,
        dt: int = 10,
        horizon_pad_min: int = 180,
        base_speed_kmph: float = 30.0,
        allow_depot_charging: bool = True,
        depot_power_kw: float = 11.0,
        reserve_kwh: float = 0.0,
) -> LegPlan:
    """
    Forward-time RCSP for a single leg with price-aware charging.
    - chargers chosen by network distance from origin,
    - optional depot charging (useful when battery is tiny),
    - arrivals snapped to time grid for correctness.
    """
    inst = _load_instance(instance_json)
    prices_hourly = inst["prices_hourly"]
    G = _load_graph()
    from src.sim.traffic import speed_multiplier

    # Locations (origin, dest)
    locs = [
        {"id": origin["id"], "lat": origin["lat"], "lon": origin["lon"], "kind": "origin"},
        {"id": destination["id"], "lat": destination["lat"], "lon": destination["lon"], "kind": "dest"},
    ]

    # Snap origin/dest once
    n_origin = _nearest_node(G, origin["lat"], origin["lon"])
    n_dest = _nearest_node(G, destination["lat"], destination["lon"])

    # Snap chargers and compute their network distance from both origin and destination.
    ch = chargers_df.copy()
    snapped = []
    ug = G.to_undirected()
    dist_from_origin = nx.single_source_dijkstra_path_length(ug, n_origin, weight="length")
    dist_from_dest = nx.single_source_dijkstra_path_length(ug, n_dest, weight="length")
    for _, row in ch.iterrows():
        try:
            nid = _nearest_node(G, float(row["lat"]), float(row["lon"]))
        except Exception:
            continue
        if nid not in dist_from_origin and nid not in dist_from_dest:
            continue
        road_from_origin = dist_from_origin.get(nid, float("inf"))
        road_from_dest = dist_from_dest.get(nid, float("inf"))
        snapped.append({
            "sid": f"CH{row['id']}",
            "node": nid,
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "power_kw": float(row.get("power_kw", 0.0)),
            "plugs": int(row.get("plugs", 1)) if not pd.isna(row.get("plugs", 1)) else 1,
            "road_m": min(road_from_origin, road_from_dest),
        })
    # Keep enough charger candidates for detours without making every leg solve too expensive.
    K = 50
    snapped.sort(key=lambda r: r["road_m"])
    snapped = snapped[:K]

    # Build loc list (chargers), with fallback power if missing
    for r in snapped:
        pkw = r["power_kw"] if r["power_kw"] and r["power_kw"] > 0.1 else 7.2  # assume L2 7.2 kW if missing
        locs.append({
            "id": r["sid"],
            "lat": r["lat"], "lon": r["lon"],
            "kind": "charger",
            "power_kw": pkw,
            "plugs": r["plugs"],
        })

    # Optional: allow charging at the actual depot or when the leg starts at a public charger.
    depot_id = inst["depot"]["id"]
    if origin["id"].startswith("CH"):
        for l in locs:
            if l["id"] == origin["id"]:
                l["kind"] = "charger_origin"
                l["power_kw"] = float(origin.get("power_kw", 7.2))
                l["plugs"] = 1
                break
    elif allow_depot_charging and origin["id"] == depot_id:
        for l in locs:
            if l["id"] == origin["id"]:
                l["kind"] = "charger_origin"
                l["power_kw"] = float(depot_power_kw)
                l["plugs"] = 4
                break

    # Precompute pairwise distances (km) once, using snapped nodes
    id_to_node = {origin["id"]: n_origin, destination["id"]: n_dest}
    for r in snapped:
        id_to_node[r["sid"]] = r["node"]
    ids = [l["id"] for l in locs]
    pair_km: Dict[Tuple[str, str], float] = {}
    for a in ids:
        for b in ids:
            if a == b:
                continue
            pair_km[(a, b)] = _shortest_km(G, id_to_node[a], id_to_node[b])

    # Quick feasibility: direct drive?
    direct_km = pair_km[(origin["id"], destination["id"])]
    if direct_km * cons_kwh_per_km <= max(0.0, soc_start_kwh - reserve_kwh):
        mult = speed_multiplier(start_minute % (24 * 60))
        tmin_direct = math.ceil((direct_km / max(5.0, base_speed_kmph * mult)) * 60)
        tmin_direct = _snap_up_to_grid(tmin_direct, dt)
        end_t = start_minute + tmin_direct
        end_soc = soc_start_kwh - direct_km * cons_kwh_per_km
        return LegPlan(
            path=[(origin["id"], start_minute, soc_start_kwh, 0.0),
                  (destination["id"], end_t, end_soc, 0.0)],
            end_soc=end_soc, total_cost=0.0
        )

    # Horizon and time grid
    worst_mult = min(0.7, speed_multiplier(start_minute))
    direct_tmin = int(math.ceil((direct_km / max(5.0, base_speed_kmph * worst_mult)) * 60.0))
    H = start_minute + direct_tmin + horizon_pad_min
    times = list(range(start_minute, H + 1, dt))

    # Prepare TRAVEL id lists once
    from_ids = ids
    to_ids = ids

    # Labels
    labels: Dict[Tuple[str, int], List[TEState]] = {
        (origin["id"], start_minute): [TEState(origin["id"], start_minute, soc_start_kwh, 0.0, None)]}

    # Forward pass by time layer
    for t in times:
        layer_nodes = [key for key in labels.keys() if key[1] == t]
        if not layer_nodes:
            continue

        # 1) WAIT + CHARGE (local moves)
        for (loc_id, _t) in layer_nodes:
            for lab in list(labels[(loc_id, _t)]):
                # WAIT
                if t + dt <= H:
                    _push_label(labels, (loc_id, t + dt),
                                TEState(loc_id, t + dt, lab.b, lab.cost, (lab.loc, lab.t, lab.b, lab.cost)))
                # CHARGE
                loc_meta = next(l for l in locs if l["id"] == loc_id)
                if loc_meta["kind"] in ("charger", "charger_origin"):
                    power_kw = max(0.0, float(loc_meta.get("power_kw", 0.0)))
                    if power_kw > 0.1 and t + dt <= H:
                        kwh_gain = power_kw * (dt / 60.0)
                        price = _price_at_minute(prices_hourly, t)
                        new_b = lab.b + kwh_gain
                        if new_b <= soc_max_kwh + 1e-6:
                            _push_label(labels, (loc_id, t + dt),
                                        TEState(loc_id, t + dt, new_b, lab.cost + price * kwh_gain,
                                                (lab.loc, lab.t, lab.b, lab.cost)))

        # 2) TRAVEL (between distinct locations)
        for (loc_id, _t) in layer_nodes:
            for lab in list(labels[(loc_id, _t)]):
                for dst_id in to_ids:
                    if dst_id == loc_id:
                        continue
                    km = pair_km.get((loc_id, dst_id), 0.0)
                    if km <= 0.0:
                        continue
                    mult = speed_multiplier(t % (24 * 60))
                    speed = max(5.0, base_speed_kmph * mult)
                    tmin_raw = (km / speed) * 60.0
                    tmin = _snap_up_to_grid(int(math.ceil(tmin_raw)), dt)
                    t2 = t + tmin
                    if t2 > H:
                        continue
                    nb = lab.b - km * cons_kwh_per_km
                    if nb < reserve_kwh - 1e-6:
                        continue
                    _push_label(labels, (dst_id, t2),
                                TEState(dst_id, t2, nb, lab.cost, (lab.loc, lab.t, lab.b, lab.cost)))

    # Best destination label at any time
    best: Optional[TEState] = None
    for t in times:
        for L in labels.get((destination["id"], t), []):
            if best is None or L.cost < L.cost:  # bug guard (won't trigger)
                best = L
        # proper compare
        for L in labels.get((destination["id"], t), []):
            if best is None or L.cost < best.cost:
                best = L
    if best is None:
        raise RuntimeError("No energy-feasible path found; try larger horizon, more chargers, or bigger dt.")

    # Reconstruct
    seq: List[Tuple[str, int, float, float]] = []
    cur = best
    while cur is not None:
        seq.append((cur.loc, cur.t, cur.b, cur.cost))
        p = cur.pred
        if p is None:
            break
        cur = TEState(p[0], p[1], p[2], p[3], None)
    seq.reverse()

    return LegPlan(path=seq, end_soc=best.b, total_cost=best.cost)


# Orchestrator for a fixed visit order (one vehicle)
def plan_route_with_charging(
        instance_json: str,
        route_ids_in_order: List[str],  # e.g., ["DEPOT1","C003","C021","C014","DEPOT1"]
        vehicle_spec: Dict,
        start_minute: int,
        dt: int = 10,
        horizon_pad_min: int = 180,
        allow_depot_charging: bool = True,
        depot_power_kw: float = 11.0,
):
    inst = _load_instance(instance_json)
    depot_id = inst["depot"]["id"]
    if bool(vehicle_spec.get("mandatory_return_to_depot", True)) and route_ids_in_order:
        if route_ids_in_order[-1] != depot_id:
            route_ids_in_order = list(route_ids_in_order) + [depot_id]
    custs = {c["cust_id"]: (c["lat"], c["lon"]) for c in inst["customers"]}
    customer_meta = {c["cust_id"]: c for c in inst["customers"]}
    depot = (inst["depot"]["lat"], inst["depot"]["lon"])
    depot_end_min = int(inst["depot"].get("end_min", 24 * 60))
    shift_limit_min = int(vehicle_spec.get("shift_limit_min", 0) or 0)
    day_end_min = min(depot_end_min, int(start_minute) + shift_limit_min) if shift_limit_min > 0 else depot_end_min
    G = _load_graph()

    def id2latlon(i: str) -> Dict[str, float]:
        if i == inst["depot"]["id"]:
            return {"id": i, "lat": depot[0], "lon": depot[1]}
        if i.startswith("CH"):
            charger_id = i.removeprefix("CH")
            for row in inst["chargers"]:
                if str(row["id"]) == charger_id:
                    power_kw = float(row.get("power_kw", 0.0) or 0.0)
                    return {
                        "id": i,
                        "lat": float(row["lat"]),
                        "lon": float(row["lon"]),
                        "power_kw": power_kw if power_kw > 0.1 else 7.2,
                    }
            raise KeyError(i)
        return {"id": i, "lat": custs[i][0], "lon": custs[i][1]}

    ch = pd.DataFrame(inst["chargers"])
    if ch.empty:
        ch = pd.DataFrame(columns=["id", "name", "lat", "lon", "power_kw", "plugs", "plug_type"])
    required_plug_type = str(vehicle_spec.get("required_plug_type", "") or "").strip()
    if not ch.empty:
        ch = ch[ch.apply(lambda row: _charger_matches(row, required_plug_type), axis=1)].reset_index(drop=True)

    matrix_locations = {
        inst["depot"]["id"]: {"lat": depot[0], "lon": depot[1]},
    }
    for customer in inst["customers"]:
        matrix_locations[customer["cust_id"]] = {"lat": customer["lat"], "lon": customer["lon"]}
    for _, row in ch.iterrows():
        matrix_locations[f"CH{row['id']}"] = {"lat": float(row["lat"]), "lon": float(row["lon"])}
    travel_matrix = None
    try:
        node_by_id = {
            loc_id: _nearest_node(G, loc["lat"], loc["lon"])
            for loc_id, loc in matrix_locations.items()
        }

        def matrix_distance(origin_id: str, destination_id: str) -> float:
            return shortest_path_km(G, node_by_id, origin_id, destination_id)

        travel_matrix = TimeDependentTravelMatrix(
            cache_dir=CACHE_DIR,
            namespace="manhattan_drive",
            locations=matrix_locations,
            dt=dt,
            distance_func=matrix_distance,
        )
    except Exception:
        travel_matrix = None

    energy_model = model_from_vehicle_spec(vehicle_spec)
    soc_max = usable_battery_kwh(float(vehicle_spec["battery_kwh"]), energy_model)
    initial_soc_pct = float(vehicle_spec.get("initial_soc_pct", 1.0))
    initial_soc_pct = max(0.0, min(1.0, initial_soc_pct))
    soc = soc_max * initial_soc_pct
    cons = float(vehicle_spec["cons_kwh_per_km"])
    reserve_kwh = max(0.0, float(vehicle_spec.get("reserve_kwh", 0.0)))
    service_time_min = max(0, int(vehicle_spec.get("service_time_min", inst.get("service_time_min", 0)) or 0))
    queue_wait_min = max(0, int(vehicle_spec.get("charger_queue_wait_min", 0) or 0))
    cap_kg = float(vehicle_spec.get("cap_kg", float("inf")))
    route_demand_kg = sum(float(customer_meta[cid].get("demand_kg", 0.0)) for cid in route_ids_in_order if cid in customer_meta)
    if route_demand_kg > cap_kg + 1e-6:
        return {
            "timeline": [(route_ids_in_order[0] if route_ids_in_order else depot_id, int(start_minute), soc, 0.0)],
            "end_soc": soc,
            "total_energy_cost": 0.0,
            "end_time": int(start_minute),
            "completed": False,
            "completion_reason": "capacity",
            "completed_route_ids": [route_ids_in_order[0]] if route_ids_in_order else [],
            "remaining_route_ids": route_ids_in_order[1:] if route_ids_in_order else [],
        }

    def charge_here(location: Dict, minute: int, start_soc: float, start_cost: float, target_soc: float):
        power_kw = float(location.get("power_kw", depot_power_kw if location["id"] == inst["depot"]["id"] else 7.2))
        path = []
        soc_now = start_soc
        cost_now = start_cost
        t = minute
        target_soc = max(start_soc, min(soc_max, target_soc))
        if queue_wait_min > 0 and location["id"].startswith("CH"):
            wait_end = t + queue_wait_min
            if wait_end > day_end_min:
                return path, soc_now, t, cost_now
            path.append((location["id"], wait_end, soc_now, cost_now))
            t = wait_end
        while soc_now < target_soc - 1e-6:
            if t + dt > day_end_min:
                break
            t2 = t + dt
            gain = min(target_soc - soc_now, power_kw * (dt / 60.0))
            if gain <= 1e-9:
                break
            price = _price_at_minute(inst["prices_hourly"], t)
            soc_now += gain
            cost_now += gain * price
            path.append((location["id"], t2, soc_now, cost_now))
            t = t2
        return path, soc_now, t, cost_now

    def apply_customer_time_constraints(customer_id: str, minute: int, soc_now: float, cost_now: float):
        if customer_id not in customer_meta:
            return [], minute, True
        customer = customer_meta[customer_id]
        rows = []
        tw_start = int(customer.get("tw_start_min", 0))
        tw_end = int(customer.get("tw_end_min", 24 * 60))
        t = minute
        if t < tw_start:
            if tw_start > day_end_min:
                return rows, t, False
            rows.append((customer_id, tw_start, soc_now, cost_now))
            t = tw_start
        if t > tw_end:
            return rows, t, False
        if service_time_min > 0:
            service_end = t + service_time_min
            if service_end > tw_end or service_end > day_end_min:
                return rows, t, False
            rows.append((customer_id, service_end, soc_now, cost_now))
            t = service_end
        return rows, t, True

    completed = True
    completion_reason = "completed"
    completed_route_ids = [route_ids_in_order[0]] if route_ids_in_order else []
    cur_start = int(start_minute)
    current_id = route_ids_in_order[0] if route_ids_in_order else inst["depot"]["id"]
    current_loc = id2latlon(current_id)
    total_cost = 0.0
    timeline: List[Tuple[str, int, float, float]] = [(current_id, cur_start, soc, total_cost)]
    target_index = 1
    safety_counter = 0

    while target_index < len(route_ids_in_order):
        safety_counter += 1
        if safety_counter > len(route_ids_in_order) * 8 + 20:
            completed = False
            completion_reason = "energy"
            break
        target_id = route_ids_in_order[target_index]
        B = id2latlon(target_id)
        nearest_charge = _best_charge_site_from(inst, G, B, ch, include_depot=allow_depot_charging)
        needed_after_arrival = reserve_kwh
        if target_id != inst["depot"]["id"] and nearest_charge is not None:
            nearest_charge_kwh = kwh_needed(nearest_charge["km"], cons, energy_model, speed_kmph=_speed_kmph(cur_start))
            needed_after_arrival = min(soc_max, max(needed_after_arrival, nearest_charge_kwh))

        drive_row, drive_energy, arrive = _drive_leg(
            G, current_loc, B, cur_start, soc, total_cost, cons, dt, energy_model, travel_matrix
        )
        if arrive > day_end_min:
            completed = False
            completion_reason = "time"
            break
        if soc - drive_energy >= needed_after_arrival - 1e-6:
            timeline.append(drive_row)
            soc = drive_row[2]
            cur_start = arrive
            current_id = target_id
            current_loc = B
            constraint_rows, constrained_time, ok = apply_customer_time_constraints(target_id, cur_start, soc, total_cost)
            if not ok:
                completed = False
                completion_reason = "time_window"
                break
            if constraint_rows:
                timeline.extend(constraint_rows)
                cur_start = constrained_time
            completed_route_ids.append(target_id)
            target_index += 1
            continue

        charge_target = min(soc_max, drive_energy + needed_after_arrival)
        if (current_id.startswith("CH") or (allow_depot_charging and current_id == inst["depot"]["id"])) and soc < charge_target - 1e-6:
            charge_rows, charged_soc, charge_end, charged_cost = charge_here(
                current_loc,
                cur_start,
                soc,
                total_cost,
                charge_target,
            )
            if not charge_rows or charged_soc <= soc + 1e-6:
                completed = False
                completion_reason = "time"
                break
            timeline.extend(charge_rows)
            soc = charged_soc
            cur_start = charge_end
            total_cost = charged_cost
            continue

        reachable_sites = []
        for site in _charge_sites_from(inst, G, current_loc, ch, include_depot=allow_depot_charging)[:60]:
            if site["id"] == current_id:
                continue
            energy_to_site = kwh_needed(site["km"], cons, energy_model, speed_kmph=_speed_kmph(cur_start))
            if soc - energy_to_site >= reserve_kwh - 1e-6:
                reachable_sites.append(site)
        if not reachable_sites:
            completed = False
            completion_reason = "energy"
            break
        charge_site = None
        charge_row = None
        charge_arrive = None
        for site in reachable_sites:
            candidate_row, _, candidate_arrive = _drive_leg(
                G, current_loc, site, cur_start, soc, total_cost, cons, dt, energy_model, travel_matrix
            )
            if candidate_row[2] >= reserve_kwh - 1e-6:
                charge_site = site
                charge_row = candidate_row
                charge_arrive = candidate_arrive
                break
        if charge_site is None or charge_row is None or charge_arrive is None:
            completed = False
            completion_reason = "energy"
            break
        if charge_arrive > day_end_min:
            completed = False
            completion_reason = "time"
            break
        timeline.append(charge_row)
        current_id = charge_site["id"]
        current_loc = charge_site
        soc = charge_row[2]
        cur_start = charge_arrive

    if travel_matrix is not None:
        travel_matrix.save()

    return {
        "timeline": timeline,
        "end_soc": soc,
        "total_energy_cost": total_cost,
        "end_time": cur_start,
        "completed": completed,
        "completion_reason": completion_reason,
        "completed_route_ids": completed_route_ids,
        "remaining_route_ids": route_ids_in_order[len(completed_route_ids):],
    }
