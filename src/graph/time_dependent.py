from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Dict, Tuple

import networkx as nx

from src.graph.traffic import speed_multiplier


DistanceFunc = Callable[[str, str], float]


def speed_kmph(minute: int, base_speed_kmph: float = 30.0) -> float:
    return max(5.0, base_speed_kmph * speed_multiplier(minute % (24 * 60)))


def snap_up_to_grid(minutes: int, dt: int) -> int:
    return int(math.ceil(minutes / float(dt)) * dt)


def travel_minutes_for_departure(distance_km: float, minute: int, dt: int, base_speed_kmph: float = 30.0) -> int:
    raw = int(math.ceil((float(distance_km) / speed_kmph(minute, base_speed_kmph)) * 60.0))
    return snap_up_to_grid(raw, dt)


class TimeDependentTravelMatrix:
    """
    Lazy cached T[i,j,t] matrix.

    Each requested origin/destination pair stores one shortest-path distance and
    travel times for every departure bucket in the day. This avoids repeated
    NetworkX shortest-path calls while keeping cache creation proportional to
    routes actually evaluated by the planner.
    """

    def __init__(
            self,
            cache_dir: str | Path,
            namespace: str,
            locations: Dict[str, Dict],
            dt: int,
            distance_func: DistanceFunc,
            base_speed_kmph: float = 30.0,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self.locations = {
            loc_id: {
                "lat": round(float(loc["lat"]), 7),
                "lon": round(float(loc["lon"]), 7),
            }
            for loc_id, loc in sorted(locations.items())
        }
        self.dt = int(dt)
        self.distance_func = distance_func
        self.base_speed_kmph = float(base_speed_kmph)
        self.buckets = list(range(0, 24 * 60, self.dt))
        self.path = self.cache_dir / f"time_matrix_{self._cache_key()}.json"
        self.data = self._load()
        self.dirty = False

    def travel(self, origin_id: str, destination_id: str, depart_minute: int) -> Tuple[float, int]:
        if origin_id == destination_id:
            return 0.0, 0
        pair_key = f"{origin_id}->{destination_id}"
        pair = self.data["pairs"].get(pair_key)
        if pair is None:
            km = float(self.distance_func(origin_id, destination_id))
            pair = {
                "distance_km": km,
                "time_by_departure": {
                    str(bucket): travel_minutes_for_departure(km, bucket, self.dt, self.base_speed_kmph)
                    for bucket in self.buckets
                },
            }
            self.data["pairs"][pair_key] = pair
            self.dirty = True
        bucket = (int(depart_minute) // self.dt) * self.dt
        bucket = max(0, min(self.buckets[-1], bucket))
        return float(pair["distance_km"]), int(pair["time_by_departure"][str(bucket)])

    def save(self):
        if not self.dirty:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, separators=(",", ":")))
        tmp.replace(self.path)
        self.dirty = False

    def _cache_key(self) -> str:
        payload = {
            "namespace": self.namespace,
            "dt": self.dt,
            "base_speed_kmph": self.base_speed_kmph,
            "locations": self.locations,
            "traffic_model": "hourly_speed_multiplier_v1",
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:20]

    def _load(self) -> Dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if data.get("dt") == self.dt and data.get("locations") == self.locations:
                    data.setdefault("pairs", {})
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "namespace": self.namespace,
            "dt": self.dt,
            "base_speed_kmph": self.base_speed_kmph,
            "locations": self.locations,
            "pairs": {},
        }


def shortest_path_km(G: nx.MultiDiGraph, node_by_id: Dict[str, int], origin_id: str, destination_id: str) -> float:
    try:
        path = nx.shortest_path(G, node_by_id[origin_id], node_by_id[destination_id], weight="length")
    except nx.NetworkXNoPath:
        path = nx.shortest_path(G.to_undirected(), node_by_id[origin_id], node_by_id[destination_id], weight="length")
    total_m = 0.0
    for u, v in zip(path[:-1], path[1:]):
        data = G.get_edge_data(u, v)
        if not data:
            continue
        lengths = [attrs.get("length", 0.0) for attrs in data.values()] if isinstance(data, dict) else [data.get("length", 0.0)]
        total_m += min(lengths) if lengths else 0.0
    return total_m / 1000.0
