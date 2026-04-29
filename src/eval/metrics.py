from __future__ import annotations

from typing import Dict, List

from src.eval.summarize import grouped_charges


def _price_at_minute(prices_hourly: List[float], minute: int) -> float:
    if not prices_hourly:
        return 0.0
    hour_idx = max(0, min(len(prices_hourly) - 1, (int(minute) // 60) % 24))
    return float(prices_hourly[hour_idx])


def evaluate_plan(inst: Dict, route: List[str], plan: Dict, runtime_sec: float = 0.0) -> Dict:
    timeline = plan.get("timeline", [])
    depot_id = inst["depot"]["id"]
    planned_customers = [stop for stop in route if str(stop).startswith("C")]
    served_customers = [
        stop for stop in plan.get("completed_route_ids", [])
        if str(stop).startswith("C")
    ]
    charges = grouped_charges(timeline, depot_id=depot_id)
    drive_legs = plan.get("drive_legs", [])
    total_time = 0
    if timeline:
        total_time = max(0, int(plan.get("end_time", timeline[-1][1])) - int(timeline[0][1]))
    min_soc = min((float(row[2]) for row in timeline), default=0.0)
    charging_time = sum(max(0, int(c["end_min"]) - int(c["start_min"])) for c in charges)
    recharge_cost = sum(float(c["cost_usd"]) for c in charges)
    prices_hourly = inst.get("prices_hourly", [])
    driving_energy_value = sum(
        float(leg.get("energy_kwh", 0.0)) * _price_at_minute(prices_hourly, int(leg.get("depart_min", 0)))
        for leg in drive_legs
    )
    energy_cost = driving_energy_value + recharge_cost

    return {
        "customers_planned": len(planned_customers),
        "customers_served": len(served_customers),
        "customers_served_pct": (len(served_customers) / len(planned_customers) * 100.0) if planned_customers else 100.0,
        "total_distance_km": sum(float(leg.get("distance_km", 0.0)) for leg in drive_legs),
        "total_time_min": total_time,
        "driving_energy_value_usd": driving_energy_value,
        "recharge_cost_usd": recharge_cost,
        "energy_cost_usd": energy_cost,
        "charging_time_min": charging_time,
        "charging_stops": len(charges),
        "late_deliveries": len(plan.get("late_delivery_ids", [])),
        "min_soc_kwh": min_soc,
        "runtime_sec": float(runtime_sec or plan.get("runtime_sec", 0.0) or 0.0),
    }
