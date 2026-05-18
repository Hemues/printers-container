"""
Multi-user management for the Printers container.

Manages users stored in /configs/database/global/users.json with:
  - username, password_hash, homedir, group, enabled, must_change_password
  - Per-user config overlay at /configs/database/<username>/printers.conf
  - Per-user session file   at /configs/database/<username>/sessions.json
  - Per-user printings dir  at /printings/<username>/

Passwords are mirrored into a Samba smbpasswd database via set_smb_password()
so Windows / Linux print clients can authenticate to the same shared queues
with the same credentials. 2FA is enforced only on the web UI, not on the
print path.

Sessions are persisted to disk so they survive container restarts.
"""

import hashlib
import json
import logging
import os
import secrets
import smtplib
import socket
import ssl
import string
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import pyotp
import qrcode
import qrcode.constants
import io
import base64

log = logging.getLogger('user_manager')

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CONFIG_DIR = os.environ.get('CONFIG_DIR', '/configs')
PRINTINGS_DIR = os.environ.get('PRINTINGS_DIR', '/printings')
LOG_DIR_ENV = os.environ.get('LOG_DIR', '/logs')
SAMBA_DIR = os.path.join(CONFIG_DIR, 'samba')
SMBPASSWD_FILE = os.environ.get('SMBPASSWD_FILE', os.path.join(SAMBA_DIR, 'smbpasswd'))
DATABASE_DIR = os.path.join(CONFIG_DIR, 'database')
GLOBAL_DIR = os.path.join(DATABASE_DIR, 'global')
USERS_FILE = os.path.join(GLOBAL_DIR, 'users.json')
SMTP_CONFIG_FILE = os.path.join(GLOBAL_DIR, 'smtp.json')
NOT_FIRST_RUN_FILE = os.path.join(CONFIG_DIR, 'notfirstrun')

# ---------------------------------------------------------------------------
# Password hashing (SHA-256 + salt â€” no external dependency)
# ---------------------------------------------------------------------------


def _hash_password(password: str, salt: str | None = None) -> str:
    """Return 'salt$hash' using SHA-256.  Generate a random salt if none given."""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f'{salt}${h}'


def _verify_password(password: str, stored: str) -> bool:
    """Verify *password* against a 'salt$hash' string."""
    if '$' not in stored:
        return False
    salt = stored.split('$', 1)[0]
    return _hash_password(password, salt) == stored


# ---------------------------------------------------------------------------
# Session store  (in-memory; sessions survive server restarts only if we
# persist them â€” for a container this is fine because a restart also drops
# browser connections)
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}          # token -> {username, created}
SESSION_TTL = 86400 * 7                  # 7 days


def _get_user_sessions_path(username: str) -> str:
    """Return path to per-user sessions file: /config/database/<username>/sessions.json"""
    return os.path.join(DATABASE_DIR, username, 'sessions.json')


def _load_user_sessions(username: str) -> dict:
    """Load sessions for a single user from their sessions.json file."""
    path = _get_user_sessions_path(username)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        now = time.time()
        return {
            token: sess for token, sess in data.items()
            if isinstance(sess, dict)
            and 'username' in sess
            and 'created' in sess
            and now - sess['created'] <= SESSION_TTL
        }
    except Exception as exc:
        log.warning(f'Failed to read sessions for {username}: {exc}')
        return {}


def _save_user_sessions(username: str):
    """Persist sessions for a single user to their sessions.json file."""
    # Collect only this user's sessions from the in-memory store
    user_sessions = {t: s for t, s in _sessions.items() if s.get('username') == username}
    path = _get_user_sessions_path(username)
    user_dir = os.path.dirname(path)
    try:
        os.makedirs(user_dir, exist_ok=True)
        if user_sessions:
            with open(path, 'w') as f:
                json.dump(user_sessions, f, indent=2)
        elif os.path.isfile(path):
            os.remove(path)
    except Exception as exc:
        log.warning(f'Failed to write sessions for {username}: {exc}')


def _remove_user_sessions_file(username: str):
    """Delete the per-user sessions file (e.g. when user is deleted)."""
    path = _get_user_sessions_path(username)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except Exception as exc:
            log.warning(f'Failed to remove sessions file for {username}: {exc}')


def _load_all_sessions():
    """Load sessions for all users from their per-user session files."""
    global _sessions
    _sessions = {}
    if not os.path.isdir(DATABASE_DIR):
        return
    total = 0
    for entry in os.listdir(DATABASE_DIR):
        user_dir = os.path.join(DATABASE_DIR, entry)
        if not os.path.isdir(user_dir) or entry == 'global':
            continue
        user_sessions = _load_user_sessions(entry)
        _sessions.update(user_sessions)
        total += len(user_sessions)
    if total:
        log.info(f'Loaded {total} session(s) from disk')


def create_session(username: str) -> str:
    """Create a new session token for *username*."""
    token = secrets.token_urlsafe(48)
    _sessions[token] = {'username': username, 'created': time.time()}
    _save_user_sessions(username)
    return token


def get_session_user(token: str | None) -> str | None:
    """Return the username for *token*, or None if invalid / expired."""
    if not token:
        return None
    sess = _sessions.get(token)
    if sess is None:
        return None
    if time.time() - sess['created'] > SESSION_TTL:
        username = sess['username']
        _sessions.pop(token, None)
        _save_user_sessions(username)
        return None
    return sess['username']


def destroy_session(token: str | None):
    """Remove a session."""
    if token:
        sess = _sessions.pop(token, None)
        if sess:
            _save_user_sessions(sess['username'])


def destroy_all_sessions(username: str) -> int:
    """Remove every session belonging to *username*.  Returns the count removed."""
    tokens = [t for t, s in _sessions.items() if s['username'] == username]
    for t in tokens:
        del _sessions[t]
    if tokens:
        _remove_user_sessions_file(username)
    return len(tokens)


# ---------------------------------------------------------------------------
# User database
# ---------------------------------------------------------------------------

def _ensure_dirs():
    """Create /config/database/global/ if missing."""
    os.makedirs(GLOBAL_DIR, exist_ok=True)


def _load_users() -> list[dict]:
    """Load user list from disk."""
    if not os.path.isfile(USERS_FILE):
        return []
    try:
        with open(USERS_FILE, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning(f'Failed to read {USERS_FILE}: {exc}')
        return []


def _save_users(users: list[dict]):
    """Persist user list to disk."""
    _ensure_dirs()
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f, indent=2)
    except Exception as exc:
        log.warning(f'Failed to write {USERS_FILE}: {exc}')


def _find_user(users: list[dict], username: str) -> dict | None:
    for u in users:
        if u['username'] == username:
            return u
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_first_run() -> bool:
    return not os.path.isfile(NOT_FIRST_RUN_FILE)


def mark_first_run_done():
    _ensure_dirs()
    os.makedirs(os.path.dirname(NOT_FIRST_RUN_FILE), exist_ok=True)
    with open(NOT_FIRST_RUN_FILE, 'w') as f:
        f.write('1')


def bootstrap_admin():
    """Create the default admin and a test user if no users exist.
    Called at startup.  Returns True if new users were created."""
    users = _load_users()
    if users:
        return False

    printings_dir = os.environ.get('PRINTINGS_DIR', '/printings')

    # admin user â€” admin-group, must change password on first login
    admin_home = os.path.join(printings_dir, 'admin')
    os.makedirs(admin_home, exist_ok=True)
    os.makedirs(os.path.join(DATABASE_DIR, 'admin'), exist_ok=True)
    users.append({
        'username': 'admin',
        'password_hash': _hash_password('admin'),
        'homedir': admin_home,
        'group': 'admin-group',
        'enabled': True,
        'must_change_password': True,
        'locked_settings': [],
    })

    # test user â€” users-group, fixed password for QA
    test_home = os.path.join(printings_dir, 'test')
    os.makedirs(test_home, exist_ok=True)
    os.makedirs(os.path.join(DATABASE_DIR, 'test'), exist_ok=True)
    users.append({
        'username': 'test',
        'password_hash': _hash_password('test123456'),
        'homedir': test_home,
        'group': 'users-group',
        'enabled': True,
        'must_change_password': False,
        'locked_settings': [],
    })

    _save_users(users)

    # Sync both into smbpasswd so the SMB clients can print straight away.
    try:
        set_smb_password('admin', 'admin')
        set_smb_password('test', 'test123456')
    except Exception as exc:
        log.warning(f'Failed to bootstrap Samba accounts: {exc}')

    log.info('Bootstrapped default users: admin/admin (must change), test/test123456')
    return True


def ensure_unix_accounts():
    """Ensure Unix accounts exist for all users in the database.

    On container recreation (podman rm + run) the image /etc/passwd is fresh
    but the persistent volume still holds users.json and Samba passdb.tdb.
    This re-creates the system accounts so Samba auth works immediately.
    """
    import subprocess as _sp
    users = _load_users()
    for u in users:
        username = u.get('username', '')
        if not username:
            continue
        try:
            _sp.run(['id', username], check=True, capture_output=True)
        except _sp.CalledProcessError:
            _sp.run(
                ['useradd', '--system', '--no-create-home',
                 '--shell', '/usr/sbin/nologin', username],
                capture_output=True,
            )
            log.debug(f'recreated Unix account for {username}')


def authenticate(username: str, password: str) -> tuple[dict | None, str | None]:
    """Return (user_dict, None) on success, or (None, error_message) on failure."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return None, 'Invalid username or password.'
    if not user.get('enabled', True):
        return None, f'User "{username}" is disabled.'
    if not _verify_password(password, user['password_hash']):
        return None, 'Invalid username or password.'
    return user, None


def change_password(username: str, current_password: str, new_password: str) -> tuple[bool, str]:
    """Change password.  Returns (success, message)."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'
    if not _verify_password(current_password, user['password_hash']):
        return False, 'Current password is incorrect.'
    if len(new_password) < 1:
        return False, 'New password must not be empty.'
    user['password_hash'] = _hash_password(new_password)
    user['must_change_password'] = False
    _save_users(users)
    try:
        set_smb_password(username, new_password)
    except Exception as exc:
        log.warning(f'Failed to update Samba password for {username}: {exc}')
    return True, 'Password changed.'


def list_users() -> list[dict]:
    """Return user list (without password hashes or TOTP secrets)."""
    users = _load_users()
    _exclude = {'password_hash', 'totp_secret', 'email_verification_token'}
    return [{k: v for k, v in u.items() if k not in _exclude} for u in users]


def get_user(username: str) -> dict | None:
    """Return a single user (without password hash or TOTP secret)."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return None
    _exclude = {'password_hash', 'totp_secret', 'email_verification_token'}
    return {k: v for k, v in user.items() if k not in _exclude}


def create_user(username: str, password: str, group: str = 'users-group') -> tuple[bool, str]:
    """Create a new user.  Returns (success, message)."""
    if not username or not username.strip():
        return False, 'Username must not be empty.'
    # Sanitise username to filesystem-safe characters
    safe = ''.join(c for c in username if c.isalnum() or c in '-_.')
    if safe != username:
        return False, 'Username may only contain alphanumeric chars, hyphens, underscores, and dots.'
    if len(safe) > 64:
        return False, 'Username must be 64 characters or fewer.'

    users = _load_users()
    if _find_user(users, username):
        return False, 'User already exists.'

    download_dir = os.environ.get('PRINTINGS_DIR', '/printings')
    homedir = os.path.join(download_dir, username)
    os.makedirs(homedir, exist_ok=True)
    os.makedirs(os.path.join(DATABASE_DIR, username), exist_ok=True)

    users.append({
        'username': username,
        'password_hash': _hash_password(password),
        'homedir': homedir,
        'group': group,
        'enabled': True,
        'must_change_password': False,
        'locked_settings': [],
    })
    _save_users(users)
    try:
        set_smb_password(username, password)
    except Exception as exc:
        log.warning(f'Failed to create Samba account for {username}: {exc}')
    log.info(f'Created user {username} ({group})')
    return True, 'User created.'


def modify_user(username: str, changes: dict) -> tuple[bool, str]:
    """Modify fields of a user.  Allowed keys: group, enabled, locked_settings,
    must_change_password, storage_quota.  Returns (success, message)."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'

    allowed = {'group', 'enabled', 'locked_settings', 'must_change_password', 'storage_quota'}
    for k, v in changes.items():
        if k in allowed:
            user[k] = v

    _save_users(users)
    return True, 'User modified.'


def admin_reset_password(username: str, new_password: str) -> tuple[bool, str]:
    """Admin-initiated password reset (no current password needed)."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'
    user['password_hash'] = _hash_password(new_password)
    user['must_change_password'] = True
    _save_users(users)
    try:
        set_smb_password(username, new_password)
    except Exception as exc:
        log.warning(f'Failed to reset Samba password for {username}: {exc}')
    return True, 'Password reset.'


def delete_user(username: str) -> tuple[bool, str]:
    """Delete a user.  Does NOT remove files.  Returns (success, message)."""
    if username == 'admin':
        return False, 'Cannot delete the admin user.'
    users = _load_users()
    before = len(users)
    users = [u for u in users if u['username'] != username]
    if len(users) == before:
        return False, 'User not found.'
    _save_users(users)
    # Clean up in-memory sessions and per-user sessions file
    destroy_all_sessions(username)
    try:
        remove_smb_user(username)
    except Exception as exc:
        log.warning(f'Failed to remove Samba account for {username}: {exc}')
    log.info(f'Deleted user {username}')
    return True, 'User deleted.'


# ---------------------------------------------------------------------------
# Per-user config overlay
# ---------------------------------------------------------------------------

def get_user_config_dir(username: str) -> str:
    return os.path.join(DATABASE_DIR, username)


def get_user_config_file(username: str) -> str:
    return os.path.join(get_user_config_dir(username), 'printers.conf')


def load_user_config(username: str) -> dict:
    """Load per-user config overlay (empty dict if missing)."""
    path = get_user_config_file(username)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_user_config(username: str, data: dict):
    """Save per-user config overlay."""
    os.makedirs(get_user_config_dir(username), exist_ok=True)
    path = get_user_config_file(username)
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        log.warning(f'Failed to write {path}: {exc}')


# ---------------------------------------------------------------------------
# Per-user cookie file
# ---------------------------------------------------------------------------

def get_user_cookies_path(username: str) -> str:
    return os.path.join(get_user_config_dir(username), 'cookies.txt')


def has_user_cookies(username: str) -> bool:
    return os.path.isfile(get_user_cookies_path(username))


def remove_user_cookies(username: str) -> bool:
    path = get_user_cookies_path(username)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# ---------------------------------------------------------------------------
# Two-Factor Authentication (TOTP)
# ---------------------------------------------------------------------------

# In-memory tracking for 2FA login penalties: username -> {penalty_until, attempts}
_2fa_penalties: dict[str, dict] = {}

# Pending 2FA tokens: temp_token -> {username, created, attempts}
_2fa_pending: dict[str, dict] = {}
_2FA_PENDING_TTL = 60  # seconds â€” temp token valid for 60s
_2FA_MAX_RETRIES = 3
_2FA_PENALTY_SECONDS = 60


def generate_totp_secret() -> str:
    """Generate a new TOTP secret key."""
    return pyotp.random_base32()


def get_totp_provisioning_uri(username: str, secret: str) -> str:
    """Return the otpauth:// URI for registering with an authenticator app."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name='Printers')


def generate_totp_qr_base64(username: str, secret: str) -> str:
    """Generate a QR code PNG as a base64-encoded data URI."""
    uri = get_totp_provisioning_uri(username, secret)
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=6, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return f'data:image/png;base64,{b64}'


def verify_totp(secret: str, code: str) -> bool:
    """Verify a TOTP code for the given secret.  Allows +-1 window."""
    if not secret or not code:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def enable_2fa(username: str, secret: str):
    """Enable 2FA for the user by storing the secret in users.json."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'
    user['totp_secret'] = secret
    user['totp_enabled'] = True
    _save_users(users)
    log.info(f'2FA enabled for {username}')
    return True, '2FA enabled.'


def disable_2fa(username: str):
    """Disable 2FA for the user."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'
    user.pop('totp_secret', None)
    user['totp_enabled'] = False
    _save_users(users)
    log.info(f'2FA disabled for {username}')
    return True, '2FA disabled.'


def is_2fa_enabled(username: str) -> bool:
    """Check if 2FA is enabled for the user."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False
    return user.get('totp_enabled', False)


def get_totp_secret(username: str) -> str | None:
    """Return the stored TOTP secret for the user (internal use only)."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return None
    return user.get('totp_secret')


# --- 2FA Pending Tokens ---

def create_2fa_pending(username: str) -> str:
    """Create a temporary token for 2FA verification step.  Returns the temp token."""
    _cleanup_2fa_pending()
    token = secrets.token_urlsafe(48)
    _2fa_pending[token] = {'username': username, 'created': time.time(), 'attempts': 0}
    return token


def get_2fa_pending(token: str) -> dict | None:
    """Return pending 2FA entry or None if expired/invalid."""
    _cleanup_2fa_pending()
    entry = _2fa_pending.get(token)
    if entry is None:
        return None
    if time.time() - entry['created'] > _2FA_PENDING_TTL:
        _2fa_pending.pop(token, None)
        return None
    return entry


def increment_2fa_attempts(token: str) -> int:
    """Increment attempt count for a pending 2FA token.  Returns new count."""
    entry = _2fa_pending.get(token)
    if entry is None:
        return 0
    entry['attempts'] += 1
    # Reset the timer on each attempt (give another 30s window)
    entry['created'] = time.time()
    return entry['attempts']


def remove_2fa_pending(token: str):
    """Remove a pending 2FA token (after success or max retries)."""
    _2fa_pending.pop(token, None)


def _cleanup_2fa_pending():
    """Remove expired pending tokens."""
    now = time.time()
    expired = [t for t, e in _2fa_pending.items() if now - e['created'] > _2FA_PENDING_TTL]
    for t in expired:
        del _2fa_pending[t]


# --- 2FA Penalty ---

def set_2fa_penalty(username: str):
    """Set a 1-minute penalty for the user after failed 2FA."""
    _2fa_penalties[username] = {
        'penalty_until': time.time() + _2FA_PENALTY_SECONDS,
    }
    log.warning(f'2FA penalty applied to {username} for {_2FA_PENALTY_SECONDS}s')


def get_2fa_penalty_remaining(username: str) -> int:
    """Return remaining penalty seconds for the user, or 0 if no penalty."""
    entry = _2fa_penalties.get(username)
    if entry is None:
        return 0
    remaining = entry['penalty_until'] - time.time()
    if remaining <= 0:
        _2fa_penalties.pop(username, None)
        return 0
    return int(remaining) + 1  # round up


# ---------------------------------------------------------------------------
# Storage quota helpers
# ---------------------------------------------------------------------------

def _parse_size(size_str) -> int:
    """Parse a human-readable size string (e.g. '10G', '500M') to bytes.
    Returns 0 if disabled or unparseable."""
    if not size_str or str(size_str) == '0':
        return 0
    s = str(size_str).strip().upper()
    multipliers = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4}
    if s[-1] in multipliers:
        try:
            return int(float(s[:-1]) * multipliers[s[-1]])
        except ValueError:
            return 0
    try:
        return int(s)
    except ValueError:
        return 0


def get_user_disk_usage(username: str) -> int:
    """Return total size in bytes of the user's download directory."""
    user = get_user(username)
    if not user or not user.get('homedir'):
        return 0
    homedir = user['homedir']
    if not os.path.isdir(homedir):
        return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(homedir):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def user_has_files(username: str) -> bool:
    """Return True if the user has any files in their download directory."""
    user = get_user(username)
    if not user or not user.get('homedir'):
        return False
    homedir = user['homedir']
    if not os.path.isdir(homedir):
        return False
    for _dirpath, _dirs, filenames in os.walk(homedir):
        if filenames:
            return True
    return False


def get_user_storage_quota(username: str) -> int:
    """Return the storage quota in bytes for *username* (0 = unlimited)."""
    user_cfg = load_user_config(username)
    return _parse_size(user_cfg.get('STORAGE_QUOTA', '0'))


# ---------------------------------------------------------------------------
# Home directory change & user rename
# ---------------------------------------------------------------------------

def change_user_homedir(username: str, new_homedir: str, move_data: bool = True) -> tuple[bool, str]:
    """Change a user's download directory to *new_homedir*.
    If move_data is True, moves existing files.  Otherwise only updates the DB record.
    Returns (success, message).  Caller must pause/resume downloads."""
    import shutil
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'
    old_homedir = user.get('homedir', '')
    if not old_homedir:
        return False, 'User has no home directory set.'
    new_homedir = os.path.normpath(new_homedir)
    if old_homedir == new_homedir:
        return False, 'New directory is the same as the current one.'
    if move_data:
        if os.path.exists(new_homedir):
            return False, f'Directory "{new_homedir}" already exists.'
        # Move data
        try:
            parent = os.path.dirname(new_homedir)
            os.makedirs(parent, exist_ok=True)
            if os.path.isdir(old_homedir):
                shutil.move(old_homedir, new_homedir)
            else:
                os.makedirs(new_homedir, exist_ok=True)
        except Exception as exc:
            return False, f'Failed to move directory: {exc}'
    else:
        # Just ensure the new directory exists
        os.makedirs(new_homedir, exist_ok=True)
    user['homedir'] = new_homedir
    _save_users(users)
    log.info(f'Changed homedir for {username}: {old_homedir} -> {new_homedir} (move_data={move_data})')
    return True, 'Home directory changed.'


def rename_user(old_username: str, new_username: str, move_data: bool = True) -> tuple[bool, str]:
    """Rename a user.  If move_data is True, moves config dir, home dir, and log.
    Otherwise only updates the DB record and sessions.
    Returns (success, message).  Caller must pause/resume downloads."""
    import shutil
    if not new_username or not new_username.strip():
        return False, 'New username must not be empty.'
    safe = ''.join(c for c in new_username if c.isalnum() or c in '-_.')
    if safe != new_username:
        return False, 'Username may only contain alphanumeric chars, hyphens, underscores, and dots.'
    if len(safe) > 64:
        return False, 'Username must be 64 characters or fewer.'
    if old_username == new_username:
        return False, 'New username is the same as the current one.'

    users = _load_users()
    user = _find_user(users, old_username)
    if user is None:
        return False, 'User not found.'
    if _find_user(users, new_username):
        return False, f'Username "{new_username}" already exists.'

    if move_data:
        # Move config directory
        old_config_dir = get_user_config_dir(old_username)
        new_config_dir = get_user_config_dir(new_username)
        if os.path.isdir(old_config_dir):
            try:
                shutil.move(old_config_dir, new_config_dir)
            except Exception as exc:
                return False, f'Failed to move config directory: {exc}'

        # Move home directory to new username path
        download_dir = os.environ.get('DOWNLOAD_DIR', '/downloads')
        old_homedir = user.get('homedir', os.path.join(download_dir, old_username))
        new_homedir = os.path.join(download_dir, new_username)
        if os.path.exists(new_homedir):
            return False, f'Home directory "{new_homedir}" already exists.'
        if os.path.isdir(old_homedir):
            try:
                shutil.move(old_homedir, new_homedir)
            except Exception as exc:
                # Roll back config move
                if os.path.isdir(new_config_dir):
                    try:
                        shutil.move(new_config_dir, old_config_dir)
                    except Exception:
                        pass
                return False, f'Failed to move home directory: {exc}'
        else:
            os.makedirs(new_homedir, exist_ok=True)

        # Move global log file
        log_dir = os.path.join(CONFIG_DIR, 'log')
        old_log = os.path.join(log_dir, f'{old_username}.txt')
        new_log = os.path.join(log_dir, f'{new_username}.txt')
        if os.path.isfile(old_log):
            try:
                os.makedirs(log_dir, exist_ok=True)
                shutil.move(old_log, new_log)
            except Exception:
                pass

        # Update user record with new homedir
        user['username'] = new_username
        user['homedir'] = new_homedir
    else:
        # No file move â€” just update username, keep homedir as-is
        user['username'] = new_username

    _save_users(users)

    # Migrate in-memory sessions to new username and persist
    migrated = 0
    for token, sess in list(_sessions.items()):
        if sess['username'] == old_username:
            sess['username'] = new_username
            migrated += 1
    if migrated:
        # When move_data=True, shutil.move already moved the sessions.json
        # file to the new config dir â€” but it still contains old username refs.
        # When move_data=False, the old sessions.json is orphaned.
        # In both cases, save to the new location and clean up the old one.
        _save_user_sessions(new_username)
        if not move_data:
            _remove_user_sessions_file(old_username)

    log.info(f'Renamed user {old_username} -> {new_username} (move_data={move_data})')
    return True, 'User renamed.'


# ---------------------------------------------------------------------------
# Download logging
# ---------------------------------------------------------------------------

_LOG_DIR = os.path.join(LOG_DIR_ENV, 'print-log')
_MAX_LOG_ENTRIES = 1000
_MAX_GLOBAL_LOG_ENTRIES = 5000


def _get_user_log_path(username: str) -> str:
    """Per-user log inside their config directory."""
    return os.path.join(get_user_config_dir(username), 'print.log')


def _get_global_log_path(username: str) -> str:
    """Global per-user log at /config/log/USERNAME.txt."""
    return os.path.join(_LOG_DIR, f'{username}.txt')


def _format_size(size: int) -> str:
    """Human-readable file size string."""
    if size and size > 0:
        if size >= 1024 ** 3:
            return f'{size / 1024 ** 3:.2f} GB'
        if size >= 1024 ** 2:
            return f'{size / 1024 ** 2:.2f} MB'
        if size >= 1024:
            return f'{size / 1024:.2f} KB'
        return f'{size} B'
    return 'unknown'


def _parse_log_line(raw: str) -> dict | None:
    """Parse a single log line (JSON or legacy dash-separated).
    Returns a dict with url, name, datetime, size, filename, username or None."""
    raw = raw.strip()
    if not raw:
        return None
    # Try JSON first
    if raw.startswith('{'):
        try:
            entry = json.loads(raw)
            if isinstance(entry, dict) and 'url' in entry:
                return entry
        except (json.JSONDecodeError, ValueError):
            pass
    # Legacy dash-separated format
    parts = raw.split(' - ')
    if len(parts) >= 5:
        return {
            'url': parts[0].strip(),
            'name': parts[1].strip(),
            'datetime': parts[2].strip(),
            'size': parts[3].strip(),
            'filename': parts[4].strip(),
        }
    if len(parts) >= 4:
        return {
            'url': parts[0].strip(),
            'name': parts[1].strip(),
            'datetime': parts[2].strip(),
            'size': parts[3].strip(),
            'filename': '',
        }
    return None


def append_download_log(username: str, url: str, name: str, size: int, filename: str = ''):
    """Append a download entry as JSON to both per-user and global logs.
    Trims per-user log to _MAX_LOG_ENTRIES."""
    import datetime as _dt
    entry = {
        'url': url,
        'name': name,
        'datetime': _dt.datetime.now().strftime('%Y:%m:%d %H:%M:%S'),
        'size': _format_size(size),
        'filename': filename,
        'username': username,
    }
    line = json.dumps(entry, ensure_ascii=False) + '\n'

    # Per-user log (capped at _MAX_LOG_ENTRIES)
    user_log = _get_user_log_path(username)
    os.makedirs(os.path.dirname(user_log), exist_ok=True)
    try:
        existing = []
        if os.path.isfile(user_log):
            with open(user_log, 'r', encoding='utf-8', errors='replace') as f:
                existing = f.readlines()
        existing.append(line)
        if len(existing) > _MAX_LOG_ENTRIES:
            existing = existing[-_MAX_LOG_ENTRIES:]
        with open(user_log, 'w', encoding='utf-8') as f:
            f.writelines(existing)
    except Exception as exc:
        log.warning(f'Failed to write user log for {username}: {exc}')

    # Global log (append only, not trimmed)
    global_log = _get_global_log_path(username)
    os.makedirs(os.path.dirname(global_log), exist_ok=True)
    try:
        with open(global_log, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception as exc:
        log.warning(f'Failed to write global log for {username}: {exc}')


def get_download_log(username: str, download_dir: str = '') -> list[dict]:
    """Return the per-user download log as a list of structured entries.
    Parses JSON and legacy dash-separated formats.
    Each entry includes file_exists when a filename is recorded."""
    user_log = _get_user_log_path(username)
    if not os.path.isfile(user_log):
        return []
    try:
        with open(user_log, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return []

    result: list[dict] = []
    for raw in lines:
        entry = _parse_log_line(raw)
        if entry is None:
            continue
        # Check file existence
        filename = entry.get('filename', '')
        if filename and download_dir:
            full_path = os.path.join(download_dir, filename)
            entry['file_exists'] = os.path.isfile(full_path)
        else:
            entry['file_exists'] = False
        result.append(entry)
    return result


def get_global_download_log(download_dir: str = '') -> list[dict]:
    """Return a merged global download log from all users' global log files.
    Each entry includes the 'username' field.  Sorted by datetime descending.
    Capped at _MAX_GLOBAL_LOG_ENTRIES most recent entries."""
    if not os.path.isdir(_LOG_DIR):
        return []
    all_entries: list[dict] = []
    for fname in os.listdir(_LOG_DIR):
        if not fname.endswith('.txt'):
            continue
        username = fname[:-4]  # strip .txt
        fpath = os.path.join(_LOG_DIR, fname)
        try:
            with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                for raw in f:
                    entry = _parse_log_line(raw)
                    if entry is None:
                        continue
                    entry.setdefault('username', username)
                    # Check file existence
                    filename = entry.get('filename', '')
                    if filename and download_dir:
                        full_path = os.path.join(download_dir, filename)
                        entry['file_exists'] = os.path.isfile(full_path)
                    else:
                        entry['file_exists'] = False
                    all_entries.append(entry)
        except Exception:
            continue
    # Sort by datetime descending (format: YYYY:MM:DD HH:MM:SS)
    all_entries.sort(key=lambda e: e.get('datetime', ''), reverse=True)
    return all_entries[:_MAX_GLOBAL_LOG_ENTRIES]


def clear_global_download_log(archive: bool = False) -> dict:
    """Clear all global log files.

    If *archive* is True, concatenate every log file, compress with
    ``xz -9e`` (maximum compression) and store the result in
    ``/config/database/archive-log/<timestamp>.log.xz`` before deleting
    the originals.

    Returns ``{'status': 'ok', 'archived': <path|None>, 'deleted': <int>}``.
    """
    import datetime as _dt
    import subprocess as _sp

    if not os.path.isdir(_LOG_DIR):
        return {'status': 'ok', 'archived': None, 'deleted': 0}

    log_files = [os.path.join(_LOG_DIR, f)
                 for f in sorted(os.listdir(_LOG_DIR))
                 if f.endswith('.txt') and os.path.isfile(os.path.join(_LOG_DIR, f))]

    if not log_files:
        return {'status': 'ok', 'archived': None, 'deleted': 0}

    archived_path = None
    if archive:
        archive_dir = os.path.join(DATABASE_DIR, 'archive-log')
        os.makedirs(archive_dir, exist_ok=True)
        ts = _dt.datetime.now().strftime('%Y%m%d_%H%M%S')
        archive_name = f'global-log-{ts}.log.xz'
        archived_path = os.path.join(archive_dir, archive_name)

        # Concatenate all log files and pipe through xz -9e
        try:
            with open(archived_path, 'wb') as out_f:
                cat = _sp.Popen(['cat'] + log_files, stdout=_sp.PIPE)
                xz = _sp.Popen(['xz', '-9e'], stdin=cat.stdout, stdout=out_f)
                cat.stdout.close()
                xz.communicate()
                if xz.returncode != 0:
                    log.error('xz compression failed with code %d', xz.returncode)
                    return {'status': 'error', 'msg': 'xz compression failed'}
            log.info('Archived global log to %s', archived_path)
        except Exception as exc:
            log.error('Failed to archive global log: %s', exc)
            return {'status': 'error', 'msg': str(exc)}

    # Delete all log files
    deleted = 0
    for fpath in log_files:
        try:
            os.remove(fpath)
            deleted += 1
        except Exception as exc:
            log.warning('Failed to delete %s: %s', fpath, exc)

    log.info('Cleared %d global log file(s)', deleted)
    return {'status': 'ok', 'archived': archived_path, 'deleted': deleted}


def clear_download_log(username: str) -> bool:
    """Clear the per-user download log.  Returns True if successful."""
    user_log = _get_user_log_path(username)
    try:
        with open(user_log, 'w', encoding='utf-8') as f:
            f.write('')
        return True
    except Exception:
        return False


def remove_download_log_entries(username: str, filenames: list[str]) -> int:
    """Remove log entries whose filename is in *filenames*.
    Returns the number of entries removed."""
    if not filenames:
        return 0
    user_log = _get_user_log_path(username)
    if not os.path.isfile(user_log):
        return 0
    to_remove = set(filenames)
    try:
        with open(user_log, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return 0
    kept: list[str] = []
    removed = 0
    for raw in lines:
        entry = _parse_log_line(raw)
        if entry and entry.get('filename', '') in to_remove:
            removed += 1
        else:
            kept.append(raw)
    if removed == 0:
        return 0
    try:
        with open(user_log, 'w', encoding='utf-8') as f:
            f.writelines(kept)
    except Exception as exc:
        log.warning(f'Failed to rewrite log for {username}: {exc}')
        return 0
    return removed


def recover_download_log(username: str, download_dir: str) -> int:
    """Recover log entries from the global log whose files still exist on disk.

    Reads the per-user global log (/config/log/USERNAME.txt), which is
    append-only and never cleared, and checks each entry for file existence.
    Entries with a filename field are checked directly against disk.
    Legacy entries (no filename) are matched by name against files in the
    user's homedir and upgraded to JSON format with the filename filled in.
    Deduplication uses URL + datetime as composite key.
    Recovered entries are written in JSON format to the per-user log.
    Returns the number of recovered entries.
    """
    global_log = _get_global_log_path(username)
    if not os.path.isfile(global_log):
        return 0

    try:
        with open(global_log, 'r', encoding='utf-8', errors='replace') as f:
            global_raw = f.readlines()
    except Exception:
        return 0

    # Parse every global line (JSON or legacy)
    global_entries: list[dict] = []
    for raw in global_raw:
        entry = _parse_log_line(raw)
        if entry:
            global_entries.append(entry)
    if not global_entries:
        return 0

    # Build dedup keys from existing per-user log (URL + datetime)
    user_log = _get_user_log_path(username)
    existing_keys: set[str] = set()
    if os.path.isfile(user_log):
        try:
            with open(user_log, 'r', encoding='utf-8', errors='replace') as f:
                for raw in f:
                    e = _parse_log_line(raw)
                    if e and e.get('url') and e.get('datetime'):
                        existing_keys.add(f"{e['url']}|{e['datetime']}")
        except Exception:
            pass

    # Resolve user homedir for legacy entry matching
    user = get_user(username)
    user_homedir = user.get('homedir', '') if user else ''

    # Index files in user's homedir: basename and basename-without-ext â†’ relpath
    homedir_files: dict[str, str] = {}
    if user_homedir and os.path.isdir(user_homedir) and download_dir:
        for dirpath, _dirs, filenames in os.walk(user_homedir):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, download_dir)
                homedir_files[fn] = rel
                base_no_ext = os.path.splitext(fn)[0]
                if base_no_ext not in homedir_files:
                    homedir_files[base_no_ext] = rel

    recovered: list[str] = []  # JSON lines to append
    for entry in global_entries:
        url = entry.get('url', '')
        name = entry.get('name', '')
        dt = entry.get('datetime', '')
        if not url or not dt:
            continue

        dedup_key = f'{url}|{dt}'
        if dedup_key in existing_keys:
            continue

        filename = entry.get('filename', '')
        if filename and download_dir:
            full_path = os.path.join(download_dir, filename)
            if os.path.isfile(full_path):
                entry.setdefault('username', username)
                recovered.append(json.dumps(entry, ensure_ascii=False))
                existing_keys.add(dedup_key)
                continue

        # No filename or file gone â€” try matching by name against homedir
        if name and not filename:
            matched_rel = (homedir_files.get(name)
                           or homedir_files.get(os.path.splitext(name)[0]))
            if matched_rel:
                entry['filename'] = matched_rel
                entry.setdefault('username', username)
                recovered.append(json.dumps(entry, ensure_ascii=False))
                existing_keys.add(dedup_key)

    if not recovered:
        return 0

    # Merge into per-user log
    os.makedirs(os.path.dirname(user_log), exist_ok=True)
    try:
        existing: list[str] = []
        if os.path.isfile(user_log):
            with open(user_log, 'r', encoding='utf-8', errors='replace') as f:
                existing = [l.rstrip('\n') for l in f.readlines() if l.strip()]
        merged = existing + recovered
        if len(merged) > _MAX_LOG_ENTRIES:
            merged = merged[-_MAX_LOG_ENTRIES:]
        with open(user_log, 'w', encoding='utf-8') as f:
            f.writelines(l + '\n' for l in merged)
    except Exception as exc:
        log.warning(f'Failed to write recovered log for {username}: {exc}')
        return 0

    log.info(f'Recovered {len(recovered)} log entries for {username}')
    return len(recovered)


# ---------------------------------------------------------------------------
# Email management
# ---------------------------------------------------------------------------

def set_user_email(username: str, email: str) -> tuple[bool, str]:
    """Set or update a user's email.  Resets status to 'pending' and generates
    a new verification token.  Returns (success, token)."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'
    token = secrets.token_urlsafe(48)
    user['email'] = email
    user['email_status'] = 'pending'
    user['email_verification_token'] = token
    _save_users(users)
    log.info(f'[{username}] Email set to {email} (pending verification)')
    return True, token


def delete_user_email(username: str) -> tuple[bool, str]:
    """Remove a user's email address entirely."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'
    user.pop('email', None)
    user.pop('email_status', None)
    user.pop('email_verification_token', None)
    _save_users(users)
    log.info(f'[{username}] Email removed')
    return True, 'Email removed.'


def verify_user_email(token: str) -> tuple[bool, str]:
    """Verify a user's email using the verification token.
    Returns (success, message)."""
    users = _load_users()
    for user in users:
        if user.get('email_verification_token') == token:
            user['email_status'] = 'verified'
            user.pop('email_verification_token', None)
            _save_users(users)
            log.info(f'[{user["username"]}] Email verified: {user.get("email")}')
            return True, f'Email verified for {user["username"]}.'
    return False, 'Invalid or expired verification link.'


def get_user_email(username: str) -> dict:
    """Return {email, email_status} for a user, or empty values."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return {'email': '', 'email_status': 'none'}
    return {
        'email': user.get('email', ''),
        'email_status': user.get('email_status', 'none'),
    }


def admin_set_user_email_verified(username: str, email: str) -> tuple[bool, str]:
    """Admin: set a user's email and mark it verified immediately (no token)."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.'
    user['email'] = email
    user['email_status'] = 'verified'
    user.pop('email_verification_token', None)
    _save_users(users)
    log.info(f'[{username}] Admin set email to {email} (verified)')
    return True, 'Email set and verified.'


# ---------------------------------------------------------------------------
# SMTP configuration
# ---------------------------------------------------------------------------

def load_smtp_config() -> dict:
    """Load SMTP configuration from disk.  Returns empty dict if not set."""
    if not os.path.isfile(SMTP_CONFIG_FILE):
        return {}
    try:
        with open(SMTP_CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as exc:
        log.warning(f'Failed to read {SMTP_CONFIG_FILE}: {exc}')
        return {}


def save_smtp_config(cfg: dict):
    """Persist SMTP configuration to disk."""
    _ensure_dirs()
    try:
        with open(SMTP_CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
        log.info('SMTP configuration saved')
    except Exception as exc:
        log.warning(f'Failed to write {SMTP_CONFIG_FILE}: {exc}')


def test_smtp_connection(cfg: dict) -> tuple[bool, str]:
    """Test SMTP connection by authenticating and sending a NOOP.
    Returns (success, message)."""
    host = cfg.get('host', '')
    port = int(cfg.get('port', 587))
    username = cfg.get('username', '')
    password = cfg.get('password', '')
    security = cfg.get('security', 'starttls')  # starttls | ssl

    if not host or not username or not password:
        return False, 'Host, username, and password are required.'

    try:
        if security == 'ssl':
            ctx = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            ctx = ssl.create_default_context()
            server.starttls(context=ctx)
            server.ehlo()
        server.login(username, password)
        server.noop()
        server.quit()
        return True, 'SMTP connection successful.'
    except smtplib.SMTPAuthenticationError as exc:
        return False, f'Authentication failed: {exc.smtp_error.decode("utf-8", errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)}'
    except Exception as exc:
        return False, f'Connection failed: {exc}'


# Well-known SMTP configurations keyed by email domain.
_KNOWN_SMTP: dict[str, dict] = {
    'gmail.com':       {'host': 'smtp.gmail.com',       'port': 587, 'security': 'starttls'},
    'googlemail.com':  {'host': 'smtp.gmail.com',       'port': 587, 'security': 'starttls'},
    'outlook.com':     {'host': 'smtp-mail.outlook.com','port': 587, 'security': 'starttls'},
    'hotmail.com':     {'host': 'smtp-mail.outlook.com','port': 587, 'security': 'starttls'},
    'live.com':        {'host': 'smtp-mail.outlook.com','port': 587, 'security': 'starttls'},
    'yahoo.com':       {'host': 'smtp.mail.yahoo.com',  'port': 587, 'security': 'starttls'},
    'yahoo.co.uk':     {'host': 'smtp.mail.yahoo.com',  'port': 587, 'security': 'starttls'},
    'icloud.com':      {'host': 'smtp.mail.me.com',     'port': 587, 'security': 'starttls'},
    'me.com':          {'host': 'smtp.mail.me.com',     'port': 587, 'security': 'starttls'},
    'mac.com':         {'host': 'smtp.mail.me.com',     'port': 587, 'security': 'starttls'},
    'aol.com':         {'host': 'smtp.aol.com',         'port': 587, 'security': 'starttls'},
    'zoho.com':        {'host': 'smtp.zoho.com',        'port': 587, 'security': 'starttls'},
    'protonmail.com':  {'host': 'smtp.protonmail.ch',   'port': 587, 'security': 'starttls'},
    'proton.me':       {'host': 'smtp.protonmail.ch',   'port': 587, 'security': 'starttls'},
    'gmx.com':         {'host': 'mail.gmx.com',         'port': 587, 'security': 'starttls'},
    'gmx.net':         {'host': 'mail.gmx.net',         'port': 587, 'security': 'starttls'},
    'mail.com':        {'host': 'smtp.mail.com',        'port': 587, 'security': 'starttls'},
    'yandex.com':      {'host': 'smtp.yandex.com',      'port': 465, 'security': 'ssl'},
    'yandex.ru':       {'host': 'smtp.yandex.ru',       'port': 465, 'security': 'ssl'},
    'fastmail.com':    {'host': 'smtp.fastmail.com',    'port': 465, 'security': 'ssl'},
    'tutanota.com':    {'host': 'smtp.tutanota.com',    'port': 587, 'security': 'starttls'},
    'mailbox.org':     {'host': 'smtp.mailbox.org',     'port': 465, 'security': 'ssl'},
    'posteo.de':       {'host': 'posteo.de',            'port': 465, 'security': 'ssl'},
}


def _probe_smtp_port(host: str, port: int, use_ssl: bool, timeout: float = 5) -> bool:
    """Try to connect to an SMTP server on the given port.  Returns True on success."""
    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) as s:
                s.noop()
            return True
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.ehlo()
                ctx = ssl.create_default_context()
                s.starttls(context=ctx)
                s.ehlo()
            return True
    except Exception:
        return False


def autodetect_smtp(email: str) -> tuple[bool, dict]:
    """Try to auto-detect SMTP settings for the given email address.

    Returns ``(found, config_dict)`` where *config_dict* has keys
    ``host``, ``port``, ``security`` (only when *found* is True).
    """
    if not email or '@' not in email:
        return False, {}

    domain = email.rsplit('@', 1)[1].strip().lower()

    # 1. Check well-known providers
    if domain in _KNOWN_SMTP:
        return True, dict(_KNOWN_SMTP[domain])

    # 2. Try common smtp.domain patterns with typical ports
    candidates = [
        (f'smtp.{domain}', 587, 'starttls', False),
        (f'smtp.{domain}', 465, 'ssl', True),
        (f'mail.{domain}', 587, 'starttls', False),
        (f'mail.{domain}', 465, 'ssl', True),
    ]

    for host, port, security, use_ssl in candidates:
        # DNS check first to skip unresolvable hosts quickly
        try:
            socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            continue
        if _probe_smtp_port(host, port, use_ssl, timeout=5):
            return True, {'host': host, 'port': port, 'security': security}

    return False, {}


def send_verification_email(to_email: str, username: str, verification_url: str) -> tuple[bool, str]:
    """Send a verification email.  Returns (success, message)."""
    cfg = load_smtp_config()
    if not cfg.get('host') or not cfg.get('username') or not cfg.get('password'):
        return False, 'SMTP is not configured.'
    if cfg.get('status') != 'verified':
        return False, 'SMTP configuration is not verified.'

    host = cfg['host']
    port = int(cfg.get('port', 587))
    smtp_user = cfg['username']
    smtp_pass = cfg['password']
    security = cfg.get('security', 'starttls')
    sender_name = cfg.get('sender_name', 'Printers')
    from_addr = cfg.get('from_address', smtp_user)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Printers â€” Verify your email address'
    msg['From'] = f'{sender_name} <{from_addr}>'
    msg['To'] = to_email

    text = (
        f'Hello {username},\n\n'
        f'Please verify your email address by visiting this link:\n'
        f'{verification_url}\n\n'
        f'If you did not request this, you can ignore this email.\n\n'
        f'â€” Printers'
    )
    html = (
        f'<p>Hello <b>{username}</b>,</p>'
        f'<p>Please verify your email address by clicking the link below:</p>'
        f'<p><a href="{verification_url}" style="display:inline-block;padding:10px 24px;'
        f'background:#0d6efd;color:#fff;text-decoration:none;border-radius:6px;">'
        f'Verify Email</a></p>'
        f'<p style="color:#888;font-size:0.85em;">Or copy and paste this URL: '
        f'{verification_url}</p>'
        f'<p>If you did not request this, ignore this email.</p>'
        f'<p>â€” Printers</p>'
    )
    msg.attach(MIMEText(text, 'plain'))
    msg.attach(MIMEText(html, 'html'))

    ok, result = _smtp_send(cfg, from_addr, to_email, msg)
    if ok:
        log.info(f'Verification email sent to {to_email} for user {username}')
    return ok, result if not ok else 'Verification email sent.'


def _smtp_send(cfg: dict, from_addr: str, to_email: str, msg) -> tuple[bool, str]:
    """Low-level SMTP send helper."""
    host = cfg['host']
    port = int(cfg.get('port', 587))
    smtp_user = cfg['username']
    smtp_pass = cfg['password']
    security = cfg.get('security', 'starttls')
    try:
        if security == 'ssl':
            ctx = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            ctx = ssl.create_default_context()
            server.starttls(context=ctx)
            server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.sendmail(from_addr, [to_email], msg.as_string())
        server.quit()
        return True, 'Email sent.'
    except Exception as exc:
        log.warning(f'Failed to send email to {to_email}: {exc}')
        return False, f'Failed to send email: {exc}'


# ---------------------------------------------------------------------------
# Password recovery (forgot password)
# ---------------------------------------------------------------------------

# In-memory: IP/identifier -> {attempts, penalty_until}
_recovery_attempts: dict[str, dict] = {}
_RECOVERY_MAX_ATTEMPTS = 5
_RECOVERY_PENALTY_SECONDS = 60

# Pending recovery tokens: token -> {username, created}
_recovery_tokens: dict[str, dict] = {}
_RECOVERY_TOKEN_TTL = 600  # 10 minutes


def is_smtp_active() -> bool:
    """Return True if SMTP is configured and verified."""
    cfg = load_smtp_config()
    return bool(cfg.get('host')) and cfg.get('status') == 'verified'


def get_recovery_penalty(identifier: str) -> int:
    """Return remaining penalty seconds, or 0."""
    entry = _recovery_attempts.get(identifier)
    if entry is None:
        return 0
    if entry.get('penalty_until', 0) > time.time():
        return int(entry['penalty_until'] - time.time()) + 1
    # Penalty expired â€” reset
    if entry.get('penalty_until', 0) > 0:
        _recovery_attempts.pop(identifier, None)
    return 0


def record_recovery_attempt(identifier: str) -> int:
    """Record a failed recovery attempt.  Returns updated count.
    If count reaches _RECOVERY_MAX_ATTEMPTS, applies a penalty."""
    entry = _recovery_attempts.setdefault(identifier, {'attempts': 0, 'penalty_until': 0})
    entry['attempts'] = entry.get('attempts', 0) + 1
    if entry['attempts'] >= _RECOVERY_MAX_ATTEMPTS:
        entry['penalty_until'] = time.time() + _RECOVERY_PENALTY_SECONDS
        entry['attempts'] = 0
        log.warning(f'Recovery penalty applied to {identifier} for {_RECOVERY_PENALTY_SECONDS}s')
    return entry['attempts']


def clear_recovery_attempts(identifier: str):
    """Clear attempts on success."""
    _recovery_attempts.pop(identifier, None)


def validate_recovery_request(username: str, email: str) -> tuple[bool, str]:
    """Validate that username exists, email matches, and email is verified.
    Returns generic error to avoid user enumeration."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'Wrong username or email address.'
    if user.get('email', '') != email:
        return False, 'Wrong username or email address.'
    if user.get('email_status') != 'verified':
        return False, 'Wrong username or email address.'
    if not user.get('enabled', True):
        return False, 'Wrong username or email address.'
    return True, ''


def create_recovery_token(username: str) -> str:
    """Create a password-recovery token.  Returns the token string."""
    _cleanup_recovery_tokens()
    token = secrets.token_urlsafe(48)
    _recovery_tokens[token] = {'username': username, 'created': time.time()}
    return token


def get_recovery_token(token: str) -> dict | None:
    """Return recovery entry or None if expired/invalid."""
    _cleanup_recovery_tokens()
    entry = _recovery_tokens.get(token)
    if entry is None:
        return None
    if time.time() - entry['created'] > _RECOVERY_TOKEN_TTL:
        _recovery_tokens.pop(token, None)
        return None
    return entry


def consume_recovery_token(token: str) -> str | None:
    """Use a recovery token â€” returns the username and removes the token.
    Returns None if invalid/expired."""
    entry = get_recovery_token(token)
    if entry is None:
        return None
    _recovery_tokens.pop(token, None)
    return entry['username']


def _cleanup_recovery_tokens():
    now = time.time()
    expired = [t for t, e in _recovery_tokens.items() if now - e['created'] > _RECOVERY_TOKEN_TTL]
    for t in expired:
        del _recovery_tokens[t]


def generate_random_password(length: int = 16) -> str:
    """Generate a random password with lowercase, uppercase, digits, and special chars."""
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = '!@#$%&*'
    pw = [
        secrets.choice(lower),
        secrets.choice(upper),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    pool = lower + upper + digits + special
    pw += [secrets.choice(pool) for _ in range(length - 4)]
    result = list(pw)
    for i in range(len(result) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        result[i], result[j] = result[j], result[i]
    return ''.join(result)


def execute_password_recovery(username: str) -> tuple[bool, str, str]:
    """Reset user's password, disable 2FA, force password change.
    Returns (success, message, new_password)."""
    users = _load_users()
    user = _find_user(users, username)
    if user is None:
        return False, 'User not found.', ''
    new_password = generate_random_password()
    user['password_hash'] = _hash_password(new_password)
    user['must_change_password'] = True
    user.pop('totp_secret', None)
    user['totp_enabled'] = False
    _save_users(users)
    destroy_all_sessions(username)
    log.info(f'[{username}] Password recovery executed â€” 2FA disabled, password reset, must_change_password=True')
    return True, 'Password reset.', new_password


def send_recovery_link_email(to_email: str, username: str, recovery_url: str) -> tuple[bool, str]:
    """Send password recovery link email."""
    cfg = load_smtp_config()
    if not cfg.get('host') or not cfg.get('username') or not cfg.get('password'):
        return False, 'SMTP is not configured.'
    if cfg.get('status') != 'verified':
        return False, 'SMTP is not verified.'

    sender_name = cfg.get('sender_name', 'Printers')
    from_addr = cfg.get('from_address', cfg['username'])

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Printers â€” Password Recovery'
    msg['From'] = f'{sender_name} <{from_addr}>'
    msg['To'] = to_email

    text = (
        f'Hello {username},\n\n'
        f'A password recovery was requested for your account.\n'
        f'Click the link below to reset your password:\n\n'
        f'{recovery_url}\n\n'
        f'This link expires in 10 minutes.\n'
        f'If you did not request this, you can ignore this email.\n\n'
        f'â€” Printers'
    )
    html = (
        f'<p>Hello <b>{username}</b>,</p>'
        f'<p>A password recovery was requested for your account.</p>'
        f'<p><a href="{recovery_url}" style="display:inline-block;padding:10px 24px;'
        f'background:#ffc107;color:#212529;text-decoration:none;border-radius:6px;'
        f'font-weight:bold;">Reset Password</a></p>'
        f'<p style="color:#888;font-size:0.85em;">This link expires in 10 minutes. '
        f'Or copy and paste: {recovery_url}</p>'
        f'<p>If you did not request this, ignore this email.</p>'
        f'<p>â€” Printers</p>'
    )
    msg.attach(MIMEText(text, 'plain'))
    msg.attach(MIMEText(html, 'html'))

    return _smtp_send(cfg, from_addr, to_email, msg)


def send_new_password_email(to_email: str, username: str, new_password: str) -> tuple[bool, str]:
    """Send the new generated password to the user's email."""
    cfg = load_smtp_config()
    if not cfg.get('host') or not cfg.get('username') or not cfg.get('password'):
        return False, 'SMTP is not configured.'

    sender_name = cfg.get('sender_name', 'Printers')
    from_addr = cfg.get('from_address', cfg['username'])

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Printers â€” Your new password'
    msg['From'] = f'{sender_name} <{from_addr}>'
    msg['To'] = to_email

    text = (
        f'Hello {username},\n\n'
        f'Your password has been reset.\n'
        f'Your new temporary password is:\n\n'
        f'    {new_password}\n\n'
        f'You will be required to change this password on your next login.\n'
        f'If 2FA was enabled, it has been turned off.\n\n'
        f'â€” Printers'
    )
    html = (
        f'<p>Hello <b>{username}</b>,</p>'
        f'<p>Your password has been reset. Your new temporary password is:</p>'
        f'<p style="font-family:monospace;font-size:1.2em;padding:12px;'
        f'background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;'
        f'display:inline-block;letter-spacing:1px;">{new_password}</p>'
        f'<p>You will be required to <b>change this password</b> on your next login.</p>'
        f'<p style="color:#888;font-size:0.85em;">If 2FA was enabled, it has been turned off.</p>'
        f'<p>â€” Printers</p>'
    )
    msg.attach(MIMEText(text, 'plain'))
    msg.attach(MIMEText(html, 'html'))

    return _smtp_send(cfg, from_addr, to_email, msg)


# ---------------------------------------------------------------------------
# Samba password sync
# ---------------------------------------------------------------------------
def set_smb_password(username: str, password: str) -> bool:
    """Add or update *username* in the Samba passdb so SMB print clients
    can authenticate with the same password as the web UI.

    Uses `smbpasswd -a -s` (script mode reads passwd from stdin twice).
    Falls back silently when smbpasswd is missing (e.g. during local
    development on Windows)."""
    import shutil as _shutil
    import subprocess as _sp
    smbpasswd = _shutil.which('smbpasswd')
    if smbpasswd is None:
        log.debug('smbpasswd binary not found \u2014 skipping Samba sync')
        return False
    # smbpasswd -a requires the Unix user to exist (tdbsam maps to /etc/passwd).
    # Create a locked system account if it is absent.
    try:
        _sp.run(['id', username], check=True, capture_output=True)
    except _sp.CalledProcessError:
        _sp.run(
            ['useradd', '--system', '--no-create-home',
             '--shell', '/usr/sbin/nologin', username],
            capture_output=True,
        )
    # `-a` adds the user if missing; `-s` reads stdin (pw twice).
    payload = f'{password}\n{password}\n'.encode()
    try:
        proc = _sp.run(
            [smbpasswd, '-a', '-s', username],
            input=payload, capture_output=True, timeout=10,
        )
        if proc.returncode != 0:
            log.warning(f'smbpasswd add/update for {username} failed: '
                        f'{proc.stderr.decode(errors="replace")}')
            return False
        # Make sure account is enabled (a fresh add may be disabled).
        _sp.run([smbpasswd, '-e', username], capture_output=True, timeout=10)
        return True
    except _sp.TimeoutExpired:
        log.warning(f'smbpasswd timed out for {username}')
        return False
    except Exception as exc:
        log.warning(f'smbpasswd exception for {username}: {exc}')
        return False


def remove_smb_user(username: str) -> bool:
    """Remove *username* from the Samba passdb."""
    import shutil as _shutil
    import subprocess as _sp
    smbpasswd = _shutil.which('smbpasswd')
    if smbpasswd is None:
        return False
    try:
        _sp.run([smbpasswd, '-x', username], capture_output=True, timeout=10)
        return True
    except Exception as exc:
        log.warning(f'smbpasswd -x failed for {username}: {exc}')
        return False


# ---------------------------------------------------------------------------
# Print job log (thin wrapper over the existing JSON line append helper)
# ---------------------------------------------------------------------------
def append_print_log(username: str, title: str, filename: str,
                     pages: int = 0, size: int = 0,
                     printer: str = '', color_mode: str = 'unknown',
                     status: str = 'finished'):
    """Append a print job entry to per-user log + global log.

    Schema kept JSON-compatible with the Printers logs so the existing
    log viewer doesn't need a rewrite. `url` is repurposed to carry
    the CUPS printer name; `name` carries the original document
    title; `filename` is the on-disk shadow PDF path relative to
    PRINTINGS_DIR/<user>/.
    """
    import datetime as _dt
    entry = {
        'url': printer,
        'name': title,
        'datetime': _dt.datetime.now().strftime('%Y:%m:%d %H:%M:%S'),
        'size': _format_size(size),
        'filename': filename,
        'username': username,
        'pages': int(pages or 0),
        'printer': printer,
        'color_mode': color_mode,
        'status': status,
    }
    line = json.dumps(entry, ensure_ascii=False) + '\n'

    user_log = _get_user_log_path(username)
    os.makedirs(os.path.dirname(user_log), exist_ok=True)
    try:
        existing = []
        if os.path.isfile(user_log):
            with open(user_log, 'r', encoding='utf-8', errors='replace') as f:
                existing = f.readlines()
        existing.append(line)
        if len(existing) > _MAX_LOG_ENTRIES:
            existing = existing[-_MAX_LOG_ENTRIES:]
        with open(user_log, 'w', encoding='utf-8') as f:
            f.writelines(existing)
    except Exception as exc:
        log.warning(f'Failed to write print log for {username}: {exc}')

    global_log = _get_global_log_path(username)
    os.makedirs(os.path.dirname(global_log), exist_ok=True)
    try:
        with open(global_log, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception as exc:
        log.warning(f'Failed to write global print log for {username}: {exc}')


def _iter_user_entries(username: str):
    """Yield parsed log entries for *username* (per-user log)."""
    user_log = _get_user_log_path(username)
    if not os.path.isfile(user_log):
        return
    try:
        with open(user_log, 'r', encoding='utf-8', errors='replace') as f:
            for raw in f:
                e = _parse_log_line(raw)
                if e is not None:
                    yield e
    except Exception:
        return


def get_print_stats(username: str | None = None) -> dict:
    """Return daily / monthly / yearly / overall page count + job counts.

    When *username* is None, aggregate across every user (admin dashboard).
    """
    import datetime as _dt
    now = _dt.datetime.now()
    today = now.strftime('%Y:%m:%d')
    this_month = now.strftime('%Y:%m')
    this_year = now.strftime('%Y')

    stats = {
        'today':   {'pages': 0, 'jobs': 0},
        'month':   {'pages': 0, 'jobs': 0},
        'year':    {'pages': 0, 'jobs': 0},
        'overall': {'pages': 0, 'jobs': 0},
    }

    if username is None:
        # Aggregate every per-user global log
        if not os.path.isdir(_LOG_DIR):
            return stats
        for fname in os.listdir(_LOG_DIR):
            if not fname.endswith('.txt'):
                continue
            uname = fname[:-4]
            for e in _iter_user_entries(uname):
                _accumulate_stats(stats, e, today, this_month, this_year)
    else:
        for e in _iter_user_entries(username):
            _accumulate_stats(stats, e, today, this_month, this_year)
    return stats


def _accumulate_stats(stats: dict, entry: dict,
                       today: str, this_month: str, this_year: str):
    pages = int(entry.get('pages', 0) or 0)
    when = entry.get('datetime', '')
    stats['overall']['pages'] += pages
    stats['overall']['jobs'] += 1
    if when.startswith(this_year):
        stats['year']['pages'] += pages
        stats['year']['jobs'] += 1
        if when.startswith(this_month):
            stats['month']['pages'] += pages
            stats['month']['jobs'] += 1
            if when.startswith(today):
                stats['today']['pages'] += pages
                stats['today']['jobs'] += 1
