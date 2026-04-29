from pathlib import Path
import osmnx as ox
import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PLACE = "Manhattan, New York County, New York, USA"
OUT = PROCESSED_DIR / "manhattan_drive.graphml"


def build_manhattan_graph(out_path=OUT):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    # Make Overpass more permissive and resilient
    ox.settings.log_console = True
    ox.settings.timeout = 180  # seconds
    ox.settings.overpass_rate_limit = True
    # Increase max area so Overpass makes far fewer sub-queries
    ox.settings.max_query_area_size = 2_000_000_000  # 2e9 (default is much smaller)

    # Keep the "drive" network, simplify geometry
    # Custom filter prunes pedestrian-only paths so the query is lighter
    custom_filter = (
        '["highway"~"motorway|trunk|primary|secondary|tertiary|residential|'
        'living_street|unclassified|service|motorway_link|trunk_link|primary_link|'
        'secondary_link|tertiary_link"]'
    )

    # Use the administrative polygon of Manhattan and truncate by edge
    G = ox.graph_from_place(
        PLACE,
        network_type="drive",
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
        custom_filter=custom_filter,
    )

    # Keep largest weakly connected component
    ug = G.to_undirected()
    largest_cc = max(nx.connected_components(ug), key=len)
    Gc = G.subgraph(largest_cc).copy()

    # Ensure edge lengths (no-op if already present)
    try:
        ox.distance.add_edge_lengths(Gc)
    except Exception:
        pass

    ox.save_graphml(Gc, out_path)
    return out_path


if __name__ == "__main__":
    p = build_manhattan_graph()
    print("Saved:", p)
