# Planner

The main planner is implemented in:

```text
src/solve/rcsp_one_vehicle.py
```

The multi-vehicle wrapper is:

```text
src/run/plan_day.py
```

The browser app calls these through:

```text
src/run/web_app.py
```

## Route Flow

For each vehicle, the planner receives a fixed stop order:

```text
DEPOT1 -> C003 -> C011 -> ... -> DEPOT1
```

The browser workflow builds a customer pool from either random customers or selected map customers, then chooses a route-ordering optimizer.

Implemented route-ordering modes:

- `nearest_neighbor`: Baseline 1. Orders customers by nearest-neighbor and then inserts charging stops during EV feasibility.
- `vrptw_ortools`: Baseline 2. Uses OR-Tools VRPTW with time windows and vehicle capacity, but no battery constraints. EV feasibility is checked afterward.
- `evrptw_greedy`: Main browser model. Builds route order with battery, reserve, and charger access in the scoring function, then inserts exact charging stops during EV feasibility.

The main mode is a charging-aware EVRPTW heuristic. It is not yet a full global EVRPTW mixed-integer optimizer or ALNS metaheuristic, but charging access now influences route order before the EV feasibility pass.

Advanced solver scaffold:

- `rcsp_leg` remains available for label-setting, time-expanded single-leg planning.
- `plan_route_with_charging` is the active browser feasibility layer because it is much faster for interactive runs.
- A future ALNS layer can use the current route optimizer module as the destroy/repair interface and call the EV feasibility layer to evaluate candidate route sets.

## Active EV Feasibility Algorithm

The active browser feasibility planner is a charging-aware greedy heuristic in `plan_route_with_charging`.

At each step it tries to advance to the next planned stop. It can insert public charger stops or depot charging, but it does not reorder the remaining customer list.

For each next planned stop:

1. Convert the current location and target stop to lat/lon records.
2. Estimate the closest real charge site from the target stop.
3. Compute how much energy should remain after arrival:
   - at least `Reserve kWh`,
   - and enough to reach the nearest charger after the customer.
4. Compute the road-network drive leg from current location to target stop.
5. If the vehicle can drive there and still satisfy the post-arrival energy requirement, append the target stop.
6. If not, find reachable charger candidates from the current location.
7. Drive to the first reachable charger whose road-network leg is feasible.
8. Charge at that station up to battery capacity, or until the time window ends.
9. Retry the same customer.

The route loop continues until all planned stops are reached or the route would exceed the configured end time.

## Charger Selection

Charging candidates are selected in two layers:

- Browser data filtering keeps chargers inside the Manhattan road-network hull.
- Route-time charger lookup uses fast geographic distance with a road-distance safety factor to rank likely candidates.
- Actual driving to the chosen charger uses the Manhattan road graph.

This keeps the app responsive while still calculating actual drive legs on the road network.

## Road Distance And Travel Time

Drive legs use `data/processed/manhattan_drive.graphml`.

The active planner uses a cached time-dependent travel-time matrix:

```text
T[i, j, t] = shortest_path_travel_time_minutes(i, j, departing_at_t)
```

For each origin/destination pair, the first request computes shortest-path road distance on the Manhattan graph. The cache then stores travel time for every departure bucket in the day using the configured `dt`.

For each pair:

1. Snap origin and destination lat/lon to the nearest graph nodes.
2. Compute shortest path distance using edge `length`.
3. Fall back to the undirected graph if the directed graph has no path.
4. Convert meters to kilometers.
5. Build `T[i, j, t]` from the distance and time-of-day traffic speed.

The implementation calls:

```python
nx.shortest_path(G, origin_node, destination_node, weight="length")
```

The graph is an OSMnx `MultiDiGraph`, so there can be multiple road edges between the same node pair. After NetworkX returns the node path, the distance summation uses the shortest parallel edge for each hop.

Formula:

```text
G = (V, E)
P = (v0, v1, ..., vk)

L(P) = sum from i=0 to k-1 of min(length_e for e in E(vi, vi+1))
P* = argmin over all paths P from origin to destination of L(P)
distance_km = L(P*) / 1000
```

Where `length_e` is the OSM road-segment length in meters.

Travel time is:

```text
raw_minutes = (distance_km / speed_kmph) * 60
```

The result is rounded up to the configured `dt` grid:

```text
travel_minutes = ceil(raw_minutes / dt) * dt
```

The base speed is `30 km/h`. It is multiplied by `src/sim/traffic.py`:

```text
07:00-10:00 -> 0.70
12:00-14:00 -> 0.85
16:00-19:00 -> 0.70
otherwise   -> 1.00
```

The effective speed is never allowed below `5 km/h`.

Cache behavior:

- The matrix is lazy, so it only computes pairs the planner actually evaluates.
- A cache entry stores `distance_km` plus `time_by_departure`.
- Cache keys include the road-network namespace, locations, `dt`, base speed, and traffic model version.
- Cache files are saved in `data/cache/` as JSON and are safe to delete.

## Route Completion

The planner treats the following as hard constraints:

- customer service time,
- customer time windows, recorded as late deliveries when the customer can still be served before the route end time,
- vehicle capacity,
- mandatory return-to-depot when enabled,
- depot end time and optional driver shift limit,
- charging station queue wait,
- charger plug availability and optional required plug type,
- minimum reserve SoC,
- depot charging availability,
- partial charging decisions.

The browser route stops when one of these constraints prevents a feasible next step.

If the vehicle reaches the configured `End time` or driver shift limit, it returns a partial route with:

```text
Did not complete route in the given time
```

The UI still shows customers reached, recharge stops reached, remaining planned stops, and a map of the partial route.

If a customer time window cannot be met after travel, waiting, and service time, the route records that customer as late and continues when service still fits inside the configured route end time. If service would exceed the depot end time or driver shift limit, the route stops with a time failure. If route demand exceeds vehicle capacity, it stops before dispatch with a capacity failure. If no compatible reachable charger exists while maintaining reserve SoC, it stops with an energy failure.

## Evaluation Metrics

Every browser run computes a comparable summary per vehicle and for CSV export:

- `% customers served`: served customer stops divided by planned customer stops.
- `total distance`: sum of road-network drive-leg distance, including charger detours.
- `total time`: elapsed route time from dispatch to the last reached stop.
- `energy cost`: estimated driving energy value plus recharge cost.
- `charging time`: minutes spent waiting for and adding energy at chargers.
- `number of charging stops`: grouped recharge sessions at depot or public chargers.
- `late deliveries`: customers served after their configured time window.
- `minimum SoC`: lowest state of charge in the route timeline.
- `runtime`: wall-clock seconds spent planning that vehicle route.

## Energy And Cost

The timeline stores rows like:

```python
(location_id, minute, soc_kwh, charging_cost_usd)
```

## kWh Calculation

Driving energy is calculated per road-network drive leg:

```text
driving_kwh = distance_km * cons_kwh_per_km
```

This is the default simple model. The browser can also enable a realistic energy model for Manhattan-style operation:

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

The stop-and-go term is intentionally tied to `distance_km * stop_density_per_km` because Manhattan routes are dominated by intersections, curb stops, and congestion rather than steady highway cruising. Regenerative braking is modeled as a bounded credit, not free energy; the browser caps it at 18 percent.

The vehicle's SoC is updated as:

```text
next_soc_kwh = previous_soc_kwh - driving_kwh
```

Initial SoC is:

```text
initial_soc_kwh = battery_kwh * initial_soc_pct
```

Charging adds energy by time step:

```text
kwh_added = charger_power_kw * (dt / 60)
```

The added energy is capped so SoC never exceeds `battery_kwh`.

For missing or zero public charger power, the planner uses `7.2 kW` as a Level 2 fallback. Depot charging uses the browser's `Depot power kW` field.

Charging is partial by default in the active browser planner. When a vehicle stops to charge, it targets the smaller of:

```text
usable_battery_kwh
energy_needed_to_drive_next_leg + required_energy_after_arrival
```

If a queue wait is configured, the vehicle waits at the charger before charging. Chargers with zero available plugs are ignored. If `Required plug type` is set, only chargers whose `plug_type` or `connection_type` text contains that value are considered.

## Dollar Cost Calculation

Charging cost is calculated per charging step:

```text
step_cost_usd = kwh_added * hourly_price_usd_per_kwh
```

The hourly price comes from the selected browser price mode:

- synthetic daily profile,
- selected-day NYISO Zone J data,
- local NYISO file,
- or flat price.

Selected-day NYISO mode fetches public NYISO CSV data for the date in the browser `Day` field. The app tries day-ahead prices first and real-time prices second. Successful fetches are cached in `data/processed/`.

The cumulative charging cost is stored in the route timeline.

Driving energy does not directly create a paid charging transaction, but the UI also estimates the dollar value of energy consumed while driving:

```text
driving_energy_value = driving_kwh * hourly_price_at_drive_departure
```

The UI reports:

- `Energy used`: total kWh consumed while driving.
- `Driving energy value`: estimated value of consumed driving energy using the hourly price at departure time.
- `Recharge cost`: actual charging dollars spent during recharge sessions.
- `Estimated energy cost`: driving energy value plus recharge cost.

## Elapsed And Billable Time

The planner stores time as minutes from day start.

```text
elapsed_time = end_time - start_time
```

If a break is enabled and `Billable break` is off, the app subtracts the overlap between the route interval and break interval:

```text
billable_time = elapsed_time - non_billable_break_overlap
```

Breaks currently affect reporting and billing only. They do not prevent driving or charging during the break.

## Recharge Display

Raw timelines contain one row per time step while charging.

For display, `src/eval/summarize.py` groups consecutive charging rows into one recharge session:

```text
Recharge CH353541 09:40-10:20; 2.13 kWh; $0.53
```

The grouped recharge sessions are used in the stop order table, the recharge details table, and the per-vehicle Folium map.

## Time Step

`dt` controls the time grid in minutes. Larger values run faster but are less precise.

Common values:

- `10`: more precise, slower.
- `20`: faster, useful for browser experiments.

## Known Limitations

- The main route order is charging-aware, but it is still heuristic rather than a globally optimal EVRPTW solver.
- Break windows affect reporting and billing, not route feasibility.
- Charger queue simulation exists as a scaffold, but the browser run currently focuses on route feasibility and charging cost.
- Charger selection is optimized for interactive speed. It uses fast geographic filtering and road-network distance for actual drive legs.
- The older `rcsp_leg` function still exists in the solver file for a time-expanded leg search, but the browser workflow currently uses the faster greedy route loop for responsiveness.
