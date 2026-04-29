from src.energy.model import EnergyModel, kwh_needed, usable_battery_kwh


def test_simple_energy_model_preserves_distance_times_consumption():
    assert kwh_needed(12.0, 0.25) == 3.0


def test_realistic_energy_model_applies_modifiers_and_regen():
    model = EnergyModel(
        enabled=True,
        payload_kg=200.0,
        payload_penalty_per_100kg=0.01,
        stop_density_per_km=2.0,
        stop_go_penalty_per_stop=0.01,
        ambient_temp_c=30.0,
        comfort_temp_c=20.0,
        hvac_penalty_per_deg_c=0.005,
        speed_reference_kmph=30.0,
        speed_penalty_factor=0.30,
        regen_credit=0.10,
    )

    energy = kwh_needed(10.0, 0.20, model, speed_kmph=15.0)

    base = 2.0
    factor = 1.0 + 0.02 + 0.20 + 0.05 + 0.15
    assert round(energy, 6) == round(base * factor * 0.90, 6)


def test_battery_degradation_reduces_usable_capacity():
    model = EnergyModel(enabled=True, battery_degradation_pct=0.20)

    assert usable_battery_kwh(50.0, model) == 40.0
