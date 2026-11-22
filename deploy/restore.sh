#!/bin/sh
# Restore the investigation store from a verified backup archive.
# The archive is checksummed and fully validated before extraction. Extraction
# happens in a temporary directory next to the store, and the current store is
# moved aside, never deleted. Usage: restore.sh ARCHIVE
set -eu

ARCHIVE=${1:?usage: restore.sh ARCHIVE}
CHECKSUM="$ARCHIVE.sha256"
STORE=${STORE:-/var/lib/chaininquest}
SERVICE=${SERVICE:-chaininquest}
SERVICE_USER=${SERVICE_USER:-chaininquest}
PYTHON=${PYTHON:-python3}
VERIFY=$(dirname "$0")/verify_archive.py
ROOT=$(basename "$STORE")
umask 077

fail() {
	echo "$1" >&2
	exit 1
}

[ -f "$ARCHIVE" ] || fail "archive not found: $ARCHIVE"
[ -f "$CHECKSUM" ] || fail "checksum file is required: $CHECKSUM"
[ -f "$VERIFY" ] || fail "archive validator not found: $VERIFY"
command -v "$PYTHON" >/dev/null 2>&1 || fail "python interpreter not found: $PYTHON"
"$PYTHON" "$VERIFY" "$ARCHIVE" --root "$ROOT" --checksum "$CHECKSUM" >/dev/null ||
	fail "archive validation failed; the store was not changed"

active=0
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$SERVICE"; then
	active=1
	systemctl stop "$SERVICE"
fi

tmp=""
previous=""
restarted=0
cleanup() {
	if [ -n "$tmp" ] && [ -d "$tmp" ]; then
		rm -rf "$tmp"
	fi
	if [ "$active" = 1 ] && [ "$restarted" = 0 ]; then
		systemctl start "$SERVICE" || true
	fi
}
trap cleanup EXIT

mkdir -p "$(dirname "$STORE")"
tmp=$(mktemp -d "$(dirname "$STORE")/.chaininquest-restore.XXXXXX")
tar -C "$tmp" --no-same-owner -xzf "$ARCHIVE"
new="$tmp/$ROOT"
[ -d "$new" ] || fail "archive did not produce $ROOT"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
previous="$STORE.pre-restore-$stamp"
if [ -e "$STORE" ]; then
	mv "$STORE" "$previous"
fi
if ! mv "$new" "$STORE"; then
	if [ -n "$previous" ] && [ -e "$previous" ]; then
		mv "$previous" "$STORE"
	fi
	fail "could not activate the restored store; previous store was kept"
fi

if [ "$(id -u)" = 0 ]; then
	chown -R "$SERVICE_USER:$SERVICE_USER" "$STORE"
fi
chmod 700 "$STORE"

restarted=1
if [ "$active" = 1 ]; then
	systemctl start "$SERVICE"
	if command -v curl >/dev/null 2>&1; then
		host=""
		if [ -r "${ENV_FILE:-/etc/chaininquest/beta.env}" ]; then
			host=$(
				# shellcheck disable=SC1090
				. "${ENV_FILE:-/etc/chaininquest/beta.env}" &&
					printf %s "${CHAININQUEST_BETA_ORIGIN:-}" | cut -d/ -f3
			)
		fi
		if [ -n "$host" ]; then
			curl --fail --silent -H "Host: $host" http://127.0.0.1:8000/healthz >/dev/null
		else
			curl --fail --silent http://127.0.0.1:8000/healthz >/dev/null
		fi
	fi
fi
echo "restored $ARCHIVE"
if [ -n "$previous" ] && [ -e "$previous" ]; then
	echo "previous store kept at $previous"
fi
