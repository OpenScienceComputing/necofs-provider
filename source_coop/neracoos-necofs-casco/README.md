# NECOFS Casco Bay ocean forecast — NetCDF and Icechunk

**[💻 Data access (code)](#how-to-open-it)** · **[📁 NetCDF files](#netcdf-files)** · **[🌊 NECOFS](http://fvcom.smast.umassd.edu/necofs/)**

Daily [NECOFS](http://fvcom.smast.umassd.edu/necofs/) (Northeast Coastal Ocean Forecast
System) Casco Bay ocean forecast, produced by the University of Massachusetts Dartmouth
School for Marine Science and Technology (SMAST) with FVCOM 5.1, and published here as
cloud-optimized NetCDF files plus a virtual [Icechunk](https://icechunk.io) store, so the
whole forecast archive opens in about a second as one lazy `xarray` dataset.

| What | Where |
|---|---|
| **NetCDF4** (one file per forecast, ~3.4 GB) | `https://data.source.coop/ocean-icechunks/neracoos-necofs-casco/netcdf/NECOFS_FVCOM_OCEAN_CASCO_FORECAST_YYYYMMDD_br.nc` |
| **Icechunk repository** (virtual, all forecasts) | `https://data.source.coop/ocean-icechunks/neracoos-necofs-casco/icechunk/casco_ocean_forecast` |

The Icechunk store is **virtual**: it holds only Zarr metadata and byte-range references to
the NetCDF files in `netcdf/`. Both are updated together, once per forecast.

## About the data

- **Model:** FVCOM 5.1 "Casco Bay Case", run by UMass Dartmouth SMAST.
- **Grid:** unstructured triangular mesh, 305,705 nodes and 609,144 elements (`node`,
  `nele`), 10 sigma layers (`siglay`) and 11 sigma levels (`siglev`). Metadata follows
  CF-1.11 and UGRID-1.0.
- **Forecast length:** 144 hourly steps (0–143 h, 6 days) from 00:00 UTC. The provider's
  file contains 145 steps; the last (t+144 h) is dropped so the time axis divides evenly into
  12-step chunks.
- **Cadence:** one new forecast per day. The first forecast in this product is 2026-11-11.
  Days the provider did not deliver are absent.

| Variable | Dimensions | Units | Description |
|---|---|---|---|
| `zeta` | (time, step, node) | m | sea surface height |
| `temp` | (time, step, siglay, node) | °C | water temperature |
| `salinity` | (time, step, siglay, node) | 1 | practical salinity |
| `u`, `v` | (time, step, siglay, nele) | m s⁻¹ | eastward / northward velocity, at element centres |
| `uwind_speed`, `vwind_speed` | (time, step, nele) | m s⁻¹ | eastward / northward wind, at element centres |
| `hs` | (time, step, node) | m | significant wave height |
| `wdir` | (time, step, node) | degree | wave direction (from) |
| `tpeak` | (time, step, node) | s | peak wave period |
| `wlen` | (time, step, node) | m | mean wave length |
| `wet_nodes`, `wet_cells`, `inundation_cells` | (time, step, node / nele) | – | wetting/drying flags |
| `h` | (node) | m | bathymetry (depth below geoid) |
| `lon`, `lat`, `x`, `y` / `lonc`, `latc`, `xc`, `yc` | node / nele | degrees, m | node and element-centre coordinates |
| `siglay`, `siglev` | (siglay / siglev, node) | – | sigma coordinates |
| `nv`, `nbe`, `aw0`, `awx`, `awy` | (three, nele) | – | mesh connectivity and interpolation coefficients |

In the Icechunk store `time` is the **forecast reference time** (one entry per forecast) and
`step` the lead time, so valid time = `time + step`. Time-invariant grid variables are stored
once, without a `time` dimension.

### Chunking and precision

Both formats use chunks of 12 time steps × 19,107 nodes (or 38,072 elements) × 1 sigma layer,
deflate level 4. Fields are **bit-rounded** before compression: 16 mantissa bits for `u`/`v`
and 12 for `zeta`, `temp`, `salinity`, winds and waves (12 bits is about 3 decimal digits).
This is lossy; if you need the provider's full-precision values, contact them.

## How to open it

**Required:** Python ≥ 3.12, `icechunk` ≥ 2 (tested with 2.0.5), `xarray`, `zarr`. No credentials are needed.

```python
import icechunk
import xarray as xr

ENDPOINT = "https://data.source.coop"
PREFIX = "s3://ocean-icechunks/neracoos-necofs-casco/netcdf/"

storage = icechunk.s3_storage(
    bucket="ocean-icechunks",
    prefix="neracoos-necofs-casco/icechunk/casco_ocean_forecast",
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

# latest forecast: surface temperature at one node, first 24 hours
ds.temp.isel(time=-1, siglay=0, node=1000, step=slice(0, 24)).plot()
```

The `endpoint_url` / `force_path_style` settings are required because Source Cooperative's
data proxy only supports path-style requests.

## NetCDF files

Each `netcdf/NECOFS_FVCOM_OCEAN_CASCO_FORECAST_YYYYMMDD_br.nc` is a self-contained NetCDF4
file for the forecast starting on that date (UTC). `sigma_layer` / `sigma_level` in the files
are `siglay` / `siglev` renamed to avoid a name clash with their dimensions (the Icechunk
store renames them back). They open directly with `xarray`:

```python
ds = xr.open_dataset(
    "https://data.source.coop/ocean-icechunks/neracoos-necofs-casco/netcdf/NECOFS_FVCOM_OCEAN_CASCO_FORECAST_20261113_br.nc"
)
```

## How this was built

A daily cron job ([`process_casco_source_coop.sh`](https://github.com/OpenScienceComputing/necofs-provider/blob/main/process_casco_source_coop.sh))
polls the provider's file; for each new forecast it (1) clips to 144 steps, bit-rounds and
rechunks with [NCO](https://nco.sourceforge.net) `ncks`, (2) uploads the NetCDF to `netcdf/`,
and (3) appends a virtual dataset to the Icechunk repository with
[VirtualiZarr](https://virtualizarr.readthedocs.io)
([`process_casco_source_coop_icechunk.py`](https://github.com/OpenScienceComputing/necofs-provider/blob/main/process_casco_source_coop_icechunk.py)).
Code and notes: <https://github.com/OpenScienceComputing/necofs-provider>.

## Credits

- **Data:** NECOFS, University of Massachusetts Dartmouth School for Marine Science and
  Technology (SMAST), <http://fvcom.smast.umassd.edu/necofs/>. Please acknowledge NECOFS/UMassD when using these data.
- **Packaging:** Rich Signell, built with [Icechunk](https://icechunk.io),
  [VirtualiZarr](https://virtualizarr.readthedocs.io) and [Xarray](https://xarray.dev).
