# NECOFS Provider

Documentation of NECOFS (Northeast Coastal Ocean Forecast System) NetCDF file locations on the WHOI/UMASS servers under `/data/necofs/`, and the daily workflow for processing and publishing forecast files to S3.

## Daily Wave Forecast Processing

The script `process_wave_forecast.sh` runs automatically at 11am EDT via cron. It:

1. Checks that `/data/necofs/NECOFS_ARCHIVES/NECOFS_WAVE_FORECAST.nc` was updated today (source updates ~09:57)
2. Bitrounds and rechunks the file using NCO for better performance and compression:
   - Chunks: `time=48`, `node=34514`, `nele=34514` (optimized for time series extraction)
   - Bitrounding: `keepbits=12` (BitRound) for all wave variables; `keepbits=14` for `zeta`
   - Compression: NetCDF4, deflate level 4
   - Result: ~1.1 GB → ~320 MB
3. Uploads to S3 as `s3://neracoos-necofs-forecast/WAVE/NECOFS_WAVE_FORECAST_YYYYMMDD_br.nc`

Logs are written to `~/bin/process_wave_forecast.log`.

### Requirements

- NCO 5.x in a conda environment named `CLI` at `/data/rsignell/miniforge3/envs/CLI/`
- `rclone` with the `neracoos-necofs-forecast` remote configured in `~/.config/rclone/rclone.conf`
- S3 credentials for the `necofs_forecast_uploader` IAM user in `~/dotenv/gom3_forecast.env`
- `--s3-no-check-bucket` flag required (IAM user has `s3:PutObject` only, not `s3:CreateBucket`)

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
