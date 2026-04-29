import json

from src.data.sample_scenario import build_sample_instance, write_sample_dataset


def test_sample_scenario_is_seed_reproducible():
    first = build_sample_instance(seed=11, customer_count=6, charger_count=2)
    second = build_sample_instance(seed=11, customer_count=6, charger_count=2)
    different = build_sample_instance(seed=12, customer_count=6, charger_count=2)

    assert first == second
    assert first != different
    assert first["meta"]["seed"] == 11
    assert len(first["customers"]) == 6
    assert len(first["chargers"]) == 2
    assert len(first["prices_hourly"]) == 24


def test_write_sample_dataset_outputs_json_and_prices(tmp_path):
    paths = write_sample_dataset(tmp_path, seed=5, customer_count=3, charger_count=1)

    instance = json.loads(paths["instance"].read_text())
    prices = paths["prices"].read_text().splitlines()

    assert instance["meta"]["sample"] is True
    assert [customer["cust_id"] for customer in instance["customers"]] == ["C000", "C001", "C002"]
    assert prices[0] == "hour,price_usd_per_kwh"
    assert len(prices) == 25
