import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "outputs"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.run.plan_day import plan_day
from src.viz.overlay_plan import overlay_plan

INST = str(PROJECT_ROOT / "data" / "processed" / "instance_2025-07-15.json")
inst = json.load(open(INST))

# toy split: first 6 customers → V0, next 6 → V1
routes = {
    "V0": [inst["depot"]["id"]] + [c["cust_id"] for c in inst["customers"][:6]] + [inst["depot"]["id"]],
    "V1": [inst["depot"]["id"]] + [c["cust_id"] for c in inst["customers"][6:12]] + [inst["depot"]["id"]],
}

veh0 = dict(inst["vehicles"][0])
veh1 = dict(inst["vehicles"][0])
veh_specs = {"V0": veh0, "V1": veh1}

plans, adj = plan_day(
    inst_path=INST,
    routes_by_vehicle=routes,
    veh_specs=veh_specs,
    dt=10,
    horizon_pad_min=240,
    allow_depot_charging=True,
    depot_power_kw=11.0,
)

print(f"Planned {len(plans)} vehicles.")
print(f"Queue-adjusted sessions: {len(adj)} (saved to {OUTPUT_DIR / 'queue_adjusted_sessions.csv'})")

# OPTIONAL: write a separate map per vehicle
for vid, plan in plans.items():
    out_html = OUTPUT_DIR / f"plan_map_{vid}.html"
    saved = overlay_plan(INST, plan, out_html=out_html)
    print(f"{vid} map → {saved}")
