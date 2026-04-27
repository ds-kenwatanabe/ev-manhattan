import folium, json, osmnx as ox, networkx as nx
from pathlib import Path
from src.eval.summarize import grouped_charges

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def _largest_cc(G):
    ug = G.to_undirected()
    largest = max(nx.connected_components(ug), key=len)
    return G.subgraph(largest).copy()


def _nearest(G, lat, lon):
    return ox.distance.nearest_nodes(G, lon, lat)


def _path_nodes(G, a_lat, a_lon, b_lat, b_lon):
    na, nb = _nearest(G, a_lat, a_lon), _nearest(G, b_lat, b_lon)
    try:
        p = nx.shortest_path(G, na, nb, weight="length")
    except nx.NetworkXNoPath:
        p = nx.shortest_path(G.to_undirected(), na, nb, weight="length")
    return p


def _format_minutes(minute):
    minute = int(minute)
    return f"{(minute // 60) % 24:02d}:{minute % 60:02d}"


def _stop_events(timeline, charge_sessions, depot_id):
    charge_by_station = {}
    for charge in charge_sessions:
        charge_by_station.setdefault(charge["station_id"], []).append(charge)
    offsets = {station_id: 0 for station_id in charge_by_station}
    events = []
    last_loc = None
    for row in timeline:
        loc_id = row[0]
        if loc_id == last_loc:
            continue
        if loc_id.startswith("CH") or loc_id == depot_id:
            sessions = charge_by_station.get(loc_id, [])
            offset = offsets.get(loc_id, 0)
            if offset < len(sessions) and int(sessions[offset]["start_min"]) >= int(row[1]):
                events.append({"loc_id": loc_id, "action": "Recharge", "charge": sessions[offset]})
                offsets[loc_id] = offset + 1
            else:
                events.append({"loc_id": loc_id, "action": "Depot" if loc_id == depot_id else "Stop", "charge": None})
        else:
            events.append({"loc_id": loc_id, "action": "Delivery", "charge": None})
        last_loc = loc_id
    return events


def _lerp_color(low_hex, high_hex, value):
    value = max(0.0, min(1.0, float(value)))
    low = tuple(int(low_hex[i:i + 2], 16) for i in (1, 3, 5))
    high = tuple(int(high_hex[i:i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(int(low[i] + (high[i] - low[i]) * value) for i in range(3))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _line_color(mode, row, t_min, t_max, soc_max):
    if mode == "battery":
        return _lerp_color("#dc2626", "#16a34a", float(row[2]) / max(0.1, float(soc_max)))
    if mode == "time":
        span = max(1, int(t_max) - int(t_min))
        return _lerp_color("#2563eb", "#f97316", (int(row[1]) - int(t_min)) / span)
    return "#2563eb"


def overlay_plan(instance_json, plan, out_html=PROCESSED_DIR / "plan_map.html", color_by="default"):
    inst = json.load(open(instance_json))
    G = ox.load_graphml(PROCESSED_DIR / "manhattan_drive.graphml")
    G = _largest_cc(G)

    # coords by id
    coords = {inst["depot"]["id"]: (inst["depot"]["lat"], inst["depot"]["lon"])}
    names = {inst["depot"]["id"]: "Depot"}
    for c in inst["customers"]:
        coords[c["cust_id"]] = (c["lat"], c["lon"])
        names[c["cust_id"]] = c["cust_id"]
    for ch in inst["chargers"]:
        coords[f"CH{ch['id']}"] = (ch["lat"], ch["lon"])
        names[f"CH{ch['id']}"] = ch.get("name") or f"Charger {ch['id']}"

    m = folium.Map(location=[40.7831, -73.9712], zoom_start=12, control_scale=True)
    # markers
    folium.Marker(coords[inst["depot"]["id"]], tooltip="Depot", icon=folium.Icon(color="blue")).add_to(m)

    # draw drives as polylines
    tl = plan["timeline"]
    t_values = [int(row[1]) for row in tl] or [0]
    soc_values = [float(row[2]) for row in tl] or [1.0]
    t_min, t_max = min(t_values), max(t_values)
    soc_max = max(soc_values)
    stop_order = []
    for i in range(len(tl) - 1):
        a, b = tl[i][0], tl[i + 1][0]
        if a == b:  # charging or waiting block, skip
            continue
        if not stop_order:
            stop_order.append(a)
        stop_order.append(b)
        (alat, alon), (blat, blon) = coords[a], coords[b]
        nodes = _path_nodes(G, alat, alon, blat, blon)
        latlons = [(G.nodes[n]["y"], G.nodes[n]["x"]) for n in nodes]
        folium.PolyLine(
            latlons,
            weight=4,
            opacity=0.78,
            color=_line_color(color_by, tl[i], t_min, t_max, soc_max),
            tooltip=f"{a} -> {b} | {_format_minutes(tl[i][1])} | SoC {float(tl[i][2]):.2f} kWh",
        ).add_to(m)

    charges = grouped_charges(tl, depot_id=inst["depot"]["id"])
    stop_events = _stop_events(tl, charges, inst["depot"]["id"])

    # ordered route stop markers, including delivery and recharge steps
    for idx, event in enumerate(stop_events, start=1):
        loc_id = event["loc_id"]
        lat, lon = coords[loc_id]
        label = f"{idx}. {event['action']} | {loc_id}"
        charge = event.get("charge")
        if charge:
            label += f" | {_format_minutes(charge['start_min'])}-{_format_minutes(charge['end_min'])} | {charge['energy_kwh']:.2f} kWh | ${charge['cost_usd']:.2f}"
        tooltip = f"{label} | {names.get(loc_id, loc_id)} | {lat:.6f}, {lon:.6f}"
        color = "#2563eb"
        if loc_id == inst["depot"]["id"]:
            color = "#1d4ed8"
        elif event["action"] == "Recharge":
            color = "#15803d"
        html_label = (
            f"<div style='background:{color};color:white;border:2px solid white;"
            "border-radius:14px;box-shadow:0 1px 4px rgba(0,0,0,.35);"
            "font-size:11px;font-weight:700;line-height:20px;text-align:center;"
            "min-width:26px;height:24px;padding:0 5px;'>"
            f"{idx}</div>"
        )
        folium.Marker(
            [lat, lon],
            tooltip=tooltip,
            icon=folium.DivIcon(html=html_label, class_name="route-stop-label"),
        ).add_to(m)
        if event["action"] == "Delivery":
            folium.CircleMarker(
                [lat, lon],
                radius=5,
                tooltip=tooltip,
                color=color,
                fill=True,
                fill_opacity=0.85,
            ).add_to(m)

    # charger markers for grouped sessions
    for s in charges:
        lat, lon = coords[s["station_id"]]
        folium.Marker([lat, lon],
                      tooltip=f"Recharge | {s['station_id']} | {_format_minutes(s['start_min'])}-{_format_minutes(s['end_min'])} | {s['energy_kwh']:.2f} kWh | ${s['cost_usd']:.2f}",
                      icon=folium.Icon(color="green", icon="bolt", prefix="fa")).add_to(m)

    out_path = Path(out_html)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(out_path)
    return str(out_path)
