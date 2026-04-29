from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path
from typing import Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "sample"


def _jitter(rng: random.Random, center: float, spread: float) -> float:
    return round(center + rng.uniform(-spread, spread), 6)


def _hourly_prices(seed: int) -> List[float]:
    rng = random.Random(seed + 10_000)
    prices = []
    for hour in range(24):
        morning_peak = math.exp(-((hour - 8) ** 2) / 10.0)
        evening_peak = math.exp(-((hour - 18) ** 2) / 8.0)
        price = 0.17 + 0.06 * morning_peak + 0.09 * evening_peak + rng.uniform(-0.008, 0.008)
        prices.append(round(max(0.08, price), 4))
    return prices


def build_sample_instance(
    seed: int = 7,
    customer_count: int = 12,
    charger_count: int = 4,
    day: str = "2025-07-15",
) -> Dict:
    rng = random.Random(seed)
    depot = {
        "id": "DEPOT1",
        "lat": 40.756,
        "lon": -73.998,
        "start_min": 8 * 60,
        "end_min": 17 * 60,
    }
    customers = []
    for idx in range(customer_count):
        start = rng.randrange(8 * 60, 14 * 60 + 1, 15)
        end = min(17 * 60, start + rng.randrange(90, 241, 15))
        customers.append({
            "cust_id": f"C{idx:03d}",
            "lat": _jitter(rng, 40.755, 0.035),
            "lon": _jitter(rng, -73.985, 0.028),
            "tw_start_min": start,
            "tw_end_min": end,
            "demand_kg": rng.randint(10, 55),
        })

    chargers = []
    for idx in range(charger_count):
        chargers.append({
            "id": idx + 1,
            "name": f"Sample Charger {idx + 1}",
            "lat": _jitter(rng, 40.755, 0.032),
            "lon": _jitter(rng, -73.985, 0.026),
            "power_kw": rng.choice([7.2, 11.0, 50.0]),
            "plugs": rng.randint(1, 4),
            "plug_type": rng.choice(["J1772", "CCS"]),
        })

    vehicles = [
        {
            "id": "V1",
            "battery_kwh": 35.0,
            "initial_soc_pct": 0.85,
            "cons_kwh_per_km": 0.24,
            "cap_kg": 500.0,
            "depot": depot["id"],
        },
        {
            "id": "V2",
            "battery_kwh": 35.0,
            "initial_soc_pct": 0.85,
            "cons_kwh_per_km": 0.24,
            "cap_kg": 500.0,
            "depot": depot["id"],
        },
    ]

    return {
        "meta": {
            "city": "Manhattan, NYC",
            "date": day,
            "price_unit": "USD/kWh",
            "timezone": "America/New_York",
            "sample": True,
            "seed": seed,
        },
        "depot": depot,
        "vehicles": vehicles,
        "customers": customers,
        "chargers": chargers,
        "prices_hourly": _hourly_prices(seed),
    }


def write_sample_dataset(
    out_dir: Path,
    seed: int = 7,
    customer_count: int = 12,
    charger_count: int = 4,
    day: str = "2025-07-15",
) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    instance = build_sample_instance(seed, customer_count, charger_count, day)
    instance_path = out_dir / f"sample_instance_seed_{seed}.json"
    prices_path = out_dir / f"sample_prices_seed_{seed}.csv"

    instance_path.write_text(json.dumps(instance, indent=2) + "\n")
    with prices_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["hour", "price_usd_per_kwh"], lineterminator="\n")
        writer.writeheader()
        for hour, price in enumerate(instance["prices_hourly"]):
            writer.writerow({"hour": hour, "price_usd_per_kwh": price})

    return {"instance": instance_path, "prices": prices_path}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a deterministic small EV Manhattan sample scenario.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--customers", type=int, default=12)
    parser.add_argument("--chargers", type=int, default=4)
    parser.add_argument("--date", default="2025-07-15")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = write_sample_dataset(
        out_dir=args.out_dir,
        seed=args.seed,
        customer_count=args.customers,
        charger_count=args.chargers,
        day=args.date,
    )
    print(f"instance={paths['instance']}")
    print(f"prices={paths['prices']}")


if __name__ == "__main__":
    main()
