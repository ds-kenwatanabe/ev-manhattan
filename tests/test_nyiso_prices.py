import pandas as pd

from src.fetch_nyiso import _normalize_nyiso_zone_j


def test_normalize_nyiso_zone_j_filters_nyc_ptid_and_converts_to_kwh():
    raw = pd.DataFrame(
        {
            "Time Stamp": ["04/27/2026 00:00:00", "04/27/2026 00:00:00", "04/27/2026 01:00:00"],
            "Name": ["N.Y.C.", "CAPITL", "N.Y.C."],
            "PTID": [61761, 61757, 61761],
            "LBMP ($/MWHr)": [42.0, 10.0, 45.0],
        }
    )

    normalized = _normalize_nyiso_zone_j(raw)

    assert normalized["price_usd_per_kwh"].tolist() == [0.042, 0.045]
    assert len(normalized) == 2
