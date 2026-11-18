#!/bin/bash
# Cron job: publish each new NECOFS WW3 wave forecast to Source Cooperative
# (product ocean-icechunks/necofs-waves).
#   1. 120-step bitrounded/rechunked NetCDF (5 full 24-step time chunks) -> netcdf/
#   2. append to the FMRC virtual icechunk store -> icechunk/necofs_wave_forecast
#
# Every step is idempotent, so any failure exits non-zero without updating the
# state file and the next poll retries the whole job.

WAVE_SRC="/data/necofs/NECOFS_ARCHIVES/NECOFS_WAVE_FORECAST.nc"
WAVE_SC_OUT="/home/user/rsignell/NECOFS_WAVE_FORECAST_120_br.nc"
SOURCE_COOP_DEST="s3://ocean-icechunks/necofs-waves/netcdf"
NCKS="/data/rsignell/miniforge3/envs/CLI/bin/ncks"
NCDUMP="/data/rsignell/miniforge3/envs/CLI/bin/ncdump"
ICECHUNK_PY="/data/rsignell/miniforge3/envs/icechunk/bin/python"
REPO_DIR="/home/user/rsignell/repos/necofs-provider"
SOURCE_COOP_PUSH="$REPO_DIR/source_coop_push.sh"
ICECHUNK_SCRIPT="$REPO_DIR/process_wave_source_coop_icechunk.py"
LOGFILE="/home/user/rsignell/bin/process_wave_source_coop.log"
LAST_TIME_FILE="/home/user/rsignell/.necofs_last_wave_sc_time"
LOCKFILE="/home/user/rsignell/.necofs_wave_sc.lock"

# Service account for the necofs-waves product (also read by the icechunk script).
export SC_KEY_FILE="/home/user/rsignell/.source-coop/necofs-waves.key"
export SC_ROLE_ARN="arn:aws:iam::ocean-icechunks--necofs-wave-updates:role/FullAccess"

log() { echo "$(date): $*" >> "$LOGFILE"; }

exec 9>"$LOCKFILE"
flock -n 9 || exit 0

FORECAST_TIME=$("$NCKS" -H -s '%g\n' -v time -d time,0 "$WAVE_SRC" 2>/dev/null)
DATE=$("$NCDUMP" -t -v time "$WAVE_SRC" 2>/dev/null | grep -oP '"\K[0-9]{4}-[0-9]{2}-[0-9]{2}(?=")' | head -1 | tr -d '-')

if [ -z "$FORECAST_TIME" ] || [ -z "$DATE" ]; then
    log "WARNING - could not read time[0] from source file, skipping."
    exit 0
fi

if [ -f "$LAST_TIME_FILE" ] && [ "$(cat "$LAST_TIME_FILE")" = "$FORECAST_TIME" ]; then
    exit 0
fi

log "Starting source.coop publish for wave forecast $DATE"

log "Running ncks bitrounding/rechunking (120 steps)..."
"$NCKS" -O -4 -L 4 \
  -x -v Itime,Itime2,Times \
  --cnk_dmn time,24 --cnk_dmn node,34514 --cnk_dmn nele,34514 \
  --qnt_alg btr \
  --qnt hs=12 --qnt wdir=12 --qnt tpeak=12 --qnt wlen=12 --qnt fp=12 \
  --qnt uwind_speed=12 --qnt vwind_speed=12 \
  -d time,0,119 \
  "$WAVE_SRC" "$WAVE_SC_OUT" >> "$LOGFILE" 2>&1
if [ $? -ne 0 ]; then
    log "ERROR - ncks failed"
    exit 1
fi

log "Pushing NECOFS_WAVE_FORECAST_${DATE}_br.nc to source.coop..."
"$SOURCE_COOP_PUSH" "$WAVE_SC_OUT" \
  "$SOURCE_COOP_DEST/NECOFS_WAVE_FORECAST_${DATE}_br.nc" >> "$LOGFILE" 2>&1
if [ $? -ne 0 ]; then
    log "ERROR - source.coop push failed (will retry next poll)"
    exit 1
fi

log "Appending to source.coop FMRC icechunk store..."
"$ICECHUNK_PY" "$ICECHUNK_SCRIPT" "$DATE" >> "$LOGFILE" 2>&1
if [ $? -ne 0 ]; then
    log "ERROR - source.coop icechunk append failed (will retry next poll)"
    exit 1
fi

echo "$FORECAST_TIME" > "$LAST_TIME_FILE"
log "Done. Published wave forecast $DATE to source.coop."
