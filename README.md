# printers

Self-hosted CUPS + Samba print server in a container with a web admin UI.

## Features

- **CUPS** print spooler with `cups-pdf` virtual printer — every job is captured as a PDF shadow copy
- **Samba** sharing — exposes the CUPS queue (`\\server\printers`) to Windows and Linux clients
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

## Quick start

```bash
install -d -m 755 -o printers -g printers \
    /nvme/pods-data/printers/printings \
    /nvme/pods-data/printers/configs \
    /nvme/pods-data/printers/logs

podman run -d \
    --name printers \
    --restart unless-stopped \
    -p 8082:8082 \
    -p 631:631 \
    -p 139:139 -p 445:445 \
    -p 137:137/udp -p 138:138/udp \
    -v /nvme/pods-data/printers/printings:/printings:Z \
    -v /nvme/pods-data/printers/configs:/configs:Z \
    -v /nvme/pods-data/printers/logs:/logs:Z \
    ghcr.io/hemues/printers:latest
```

Browse to `http://<host>:8082` and log in as `admin` / `admin`.

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
| 631  | TCP | IPP (CUPS) |
| 445  | TCP | SMB |
| 139  | TCP | NetBIOS session |
| 137  | UDP | NetBIOS name service |
| 138  | UDP | NetBIOS datagram |

## Build

```bash
git clone https://github.com/Hemues/printers.git
cd printers
./build.sh
```

## Update (production)

```bash
sudo /etc/scripts/podman-printers-updater-inside-pod
```

## Architecture

```
Dockerfile (debian:stable-slim)
+-- CUPS + cups-pdf  ->  virtual printer captures every job as PDF
+-- cups-pdf-postprocess.sh  ->  writes JSON notification to INBOX spool
+-- Samba (smbd/nmbd)  ->  exposes CUPS printer to Windows/Linux SMB clients
+-- Python aiohttp backend  (app/)
    +-- printer_engine.py  ->  polls INBOX, moves PDF, counts pages via pdfinfo/gs
    +-- user_manager.py    ->  users DB, sessions, 2FA, Samba passdb sync, print stats
    +-- main.py            ->  REST API + Socket.IO + static Angular SPA
```

