# run_summary.py
import json
import sys
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 1) compute a plan (res)
from src.solver.rcsp import plan_route_with_charging
from src.eval.summarize import summarize_timeline
from src.experiments.queues import simulate_queues_and_reprice

INST = str(PROCESSED_DIR / "instance_2025-07-15.json")
inst = json.load(open(INST))

# Build a simple route: depot -> first 8 customers -> depot
route = [inst["depot"]["id"]] + [c["cust_id"] for c in inst["customers"][:8]] + [inst["depot"]["id"]]

# Force at least one charge so the summary has content (you can switch back to your real vehicle later)
veh = dict(inst["vehicles"][0])
veh["battery_kwh"] = 12.0
veh["cons_kwh_per_km"] = 0.27

res = plan_route_with_charging(
    instance_json=INST,
    route_ids_in_order=route,
    vehicle_spec=veh,
    start_minute=inst["depot"]["start_min"],
    dt=10,
    horizon_pad_min=240,
    allow_depot_charging=True,
    depot_power_kw=11.0,
)

print("Energy $:", round(res["total_energy_cost"], 2), "End SoC:", round(res["end_soc"], 2))

# 2) summarize timeline → drives + charge sessions
summary = summarize_timeline(res["timeline"])
print("\nCharges:")
for s in summary["charges"]:
    print(s)
print("\nFirst 3 drives:")
for d in summary["drives"][:3]:
    print(d)

# 3) simulate charger queues (you'll see effects when you have >1 vehicle)
chargers_df = pd.DataFrame(inst["chargers"])
adj = simulate_queues_and_reprice(INST, chargers_df, summary["charges"])
print("\nQueue-adjusted sessions (if any waits):")
for x in adj:
    print(x)
