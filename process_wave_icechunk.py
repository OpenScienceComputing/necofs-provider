#!/usr/bin/env python
"""
Append today's bitrounded NECOFS wave forecast to the GOM6 wave icechunk store.

Usage: process_wave_icechunk.py YYYYMMDD
"""

import sys
import os
from dotenv import load_dotenv
import pandas as pd  # still used in fix_ds for date_range
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
ICECHUNK_PREFIX = "WAVE/icechunk/gom6_wave_forecast"
REGION = "us-east-1"


def fix_ds(ds):
    # Snap to exact hourly steps (eliminates float imprecision from MJD encoding)
    t0 = pd.Timestamp(ds.time.values[0])
    ds = ds.assign_coords(time=pd.date_range(t0, periods=len(ds.time), freq="1h"))
    # Convert to CF forecast model: scalar reference time + step dimension
    ds = ds.rename_vars(time="valid_time")
    ds = ds.rename_dims(time="step")
    step = (ds.valid_time - ds.valid_time[0]).assign_attrs({"standard_name": "forecast_period"})
    time = ds.valid_time[0].assign_attrs({"standard_name": "forecast_reference_time"})
    ds = ds.assign_coords(step=step, time=time)
    ds = ds.drop_indexes("valid_time")
    ds = ds.drop_vars("valid_time")
    return ds


def main(date):
    load_dotenv(os.path.expanduser("~/dotenv/gom3_forecast.env"))

    url = f"{BUCKET_URL}/WAVE/NECOFS_WAVE_FORECAST_{date}_br.nc"
    print(f"Opening virtual dataset: {url}")

    store = from_url(BUCKET_URL, region=REGION)
    registry = ObjectStoreRegistry({BUCKET_URL: store})
    # sigma_layer/sigma_level are siglay/siglev renamed in the file to avoid a
    # VirtualiZarr parser bug (variables sharing a name with their first dimension)
    ds = open_virtual_dataset(
        url=url,
        parser=HDFParser(),
        registry=registry,
        loadable_variables=["time"],
    )
    ds = ds.rename_vars({"sigma_layer": "siglay", "sigma_level": "siglev"})
    ds = fix_ds(ds)
    ds = ds.expand_dims("time")  # promote scalar time coord to 1-element dimension
    print(f"Virtual dataset ready: time={ds.time.values}, steps={len(ds.step)}")

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
        ds.virtualize.to_icechunk(session.store, append_dim="time")
        print("Appended to existing icechunk store")
    except icechunk.IcechunkError:
        repo = icechunk.Repository.create(storage, config, authorize_virtual_chunk_access=credentials)
        session = repo.writable_session("main")
        ds.virtualize.to_icechunk(session.store)
        print("Created new icechunk store")

    session.commit(f"appended wave forecast {date}")
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} YYYYMMDD")
        sys.exit(1)
    main(sys.argv[1])
