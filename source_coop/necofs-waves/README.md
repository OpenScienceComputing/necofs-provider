# NECOFS wave forecast — NetCDF and Icechunk

**[💻 Data access (code)](#how-to-open-it)** · **[📁 NetCDF files](#netcdf-files)** · **[🌊 NECOFS](http://fvcom.smast.umassd.edu/necofs/)**

Daily [NECOFS](http://fvcom.smast.umassd.edu/necofs/) (Northeast Coastal Ocean Forecast System)
wave forecast for the Gulf of Maine region, produced by the University of Massachusetts
Dartmouth School for Marine Science and Technology (SMAST) and published here as
cloud-optimized NetCDF files plus a virtual
[Icechunk](https://icechunk.io) store, so the whole forecast archive opens in about a second
as one lazy `xarray` dataset.

| What | Where |
|---|---|
| **NetCDF4** (one file per forecast) | `https://data.source.coop/ocean-icechunks/necofs-waves/netcdf/NECOFS_WAVE_FORECAST_YYYYMMDD_br.nc` |
| **Icechunk repository** (virtual, all forecasts) | `https://data.source.coop/ocean-icechunks/necofs-waves/icechunk/necofs_wave_forecast` |

The Icechunk store is **virtual**: it holds only Zarr metadata and byte-range references to
the NetCDF files in `netcdf/`. Both are updated together, once per forecast.

## About the data

- **Model:** WaveWatch III (WW3), run by UMass Dartmouth SMAST. Before 2026-11-17 this
  forecast came from FVCOM-SWAN; those earlier forecasts are **not** in this product (different variables).
- **Grid:** unstructured triangular mesh, 207,081 nodes and 371,290 elements (`node`, `nele`
  dimensions, connectivity in `nv`). Metadata follows CF-1.11 and UGRID-1.0.
- **Forecast length:** 120 hourly steps (0–119 h) from 00:00 UTC. The provider's file
  contains 121 steps; the last (t+120 h) is dropped so the time axis divides evenly into
  24-step chunks.
- **Cadence:** one new forecast per day. The first forecast in this product is 2026-11-17.
  Days the provider did not deliver are absent.

| Variable | Dimensions | Units | Description |
|---|---|---|---|
| `hs` | (time, step, node) | m | significant wave height |
| `wdir` | (time, step, node) | degree | wave direction (from) |
| `tpeak` | (time, step, node) | s | peak wave period |
| `fp` | (time, step, node) | s⁻¹ | peak wave frequency |
| `wlen` | (time, step, node) | m | mean wave length |
| `uwind_speed` | (time, step, nele) | m s⁻¹ | eastward wind, at element centres |
| `vwind_speed` | (time, step, nele) | m s⁻¹ | northward wind, at element centres |
| `lon`, `lat` / `lonc`, `latc` | node / nele | degrees | node and element-centre coordinates |
| `nv` | (three, nele) | – | nodes surrounding each element (1-based) |

In the Icechunk store `time` is the **forecast reference time** (one entry per forecast) and
`step` the lead time, so valid time = `time + step`.

### Chunking and precision

Both formats use chunks of 24 time steps × 34,514 nodes (or elements), deflate level 4.
Wave and wind fields are **bit-rounded** to 12 significant mantissa bits (about 3 decimal
digits) before compression, which cuts a forecast from ~870 MB to ~310 MB. This is lossy; if
you need the provider's full-precision values, contact them.

## How to open it

**Required:** Python ≥ 3.12, `icechunk` ≥ 2 (tested with 2.0.5), `xarray`, `zarr`. No credentials are needed.

```python
import icechunk
import xarray as xr

ENDPOINT = "https://data.source.coop"
PREFIX = "s3://ocean-icechunks/necofs-waves/netcdf/"

storage = icechunk.s3_storage(
    bucket="ocean-icechunks",
    prefix="necofs-waves/icechunk/necofs_wave_forecast",
    region="us-west-2",
    endpoint_url=ENDPOINT,
    force_path_style=True,
    anonymous=True,
)
config = icechunk.RepositoryConfig.default()
config.set_virtual_chunk_container(
    icechunk.VirtualChunkContainer(
        url_prefix=PREFIX,
        store=icechunk.s3_store(
            region="us-west-2", endpoint_url=ENDPOINT,
            anonymous=True, s3_compatible=True, force_path_style=True,
        ),
    )
)
repo = icechunk.Repository.open(
    storage, config,
    authorize_virtual_chunk_access=icechunk.containers_credentials(
        {PREFIX: icechunk.s3_anonymous_credentials()}
    ),
)
ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False)

# latest forecast, significant wave height at one node
ds.hs.isel(time=-1, node=1000).plot()
```

The `endpoint_url` / `force_path_style` settings are required because Source Cooperative's
data proxy only supports path-style requests.

## NetCDF files

Each `netcdf/NECOFS_WAVE_FORECAST_YYYYMMDD_br.nc` is a self-contained NetCDF4 file for the
forecast starting on that date (UTC). They open directly with `xarray`:

```python
ds = xr.open_dataset(
    "https://data.source.coop/ocean-icechunks/necofs-waves/netcdf/NECOFS_WAVE_FORECAST_20261117_br.nc"
)
```

## How this was built

A daily cron job ([`process_wave_source_coop.sh`](https://github.com/OpenScienceComputing/necofs-provider/blob/main/process_wave_source_coop.sh))
polls the provider's file; for each new forecast it (1) clips to 120 steps, bit-rounds and
rechunks with [NCO](https://nco.sourceforge.net) `ncks`, (2) uploads the NetCDF to `netcdf/`,
and (3) appends a virtual dataset to the Icechunk repository with
[VirtualiZarr](https://virtualizarr.readthedocs.io)
([`process_wave_source_coop_icechunk.py`](https://github.com/OpenScienceComputing/necofs-provider/blob/main/process_wave_source_coop_icechunk.py)).
Code and notes: <https://github.com/OpenScienceComputing/necofs-provider>.

## Credits

- **Data:** NECOFS, University of Massachusetts Dartmouth School for Marine Science and
  Technology (SMAST), <http://fvcom.smast.umassd.edu/necofs/>. Please acknowledge NECOFS/UMassD when using these data.
- **Packaging:** Rich Signell, built with [Icechunk](https://icechunk.io),
  [VirtualiZarr](https://virtualizarr.readthedocs.io) and [Xarray](https://xarray.dev).
