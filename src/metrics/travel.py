from pathlib import Path
import networkx as nx
import numpy as np
import osmnx as ox

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def load_graph():
    return ox.load_graphml(PROCESSED_DIR / "manhattan_drive.graphml")


def nearest_node(G, lat, lon):
    return ox.distance.nearest_nodes(G, lon, lat)


def path_time_minutes(G, path, depart_min, base_speed_kmph=30.0):
    from src.sim.traffic import speed_multiplier
    # sum edge lengths; apply multiplier at depart time (simple 1-hop model for now)
    length_m = ox.utils_graph.get_route_edge_attributes(G, path, "length", minimize_key="length")
    km = sum(length_m) / 1000.0
    mult = speed_multiplier(depart_min % (24 * 60))
    speed = base_speed_kmph * mult
    return km, int(np.ceil(km / speed * 60))


def build_matrices(points_df):
    """
    points_df: columns [id, lat, lon]
    returns: dist_km[i][j], time_min[i][j], and the node ids used
    """
    G = load_graph()
    node_by_id = {r["id"]: nearest_node(G, r["lat"], r["lon"]) for _, r in points_df.iterrows()}
    ids = points_df["id"].tolist()
    n = len(ids)
    dist = [[0] * n for _ in range(n)]
    time = [[0] * n for _ in range(n)]
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            if i == j: continue
            path = nx.shortest_path(G, node_by_id[a], node_by_id[b], weight="length")
            km, tmin = path_time_minutes(G, path, depart_min=8 * 60)  # baseline depart time
            dist[i][j] = km
            time[i][j] = tmin
    return ids, dist, time, node_by_id
