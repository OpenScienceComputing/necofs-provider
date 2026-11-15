#!/usr/bin/env python
"""
Append today's bitrounded NECOFS Casco Bay ocean forecast to the icechunk FMRC store.

Usage: process_casco_icechunk.py YYYYMMDD
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
ICECHUNK_PREFIX = "OCEAN_CASCO/icechunk/casco_ocean_forecast"
REGION = "us-east-1"

CF_VAR_ATTRS = {
    'zeta':         {'standard_name': 'sea_surface_height_above_geoid', 'units': 'm'},
    'temp':         {'standard_name': 'sea_water_temperature', 'units': 'degrees_C'},
    'salinity':     {'standard_name': 'sea_water_practical_salinity', 'units': '1'},
    'u':            {'standard_name': 'eastward_sea_water_velocity', 'units': 'm s-1'},
    'v':            {'standard_name': 'northward_sea_water_velocity', 'units': 'm s-1'},
    'uwind_speed':  {'standard_name': 'eastward_wind', 'units': 'm s-1'},
    'vwind_speed':  {'standard_name': 'northward_wind', 'units': 'm s-1'},
    'hs':           {'standard_name': 'sea_surface_wave_significant_height', 'units': 'm'},
    'wdir':         {'standard_name': 'sea_surface_wave_from_direction', 'units': 'degree'},
    'tpeak':        {'standard_name': 'sea_surface_wave_period_at_variance_spectral_density_maximum',
                     'units': 's'},
    'wlen':         {'long_name': 'Mean Wave Length', 'units': 'm'},
    'h':            {'standard_name': 'sea_floor_depth_below_geoid', 'units': 'm'},
    'nv':           {'long_name': 'nodes surrounding element',
                     'cf_role': 'face_node_connectivity', 'start_index': 1},
    'siglay':       {'standard_name': 'ocean_sigma_coordinate', 'positive': 'up',
                     'valid_min': -1.0, 'valid_max': 0.0,
                     'formula_terms': 'sigma: siglay eta: zeta depth: h'},
    'siglev':       {'standard_name': 'ocean_sigma_coordinate', 'positive': 'up',
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
    ds.attrs['Conventions'] = 'CF-1.11, UGRID-1.0'
    for var, attrs in CF_VAR_ATTRS.items():
        if var in ds:
            ds[var].attrs.update(attrs)
    for var in ds.data_vars:
        dims = ds[var].dims
        if 'nele' in dims:
            ds[var].attrs.setdefault('mesh', 'mesh_topology')
            ds[var].attrs.setdefault('location', 'face')
            ds[var].attrs.setdefault('coordinates', 'latc lonc')
        elif 'node' in dims:
            ds[var].attrs.setdefault('mesh', 'mesh_topology')
            ds[var].attrs.setdefault('location', 'node')
            ds[var].attrs.setdefault('coordinates', 'lat lon')
    return ds


def write_mesh_topology(session):
    z = zarr.open_group(session.store, mode="r+")
    arr = z.require_array('mesh_topology', shape=(), dtype='int32')
    arr[()] = np.int32(0)
    arr.attrs.update(MESH_TOPOLOGY_ATTRS)


def fix_ds(ds):
    t0 = pd.Timestamp(ds.time.values[0])
    ds = ds.assign_coords(time=pd.date_range(t0, periods=len(ds.time), freq="1h"))
    ds = ds.rename_vars(time="valid_time")
    ds = ds.rename_dims(time="step")
    step = (ds.valid_time - ds.valid_time[0]).assign_attrs({"standard_name": "forecast_period"})
    time = ds.valid_time[0].assign_attrs({"standard_name": "forecast_reference_time"})
    ds = ds.assign_coords(step=step, time=time)
    ds = ds.drop_indexes("valid_time")
    ds = ds.drop_vars("valid_time")
    static_vars = [v for v in ds.data_vars if 'step' not in ds[v].dims]
    static_dict = {v: ds[v] for v in static_vars}
    return ds.drop_vars(static_vars).expand_dims("time").assign(static_dict)


def main(date):
    load_dotenv(os.path.expanduser("~/dotenv/gom3_forecast.env"))

    url = f"{BUCKET_URL}/OCEAN_CASCO/NECOFS_FVCOM_OCEAN_CASCO_FORECAST_{date}_br.nc"
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
    ds = fix_ds(ds)
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
    session.commit(f"appended casco ocean forecast {date}")
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} YYYYMMDD")
        sys.exit(1)
    main(sys.argv[1])
