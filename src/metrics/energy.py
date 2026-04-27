from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class EnergyModel:
    enabled: bool = False
    payload_kg: float = 0.0
    payload_penalty_per_100kg: float = 0.015
    stop_density_per_km: float = 0.0
    stop_go_penalty_per_stop: float = 0.008
    ambient_temp_c: float = 20.0
    comfort_temp_c: float = 20.0
    hvac_penalty_per_deg_c: float = 0.006
    speed_reference_kmph: float = 30.0
    speed_penalty_factor: float = 0.35
    regen_credit: float = 0.0
    max_regen_credit: float = 0.18
    battery_degradation_pct: float = 0.0


def model_from_vehicle_spec(vehicle_spec: Mapping) -> EnergyModel:
    params = vehicle_spec.get("energy_model", {}) or {}
    return EnergyModel(
        enabled=bool(params.get("enabled", False)),
        payload_kg=float(params.get("payload_kg", 0.0) or 0.0),
        payload_penalty_per_100kg=float(params.get("payload_penalty_per_100kg", 0.015) or 0.0),
        stop_density_per_km=float(params.get("stop_density_per_km", 0.0) or 0.0),
        stop_go_penalty_per_stop=float(params.get("stop_go_penalty_per_stop", 0.008) or 0.0),
        ambient_temp_c=float(params.get("ambient_temp_c", 20.0) or 20.0),
        comfort_temp_c=float(params.get("comfort_temp_c", 20.0) or 20.0),
        hvac_penalty_per_deg_c=float(params.get("hvac_penalty_per_deg_c", 0.006) or 0.0),
        speed_reference_kmph=float(params.get("speed_reference_kmph", 30.0) or 30.0),
        speed_penalty_factor=float(params.get("speed_penalty_factor", 0.35) or 0.0),
        regen_credit=float(params.get("regen_credit", 0.0) or 0.0),
        max_regen_credit=float(params.get("max_regen_credit", 0.18) or 0.0),
        battery_degradation_pct=float(params.get("battery_degradation_pct", 0.0) or 0.0),
    )


def usable_battery_kwh(battery_kwh: float, model: EnergyModel | None = None) -> float:
    if not model or not model.enabled:
        return float(battery_kwh)
    degradation = max(0.0, min(0.95, model.battery_degradation_pct))
    return float(battery_kwh) * (1.0 - degradation)


def kwh_needed(
        distance_km: float,
        cons_kwh_per_km: float,
        model: EnergyModel | None = None,
        speed_kmph: float | None = None,
) -> float:
    distance_km = max(0.0, float(distance_km))
    base = distance_km * float(cons_kwh_per_km)
    if not model or not model.enabled or distance_km <= 0:
        return base

    factor = 1.0
    factor += max(0.0, model.payload_kg) / 100.0 * max(0.0, model.payload_penalty_per_100kg)
    factor += distance_km * max(0.0, model.stop_density_per_km) * max(0.0, model.stop_go_penalty_per_stop)
    factor += abs(model.ambient_temp_c - model.comfort_temp_c) * max(0.0, model.hvac_penalty_per_deg_c)

    if speed_kmph and speed_kmph > 0 and model.speed_reference_kmph > 0:
        speed_delta = abs(float(speed_kmph) - model.speed_reference_kmph) / model.speed_reference_kmph
        factor += speed_delta * max(0.0, model.speed_penalty_factor)

    regen = max(0.0, min(max(0.0, model.max_regen_credit), model.regen_credit))
    factor *= 1.0 - regen
    return max(0.0, base * factor)
