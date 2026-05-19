#!/bin/sh
# cups-pdf post-processor — invoked by cups-pdf after every successful PDF
# generation. Arguments:
#   $1 = absolute path to the generated PDF
#   $2 = username that submitted the job (cups-pdf passes this)
#
# CUPS sets the PRINTER environment variable for the queue name.
#
# Forwards the event to the in-container Python backend via a small JSON
# file dropped under /var/spool/cups-pdf/INBOX, where the print_watcher
# task is monitoring with inotify (or a polling fallback).
#
# LOGGING ARCHITECTURE:
#   Both Samba and IPP print paths end up at the same CUPS queue, so this
#   post-processor captures ALL jobs identically regardless of source:
#     Samba path: Windows -> Samba -> CUPS queue -> capture -> log
#     IPP path:   Windows -> CUPS queue (direct) -> capture -> log
#   The log format and database are the same for both paths.

set -eu

PDF="$1"
USER="${2:-anonymous}"
INBOX="${CUPS_PDF_INBOX:-/var/spool/cups-pdf/INBOX}"
# CUPS sets PRINTER env var for the queue being printed to
QUEUE="${PRINTER:-unknown}"

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
  "printer": "$QUEUE",
  "ts_utc": "$TS"
}
EOF

# Make sure the watcher (running as the python user) can read & move it
chmod 0644 "$INBOX/$ID.json" || true
chmod 0644 "$PDF" || true
