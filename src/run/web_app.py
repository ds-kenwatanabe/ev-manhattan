import argparse
import copy
import html
import json
import math
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPoint, Point

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "outputs"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.run.plan_day import plan_day
from src.eval.summarize import grouped_charges, summarize_timeline
from src.fetch_nyiso import fetch_nyiso_zone_j_day
from src.solve.route_optimizer import optimize_routes
from src.viz.map import quick_map
from src.viz.overlay_plan import overlay_plan

BASE_INSTANCE = PROCESSED_DIR / "instance_2025-07-15.json"
PRICE_PARQUET = PROCESSED_DIR / "nyiso_zone_j_2025_07.parquet"
EDGES_PARQUET = PROCESSED_DIR / "edges.parquet"
NODES_PARQUET = PROCESSED_DIR / "nodes.parquet"
_STREET_SEGMENTS = None
_MANHATTAN_AREA = None


def _load_base_instance():
    return json.load(open(BASE_INSTANCE))


def _field(form, name, default):
    vals = form.get(name)
    return vals[0] if vals else default


def _int_field(form, name, default, min_value=None, max_value=None):
    try:
        value = int(_field(form, name, default))
    except (TypeError, ValueError):
        value = int(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _float_field(form, name, default, min_value=None, max_value=None):
    try:
        value = float(_field(form, name, default))
    except (TypeError, ValueError):
        value = float(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _checked(form, name, default=True):
    if name in form:
        return _field(form, name, "off") == "on"
    return default


def _default_form():
    inst = _load_base_instance()
    depot = inst["depot"]
    vehicle = inst["vehicles"][0]
    return {
        "day": inst.get("meta", {}).get("date", "2025-07-15"),
        "price_source": "synthetic",
        "flat_price_usd_per_kwh": "0.30",
        "charger_min_power_kw": "0.0",
        "charger_limit": str(len(inst["chargers"])),
        "depot_lat": str(depot["lat"]),
        "depot_lon": str(depot["lon"]),
        "depot_mode": "coordinates",
        "start_time": _minutes_to_time_value(depot["start_min"]),
        "end_time": _minutes_to_time_value(depot["end_min"]),
        "enable_break": "off",
        "break_start_time": "12:00",
        "break_end_time": "13:00",
        "break_billable": "off",
        "customer_selection": "random",
        "selected_customer_ids": "",
        "available_customer_count": "150",
        "vehicle_count": "2",
        "runs": "1",
        "customers_per_vehicle": "3",
        "optimizer_mode": "evrptw_greedy",
        "battery_kwh": str(vehicle["battery_kwh"]),
        "initial_soc_pct": "1.0",
        "reserve_kwh": "0.0",
        "cons_kwh_per_km": str(vehicle["cons_kwh_per_km"]),
        "energy_model_enabled": "off",
        "payload_kg": "0",
        "stop_density_per_km": "2.0",
        "stop_go_penalty_per_stop": "0.008",
        "ambient_temp_c": "20",
        "hvac_penalty_per_deg_c": "0.006",
        "speed_penalty_factor": "0.35",
        "regen_credit": "0.08",
        "battery_degradation_pct": "0.0",
        "cap_kg": str(vehicle["cap_kg"]),
        "dt": "10",
        "horizon_pad_min": "240",
        "allow_depot_charging": "on",
        "depot_power_kw": "11.0",
    }


def _haversine_km(a_lat, a_lon, b_lat, b_lon):
    radius_km = 6371.0
    d_lat = math.radians(b_lat - a_lat)
    d_lon = math.radians(b_lon - a_lon)
    lat1 = math.radians(a_lat)
    lat2 = math.radians(b_lat)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(h))


def _manhattan_area():
    global _MANHATTAN_AREA
    if _MANHATTAN_AREA is not None:
        return _MANHATTAN_AREA
    nodes = pd.read_parquet(NODES_PARQUET)[["x", "y"]].dropna()
    points = [Point(float(row.x), float(row.y)) for row in nodes.itertuples(index=False)]
    hull = MultiPoint(points).convex_hull
    _MANHATTAN_AREA = hull.buffer(0.004)
    return _MANHATTAN_AREA


def _point_in_manhattan(lat, lon):
    return _manhattan_area().contains(Point(float(lon), float(lat)))


def _manhattan_bounds():
    min_lon, min_lat, max_lon, max_lat = _manhattan_area().bounds
    return {
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lon": min_lon,
        "max_lon": max_lon,
    }


def _nearest_neighbor_order(depot, customers):
    remaining = list(customers)
    ordered = []
    cur_lat = float(depot["lat"])
    cur_lon = float(depot["lon"])
    while remaining:
        next_customer = min(
            remaining,
            key=lambda c: _haversine_km(cur_lat, cur_lon, float(c["lat"]), float(c["lon"])),
        )
        ordered.append(next_customer)
        remaining.remove(next_customer)
        cur_lat = float(next_customer["lat"])
        cur_lon = float(next_customer["lon"])
    return ordered


def _split_customers(customers, vehicle_count):
    chunks = [[] for _ in range(vehicle_count)]
    for idx, customer in enumerate(customers):
        chunks[idx % vehicle_count].append(customer)
    return chunks


def _selected_customer_ids(form):
    raw = _field(form, "selected_customer_ids", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _generate_customer_pool(count, seed=7):
    nodes = pd.read_parquet(NODES_PARQUET)
    count = max(1, min(int(count), len(nodes)))
    keep = nodes.sample(count, random_state=seed)[["y", "x"]].reset_index(drop=True)
    rng = np.random.default_rng(seed)
    start = rng.integers(9 * 60, 13 * 60, size=count)
    end = start + rng.integers(120, 360, size=count)
    demand = rng.integers(10, 60, size=count)
    customers = []
    for i in range(count):
        customers.append({
            "cust_id": f"C{i:03d}",
            "lat": float(keep.loc[i, "y"]),
            "lon": float(keep.loc[i, "x"]),
            "tw_start_min": int(start[i]),
            "tw_end_min": int(end[i]),
            "demand_kg": int(demand[i]),
        })
    return customers


def _filter_and_rank_chargers(chargers, min_power, limit):
    nodes = pd.read_parquet(NODES_PARQUET)[["y", "x"]].dropna()
    node_records = list(nodes.itertuples(index=False))
    anchors = []
    for frac in np.linspace(0.02, 0.98, num=max(1, min(24, int(limit or 1)))):
        idx = min(len(node_records) - 1, int(frac * (len(node_records) - 1)))
        row = node_records[idx]
        anchors.append((float(row.y), float(row.x)))

    candidates = []
    for charger in chargers:
        power_kw = float(charger.get("power_kw") or 0.0)
        lat = float(charger.get("lat"))
        lon = float(charger.get("lon"))
        if power_kw < min_power:
            continue
        if not _point_in_manhattan(lat, lon):
            continue
        nearest_anchor_km = min(_haversine_km(lat, lon, a_lat, a_lon) for a_lat, a_lon in anchors) if anchors else 0.0
        candidates.append((nearest_anchor_km, -power_kw, charger))

    candidates.sort(key=lambda row: (row[0], row[1]))
    selected = []
    seen_ids = set()
    for _, _, charger in candidates:
        charger_id = str(charger.get("id"))
        if charger_id in seen_ids:
            continue
        seen_ids.add(charger_id)
        selected.append(charger)
        if len(selected) >= limit:
            break
    return selected


def _build_routes(
        inst,
        vehicle_count,
        customers_per_vehicle,
        run_index,
        form,
        vehicle_specs=None,
        allow_depot_charging=True,
):
    customers = inst["customers"]
    if not customers:
        return {}

    customer_by_id = {customer["cust_id"]: customer for customer in customers}
    selection_mode = _field(form, "customer_selection", "random")
    total_needed = vehicle_count * customers_per_vehicle
    if selection_mode == "manual":
        selected = [customer_by_id[cid] for cid in _selected_customer_ids(form) if cid in customer_by_id]
        if not selected:
            selected = customers[:total_needed]
        else:
            selected = selected[:total_needed]
    else:
        start = (run_index * total_needed) % len(customers)
        selected = [customers[(start + i) % len(customers)] for i in range(total_needed)]

    vehicle_ids = [f"V{idx + 1}" for idx in range(vehicle_count)]
    return optimize_routes(
        inst=inst,
        selected_customers=selected,
        vehicle_ids=vehicle_ids,
        customers_per_vehicle=customers_per_vehicle,
        mode=_field(form, "optimizer_mode", "evrptw_greedy"),
        vehicle_specs=vehicle_specs or {},
        allow_depot_charging=allow_depot_charging,
    )


def _location_names(inst):
    names = {
        inst["depot"]["id"]: "Depot",
    }
    for customer in inst["customers"]:
        names[customer["cust_id"]] = customer["cust_id"]
    for charger in inst["chargers"]:
        names[f"CH{charger['id']}"] = charger.get("name") or f"Charger {charger['id']}"
    return names


def _location_details(inst):
    details = {
        inst["depot"]["id"]: {
            "id": inst["depot"]["id"],
            "name": "Depot",
            "lat": float(inst["depot"]["lat"]),
            "lon": float(inst["depot"]["lon"]),
            "kind": "Depot",
        }
    }
    for customer in inst["customers"]:
        details[customer["cust_id"]] = {
            "id": customer["cust_id"],
            "name": customer["cust_id"],
            "lat": float(customer["lat"]),
            "lon": float(customer["lon"]),
            "kind": "Customer",
        }
    for charger in inst["chargers"]:
        loc_id = f"CH{charger['id']}"
        details[loc_id] = {
            "id": loc_id,
            "name": charger.get("name") or f"Charger {charger['id']}",
            "lat": float(charger["lat"]),
            "lon": float(charger["lon"]),
            "kind": "Charger",
        }
    return details


def _format_location(loc_id, names):
    name = names.get(loc_id, loc_id)
    if name == loc_id:
        return loc_id
    return f"{loc_id} - {name}"


def _format_minutes(minute):
    minute = int(minute)
    hour = (minute // 60) % 24
    mins = minute % 60
    return f"{hour:02d}:{mins:02d}"


def _minutes_to_time_value(minute):
    minute = int(minute)
    return f"{(minute // 60) % 24:02d}:{minute % 60:02d}"


def _parse_time_value(value, default_minute):
    if value is None:
        return int(default_minute)
    try:
        hour_str, minute_str = str(value).split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    except (TypeError, ValueError):
        pass
    return int(default_minute)


def _time_field(form, name, default_minute):
    return _parse_time_value(_field(form, name, None), default_minute)


def _overlap_minutes(start_a, end_a, start_b, end_b):
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def _price_at_minute(inst, minute):
    prices = inst.get("prices_hourly") or [0.0] * 24
    hour = max(0, min(23, (int(minute) // 60) % 24))
    return float(prices[hour])


def _synthetic_prices_for_day(day_str):
    day = pd.Timestamp(day_str)
    prices = []
    for hour in range(24):
        price = 0.22
        if 14 <= hour < 20:
            price += 0.18
        if 20 <= hour < 23:
            price += 0.07
        if 0 <= hour < 6:
            price -= 0.06
        if day.weekday() < 5:
            price += 0.03
        prices.append(price)
    return prices, "synthetic summer profile"


def _nyiso_error_summary(errors):
    text = " ".join(str(error).lower() for error in errors)
    if any(fragment in text for fragment in ("temporary failure in name resolution", "name or service not known", "nodename nor servname", "failed to resolve")):
        return "network/DNS unavailable"
    if any(fragment in text for fragment in ("404", "not found", "returned 0 hourly prices")):
        return "NYISO data not published for that date"
    if "timed out" in text or "timeout" in text:
        return "NYISO request timed out"
    if errors:
        return "NYISO data unavailable"
    return "NYISO data unavailable"


def _prices_for_day(day_str, price_source, flat_price):
    if price_source == "flat":
        return [float(flat_price)] * 24, f"flat ${float(flat_price):.3f}/kWh"

    if price_source == "synthetic":
        return _synthetic_prices_for_day(day_str)

    if price_source == "nyiso_dynamic":
        errors = []
        try:
            path = fetch_nyiso_zone_j_day(day_str, market="dam", timeout_sec=5)
            df = pd.read_parquet(path).rename_axis("datetime").reset_index()
            prices = df["price_usd_per_kwh"].astype(float).tolist()
            if len(prices) == 24:
                return prices, f"NYISO Zone J day-ahead profile for {day_str}"
        except Exception as exc:
            errors.append(f"day-ahead: {exc}")
        try:
            path = fetch_nyiso_zone_j_day(day_str, market="rt", timeout_sec=5)
            df = pd.read_parquet(path).rename_axis("datetime").reset_index()
            prices = df["price_usd_per_kwh"].astype(float).tolist()
            if len(prices) == 24:
                return prices, f"NYISO Zone J real-time profile for {day_str}"
        except Exception as exc:
            errors.append(f"real-time: {exc}")
        if PRICE_PARQUET.exists():
            local_prices = _prices_for_day(day_str, "nyiso_local", flat_price)
            if "unavailable" not in local_prices[1]:
                return local_prices[0], f"NYISO selected-day unavailable for {day_str}; using {local_prices[1]}"
        prices, fallback = _synthetic_prices_for_day(day_str)
        reason = _nyiso_error_summary(errors)
        return prices, f"NYISO selected-day unavailable for {day_str} ({reason}); using {fallback}"

    if price_source == "nyiso_local" and PRICE_PARQUET.exists():
        df = pd.read_parquet(PRICE_PARQUET)
        df = df.rename_axis("datetime").reset_index()
        df["datetime"] = pd.to_datetime(df["datetime"])
        day = pd.Timestamp(day_str)
        selected = df[df["datetime"].dt.date == day.date()].sort_values("datetime")
        if not selected.empty:
            prices = selected["price_usd_per_kwh"].astype(float).tolist()
            if len(prices) != 24:
                prices = (
                    selected.set_index("datetime")
                    .resample("h")["price_usd_per_kwh"]
                    .mean()
                    .iloc[:24]
                    .astype(float)
                    .tolist()
                )
            if len(prices) == 24:
                return prices, f"local NYISO Zone J profile for {day_str}"

        prices, fallback = _synthetic_prices_for_day(day_str)
        return prices, f"NYISO local profile unavailable for {day_str}; using {fallback}"

    prices, fallback = _synthetic_prices_for_day(day_str)
    return prices, f"unknown price source; using {fallback}"


def _write_daily_price_files(stamp, run_index, day_str, prices, source_label):
    price_df = pd.DataFrame({
        "datetime": pd.date_range(day_str, periods=24, freq="h"),
        "hour": list(range(24)),
        "zone": ["J"] * 24,
        "price_usd_per_kwh": prices,
        "source": [source_label] * 24,
        "day": [day_str] * 24,
    })
    csv_path = OUTPUT_DIR / f"web_prices_{stamp}_run_{run_index}.csv"
    parquet_path = OUTPUT_DIR / f"web_nyiso_zone_j_{day_str}_{stamp}_run_{run_index}.parquet"
    price_df.to_csv(csv_path, index=False)
    price_df.to_parquet(parquet_path, index=False)
    return csv_path, parquet_path


def _load_street_segments():
    global _STREET_SEGMENTS
    if _STREET_SEGMENTS is not None:
        return _STREET_SEGMENTS
    gdf = gpd.read_parquet(EDGES_PARQUET)
    segments = []
    for geom in gdf.geometry:
        if geom is None:
            continue
        if geom.geom_type == "LineString":
            segments.append([(float(y), float(x)) for x, y in geom.coords])
        elif geom.geom_type == "MultiLineString":
            for line in geom.geoms:
                segments.append([(float(y), float(x)) for x, y in line.coords])
    _STREET_SEGMENTS = segments
    return segments


def _selection_map_html(inst, values):
    selected_ids = set(_selected_customer_ids({k: [v] for k, v in values.items()}))
    default_depot = inst["depot"]
    depot_lat = float(values.get("depot_lat", inst["depot"]["lat"]))
    depot_lon = float(values.get("depot_lon", inst["depot"]["lon"]))
    area = _manhattan_area()
    bounds_payload = _manhattan_bounds()
    polygon_payload = [[float(lat), float(lon)] for lon, lat in area.exterior.coords]
    payload = {
        "depot": {"lat": depot_lat, "lon": depot_lon},
        "defaultDepot": {"lat": float(default_depot["lat"]), "lon": float(default_depot["lon"])},
        "bounds": bounds_payload,
        "manhattanPolygon": polygon_payload,
        "customers": [
            {
                "id": customer["cust_id"],
                "lat": float(customer["lat"]),
                "lon": float(customer["lon"]),
                "selected": customer["cust_id"] in selected_ids,
            }
            for customer in inst["customers"]
        ],
    }
    payload_json = json.dumps(payload).replace("</script>", "<\\/script>")
    return f"""
      <section class="selector">
        <div class="selector-head">
          <h2>Depot And Customer Selector</h2>
          <p>Use the map to choose customers and place the depot. Street labels come from OpenStreetMap tiles.</p>
        </div>
        <link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
        <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
        <script id="selector-payload" type="application/json">{payload_json}</script>
        <div id="selection-map">
          <div class="selector-tools" aria-label="Map controls">
            <button type="button" id="selector-rotate-left" title="Rotate left">&#8634;</button>
            <button type="button" id="selector-rotate-reset" title="Reset rotation">N</button>
            <button type="button" id="selector-rotate-right" title="Rotate right">&#8635;</button>
          </div>
        </div>
        <div class="selector-bar">
          <span id="selected-count">{len(selected_ids)} customers selected</span>
          <span id="available-count">{len(inst["customers"])} customers available</span>
          <span>Depot: <strong id="depot-readout">{depot_lat:.6f}, {depot_lon:.6f}</strong></span>
          <span id="depot-warning"></span>
          <span id="selector-mode-readout">Modes: coordinates depot / random customers</span>
        </div>
      </section>
      <script>
        (() => {{
          try {{
          if (!window.maplibregl) {{
            throw new Error('Map library did not load. Check the browser network connection.');
          }}
          const payload = JSON.parse(document.getElementById('selector-payload').textContent);
          const host = document.getElementById('selection-map');
          const selectedInput = document.querySelector('input[name="selected_customer_ids"]');
          const depotLatInput = document.querySelector('input[name="depot_lat"]');
          const depotLonInput = document.querySelector('input[name="depot_lon"]');
          const depotModeInput = document.querySelector('select[name="depot_mode"]');
          const customerModeInput = document.querySelector('select[name="customer_selection"]');
          const countEl = document.getElementById('selected-count');
          const availableEl = document.getElementById('available-count');
          const depotReadout = document.getElementById('depot-readout');
          const depotWarning = document.getElementById('depot-warning');
          const modeReadout = document.getElementById('selector-mode-readout');
          const selected = new Set((selectedInput.value || '').split(',').map(x => x.trim()).filter(Boolean));
          let coordinateDepot = {{lat: Number(depotLatInput.value || payload.defaultDepot.lat), lon: Number(depotLonInput.value || payload.defaultDepot.lon)}};
          const bounds = new maplibregl.LngLatBounds(
            [payload.depot.lon, payload.depot.lat],
            [payload.depot.lon, payload.depot.lat]
          );
          payload.customers.forEach((customer) => bounds.extend([customer.lon, customer.lat]));

          const map = new maplibregl.Map({{
            container: host,
            style: {{
              version: 8,
              sources: {{
                osm: {{
                  type: 'raster',
                  tiles: ['https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png'],
                  tileSize: 256,
                  attribution: 'OpenStreetMap contributors'
                }}
              }},
              layers: [{{id: 'osm', type: 'raster', source: 'osm'}}]
            }},
            center: [payload.depot.lon, payload.depot.lat],
            zoom: 12.5,
            bearing: 0,
            pitch: 0,
            dragRotate: true,
            touchPitch: false
          }});
          map.touchZoomRotate.enableRotation();
          map.addControl(new maplibregl.NavigationControl({{visualizePitch: false}}), 'top-right');
          map.fitBounds(bounds, {{padding: 42, maxZoom: 14}});
          map.setMaxBounds([
            [payload.bounds.min_lon, payload.bounds.min_lat],
            [payload.bounds.max_lon, payload.bounds.max_lat]
          ]);

          function pointInPolygon(lat, lon, polygon) {{
            let inside = false;
            for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {{
              const yi = polygon[i][0], xi = polygon[i][1];
              const yj = polygon[j][0], xj = polygon[j][1];
              const intersect = ((xi > lon) !== (xj > lon)) &&
                (lat < (yj - yi) * (lon - xi) / ((xj - xi) || 1e-12) + yi);
              if (intersect) inside = !inside;
            }}
            return inside;
          }}

          function setDepotWarning(message) {{
            depotWarning.textContent = message || '';
            depotWarning.classList.toggle('warning', Boolean(message));
          }}

          function syncSelected() {{
            const ids = Array.from(selected).sort();
            selectedInput.value = ids.join(',');
            countEl.textContent = `${{ids.length}} customers selected`;
            availableEl.textContent = `${{payload.customers.length}} customers available`;
          }}

          function syncDepot(lat, lon) {{
            depotLatInput.value = lat.toFixed(6);
            depotLonInput.value = lon.toFixed(6);
            depotReadout.textContent = `${{lat.toFixed(6)}}, ${{lon.toFixed(6)}}`;
            setDepotWarning('');
          }}

          function syncModes() {{
            modeReadout.textContent = `Modes: ${{depotModeInput.value}} depot / ${{customerModeInput.value}} customers`;
          }}

          const depotEl = document.createElement('div');
          depotEl.className = 'depot-marker';
          depotEl.innerHTML = '<span>D</span>';
          const depotMarker = new maplibregl.Marker({{element: depotEl, anchor: 'center'}})
            .setLngLat([payload.depot.lon, payload.depot.lat])
            .setPopup(new maplibregl.Popup({{offset: 18}}).setText('Depot'))
            .addTo(map);

          function placeDepot(lat, lon) {{
            depotMarker.setLngLat([lon, lat]);
            syncDepot(lat, lon);
          }}

          placeDepot(payload.depot.lat, payload.depot.lon);

          payload.customers.forEach((customer) => {{
            const el = document.createElement('button');
            el.type = 'button';
            el.className = 'customer-marker';
            el.title = customer.id;
            el.innerHTML = `<span class="marker-dot"></span><span class="marker-label">${{customer.id}}</span>`;
            function style() {{
              const active = selected.has(customer.id);
              el.classList.toggle('selected', active);
            }}
            style();
            el.addEventListener('click', (ev) => {{
              ev.stopPropagation();
              if (customerModeInput.value !== 'manual') return;
              if (selected.has(customer.id)) selected.delete(customer.id);
              else selected.add(customer.id);
              style();
              syncSelected();
            }});
            customerModeInput.addEventListener('change', style);
            new maplibregl.Marker({{element: el, anchor: 'center'}})
              .setLngLat([customer.lon, customer.lat])
              .setPopup(new maplibregl.Popup({{offset: 18}}).setText(customer.id))
              .addTo(map);
          }});

          map.on('click', (ev) => {{
            if (depotModeInput.value !== 'map') return;
            if (!pointInPolygon(ev.lngLat.lat, ev.lngLat.lng, payload.manhattanPolygon)) {{
              setDepotWarning('Depot must be inside Manhattan.');
              return;
            }}
            placeDepot(ev.lngLat.lat, ev.lngLat.lng);
          }});

          document.getElementById('selector-rotate-left').addEventListener('click', () => map.rotateTo(map.getBearing() - 20, {{duration: 180}}));
          document.getElementById('selector-rotate-right').addEventListener('click', () => map.rotateTo(map.getBearing() + 20, {{duration: 180}}));
          document.getElementById('selector-rotate-reset').addEventListener('click', () => map.rotateTo(0, {{duration: 180}}));

          depotModeInput.addEventListener('change', () => {{
            if (depotModeInput.value === 'coordinates') {{
              const lat = coordinateDepot.lat;
              const lon = coordinateDepot.lon;
              if (pointInPolygon(lat, lon, payload.manhattanPolygon)) {{
                placeDepot(lat, lon);
              }} else {{
                coordinateDepot = {{lat: payload.defaultDepot.lat, lon: payload.defaultDepot.lon}};
                placeDepot(payload.defaultDepot.lat, payload.defaultDepot.lon);
                setDepotWarning('Coordinates reset to the default Manhattan depot.');
              }}
            }} else {{
              coordinateDepot = {{lat: Number(depotLatInput.value || payload.defaultDepot.lat), lon: Number(depotLonInput.value || payload.defaultDepot.lon)}};
            }}
            syncModes();
          }});
          depotLatInput.addEventListener('change', () => {{
            if (depotModeInput.value !== 'coordinates') return;
            const lat = Number(depotLatInput.value);
            const lon = Number(depotLonInput.value);
            if (Number.isFinite(lat) && Number.isFinite(lon) && pointInPolygon(lat, lon, payload.manhattanPolygon)) {{
              coordinateDepot = {{lat, lon}};
              placeDepot(lat, lon);
            }} else {{
              coordinateDepot = {{lat: payload.defaultDepot.lat, lon: payload.defaultDepot.lon}};
              placeDepot(payload.defaultDepot.lat, payload.defaultDepot.lon);
              setDepotWarning('Coordinates reset to the default Manhattan depot.');
            }}
          }});
          depotLonInput.addEventListener('change', () => {{
            if (depotModeInput.value !== 'coordinates') return;
            const lat = Number(depotLatInput.value);
            const lon = Number(depotLonInput.value);
            if (Number.isFinite(lat) && Number.isFinite(lon) && pointInPolygon(lat, lon, payload.manhattanPolygon)) {{
              coordinateDepot = {{lat, lon}};
              placeDepot(lat, lon);
            }} else {{
              coordinateDepot = {{lat: payload.defaultDepot.lat, lon: payload.defaultDepot.lon}};
              placeDepot(payload.defaultDepot.lat, payload.defaultDepot.lon);
              setDepotWarning('Coordinates reset to the default Manhattan depot.');
            }}
          }});
          customerModeInput.addEventListener('change', syncModes);
          syncSelected();
          syncModes();
          }} catch (err) {{
            host.innerHTML = '<div style="padding:16px;color:#9f1d1d;font:14px system-ui">Selector failed to load: ' + err.message + '</div>';
          }}
        }})();
      </script>
    """


def _apply_day_prices_and_chargers(inst, form):
    day_str = _field(form, "day", inst.get("meta", {}).get("date", "2025-07-15"))
    price_source = _field(form, "price_source", "synthetic")
    optimizer_mode = _field(form, "optimizer_mode", "evrptw_greedy")
    flat_price = _float_field(form, "flat_price_usd_per_kwh", 0.30, 0.0)
    prices, price_source_label = _prices_for_day(day_str, price_source, flat_price)

    inst.setdefault("meta", {})["date"] = day_str
    inst["meta"]["price_source"] = price_source_label
    inst["prices_hourly"] = prices

    min_power = _float_field(form, "charger_min_power_kw", 0.0, 0.0)
    charger_limit = _int_field(form, "charger_limit", len(inst["chargers"]), 0, max(1, len(inst["chargers"])))
    use_break = _checked(form, "enable_break", False)
    break_billable = _checked(form, "break_billable", False)
    break_start_min = _time_field(form, "break_start_time", 12 * 60)
    break_end_min = _time_field(form, "break_end_time", 13 * 60)
    if break_end_min < break_start_min:
        break_start_min, break_end_min = break_end_min, break_start_min
    inst["chargers"] = _filter_and_rank_chargers(inst["chargers"], min_power, charger_limit)

    return {
        "day": day_str,
        "price_source": price_source_label,
        "price_source_key": price_source,
        "optimizer_mode": optimizer_mode,
        "optimizer_label": _optimizer_label(optimizer_mode),
        "avg_price": sum(prices) / len(prices),
        "min_price": min(prices),
        "max_price": max(prices),
        "charger_count": len(inst["chargers"]),
        "charger_min_power_kw": min_power,
        "available_customer_count": len(inst["customers"]),
        "use_break": use_break,
        "break_billable": break_billable,
        "break_start_min": break_start_min,
        "break_end_min": break_end_min,
    }


def _optimizer_label(mode):
    labels = {
        "nearest_neighbor": "Baseline 1: nearest-neighbor + charging insertion",
        "vrptw_ortools": "Baseline 2: OR-Tools VRPTW without EV constraints",
        "evrptw_greedy": "Main: EVRPTW charging-aware route order",
    }
    return labels.get(mode, labels["evrptw_greedy"])


def _timeline_stop_ids(timeline):
    if not timeline:
        return []
    stops = [timeline[0][0]]
    for row in timeline[1:]:
        loc_id = row[0]
        if loc_id != stops[-1]:
            stops.append(loc_id)
    return stops


def _route_events(timeline, charge_sessions, depot_id):
    charge_by_station = {}
    for charge in charge_sessions:
        charge_by_station.setdefault(charge["station_id"], []).append(charge)
    charge_offsets = {station_id: 0 for station_id in charge_by_station}
    events = []
    last_loc = None
    for row in timeline:
        loc_id = row[0]
        if loc_id == last_loc:
            continue
        if loc_id.startswith("CH") or loc_id == depot_id:
            sessions = charge_by_station.get(loc_id, [])
            offset = charge_offsets.get(loc_id, 0)
            if offset < len(sessions) and int(sessions[offset]["start_min"]) >= int(row[1]):
                events.append({"loc_id": loc_id, "event": "recharge", "charge": sessions[offset]})
                charge_offsets[loc_id] = offset + 1
            else:
                events.append({"loc_id": loc_id, "event": "depot" if loc_id == depot_id else "stop", "charge": None})
        else:
            events.append({"loc_id": loc_id, "event": "delivery", "charge": None})
        last_loc = loc_id
    return events


def _vehicle_summary(inst, vehicle_id, route, plan, run_config):
    names = _location_names(inst)
    details = _location_details(inst)
    summary = summarize_timeline(plan["timeline"], depot_id=inst["depot"]["id"])
    charges = grouped_charges(plan["timeline"], depot_id=inst["depot"]["id"])
    drives = summary["drives"]
    charge_energy = sum(float(c["energy_kwh"]) for c in charges)
    charge_cost = sum(float(c["cost_usd"]) for c in charges)
    drive_energy = sum(float(d["energy_kwh"]) for d in drives)
    drive_energy_cost = sum(
        float(d["energy_kwh"]) * _price_at_minute(inst, d["depart_min"])
        for d in drives
    )

    route_details = []
    actual_route = _route_events(plan["timeline"], charges, inst["depot"]["id"])
    if not actual_route:
        actual_route = [{"loc_id": loc_id, "event": "stop", "charge": None} for loc_id in route]
    for order, event in enumerate(actual_route, start=1):
        loc_id = event["loc_id"]
        loc = details.get(loc_id, {"id": loc_id, "name": names.get(loc_id, loc_id), "lat": None, "lon": None, "kind": "Stop"})
        event_type = event["event"]
        action = "Delivery"
        if event_type == "recharge":
            action = "Recharge"
        elif loc_id == inst["depot"]["id"]:
            action = "Depot"
        charge = event.get("charge")
        note = ""
        if charge:
            note = f"{_format_minutes(charge['start_min'])}-{_format_minutes(charge['end_min'])}; {charge['energy_kwh']:.2f} kWh; ${charge['cost_usd']:.2f}"
        route_details.append({
            "order": order,
            "id": loc_id,
            "label": _format_location(loc_id, names),
            "name": loc["name"],
            "kind": loc["kind"],
            "action": action,
            "note": note,
            "lat": loc["lat"],
            "lon": loc["lon"],
        })

    start_min = int(plan["timeline"][0][1]) if plan["timeline"] else int(inst["depot"]["start_min"])
    end_min = int(plan["end_time"])
    elapsed_min = max(0, end_min - start_min)
    break_overlap = 0
    if run_config.get("use_break") and not run_config.get("break_billable"):
        break_overlap = _overlap_minutes(
            start_min,
            end_min,
            int(run_config["break_start_min"]),
            int(run_config["break_end_min"]),
        )

    completion_reason = plan.get("completion_reason", "completed")
    if plan.get("completed", True):
        status_text = "Completed route"
    elif completion_reason == "time":
        status_text = "Did not complete route in the given time"
    elif completion_reason == "energy":
        status_text = "Did not complete route with the available battery and chargers"
    else:
        status_text = "Did not complete route"

    return {
        "vehicle_id": vehicle_id,
        "route": route_details,
        "drive_energy_kwh": drive_energy,
        "drive_energy_cost_usd": drive_energy_cost,
        "charge_energy_kwh": charge_energy,
        "charge_cost_usd": charge_cost,
        "total_energy_cost": drive_energy_cost + charge_cost,
        "end_soc": float(plan["end_soc"]),
        "end_time": int(plan["end_time"]),
        "elapsed_min": elapsed_min,
        "break_overlap_min": break_overlap,
        "billable_min": max(0, elapsed_min - break_overlap),
        "completed": bool(plan.get("completed", True)),
        "completion_reason": completion_reason,
        "status_text": status_text,
        "remaining_route_ids": list(plan.get("remaining_route_ids", [])),
        "charges": [
            {
                "station": _format_location(charge["station_id"], names),
                "start": int(charge["start_min"]),
                "end": int(charge["end_min"]),
                "energy_kwh": float(charge["energy_kwh"]),
                "cost_usd": float(charge["cost_usd"]),
            }
            for charge in charges
        ],
    }


def _make_instance(base_inst, form):
    inst = copy.deepcopy(base_inst)
    available_customer_count = _int_field(form, "available_customer_count", 150, 1, 1000)
    inst["customers"] = _generate_customer_pool(available_customer_count)
    run_config = _apply_day_prices_and_chargers(inst, form)
    inst["depot"]["lat"] = _float_field(form, "depot_lat", inst["depot"]["lat"])
    inst["depot"]["lon"] = _float_field(form, "depot_lon", inst["depot"]["lon"])
    inst["depot"]["start_min"] = _time_field(form, "start_time", inst["depot"]["start_min"])
    inst["depot"]["end_min"] = _time_field(form, "end_time", inst["depot"]["end_min"])
    if inst["depot"]["end_min"] < inst["depot"]["start_min"]:
        inst["depot"]["end_min"] = inst["depot"]["start_min"]

    vehicle_count = _int_field(form, "vehicle_count", 2, 1, 12)
    template = dict(inst["vehicles"][0])
    template["battery_kwh"] = _float_field(form, "battery_kwh", template["battery_kwh"], 1.0)
    template["initial_soc_pct"] = _float_field(form, "initial_soc_pct", 1.0, 0.0, 1.0)
    template["reserve_kwh"] = _float_field(form, "reserve_kwh", 0.0, 0.0)
    template["cons_kwh_per_km"] = _float_field(form, "cons_kwh_per_km", template["cons_kwh_per_km"], 0.01)
    template["energy_model"] = {
        "enabled": _checked(form, "energy_model_enabled", False),
        "payload_kg": _float_field(form, "payload_kg", 0.0, 0.0),
        "payload_penalty_per_100kg": 0.015,
        "stop_density_per_km": _float_field(form, "stop_density_per_km", 2.0, 0.0),
        "stop_go_penalty_per_stop": _float_field(form, "stop_go_penalty_per_stop", 0.008, 0.0),
        "ambient_temp_c": _float_field(form, "ambient_temp_c", 20.0),
        "comfort_temp_c": 20.0,
        "hvac_penalty_per_deg_c": _float_field(form, "hvac_penalty_per_deg_c", 0.006, 0.0),
        "speed_reference_kmph": 30.0,
        "speed_penalty_factor": _float_field(form, "speed_penalty_factor", 0.35, 0.0),
        "regen_credit": _float_field(form, "regen_credit", 0.08, 0.0, 0.18),
        "max_regen_credit": 0.18,
        "battery_degradation_pct": _float_field(form, "battery_degradation_pct", 0.0, 0.0, 0.95),
    }
    run_config["energy_model"] = dict(template["energy_model"])
    template["cap_kg"] = _float_field(form, "cap_kg", template["cap_kg"], 0.0)
    template["depot"] = inst["depot"]["id"]

    inst["vehicles"] = []
    veh_specs = {}
    for idx in range(vehicle_count):
        vehicle = dict(template)
        vehicle["id"] = f"V{idx + 1}"
        inst["vehicles"].append(vehicle)
        veh_specs[vehicle["id"]] = vehicle

    return inst, veh_specs, run_config


def _run_plans(form):
    base_inst = _load_base_instance()
    inst, veh_specs, run_config = _make_instance(base_inst, form)

    run_count = _int_field(form, "runs", 1, 1, 10)
    vehicle_count = len(inst["vehicles"])
    customers_per_vehicle = _int_field(form, "customers_per_vehicle", 3, 1, max(1, len(inst["customers"])))
    dt = _int_field(form, "dt", 10, 1, 120)
    horizon_pad_min = _int_field(form, "horizon_pad_min", 240, 0, 24 * 60)
    allow_depot_charging = _checked(form, "allow_depot_charging", True)
    depot_power_kw = _float_field(form, "depot_power_kw", 11.0, 0.0)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    results = []

    for run_index in range(run_count):
        routes = _build_routes(
            inst,
            vehicle_count,
            customers_per_vehicle,
            run_index,
            form,
            vehicle_specs=veh_specs,
            allow_depot_charging=allow_depot_charging,
        )
        inst_path = OUTPUT_DIR / f"web_instance_{stamp}_run_{run_index + 1}.json"
        json.dump(inst, open(inst_path, "w"))
        prices_path, prices_parquet_path = _write_daily_price_files(
            stamp,
            run_index + 1,
            run_config["day"],
            inst["prices_hourly"],
            run_config["price_source"],
        )

        plans, sessions = plan_day(
            inst_path=str(inst_path),
            routes_by_vehicle=routes,
            veh_specs=veh_specs,
            dt=dt,
            horizon_pad_min=horizon_pad_min,
            allow_depot_charging=allow_depot_charging,
            depot_power_kw=depot_power_kw,
        )

        overview_path = OUTPUT_DIR / f"web_overview_{stamp}_run_{run_index + 1}.html"
        quick_map(inst_path, overview_path)
        maps = [{"label": "Overview", "path": overview_path.name}]

        for vehicle_id, plan in plans.items():
            map_path = OUTPUT_DIR / f"web_plan_{stamp}_run_{run_index + 1}_{vehicle_id}.html"
            overlay_plan(str(inst_path), plan, out_html=map_path)
            maps.append({"label": vehicle_id, "path": map_path.name})

        vehicle_summaries = [
            _vehicle_summary(inst, vehicle_id, routes[vehicle_id], plans[vehicle_id], run_config)
            for vehicle_id in routes
        ]

        results.append({
            "run": run_index + 1,
            "routes": routes,
            "plans": plans,
            "sessions": sessions,
            "maps": maps,
            "vehicle_summaries": vehicle_summaries,
            "run_config": run_config,
            "prices_path": prices_path.name,
            "prices_parquet_path": prices_parquet_path.name,
            "instance_path": inst_path.name,
        })

    return results


def _friendly_error_message(exc):
    message = str(exc).strip()
    if isinstance(exc, RuntimeError) and "No energy-feasible path found" in message:
        return (
            "No feasible route was found for the current battery, starting charge, selected "
            "customers, and charging options. Reduce the route, increase available energy, "
            "or make more chargers available."
        )
    if message:
        return f"Planner run failed: {message}"
    return "Planner run failed. Check the selected settings and try again."


def _page(form=None, results=None, error=None):
    values = _default_form()
    if form:
        for key, vals in form.items():
            values[key] = vals[0]
        if "allow_depot_charging" not in form:
            values.pop("allow_depot_charging", None)
        if "enable_break" not in form:
            values.pop("enable_break", None)
        if "break_billable" not in form:
            values.pop("break_billable", None)

    def val(name):
        return html.escape(str(values.get(name, "")))

    def selected(name, value):
        return "selected" if values.get(name) == value else ""

    checked = "checked" if values.get("allow_depot_charging") == "on" else ""
    break_checked = "checked" if values.get("enable_break") == "on" else ""
    break_billable_checked = "checked" if values.get("break_billable") == "on" else ""
    energy_checked = "checked" if values.get("energy_model_enabled") == "on" else ""
    selector_inst = copy.deepcopy(_load_base_instance())
    selector_inst["customers"] = _generate_customer_pool(_int_field({k: [v] for k, v in values.items()}, "available_customer_count", 150, 1, 1000))
    selection_map = _selection_map_html(selector_inst, values)
    result_html = ""
    if error:
        result_html = f"<section class='error'><h2>Error</h2><pre>{html.escape(error)}</pre></section>"
    elif results:
        chunks = []
        for result in results:
            map_tabs = "".join(
                f"<a href='/outputs/{html.escape(m['path'])}' target='_blank'>{html.escape(m['label'])}</a>"
                for m in result["maps"]
            )
            first_map = result["maps"][0]["path"]
            routes = html.escape(json.dumps(result["routes"], indent=2))
            config = result["run_config"]
            energy_model = config.get("energy_model", {})
            energy_label = "Realistic" if energy_model.get("enabled") else "Simple"
            config_html = f"""
                <div class="run-config">
                  <span>Day <strong>{html.escape(config['day'])}</strong></span>
                  <span>Price source <strong>{html.escape(config['price_source'])}</strong></span>
                  <span>Optimizer <strong>{html.escape(config.get('optimizer_label', _optimizer_label('evrptw_greedy')))}</strong></span>
                  <span>Energy model <strong>{energy_label}</strong></span>
                  <span>Avg price <strong>${config['avg_price']:.3f}/kWh</strong></span>
                  <span>Price range <strong>${config['min_price']:.3f}-${config['max_price']:.3f}/kWh</strong></span>
                  <span>Chargers available <strong>{config['charger_count']}</strong></span>
                  <span>Break <strong>{html.escape(_format_minutes(config['break_start_min']) + '-' + _format_minutes(config['break_end_min']) if config['use_break'] else 'Off')}</strong></span>
                  <span>Break billing <strong>{'Billable' if config['break_billable'] else 'Not billable'}</strong></span>
                  <span>Generated data <strong><a href="/outputs/{html.escape(result['prices_path'])}" target="_blank">CSV</a> / <a href="/outputs/{html.escape(result['prices_parquet_path'])}" target="_blank">Parquet</a></strong></span>
                </div>
            """
            vehicles = []
            for vehicle in result["vehicle_summaries"]:
                route_rows = "".join(
                    "<tr>"
                    f"<td>{stop['order']}</td>"
                    f"<td><strong>{html.escape(stop['action'])}</strong><br><span class='muted'>{html.escape(stop['id'])}</span></td>"
                    f"<td>{html.escape(stop['name'])}</td>"
                    f"<td>{html.escape(stop['note']) if stop['note'] else '-'}</td>"
                    f"<td>{stop['lat']:.6f}</td>"
                    f"<td>{stop['lon']:.6f}</td>"
                    "</tr>"
                    for stop in vehicle["route"]
                )
                if vehicle["charges"]:
                    charge_rows = "".join(
                        "<tr>"
                        f"<td>{html.escape(charge['station'])}</td>"
                        f"<td>{_format_minutes(charge['start'])}</td>"
                        f"<td>{_format_minutes(charge['end'])}</td>"
                        f"<td>{charge['energy_kwh']:.2f}</td>"
                        f"<td>${charge['cost_usd']:.2f}</td>"
                        "</tr>"
                        for charge in vehicle["charges"]
                    )
                    recharge_html = f"""
                        <table>
                          <thead>
                            <tr><th>Recharge stop</th><th>Start</th><th>End</th><th>kWh</th><th>Cost</th></tr>
                          </thead>
                          <tbody>{charge_rows}</tbody>
                        </table>
                    """
                else:
                    recharge_html = "<p class='muted'>No recharge stops.</p>"

                vehicles.append(f"""
                    <article class="vehicle">
                      <div class="vehicle-title">
                        <h3>{html.escape(vehicle['vehicle_id'])}</h3>
                        <div class="muted"><strong>{html.escape(vehicle['status_text'])}</strong></div>
                        <div class="metrics">
                          <span>Estimated energy cost <strong>${vehicle['total_energy_cost']:.2f}</strong></span>
                          <span>Driving energy value <strong>${vehicle['drive_energy_cost_usd']:.2f}</strong></span>
                          <span>Energy used <strong>{vehicle['drive_energy_kwh']:.2f} kWh</strong></span>
                          <span>Recharge cost <strong>${vehicle['charge_cost_usd']:.2f}</strong></span>
                          <span>Charged <strong>{vehicle['charge_energy_kwh']:.2f} kWh</strong></span>
                          <span>End SoC <strong>{vehicle['end_soc']:.2f} kWh</strong></span>
                          <span>End time <strong>{_format_minutes(vehicle['end_time'])}</strong></span>
                          <span>Elapsed time <strong>{vehicle['elapsed_min']} min</strong></span>
                          <span>Break excluded <strong>{vehicle['break_overlap_min']} min</strong></span>
                          <span>Billable time <strong>{vehicle['billable_min']} min</strong></span>
                        </div>
                      </div>
                      <div class="vehicle-grid">
                        <div>
                          <h4>Spot Order</h4>
                          <table>
                            <thead>
                              <tr><th>#</th><th>Action</th><th>Stop</th><th>Details</th><th>Lat</th><th>Lon</th></tr>
                            </thead>
                            <tbody>{route_rows}</tbody>
                          </table>
                        </div>
                        <div>
                          <h4>Recharge Details</h4>
                          {recharge_html}
                          {"<p class='muted'>Remaining planned stops: " + html.escape(", ".join(vehicle["remaining_route_ids"])) + "</p>" if vehicle["remaining_route_ids"] else ""}
                        </div>
                      </div>
                    </article>
                """)
            vehicle_html = "\n".join(vehicles)
            chunks.append(f"""
                <section class="result">
                    <div class="result-head">
                        <h2>Run {result['run']}</h2>
                        <div class="map-links">{map_tabs}</div>
                    </div>
                    {config_html}
                    <iframe src="/outputs/{html.escape(first_map)}" title="Run {result['run']} map"></iframe>
                    <div class="vehicles">{vehicle_html}</div>
                    <details>
                        <summary>Routes</summary>
                        <pre>{routes}</pre>
                    </details>
                </section>
            """)
        result_html = "\n".join(chunks)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EV Manhattan Planner</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d7dde5;
      --panel: #f7f9fb;
      --accent: #0f766e;
      --accent-dark: #0b5f59;
      --danger: #9f1d1d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #ffffff;
    }}
    header {{
      padding: 24px 32px 16px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, #f7fbfa, #ffffff);
    }}
    h1 {{ margin: 0 0 6px; font-size: 28px; font-weight: 720; letter-spacing: 0; }}
    p {{ margin: 0; color: var(--muted); }}
    main {{
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 24px;
      padding: 24px 32px 40px;
    }}
    form {{
      align-self: start;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 18px;
    }}
    fieldset {{
      margin: 0 0 18px;
      padding: 0;
      border: 0;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    legend {{
      grid-column: 1 / -1;
      margin-bottom: 2px;
      font-weight: 700;
    }}
    label {{ display: grid; gap: 5px; font-size: 13px; color: #344054; }}
    input, select {{
      width: 100%;
      min-height: 38px;
      padding: 8px 10px;
      border: 1px solid #c8d0db;
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }}
    .wide, .check {{ grid-column: 1 / -1; }}
    .check {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .check input {{ width: 18px; min-height: 18px; }}
    .inline-actions {{
      display: grid;
      gap: 6px;
    }}
    button {{
      width: 100%;
      min-height: 42px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      font-weight: 700;
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent-dark); }}
    .inline-actions button {{
      width: auto;
      justify-self: start;
      min-height: 34px;
      padding: 7px 10px;
      border: 1px solid #a8d6d1;
      background: #f0fbf9;
      color: var(--accent-dark);
    }}
    .inline-actions button:hover {{ background: #dff5f1; }}
    .result, .empty, .error {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
      margin-bottom: 18px;
    }}
    .result-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}
    h2 {{ margin: 0; font-size: 18px; }}
    .map-links {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .map-links a {{
      color: var(--accent-dark);
      font-weight: 700;
      text-decoration: none;
      border: 1px solid #a8d6d1;
      border-radius: 6px;
      padding: 6px 9px;
      background: #f0fbf9;
    }}
    .run-config {{
      display: grid;
      grid-template-columns: repeat(7, minmax(130px, 1fr));
      gap: 8px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
    }}
    .run-config span {{
      display: grid;
      gap: 3px;
      color: var(--muted);
      font-size: 12px;
    }}
    .run-config strong {{ color: var(--ink); font-size: 13px; }}
    iframe {{
      width: 100%;
      height: min(68vh, 760px);
      border: 0;
      display: block;
    }}
    details {{ padding: 12px 16px 16px; border-top: 1px solid var(--line); }}
    summary {{ cursor: pointer; font-weight: 700; }}
    pre {{
      overflow: auto;
      background: #f4f6f8;
      padding: 12px;
      border-radius: 6px;
    }}
    .empty, .error {{ padding: 18px; }}
    .error {{ border-color: #efb4b4; color: var(--danger); }}
    .vehicles {{
      display: grid;
      gap: 14px;
      padding: 16px;
      border-top: 1px solid var(--line);
      background: #fbfcfd;
    }}
    .vehicle {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 14px;
    }}
    .vehicle-title {{
      display: grid;
      gap: 10px;
      margin-bottom: 12px;
    }}
    h3, h4 {{ margin: 0; }}
    h3 {{ font-size: 17px; }}
    h4 {{ font-size: 14px; margin-bottom: 8px; color: #344054; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
      gap: 8px;
    }}
    .metrics span {{
      display: grid;
      gap: 3px;
      padding: 8px 10px;
      border: 1px solid #dbe4ed;
      border-radius: 6px;
      background: #f8fafc;
      color: var(--muted);
      font-size: 12px;
    }}
    .metrics strong {{ color: var(--ink); font-size: 15px; }}
    .vehicle-grid {{
      display: grid;
      grid-template-columns: minmax(220px, 0.85fr) minmax(320px, 1.15fr);
      gap: 18px;
    }}
    ol {{
      margin: 0;
      padding-left: 24px;
    }}
    li {{ margin: 4px 0; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      text-align: left;
      padding: 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    th {{
      color: #344054;
      background: #f4f6f8;
    }}
    .muted {{ color: var(--muted); }}
    .selector {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
      margin-bottom: 18px;
    }}
    .selector-head {{
      padding: 14px 16px 10px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
    }}
    #selection-map {{
      position: relative;
      width: 100%;
      height: min(52vh, 520px);
      min-height: 430px;
      background: #eef2f6;
    }}
    .selector-tools {{
      position: absolute;
      left: 12px;
      top: 12px;
      z-index: 5;
      display: grid;
      grid-template-columns: repeat(3, 32px);
      gap: 6px;
    }}
    .selector-tools button {{
      width: 32px;
      min-height: 32px;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      background: #ffffff;
      color: #1f2933;
      font-weight: 800;
      box-shadow: 0 1px 4px rgba(15, 23, 42, 0.18);
    }}
    .selector-tools button:hover {{ background: #f1f5f9; }}
    .customer-marker {{
      width: 18px;
      height: 18px;
      min-height: 18px;
      padding: 0;
      border: 0;
      background: transparent;
      position: relative;
      cursor: pointer;
    }}
    .marker-dot {{
      display: block;
      width: 14px;
      height: 14px;
      margin: 2px;
      border-radius: 50%;
      background: #ffffff;
      border: 2px solid #475569;
      box-shadow: 0 1px 4px rgba(15, 23, 42, 0.32);
    }}
    .customer-marker.selected .marker-dot {{
      width: 18px;
      height: 18px;
      margin: 0;
      background: #14b8a6;
      border-color: #0f766e;
    }}
    .marker-label {{
      display: none;
      position: absolute;
      left: 20px;
      top: -2px;
      white-space: nowrap;
      padding: 2px 5px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.95);
      border: 1px solid #cbd5e1;
      color: #1f2933;
      font-size: 11px;
      font-weight: 700;
      box-shadow: 0 1px 4px rgba(15, 23, 42, 0.18);
    }}
    .customer-marker:hover .marker-label,
    .customer-marker.selected .marker-label {{
      display: block;
    }}
    .depot-marker {{
      width: 28px;
      height: 28px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      background: #2563eb;
      color: #ffffff;
      border: 3px solid #ffffff;
      box-shadow: 0 2px 8px rgba(15, 23, 42, 0.36);
      font-size: 12px;
      font-weight: 800;
    }}
    .maplibregl-ctrl-top-right {{
      top: 8px;
      right: 8px;
    }}
    .selector-bar {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 16px 14px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
      flex-wrap: wrap;
    }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; padding: 18px; }}
      header {{ padding: 20px 18px 12px; }}
      .run-config {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
      .metrics {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
      .vehicle-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>EV Manhattan Planner</h1>
    <p>Change the depot, fleet, route batch, and solver settings, then generate browser maps.</p>
  </header>
  <main>
    <form method="post" action="/run">
      <fieldset>
        <legend>Customers</legend>
        <label>Customer selection
          <select name="customer_selection">
            <option value="random" {selected('customer_selection', 'random')}>Random customers</option>
            <option value="manual" {selected('customer_selection', 'manual')}>Choose on map</option>
          </select>
        </label>
        <label>Available customers <input id="available-customer-count" name="available_customer_count" type="number" min="1" max="1000" value="{val('available_customer_count')}"></label>
        <div class="wide inline-actions">
          <button type="button" id="refresh-customers-map">Refresh selector map</button>
          <span class="muted">Changing this value creates a new customer pool.</span>
        </div>
        <label class="wide">Selected customer IDs <input name="selected_customer_ids" type="text" value="{val('selected_customer_ids')}" placeholder="C000,C014,C021"></label>
      </fieldset>
      <fieldset>
        <legend>Day, Prices, Chargers</legend>
        <label>Day <input name="day" type="date" value="{val('day')}"></label>
        <label>Price data
          <select name="price_source">
            <option value="synthetic" {selected('price_source', 'synthetic')}>Use synthetic data</option>
            <option value="nyiso_dynamic" {selected('price_source', 'nyiso_dynamic')}>Use NYISO selected day</option>
            <option value="nyiso_local" {selected('price_source', 'nyiso_local')}>Use local July 2025 NYISO file</option>
            <option value="flat" {selected('price_source', 'flat')}>Use flat price</option>
          </select>
        </label>
        <label>Flat price $/kWh <input name="flat_price_usd_per_kwh" type="number" min="0" step="0.001" value="{val('flat_price_usd_per_kwh')}"></label>
        <label>Min charger kW <input name="charger_min_power_kw" type="number" min="0" step="0.1" value="{val('charger_min_power_kw')}"></label>
        <label class="wide">Max chargers available <input name="charger_limit" type="number" min="0" max="5000" value="{val('charger_limit')}"></label>
      </fieldset>
      <fieldset>
        <legend>Depot</legend>
        <label>Depot input
          <select name="depot_mode">
            <option value="coordinates" {selected('depot_mode', 'coordinates')}>Use coordinates</option>
            <option value="map" {selected('depot_mode', 'map')}>Choose on map</option>
          </select>
        </label>
        <label>Latitude <input name="depot_lat" type="number" step="0.000001" value="{val('depot_lat')}"></label>
        <label>Longitude <input name="depot_lon" type="number" step="0.000001" value="{val('depot_lon')}"></label>
        <label>Start time <input name="start_time" type="time" value="{val('start_time')}"></label>
        <label>End time <input name="end_time" type="time" value="{val('end_time')}"></label>
      </fieldset>
      <fieldset>
        <legend>Break</legend>
        <label class="check"><input name="enable_break" type="checkbox" {break_checked}> Enable break</label>
        <label class="check"><input name="break_billable" type="checkbox" {break_billable_checked}> Billable break</label>
        <label>Break start <input name="break_start_time" type="time" value="{val('break_start_time')}"></label>
        <label>Break end <input name="break_end_time" type="time" value="{val('break_end_time')}"></label>
      </fieldset>
      <fieldset>
        <legend>Fleet And Runs</legend>
        <label>Vehicles <input name="vehicle_count" type="number" min="1" max="12" value="{val('vehicle_count')}"></label>
        <label>Runs <input name="runs" type="number" min="1" max="10" value="{val('runs')}"></label>
        <label class="wide">Customers per vehicle <input name="customers_per_vehicle" type="number" min="1" value="{val('customers_per_vehicle')}"></label>
      </fieldset>
      <fieldset>
        <legend>Vehicle</legend>
        <label>Battery kWh <input name="battery_kwh" type="number" min="1" step="0.1" value="{val('battery_kwh')}"></label>
        <label>Initial SoC % <input name="initial_soc_pct" type="number" min="0" max="1" step="0.01" value="{val('initial_soc_pct')}"></label>
        <label>Reserve kWh <input name="reserve_kwh" type="number" min="0" step="0.1" value="{val('reserve_kwh')}"></label>
        <label>kWh per km <input name="cons_kwh_per_km" type="number" min="0.01" step="0.01" value="{val('cons_kwh_per_km')}"></label>
        <label class="wide">Capacity kg <input name="cap_kg" type="number" min="0" step="1" value="{val('cap_kg')}"></label>
      </fieldset>
      <fieldset>
        <legend>Energy Realism</legend>
        <label class="check wide"><input name="energy_model_enabled" type="checkbox" {energy_checked}> Use realistic energy modifiers</label>
        <label>Payload kg <input name="payload_kg" type="number" min="0" step="1" value="{val('payload_kg')}"></label>
        <label>Stops per km <input name="stop_density_per_km" type="number" min="0" step="0.1" value="{val('stop_density_per_km')}"></label>
        <label>Stop penalty <input name="stop_go_penalty_per_stop" type="number" min="0" step="0.001" value="{val('stop_go_penalty_per_stop')}"></label>
        <label>Ambient C <input name="ambient_temp_c" type="number" step="0.5" value="{val('ambient_temp_c')}"></label>
        <label>HVAC / deg <input name="hvac_penalty_per_deg_c" type="number" min="0" step="0.001" value="{val('hvac_penalty_per_deg_c')}"></label>
        <label>Speed penalty <input name="speed_penalty_factor" type="number" min="0" step="0.01" value="{val('speed_penalty_factor')}"></label>
        <label>Regen credit <input name="regen_credit" type="number" min="0" max="0.18" step="0.01" value="{val('regen_credit')}"></label>
        <label>Battery degradation <input name="battery_degradation_pct" type="number" min="0" max="0.95" step="0.01" value="{val('battery_degradation_pct')}"></label>
      </fieldset>
      <fieldset>
        <legend>Planner</legend>
        <label class="wide">Optimizer
          <select name="optimizer_mode">
            <option value="evrptw_greedy" {selected('optimizer_mode', 'evrptw_greedy')}>Main: EVRPTW charging-aware order</option>
            <option value="nearest_neighbor" {selected('optimizer_mode', 'nearest_neighbor')}>Baseline 1: nearest-neighbor + charging insertion</option>
            <option value="vrptw_ortools" {selected('optimizer_mode', 'vrptw_ortools')}>Baseline 2: OR-Tools VRPTW without EV</option>
          </select>
        </label>
        <label>Time step min <input name="dt" type="number" min="1" max="120" value="{val('dt')}"></label>
        <label>Horizon pad min <input name="horizon_pad_min" type="number" min="0" value="{val('horizon_pad_min')}"></label>
        <label>Depot power kW <input name="depot_power_kw" type="number" min="0" step="0.1" value="{val('depot_power_kw')}"></label>
        <label class="check"><input name="allow_depot_charging" type="checkbox" {checked}> Allow depot charging</label>
      </fieldset>
      <button type="submit">Run Planner</button>
    </form>
    <div>
      {selection_map}
      {result_html or "<section class='empty'><h2>No Run Yet</h2><p>Choose parameters and run the planner. Start with a small number of customers per vehicle; larger values can take a while.</p></section>"}
    </div>
  </main>
</body>
<script>
  (() => {{
    const refresh = document.getElementById('refresh-customers-map');
    const available = document.getElementById('available-customer-count');
    if (!refresh || !available) return;
    refresh.addEventListener('click', () => {{
      const url = new URL(window.location.href);
      const form = refresh.closest('form');
      const params = new URLSearchParams(new FormData(form));
      url.pathname = '/';
      url.search = params.toString();
      url.searchParams.set('available_customer_count', available.value || '150');
      url.searchParams.set('selected_customer_ids', '');
      window.location.href = url.toString();
    }});
  }})();
</script>
</html>"""


class PlannerHandler(BaseHTTPRequestHandler):
    def _send_html(self, body, status=200):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_file(self, path):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        content_type = "text/html; charset=utf-8"
        if path.suffix == ".csv":
            content_type = "text/csv; charset=utf-8"
        elif path.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        elif path.suffix == ".parquet":
            content_type = "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            form = parse_qs(parsed.query) if parsed.query else None
            self._send_html(_page(form=form))
            return
        if self.path.startswith("/outputs/"):
            name = unquote(self.path.removeprefix("/outputs/")).split("?", 1)[0]
            path = (OUTPUT_DIR / name).resolve()
            if OUTPUT_DIR.resolve() not in path.parents:
                self.send_error(403)
                return
            self._send_file(path)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/run":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        form = parse_qs(raw)
        try:
            results = _run_plans(form)
            self._send_html(_page(form=form, results=results))
        except Exception as exc:
            traceback.print_exc()
            self._send_html(_page(form=form, error=_friendly_error_message(exc)), status=400)


def main():
    parser = argparse.ArgumentParser(description="Local browser UI for EV Manhattan planning.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), PlannerHandler)
    print(f"EV Manhattan planner UI: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
