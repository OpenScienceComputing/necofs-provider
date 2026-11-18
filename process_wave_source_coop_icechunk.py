#!/usr/bin/env python
"""
Append a NECOFS WW3 wave forecast to the FMRC virtual icechunk store on
Source Cooperative.

The virtual chunk references point at the 120-step NetCDFs that
source_coop_push.sh uploads to s3://ocean-icechunks/necofs-waves/netcdf/.
The icechunk repo itself also lives on source.coop. All S3 traffic goes through
the data proxy (path-style); writes use 1-hour credentials obtained from the
service-account key via the AWS CLI and refreshed on demand.

Usage: process_wave_source_coop_icechunk.py YYYYMMDD
"""

import datetime
import json
import subprocess
import sys

import numpy as np
import pandas as pd
import xarray as xr
import zarr
import icechunk
from obstore.store import from_url
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser
try:
    from obspec_utils.registry import ObjectStoreRegistry
except ImportError:
    from virtualizarr.registry import ObjectStoreRegistry

ENDPOINT = "https://data.source.coop"
REGION = "us-west-2"
BUCKET = "ocean-icechunks"
PRODUCT = "necofs-waves"
BUCKET_URL = f"s3://{BUCKET}"
NETCDF_PREFIX = f"{PRODUCT}/netcdf"
NETCDF_URL_PREFIX = f"{BUCKET_URL}/{NETCDF_PREFIX}/"
ICECHUNK_PREFIX = f"{PRODUCT}/icechunk/necofs_wave_forecast"

AWS = "/home/user/rsignell/bin/aws"
# Service account for the necofs-waves product (see source_coop_push.sh for the same pair).
KEY_FILE = "/home/user/rsignell/.source-coop/necofs-waves.key"
ROLE_ARN = "arn:aws:iam::ocean-icechunks--necofs-wave-updates:role/FullAccess"
AWS_ENV = {
    "AWS_REGION": REGION,
    "AWS_ENDPOINT_URL_S3": ENDPOINT,
    "AWS_ENDPOINT_URL_STS": f"{ENDPOINT}/.sts",
    "AWS_ROLE_ARN": ROLE_ARN,
    "AWS_WEB_IDENTITY_TOKEN_FILE": KEY_FILE,
    "HOME": "/home/user/rsignell",
    "PATH": "/usr/bin:/bin",
}

CF_VAR_ATTRS = {
    'hs':          {'standard_name': 'sea_surface_wave_significant_height', 'units': 'm'},
    'wdir':        {'standard_name': 'sea_surface_wave_from_direction', 'units': 'degree'},
    'tpeak':       {'standard_name': 'sea_surface_wave_period_at_variance_spectral_density_maximum',
                    'units': 's'},
    'fp':          {'standard_name': 'sea_surface_wave_frequency_at_variance_spectral_density_maximum',
                    'units': 's-1'},
    'wlen':        {'long_name': 'Mean Wave Length', 'units': 'm'},
    'uwind_speed': {'standard_name': 'eastward_wind', 'units': 'm s-1'},
    'vwind_speed': {'standard_name': 'northward_wind', 'units': 'm s-1'},
    'nv':          {'long_name': 'nodes surrounding element',
                    'cf_role': 'face_node_connectivity', 'start_index': 1},
}

MESH_TOPOLOGY_ATTRS = {
    'cf_role':                'mesh_topology',
    'topology_dimension':     2,
    'node_coordinates':       'lon lat',
    'face_coordinates':       'lonc latc',
    'face_node_connectivity': 'nv',
    'face_dimension':         'nele',
}


def get_credentials():
    """Exchange the service-account key for 1-hour S3 credentials (icechunk refreshes via this)."""
    out = subprocess.run(
        [AWS, "configure", "export-credentials", "--format", "process"],
        env=AWS_ENV, capture_output=True, text=True, check=True,
    ).stdout
    c = json.loads(out)
    return icechunk.S3StaticCredentials(
        access_key_id=c["AccessKeyId"],
        secret_access_key=c["SecretAccessKey"],
        session_token=c["SessionToken"],
        expires_after=datetime.datetime.fromisoformat(c["Expiration"].replace("Z", "+00:00")),
    )


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
    url = f"{NETCDF_URL_PREFIX}NECOFS_WAVE_FORECAST_{date}_br.nc"
    print(f"Opening virtual dataset: {url}")

    # Public product: read the NetCDFs anonymously through the proxy.
    store = from_url(
        BUCKET_URL,
        config={
            "endpoint": ENDPOINT,
            "region": REGION,
            "virtual_hosted_style_request": False,
            "skip_signature": True,
        },
    )
    registry = ObjectStoreRegistry({BUCKET_URL: store})
    ds = open_virtual_dataset(
        url=url,
        parser=HDFParser(),
        registry=registry,
        loadable_variables=["time"],
    )
    ds = add_ugrid_metadata(ds)
    ds = fix_ds(ds)
    print(f"Virtual dataset ready: time={ds.time.values}, steps={len(ds.step)}")
    if len(ds.step) != 120:
        raise SystemExit(f"ERROR: expected 120 steps (5 x 24-step time chunks), got {len(ds.step)}")

    storage = icechunk.s3_storage(
        bucket=BUCKET,
        prefix=ICECHUNK_PREFIX,
        region=REGION,
        endpoint_url=ENDPOINT,
        force_path_style=True,
        get_credentials=get_credentials,
    )
    config = icechunk.RepositoryConfig.default()
    config.set_virtual_chunk_container(
        icechunk.VirtualChunkContainer(
            url_prefix=NETCDF_URL_PREFIX,
            store=icechunk.s3_store(
                region=REGION,
                endpoint_url=ENDPOINT,
                anonymous=True,
                s3_compatible=True,
                force_path_style=True,
            ),
        )
    )
    credentials = icechunk.containers_credentials(
        {NETCDF_URL_PREFIX: icechunk.s3_anonymous_credentials()}
    )

    # Explicit existence check: auth/network errors must fail loudly, not trigger a create.
    if icechunk.Repository.exists(storage):
        repo = icechunk.Repository.open(storage, config, authorize_virtual_chunk_access=credentials)
        session = repo.writable_session("main")
        existing = xr.open_zarr(session.store, consolidated=False, chunks={}).time.values
        if ds.time.values[0] in existing:
            print(f"Forecast {ds.time.values[0]} already in store — nothing to do.")
            return
        ds.virtualize.to_icechunk(session.store, append_dim="time")
        print("Appended to existing icechunk store")
    else:
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
