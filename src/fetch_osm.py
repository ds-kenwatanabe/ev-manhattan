from pathlib import Path
import osmnx as ox
import geopandas as gpd
import pandas as pd
import json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def _stringify_mixed_col(s: pd.Series) -> pd.Series:
    """Make lists/dicts JSON strings; scalars -> str; NaNs -> None."""
    def conv(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        if isinstance(v, (list, dict)):
            return json.dumps(v)
        return str(v)
    return s.apply(conv)


def _sanitize_for_parquet(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    # Columns that are often mixed-type in OSMnx edges:
    candidate_cols = [c for c in gdf.columns if gdf[c].dtype == "object"]
    for c in candidate_cols:
        # Only stringify if the column *actually* contains a list/dict somewhere
        if gdf[c].apply(lambda v: isinstance(v, (list, dict))).any():
            gdf[c] = _stringify_mixed_col(gdf[c])
    return gdf


def download_manhattan_drive_graph(out_dir=PROCESSED_DIR):
    G = ox.graph_from_place("Manhattan, New York, USA", network_type="drive")

    # OSMnx ≥ 1.3: lengths/travel-times live under ox.distance
    G = ox.distance.add_edge_lengths(G)
    # (optional) if you want naive travel times:
    # G = ox.distance.add_edge_travel_times(G, precision=1)

    out_dir.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(G, out_dir / "manhattan_drive.graphml")

    nodes, edges = ox.graph_to_gdfs(G)

    # Sanitize mixed-type columns before Parquet
    nodes_s = _sanitize_for_parquet(nodes)
    edges_s = _sanitize_for_parquet(edges)

    # GeoParquet (geometry preserved) — requires recent pyarrow/geopandas
    nodes_s.to_parquet(out_dir / "nodes.parquet", index=False)
    edges_s.to_parquet(out_dir / "edges.parquet", index=False)

    return nodes_s, edges_s


if __name__ == "__main__":
    download_manhattan_drive_graph()
