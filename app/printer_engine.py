"""
printer_engine.py — capture pipeline + in-memory print-job state.

Each successful CUPS print job (printed to one of the cups-pdf virtual
queues) lands in /var/spool/cups-pdf/INBOX/<id>.json with a sibling PDF.
The capture watcher:
  1. moves the PDF to /printings/<username>/<DATETIME>-<basename>.pdf
  2. determines the page count via `pdfinfo` (poppler) with a ghostscript
     fallback
  3. appends a structured entry to the per-user print log
  4. emits Socket.IO 'added'/'completed' events so the live UI updates

The PrintJobQueue exposes a videodl-compatible surface so the existing
main.py event flow can be reused with minimal changes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger('printer_engine')


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PrintJob:
    """A single captured print job."""
    id: str
    username: str
    title: str
    filename: str          # final on-disk filename (no path)
    path: str              # absolute path under /printings/<user>/
    pages: int = 0
    size: int = 0          # bytes
    printer: str = ''      # CUPS queue name
    status: str = 'finished'   # finished | failed
    error: str = ''
    timestamp: float = field(default_factory=time.time)
    # Used by Socket.IO serialiser
    @property
    def date(self) -> str:
        return dt.datetime.fromtimestamp(self.timestamp).strftime('%Y-%m-%d %H:%M:%S')


class PrintQueueNotifier:
    """Interface so main.py's Notifier subclass can hook us. Mirrors the
    DownloadQueueNotifier surface used in the videodl codebase."""
    async def added(self, job: PrintJob): ...
    async def completed(self, job: PrintJob): ...


# ---------------------------------------------------------------------------
# Filename / page-count helpers
# ---------------------------------------------------------------------------

_BAD_CHARS = re.compile(r'[\x00-\x1f\\/:*?"<>|]+')


def sanitize_basename(name: str) -> str:
    """Strip path components, control chars, and the file extension."""
    name = os.path.basename(name or 'print')
    name = _BAD_CHARS.sub('_', name).strip(' .')
    # Drop ANY extension; cups-pdf may have given us things like "report.docx"
    stem, _ = os.path.splitext(name)
    stem = stem.strip(' .') or 'print'
    return stem[:120]


def count_pdf_pages(pdf_path: str) -> int:
    """Return the page count of a PDF. Tries pdfinfo first, then gs."""
    # 1. pdfinfo (poppler-utils) — fastest, most reliable
    try:
        out = subprocess.run(
            ['pdfinfo', pdf_path],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        for line in out.splitlines():
            if line.startswith('Pages:'):
                return int(line.split(':', 1)[1].strip())
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as exc:
        log.debug(f'pdfinfo failed for {pdf_path}: {exc}')

    # 2. ghostscript fallback (always present because cups-pdf needs it)
    try:
        out = subprocess.run(
            ['gs', '-q', '-dNODISPLAY', '-dNOSAFER',
             '-c', f'({pdf_path}) (r) file runpdfbegin pdfpagecount = quit'],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
        return int(out)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as exc:
        log.warning(f'gs page count failed for {pdf_path}: {exc}')

    return 0


# ---------------------------------------------------------------------------
# Capture watcher
# ---------------------------------------------------------------------------

class PrintCapture:
    """Polls /var/spool/cups-pdf/INBOX for new job notifications and routes
    them to per-user archives."""

    def __init__(
        self,
        notifier_factory,                     # username -> PrintQueueNotifier
        printings_dir: str = '/printings',
        inbox_dir: str = '/var/spool/cups-pdf/INBOX',
        spool_dir: str = '/var/spool/cups-pdf',
        log_appender=None,                    # callable(username, job_dict)
        poll_interval: float = 1.0,
    ):
        self.notifier_factory = notifier_factory
        self.printings_dir = printings_dir
        self.inbox_dir = inbox_dir
        self.spool_dir = spool_dir
        self.log_appender = log_appender
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        # Short-term memory so we can emit 'added' before 'completed' cleanly.
        self.recent: dict[str, list[PrintJob]] = {}   # username -> list

    def start(self):
        os.makedirs(self.inbox_dir, exist_ok=True)
        os.makedirs(self.printings_dir, exist_ok=True)
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name='print-capture')
            log.info(f'Print capture watcher started (inbox={self.inbox_dir})')

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        while True:
            try:
                await self._scan_once()
            except Exception as exc:
                log.exception(f'capture loop iteration failed: {exc}')
            await asyncio.sleep(self.poll_interval)

    async def _scan_once(self):
        try:
            entries = sorted(os.listdir(self.inbox_dir))
        except FileNotFoundError:
            return
        for entry in entries:
            if not entry.endswith('.json'):
                continue
            notif_path = os.path.join(self.inbox_dir, entry)
            try:
                await self._handle_notification(notif_path)
            except Exception as exc:
                log.exception(f'failed to process {notif_path}: {exc}')
                # rename so we don't loop forever on broken notifications
                try:
                    os.rename(notif_path, notif_path + '.bad')
                except OSError:
                    pass

    async def _handle_notification(self, notif_path: str):
        with open(notif_path, 'r') as f:
            data = json.load(f)

        src_pdf = data.get('pdf', '')
        username = (data.get('user') or 'anonymous').strip() or 'anonymous'
        title = data.get('title') or os.path.basename(src_pdf)
        printer = data.get('printer', '')

        if not src_pdf or not os.path.isfile(src_pdf):
            log.warning(f'notification {notif_path} references missing PDF {src_pdf}')
            os.remove(notif_path)
            return

        # Build target path: /printings/<user>/<DATETIME>-<basename>.pdf
        user_dir = os.path.join(self.printings_dir, _safe_user(username))
        os.makedirs(user_dir, exist_ok=True)
        stamp = dt.datetime.now().strftime('%Y%m%d%H%M%S')
        basename = sanitize_basename(title)
        target_name = f'{stamp}-{basename}.pdf'
        target_path = os.path.join(user_dir, target_name)
        # In the very unlikely event of a collision (same user, same second,
        # same filename) tack on a short uuid.
        if os.path.exists(target_path):
            target_name = f'{stamp}-{basename}-{uuid.uuid4().hex[:6]}.pdf'
            target_path = os.path.join(user_dir, target_name)

        shutil.move(src_pdf, target_path)
        os.remove(notif_path)

        try:
            size = os.path.getsize(target_path)
        except OSError:
            size = 0
        pages = count_pdf_pages(target_path)

        job = PrintJob(
            id=stamp + '-' + uuid.uuid4().hex[:8],
            username=username,
            title=title,
            filename=target_name,
            path=target_path,
            pages=pages,
            size=size,
            printer=printer,
            status='finished',
        )
        log.info(
            f'[{username}] captured print "{title}" -> {target_path} '
            f'({pages} pages, {size} bytes)'
        )

        # Persist in the per-user print log
        if self.log_appender:
            try:
                self.log_appender(
                    username,
                    title=job.title,
                    filename=job.filename,
                    pages=job.pages,
                    size=job.size,
                    printer=job.printer,
                    status=job.status,
                )
            except Exception as exc:
                log.warning(f'failed to append print log for {username}: {exc}')

        # Remember for /history endpoint until the UI fetches via api/log
        self.recent.setdefault(username, []).append(job)
        if len(self.recent[username]) > 100:
            self.recent[username] = self.recent[username][-100:]

        # Notify the user's Socket.IO room
        notifier = self.notifier_factory(username)
        if notifier:
            try:
                await notifier.added(job)
                await notifier.completed(job)
            except Exception as exc:
                log.debug(f'notifier emit failed for {username}: {exc}')


def _safe_user(username: str) -> str:
    """Strip any path-traversal characters from a username for use as a dir."""
    return re.sub(r'[^A-Za-z0-9_.-]', '_', username) or 'anonymous'


# ---------------------------------------------------------------------------
# Backwards-compat aliases (so main.py can import a familiar surface).
# ---------------------------------------------------------------------------

# `Job` is the legacy name used by the UI socket payloads.
Job = PrintJob
Notifier = PrintQueueNotifier
