import json
from pathlib import Path
import pandas as pd
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def solve_baseline(instance_json=PROCESSED_DIR / "instance_2025-07-15.json"):
    inst = json.load(open(instance_json))
    depot = inst["depot"]
    custs = pd.DataFrame(inst["customers"])
    # build point list: depot + customers
    points = [{"id": depot["id"], "lat": depot["lat"], "lon": depot["lon"]}] + custs[["cust_id", "lat", "lon"]].rename(
        columns={"cust_id": "id"}).to_dict("records")

    from src.graph.travel import build_matrices
    ids, dist_km, time_min, _ = build_matrices(pd.DataFrame(points))

    # OR-Tools data
    data = {
        "time_matrix": time_min,
        "num_vehicles": len(inst["vehicles"]),
        "depot": 0,
        "demands": [0] + custs["demand_kg"].tolist(),
        "vehicle_capacities": [v["cap_kg"] for v in inst["vehicles"]],
        "time_windows": [(depot["start_min"], depot["end_min"])]
                        + list(zip(custs["tw_start_min"], custs["tw_end_min"])),
        "service_time": 5  # minutes at each customer
    }

    # Routing index manager
    manager = pywrapcp.RoutingIndexManager(len(ids), data["num_vehicles"], data["depot"])
    routing = pywrapcp.RoutingModel(manager)

    # Transit callback (time)
    def time_cb(from_idx, to_idx):
        i, j = manager.IndexToNode(from_idx), manager.IndexToNode(to_idx)
        return int(data["time_matrix"][i][j] + (data["service_time"] if i != data["depot"] else 0))

    time_cb_idx = routing.RegisterTransitCallback(time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(time_cb_idx)

    # Capacity
    demands = [0] + data["demands"][1:]

    def demand_cb(from_idx):
        i = manager.IndexToNode(from_idx)
        return int(demands[i])

    demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_idx, 0, data["vehicle_capacities"], True, "Capacity"
    )

    # Time windows
    routing.AddDimension(
        time_cb_idx,  # transit
        60,  # slack
        14 * 60,  # vehicle horizon
        True,  # force start at window
        "Time"
    )
    time_dim = routing.GetDimensionOrDie("Time")
    for node, (start, end) in enumerate(data["time_windows"]):
        index = manager.NodeToIndex(node)
        time_dim.CumulVar(index).SetRange(start, end)

    # Search params
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = 30

    solution = routing.SolveWithParameters(params)
    routes = []
    if solution:
        for v in range(data["num_vehicles"]):
            idx = routing.Start(v)
            path, tcur = [], []
            while not routing.IsEnd(idx):
                node = manager.IndexToNode(idx)
                path.append(ids[node])
                tval = solution.Min(time_dim.CumulVar(idx))
                tcur.append(int(tval))
                idx = solution.Value(routing.NextVar(idx))
            path.append(ids[manager.IndexToNode(idx)])
            tcur.append(int(solution.Min(time_dim.CumulVar(idx))))
            routes.append({"vehicle": v, "path": path, "times": tcur})
    return routes
