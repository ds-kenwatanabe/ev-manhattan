# Data

The project uses local processed files under `data/processed/` and writes run outputs under `data/outputs/`.

## Processed Data

Required files:

```text
data/processed/manhattan_drive.graphml
data/processed/nodes.parquet
data/processed/edges.parquet
data/processed/chargers.parquet
data/processed/instance_2025-07-15.json
data/processed/nyiso_zone_j_2025_07.parquet
```

## Road Network

The Manhattan road graph is stored as:

```text
data/processed/manhattan_drive.graphml
```

It is used for snapping locations to road nodes, shortest-path distance, route map polylines, and drive energy estimates.

`nodes.parquet` and `edges.parquet` are used by the browser selector map.

## Charging Stations

Charging stations are stored in:

```text
data/processed/chargers.parquet
```

The browser UI can filter chargers with `Min charger kW` and `Max chargers available`.

The planner inserts charging stops using real charger IDs formatted as:

```text
CH<id>
```

## Electricity Prices

The local July 2025 NYISO Zone J file is:

```text
data/processed/nyiso_zone_j_2025_07.parquet
```

The browser can use synthetic daily prices, selected-day NYISO prices, local July 2025 NYISO prices, or flat prices.

When `Use NYISO selected day` is selected, the app fetches public NYISO Zone J CSV data for the selected date and caches it as:

```text
data/processed/nyiso_zone_j_YYYY_MM_DD_dam.parquet
data/processed/nyiso_zone_j_YYYY_MM_DD_rt.parquet
```

The app tries day-ahead first, then real-time. If neither is available, it falls back to local July 2025 data when the selected day exists there, otherwise synthetic data.

Selected-day NYISO fetching requires outbound network and DNS access to `mis.nyiso.com`. If the app cannot resolve or reach NYISO, it uses the fallback profile and reports `network/DNS unavailable` in the run summary.

Every browser run writes generated price data to:

```text
data/outputs/web_prices_...csv
data/outputs/web_nyiso_zone_j_...parquet
```

These generated files are what the run used.

## Instances

The base instance is:

```text
data/processed/instance_2025-07-15.json
```

Browser runs copy and modify this base instance based on the selected settings.

Generated instances are written to:

```text
data/outputs/web_instance_...json
```

## Output Maps

Browser runs write maps to:

```text
data/outputs/web_overview_...html
data/outputs/web_plan_..._V1.html
data/outputs/web_plan_..._V2.html
```

The overview map shows the run context. Per-vehicle maps show ordered route stops and recharge stops.
