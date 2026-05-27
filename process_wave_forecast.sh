#!/bin/bash
# Process and push daily NECOFS wave forecast to S3
# Bitrounds + rechunks the file, then uploads with date in filename

WAVE_SRC="/data/necofs/NECOFS_ARCHIVES/NECOFS_WAVE_FORECAST.nc"
WAVE_OUT="/home/user/rsignell/NECOFS_WAVE_FORECAST_br.nc"
S3_DEST="neracoos-necofs-forecast:neracoos-necofs-forecast/WAVE"
NCKS="/data/rsignell/miniforge3/envs/CLI/bin/ncks"
NCRENAME="/data/rsignell/miniforge3/envs/CLI/bin/ncrename"
RCLONE="/usr/bin/rclone"
ICECHUNK_PY="/data/rsignell/miniforge3/envs/icechunk/bin/python"
ICECHUNK_SCRIPT="/home/user/rsignell/repos/necofs-provider/process_wave_icechunk.py"
TIMESERIES_SCRIPT="/home/user/rsignell/repos/necofs-provider/process_wave_timeseries.py"
LOGFILE="/home/user/rsignell/bin/process_wave_forecast.log"
LAST_TIME_FILE="${HOME}/.necofs_last_wave_forecast_time"

DATE=$(date +%Y%m%d)

# Check if file was updated today
FILE_DATE=$(date -r "$WAVE_SRC" +%Y%m%d 2>/dev/null)
if [ "$FILE_DATE" != "$DATE" ]; then
    exit 0
fi

# Check if this forecast has already been uploaded (time[0] unchanged)
FORECAST_TIME=$("$ICECHUNK_PY" -c "
import netCDF4
ds = netCDF4.Dataset('$WAVE_SRC')
print(ds.variables['time'][0])
ds.close()
" 2>/dev/null)

if [ -z "$FORECAST_TIME" ]; then
    echo "$(date): WARNING - could not read time[0] from source file, proceeding anyway." >> "$LOGFILE"
elif [ -f "$LAST_TIME_FILE" ] && [ "$(cat "$LAST_TIME_FILE")" = "$FORECAST_TIME" ]; then
    exit 0
fi

echo "$(date): Starting wave forecast processing for $DATE" >> "$LOGFILE"

# Bitrounding + rechunking
echo "$(date): Running ncks bitrounding/rechunking..." >> "$LOGFILE"
"$NCKS" -O -4 -L 4 \
  -x -v Itime,Itime2,Times \
  --cnk_dmn time,24 --cnk_dmn node,34514 --cnk_dmn nele,34514 \
  --qnt_alg btr --qnt hs=12 --qnt wdir=12 --qnt tpeak=12 --qnt wlen=12 \
  --qnt zeta=14 --qnt uwind_speed=12 --qnt vwind_speed=12 \
  "$WAVE_SRC" "$WAVE_OUT" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - ncks failed" >> "$LOGFILE"
    exit 1
fi

# Rename siglay/siglev to avoid VirtualiZarr parser confusion (same name as dimension)
echo "$(date): Renaming siglay/siglev variables..." >> "$LOGFILE"
"$NCRENAME" -v siglay,sigma_layer -v siglev,sigma_level "$WAVE_OUT" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - ncrename failed" >> "$LOGFILE"
    exit 1
fi

# Push to S3
echo "$(date): Pushing to S3 as NECOFS_WAVE_FORECAST_${DATE}_br.nc..." >> "$LOGFILE"
"$RCLONE" copyto "$WAVE_OUT" \
  "$S3_DEST/NECOFS_WAVE_FORECAST_${DATE}_br.nc" \
  --s3-no-check-bucket >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - rclone failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Done. Uploaded NECOFS_WAVE_FORECAST_${DATE}_br.nc" >> "$LOGFILE"
[ -n "$FORECAST_TIME" ] && echo "$FORECAST_TIME" > "$LAST_TIME_FILE"

# Append virtual dataset to icechunk store
echo "$(date): Appending virtual dataset to icechunk..." >> "$LOGFILE"
"$ICECHUNK_PY" "$ICECHUNK_SCRIPT" "$DATE" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - icechunk append failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Icechunk FMRC append complete." >> "$LOGFILE"

# Append to flat timeseries icechunk store
echo "$(date): Appending to timeseries icechunk..." >> "$LOGFILE"
"$ICECHUNK_PY" "$TIMESERIES_SCRIPT" "$DATE" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - timeseries icechunk append failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Timeseries icechunk append complete." >> "$LOGFILE"
