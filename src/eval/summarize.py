from typing import List, Tuple


def summarize_timeline(tl: List[Tuple[str, int, float, float]], charger_prefix="CH", depot_id="DEPOT1"):
    charges, drives = [], []
    n = len(tl)
    for i in range(n - 1):
        loc, t, b, c = tl[i]
        loc2, t2, b2, c2 = tl[i + 1]

        # drive between nodes
        if loc != loc2:
            drives.append({"from": loc, "to": loc2, "depart_min": t, "arrive_min": t2,
                           "energy_kwh": max(0.0, b - b2)})

        # exact charging blocks (consecutive stays at same node with SoC ↑)
        if loc == loc2 and (loc.startswith(charger_prefix) or loc == depot_id) and b2 > b + 1e-9:
            charges.append({"station_id": loc, "start_min": t, "end_min": t2,
                            "energy_kwh": b2 - b, "cost_usd": c2 - c})

        # fallback: if cost ↑ between different nodes, attribute charge to the *origin* node
        if loc != loc2 and (c2 > c + 1e-9) and (loc.startswith(charger_prefix) or loc == depot_id):
            charges.append({"station_id": loc, "start_min": t, "end_min": t2,
                            "energy_kwh": max(0.0, (b2 - b)),  # may be 0 if travel consumed more
                            "cost_usd": c2 - c})

    return {"charges": charges, "drives": drives}


def grouped_charges(tl: List[Tuple[str, int, float, float]], charger_prefix="CH", depot_id="DEPOT1"):
    grouped = []
    current = None
    for i in range(len(tl) - 1):
        loc, t, b, c = tl[i]
        loc2, t2, b2, c2 = tl[i + 1]
        is_charge = loc == loc2 and (loc.startswith(charger_prefix) or loc == depot_id) and b2 > b + 1e-9
        if not is_charge:
            if current is not None:
                grouped.append(current)
                current = None
            continue
        if current is None or current["station_id"] != loc:
            if current is not None:
                grouped.append(current)
            current = {
                "station_id": loc,
                "start_min": t,
                "end_min": t2,
                "energy_kwh": b2 - b,
                "cost_usd": c2 - c,
            }
        else:
            current["end_min"] = t2
            current["energy_kwh"] += b2 - b
            current["cost_usd"] += c2 - c
    if current is not None:
        grouped.append(current)
    return grouped
