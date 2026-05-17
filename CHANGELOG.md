# Changelog

All notable changes to the printers container are documented here.

## [Unreleased]

## [0.1.14] - 2026-05-17

### Fixed
- All close buttons replaced with visible FA icon buttons (matching videodl
  approach) — resolves invisible `btn-close` in dark mode across admin panel,
  change-password, two-factor-setup, log-viewer, and all sub-modals.
- Fixed broken `<i class="fa-solid ...">` tags (CSS font approach) in global
  log clear/archive buttons — replaced with proper `<fa-icon>` components
  that use the SVG rendering pipeline.
- Added global CSS safety rules ensuring `fa-icon` SVG elements always render
  with correct dimensions and `fill: currentColor`.

## [0.1.13] - 2026-05-17

### Fixed
- Change Password button was unclickable in zoneless mode because
  `[disabled]` guard depended on `ngModel` bindings that did not
  trigger change detection reliably.  Button is now always clickable;
  `submit()` validates all fields and shows clear per-field error
  messages ("Please enter your current password", etc.).  Added `name`
  attributes and `(ngModelChange)` handlers to each input for reliable
  two-way binding.

## [0.1.12] - 2026-05-17

### Added
- Version display in the login page footer (fetches `GET /api/version` and
  shows "container 0.1.12" at the bottom of the login card, matching the
  main-app footer style).

## [0.1.11] - 2026-05-17

### Fixed
- Version endpoint: added `GET /api/version` alias (frontend fetches
  `api/version` but only `/version` existed).
- `lpstat -p` parser: disabled printers were incorrectly shown as enabled
  because the word "disabled" appears in `parts[2]`, not `parts[3]`; now
  checks the full line and extracts a clean state keyword (`idle` /
  `disabled` / `processing`).
- Footer: removed redundant duplicate "app" + "container" rows; now shows
  a single "container" version line, matching the videodl footer style.

## [0.1.10] - 2026-05-23

### Added
- **Printer Server Settings — full management UI**: the existing "CUPS Printers"
  modal has been rewritten into a real CUPS-style admin surface.  The table now
  has columns *Name / State / URI / Reachable / Actions* with per-row buttons to
  enable/disable, modify, ping, or remove a queue.  An enabled queue shows a
  green badge; disabled is grey; queues that are not accepting jobs get an
  extra warning badge.
- **Friendly Add-Printer wizard** (3 steps): connection type → device →
  details.  Supported connection types: Virtual (`cups-pdf:/`), USB (auto-probed
  via `lpinfo -v`), JetDirect/AppSocket (`socket://host:9100`), IPP, IPPS, LPD,
  and a manual URI for advanced backends.  The wizard composes the final URI
  for you, lets you pick a PPD/driver from the live `lpinfo -m` list, and only
  enables *Create* once the inputs are valid.
- **Modify Printer modal**: change URI, description, and location of an
  existing queue without removing/re-adding it.
- **Per-printer reachability test**: a magnifying-glass icon next to each
  printer triggers `GET /api/admin/printers/{name}/ping`, which probes the
  TCP endpoint behind `socket://`, `ipp://`, `ipps://`, `lpd://`, or
  `http(s)://` URIs (USB / cups-pdf are reported as locally reachable).
- **Backend endpoints**:
  - `GET    /api/admin/printers` now returns a structured list
    `[{name, status, enabled, accepting, uri}]` (was: raw `lpstat` text).
  - `GET    /api/admin/printers/devices` — `lpinfo -v` wrapper.
  - `GET    /api/admin/printers/drivers` — `lpinfo -m` wrapper.
  - `GET    /api/admin/printers/{name}/ping` — TCP reachability probe.
  - `POST   /api/admin/printers/{name}/enable` — `cupsenable` + `cupsaccept`.
  - `POST   /api/admin/printers/{name}/disable` — `cupsdisable`.
  - `PUT    /api/admin/printers/{name}` — modify URI/description/location/PPD.
- **Per-print colour detection**: `printer_engine.py` now records a
  `color_mode` ∈ {`color`, `mono`, `unknown`} for every captured job by
  inspecting embedded image colorspaces via `pdfimages -list` and falling
  back to `gs -sDEVICE=inkcov` ink coverage on page 1.  The mode is appended
  to the per-user print log via a new `color_mode` field.
- **Landing-page log columns**: the `<app-log-viewer>` table now shows two
  extra columns — *Printer* (CUPS queue name) and *Color* (badge: B&W /
  colour / —).  Both are filterable like the existing columns.
- **Admin back-arrow icon**: restored to a real `<fa-icon>` of `faArrowLeft`
  (the `&larr;` workaround from 0.1.9 is no longer needed — FA7 names render
  correctly when the FA package set is consistent with videodl, which it is).

### Fixed
- Several FA7 icons (`faPenToSquare`, `faCircleCheck`, `faCircleXmark`,
  `faSearch`, `faNetworkWired`, `faUsb`, `faServer`, `faGlobe`) added to the
  admin panel imports so the new icons render reliably.

## [0.1.9] - 2026-05-22

### Fixed
- **Add User bug**: after a successful user creation the group select was
  silently reset to `'guest-group'` (an invalid group), causing every
  subsequent creation attempt to fail with "Invalid group." from the backend.
  Fixed by resetting to `'users-group'` instead.
- **Back-arrow icon**: the `faArrowLeft` icon from `@fortawesome/free-solid-svg-icons`
  did not render via `@fortawesome/angular-fontawesome ~4.0.0` (FA6-era
  wrapper + FA7 icon). Replaced with a plain `&larr;` HTML entity so the
  button is always visible and correctly labelled.
- **FA7 icon renames**: updated all icon references that were renamed between
  FA5 and FA6/7 — `faTrashAlt→faTrashCan`, `faCog→faGear`,
  `faShieldAlt→faShieldHalved`, `faSave→faFloppyDisk` — preventing empty
  icon buttons throughout the admin panel.

### Changed
- **Removed "Cookies" column** from the User Management table (header, filter
  row, data cell, state variable, and filter logic all removed).  The column
  was not used by the printers app and just added visual clutter.
- **SMTP email configuration** now opens as an inline modal inside the admin
  panel (previously it emitted an unhandled `smtpOpen` output event).  The
  modal is identical in form and function to the videodl equivalent and uses
  the existing `api/admin/smtp`, `api/admin/smtp/test`, and
  `api/admin/smtp/detect` backend endpoints.  Supports auto-detect, manual
  host/port/security, sender name, and from-address fields.



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
