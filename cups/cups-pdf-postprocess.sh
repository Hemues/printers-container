#!/bin/sh
# cups-pdf post-processor — invoked by cups-pdf after every successful PDF
# generation. Arguments:
#   $1 = absolute path to the generated PDF
#   $2 = username that submitted the job (cups-pdf passes this)
#
# Forwards the event to the in-container Python backend via a small JSON
# file dropped under /var/spool/cups-pdf/INBOX, where the print_watcher
# task is monitoring with inotify (or a polling fallback).

set -eu

PDF="$1"
USER="${2:-anonymous}"
INBOX="${CUPS_PDF_INBOX:-/var/spool/cups-pdf/INBOX}"

mkdir -p "$INBOX"
TS=$(date -u +%Y%m%dT%H%M%S%3NZ)
ID="${TS}-$$"

# Pull the original job title from the cups-pdf log if available, otherwise
# fall back to the filename stem.
TITLE=$(basename "$PDF" .pdf)

cat >"$INBOX/$ID.json" <<EOF
{
  "pdf": "$PDF",
  "user": "$USER",
  "title": "$TITLE",
  "ts_utc": "$TS"
}
EOF

# Make sure the watcher (running as the python user) can read & move it
chmod 0644 "$INBOX/$ID.json" || true
chmod 0644 "$PDF" || true
