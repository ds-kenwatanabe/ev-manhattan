import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from shapely.geometry import MultiPoint, Point

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PROC = PROJECT_ROOT / "data" / "processed"
PROC.mkdir(parents=True, exist_ok=True)


def _sample_customers(n=40, nodes_path=PROC / "nodes.parquet", seed=7):
    nodes = pd.read_parquet(nodes_path)
    keep = nodes.sample(n, random_state=seed)[["y", "x"]].rename(columns={"y": "lat", "x": "lon"})
    # business-like time windows inside 9–18h
    rng = np.random.default_rng(seed)
    start = rng.integers(9*60, 13*60, size=n)
    end   = start + rng.integers(120, 360, size=n)  # 2–6h
    return pd.DataFrame({
        "cust_id": [f"C{i:03d}" for i in range(n)],
        "lat": keep["lat"].to_numpy(),
        "lon": keep["lon"].to_numpy(),
        "tw_start_min": start, "tw_end_min": end,
        "demand_kg": rng.integers(10, 60, size=n)
    })


def _pick_prices_for_day(day_str="2025-07-15", parquet=PROC / "nyiso_zone_j_2025_07.parquet"):
    df = pd.read_parquet(parquet)
    df = df.rename_axis("datetime").reset_index()
    day = pd.Timestamp(day_str)
    sel = df[df["datetime"].dt.date == day.date()].sort_values("datetime")
    if sel.empty:
        raise ValueError(f"No prices found for {day_str}.")
    # Exactly 24 hourly prices expected
    prices = sel["price_usd_per_kwh"].tolist()
    if len(prices) != 24:
        # resample just in case
        sel = (sel.set_index("datetime")
                  .resample("H")["price_usd_per_kwh"].mean()
                  .iloc[:24])
        prices = sel.tolist()
    return prices


def _load_chargers(parquet=PROC / "chargers.parquet"):
    df = pd.read_parquet(parquet)
    # basic sanity filters
    df = df.dropna(subset=["lat","lon"])
    # fill zeros with small value to avoid divide-by-zero elsewhere
    df["power_kw"] = df["power_kw"].fillna(0).clip(lower=0)
    df["plugs"] = df["plugs"].fillna(1).astype(int).clip(lower=1)
    # keep only plausible entries
    df = df[df["power_kw"] <= 400]  # safety against garbage values
    nodes = pd.read_parquet(PROC / "nodes.parquet")[["x", "y"]].dropna()
    hull = MultiPoint([Point(float(row.x), float(row.y)) for row in nodes.itertuples(index=False)]).convex_hull.buffer(0.004)
    df = df[df.apply(lambda row: hull.contains(Point(float(row["lon"]), float(row["lat"]))), axis=1)]
    return df


def build_instance(day_str="2025-07-15", out=PROC / "instance_2025-07-15.json"):
    edges = pd.read_parquet(PROC / "edges.parquet")  # ensures graph exists
    chargers = _load_chargers()
    customers = _sample_customers(n=40)
    prices_hourly = _pick_prices_for_day(day_str)

    depot = {
        "id": "DEPOT1",
        "lat": 40.756, "lon": -73.998,  # near Hudson Yards
        "start_min": 8*60, "end_min": 20*60
    }
    fleet = [{
        "id": f"V{i+1}",
        "battery_kwh": 45.0,
        "cons_kwh_per_km": 0.23,
        "cap_kg": 600.0,
        "depot": depot["id"]
    } for i in range(4)]

    instance = {
        "meta": {
            "city": "Manhattan, NYC",
            "date": day_str,
            "price_unit": "USD/kWh",
            "timezone": "America/New_York"
        },
        "depot": depot,
        "vehicles": fleet,
        "customers": customers.to_dict(orient="records"),
        "chargers": chargers.to_dict(orient="records"),
        "prices_hourly": prices_hourly,
        "network": {
            "n_edges": int(len(edges)),
            "avg_edge_len_m": float(edges["length"].mean())
        }
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(instance, open(out, "w"))
    return out


if __name__ == "__main__":
    # ensure prices exist (real CSV or synthetic)
    from src.fetch_nyiso import load_nyiso_or_synthetic
    load_nyiso_or_synthetic()
    print(build_instance())
