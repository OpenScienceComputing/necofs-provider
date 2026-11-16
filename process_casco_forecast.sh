#!/bin/bash
# Process and push daily NECOFS Casco Bay ocean forecast to S3

CASCO_SRC="/data/necofs/NECOFS_ARCHIVES/NECOFS_FVCOM_OCEAN_CASCO_FORECAST.nc"
CASCO_OUT="/home/user/rsignell/NECOFS_FVCOM_OCEAN_CASCO_FORECAST_br.nc"
S3_DEST="neracoos-necofs-forecast:neracoos-necofs-forecast/OCEAN_CASCO"
NCKS="/data/rsignell/miniforge3/envs/CLI/bin/ncks"
NCDUMP="/data/rsignell/miniforge3/envs/CLI/bin/ncdump"
NCRENAME="/data/rsignell/miniforge3/envs/CLI/bin/ncrename"
RCLONE="/usr/bin/rclone"
ICECHUNK_PY="/data/rsignell/miniforge3/envs/icechunk/bin/python"
ICECHUNK_SCRIPT="/home/user/rsignell/repos/necofs-provider/process_casco_icechunk.py"
TIMESERIES_SCRIPT="/home/user/rsignell/repos/necofs-provider/process_casco_timeseries.py"
LOGFILE="/home/user/rsignell/bin/process_casco_forecast.log"
LAST_TIME_FILE="${HOME}/.necofs_last_casco_ocean_time"

FORECAST_TIME=$("$NCKS" -H -s '%g\n' -v time -d time,0 "$CASCO_SRC" 2>/dev/null)
DATE=$("$NCDUMP" -t -v time "$CASCO_SRC" 2>/dev/null | grep -oP '"\K[0-9]{4}-[0-9]{2}-[0-9]{2}(?=")' | head -1 | tr -d '-')

if [ -z "$FORECAST_TIME" ] || [ -z "$DATE" ]; then
    echo "$(date): WARNING - could not read time[0] from source file, skipping." >> "$LOGFILE"
    exit 0
fi

if [ -f "$LAST_TIME_FILE" ] && [ "$(cat "$LAST_TIME_FILE")" = "$FORECAST_TIME" ]; then
    exit 0
fi

echo "$(date): Starting Casco Bay ocean forecast processing for $DATE" >> "$LOGFILE"

echo "$(date): Running ncks bitrounding/rechunking..." >> "$LOGFILE"
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
  "$CASCO_SRC" "$CASCO_OUT" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - ncks failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Renaming siglay/siglev variables..." >> "$LOGFILE"
"$NCRENAME" -v siglay,sigma_layer -v siglev,sigma_level "$CASCO_OUT" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - ncrename failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Pushing to S3 as NECOFS_FVCOM_OCEAN_CASCO_FORECAST_${DATE}_br.nc..." >> "$LOGFILE"
"$RCLONE" copyto "$CASCO_OUT" \
  "$S3_DEST/NECOFS_FVCOM_OCEAN_CASCO_FORECAST_${DATE}_br.nc" \
  --s3-no-check-bucket --ignore-existing >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - rclone failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Done. Uploaded NECOFS_FVCOM_OCEAN_CASCO_FORECAST_${DATE}_br.nc" >> "$LOGFILE"
[ -n "$FORECAST_TIME" ] && echo "$FORECAST_TIME" > "$LAST_TIME_FILE"

echo "$(date): Appending virtual dataset to icechunk FMRC store..." >> "$LOGFILE"
"$ICECHUNK_PY" "$ICECHUNK_SCRIPT" "$DATE" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - icechunk FMRC append failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Icechunk FMRC append complete." >> "$LOGFILE"

echo "$(date): Appending to timeseries icechunk store..." >> "$LOGFILE"
"$ICECHUNK_PY" "$TIMESERIES_SCRIPT" "$DATE" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - timeseries icechunk append failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Timeseries icechunk append complete." >> "$LOGFILE"
