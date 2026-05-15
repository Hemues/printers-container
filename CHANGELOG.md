# Changelog

All notable changes to the printers container are documented here.

## [Unreleased]

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
