# Changelog

All notable changes to the printers container are documented here.

## [Unreleased]

## [0.1.6] - 2026-05-15

### Changed
- Landing page: shows only the **monthly** page/job count by default; the full
  print log is opened on demand via "Show Print Log".
- Print Log (log viewer): now renders **inline** on the page (no full-screen
  overlay); columns are **Filename / Date / Status** with per-column search
  filters; action buttons reduced to **Export Selected** + close.
- Navbar: theme picker is now a **single click-to-cycle** button (no dropdown).
  User is shown as plain text with individual icon buttons for Change Password,
  2FA toggle, and Logout — no dropdown.
- Brand "Printers" text is now always **white** on the dark navbar, fixing the
  black-on-dark-grey contrast issue.
- Footer added: always shows **app** and **container** version for easy
  troubleshooting.

## [0.1.5] - 2026-05-15

### Fixed
- Print Log close button (X) now shows its icon: replaced deprecated `faTimes`
  with `faXmark` (Font Awesome 6 rename).
- Dashboard now starts with the stats view; the Print Log is hidden by default
  and opened on demand via the "Show print log" button (reversed the initial
  value of `showLogViewer`).

## [0.1.4] - 2026-05-15

### Fixed
- Change-password form now shows the success message before logging the user
  out.  Previously, `_clearSession()` was called inside the RxJS `tap`
  operator of `changePassword()`, which ran before the component's `next`
  handler.  Angular's reactivity immediately removed the modal (because
  `auth.isLoggedIn()` became false), so the user saw no feedback and could not
  tell whether the change had succeeded.  Fixed by removing the eager
  `_clearSession()` call; session cleanup now happens via `checkSession()`,
  which receives a 401 for the now-invalid token and calls `_clearSession()`
  in its `catchError` handler.

## [0.1.3] - 2026-05-15

### Fixed
- `smbpasswd -a` now succeeds on bootstrap: `set_smb_password()` creates a
  locked system Unix account (`useradd --system --no-create-home`) before
  calling `smbpasswd -a`, which requires the Unix user to exist in the tdbsam
  backend.  The retry loop is removed (pre-init via `pdbedit -L` in the
  entrypoint handles the TDB version-0.0 case separately).

## [0.1.2] - 2026-05-15

### Fixed
- Samba `passdb.tdb` version-0.0 error on first container start: entrypoint now
  pre-initialises the TDB with `pdbedit -L` before starting daemons, and
  `set_smb_password()` retries once after a 0.5 s delay so bootstrap users
  (`admin`, `test`) are always created cleanly with no WARNING in the logs

## [0.1.1] - 2026-05-14

### Changed
- Removed CUPS IPP port (631) from host port bindings — all clients (Windows, macOS, Linux) now connect via the Samba share; IPP remains running internally for the virtual PDF backend
- Updated documentation with macOS and Linux SMB client setup instructions
- Updated Dockerfile `EXPOSE` to drop port 631

## [0.1.0] - 2026-05-14

### Added
- Initial release
- CUPS print spooler with `cups-pdf` virtual backend (PDF shadow copies of every job)
- Samba share exposing CUPS queues to Windows/macOS/Linux clients
- Python aiohttp backend with REST API and Socket.IO
- Angular admin UI with 2FA (TOTP), user management, print stats, and log viewer
- Per-user PDF download from the web UI
- Admin CUPS printer CRUD, global print log, SMTP configuration
- `build.sh` with auto version increment and GitHub release creation
- Deploy script `podman-printers-updater-inside-pod`
