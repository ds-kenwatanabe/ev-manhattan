def speed_multiplier(minute_of_day: int) -> float:
    """Return speed factor ∈ (0,1] by time of day (local).
    Peaks: 7–10 and 16–19 ⇒ slower.
    """
    h = (minute_of_day // 60) % 24
    if 7 <= h < 10 or 16 <= h < 19:
        return 0.7
    if 12 <= h < 14:
        return 0.85
    return 1.0
