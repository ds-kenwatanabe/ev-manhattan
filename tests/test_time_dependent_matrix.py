from src.graph.time_dependent import TimeDependentTravelMatrix


def test_time_dependent_matrix_caches_pair_for_all_departure_buckets(tmp_path):
    calls = []

    def distance_func(origin_id, destination_id):
        calls.append((origin_id, destination_id))
        return 10.0

    locations = {
        "A": {"lat": 40.0, "lon": -73.0},
        "B": {"lat": 40.1, "lon": -73.1},
    }
    matrix = TimeDependentTravelMatrix(tmp_path, "unit", locations, dt=60, distance_func=distance_func)

    km_8, t_8 = matrix.travel("A", "B", 8 * 60)
    km_17, t_17 = matrix.travel("A", "B", 17 * 60)
    matrix.save()

    assert km_8 == 10.0
    assert t_8 == 60
    assert km_17 == 10.0
    assert t_17 == 60
    assert calls == [("A", "B")]

    reloaded = TimeDependentTravelMatrix(tmp_path, "unit", locations, dt=60, distance_func=distance_func)
    assert reloaded.travel("A", "B", 12 * 60) == (10.0, 60)
    assert calls == [("A", "B")]


def test_time_dependent_matrix_reflects_midday_speed_bucket(tmp_path):
    matrix = TimeDependentTravelMatrix(
        tmp_path,
        "unit",
        {"A": {"lat": 0, "lon": 0}, "B": {"lat": 0, "lon": 1}},
        dt=10,
        distance_func=lambda _a, _b: 10.0,
    )

    assert matrix.travel("A", "B", 11 * 60)[1] == 20
    assert matrix.travel("A", "B", 12 * 60)[1] == 30
