import folium
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def quick_map(instance_json=PROCESSED_DIR / "instance_2025-07-15.json",
              out_html=PROCESSED_DIR / "map.html"):
    inst = json.load(open(instance_json))
    m = folium.Map(location=[40.7831, -73.9712], zoom_start=12, control_scale=True)

    # depot
    d = inst["depot"]
    folium.Marker([d["lat"], d["lon"]], tooltip="Depot", icon=folium.Icon(color="blue")).add_to(m)

    # customers
    for c in inst["customers"]:
        folium.CircleMarker([c["lat"], c["lon"]], radius=3, tooltip=c["cust_id"]).add_to(m)

    # chargers
    for ch in inst["chargers"]:
        folium.Marker([ch["lat"], ch["lon"]], tooltip=f"{ch['name']} ({ch['power_kw']} kW, {ch['plugs']} plugs)",
                      icon=folium.Icon(color="green", icon="bolt", prefix="fa")).add_to(m)

    out_path = Path(out_html)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(out_path)
    return str(out_path)


if __name__ == "__main__":
    print(quick_map())
