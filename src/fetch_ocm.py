import json
import os
import pandas as pd
import requests
from dotenv import load_dotenv
from pathlib import Path
from shapely.geometry import MultiPoint, Point

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

# Manhattan bounds
S, W, N, E = 40.698, -74.028, 40.882, -73.907
# Central Park-ish centroid for fallback
CENTER_LAT, CENTER_LON = 40.7831, -73.9712


def fetch_ocm_bbox(
    api_key: str,
    s=S, w=W, n=N, e=E,
    max_results=500,
    out=RAW_DIR / "ocm.json"
):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    url = "https://api.openchargemap.io/v3/poi/"
    # NOTE: OCM wants top-left (N,W) and bottom-right (S,E) as (lat,lon) pairs
    bbox_str = f"({n},{w}),({s},{e})"
    params = {
        "output": "json",
        "boundingbox": bbox_str,
        "maxresults": max_results,
        "compact": True,
        "verbose": False,
        "countrycode": "US",
    }
    headers = {"X-API-Key": api_key, "User-Agent": "ev-manhattan-2025/1.0"}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    with open(out, "wb") as f:
        f.write(r.content)
    return out


def fetch_ocm_manhattan_bands(
    api_key: str,
    bands=8,
    max_results_per_band=500,
    out=RAW_DIR / "ocm.json",
):
    """Fetch Manhattan in north-south bands to avoid OCM's per-request cap."""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    url = "https://api.openchargemap.io/v3/poi/"
    headers = {"X-API-Key": api_key, "User-Agent": "ev-manhattan-2025/1.0"}
    by_id = {}
    step = (N - S) / float(bands)
    for idx in range(bands):
        band_s = S + idx * step
        band_n = S + (idx + 1) * step
        bbox_str = f"({band_n},{W}),({band_s},{E})"
        params = {
            "output": "json",
            "boundingbox": bbox_str,
            "maxresults": max_results_per_band,
            "compact": True,
            "verbose": False,
            "countrycode": "US",
        }
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected OCM payload for band {idx + 1}: {str(data)[:200]}")
        for item in data:
            item_id = item.get("ID")
            if item_id is not None:
                by_id[item_id] = item
    with open(out, "w", encoding="utf-8") as f:
        json.dump(list(by_id.values()), f)
    return out


def fetch_ocm_fallback_radius(
    api_key: str,
    lat=CENTER_LAT, lon=CENTER_LON,
    distance_km=8,
    max_results=500,
    out=RAW_DIR / "ocm_fallback.json"
):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    url = "https://api.openchargemap.io/v3/poi/"
    params = {
        "output": "json",
        "latitude": lat,
        "longitude": lon,
        "distance": distance_km,
        "distanceunit": "KM",
        "maxresults": max_results,
        "compact": True,
        "verbose": False,
        "countrycode": "US",
    }
    headers = {"X-API-Key": api_key, "User-Agent": "ev-manhattan-2025/1.0"}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    with open(out, "wb") as f:
        f.write(r.content)
    return out


def normalize_ocm(in_json, out_parquet=PROCESSED_DIR / "chargers.parquet"):
    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    data = json.load(open(in_json))
    if not isinstance(data, list):
        raise ValueError(f"Unexpected OCM payload: {str(data)[:200]}")
    rows = []
    for p in data:
        st = p.get("AddressInfo") or {}
        ops = p.get("Connections") or []
        power_kw = max([c.get("PowerKW") or 0 for c in ops] + [0])
        plugs = len(ops)
        rows.append({
            "id": p.get("ID"),
            "name": st.get("Title"),
            "lat": st.get("Latitude"),
            "lon": st.get("Longitude"),
            "power_kw": power_kw,
            "plugs": plugs,
        })
    df = pd.DataFrame(rows, columns=["id","name","lat","lon","power_kw","plugs"])
    # filter to Manhattan bbox just in case the radius call brought nearby points
    df = df.dropna(subset=["lat","lon"])
    df = df[(df["lat"].between(S, N)) & (df["lon"].between(W, E))]
    nodes_path = PROCESSED_DIR / "nodes.parquet"
    if nodes_path.exists():
        nodes = pd.read_parquet(nodes_path)[["x", "y"]].dropna()
        hull = MultiPoint([Point(float(row.x), float(row.y)) for row in nodes.itertuples(index=False)]).convex_hull.buffer(0.004)
        df = df[df.apply(lambda row: hull.contains(Point(float(row["lon"]), float(row["lat"]))), axis=1)]
    df.to_parquet(out_parquet, index=False)
    return df


if __name__ == "__main__":
    api_key = os.getenv("OCM_API_KEY")
    if not api_key:
        raise RuntimeError("Please set OCM_API_KEY in your environment.")

    # Fetch in bands so the 500-result API cap does not starve parts of Manhattan.
    path = fetch_ocm_manhattan_bands(api_key)
    df = normalize_ocm(path)

    # Fallback if empty
    if df.empty:
        print("bbox returned 0 rows — trying radius fallback…")
        path = fetch_ocm_fallback_radius(api_key)
        df = normalize_ocm(path)

    print(df.shape)
    print(df.head())
