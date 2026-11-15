#!/bin/bash
# Push one file to Source Cooperative via the AWS CLI, skipping if the key already exists.
# (rclone 1.57 gets 403s from the data proxy, so we use `aws s3 cp`.)
#
# Usage: source_coop_push.sh LOCAL_FILE s3://account/product/path/file.nc
#
# Auth: service-account API key at ~/.source-coop/necofs.key, exchanged for 1-hour
# credentials by the AWS CLI (web-identity). Absolute paths are used throughout
# because cron does not expand ~ or $HOME.
set -u

AWS="/home/user/rsignell/bin/aws"
export AWS_REGION="us-west-2"
export AWS_ENDPOINT_URL_S3="https://data.source.coop"
export AWS_ENDPOINT_URL_STS="https://data.source.coop/.sts"
export AWS_ROLE_ARN="arn:aws:iam::ocean-icechunks--necofs-pipeline:role/FullAccess"
export AWS_WEB_IDENTITY_TOKEN_FILE="/home/user/rsignell/.source-coop/necofs.key"

if [ $# -ne 2 ]; then
    echo "Usage: $0 LOCAL_FILE s3://account/product/path" >&2
    exit 2
fi
SRC="$1"
DEST="$2"

if [ ! -f "$SRC" ]; then
    echo "source_coop_push: local file not found: $SRC" >&2
    exit 2
fi

case "$DEST" in
    s3://*/*) ;;
    *) echo "source_coop_push: bad destination: $DEST" >&2; exit 2 ;;
esac
BUCKET="${DEST#s3://}"; BUCKET="${BUCKET%%/*}"
KEY="${DEST#s3://$BUCKET/}"

# Emulate rclone --ignore-existing: skip only on a definite "exists".
# Any failure other than 404 (auth, network) is an error, not "missing".
out=$("$AWS" s3api head-object --bucket "$BUCKET" --key "$KEY" 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
    echo "source_coop_push: $DEST already exists, skipping."
    exit 0
elif ! echo "$out" | grep -qE '\(404\)|Not Found'; then
    echo "source_coop_push: head-object failed (not a 404): ${out:0:300}" >&2
    exit 1
fi

"$AWS" s3 cp "$SRC" "$DEST" --only-show-errors || exit 1
echo "source_coop_push: uploaded $DEST"
