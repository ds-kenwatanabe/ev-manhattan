import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.solve.rcsp_one_vehicle import plan_route_with_charging

inst_path = str(PROCESSED_DIR / "instance_2025-07-15.json")
inst = json.load(open(inst_path))

# slightly longer route so we need juice
route = [inst["depot"]["id"]] + [c["cust_id"] for c in inst["customers"][:8]] + [inst["depot"]["id"]]

veh = dict(inst["vehicles"][0])
veh["battery_kwh"] = 12.0  # small battery to force a stop
veh["cons_kwh_per_km"] = 0.27  # hungry

res = plan_route_with_charging(
    instance_json=inst_path,
    route_ids_in_order=route,
    vehicle_spec=veh,
    start_minute=inst["depot"]["start_min"],
    dt=10,
    horizon_pad_min=180,
    allow_depot_charging=True,   # turn off later if you like
    depot_power_kw=11.0
)

print("Energy $:", round(res["total_energy_cost"], 2), "End SoC:", round(res["end_soc"], 2))
print("First 12 timeline rows:")
for row in res["timeline"][:12]:
    print(row)
