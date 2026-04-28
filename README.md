# EV Manhattan

EV Manhattan is a local browser tool and Python project for testing electric delivery vehicle routes in Manhattan. It combines a Manhattan road graph, charging stations, hourly electricity prices, battery constraints, depot settings, customer selection, and browser maps.

The current workflow is interactive: start the web app, choose the depot/customers/vehicle settings in the browser, run the planner, and inspect the route, recharge stops, cost, and map output.

## What It Does

- Runs EV delivery route experiments for Manhattan.
- Lets you choose customers randomly or from an interactive map.
- Lets you use depot coordinates or choose the depot on the map.
- Supports multiple vehicles and repeated runs.
- Enforces service time, customer time windows, vehicle capacity, driver shift limits, reserve SoC, charger availability, depot return, and partial charging.
- Supports synthetic, local NYISO, or flat electricity prices.
- Can fetch NYISO Zone J prices dynamically for the selected day when public NYISO data is available.
- Generates per-run price files and routing instances.
- Tracks battery state of charge, driving energy, charging energy, charging cost, and estimated energy cost.
- Uses a cached time-dependent travel-time matrix for route legs.
- Inserts real charging stops when the vehicle needs to recharge.
- Stops a vehicle only when the configured time window is exceeded.
- Shows completed stops, recharge stops, remaining stops, and route maps in the browser.

## Current Route Optimizers

The browser planner supports multiple route-ordering modes. The selected route order is passed to the EV feasibility layer, which inserts charging stops and stops only when the time window is exceeded or no reachable charger exists.

Optimizer modes:

- `Main: EVRPTW charging-aware order`: chooses customers while considering battery, reserve, and distance to reachable charging sites.
- `Baseline 1: nearest-neighbor + charging insertion`: orders customers geographically, then inserts chargers afterward.
- `Baseline 2: OR-Tools VRPTW without EV`: uses OR-Tools for capacity/time-window route ordering, then applies EV charging feasibility afterward.

EV feasibility loop:

1. Start at the depot with `Battery kWh * Initial SoC %`.
2. For the next planned stop, calculate road-network distance using the Manhattan OSM graph.
3. Convert distance to driving energy: `kWh = distance_km * kWh per km`.
4. Estimate travel time from distance, base speed, traffic multiplier, and `dt`.
5. Check whether the vehicle can reach the next stop and still have enough energy to reach a real charger afterward.
6. If yes, drive to the stop, wait until the customer time window opens if needed, and apply service time.
7. If no, drive to a reachable compatible charger, wait for queue time if configured, partially charge to the energy needed for the next feasible leg, then retry the same stop.
8. Stop when a hard constraint is violated: route time/shift, customer time window, capacity, reserve SoC, or charger availability.

Recharge stops appear as route steps, for example:

```text
#6 Recharge CH353541 09:40-10:20; 2.13 kWh; $0.53
```

The map uses the same order: delivery stops are numbered, recharge stops are numbered green markers, and recharge tooltips show station, time, kWh, and cost.

## Time, Energy, And Cost Calculations

Each route timeline row is stored as:

```python
(location_id, minute, soc_kwh, cumulative_charging_cost_usd)
```

Driving:

- Road distance comes from shortest paths on `data/processed/manhattan_drive.graphml`.
- Origins and destinations are snapped to the nearest OSM graph nodes.
- Shortest paths minimize OSM edge `length` in meters on the directed road graph, with an undirected fallback when a directed path is unavailable.
- Simple driving energy is `distance_km * cons_kwh_per_km`.
- Optional realistic energy modifiers add payload, stop-and-go, temperature/HVAC, speed, regenerative braking, and usable battery degradation effects.
- SoC after a drive is `previous_soc_kwh - driving_energy_kwh`.
- Travel time comes from a cached `T[i, j, t]` matrix, rounded up to the `dt` grid.
- Speed starts at `30 km/h` and is reduced by the traffic multiplier in `src/sim/traffic.py`.

Travel-time matrix:

```text
T[i, j, t] = shortest_path_travel_time_minutes(i, j, departing_at_t)
```

The planner caches shortest-path distance once per origin/destination pair, then stores travel time for each departure bucket in the day. Cache files are written under `data/cache/` and are ignored by Git.

Shortest-path formula:

```text
P* = argmin_P sum(length_e for e in P)
distance_km = sum(length_e for e in P*) / 1000
```

For parallel OSM edges between the same two nodes, the planner uses the shortest edge length for that hop.

Charging:

- Charging gain per step is `charger_power_kw * (dt / 60)`.
- Charging is capped at the vehicle battery capacity.
- Charging cost per step is `kWh_added * hourly_price_usd_per_kwh`.

Reporting:

- `Elapsed time` is `end_time - start_time`.
- `Billable time` is elapsed time minus a non-billable break overlap.
- `Energy used` is total kWh consumed while driving.
- `Charged` is total kWh added at depot or public chargers.
- `Recharge cost` is actual cost paid during charging.
- `Driving energy value` estimates the value of consumed driving energy using the hourly price at drive departure time.
- `Estimated energy cost` is `Driving energy value + Recharge cost`.

Realistic energy model:

```text
base_kwh = distance_km * cons_kwh_per_km
modifier = 1
  + payload_kg / 100 * payload_penalty_per_100kg
  + distance_km * stop_density_per_km * stop_go_penalty_per_stop
  + abs(ambient_temp_c - comfort_temp_c) * hvac_penalty_per_deg_c
  + abs(speed_kmph - speed_reference_kmph) / speed_reference_kmph * speed_penalty_factor

driving_kwh = base_kwh * modifier * (1 - regen_credit)
usable_battery_kwh = battery_kwh * (1 - battery_degradation_pct)
```

## Quick Start

From the project root:

```bash
cd ev-manhattan
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/run/web_app.py --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

If a server is already running, stop it first:

```bash
pgrep -af "src/run/web_app.py"
kill <pid>
```

## Browser UI

The web app is in:

```text
src/run/web_app.py
```

Main controls:

- `Customer selection`: random customers or choose customers on the map.
- `Available customers`: size of the customer pool shown in the selector.
- `Depot input`: typed coordinates or choose on map.
- `Optimizer`: EV-aware main model or baseline comparison modes.
- `Day`: selected planning day.
- `Price data`: synthetic, NYISO selected day, local July 2025 NYISO file, or flat price.
- `Min charger kW` and `Max chargers available`: charger filtering.
- `Enable break` and `Billable break`: billing/reporting controls.
- `Battery kWh`, `Initial SoC %`, `Reserve kWh`, `kWh per km`: vehicle energy controls.
- `Service min/customer`, `Shift limit min`, `Queue wait min`, `Required plug type`, `Mandatory return to depot`: hard constraint controls.
- `Start time` and `End time`: route operating window.

After a run, the page shows overview and per-vehicle maps, route status, stop order, recharge details, energy used, driving energy value, recharge cost, total estimated energy cost, and generated data links.

## Data

Expected processed data lives in:

```text
data/processed/
```

Important files:

- `manhattan_drive.graphml`: Manhattan road graph.
- `nodes.parquet` and `edges.parquet`: road network data used by the selector map.
- `chargers.parquet`: charging station data.
- `nyiso_zone_j_2025_07.parquet`: local July 2025 NYISO Zone J price data.
- `nyiso_zone_j_YYYY_MM_DD_dam.parquet`: cached selected-day NYISO day-ahead prices.
- `nyiso_zone_j_YYYY_MM_DD_rt.parquet`: cached selected-day NYISO real-time prices.
- `instance_2025-07-15.json`: base routing instance.

Generated run outputs are written to:

```text
data/outputs/
```

Each browser run writes files such as:

- `web_instance_...json`
- `web_prices_...csv`
- `web_nyiso_zone_j_...parquet`
- `web_overview_...html`
- `web_plan_..._V1.html`

## Useful Commands

Run the browser app:

```bash
.venv/bin/python src/run/web_app.py --host 127.0.0.1 --port 8000
```

Run the script planner:

```bash
.venv/bin/python src/run/run_plan_day.py
```

Compile key files:

```bash
.venv/bin/python -m py_compile src/run/web_app.py src/solve/rcsp_one_vehicle.py src/viz/overlay_plan.py
```

Regenerate the road graph:

```bash
.venv/bin/python src/build_graph.py
```

Regenerate the base instance:

```bash
.venv/bin/python src/build_instance.py
```

## Documentation

More detailed docs are in:

- [docs/setup.md](docs/setup.md)
- [docs/browser-ui.md](docs/browser-ui.md)
- [docs/data.md](docs/data.md)
- [docs/planner.md](docs/planner.md)
- [docs/testing.md](docs/testing.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)

## Repository Layout

```text
src/
  run/
    web_app.py          Browser UI
    plan_day.py         Multi-vehicle run wrapper
    run_plan_day.py     Script entry point
  solve/
    rcsp_one_vehicle.py Charging-aware vehicle planner
    vrptw_baseline.py   Baseline routing
  viz/
    overlay_plan.py     Per-vehicle Folium route maps
    map.py              Base map generation
  eval/
    summarize.py        Timeline, drive, and recharge summaries
  sim/
    traffic.py          Time-dependent speed multiplier
    queues.py           Charger queue scaffold
  metrics/
    travel.py           Network travel metrics
    energy.py           Energy helpers
    time_dependent.py   Cached time-dependent travel-time matrix
  build_graph.py        Road graph builder
  build_instance.py     Base instance builder
  fetch_ocm.py          Open Charge Map fetcher
  fetch_nyiso.py        NYISO price fetcher
```

## Notes

- The project is designed for local experimentation, not production dispatch.
- Browser runs can generate many files in `data/outputs/`.
- Very low battery settings may still be limited by the operating time window because charging consumes time.
- Break settings currently affect reporting and billing, not route feasibility.
