from shapely.geometry import MultiPoint, Point

from src.run import web_app
import pandas as pd


def test_charger_filtering_removes_off_manhattan_and_keeps_coverage(monkeypatch):
    chargers = [
        {"id": "south", "lat": 0.0, "lon": 0.0, "power_kw": 50.0},
        {"id": "north", "lat": 10.0, "lon": 0.0, "power_kw": 50.0},
        {"id": "outside", "lat": 5.0, "lon": 4.0, "power_kw": 350.0},
    ]
    area = MultiPoint([Point(0.0, 0.0), Point(0.0, 10.0)]).convex_hull.buffer(0.5)
    monkeypatch.setattr(web_app, "_manhattan_area", lambda: area)
    monkeypatch.setattr(web_app.pd, "read_parquet", lambda _path: pd.DataFrame({"y": [0.0, 10.0], "x": [0.0, 0.0]}))

    selected = web_app._filter_and_rank_chargers(chargers, min_power=0.0, limit=2)

    assert [charger["id"] for charger in selected] == ["south", "north"]
