# =============================================================================
# Printers container — Alpine-based virtual print server with web admin UI
#
# Services running inside the container:
#   - CUPS (cupsd) on 631     — print server with cups-pdf virtual backend
#   - Samba (smbd)            — Windows print sharing (re-shares CUPS queues)
#   - Python aiohttp backend  — admin/user web UI on $PORT (default 8082)
#
# Volumes:
#   /printings  -> per-user shadow PDFs (/printings/<user>/<DATETIME>-<basename>.pdf)
#   /configs    -> CUPS, Samba and webapp config + user DB persisted here
#   /logs       -> application logs and print-job log
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: build the Angular UI
# -----------------------------------------------------------------------------
FROM node:lts-alpine AS builder

WORKDIR /printers
COPY ui ./
RUN corepack enable && corepack prepare pnpm --activate
RUN CI=true pnpm install && pnpm run build


# -----------------------------------------------------------------------------
# Stage 2: runtime — Debian slim with CUPS + Samba + Ghostscript + Python
# -----------------------------------------------------------------------------
FROM debian:stable-slim

WORKDIR /app

COPY pyproject.toml docker-entrypoint.sh cups-pdf-postprocess.sh ./

# Use sed to strip carriage-return characters from the entrypoint script (in case building on Windows)
# Install dependencies
RUN sed -i 's/\r$//g' docker-entrypoint.sh && \
    chmod +x docker-entrypoint.sh && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      python3-venv \
      ca-certificates \
      tini \
      gosu \
      curl \
      file \
      cups \
      cups-pdf \
      cups-bsd \
      cups-client \
      cups-filters \
      printer-driver-cups-pdf \
      ghostscript \
      poppler-utils \
      samba \
      samba-common-bin \
      libnss-winbind \
      libpam-winbind \
      acl \
      coreutils && \
    python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir aiohttp 'python-socketio>=5.0,<6.0' pyotp 'qrcode[pil]' && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir /.cache && chmod 777 /.cache

# Set up Python venv in PATH
ENV PATH="/opt/venv/bin:$PATH"

COPY app ./app
COPY samba/smb.conf.template /etc/samba/smb.conf.template
COPY cups/cupsd.conf.template /etc/cups/cupsd.conf.template
COPY cups/cups-pdf.conf.template /etc/cups/cups-pdf.conf.template
COPY --from=builder /printers/dist/printers ./ui/dist/printers

ENV PUID=0
ENV PGID=0
ENV UMASK=022

ENV PRINTINGS_DIR=/printings
ENV CONFIG_DIR=/configs
ENV LOG_DIR=/logs
# cups-pdf default spool location (mapped to /printings/<user>/ by the capture watcher)
ENV CUPS_PDF_SPOOL=/var/spool/cups-pdf
# Backend HTTP port (overridable via env)
ENV PORT=8082

VOLUME /printings
VOLUME /configs
VOLUME /logs

EXPOSE 8082 631 137/udp 138/udp 139 445

# Add build-time argument for version
ARG VERSION=dev
ENV PRINTERS_VERSION=$VERSION

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "./docker-entrypoint.sh"]
