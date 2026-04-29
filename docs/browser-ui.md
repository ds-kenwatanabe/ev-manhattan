# Browser UI

The browser UI is the main way to use the project.

Run it with:

```bash
.venv/bin/python src/web/app.py --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Customer Selection

Use `Customer selection` to choose how customers are assigned:

- `Random customers`: the app selects customers from the generated pool.
- `Choose on map`: click customer dots in the selector map.

`Available customers` controls how many candidate customers are generated and shown on the selector map.

The selector map supports customer selection, depot placement, pan, zoom, and a local street-network background.

The current selector uses OpenStreetMap street tiles. Customer labels are hidden by default to avoid overlap; they appear on hover and remain visible for selected customers.

## Depot

Use `Depot input`:

- `Use coordinates`: type latitude and longitude.
- `Choose on map`: click the selector map to place the depot.

The depot is used as the start location and final return location.

## Time Window

`Start time` and `End time` define the vehicle operating window.

The planner tries to complete the route within that window. If charging and deliveries push the vehicle past `End time`, the vehicle stops with this status:

```text
Did not complete route in the given time
```

The page still shows the reached customers and recharge stops.

## Break Settings

`Enable break` adds a break window to reporting.

`Billable break` controls whether the break is counted in billable time.

Current limitation: breaks affect reporting and billing only. They do not prevent driving or charging during the break window.

## Prices

`Price data` controls the hourly electricity price profile used in a run:

- `Use synthetic data`: generate a synthetic 24-hour price curve for the selected day.
- `Use NYISO selected day`: fetch NYISO Zone J prices for the selected date and cache them locally.
- `Use local July 2025 NYISO file`: use `data/processed/nyiso_zone_j_2025_07.parquet` when the selected day exists there.
- `Use flat price`: use the entered `Flat price $/kWh` for all 24 hours.

Each run writes a generated CSV and parquet price file under `data/outputs/`.

For `Use NYISO selected day`, the app tries NYISO day-ahead prices first, then NYISO real-time prices. If NYISO data is unavailable for the selected day, the run falls back to local July 2025 data when applicable, then to synthetic prices.

If the machine running the app cannot reach NYISO because of DNS or internet access, the result will say `network/DNS unavailable` and use the fallback profile. The selected-day NYISO mode needs outbound access to `mis.nyiso.com` unless that date is already cached in `data/processed/`.

## Vehicle Settings

Important vehicle fields:

- `Battery kWh`: usable battery capacity.
- `Initial SoC %`: starting state of charge, from `0` to `1`.
- `Reserve kWh`: minimum desired remaining energy.
- `kWh per km`: consumption rate.
- `Capacity kg`: load capacity used by the instance.

With low battery settings, the planner inserts recharge stops when needed.

## Results

Each run shows:

- overview map,
- per-vehicle map links,
- route status,
- stop order,
- recharge details,
- energy used,
- driving energy value,
- recharge cost,
- charged kWh,
- end SoC,
- end time,
- elapsed and billable time.

Recharge stops appear directly in stop order as route steps:

```text
#6 Recharge CH353541 09:40-10:20; 2.13 kWh; $0.53
```

On the map, recharge stops are green numbered markers.

## Result Field Definitions

`Stop Order` shows the actual route sequence. Delivery stops and recharge stops are both listed as numbered steps. Recharge rows include time, kWh added, and charging cost.

`Energy used` is total driving energy:

```text
sum(distance_km * kWh_per_km for each drive leg)
```

When `Use realistic energy modifiers` is enabled, this value also includes payload, stop-and-go density, ambient temperature/HVAC, speed-dependent consumption, regenerative braking credit, and battery degradation. Battery degradation reduces usable capacity before planning starts.

`Charged` is the total kWh added at public chargers or depot charging.

`Driving energy value` estimates the value of the consumed driving energy using the hourly price at each drive departure time.

`Recharge cost` is the actual dollar cost accumulated during charging sessions.

`Estimated energy cost` is:

```text
Driving energy value + Recharge cost
```

`End SoC` is the remaining battery energy in kWh at the last reached stop.

`Service min/customer`, `Shift limit min`, `Queue wait min`, `Required plug type`, `Mandatory return to depot`, and `Reserve kWh` are hard planning constraints. A route can end partially if any one of them prevents the next stop.

`Elapsed time` is:

```text
End time - Start time
```

`Billable time` is elapsed time minus the non-billable break overlap when `Enable break` is on and `Billable break` is off.
