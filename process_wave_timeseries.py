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
import numpy as np
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

# ── UGRID / CF metadata ───────────────────────────────────────────────────────
CF_VAR_ATTRS = {
    'hs':          {'standard_name': 'sea_surface_wave_significant_height', 'units': 'm',
                    'coordinates': 'lat lon'},
    'wdir':        {'standard_name': 'sea_surface_wave_from_direction', 'units': 'degree',
                    'coordinates': 'lat lon'},
    'tpeak':       {'standard_name': 'sea_surface_wave_period_at_variance_spectral_density_maximum',
                    'units': 's', 'coordinates': 'lat lon'},
    'wlen':        {'long_name': 'Mean Wave Length', 'units': 'm', 'coordinates': 'lat lon'},
    'zeta':        {'standard_name': 'sea_surface_height_above_geoid', 'units': 'm',
                    'coordinates': 'lat lon'},
    'uwind_speed': {'standard_name': 'eastward_wind', 'units': 'm s-1',
                    'coordinates': 'latc lonc'},
    'vwind_speed': {'standard_name': 'northward_wind', 'units': 'm s-1',
                    'coordinates': 'latc lonc'},
    'h':           {'standard_name': 'sea_floor_depth_below_geoid', 'units': 'm',
                    'coordinates': 'lat lon'},
    'nv':          {'long_name': 'nodes surrounding element',
                    'cf_role': 'face_node_connectivity', 'start_index': 1},
    'siglay':      {'standard_name': 'ocean_sigma_coordinate', 'positive': 'up',
                    'valid_min': -1.0, 'valid_max': 0.0,
                    'formula_terms': 'sigma: siglay eta: zeta depth: h'},
    'siglev':      {'standard_name': 'ocean_sigma_coordinate', 'positive': 'up',
                    'valid_min': -1.0, 'valid_max': 0.0,
                    'formula_terms': 'sigma: siglev eta: zeta depth: h'},
}

MESH_TOPOLOGY_ATTRS = {
    'cf_role':                'mesh_topology',
    'topology_dimension':     2,
    'node_coordinates':       'lon lat',
    'face_coordinates':       'lonc latc',
    'face_node_connectivity': 'nv',
    'face_dimension':         'nele',
}


def add_ugrid_metadata(ds):
    """Apply CF and UGRID attributes to the virtual dataset in-place."""
    ds.attrs['Conventions'] = 'CF-1.11, UGRID-1.0'
    for var, attrs in CF_VAR_ATTRS.items():
        if var in ds:
            ds[var].attrs.update(attrs)
    for var in ds.data_vars:
        dims = ds[var].dims
        if 'node' in dims or 'nele' in dims:
            ds[var].attrs.setdefault('mesh', 'mesh_topology')
            ds[var].attrs.setdefault('location', 'face' if 'nele' in dims else 'node')
    return ds


def write_mesh_topology(session):
    """Write the mesh_topology scalar variable directly via zarr after to_icechunk()."""
    z = zarr.open_group(session.store, mode="r+")
    arr = z.require_array('mesh_topology', shape=(), dtype='int32')
    arr[()] = np.int32(0)
    arr.attrs.update(MESH_TOPOLOGY_ATTRS)


HISTORY_STEPS = 24                                     # steps kept per completed forecast day
TAIL_STEPS = 145                                       # actual steps in each _br.nc file


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
    """Convert step dim to flat valid-time dimension using all available steps."""
    t0 = pd.Timestamp(ds.time.values)
    n_steps = ds.sizes['step']
    valid_times = pd.date_range(t0, periods=n_steps, freq="1h")
    ds = ds.drop_vars("time")
    ds = ds.rename_dims({"step": "time"})
    ds = ds.drop_vars("step")
    ds = ds.assign_coords(time=("time", valid_times))
    return ds


def trim_tail(session, steps_to_remove=TAIL_STEPS - HISTORY_STEPS):
    """
    Remove `steps_to_remove` time steps from the end of all time-indexed arrays.
    Default trims the previous day's extended tail from TAIL_STEPS down to HISTORY_STEPS.
    Pass steps_to_remove=TAIL_STEPS to fully undo the last append on a re-run.
    """
    z = zarr.open_group(session.store, mode="r+")
    time_len = z["time"].shape[0]
    trim_to = time_len - steps_to_remove
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
    ds = add_ugrid_metadata(ds)
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

    new_last_time = ds.time.values[-1]

    try:
        repo = icechunk.Repository.open(storage, config, authorize_virtual_chunk_access=credentials)
        session = repo.writable_session("main")
        ds_store = xr.open_zarr(session.store, consolidated=False, chunks={})
        store_last_time = ds_store.time.values[-1]
        if new_last_time > store_last_time:
            trim_tail(session)
            ds.virtualize.to_icechunk(session.store, append_dim="time")
            print("Trimmed tail and appended new forecast")
        else:
            print(f"Re-run detected (store ends {store_last_time}, file ends {new_last_time}) — overwriting last {TAIL_STEPS} steps")
            trim_tail(session, steps_to_remove=TAIL_STEPS)
            ds.virtualize.to_icechunk(session.store, append_dim="time")
    except icechunk.IcechunkError:
        repo = icechunk.Repository.create(storage, config, authorize_virtual_chunk_access=credentials)
        session = repo.writable_session("main")
        ds.virtualize.to_icechunk(session.store)
        print("Created new timeseries store")

    write_mesh_topology(session)
    session.commit(f"timeseries {date}")
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} YYYYMMDD")
        sys.exit(1)
    main(sys.argv[1])
