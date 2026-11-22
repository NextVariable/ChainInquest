#!/bin/sh
# Restore the investigation store from a backup archive.
# The current store is moved aside, never deleted, so a failed restore can be
# reversed manually. Usage: restore.sh /var/backups/chaininquest/<archive>.tar.gz
set -eu

ARCHIVE=${1:?usage: restore.sh ARCHIVE}
STORE=${STORE:-/var/lib/chaininquest}
SERVICE=${SERVICE:-chaininquest}
SERVICE_USER=${SERVICE_USER:-chaininquest}
umask 077

[ -f "$ARCHIVE" ] || { echo "archive not found: $ARCHIVE" >&2; exit 1; }
if [ -f "$ARCHIVE.sha256" ] && command -v sha256sum >/dev/null 2>&1; then
	sha256sum -c "$ARCHIVE.sha256"
fi
tar -tzf "$ARCHIVE" >/dev/null
top=$(tar -tzf "$ARCHIVE" | head -n 1 | cut -d/ -f1)
[ "$top" = "$(basename "$STORE")" ] || {
	echo "archive does not contain $(basename "$STORE")" >&2
	exit 1
}

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

stamp=$(date -u +%Y%m%dT%H%M%SZ)
previous="$STORE.pre-restore-$stamp"
if [ -e "$STORE" ]; then
	mv "$STORE" "$previous"
fi
mkdir -p "$(dirname "$STORE")"
tar -C "$(dirname "$STORE")" -xzf "$ARCHIVE"
if [ "$(id -u)" = 0 ]; then
	chown -R "$SERVICE_USER:$SERVICE_USER" "$STORE"
fi
chmod 700 "$STORE"

trap - EXIT
restart
if [ "$active" = 1 ]; then
	/usr/bin/curl --fail --silent http://127.0.0.1:8000/healthz >/dev/null
fi
echo "restored $ARCHIVE"
if [ -e "$previous" ]; then
	echo "previous store kept at $previous"
fi
