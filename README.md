# printers

Self-hosted CUPS + Samba print server in a container with a web admin UI.

## Features

- **CUPS** print spooler with `cups-pdf` virtual backend — every job is captured as a PDF shadow copy (internal only, not exposed externally)
- **Samba** sharing — exposes the CUPS print queue to **Windows, macOS and Linux** clients over SMB (`\\server\printers` / `smb://server/printers`)
- **PDF archive** — shadow PDFs land in `/printings/<username>/YYYYMMDDHHMMSS-<document>.pdf`
- **Page counting** via `pdfinfo` (Ghostscript fallback)
- **Web UI** — Angular SPA at `http://<host>:8082`
  - User login with 2FA (TOTP)
  - Per-user print log with page count, file size, and PDF download
  - Daily / monthly / yearly / overall statistics
  - Admin panel: user management, CUPS printer CRUD, global print log, SMTP configuration
- **Groups**: `admin-group`, `useradmin-group`, `users-group`
- Bootstrap admin account: `admin` / `admin` (forced password change on first login)
- Test account: `test` / `test123456`

## Container image

```
ghcr.io/hemues/printers:latest
```

Releases: [github.com/Hemues/printers-images/releases](https://github.com/Hemues/printers-images/releases)

## Quick start

```bash
# Create data directories owned by the printers service user
install -d -m 755 -o printers -g printers \
    /nvme/pods-data/printers/printings \
    /nvme/pods-data/printers/configs \
    /nvme/pods-data/printers/logs

podman run -d \
    --name printers \
    --restart unless-stopped \
    -p 8082:8082 \
    -p 139:139 -p 445:445 \
    -p 137:137/udp -p 138:138/udp \
    -v /nvme/pods-data/printers/printings:/printings:Z \
    -v /nvme/pods-data/printers/configs:/configs:Z \
    -v /nvme/pods-data/printers/logs:/logs:Z \
    ghcr.io/hemues/printers:latest
```

Browse to `http://<host>:8082` and log in as `admin` / `admin`.

## Client setup

### Windows
1. Open **Run** (`Win+R`) and type `\\<host>\printers`
2. Double-click **PDF** and install via the Windows printer wizard
3. Print any document — the PDF appears in the web UI under your user

### macOS
1. **System Settings → Printers & Scanners → Add Printer (+)**
2. Click the **Windows** tab, or press `Cmd+K` and enter `smb://<host>/printers`
3. Select the **PDF** printer and click **Add**
4. Print any document — the PDF appears in the web UI

### Linux (GNOME / command line)
```bash
# Add via lpadmin (replace <host> with the server hostname or IP)
sudo lpadmin -p PDF -E \
    -v smb://<host>/printers/PDF \
    -m everywhere
sudo lpoptions -d PDF
```
Or use **GNOME Settings → Printers → Add → Windows Printer via SAMBA** and enter `smb://<host>/printers`.

> **Note**: CUPS IPP (port 631) is intentionally not exposed. All clients — Windows, macOS, and Linux — connect through the Samba share.

## Volumes

| Path inside container | Purpose |
|-----------------------|---------|
| `/printings` | Shadow PDF archive, one sub-directory per user |
| `/configs` | Persistent config: CUPS, Samba, user database |
| `/logs` | Application and print logs |

## Ports

| Port | Protocol | Service |
|------|----------|---------|
| 8082 | TCP | Web UI / API |
| 445  | TCP | SMB (Windows / macOS / Linux printing) |
| 139  | TCP | NetBIOS session |
| 137  | UDP | NetBIOS name service |
| 138  | UDP | NetBIOS datagram |

> CUPS IPP (631) runs internally but is **not** published on the host.

## Build

```bash
git clone https://github.com/Hemues/printers.git
cd printers
bash build.sh
```

The build script auto-increments the patch version, builds the container image,
pushes to `ghcr.io/hemues/printers:latest`, and creates a GitHub release in
[Hemues/printers-images](https://github.com/Hemues/printers-images).

## Update (production)

```bash
sudo bash /etc/scripts/podman-printers-updater-inside-pod
```

## Architecture

```
Dockerfile (debian:stable-slim)
+-- CUPS + cups-pdf  ->  virtual printer captures every job as PDF (internal only)
+-- cups-pdf-postprocess.sh  ->  writes JSON notification to INBOX spool
+-- Samba (smbd/nmbd)  ->  exposes CUPS printer to Windows/macOS/Linux SMB clients
+-- Python aiohttp backend  (app/)
    +-- printer_engine.py  ->  polls INBOX, moves PDF, counts pages via pdfinfo/gs
    +-- user_manager.py    ->  users DB, sessions, 2FA, Samba passdb sync, print stats
    +-- main.py            ->  REST API + Socket.IO + static Angular SPA
```

## Security notes

- The admin password **must** be changed on first login (enforced by the UI)
- 2FA (TOTP) can be enabled per user from the profile page
- CUPS IPP is intentionally kept internal — only Samba is exposed for print submission
- All volume paths should be owned by the dedicated `printers` system user
