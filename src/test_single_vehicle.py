# quick_test_rcsp.py (scratch)
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.solver.rcsp import plan_route_with_charging

inst_path = str(PROCESSED_DIR / "instance_2025-07-15.json")
inst = json.load(open(inst_path))

# take 1 route from your baseline VRPTW (or a simple manual route)
route_ids = [inst["depot"]["id"]] + [c["cust_id"] for c in inst["customers"][:5]] + [inst["depot"]["id"]]

veh = inst["vehicles"][0]
res = plan_route_with_charging(
    instance_json=inst_path,
    route_ids_in_order=route_ids,
    vehicle_spec=veh,
    start_minute=inst["depot"]["start_min"]
)

print("End time:", res["end_time"], "End SoC:", round(res["end_soc"],2), "Cost $:", round(res["total_energy_cost"],2))
print("First 10 timeline rows:")
for row in res["timeline"][:10]:
    print(row)
