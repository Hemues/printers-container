#!/bin/sh
# =============================================================================
# Printers container entrypoint.
#
#   1. Materialise persistent config / log / printings dirs.
#   2. Seed CUPS and Samba config from templates the first time around.
#   3. Install cups-pdf post-processor that notifies the python backend.
#   4. Start cupsd + smbd + nmbd in the background.
#   5. Exec the python backend in the foreground (PID 1 via tini).
# =============================================================================

set -e

umask "${UMASK:-022}"

PRINTINGS_DIR="${PRINTINGS_DIR:-/printings}"
CONFIG_DIR="${CONFIG_DIR:-/configs}"
LOG_DIR="${LOG_DIR:-/logs}"

mkdir -p "$PRINTINGS_DIR" "$CONFIG_DIR" "$LOG_DIR"
mkdir -p "$CONFIG_DIR/cups" "$CONFIG_DIR/cups/ppd" \
         "$CONFIG_DIR/samba" "$CONFIG_DIR/samba/private" \
         "$CONFIG_DIR/samba/state" "$CONFIG_DIR/samba/cache" \
         "$CONFIG_DIR/database/global" \
         "$LOG_DIR/samba" "$LOG_DIR/cups"
mkdir -p /var/spool/cups-pdf/INBOX /var/spool/cups-pdf/SPOOL /var/spool/cups-pdf/ANONYMOUS
mkdir -p /var/spool/samba /var/lib/samba/printers /run/cups /run/samba /run/samba/msg.lock
chmod 0755 /run/samba /run/samba/msg.lock
chmod 1777 /var/spool/samba /tmp
# Remove restrictive ACLs inherited from rootless Podman overlay layers.
setfacl -b /tmp /var/spool/samba 2>/dev/null || true

# ---------------------------------------------------------------------------
# Seed configs from templates the first time (or when the template is newer).
# ---------------------------------------------------------------------------
seed_config() {
    template="$1"
    target="$2"
    if [ ! -f "$target" ] || [ "$template" -nt "$target" ]; then
        echo "[entrypoint] seeding $target from $template"
        cp "$template" "$target"
    fi
}

seed_config /etc/cups/cupsd.conf.template       "$CONFIG_DIR/cups/cupsd.conf"
seed_config /etc/cups/cups-pdf.conf.template    "$CONFIG_DIR/cups/cups-pdf.conf"
seed_config /etc/samba/smb.conf.template        "$CONFIG_DIR/samba/smb.conf"

# Bind the persisted config into the well-known locations expected by the daemons.
ln -sfn "$CONFIG_DIR/cups/cupsd.conf"     /etc/cups/cupsd.conf
ln -sfn "$CONFIG_DIR/cups/cups-pdf.conf"  /etc/cups/cups-pdf.conf
ln -sfn "$CONFIG_DIR/samba/smb.conf"      /etc/samba/smb.conf

# Persist CUPS dynamic state (printer definitions, PPDs) on /configs volume.
# Without this, lpadmin-added printers disappear on container restart.
ln -sfn "$CONFIG_DIR/cups/ppd"            /etc/cups/ppd
touch "$CONFIG_DIR/cups/printers.conf"
ln -sfn "$CONFIG_DIR/cups/printers.conf"  /etc/cups/printers.conf
touch "$CONFIG_DIR/cups/classes.conf"
ln -sfn "$CONFIG_DIR/cups/classes.conf"   /etc/cups/classes.conf

# Pre-initialise the Samba passdb TDB so the very first smbpasswd -a call does
# not fail with "tdbsam_open: Converting version 0.0 database" during bootstrap.
# pdbedit -L just lists users (empty is fine) and creates a valid TDB schema.
pdbedit -L --configfile="$CONFIG_DIR/samba/smb.conf" >/dev/null 2>&1 || true

# Install the post-processor that hands new PDFs off to the python backend.
install -m 0755 /app/cups-pdf-postprocess.sh /usr/local/bin/cups-pdf-postprocess.sh 2>/dev/null || true

# ---------------------------------------------------------------------------
# Start CUPS in the background. cupsd refuses to fork without -f so we run
# it in the foreground and background it from the shell.
# ---------------------------------------------------------------------------
echo "[entrypoint] starting cupsd …"
/usr/sbin/cupsd -f -c /etc/cups/cupsd.conf &
CUPSD_PID=$!

# ---------------------------------------------------------------------------
# Start Samba (smbd + nmbd) in the background.
# ---------------------------------------------------------------------------
echo "[entrypoint] starting smbd …"
/usr/sbin/smbd --foreground --no-process-group --configfile=/etc/samba/smb.conf &
SMBD_PID=$!

echo "[entrypoint] starting nmbd …"
/usr/sbin/nmbd --foreground --no-process-group --configfile=/etc/samba/smb.conf &
NMBD_PID=$!

# ---------------------------------------------------------------------------
# Graceful shutdown.
# ---------------------------------------------------------------------------
trap 'echo "[entrypoint] stopping daemons …"; kill $CUPSD_PID $SMBD_PID $NMBD_PID 2>/dev/null || true; wait' INT TERM

# ---------------------------------------------------------------------------
# Run the python backend in the foreground (so docker logs follow it).
# ---------------------------------------------------------------------------
cd /app
echo "[entrypoint] starting python backend on :${PORT:-8082}"
exec python3 app/main.py
