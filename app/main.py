#!/usr/bin/env python3
"""
Printers container — aiohttp + Socket.IO backend.

Surface (most routes lifted from videodl-container/main.py to keep the UI
auth / admin / log / 2FA flows identical):

  Auth
    POST   /api/login
    POST   /api/2fa/verify
    POST   /api/2fa/setup
    POST   /api/2fa/activate
    POST   /api/2fa/disable
    POST   /api/logout
    POST   /api/logout-all
    POST   /api/change-password
    GET    /api/me
    GET    /api/first-run
    GET    /api/recovery-available
    POST   /api/forgot-password
    GET    /api/reset-password

  Email + SMTP (admin)
    GET    /api/email          (& POST/DELETE)
    GET    /api/email/verify
    GET/POST /api/admin/smtp   (+ /test, /detect)
    POST/DELETE /api/admin/users/{u}/email

  Users / Admin
    GET/POST/PUT/DELETE  /api/admin/users[/{u}]
    POST   /api/admin/users/{u}/reset-password
    POST   /api/admin/users/{u}/disable-2fa
    GET/PUT /api/admin/users/{u}/settings
    POST   /api/admin/users/{u}/rename
    POST   /api/admin/users/{u}/change-homedir

  Print job log (the print-domain analogue of videodl's download log)
    GET    /api/log                              (current user)
    POST   /api/log/clear
    POST   /api/log/recover
    POST   /api/log/delete-file
    GET    /api/admin/global-log                 (admin)
    POST   /api/admin/global-log/clear

  Print job stats + download/preview shadow PDF
    GET    /api/stats                            (current user — daily/month/year/all)
    GET    /api/admin/stats                      (admin — global aggregate)
    GET    /printings/{username}/{filename}      (auth: owner or admin)

  CUPS / Samba admin
    GET    /api/admin/printers                   (list cups queues, structured)
    GET    /api/admin/printers/devices           (lpinfo -v — discover URIs)
    GET    /api/admin/printers/drivers           (lpinfo -m — list PPDs)
    GET    /api/admin/printers/{name}/ping       (probe reachability)
    POST   /api/admin/printers                   (create queue)
    PUT    /api/admin/printers/{name}            (modify queue)
    POST   /api/admin/printers/{name}/enable     (cupsenable + cupsaccept)
    POST   /api/admin/printers/{name}/disable    (cupsdisable)
    DELETE /api/admin/printers/{name}            (delete cups queue)

  Socket.IO  (rooms keyed by username)
    event 'added'     — new print job captured
    event 'completed' — same job (kept for UI compatibility)
"""

# pylint: disable=no-member

import os
import sys
import asyncio
import logging
import json
import re
import socket
import ssl
import subprocess
import shlex
from pathlib import Path

import tomllib
import socketio
from aiohttp import web
from aiohttp.log import access_logger

from printer_engine import PrintCapture, PrintJob, PrintQueueNotifier
import user_manager
import driver_manager

log = logging.getLogger('main')


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
def _read_container_version() -> str:
    env_ver = os.getenv('PRINTERS_VERSION', '')
    if env_ver and env_ver != 'dev':
        return env_ver
    try:
        pyproject = Path(__file__).resolve().parent.parent / 'pyproject.toml'
        with open(pyproject, 'rb') as f:
            return tomllib.load(f).get('project', {}).get('version', env_ver or 'dev')
    except Exception:
        return env_ver or 'dev'


CONTAINER_VERSION = _read_container_version()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def parse_log_level(level: str):
    return {
        'DEBUG': logging.DEBUG, 'INFO': logging.INFO,
        'WARNING': logging.WARNING, 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL,
    }.get((level or 'INFO').upper(), logging.INFO)


if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=parse_log_level(os.environ.get('LOGLEVEL', 'INFO')))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG_DIR = os.environ.get('CONFIG_DIR', '/configs')
PRINTINGS_DIR = os.environ.get('PRINTINGS_DIR', '/printings')
LOG_DIR = os.environ.get('LOG_DIR', '/logs')
GLOBAL_DIR = os.path.join(CONFIG_DIR, 'database', 'global')
CONFIG_FILE = os.path.join(GLOBAL_DIR, 'printers.conf')


def _load_config_file() -> dict:
    if not os.path.isfile(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as exc:
        log.warning(f'failed to read {CONFIG_FILE}: {exc}')
        return {}


def _save_config_file(data: dict):
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        log.warning(f'failed to write {CONFIG_FILE}: {exc}')


_PERSISTENT_KEYS = ('LOGLEVEL',)


class Config:
    _DEFAULTS = {
        'HOST': '0.0.0.0',
        'PORT': os.environ.get('PORT', '8082'),
        'URL_PREFIX': '/',
        'HTTPS': 'false',
        'CERTFILE': '',
        'KEYFILE': '',
        'LOGLEVEL': 'INFO',
        'ENABLE_ACCESSLOG': 'false',
        'BASE_DIR': '',
        'DEFAULT_THEME': 'auto',
        'PRINTINGS_DIR': PRINTINGS_DIR,
        'CUPS_PDF_INBOX': '/var/spool/cups-pdf/INBOX',
    }
    _BOOLEAN = ('HTTPS', 'ENABLE_ACCESSLOG')

    def __init__(self):
        file_cfg = _load_config_file()
        for k, v in self._DEFAULTS.items():
            if k in _PERSISTENT_KEYS and k in file_cfg:
                setattr(self, k, file_cfg[k])
            else:
                setattr(self, k, os.environ.get(k, v))
        for k in self._BOOLEAN:
            v = getattr(self, k)
            setattr(self, k, str(v).lower() in ('true', '1', 'on', 'yes'))
        if not self.URL_PREFIX.endswith('/'):
            self.URL_PREFIX += '/'

        if _PERSISTENT_KEYS:
            save_cfg = {k: getattr(self, k) for k in _PERSISTENT_KEYS if hasattr(self, k)}
            if save_cfg != file_cfg:
                _save_config_file(save_cfg)


config = Config()
logging.getLogger().setLevel(parse_log_level(config.LOGLEVEL))


# ---------------------------------------------------------------------------
# Web app + Socket.IO
# ---------------------------------------------------------------------------
class _Serializer(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        if hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
            try:
                return list(obj)
            except Exception:
                pass
        return super().default(obj)


serializer = _Serializer()
app = web.Application()
sio = socketio.AsyncServer(cors_allowed_origins='*')
sio.attach(app, socketio_path=config.URL_PREFIX + 'socket.io')
routes = web.RouteTableDef()


# ---------------------------------------------------------------------------
# Bootstrap user system
# ---------------------------------------------------------------------------
user_manager.bootstrap_admin()
user_manager.ensure_unix_accounts()
user_manager._load_all_sessions()


# ---------------------------------------------------------------------------
# Print capture
# ---------------------------------------------------------------------------
_sid_user: dict[str, str] = {}


def _notifier_factory(username: str):
    """Return a notifier that forwards to the user's Socket.IO room."""
    class _Notifier(PrintQueueNotifier):
        async def added(self, job: PrintJob):
            await sio.emit('added', serializer.encode(job), room=username)

        async def completed(self, job: PrintJob):
            await sio.emit('completed', serializer.encode(job), room=username)
            # Also tell admins so the global view updates live.
            await sio.emit('print', serializer.encode({**job.__dict__, 'username': username}), room='__admins__')
    return _Notifier()


capture = PrintCapture(
    notifier_factory=_notifier_factory,
    printings_dir=PRINTINGS_DIR,
    inbox_dir=os.environ.get('CUPS_PDF_INBOX', '/var/spool/cups-pdf/INBOX'),
    log_appender=user_manager.append_print_log,
)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
SESSION_COOKIE = 'printers_session'


def _get_token(request) -> str | None:
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:]
    return request.cookies.get(SESSION_COOKIE)


def _require_auth(request) -> dict:
    token = _get_token(request)
    username = user_manager.get_session_user(token)
    if username is None:
        raise web.HTTPUnauthorized(text='Not authenticated')
    user = user_manager.get_user(username)
    if user is None or not user.get('enabled', True):
        user_manager.destroy_session(token)
        raise web.HTTPUnauthorized(text='Not authenticated')
    return user


def _require_admin(request) -> dict:
    user = _require_auth(request)
    if user.get('group') != 'admin-group':
        raise web.HTTPForbidden(text='Admin access required')
    return user


def _require_admin_or_useradmin(request) -> dict:
    user = _require_auth(request)
    if user.get('group') not in ('admin-group', 'useradmin-group'):
        raise web.HTTPForbidden(text='Admin access required')
    return user


def _can_manage_target(caller: dict, target_username: str, target_group: str) -> tuple[bool, str]:
    if target_username == 'admin':
        return False, 'Cannot modify the admin user.'
    g = caller.get('group', '')
    if g == 'admin-group':
        return True, ''
    if g == 'useradmin-group' and target_group in ('users-group', 'useradmin-group'):
        return True, ''
    return False, 'Insufficient permissions.'


# ---------------------------------------------------------------------------
# Index / static
# ---------------------------------------------------------------------------
UI_ROOT = os.path.join(config.BASE_DIR, 'ui/dist/printers/browser')


@routes.get(config.URL_PREFIX)
def index(request):
    response = web.FileResponse(os.path.join(UI_ROOT, 'index.html'))
    if 'printers_theme' not in request.cookies:
        response.set_cookie('printers_theme', config.DEFAULT_THEME)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


# Serve service-worker scripts with no-cache to ensure updates propagate
@routes.get(config.URL_PREFIX + 'ngsw-worker.js')
def serve_ngsw_worker(request):
    # Return a self-unregistering SW script to kill the old Angular NGSW
    kill_script = """self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(names => Promise.all(names.map(n => caches.delete(n))))
    .then(() => self.clients.claim())
    .then(() => self.registration.unregister())
    .then(() => self.clients.matchAll())
    .then(clients => clients.forEach(c => c.navigate(c.url)))
  );
});
"""
    resp = web.Response(text=kill_script, content_type='text/javascript')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Clear-Site-Data'] = '"cache", "storage"'
    return resp


@routes.get(config.URL_PREFIX + 'custom-service-worker.js')
def serve_custom_sw(request):
    path = os.path.join(UI_ROOT, 'custom-service-worker.js')
    if not os.path.isfile(path):
        return web.Response(status=404, text='Not found')
    resp = web.FileResponse(path)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


@routes.get(config.URL_PREFIX + 'ngsw.json')
def serve_ngsw_json(request):
    path = os.path.join(UI_ROOT, 'ngsw.json')
    if not os.path.isfile(path):
        return web.Response(status=404, text='Not found')
    resp = web.FileResponse(path)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Clear-Site-Data'] = '"cache", "storage"'
    return resp


@routes.get(config.URL_PREFIX + 'version')
@routes.get(config.URL_PREFIX + 'api/version')
def get_version(request):
    return web.json_response({'version': CONTAINER_VERSION})


@routes.get(config.URL_PREFIX + 'api/first-run')
def first_run_check(request):
    return web.json_response({'first_run': user_manager.is_first_run()})


# ---------------------------------------------------------------------------
# Login + 2FA
# ---------------------------------------------------------------------------
@routes.post(config.URL_PREFIX + 'api/login')
async def api_login(request):
    post = await request.json()
    username = post.get('username', '').strip()
    password = post.get('password', '')
    totp_code = post.get('totp_code', '').strip()
    if not username or not password:
        return web.json_response({'status': 'error', 'msg': 'Username and password required.'}, status=400)

    penalty = user_manager.get_2fa_penalty_remaining(username)
    if penalty > 0:
        return web.json_response({'status': 'error',
                                  'msg': f'Too many failed 2FA attempts. Try again in {penalty} seconds.',
                                  '2fa_penalty': penalty}, status=429)

    user, error_msg = user_manager.authenticate(username, password)
    if user is None:
        return web.json_response({'status': 'error', 'msg': error_msg}, status=401)

    # Lazy-sync Samba password on every successful login so SMB clients
    # always have the correct credentials (handles bootstrap mismatch and
    # password changes that bypassed the normal sync path).
    user_manager.set_smb_password(username, password)

    if user_manager.is_2fa_enabled(username):
        if not totp_code:
            temp_token = user_manager.create_2fa_pending(username)
            return web.json_response({'status': '2fa_required',
                                      'msg': 'Two-factor authentication required.',
                                      '2fa_token': temp_token})
        secret = user_manager.get_totp_secret(username)
        if not user_manager.verify_totp(secret, totp_code):
            return web.json_response({'status': 'error', 'msg': 'Invalid 2FA code.'}, status=401)

    token = user_manager.create_session(username)
    resp = web.json_response({
        'status': 'ok', 'token': token, 'username': username,
        'group': user.get('group', 'users-group'),
        'must_change_password': user.get('must_change_password', False),
        'first_run': user_manager.is_first_run(),
    })
    resp.set_cookie(SESSION_COOKIE, token, max_age=user_manager.SESSION_TTL, httponly=True, samesite='Strict')
    return resp


@routes.post(config.URL_PREFIX + 'api/2fa/verify')
async def api_2fa_verify(request):
    post = await request.json()
    temp_token = post.get('2fa_token', '').strip()
    totp_code = post.get('totp_code', '').strip()
    if not temp_token or not totp_code:
        return web.json_response({'status': 'error', 'msg': '2FA token and code required.'}, status=400)

    pending = user_manager.get_2fa_pending(temp_token)
    if pending is None:
        return web.json_response({'status': 'error', 'msg': '2FA session expired. Please login again.'}, status=401)

    username = pending['username']
    penalty = user_manager.get_2fa_penalty_remaining(username)
    if penalty > 0:
        user_manager.remove_2fa_pending(temp_token)
        return web.json_response({'status': 'error',
                                  'msg': f'Too many failed 2FA attempts. Try again in {penalty} seconds.',
                                  '2fa_penalty': penalty}, status=429)

    secret = user_manager.get_totp_secret(username)
    if not user_manager.verify_totp(secret, totp_code):
        attempts = user_manager.increment_2fa_attempts(temp_token)
        if attempts >= user_manager._2FA_MAX_RETRIES:
            user_manager.remove_2fa_pending(temp_token)
            user_manager.set_2fa_penalty(username)
            return web.json_response({'status': 'error',
                                      'msg': 'Failed 2FA authentication. 1 minute penalty applied.',
                                      '2fa_penalty': user_manager._2FA_PENALTY_SECONDS,
                                      '2fa_failed': True}, status=429)
        remaining = user_manager._2FA_MAX_RETRIES - attempts
        return web.json_response({'status': 'error', 'msg': f'Invalid 2FA code. {remaining} attempt(s) remaining.',
                                  '2fa_retries_left': remaining}, status=401)

    user_manager.remove_2fa_pending(temp_token)
    user = user_manager.get_user(username)
    token = user_manager.create_session(username)
    resp = web.json_response({
        'status': 'ok', 'token': token, 'username': username,
        'group': user.get('group', 'users-group') if user else 'users-group',
        'must_change_password': user.get('must_change_password', False) if user else False,
        'first_run': user_manager.is_first_run(),
    })
    resp.set_cookie(SESSION_COOKIE, token, max_age=user_manager.SESSION_TTL, httponly=True, samesite='Strict')
    return resp


@routes.post(config.URL_PREFIX + 'api/2fa/setup')
async def api_2fa_setup(request):
    user = _require_auth(request)
    username = user['username']
    if user_manager.is_2fa_enabled(username):
        return web.json_response({'status': 'error', 'msg': '2FA is already enabled.'}, status=400)
    secret = user_manager.generate_totp_secret()
    return web.json_response({
        'status': 'ok',
        'secret': secret,
        'qr_code': user_manager.generate_totp_qr_base64(username, secret),
        'uri': user_manager.get_totp_provisioning_uri(username, secret),
    })


@routes.post(config.URL_PREFIX + 'api/2fa/activate')
async def api_2fa_activate(request):
    user = _require_auth(request)
    username = user['username']
    post = await request.json()
    secret = post.get('secret', '').strip()
    code = post.get('code', '').strip()
    if not secret or not code:
        return web.json_response({'status': 'error', 'msg': 'Secret and verification code required.'}, status=400)
    if user_manager.is_2fa_enabled(username):
        return web.json_response({'status': 'error', 'msg': '2FA is already enabled.'}, status=400)
    if not user_manager.verify_totp(secret, code):
        return web.json_response({'status': 'error', 'msg': 'Invalid verification code.'}, status=400)
    ok, msg = user_manager.enable_2fa(username, secret)
    if not ok:
        return web.json_response({'status': 'error', 'msg': msg}, status=500)
    user_manager.destroy_all_sessions(username)
    await _disconnect_user_sockets(username)
    return web.json_response({'status': 'ok', 'msg': '2FA activated. You will be logged out.'})


@routes.post(config.URL_PREFIX + 'api/2fa/disable')
async def api_2fa_disable(request):
    user = _require_auth(request)
    username = user['username']
    post = await request.json()
    code = post.get('code', '').strip()
    if not code:
        return web.json_response({'status': 'error', 'msg': 'Current 2FA code required to disable.'}, status=400)
    if not user_manager.is_2fa_enabled(username):
        return web.json_response({'status': 'error', 'msg': '2FA is not enabled.'}, status=400)
    secret = user_manager.get_totp_secret(username)
    if not user_manager.verify_totp(secret, code):
        return web.json_response({'status': 'error', 'msg': 'Invalid 2FA code.'}, status=401)
    ok, msg = user_manager.disable_2fa(username)
    if not ok:
        return web.json_response({'status': 'error', 'msg': msg}, status=500)
    user_manager.destroy_all_sessions(username)
    await _disconnect_user_sockets(username)
    return web.json_response({'status': 'ok', 'msg': '2FA disabled. You will be logged out.'})


# ---------------------------------------------------------------------------
# Logout, change password, me
# ---------------------------------------------------------------------------
@routes.post(config.URL_PREFIX + 'api/logout')
async def api_logout(request):
    user_manager.destroy_session(_get_token(request))
    resp = web.json_response({'status': 'ok'})
    resp.del_cookie(SESSION_COOKIE)
    return resp


@routes.post(config.URL_PREFIX + 'api/logout-all')
async def api_logout_all(request):
    user = _require_auth(request)
    username = user['username']
    count = user_manager.destroy_all_sessions(username)
    await _disconnect_user_sockets(username)
    resp = web.json_response({'status': 'ok', 'sessions_destroyed': count})
    resp.del_cookie(SESSION_COOKIE)
    return resp


@routes.post(config.URL_PREFIX + 'api/change-password')
async def api_change_password(request):
    user = _require_auth(request)
    post = await request.json()
    cur = post.get('current_password', '')
    new1 = post.get('new_password', '')
    new2 = post.get('new_password_confirm', '')
    if new1 != new2:
        return web.json_response({'status': 'error', 'msg': 'New passwords do not match.'}, status=400)
    ok, msg = user_manager.change_password(user['username'], cur, new1)
    if not ok:
        return web.json_response({'status': 'error', 'msg': msg}, status=400)
    if user_manager.is_first_run():
        user_manager.mark_first_run_done()
    user_manager.destroy_session(_get_token(request))
    resp = web.json_response({'status': 'ok', 'msg': msg})
    resp.del_cookie(SESSION_COOKIE)
    return resp


@routes.get(config.URL_PREFIX + 'api/me')
async def api_me(request):
    user = _require_auth(request)
    username = user['username']
    smtp_cfg = user_manager.load_smtp_config()
    email_info = user_manager.get_user_email(username)
    return web.json_response({
        'status': 'ok', 'username': username,
        'group': user.get('group', 'users-group'),
        'must_change_password': user.get('must_change_password', False),
        'totp_enabled': user_manager.is_2fa_enabled(username),
        'email': email_info.get('email', ''),
        'email_status': email_info.get('email_status', 'none'),
        'smtp_configured': bool(smtp_cfg.get('host')) and smtp_cfg.get('status') == 'verified',
        'first_run': user_manager.is_first_run(),
        'locked_settings': user.get('locked_settings', []),
    })


# ---------------------------------------------------------------------------
# Email + SMTP
# ---------------------------------------------------------------------------
@routes.get(config.URL_PREFIX + 'api/email')
async def api_get_email(request):
    user = _require_auth(request)
    return web.json_response({'status': 'ok', **user_manager.get_user_email(user['username'])})


@routes.post(config.URL_PREFIX + 'api/email')
async def api_set_email(request):
    user = _require_auth(request)
    post = await request.json()
    email = post.get('email', '').strip()
    if not email or '@' not in email:
        return web.json_response({'status': 'error', 'msg': 'A valid email address is required.'}, status=400)
    ok, token = user_manager.set_user_email(user['username'], email)
    if not ok:
        return web.json_response({'status': 'error', 'msg': token}, status=400)
    smtp_cfg = user_manager.load_smtp_config()
    if smtp_cfg.get('status') == 'verified':
        scheme = 'https' if request.secure else 'http'
        host_header = request.headers.get('X-Forwarded-Host', request.headers.get('Host', 'localhost'))
        base_url = f'{scheme}://{host_header}{config.URL_PREFIX}'
        verification_url = f'{base_url}api/email/verify?token={token}'
        sent_ok, sent_msg = user_manager.send_verification_email(email, user['username'], verification_url)
        msg = 'Verification email sent.' if sent_ok else f'Email saved but could not send verification: {sent_msg}'
        return web.json_response({'status': 'ok', 'msg': msg, 'email_status': 'pending'})
    return web.json_response({'status': 'ok', 'msg': 'Email saved. SMTP not configured.', 'email_status': 'pending'})


@routes.delete(config.URL_PREFIX + 'api/email')
async def api_delete_email(request):
    user = _require_auth(request)
    ok, msg = user_manager.delete_user_email(user['username'])
    return web.json_response({'status': 'ok' if ok else 'error', 'msg': msg}, status=200 if ok else 400)


@routes.get(config.URL_PREFIX + 'api/email/verify')
async def api_verify_email(request):
    token = request.query.get('token', '')
    if not token:
        return web.Response(text='Missing token.', content_type='text/html', status=400)
    ok, msg = user_manager.verify_user_email(token)
    color = '#198754' if ok else '#dc3545'
    title = '&#10003; Email Verified' if ok else '&#10007; Verification Failed'
    html = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>Printers - Email Verification</title>'
        f'<style>body{{font-family:system-ui;display:flex;align-items:center;justify-content:center;'
        f'min-height:100vh;margin:0;background:#f8f9fa}}div{{text-align:center;padding:2rem;'
        f'border-radius:12px;background:#fff;box-shadow:0 4px 16px rgba(0,0,0,.1)}}'
        f'h2{{color:{color}}}</style></head><body><div><h2>{title}</h2>'
        f'<p>{msg}</p><p><a href="{config.URL_PREFIX}">Go to Printers</a></p></div></body></html>'
    )
    return web.Response(text=html, content_type='text/html', status=200 if ok else 400)


# Password recovery
@routes.get(config.URL_PREFIX + 'api/recovery-available')
async def api_recovery_available(request):
    return web.json_response({'available': user_manager.is_smtp_active()})


@routes.post(config.URL_PREFIX + 'api/forgot-password')
async def api_forgot_password(request):
    post = await request.json()
    username = post.get('username', '').strip()
    email = post.get('email', '').strip()
    if not username or not email:
        return web.json_response({'status': 'error', 'msg': 'Username and email are required.'}, status=400)
    client_ip = request.headers.get('X-Forwarded-For', request.remote or 'unknown').split(',')[0].strip()
    penalty = user_manager.get_recovery_penalty(client_ip)
    if penalty > 0:
        return web.json_response({'status': 'error', 'msg': f'Too many attempts. Try again in {penalty} seconds.', 'penalty': penalty}, status=429)
    ok, msg = user_manager.validate_recovery_request(username, email)
    if not ok:
        user_manager.record_recovery_attempt(client_ip)
        return web.json_response({'status': 'error', 'msg': msg}, status=401)
    user_manager.clear_recovery_attempts(client_ip)
    token = user_manager.create_recovery_token(username)
    scheme = 'https' if request.secure else 'http'
    host_header = request.headers.get('X-Forwarded-Host', request.headers.get('Host', 'localhost'))
    base_url = f'{scheme}://{host_header}{config.URL_PREFIX}'
    recovery_url = f'{base_url}api/reset-password?token={token}'
    sent_ok, sent_msg = user_manager.send_recovery_link_email(email, username, recovery_url)
    if sent_ok:
        return web.json_response({'status': 'ok', 'msg': 'Recovery email has been sent.'})
    return web.json_response({'status': 'error', 'msg': f'Failed to send recovery email: {sent_msg}'}, status=500)


@routes.get(config.URL_PREFIX + 'api/reset-password')
async def api_reset_password(request):
    token = request.query.get('token', '')
    if not token:
        return web.Response(text='Missing token.', content_type='text/html', status=400)
    username = user_manager.consume_recovery_token(token)
    if username is None:
        html = ('<!DOCTYPE html><html><body><h2>Invalid or expired link.</h2>'
                f'<a href="{config.URL_PREFIX}">Back to Printers</a></body></html>')
        return web.Response(text=html, content_type='text/html', status=400)
    ok, msg, new_password = user_manager.execute_password_recovery(username)
    if not ok:
        return web.Response(text=f'<h2>Reset failed.</h2><p>{msg}</p>', content_type='text/html', status=500)
    email_addr = user_manager.get_user_email(username).get('email', '')
    if email_addr:
        user_manager.send_new_password_email(email_addr, username, new_password)
    html = (
        '<!DOCTYPE html><html><body style="font-family:system-ui;text-align:center;padding:2rem;">'
        f'<h2 style="color:#198754;">Password Reset Successful</h2>'
        f'<p>A new password has been emailed to you.</p>'
        f'<p><a href="{config.URL_PREFIX}" style="padding:8px 20px;background:#0d6efd;color:#fff;'
        'text-decoration:none;border-radius:6px;">Go to Printers</a></p></body></html>'
    )
    return web.Response(text=html, content_type='text/html')


# ---------------------------------------------------------------------------
# Print job log
# ---------------------------------------------------------------------------
@routes.get(config.URL_PREFIX + 'api/log')
async def api_get_log(request):
    user = _require_auth(request)
    entries = user_manager.get_download_log(user['username'], download_dir=PRINTINGS_DIR)
    return web.json_response({'status': 'ok', 'entries': entries, 'has_files': True})


@routes.post(config.URL_PREFIX + 'api/log/clear')
async def api_clear_log(request):
    user = _require_auth(request)
    user_manager.clear_download_log(user['username'])
    return web.json_response({'status': 'ok'})


@routes.post(config.URL_PREFIX + 'api/log/recover')
async def api_recover_log(request):
    user = _require_auth(request)
    count = user_manager.recover_download_log(user['username'], download_dir=PRINTINGS_DIR)
    return web.json_response({'status': 'ok', 'recovered': count})


@routes.post(config.URL_PREFIX + 'api/log/delete-file')
async def api_log_delete_file(request):
    user = _require_auth(request)
    username = user['username']
    post = await request.json()
    filenames = post.get('filenames', [])
    single = post.get('filename', '').strip()
    if single and not filenames:
        filenames = [single]
    if not filenames:
        return web.json_response({'status': 'error', 'msg': 'No filename specified.'}, status=400)
    user_dir = os.path.join(PRINTINGS_DIR, username)
    real_base = os.path.realpath(user_dir)
    deleted: list[str] = []
    errors: list[str] = []
    for fn in filenames:
        fn = fn.strip()
        if not fn:
            continue
        full_path = os.path.realpath(os.path.join(user_dir, fn))
        if not (full_path == real_base or full_path.startswith(real_base + os.sep)):
            errors.append(f'{fn}: invalid path')
            continue
        if not os.path.isfile(full_path):
            errors.append(f'{fn}: not found')
            continue
        try:
            os.remove(full_path)
            deleted.append(fn)
        except Exception as exc:
            errors.append(f'{fn}: {exc}')
    remove_from_log = post.get('remove_from_log', False)
    removed = user_manager.remove_download_log_entries(username, deleted) if remove_from_log and deleted else 0
    return web.json_response({'status': 'ok', 'deleted': deleted, 'removed_log_entries': removed, 'errors': errors})


@routes.get(config.URL_PREFIX + 'api/admin/global-log')
async def api_admin_global_log(request):
    _require_admin(request)
    return web.json_response({'status': 'ok',
                              'entries': user_manager.get_global_download_log(download_dir=PRINTINGS_DIR)})


@routes.post(config.URL_PREFIX + 'api/admin/global-log/clear')
async def api_admin_clear_global_log(request):
    _require_admin(request)
    post = await request.json()
    return web.json_response(user_manager.clear_global_download_log(archive=post.get('archive', False)))


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
@routes.get(config.URL_PREFIX + 'api/stats')
async def api_stats(request):
    user = _require_auth(request)
    return web.json_response({'status': 'ok',
                              'username': user['username'],
                              'stats': user_manager.get_print_stats(user['username'])})


@routes.get(config.URL_PREFIX + 'api/admin/stats')
async def api_admin_stats(request):
    _require_admin(request)
    per_user = {}
    for u in user_manager.list_users():
        per_user[u['username']] = user_manager.get_print_stats(u['username'])
    return web.json_response({
        'status': 'ok',
        'global': user_manager.get_print_stats(None),
        'per_user': per_user,
    })


# ---------------------------------------------------------------------------
# Shadow PDF download (owner or admin)
# ---------------------------------------------------------------------------
@routes.get(config.URL_PREFIX + 'printings/{username}/{filename}')
async def api_get_printing(request):
    user = _require_auth(request)
    username = request.match_info['username']
    filename = request.match_info['filename']
    if user['username'] != username and user.get('group') != 'admin-group':
        raise web.HTTPForbidden(text='You can only download your own print files')
    user_dir = os.path.join(PRINTINGS_DIR, username)
    full = os.path.realpath(os.path.join(user_dir, filename))
    base = os.path.realpath(user_dir)
    if not (full == base or full.startswith(base + os.sep)):
        raise web.HTTPBadRequest(text='Invalid path')
    if not os.path.isfile(full):
        raise web.HTTPNotFound()
    return web.FileResponse(full, headers={'Content-Disposition': f'inline; filename="{filename}"'})


# ---------------------------------------------------------------------------
# CUPS / Samba admin
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        return 127, '', str(exc)
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'


# ---------------------------------------------------------------------------
# Samba explicit printer share management.
# The smb.conf has a marker line after which we manage printer sections.
# ---------------------------------------------------------------------------
_SMB_CONF = os.path.join(CONFIG_DIR, 'samba', 'smb.conf')
_SMB_MARKER = '# --- PRINTER SHARES (managed by backend — do not edit below this line) ---'


def _smb_printer_block(name: str, comment: str = '') -> str:
    """Generate a Samba [share] block for a CUPS printer."""
    desc = comment or name
    return (
        f'\n[{name}]\n'
        f'   comment = {desc}\n'
        f'   path = /var/spool/samba\n'
        f'   printable = yes\n'
        f'   printer name = {name}\n'
        f'   browseable = yes\n'
        f'   guest ok = no\n'
        f'   read only = yes\n'
        f'   create mask = 0700\n'
        f'   use client driver = yes\n'
        f'   force user = root\n'
    )


def _sync_smb_share(name: str, action: str = 'add', comment: str = ''):
    """Add or remove an explicit printer share in smb.conf, then reload Samba."""
    if not os.path.isfile(_SMB_CONF):
        log.warning(f'smb.conf not found at {_SMB_CONF}')
        return
    with open(_SMB_CONF, 'r') as f:
        content = f.read()

    # Split at marker
    if _SMB_MARKER not in content:
        # Append marker if missing (upgrade from older template)
        content = content.rstrip() + '\n\n' + _SMB_MARKER + '\n'

    before, _, after = content.partition(_SMB_MARKER)

    # Parse existing printer sections from the "after" part
    # Each section starts with \n[Name]\n
    sections: dict[str, str] = {}
    # Split on section headers
    parts = re.split(r'\n(?=\[)', after)
    for part in parts:
        m = re.match(r'\[([^\]]+)\]', part.strip())
        if m:
            sections[m.group(1)] = '\n' + part.strip() + '\n'

    if action == 'add':
        sections[name] = _smb_printer_block(name, comment)
    elif action == 'remove' and name in sections:
        del sections[name]

    # Rebuild
    new_after = ''.join(sections.values())
    new_content = before + _SMB_MARKER + '\n' + new_after

    with open(_SMB_CONF, 'w') as f:
        f.write(new_content)

    # Reload Samba config
    _run(['smbcontrol', 'all', 'reload-config'])
    log.info(f'smb share {action}: {name}')


def _sync_all_smb_shares():
    """Sync all CUPS printers to smb.conf (used at startup)."""
    printers = _parse_lpstat_printers()
    for p in printers:
        name = p.get('name', '')
        if name:
            _sync_smb_share(name, 'add')


def _parse_lpstat_printers() -> list[dict]:
    """Parse `lpstat -p -v` to a structured list."""
    rc_p, out_p, _ = _run(['lpstat', '-p'])
    rc_v, out_v, _ = _run(['lpstat', '-v'])
    printers: dict[str, dict] = {}
    # `lpstat -p` lines:
    #   enabled:  "printer <name> is idle.  enabled since ..."
    #   disabled: "printer <name> disabled since ..."
    for line in (out_p or '').splitlines():
        if not line.startswith('printer '):
            continue
        parts = line.split(maxsplit=3)
        if len(parts) < 3:
            continue
        name = parts[1]
        rest = line.lower()
        is_disabled = 'disabled' in rest
        # extract clean state keyword
        if is_disabled:
            state = 'disabled'
        elif 'idle' in rest:
            state = 'idle'
        elif 'printing' in rest or 'processing' in rest:
            state = 'processing'
        else:
            state = parts[2].rstrip('.').lower() if len(parts) > 2 else 'unknown'
        printers[name] = {
            'name': name,
            'status': state,
            'enabled': not is_disabled,
            'uri': '',
            'accepting': True,
        }
    # `lpstat -v` lines: "device for <name>: <uri>"
    for line in (out_v or '').splitlines():
        if not line.startswith('device for '):
            continue
        rest = line[len('device for '):]
        if ':' not in rest:
            continue
        name, uri = rest.split(':', 1)
        name = name.strip()
        uri = uri.strip()
        printers.setdefault(name, {'name': name, 'status': 'unknown', 'enabled': True, 'accepting': True})
        printers[name]['uri'] = uri
    # `lpstat -a` lines: "<name> accepting requests since ..." or "not accepting"
    rc_a, out_a, _ = _run(['lpstat', '-a'])
    for line in (out_a or '').splitlines():
        parts = line.split(maxsplit=1)
        if not parts:
            continue
        name = parts[0]
        rest = (parts[1] if len(parts) > 1 else '').lower()
        if name in printers:
            printers[name]['accepting'] = 'not accepting' not in rest
    return list(printers.values())


def _printer_uri_reachable(uri: str, timeout: float = 2.0) -> dict:
    """Best-effort reachability test for a CUPS device URI."""
    import socket
    from urllib.parse import urlparse
    if not uri:
        return {'reachable': False, 'msg': 'no URI'}
    # Local/virtual backends are always reachable
    if uri.startswith(('cups-pdf:', 'file:', 'pipe:')):
        return {'reachable': True, 'msg': 'local'}
    if uri.startswith('usb:'):
        # USB device URI like usb://HP/LaserJet?serial=...
        return {'reachable': True, 'msg': 'usb (cannot verify from container)'}
    try:
        if uri.startswith('socket://'):
            p = urlparse(uri)
            host = p.hostname or ''
            port = p.port or 9100
            with socket.create_connection((host, port), timeout=timeout):
                return {'reachable': True, 'msg': f'tcp {host}:{port} ok'}
        if uri.startswith(('http://', 'https://', 'ipp://', 'ipps://')):
            p = urlparse(uri.replace('ipp://', 'http://').replace('ipps://', 'https://'))
            host = p.hostname or ''
            port = p.port or (443 if uri.startswith(('https://', 'ipps://')) else 631)
            with socket.create_connection((host, port), timeout=timeout):
                return {'reachable': True, 'msg': f'tcp {host}:{port} ok'}
        if uri.startswith('lpd://'):
            p = urlparse(uri)
            host = p.hostname or ''
            port = p.port or 515
            with socket.create_connection((host, port), timeout=timeout):
                return {'reachable': True, 'msg': f'tcp {host}:{port} ok'}
    except (socket.gaierror, socket.timeout, OSError) as exc:
        return {'reachable': False, 'msg': str(exc)}
    return {'reachable': False, 'msg': 'unknown scheme'}


# ---------------------------------------------------------------------------
# Printer model detection (PJL / SNMP / IPP)
# ---------------------------------------------------------------------------
def _detect_pjl(host: str, port: int = 9100, timeout: float = 3.0) -> str:
    """Query printer model via PJL INFO ID over JetDirect."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            # PJL Universal Exit + INFO ID
            s.sendall(b'\x1b%-12345X@PJL INFO ID\r\n\x1b%-12345X')
            data = b''
            while True:
                try:
                    chunk = s.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                    if b'\x0c' in data or b'\x1b' in data[1:] or len(data) > 4096:
                        break
                except socket.timeout:
                    break
            # Parse response — typically: @PJL INFO ID\r\n"HP LaserJet ..."\r\n
            text = data.decode('utf-8', errors='replace')
            for line in text.splitlines():
                line = line.strip().strip('"').strip()
                if line and not line.startswith('@PJL') and not line.startswith('\x1b'):
                    return line
    except (socket.gaierror, socket.timeout, OSError):
        pass
    return ''


def _detect_snmp(host: str, community: str = 'public', timeout: float = 2.0) -> str:
    """Query printer model via SNMPv1 GET (sysDescr.0 and hrDeviceDescr.1)."""
    import socket
    import struct

    def _encode_oid(oid_str: str) -> bytes:
        parts = [int(x) for x in oid_str.split('.')]
        result = bytes([parts[0] * 40 + parts[1]])
        for p in parts[2:]:
            if p < 128:
                result += bytes([p])
            else:
                # Multi-byte encoding
                enc = []
                while p > 0:
                    enc.append(p & 0x7f)
                    p >>= 7
                enc.reverse()
                for i, b in enumerate(enc):
                    result += bytes([b | 0x80] if i < len(enc) - 1 else [b])
        return result

    def _build_snmp_get(oid_str: str, comm: str) -> bytes:
        oid_bytes = _encode_oid(oid_str)
        # NULL value
        varbind = bytes([0x06, len(oid_bytes)]) + oid_bytes + b'\x05\x00'
        varbind_seq = bytes([0x30, len(varbind)]) + varbind
        varbind_list = bytes([0x30, len(varbind_seq)]) + varbind_seq
        # GetRequest PDU (0xA0), request-id=1, error=0, error-index=0
        request_id = b'\x02\x01\x01'
        error = b'\x02\x01\x00'
        error_idx = b'\x02\x01\x00'
        pdu_content = request_id + error + error_idx + varbind_list
        pdu = bytes([0xA0, len(pdu_content)]) + pdu_content
        # SNMP message: version=0 (SNMPv1), community, PDU
        version = b'\x02\x01\x00'
        comm_bytes = comm.encode()
        community_field = bytes([0x04, len(comm_bytes)]) + comm_bytes
        msg_content = version + community_field + pdu
        return bytes([0x30, len(msg_content)]) + msg_content

    def _parse_snmp_response(data: bytes) -> str:
        # Simplified: find the OctetString value in the response
        # Look for 0x04 (OctetString) followed by length and value
        i = 0
        last_str = ''
        while i < len(data) - 2:
            if data[i] == 0x04:  # OctetString
                length = data[i + 1]
                if length < 128 and i + 2 + length <= len(data):
                    val = data[i + 2:i + 2 + length].decode('utf-8', errors='replace').strip()
                    if len(val) > 3 and val != community:
                        last_str = val
                i += 1
            else:
                i += 1
        return last_str

    # Try sysDescr.0 (1.3.6.1.2.1.1.1.0) and hrDeviceDescr.1 (1.3.6.1.2.1.25.3.2.1.3.1)
    for oid in ('1.3.6.1.2.1.1.1.0', '1.3.6.1.2.1.25.3.2.1.3.1'):
        try:
            pkt = _build_snmp_get(oid, community)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(pkt, (host, 161))
            data, _ = sock.recvfrom(4096)
            sock.close()
            result = _parse_snmp_response(data)
            if result:
                return result
        except (socket.gaierror, socket.timeout, OSError):
            continue
    return ''


def _detect_ipp(host: str, port: int = 631, timeout: float = 3.0) -> str:
    """Query printer model via IPP Get-Printer-Attributes."""
    import socket
    import struct

    # Minimal IPP Get-Printer-Attributes request
    # IPP version 1.1, operation Get-Printer-Attributes (0x000B)
    uri = f'ipp://{host}:{port}/ipp/print'
    uri_bytes = uri.encode()

    # Build IPP payload
    ipp = b''
    ipp += struct.pack('>BBH', 1, 1, 0x000B)  # version 1.1, op=Get-Printer-Attributes
    ipp += struct.pack('>I', 1)  # request-id = 1
    # Operation attributes group (0x01)
    ipp += b'\x01'
    # attributes-charset = utf-8
    ipp += struct.pack('>BH', 0x47, 18) + b'attributes-charset' + struct.pack('>H', 5) + b'utf-8'
    # attributes-natural-language = en
    ipp += struct.pack('>BH', 0x48, 27) + b'attributes-natural-language' + struct.pack('>H', 2) + b'en'
    # printer-uri
    ipp += struct.pack('>BH', 0x45, 11) + b'printer-uri' + struct.pack('>H', len(uri_bytes)) + uri_bytes
    # requested-attributes = printer-make-and-model
    attr = b'printer-make-and-model'
    ipp += struct.pack('>BH', 0x44, 20) + b'requested-attributes' + struct.pack('>H', len(attr)) + attr
    # End of attributes
    ipp += b'\x03'

    # HTTP POST
    http = (
        f'POST /ipp/print HTTP/1.1\r\n'
        f'Host: {host}:{port}\r\n'
        f'Content-Type: application/ipp\r\n'
        f'Content-Length: {len(ipp)}\r\n'
        f'\r\n'
    ).encode() + ipp

    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(http)
            data = b''
            while len(data) < 8192:
                try:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                except socket.timeout:
                    break
            # Find printer-make-and-model in the IPP response
            marker = b'printer-make-and-model'
            idx = data.find(marker)
            if idx >= 0:
                # Skip tag(1) + name-length(2) + name + value-length(2)
                pos = idx + len(marker)
                if pos + 2 <= len(data):
                    vlen = struct.unpack('>H', data[pos:pos + 2])[0]
                    if pos + 2 + vlen <= len(data):
                        return data[pos + 2:pos + 2 + vlen].decode('utf-8', errors='replace').strip()
    except (socket.gaierror, socket.timeout, OSError):
        pass
    return ''


def _detect_printer_model(uri: str) -> dict:
    """Detect printer model from its URI using PJL, SNMP, and IPP."""
    from urllib.parse import urlparse
    result = {'model': '', 'method': '', 'uri': uri}

    if not uri:
        return result

    # Extract host from URI
    if uri.startswith('socket://'):
        p = urlparse(uri)
        host = p.hostname or ''
        port = p.port or 9100
        if host:
            # Try PJL first (most reliable for JetDirect)
            model = _detect_pjl(host, port)
            if model:
                result['model'] = model
                result['method'] = 'pjl'
                return result
            # Try SNMP
            model = _detect_snmp(host)
            if model:
                result['model'] = model
                result['method'] = 'snmp'
                return result
            # Try IPP (some printers also run IPP)
            model = _detect_ipp(host)
            if model:
                result['model'] = model
                result['method'] = 'ipp'
                return result
    elif uri.startswith(('ipp://', 'ipps://', 'http://', 'https://')):
        p = urlparse(uri.replace('ipp://', 'http://').replace('ipps://', 'https://'))
        host = p.hostname or ''
        port = p.port or 631
        if host:
            model = _detect_ipp(host, port)
            if model:
                result['model'] = model
                result['method'] = 'ipp'
                return result
            model = _detect_snmp(host)
            if model:
                result['model'] = model
                result['method'] = 'snmp'
                return result
    elif uri.startswith('lpd://'):
        p = urlparse(uri)
        host = p.hostname or ''
        if host:
            model = _detect_snmp(host)
            if model:
                result['model'] = model
                result['method'] = 'snmp'
                return result

    return result


@routes.get(config.URL_PREFIX + 'api/admin/printers/detect')
async def api_admin_detect_printer(request):
    """Detect printer model from a device URI or host address."""
    _require_admin(request)
    uri = request.query.get('uri', '').strip()
    host = request.query.get('host', '').strip()
    # If bare host given (no scheme), try socket:// first
    if host and not uri:
        uri = f'socket://{host}:9100'
    if not uri:
        return web.json_response({'status': 'error', 'msg': 'Provide ?uri= or ?host='}, status=400)
    result = _detect_printer_model(uri)
    # If socket:// detection failed and bare host was given, also try SNMP/IPP directly
    if not result['model'] and host:
        model = _detect_snmp(host)
        if model:
            result['model'] = model
            result['method'] = 'snmp'
        else:
            model = _detect_ipp(host)
            if model:
                result['model'] = model
                result['method'] = 'ipp'
    result['status'] = 'ok' if result['model'] else 'not_detected'
    # Include driver suggestion if model detected
    if result.get('model'):
        result['suggested_driver'] = driver_manager.suggest_driver_for_model(result['model'])
    return web.json_response(result)


@routes.post(config.URL_PREFIX + 'api/admin/printers/{name}/install-driver')
async def api_admin_install_driver(request):
    """Download, extract, register and associate a Windows driver for a printer."""
    _require_admin(request)
    name = request.match_info['name']
    post = await request.json() if request.content_length else {}

    # Get the model (from request body or auto-detect)
    model = post.get('model', '')
    url = post.get('url', '')
    drv_name = post.get('driver_name', '')

    if not model and not url:
        # Auto-detect from printer URI
        rc, out, _ = _run(['lpstat', '-v', name])
        uri = ''
        for line in (out or '').splitlines():
            if line.startswith('device for '):
                rest = line[len('device for '):]
                if ':' in rest:
                    uri = rest.split(':', 1)[1].strip()
                    break
        if uri:
            detection = _detect_printer_model(uri)
            model = detection.get('model', '')

    result = driver_manager.install_driver_for_printer(name, model=model, url=url, driver_name=drv_name)
    status_code = 200 if result.get('status') == 'ok' else 500 if result.get('status') == 'error' else 207
    return web.json_response(result, status=status_code)


@routes.get(config.URL_PREFIX + 'api/admin/printers/drivers/installed')
async def api_admin_installed_drivers(request):
    """List Windows drivers installed on the server."""
    _require_admin(request)
    drivers = driver_manager.get_installed_drivers()
    return web.json_response({'status': 'ok', 'drivers': drivers})


@routes.get(config.URL_PREFIX + 'api/admin/printers')
async def api_admin_list_printers(request):
    _require_admin(request)
    printers = _parse_lpstat_printers()
    return web.json_response({'status': 'ok', 'printers': printers})


@routes.get(config.URL_PREFIX + 'api/admin/printers/devices')
async def api_admin_list_devices(request):
    """`lpinfo -v` — list candidate device URIs for the add-wizard."""
    _require_admin(request)
    rc, out, err = _run(['lpinfo', '-v'])
    devices = []
    for line in (out or '').splitlines():
        # Lines: "<class> <uri>"  e.g. "network socket", "direct usb://HP/LaserJet?serial=..."
        parts = line.split(None, 1)
        if len(parts) == 2:
            devices.append({'class': parts[0], 'uri': parts[1].strip()})
    return web.json_response({'status': 'ok' if rc == 0 else 'error',
                              'devices': devices, 'stderr': err})


@routes.get(config.URL_PREFIX + 'api/admin/printers/drivers')
async def api_admin_list_drivers(request):
    """`lpinfo -m` — list available PPD/driver identifiers."""
    _require_admin(request)
    rc, out, err = _run(['lpinfo', '-m'], timeout=60)
    drivers = []
    for line in (out or '').splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            drivers.append({'ppd': parts[0], 'description': parts[1].strip()})
    return web.json_response({'status': 'ok' if rc == 0 else 'error',
                              'drivers': drivers, 'stderr': err})


@routes.get(config.URL_PREFIX + 'api/admin/printers/{name}/ping')
async def api_admin_ping_printer(request):
    _require_admin(request)
    name = request.match_info['name']
    rc, out, _ = _run(['lpstat', '-v', name])
    uri = ''
    for line in (out or '').splitlines():
        if line.startswith('device for '):
            rest = line[len('device for '):]
            if ':' in rest:
                uri = rest.split(':', 1)[1].strip()
                break
    if not uri:
        return web.json_response({'status': 'error', 'msg': 'printer not found'}, status=404)
    result = _printer_uri_reachable(uri)
    # Also detect model if reachable
    if result.get('reachable'):
        detection = _detect_printer_model(uri)
        if detection.get('model'):
            result['model'] = detection['model']
            result['detection_method'] = detection['method']
    return web.json_response({'status': 'ok', 'uri': uri, **result})


@routes.post(config.URL_PREFIX + 'api/admin/printers/{name}/enable')
async def api_admin_enable_printer(request):
    _require_admin(request)
    name = request.match_info['name']
    rc1, _, err1 = _run(['cupsenable', name])
    rc2, _, err2 = _run(['cupsaccept', name])
    if rc1 != 0 or rc2 != 0:
        return web.json_response({'status': 'error', 'msg': err1 or err2}, status=500)
    return web.json_response({'status': 'ok', 'msg': f'Printer "{name}" enabled.'})


@routes.post(config.URL_PREFIX + 'api/admin/printers/{name}/disable')
async def api_admin_disable_printer(request):
    _require_admin(request)
    name = request.match_info['name']
    rc1, _, err1 = _run(['cupsdisable', name])
    if rc1 != 0:
        return web.json_response({'status': 'error', 'msg': err1}, status=500)
    return web.json_response({'status': 'ok', 'msg': f'Printer "{name}" disabled.'})


@routes.post(config.URL_PREFIX + 'api/admin/printers/{name}/test-page')
async def api_admin_print_test_page(request):
    _require_admin(request)
    name = request.match_info['name']
    rc, out, err = _run(['lp', '-d', name, '/usr/share/cups/data/default-testpage.pdf'])
    if rc != 0:
        return web.json_response({'status': 'error', 'msg': err or out or 'Failed to print test page.'}, status=500)
    return web.json_response({'status': 'ok', 'msg': f'Test page sent to "{name}".'})


@routes.put(config.URL_PREFIX + 'api/admin/printers/{name}')
async def api_admin_modify_printer(request):
    _require_admin(request)
    name = request.match_info['name']
    post = await request.json()
    cmd = ['lpadmin', '-p', name]
    if 'uri' in post and post['uri']:
        cmd.extend(['-v', post['uri']])
    if 'description' in post:
        cmd.extend(['-D', post['description']])
    if 'location' in post:
        cmd.extend(['-L', post['location']])
    if 'ppd' in post and post['ppd']:
        ppd = post['ppd']
        if os.path.isfile(ppd):
            cmd.extend(['-P', ppd])
        else:
            cmd.extend(['-m', ppd])
    if len(cmd) <= 3:
        return web.json_response({'status': 'error', 'msg': 'No changes provided.'}, status=400)
    rc, out, err = _run(cmd)
    if rc != 0:
        return web.json_response({'status': 'error', 'msg': err or out}, status=500)
    return web.json_response({'status': 'ok', 'msg': f'Printer "{name}" updated.'})


@routes.post(config.URL_PREFIX + 'api/admin/printers')
async def api_admin_create_printer(request):
    _require_admin(request)
    post = await request.json()
    name = (post.get('name') or '').strip()
    description = post.get('description', name)
    location = post.get('location', '')
    # Backend: explicit URI (socket://..., ipp://..., usb://..., lpd://..., cups-pdf:/).
    backend = post.get('backend') or post.get('uri') or 'cups-pdf:/'
    ppd = post.get('ppd', '/usr/share/ppd/cups-pdf/CUPS-PDF_opt.ppd')
    if not name or not name.replace('_', '').replace('-', '').isalnum():
        return web.json_response({'status': 'error',
                                  'msg': 'Printer name must be alphanumeric (and -, _).'}, status=400)
    cmd = ['lpadmin', '-p', name, '-E', '-D', description, '-L', location, '-v', backend]
    if ppd and os.path.isfile(ppd):
        cmd.extend(['-P', ppd])
    elif ppd:
        cmd.extend(['-m', ppd])
    rc, out, err = _run(cmd)
    if rc != 0:
        return web.json_response({'status': 'error', 'msg': err or out, 'cmd': shlex.join(cmd)}, status=500)
    # Always make it shared
    _run(['cupsaccept', name])
    _run(['cupsenable', name])
    _run(['lpadmin', '-p', name, '-o', 'printer-is-shared=true'])
    # Add explicit Samba share for this printer
    _sync_smb_share(name, 'add', comment=description)
    return web.json_response({'status': 'ok', 'msg': f'Printer "{name}" created.'})


@routes.delete(config.URL_PREFIX + 'api/admin/printers/{name}')
async def api_admin_delete_printer(request):
    _require_admin(request)
    name = request.match_info['name']
    rc, out, err = _run(['lpadmin', '-x', name])
    if rc != 0:
        return web.json_response({'status': 'error', 'msg': err or out}, status=500)
    # Remove the Samba share for this printer
    _sync_smb_share(name, 'remove')
    return web.json_response({'status': 'ok', 'msg': f'Printer "{name}" deleted.'})


# ---------------------------------------------------------------------------
# Users / Admin endpoints (mostly identical to videodl)
# ---------------------------------------------------------------------------
@routes.get(config.URL_PREFIX + 'api/admin/users')
async def api_admin_list_users(request):
    _require_admin_or_useradmin(request)
    users = user_manager.list_users()
    for u in users:
        u['totp_enabled'] = user_manager.is_2fa_enabled(u['username'])
        info = user_manager.get_user_email(u['username'])
        u['email'] = info.get('email', '')
        u['email_status'] = info.get('email_status', 'none')
    return web.json_response({'status': 'ok', 'users': users})


@routes.post(config.URL_PREFIX + 'api/admin/users')
async def api_admin_create_user(request):
    caller = _require_admin_or_useradmin(request)
    post = await request.json()
    username = post.get('username', '').strip()
    password = post.get('password', '')
    group = post.get('group', 'users-group')
    if group not in ('admin-group', 'useradmin-group', 'users-group'):
        return web.json_response({'status': 'error', 'msg': 'Invalid group.'}, status=400)
    if caller.get('group') == 'useradmin-group' and group == 'admin-group':
        return web.json_response({'status': 'error', 'msg': 'Insufficient permissions.'}, status=403)
    ok, msg = user_manager.create_user(username, password, group)
    return web.json_response({'status': 'ok' if ok else 'error', 'msg': msg}, status=200 if ok else 400)


@routes.put(config.URL_PREFIX + 'api/admin/users/{username}')
async def api_admin_modify_user(request):
    caller = _require_admin_or_useradmin(request)
    username = request.match_info['username']
    target = user_manager.get_user(username)
    if not target:
        return web.json_response({'status': 'error', 'msg': 'User not found.'}, status=404)
    allowed, reason = _can_manage_target(caller, username, target.get('group', 'users-group'))
    if not allowed:
        return web.json_response({'status': 'error', 'msg': reason}, status=403)
    post = await request.json()
    if 'group' in post and caller.get('group') == 'useradmin-group' and post['group'] == 'admin-group':
        return web.json_response({'status': 'error', 'msg': 'Insufficient permissions.'}, status=403)
    ok, msg = user_manager.modify_user(username, post)
    if 'enabled' in post and not post['enabled']:
        user_manager.destroy_all_sessions(username)
        await _disconnect_user_sockets(username)
    return web.json_response({'status': 'ok' if ok else 'error', 'msg': msg}, status=200 if ok else 400)


@routes.delete(config.URL_PREFIX + 'api/admin/users/{username}')
async def api_admin_delete_user(request):
    caller = _require_admin_or_useradmin(request)
    username = request.match_info['username']
    target = user_manager.get_user(username)
    if not target:
        return web.json_response({'status': 'error', 'msg': 'User not found.'}, status=404)
    allowed, reason = _can_manage_target(caller, username, target.get('group', 'users-group'))
    if not allowed:
        return web.json_response({'status': 'error', 'msg': reason}, status=403)
    await _disconnect_user_sockets(username)
    ok, msg = user_manager.delete_user(username)
    return web.json_response({'status': 'ok' if ok else 'error', 'msg': msg}, status=200 if ok else 400)


@routes.post(config.URL_PREFIX + 'api/admin/users/{username}/reset-password')
async def api_admin_reset_password(request):
    caller = _require_admin_or_useradmin(request)
    username = request.match_info['username']
    target = user_manager.get_user(username)
    if not target:
        return web.json_response({'status': 'error', 'msg': 'User not found.'}, status=404)
    allowed, reason = _can_manage_target(caller, username, target.get('group', 'users-group'))
    if not allowed:
        return web.json_response({'status': 'error', 'msg': reason}, status=403)
    post = await request.json()
    new_password = post.get('password', '')
    if not new_password:
        return web.json_response({'status': 'error', 'msg': 'Password required.'}, status=400)
    ok, msg = user_manager.admin_reset_password(username, new_password)
    if not ok:
        return web.json_response({'status': 'error', 'msg': msg}, status=400)
    if user_manager.is_2fa_enabled(username):
        user_manager.disable_2fa(username)
    user_manager.destroy_all_sessions(username)
    await _disconnect_user_sockets(username)
    return web.json_response({'status': 'ok', 'msg': msg})


@routes.post(config.URL_PREFIX + 'api/admin/users/{username}/disable-2fa')
async def api_admin_disable_2fa(request):
    caller = _require_admin_or_useradmin(request)
    username = request.match_info['username']
    target = user_manager.get_user(username)
    if not target:
        return web.json_response({'status': 'error', 'msg': 'User not found.'}, status=404)
    allowed, reason = _can_manage_target(caller, username, target.get('group', 'users-group'))
    if not allowed:
        return web.json_response({'status': 'error', 'msg': reason}, status=403)
    if not user_manager.is_2fa_enabled(username):
        return web.json_response({'status': 'error', 'msg': '2FA is not enabled.'}, status=400)
    ok, msg = user_manager.disable_2fa(username)
    if not ok:
        return web.json_response({'status': 'error', 'msg': msg}, status=500)
    user_manager.destroy_all_sessions(username)
    await _disconnect_user_sockets(username)
    return web.json_response({'status': 'ok', 'msg': f'2FA disabled for "{username}".'})


# SMTP admin
@routes.get(config.URL_PREFIX + 'api/admin/smtp')
async def api_admin_get_smtp(request):
    _require_admin(request)
    cfg = user_manager.load_smtp_config()
    safe = {k: v for k, v in cfg.items() if k != 'password'}
    safe['has_password'] = bool(cfg.get('password'))
    return web.json_response({'status': 'ok', 'smtp': safe})


@routes.post(config.URL_PREFIX + 'api/admin/smtp')
async def api_admin_set_smtp(request):
    _require_admin(request)
    post = await request.json()
    existing = user_manager.load_smtp_config()
    password = post.get('password', '') or existing.get('password', '')
    cfg = {
        'host': post.get('host', '').strip(),
        'port': int(post.get('port', 587)),
        'username': post.get('username', '').strip(),
        'password': password,
        'security': post.get('security', 'starttls'),
        'sender_name': post.get('sender_name', 'Printers').strip() or 'Printers',
        'from_address': post.get('from_address', post.get('username', '')).strip(),
        'status': existing.get('status', 'unverified'),
    }
    user_manager.save_smtp_config(cfg)
    return web.json_response({'status': 'ok', 'msg': 'SMTP configuration saved.'})


@routes.post(config.URL_PREFIX + 'api/admin/smtp/test')
async def api_admin_test_smtp(request):
    _require_admin(request)
    cfg = user_manager.load_smtp_config()
    if not cfg.get('host'):
        return web.json_response({'status': 'error', 'msg': 'SMTP is not configured.'}, status=400)
    ok, msg = user_manager.test_smtp_connection(cfg)
    cfg['status'] = 'verified' if ok else 'failed'
    user_manager.save_smtp_config(cfg)
    return web.json_response({'status': 'ok' if ok else 'error', 'msg': msg, 'smtp_status': cfg['status']})


@routes.post(config.URL_PREFIX + 'api/admin/smtp/detect')
async def api_admin_detect_smtp(request):
    _require_admin(request)
    post = await request.json()
    email = post.get('email', '').strip()
    if not email or '@' not in email:
        return web.json_response({'status': 'error', 'msg': 'A valid email address is required.'}, status=400)
    found, cfg = user_manager.autodetect_smtp(email)
    if found:
        return web.json_response({'status': 'ok', 'detected': True, **cfg})
    return web.json_response({'status': 'ok', 'detected': False})


# ---------------------------------------------------------------------------
# Socket.IO
# ---------------------------------------------------------------------------
@sio.event
async def connect(sid, environ):
    from urllib.parse import parse_qs
    qs = parse_qs(environ.get('QUERY_STRING', ''))
    token = (qs.get('token') or [None])[0]
    username = user_manager.get_session_user(token)
    if not username:
        raise socketio.exceptions.ConnectionRefusedError('Not authenticated')
    user = user_manager.get_user(username)
    if not user or not user.get('enabled', True):
        raise socketio.exceptions.ConnectionRefusedError('User disabled')
    _sid_user[sid] = username
    await sio.enter_room(sid, username)
    if user.get('group') == 'admin-group':
        await sio.enter_room(sid, '__admins__')
    log.info(f'Socket connected: {sid} (user={username})')
    # Send initial state — list of recent print jobs for this user
    recent = capture.recent.get(username, [])
    await sio.emit('all', serializer.encode([j.__dict__ for j in recent]), to=sid)


@sio.event
async def disconnect(sid):
    username = _sid_user.pop(sid, None)
    log.info(f'Socket disconnected: {sid} (user={username})')


async def _disconnect_user_sockets(username: str):
    sids = [s for s, u in list(_sid_user.items()) if u == username]
    for s in sids:
        await sio.disconnect(s)
        _sid_user.pop(s, None)


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------
async def on_startup(app):
    capture.start()


async def on_cleanup(app):
    await capture.stop()


app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)


# ---------------------------------------------------------------------------
# CORS + static
# ---------------------------------------------------------------------------
async def on_prepare(request, response):
    if 'Origin' in request.headers:
        response.headers['Access-Control-Allow-Origin'] = request.headers['Origin']
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'


app.on_response_prepare.append(on_prepare)

if config.URL_PREFIX != '/':
    @routes.get('/')
    def redirect_root(request):
        return web.HTTPFound(config.URL_PREFIX)

# Static UI
routes.static(config.URL_PREFIX, UI_ROOT)

try:
    app.add_routes(routes)
except ValueError as exc:
    if 'ui/dist/printers' in str(exc):
        raise RuntimeError(
            'UI assets not found. Run `pnpm run build` inside the ui folder first.'
        ) from exc
    raise


def _supports_reuse_port():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.close()
        return True
    except (AttributeError, OSError):
        return False


def _access_log():
    return access_logger if config.ENABLE_ACCESSLOG else None


if __name__ == '__main__':
    # Sync existing CUPS printers to Samba smb.conf on startup
    _sync_all_smb_shares()
    log.info(f'Printers v{CONTAINER_VERSION} listening on {config.HOST}:{config.PORT}')
    if config.HTTPS:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(certfile=config.CERTFILE, keyfile=config.KEYFILE)
        web.run_app(app, host=config.HOST, port=int(config.PORT),
                    reuse_port=_supports_reuse_port(),
                    ssl_context=ssl_ctx, access_log=_access_log())
    else:
        web.run_app(app, host=config.HOST, port=int(config.PORT),
                    reuse_port=_supports_reuse_port(),
                    access_log=_access_log())
