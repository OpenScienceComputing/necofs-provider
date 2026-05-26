#!/usr/bin/env python
"""
Build the GOM6 wave best-estimate timeseries Icechunk store.

Discovers all NECOFS_WAVE_FORECAST_{DATE}_br.nc files on S3, then assembles
a continuous best-estimate timeseries by taking the most recent forecast data
available for each valid hour:

  - newest file  : all steps  (full forecast tail)
  - each older file : steps 0 .. gap, where gap = hours until the next file's
                      reference time (handles missing days automatically)

Result: gapless, uniform 1-hour timeseries with no hardwired dates or step counts.

Usage:
    python build_wave_best_timeseries.py [--dry-run] [--lookback N]

    --dry-run     Print the contribution plan without writing to Icechunk.
    --lookback N  Only consider the N most recent files (default: all).
"""

import argparse
import os
import re
import sys

import boto3
import icechunk
import numpy as np
import pandas as pd
import xarray as xr
import zarr
from dotenv import load_dotenv
from obstore.store import from_url
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import HDFParser

try:
    from obspec_utils.registry import ObjectStoreRegistry
except ImportError:
    from virtualizarr.registry import ObjectStoreRegistry

load_dotenv(os.path.expanduser("~/dotenv/gom3_forecast.env"))

BUCKET        = "neracoos-necofs-forecast"
BUCKET_URL    = f"s3://{BUCKET}"
REGION        = "us-east-1"
SOURCE_PREFIX = "WAVE/"
FILE_PATTERN  = re.compile(r"NECOFS_WAVE_FORECAST_(\d{8})_br\.nc$")
IC_PREFIX     = "WAVE/icechunk/gom6_wave_timeseries"

CF_VAR_ATTRS = {
    "hs":          {"standard_name": "sea_surface_wave_significant_height",
                    "units": "m", "coordinates": "lat lon"},
    "wdir":        {"standard_name": "sea_surface_wave_from_direction",
                    "units": "degree", "coordinates": "lat lon"},
    "tpeak":       {"standard_name": "sea_surface_wave_period_at_variance_spectral_density_maximum",
                    "units": "s", "coordinates": "lat lon"},
    "wlen":        {"long_name": "Mean Wave Length", "units": "m",
                    "coordinates": "lat lon"},
    "zeta":        {"standard_name": "sea_surface_height_above_geoid",
                    "units": "m", "coordinates": "lat lon"},
    "uwind_speed": {"standard_name": "eastward_wind", "units": "m s-1",
                    "coordinates": "latc lonc"},
    "vwind_speed": {"standard_name": "northward_wind", "units": "m s-1",
                    "coordinates": "latc lonc"},
    "h":           {"standard_name": "sea_floor_depth_below_geoid",
                    "units": "m", "coordinates": "lat lon"},
    "nv":          {"long_name": "nodes surrounding element",
                    "cf_role": "face_node_connectivity", "start_index": 1},
}

MESH_TOPOLOGY_ATTRS = {
    "cf_role":                "mesh_topology",
    "topology_dimension":     2,
    "node_coordinates":       "lon lat",
    "face_coordinates":       "lonc latc",
    "face_node_connectivity": "nv",
    "face_dimension":         "nele",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def list_forecast_files(lookback=None):
    """
    Return sorted list of (date: pd.Timestamp, url: str) for all _br.nc files.
    If lookback is given, return only the N most recent.
    """
    s3        = boto3.client("s3", region_name=REGION)
    paginator = s3.get_paginator("list_objects_v2")
    files = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=SOURCE_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            m   = FILE_PATTERN.search(key)
            if m:
                date = pd.Timestamp(m.group(1))
                files.append((date, f"{BUCKET_URL}/{key}"))
    files.sort()
    if lookback is not None:
        files = files[-lookback:]
    return files


def compute_contributions(dates):
    """
    For each file (oldest-first), return the number of 1-hour steps to use.
    Newest file → None (meaning all steps).
    Older files  → integer hours between this file's and the next file's reference time.
    """
    contributions = []
    for i, date in enumerate(dates):
        if i == len(dates) - 1:
            contributions.append(None)          # newest: full forecast
        else:
            gap = int((dates[i + 1] - date).total_seconds() // 3600)
            contributions.append(gap)
    return contributions


def add_ugrid_metadata(ds):
    ds.attrs["Conventions"] = "CF-1.11, UGRID-1.0"
    for var, attrs in CF_VAR_ATTRS.items():
        if var in ds:
            ds[var].attrs.update(attrs)
    for var in ds.data_vars:
        dims = ds[var].dims
        if "node" in dims or "nele" in dims:
            ds[var].attrs.setdefault("mesh", "mesh_topology")
            ds[var].attrs.setdefault("location", "face" if "nele" in dims else "node")
    return ds


def fix_time(ds):
    """Snap times to exact hourly grid; reshape to (step, ...) with scalar time coord."""
    t0 = pd.Timestamp(ds.time.values[0])
    ds = ds.assign_coords(time=pd.date_range(t0, periods=len(ds.time), freq="1h"))
    ds = ds.rename_vars(time="valid_time")
    ds = ds.rename_dims(time="step")
    step = (ds.valid_time - ds.valid_time[0]).assign_attrs(
        {"standard_name": "forecast_period"}
    )
    time = ds.valid_time[0].assign_attrs(
        {"standard_name": "forecast_reference_time"}
    )
    ds = ds.assign_coords(step=step, time=time)
    ds = ds.drop_indexes("valid_time")
    ds = ds.drop_vars("valid_time")
    return ds


def to_timeseries(ds):
    """Flatten step → time (valid time as coordinate)."""
    t0          = pd.Timestamp(ds.time.values)
    n_steps     = ds.sizes["step"]
    valid_times = pd.date_range(t0, periods=n_steps, freq="1h")
    ds = ds.drop_vars("time")
    ds = ds.rename_dims({"step": "time"})
    ds = ds.drop_vars("step")
    ds = ds.assign_coords(time=("time", valid_times))
    return ds


def open_preprocess(url, registry):
    """Open a virtual dataset and preprocess (rename, metadata, fix time). Does not slice."""
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
    return ds


def get_time_chunk_size(ds):
    """
    Detect the chunk size along the time axis from a virtual dataset's ManifestArrays.
    Returns None if not detectable.
    """
    for var in ds.data_vars.values():
        if "time" in var.dims and hasattr(var.data, "chunks"):
            return var.data.chunks[list(var.dims).index("time")]
    return None


def delete_icechunk_repo():
    """Delete all S3 objects under IC_PREFIX so the store can be recreated cleanly."""
    s3        = boto3.client("s3", region_name=REGION)
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=IC_PREFIX):
        for obj in page.get("Contents", []):
            keys.append({"Key": obj["Key"]})
    if not keys:
        return
    for i in range(0, len(keys), 1000):
        s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys[i:i + 1000]})
    print(f"Deleted {len(keys)} object(s) from s3://{BUCKET}/{IC_PREFIX}")


def write_mesh_topology(session):
    z   = zarr.open_group(session.store, mode="r+")
    arr = z.require_array("mesh_topology", shape=(), dtype="int32")
    arr[()] = np.int32(0)
    arr.attrs.update(MESH_TOPOLOGY_ATTRS)


# ── main ──────────────────────────────────────────────────────────────────────

def main(dry_run=False, lookback=None):
    files = list_forecast_files(lookback=lookback)
    if not files:
        print("No forecast files found on S3.")
        sys.exit(1)

    dates         = [f[0] for f in files]
    urls          = [f[1] for f in files]
    contributions = compute_contributions(dates)

    print(f"Found {len(files)} forecast file(s) — contribution plan:")
    for date, url, n in zip(dates, urls, contributions):
        steps_str = "all steps" if n is None else f"{n} step(s)"
        print(f"  {date.strftime('%Y-%m-%d')}  →  {steps_str}  ({url})")

    if dry_run:
        print("\n--dry-run: stopping before any writes.")
        return

    # Build shared object-store registry
    store    = from_url(BUCKET_URL, region=REGION)
    registry = ObjectStoreRegistry({BUCKET_URL: store})

    # Open all files (preprocessing only, no slicing yet)
    print("Opening virtual datasets ...")
    datasets = []
    for date, url in zip(dates, urls):
        print(f"  {date.strftime('%Y-%m-%d')}  {url}")
        datasets.append(open_preprocess(url, registry))

    # Detect time chunk size from the first dataset's ManifestArrays
    time_chunk = get_time_chunk_size(datasets[0])
    if time_chunk:
        print(f"Detected time chunk size: {time_chunk} hour(s)")

    def chunk_align(n, chunk):
        return (n // chunk) * chunk if chunk else n

    # Compute chunk-aligned step counts.
    # None (newest file) → align its actual total steps down to chunk boundary.
    aligned_steps = []
    for ds, n in zip(datasets, contributions):
        raw = n if n is not None else ds.sizes["time"]
        aligned_steps.append(chunk_align(raw, time_chunk))

    # Slice each dataset and report
    print("\nSlicing:")
    slices = []
    for date, ds, n in zip(dates, datasets, aligned_steps):
        sliced = ds.isel(time=slice(0, n))
        t0 = pd.Timestamp(sliced.time.values[0])
        t1 = pd.Timestamp(sliced.time.values[-1])
        print(f"  {date.strftime('%Y-%m-%d')}: {n} steps  {t0} .. {t1}")
        slices.append(sliced)

    # Concatenate along time.
    # data_vars/coords='minimal' keeps static variables (h, nv, lon, …) from the
    # first slice rather than attempting to concatenate them along time.
    print("\nConcatenating virtual slices ...")
    combined = xr.concat(
        slices,
        dim="time",
        data_vars="minimal",
        coords="minimal",
        compat="override",
    )
    t_start = pd.Timestamp(combined.time.values[0])
    t_end   = pd.Timestamp(combined.time.values[-1])
    print(f"Combined: {combined.sizes['time']} steps  {t_start} .. {t_end}")

    # Open or create the Icechunk store
    config = icechunk.RepositoryConfig.default()
    config.set_virtual_chunk_container(
        icechunk.VirtualChunkContainer(
            url_prefix=f"{BUCKET_URL}/",
            store=icechunk.s3_store(region=REGION),
        )
    )
    storage     = icechunk.s3_storage(bucket=BUCKET, prefix=IC_PREFIX, region=REGION, from_env=True)
    credentials = icechunk.containers_credentials(
        {f"{BUCKET_URL}/": icechunk.s3_credentials(anonymous=False)}
    )
    delete_icechunk_repo()
    repo = icechunk.Repository.create(storage, config, authorize_virtual_chunk_access=credentials)
    print("Created fresh Icechunk store.")

    session = repo.writable_session("main")
    print("Writing to Icechunk ...")
    combined.virtualize.to_icechunk(session.store)
    write_mesh_topology(session)

    commit_msg = (
        f"best-estimate timeseries: {len(files)} file(s), "
        f"{t_start.strftime('%Y-%m-%d')} .. {t_end.strftime('%Y-%m-%d')}"
    )
    snap = session.commit(commit_msg)
    print(f"Done.  snapshot={snap}")
    print(f"Store: s3://{BUCKET}/{IC_PREFIX}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run",  action="store_true", help="Print plan without writing")
    parser.add_argument("--lookback", type=int, default=None, metavar="N",
                        help="Use only the N most recent files")
    args = parser.parse_args()
    main(dry_run=args.dry_run, lookback=args.lookback)
