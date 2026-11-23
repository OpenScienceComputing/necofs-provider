#!/bin/bash
# Cron job: publish each new NECOFS Casco Bay ocean forecast to Source Cooperative.
#   1. 144-step bitrounded/rechunked NetCDF (12 full time chunks) -> netcdf/
#   2. append to the FMRC virtual icechunk store on source.coop
# Touches nothing else (no primary S3 push, no other icechunk stores).
#
# Every step is idempotent, so any failure exits non-zero without updating the
# state file and the next poll retries the whole job.

CASCO_SRC="/data/necofs/NECOFS_ARCHIVES/NECOFS_FVCOM_OCEAN_CASCO_FORECAST.nc"
CASCO_SC_OUT="/home/user/rsignell/NECOFS_FVCOM_OCEAN_CASCO_FORECAST_144_br.nc"
SOURCE_COOP_DEST="s3://ocean-icechunks/neracoos-necofs-casco/netcdf"
NCKS="/data/rsignell/miniforge3/envs/CLI/bin/ncks"
NCDUMP="/data/rsignell/miniforge3/envs/CLI/bin/ncdump"
NCRENAME="/data/rsignell/miniforge3/envs/CLI/bin/ncrename"
ICECHUNK_PY="/data/rsignell/miniforge3/envs/icechunk/bin/python"
REPO_DIR="/home/user/rsignell/repos/necofs-provider"
SOURCE_COOP_PUSH="$REPO_DIR/source_coop_push.sh"
ICECHUNK_SCRIPT="$REPO_DIR/process_casco_source_coop_icechunk.py"
LOGFILE="/home/user/rsignell/bin/process_casco_source_coop.log"
LAST_TIME_FILE="/home/user/rsignell/.necofs_last_casco_sc_time"
LOCKFILE="/home/user/rsignell/.necofs_casco_sc.lock"

log() { echo "$(date): $*" >> "$LOGFILE"; }

# Warn if the provider's forecast is older than this many days.
MAX_AGE_DAYS=5
WARN_FILE="/home/user/rsignell/.necofs_casco_sc_warned"

# Log a warning at most once per day per condition (the job polls hourly).
warn_once() {
    local key="$1:$(date +%Y%m%d)"
    grep -qxF "$key" "$WARN_FILE" 2>/dev/null && return 0
    echo "$key" >> "$WARN_FILE"
    log "WARNING - $2"
}

# One run at a time (the clip takes ~8 min; cron polls hourly).
exec 9>"$LOCKFILE"
flock -n 9 || exit 0

FORECAST_TIME=$("$NCKS" -H -s '%g\n' -v time -d time,0 "$CASCO_SRC" 2>/dev/null)
DATE=$("$NCDUMP" -t -v time "$CASCO_SRC" 2>/dev/null | grep -oP '"\K[0-9]{4}-[0-9]{2}-[0-9]{2}(?=")' | head -1 | tr -d '-')

if [ -z "$FORECAST_TIME" ] || [ -z "$DATE" ]; then
    log "WARNING - could not read time[0] from source file, skipping."
    exit 0
fi

# Staleness / sanity checks on the provider's file (run on every poll).
AGE_DAYS=$(( ($(date +%s) - $(date -d "$DATE" +%s)) / 86400 ))
if [ "$AGE_DAYS" -gt "$MAX_AGE_DAYS" ]; then
    warn_once "stale" "provider forecast $DATE is $AGE_DAYS days old (> $MAX_AGE_DAYS); source file mtime $(date -r "$CASCO_SRC" '+%Y-%m-%d %H:%M')"
fi
if [ -f "$LAST_TIME_FILE" ] && \
   awk -v a="$FORECAST_TIME" -v b="$(cat "$LAST_TIME_FILE")" 'BEGIN{exit !(b - a > 0.5)}'; then
    warn_once "backwards" "forecast time[0] went backwards: source is $DATE but last published time was MJD $(cat "$LAST_TIME_FILE") (source file replaced by an older run?)"
fi

if [ -f "$LAST_TIME_FILE" ] && [ "$(cat "$LAST_TIME_FILE")" = "$FORECAST_TIME" ]; then
    exit 0
fi

log "Starting source.coop publish for Casco forecast $DATE"

log "Running ncks bitrounding/rechunking (144 steps)..."
"$NCKS" -O -4 -L 4 \
  -x -v Itime,Itime2,Times \
  --cnk_map xst --cnk_plc all \
  --cnk_dmn time,12 \
  --cnk_dmn node,19107 \
  --cnk_dmn nele,38072 \
  --cnk_dmn siglay,1 \
  --cnk_dmn siglev,1 \
  --qnt_alg btr \
  --qnt u=16 --qnt v=16 \
  --qnt zeta=12 \
  --qnt temp=12 --qnt salinity=12 \
  --qnt uwind_speed=12 --qnt vwind_speed=12 \
  --qnt hs=12 --qnt wdir=12 --qnt tpeak=12 --qnt wlen=12 \
  -d time,0,143 \
  "$CASCO_SRC" "$CASCO_SC_OUT" >> "$LOGFILE" 2>&1
if [ $? -ne 0 ]; then
    log "ERROR - ncks failed"
    exit 1
fi

"$NCRENAME" -v siglay,sigma_layer -v siglev,sigma_level "$CASCO_SC_OUT" >> "$LOGFILE" 2>&1
if [ $? -ne 0 ]; then
    log "ERROR - ncrename failed"
    exit 1
fi

log "Pushing NECOFS_FVCOM_OCEAN_CASCO_FORECAST_${DATE}_br.nc to source.coop..."
"$SOURCE_COOP_PUSH" "$CASCO_SC_OUT" \
  "$SOURCE_COOP_DEST/NECOFS_FVCOM_OCEAN_CASCO_FORECAST_${DATE}_br.nc" >> "$LOGFILE" 2>&1
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
log "Done. Published Casco forecast $DATE to source.coop."
