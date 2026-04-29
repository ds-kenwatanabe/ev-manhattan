from pathlib import Path
from io import StringIO
import multiprocessing as mp
import pandas as pd
import numpy as np
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT / "data" / "raw"
PROC = PROJECT_ROOT / "data" / "processed"
PROC.mkdir(parents=True, exist_ok=True)
NYISO_ZONE_J_PTID = 61761


def _synthetic_price_frame(start, end):
    idx = pd.date_range(start, end, freq="h")
    base = np.full(len(idx), 0.22)
    hours = idx.hour
    base[(hours >= 14) & (hours < 20)] += 0.18
    base[(hours >= 20) & (hours < 23)] += 0.07
    base[(hours >= 0) & (hours < 6)] -= 0.06
    weekday = idx.weekday
    base[weekday < 5] += 0.03
    return pd.DataFrame({"price_usd_per_kwh": base}, index=idx)


def _normalize_nyiso_zone_j(df):
    cols = {str(col).strip().lower(): col for col in df.columns}

    time_col = None
    for candidate in ("time stamp", "timestamp", "datetime", "time"):
        if candidate in cols:
            time_col = cols[candidate]
            break
    if time_col is None:
        raise ValueError(f"NYISO CSV missing timestamp column. Columns: {list(df.columns)}")

    price_col = None
    for candidate in ("lbmp ($/mwh)", "lbmp ($/mwhr)", "lbmp_usd_mwh"):
        if candidate in cols:
            price_col = cols[candidate]
            break
    if price_col is None:
        for col in df.columns:
            if "lbmp" in str(col).lower():
                price_col = col
                break
    if price_col is None:
        raise ValueError(f"NYISO CSV missing LBMP column. Columns: {list(df.columns)}")

    zone_mask = pd.Series(False, index=df.index)
    if "ptid" in cols:
        zone_mask = zone_mask | pd.to_numeric(df[cols["ptid"]], errors="coerce").eq(NYISO_ZONE_J_PTID)
    if "name" in cols:
        names = df[cols["name"]].astype(str).str.upper()
        zone_mask = zone_mask | names.isin(["N.Y.C.", "NYC", "ZONE J", "J"])
    if not zone_mask.any():
        raise ValueError("NYISO CSV did not contain Zone J / NYC rows.")

    out = df.loc[zone_mask, [time_col, price_col]].copy()
    out.columns = ["datetime", "lbmp_usd_mwh"]
    out["datetime"] = pd.to_datetime(out["datetime"])
    out["price_usd_per_kwh"] = pd.to_numeric(out["lbmp_usd_mwh"], errors="coerce") / 1000.0
    out = out.dropna(subset=["datetime", "price_usd_per_kwh"])
    out = (
        out.set_index("datetime")
        .sort_index()
        .groupby(pd.Grouper(freq="h"))["price_usd_per_kwh"]
        .mean()
        .to_frame()
    )
    return out


def _fetch_nyiso_zone_j_day_uncached(day_str, market, cache_path):
    day = pd.Timestamp(day_str)
    ymd = day.strftime("%Y%m%d")
    if market == "rt":
        dataset = "realtime"
        filename = f"{ymd}realtime_zone.csv"
    else:
        dataset = "damlbmp"
        filename = f"{ymd}damlbmp_zone.csv"

    url = f"https://mis.nyiso.com/public/csv/{dataset}/{filename}"
    response = requests.get(url, timeout=8)
    response.raise_for_status()
    raw = pd.read_csv(StringIO(response.text))
    normalized = _normalize_nyiso_zone_j(raw)
    selected = normalized[normalized.index.date == day.date()]
    if len(selected) < 24:
        selected = normalized.resample("h").mean().iloc[:24]
    if len(selected) != 24:
        raise ValueError(f"NYISO returned {len(selected)} hourly prices for {day_str}; expected 24.")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    selected.to_parquet(cache_path)


def _fetch_worker(day_str, market, cache_path, queue):
    try:
        _fetch_nyiso_zone_j_day_uncached(day_str, market, Path(cache_path))
        queue.put(("ok", cache_path))
    except Exception as exc:
        queue.put(("error", str(exc)))


def fetch_nyiso_zone_j_day(day_str, market="dam", cache_dir=PROC, timeout_sec=12):
    """Fetch and cache selected-day NYISO Zone J prices.

    market="dam" uses Day-Ahead LBMP. market="rt" uses Real-Time LBMP.
    Returns a parquet path with hourly `price_usd_per_kwh`.
    """
    day = pd.Timestamp(day_str)
    ymd = day.strftime("%Y%m%d")
    if market == "rt":
        dataset = "realtime"
        filename = f"{ymd}realtime_zone.csv"
    else:
        dataset = "damlbmp"
        filename = f"{ymd}damlbmp_zone.csv"

    cache_path = Path(cache_dir) / f"nyiso_zone_j_{day.strftime('%Y_%m_%d')}_{market}.parquet"
    if cache_path.exists():
        return cache_path

    ctx = mp.get_context("fork")
    queue = ctx.Queue()
    proc = ctx.Process(target=_fetch_worker, args=(day_str, market, str(cache_path), queue))
    proc.start()
    proc.join(timeout_sec)
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        raise TimeoutError(f"NYISO {market} fetch timed out after {timeout_sec} seconds.")
    if queue.empty():
        raise RuntimeError(f"NYISO {market} fetch failed without details.")
    status, payload = queue.get()
    if status == "ok":
        return Path(payload)
    raise RuntimeError(payload)


def load_nyiso_or_synthetic(csv_path=RAW / "nyiso_zone_j_2025_07.csv"):
    if csv_path.exists():
        df = pd.read_csv(csv_path, parse_dates=["datetime"])
        # keep only Zone J (case-insensitive)
        df = df[df["zone"].str.upper().eq("J")].copy()
        # to $/kWh
        if "lbmp_usd_mwh" not in df.columns:
            raise ValueError("CSV must have 'lbmp_usd_mwh' column.")
        df["price_usd_per_kwh"] = df["lbmp_usd_mwh"] / 1000.0
        # enforce hourly frequency and single month
        df = (df
              .set_index("datetime")
              .sort_index()
              .loc["2025-07-01":"2025-07-31 23:59:59"])
        # If multiple series per hour, average them
        df = df.groupby(df.index.floor("H"))["price_usd_per_kwh"].mean().to_frame()
    else:
        # Synthetic: Manhattan summer profile (USD/kWh)
        df = _synthetic_price_frame("2025-07-01", "2025-07-31 23:00:00")
    out = PROC / "nyiso_zone_j_2025_07.parquet"
    df.to_parquet(out)
    return out


if __name__ == "__main__":
    print(load_nyiso_or_synthetic())
