from src.eval.summarize import grouped_charges, summarize_timeline


def test_summarize_timeline_splits_drives_and_charge_steps():
    timeline = [
        ("DEPOT1", 480, 5.0, 0.0),
        ("C001", 500, 4.0, 0.0),
        ("CH10", 520, 3.0, 0.0),
        ("CH10", 540, 4.2, 0.30),
        ("CH10", 560, 5.0, 0.50),
        ("C002", 580, 4.1, 0.50),
    ]

    summary = summarize_timeline(timeline)

    assert [drive["from"] for drive in summary["drives"]] == ["DEPOT1", "C001", "CH10"]
    assert [drive["to"] for drive in summary["drives"]] == ["C001", "CH10", "C002"]
    assert len(summary["charges"]) == 2
    assert sum(charge["energy_kwh"] for charge in summary["charges"]) == 2.0
    assert round(sum(charge["cost_usd"] for charge in summary["charges"]), 2) == 0.50


def test_grouped_charges_combines_consecutive_charging_rows():
    timeline = [
        ("DEPOT1", 480, 5.0, 0.0),
        ("C001", 500, 4.0, 0.0),
        ("CH10", 520, 3.0, 0.0),
        ("CH10", 540, 4.2, 0.30),
        ("CH10", 560, 5.0, 0.50),
        ("C002", 580, 4.1, 0.50),
    ]

    charges = grouped_charges(timeline)

    assert charges == [
        {
            "station_id": "CH10",
            "start_min": 520,
            "end_min": 560,
            "energy_kwh": 2.0,
            "cost_usd": 0.5,
        }
    ]
