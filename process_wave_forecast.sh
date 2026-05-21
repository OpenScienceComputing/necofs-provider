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
LOGFILE="/home/user/rsignell/bin/process_wave_forecast.log"

DATE=$(date +%Y%m%d)
echo "$(date): Starting wave forecast processing for $DATE" >> "$LOGFILE"

# Check if file was updated today
FILE_DATE=$(date -r "$WAVE_SRC" +%Y%m%d 2>/dev/null)
if [ "$FILE_DATE" != "$DATE" ]; then
    echo "$(date): Wave file not updated today (last: $FILE_DATE), skipping." >> "$LOGFILE"
    exit 0
fi

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

# Append virtual dataset to icechunk store
echo "$(date): Appending virtual dataset to icechunk..." >> "$LOGFILE"
"$ICECHUNK_PY" "$ICECHUNK_SCRIPT" "$DATE" >> "$LOGFILE" 2>&1

if [ $? -ne 0 ]; then
    echo "$(date): ERROR - icechunk append failed" >> "$LOGFILE"
    exit 1
fi

echo "$(date): Icechunk append complete." >> "$LOGFILE"
