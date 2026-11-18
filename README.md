# NECOFS Provider

Documentation of NECOFS (Northeast Coastal Ocean Forecast System) NetCDF file locations on the WHOI/UMASS servers under `/data/necofs/`, and the daily workflow for processing and publishing forecast files to S3.

## Wave Forecast → Source Cooperative (`ocean-icechunks/necofs-waves`)

UMass Dartmouth switched the wave forecast from FVCOM-SWAN to **WW3** (first WW3 forecast
published here: 2026-11-17). The WW3 file has no `zeta`, `h`, `siglay`/`siglev` or static
grid variables (`x`, `y`, `xc`, `yc`, `nbe`, `aw*`), and adds `fp` (peak frequency), so it is
published to a new product instead of the old S3 stores:
<https://source.coop/ocean-icechunks/necofs-waves>

```
s3://ocean-icechunks/necofs-waves/
├── README.md                          ← copy in source_coop/necofs-waves/README.md
├── netcdf/NECOFS_WAVE_FORECAST_YYYYMMDD_br.nc
└── icechunk/necofs_wave_forecast      ← FMRC virtual icechunk (time = reference time, step = lead)
```

`process_wave_source_coop.sh` runs from cron hourly 08:00–22:00 (idempotent; skips if the
forecast `time[0]` matches `~/.necofs_last_wave_sc_time`). For each new forecast it:

1. Clips to 120 steps (`-d time,0,119`) — the source has 121, which would break the regular
   24-step time chunk grid — and bitrounds/rechunks with `ncks`:
   - Chunks: `time=24`, `node=34514`, `nele=34514`
   - Bitrounding: `keepbits=12` (BitRound) for `hs`, `wdir`, `tpeak`, `wlen`, `fp`, `uwind_speed`, `vwind_speed`
   - Compression: NetCDF4, deflate level 4 (~870 MB → ~310 MB)
2. Uploads the NetCDF to `netcdf/` with `source_coop_push.sh` (`aws s3 cp`; skips existing keys)
3. Appends a virtual dataset to the Icechunk repo with `process_wave_source_coop_icechunk.py`
   (refuses anything other than 120 steps)

Logs: `~/bin/process_wave_source_coop.log`.

### Requirements

- NCO 5.x in the `CLI` conda env; Python env `icechunk` (icechunk, virtualizarr, obstore, xarray)
- AWS CLI ≥ 2.13 at `~/bin/aws` (rclone 1.57 does not work with the source.coop proxy)
- Service account `ocean-icechunks--necofs-wave-updates`; API key at `~/.source-coop/necofs-waves.key`
  (mode 0600), exchanged for 1-hour credentials via web identity against
  `https://data.source.coop/.sts`. `source_coop_push.sh` takes `SC_KEY_FILE` / `SC_ROLE_ARN`
  overrides (defaults are the Casco account).

### Retired: FVCOM-SWAN wave pipeline

`process_wave_forecast.sh` (bitround → `s3://neracoos-necofs-forecast/WAVE/` → icechunk
stores `gom6_wave_forecast` / `gom6_wave_timeseries`) is no longer scheduled. It fails at
`ncks` on the WW3 file, and its stores are schema-incompatible. They remain frozen at the
2026-10-05 forecast; forecasts from 2026-10-06 to 2026-11-16 were not captured.

## Active Wave Model Output

### GOM6 Wave — Monthly aggregated files (most current)
- **Path:** `/data/necofs/data5/gom6_wave_output/{YYYY}/gom6_{YYYYMM}.nc`
- **Last updated:** 2025-12 (`gom6_202511.nc`)
- **Coverage:** 2023–present, one file per month
- **Notes:** Monthly files built by concatenation scripts (`nckc_common{YYYYMM}.sh`) in the parent directory

### GOM6 Wave VM — Real-time forecast run
- **Path:** `/data/necofs/data5/NECOFS_NEW/gom6_wave_vm/output/`
- **Last updated:** 2025-07-16
- **Key files:**
  - `gom6_for_0001.nc` — GOM6 wave forecast
  - `gom6_col_0001.nc` — GOM6 wave collocated output
  - `necofs_swave_{date}.nc` — swave output
  - `gom6_wave_{date}.nc` — wave output
- **Prep/nesting input:** `/data/necofs/data5/NECOFS_NEW/gom6_wave_vm/prep_ww3_glb_nest/gom6_nest.nc`

### GOM6 Wave VM1 — Alternate/experimental run
- **Path:** `/data/necofs/NECOFS_NEW/gom6_wave_vm1/output/`
- **Last updated:** 2025-07-10
- **Key files:** `casco_nest.nc`

## Older / Archive Locations

| Path | Last NC file | Notes |
|---|---|---|
| `/data/necofs/NECOFS_NEW/gom6_wave_vm_old1/output/` | 2025-04-27 | Previous VM run |
| `/data/necofs/NECOFS_NEW/gom6_wave_vm_old0/output/` | 2025-03-06 | Older VM run |
| `/data/necofs/data5/gom3_wave_output/` | 2023-03-16 | GOM3 wave archive |
| `/data/necofs/NECOFS_NEW/gom6_wave/output/` | 2023-11-27 | Earlier GOM6 wave run |
| `/data/necofs/NECOFS_NC/NECOFS_GOM6_FORECAST.nc` | 2023-10-27 | Aggregated GOM6 forecast (stale) |
| `/data/necofs/NECOFS_NC/NECOFS_GOM3_FORECAST.nc` | 2024-02-20 | Aggregated GOM3 forecast (stale) |

## Directory Tree Summary

```
/data/necofs/
├── data5/
│   ├── gom6_wave_output/{YYYY}/gom6_{YYYYMM}.nc   ← MOST CURRENT monthly wave files
│   └── NECOFS_NEW/
│       ├── gom6_wave_vm/output/                    ← active real-time wave run
│       └── gom6_wave_vm_test/                      ← test run
├── NECOFS_NEW/
│   ├── gom6_wave_vm1/output/                       ← alternate real-time run
│   ├── gom6_wave_vm_old{0,1}/output/               ← archived runs
│   └── gom6_wave/output/                           ← older run (2023)
└── NECOFS_NC/
    ├── NECOFS_GOM6_FORECAST.nc                     ← stale aggregated file
    └── NECOFS_GOM3_FORECAST.nc                     ← stale aggregated file
```
