import json
import pandas as pd
import time
from pathlib import Path

from src.eval.summarize import summarize_timeline
from src.sim.queues import simulate_queues_and_reprice
from src.solve.rcsp_one_vehicle import plan_route_with_charging

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "outputs"


def plan_day(inst_path: str, routes_by_vehicle: dict, veh_specs: dict,
             dt=10, horizon_pad_min=180, allow_depot_charging=True, depot_power_kw=11.0):
    inst = json.load(open(inst_path))
    chargers_df = pd.DataFrame(inst["chargers"])

    plans = {}
    all_sessions = []
    for vid, route in routes_by_vehicle.items():
        t0 = time.perf_counter()
        res = plan_route_with_charging(
            instance_json=inst_path,
            route_ids_in_order=route,
            vehicle_spec=veh_specs[vid],
            start_minute=inst["depot"]["start_min"],
            dt=dt, horizon_pad_min=horizon_pad_min,
            allow_depot_charging=allow_depot_charging, depot_power_kw=depot_power_kw,
        )
        res["runtime_sec"] = time.perf_counter() - t0
        plans[vid] = res
        s = summarize_timeline(res["timeline"])
        for rec in s["charges"]:
            rec["vehicle"] = vid
        all_sessions.extend(s["charges"])

    # simulate queues across ALL vehicles
    adj = simulate_queues_and_reprice(inst_path, chargers_df, all_sessions)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(plans, open(OUTPUT_DIR / "plans.json", "w"))
    pd.DataFrame(adj).to_csv(OUTPUT_DIR / "queue_adjusted_sessions.csv", index=False)

    return plans, adj
