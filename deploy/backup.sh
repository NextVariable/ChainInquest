#!/bin/sh
# Create a consistent archive of the investigation store.
# The service is stopped for the duration so checkpoints are not changing.
set -eu

STORE=${STORE:-/var/lib/chaininquest}
DEST=${DEST:-/var/backups/chaininquest}
SERVICE=${SERVICE:-chaininquest}
umask 077
# macOS bsdtar otherwise writes AppleDouble members such as ._store, which
# deploy/verify_archive.py rejects as paths outside the store root.
export COPYFILE_DISABLE=1

[ -d "$STORE" ] || { echo "store not found: $STORE" >&2; exit 1; }
mkdir -p "$DEST"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$DEST/chaininquest-$stamp.tar.gz"

active=0
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$SERVICE"; then
	active=1
	systemctl stop "$SERVICE"
fi

restart() {
	if [ "$active" = 1 ]; then
		systemctl start "$SERVICE"
	fi
}
trap restart EXIT

tar -C "$(dirname "$STORE")" -czf "$archive" "$(basename "$STORE")"
tar -tzf "$archive" >/dev/null
if command -v sha256sum >/dev/null 2>&1; then
	sha256sum "$archive" >"$archive.sha256"
else
	shasum -a 256 "$archive" >"$archive.sha256"
fi

trap - EXIT
restart
echo "$archive"
