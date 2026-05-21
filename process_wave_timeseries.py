#!/usr/bin/env python
"""
Append today's NECOFS wave forecast to the GOM6 wave timeseries icechunk store.

On each run the previous day's extended tail is trimmed to HISTORY_STEPS,
then today's forecast (TAIL_STEPS) is appended — O(1) regardless of history length.

Store layout: flat time dimension of actual valid datetimes, uniform 1-hour spacing.
  completed days: HISTORY_STEPS each
  latest day:     TAIL_STEPS (largest multiple of TIME_CHUNK that fits in 145)

Usage: process_wave_timeseries.py YYYYMMDD
"""

import sys
import os
import zarr
from dotenv import load_dotenv
import pandas as pd
import xarray as xr
import icechunk
from obstore.store import from_url
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
try:
    from obspec_utils.registry import ObjectStoreRegistry
except ImportError:
    from virtualizarr.registry import ObjectStoreRegistry

BUCKET = "neracoos-necofs-forecast"
BUCKET_URL = f"s3://{BUCKET}"
ICECHUNK_PREFIX = "WAVE/icechunk/gom6_wave_timeseries"
REGION = "us-east-1"

TIME_CHUNK = 24                                        # time chunk size in _br.nc files
HISTORY_STEPS = 24                                     # steps kept per completed forecast day
TAIL_STEPS = (145 // TIME_CHUNK) * TIME_CHUNK          # = 144, largest aligned tail for latest file


def fix_time(ds):
    """Snap to exact hourly steps and convert to step dimension with scalar reference time."""
    t0 = pd.Timestamp(ds.time.values[0])
    ds = ds.assign_coords(time=pd.date_range(t0, periods=len(ds.time), freq="1h"))
    ds = ds.rename_vars(time="valid_time")
    ds = ds.rename_dims(time="step")
    step = (ds.valid_time - ds.valid_time[0]).assign_attrs({"standard_name": "forecast_period"})
    time = ds.valid_time[0].assign_attrs({"standard_name": "forecast_reference_time"})
    ds = ds.assign_coords(step=step, time=time)
    ds = ds.drop_indexes("valid_time")
    ds = ds.drop_vars("valid_time")
    return ds


def to_timeseries(ds):
    """Convert step dim to flat valid-time dimension using TAIL_STEPS steps."""
    ds = ds.isel(step=slice(0, TAIL_STEPS))
    t0 = pd.Timestamp(ds.time.values)
    valid_times = pd.date_range(t0, periods=len(ds.step), freq="1h")
    ds = ds.drop_vars("time")
    ds = ds.rename_dims({"step": "time"})
    ds = ds.drop_vars("step")
    ds = ds.assign_coords(time=("time", valid_times))
    return ds


def trim_tail(session):
    """
    Trim the previous day's extended tail to HISTORY_STEPS.
    Resizes all time-indexed arrays from current length to (current - TAIL_STEPS + HISTORY_STEPS).
    """
    z = zarr.open_group(session.store, mode="r+")
    time_len = z["time"].shape[0]
    trim_to = time_len - (TAIL_STEPS - HISTORY_STEPS)
    if trim_to >= time_len:
        return
    for name in z.array_keys():
        arr = z[name]
        if arr.ndim > 0 and arr.shape[0] == time_len:
            arr.resize((trim_to,) + arr.shape[1:])
    print(f"Trimmed tail: {time_len} → {trim_to} time steps")


def main(date):
    load_dotenv(os.path.expanduser("~/dotenv/gom3_forecast.env"))

    url = f"{BUCKET_URL}/WAVE/NECOFS_WAVE_FORECAST_{date}_br.nc"
    print(f"Opening virtual dataset: {url}")

    store = from_url(BUCKET_URL, region=REGION)
    registry = ObjectStoreRegistry({BUCKET_URL: store})
    ds = open_virtual_dataset(
        url=url,
        parser=HDFParser(),
        registry=registry,
        loadable_variables=["time"],
    )
    ds = ds.rename_vars({"sigma_layer": "siglay", "sigma_level": "siglev"})
    ds = fix_time(ds)
    ds = to_timeseries(ds)
    print(f"Virtual dataset: {len(ds.time)} steps, {ds.time.values[0]} → {ds.time.values[-1]}")

    storage = icechunk.s3_storage(
        bucket=BUCKET,
        prefix=ICECHUNK_PREFIX,
        region=REGION,
        from_env=True,
    )
    config = icechunk.RepositoryConfig.default()
    config.set_virtual_chunk_container(
        icechunk.VirtualChunkContainer(
            url_prefix=f"{BUCKET_URL}/",
            store=icechunk.s3_store(region=REGION),
        )
    )
    credentials = icechunk.containers_credentials(
        {f"{BUCKET_URL}/": icechunk.s3_credentials(anonymous=False)}
    )

    try:
        repo = icechunk.Repository.open(storage, config, authorize_virtual_chunk_access=credentials)
        session = repo.writable_session("main")
        trim_tail(session)
        ds.virtualize.to_icechunk(session.store, append_dim="time")
        print("Trimmed tail and appended new forecast")
    except icechunk.IcechunkError:
        repo = icechunk.Repository.create(storage, config, authorize_virtual_chunk_access=credentials)
        session = repo.writable_session("main")
        ds.virtualize.to_icechunk(session.store)
        print("Created new timeseries store")

    session.commit(f"appended timeseries {date}")
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} YYYYMMDD")
        sys.exit(1)
    main(sys.argv[1])
