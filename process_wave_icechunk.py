#!/usr/bin/env python
"""
Append today's bitrounded NECOFS wave forecast to the GOM6 wave icechunk store.

Usage: process_wave_icechunk.py YYYYMMDD
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
ICECHUNK_PREFIX = "WAVE/icechunk/gom6_wave_forecast"
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
    ds = add_ugrid_metadata(ds)
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

    write_mesh_topology(session)
    session.commit(f"appended wave forecast {date}")
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} YYYYMMDD")
        sys.exit(1)
    main(sys.argv[1])
