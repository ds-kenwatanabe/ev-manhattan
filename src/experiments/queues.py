from typing import List, Dict
import heapq
import math
import json


def _station_id(raw_id) -> str:
    try:
        as_float = float(raw_id)
        if as_float.is_integer():
            return f"CH{int(as_float)}"
    except (TypeError, ValueError):
        pass
    return f"CH{raw_id}"


def _minute_to_hour(m: int) -> int:
    return max(0, min(23, (m // 60) % 24))


def _charge_cost(prices_hourly: List[float], start_min: int, duration_min: int, power_kw: float) -> float:
    """Approximate cost by summing per-minute price * kWh/min."""
    e_per_min = power_kw / 60.0
    cost = 0.0
    for m in range(start_min, start_min + duration_min):
        cost += prices_hourly[_minute_to_hour(m)] * e_per_min
    return cost


def simulate_queues_and_reprice(
        instance_json: str,
        chargers_df,  # DataFrame with id, power_kw, plugs
        charge_sessions: List[Dict],  # from summarize_timeline(); station_id like "CH1234"
):
    inst = json.load(open(instance_json))
    prices = inst["prices_hourly"]

    # Map station_id -> (plugs, power_kw)
    # Your plan uses "CH<ocm_id>" as station_id; strip the prefix to match df.id
    plugs: Dict[str, int] = {}
    power: Dict[str, float] = {}
    for _, row in chargers_df.iterrows():
        sid = _station_id(row["id"])
        plugs[sid] = int(row.get("plugs", 1)) if not (row.get("plugs") is None) else 1
        power[sid] = float(row.get("power_kw", 0.0))

    # Group sessions per station
    by_station: Dict[str, List[Dict]] = {}
    for s in charge_sessions:
        by_station.setdefault(s["station_id"], []).append(s)

    out = []
    for sid, sess in by_station.items():
        sess = sorted(sess, key=lambda s: s["start_min"])
        kW = max(0.1, power.get(sid, 0.0))  # avoid zero-power
        P = max(1, plugs.get(sid, 1))

        # active heap of end times (size <= P)
        active: List[int] = []
        for s in sess:
            req_start = s["start_min"]
            energy = s["energy_kwh"]
            dur = int(math.ceil(energy / kW * 60.0))

            # free finished
            while active and active[0] <= req_start:
                heapq.heappop(active)

            if len(active) < P:
                act_start = req_start
            else:
                # wait until earliest plug frees
                earliest = heapq.heappop(active)
                act_start = earliest

            act_end = act_start + dur
            heapq.heappush(active, act_end)

            # recompute cost at new times
            new_cost = _charge_cost(prices, act_start, dur, kW)

            out.append({
                **s,
                "requested_start": req_start,
                "requested_end": s["end_min"],
                "actual_start": act_start,
                "actual_end": act_end,
                "wait_min": max(0, act_start - req_start),
                "recomputed_cost_usd": new_cost,
            })

    return sorted(out, key=lambda x: (x["actual_start"], x["station_id"]))
