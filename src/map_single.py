import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.solver.rcsp import plan_route_with_charging
from src.viz.overlay_plan import overlay_plan

INST = str(PROCESSED_DIR / "instance_2025-07-15.json")
inst = json.load(open(INST))

route = [inst["depot"]["id"]] + [c["cust_id"] for c in inst["customers"][:8]] + [inst["depot"]["id"]]

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

out = PROCESSED_DIR / "plan_map.html"
saved = overlay_plan(INST, res, out_html=out)
print(f"Map saved to {saved}")
