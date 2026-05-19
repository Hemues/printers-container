"""
Windows printer driver management for Samba print$ share.

Downloads, extracts, registers, and serves Windows printer drivers so that
Windows clients can auto-install them via Point and Print.

Driver files are stored on the persistent volume at /configs/samba/drivers/
which is symlinked to /var/lib/samba/printers (the print$ share path).
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

CONFIG_DIR = os.environ.get('CONFIG_DIR', '/configs')
DRIVERS_DIR = os.path.join(CONFIG_DIR, 'samba', 'drivers')
DRIVERS_CACHE = os.path.join(DRIVERS_DIR, 'cache')
DRIVERS_X64 = os.path.join(DRIVERS_DIR, 'x64')
DRIVERS_W32X86 = os.path.join(DRIVERS_DIR, 'W32X86')
DRIVERS_COLOR = os.path.join(DRIVERS_DIR, 'color')

# Samba print$ share path (symlinked to DRIVERS_DIR by entrypoint)
PRINT_SHARE_PATH = '/var/lib/samba/printers'

# Known driver download URLs — HP frequently reorganizes their CDN.
# The find_driver_url() function probes for working URLs at runtime.
# These are fallback/last-known-good entries:
HP_UPD_PCL6_URL = 'https://ftp.hp.com/pub/softpaq/sp151501-152000/sp151834.exe'  # Full UPD ~268MB
HP_UPD_PS_URL = ''  # Use find_driver_url(driver_type='ps') to locate

# Known softpaq IDs confirmed to contain HP UPD (for the finder to probe)
_HP_UPD_SOFTPAQS = [
    # (softpaq_id, folder_range_start, folder_range_end, approx_size_mb, description)
    (151834, 151501, 152000, 268, 'HP UPD full package (latest)'),
    (143682, 143501, 144000, 263, 'HP UPD full package (older)'),
]

# Driver name as registered in Samba (must match the INF DriverName exactly)
HP_UPD_PCL6_DRIVER_NAME = 'HP Universal Printing PCL 6'
HP_UPD_PS_DRIVER_NAME = 'HP Universal Printing PS'

# Model-to-driver/manufacturer mapping patterns
_HP_MODELS = re.compile(r'\bhp\b|\blaserjet\b|\bofficejet\b|\bdeskjet\b|\bcolor\s*laser|\bpagewide\b', re.IGNORECASE)
_CANON_MODELS = re.compile(r'\bcanon\b|\bimageCLASS\b|\bimageRUNNER\b|\bSATERA\b|\bLBP\b|\bMF\d', re.IGNORECASE)
_BROTHER_MODELS = re.compile(r'\bbrother\b|\bHL-\b|\bMFC-\b|\bDCP-\b', re.IGNORECASE)
_EPSON_MODELS = re.compile(r'\bepson\b|\bWorkForce\b|\bEcoTank\b|\bExpression\b', re.IGNORECASE)
_RICOH_MODELS = re.compile(r'\bricoh\b|\baficio\b|\bMP\s*\d|\bSP\s*\d|\bIM\s*\d', re.IGNORECASE)
_XEROX_MODELS = re.compile(r'\bxerox\b|\bphaser\b|\bversalink\b|\bworkcentre\b|\balta\s*link\b', re.IGNORECASE)
_KONICA_MODELS = re.compile(r'\bkonica\b|\bminolta\b|\bbizhub\b', re.IGNORECASE)
_KYOCERA_MODELS = re.compile(r'\bkyocera\b|\btaskalfa\b|\becosys\b', re.IGNORECASE)
_SAMSUNG_MODELS = re.compile(r'\bsamsung\b|\bxpress\b|\bSL-\b', re.IGNORECASE)
_LEXMARK_MODELS = re.compile(r'\blexmark\b|\bMS\d|\bMX\d|\bCX\d', re.IGNORECASE)

# Known universal/generic driver info for each manufacturer
_DRIVER_DB = {
    'HP': {
        'driver_name': HP_UPD_PCL6_DRIVER_NAME,
        'driver_type': 'PCL 6',
        'description': 'HP Universal Print Driver PCL 6 (x64) — works with all HP LaserJet/OfficeJet/MFP printers',
    },
    'Canon': {
        'driver_name': 'Canon Generic Plus UFR II',
        'driver_type': 'UFR II',
        'description': 'Canon Generic Plus UFR II Driver — works with most Canon LBP/imageRUNNER/MF series',
        'manual_page': 'https://www.canon.com/support/software-drivers',
    },
    'Brother': {
        'driver_name': 'Brother Universal Printer',
        'driver_type': 'PCL',
        'description': 'Brother Universal Printer Driver (PCL) — works with Brother HL/MFC/DCP series',
        'manual_page': 'https://support.brother.com/g/s/id/os/linux/packages/unipdrv_en.html',
    },
    'Epson': {
        'driver_name': 'EPSON Universal Print Driver',
        'driver_type': 'ESC/P-R',
        'description': 'Epson Universal Print Driver — works with Epson WorkForce/EcoTank series',
        'manual_page': 'https://download.ebz.epson.net/dsc/search/01/search/',
    },
    'Ricoh': {
        'driver_name': 'RICOH PCL6 UniversalDriver V4.0',
        'driver_type': 'PCL 6',
        'description': 'Ricoh Universal PCL6 Driver — works with Ricoh/Aficio/MP/SP/IM series',
        'manual_page': 'https://www.ricoh.com/support/download',
    },
    'Xerox': {
        'driver_name': 'Xerox Global Print Driver PCL6',
        'driver_type': 'PCL 6',
        'description': 'Xerox Global Print Driver PCL6 — works with Xerox Phaser/VersaLink/WorkCentre/AltaLink',
        'manual_page': 'https://www.support.xerox.com/en-us/product/global-printer-driver/downloads',
    },
    'Konica Minolta': {
        'driver_name': 'KONICA MINOLTA Universal PCL',
        'driver_type': 'PCL',
        'description': 'Konica Minolta Universal PCL Driver — works with bizhub series',
        'manual_page': 'https://www.konicaminolta.com/global-en/support/download/',
    },
    'Kyocera': {
        'driver_name': 'Kyocera Classic Universaldriver PCL6',
        'driver_type': 'PCL 6',
        'description': 'Kyocera Universal PCL6 Driver — works with TASKalfa/ECOSYS series',
        'manual_page': 'https://www.kyoceradocumentsolutions.com/download/',
    },
    'Samsung': {
        'driver_name': 'Samsung Universal Print Driver 3',
        'driver_type': 'PCL 6',
        'description': 'Samsung Universal Print Driver — works with Samsung/Xpress printers (now HP)',
        'manual_page': 'https://www.hp.com/support/samsungprinters',
    },
    'Lexmark': {
        'driver_name': 'Lexmark Universal v2 PCL XL Emul',
        'driver_type': 'PCL XL',
        'description': 'Lexmark Universal v2 Driver — works with Lexmark MS/MX/CX series',
        'manual_page': 'https://www.lexmark.com/en_us/solutions/print-drivers/universal-print-driver.html',
    },
    'Generic': {
        'driver_name': 'Generic / Text Only',
        'driver_type': 'Generic',
        'description': 'Windows built-in generic text driver — basic printing only, no advanced features',
        'manual_page': '',
    },
}


def ensure_dirs():
    """Create driver storage directories if they don't exist."""
    for d in (DRIVERS_DIR, DRIVERS_CACHE, DRIVERS_X64, DRIVERS_W32X86, DRIVERS_COLOR):
        os.makedirs(d, exist_ok=True)


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        return 127, '', str(exc)
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'


def _check_url_exists(url: str, timeout: int = 10) -> tuple[bool, int]:
    """HEAD-check a URL. Returns (exists, content_length)."""
    rc, out, _ = _run(['curl', '-sfIL', '--connect-timeout', '5',
                       '--max-time', str(timeout), url], timeout=timeout + 5)
    if rc != 0:
        return False, 0
    # Parse content-length from headers
    size = 0
    status_ok = False
    for line in out.splitlines():
        lower = line.lower()
        if lower.startswith('http/') and ' 200' in line:
            status_ok = True
        if lower.startswith('content-length:'):
            try:
                size = int(line.split(':', 1)[1].strip())
            except ValueError:
                pass
    return status_ok, size


def find_driver_url(manufacturer: str = 'HP', driver_type: str = 'pcl6') -> dict:
    """Probe HP's CDN to find a working driver download URL.

    Strategy (in order — stops at first hit):
      1. Check known softpaq IDs (fast: just 2-3 HEAD requests)
      2. Try legacy /printers/UPD/ paths with version enumeration
      3. Try COL40842/bi-xxx paths
      4. Scan nearby softpaq IDs around known-good ones

    Returns:
        dict with keys: url, size_mb, status, driver_name
    """
    if manufacturer.upper() != 'HP':
        return {'status': 'not_found', 'msg': f'Auto-find not supported for {manufacturer}'}

    drv_type = 'pcl6' if 'pcl' in driver_type.lower() else 'ps'
    log.info(f'Searching for HP UPD {drv_type} x64 download URL...')

    # ---- Strategy 1: Known softpaq IDs (fastest — 2-3 HEAD requests) ----
    for sp_id, range_start, range_end, expected_mb, desc in _HP_UPD_SOFTPAQS:
        url = f'https://ftp.hp.com/pub/softpaq/sp{range_start}-{range_end}/sp{sp_id}.exe'
        exists, size = _check_url_exists(url)
        if exists and size > 10_000_000:
            log.info(f'Found working URL (known softpaq): {url} ({size / 1048576:.1f} MB)')
            return {
                'status': 'found',
                'url': url,
                'size_mb': round(size / 1048576, 1),
                'driver_type': drv_type,
                'driver_name': HP_UPD_PCL6_DRIVER_NAME if drv_type == 'pcl6' else HP_UPD_PS_DRIVER_NAME,
                'note': desc,
            }

    # ---- Strategy 2: Legacy /printers/UPD/ paths ----
    versions = [
        '7.4.0.26070', '7.3.0.25919', '7.3.0.25900',
        '7.2.0.25780', '7.1.0.25500', '7.0.1.24923',
    ]
    for ver in versions:
        url = f'https://ftp.hp.com/pub/softlib/software13/printers/UPD/upd-{drv_type}-x64-{ver}.exe'
        exists, size = _check_url_exists(url)
        if exists and size > 1_000_000:
            log.info(f'Found working URL (legacy path): {url} ({size / 1048576:.1f} MB)')
            return {
                'status': 'found',
                'url': url,
                'version': ver,
                'size_mb': round(size / 1048576, 1),
                'driver_type': drv_type,
                'driver_name': HP_UPD_PCL6_DRIVER_NAME if drv_type == 'pcl6' else HP_UPD_PS_DRIVER_NAME,
            }

    # ---- Strategy 3: Scan softpaq IDs near known-good ones ----
    # Check IDs around the newest known softpaq (±50) in case HP released an update
    newest_sp = _HP_UPD_SOFTPAQS[0][0]
    range_start = _HP_UPD_SOFTPAQS[0][1]
    range_end = _HP_UPD_SOFTPAQS[0][2]
    for offset in [1, 2, 3, 5, 10, 20, 50, -1, -2, -5]:
        sp_id = newest_sp + offset
        # Determine correct folder range for this ID
        folder_start = (sp_id // 500) * 500 + 1
        folder_end = folder_start + 499
        url = f'https://ftp.hp.com/pub/softpaq/sp{folder_start}-{folder_end}/sp{sp_id}.exe'
        exists, size = _check_url_exists(url)
        # Accept files in UPD size range (200-300MB for full, 20-50MB for single-arch)
        if exists and size > 20_000_000:
            log.info(f'Found working URL (nearby scan): {url} ({size / 1048576:.1f} MB)')
            return {
                'status': 'found',
                'url': url,
                'size_mb': round(size / 1048576, 1),
                'driver_type': drv_type,
                'driver_name': HP_UPD_PCL6_DRIVER_NAME if drv_type == 'pcl6' else HP_UPD_PS_DRIVER_NAME,
                'note': f'Found near known softpaq sp{newest_sp}',
            }

    log.warning('Could not find a working HP UPD download URL')
    return {
        'status': 'not_found',
        'msg': 'Could not find a working download URL. Download manually and provide the URL.',
        'manual_page': 'https://support.hp.com/us-en/drivers/hp-universal-print-driver-for-windows/503548',
    }


def download_file(url: str, dest_path: str, timeout: int = 300) -> bool:
    """Download a file from URL to dest_path using curl."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    rc, out, err = _run(['curl', '-fSL', '--connect-timeout', '30',
                         '--max-time', str(timeout), '-o', dest_path, url],
                        timeout=timeout + 10)
    if rc != 0:
        log.error(f'Download failed: {url} → {err}')
        if os.path.exists(dest_path):
            os.unlink(dest_path)
        return False
    log.info(f'Downloaded {url} → {dest_path} ({os.path.getsize(dest_path)} bytes)')
    return True


def extract_driver_archive(archive_path: str, dest_dir: str) -> bool:
    """Extract a driver archive (exe/cab/zip) using 7z."""
    os.makedirs(dest_dir, exist_ok=True)
    rc, out, err = _run(['7z', 'x', '-y', f'-o{dest_dir}', archive_path], timeout=120)
    if rc != 0:
        log.error(f'Extraction failed: {archive_path} → {err}')
        return False
    log.info(f'Extracted {archive_path} → {dest_dir}')
    return True


def _find_driver_files(extract_dir: str, arch: str = 'x64') -> dict:
    """Find the key driver files in an extracted HP UPD directory.

    Returns dict with keys: inf, dll, gpd, cat, data_file, config_file, help_file
    and a 'files' list of all driver-related files to copy.
    """
    result = {'inf': '', 'files': [], 'driver_name': ''}

    # Walk the extracted tree looking for the INF and associated files
    # HP UPD typically has structure like: .../x64/hpgenPCL6drv_x64.inf or similar
    all_files = []
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            all_files.append(os.path.join(root, f))

    # Find INF files (prefer x64 path)
    inf_files = [f for f in all_files if f.lower().endswith('.inf')]
    # Prefer INF files in paths containing 'x64' or 'amd64'
    arch_infs = [f for f in inf_files if 'x64' in f.lower() or 'amd64' in f.lower()]
    if not arch_infs:
        arch_infs = inf_files

    # Find the main printer driver INF (contains "HP Universal" or "DriverName")
    target_inf = ''
    driver_name = ''
    for inf_path in arch_infs:
        try:
            with open(inf_path, 'r', errors='replace') as fh:
                content = fh.read()
                # Look for the driver name in the INF
                for line in content.splitlines():
                    if '=' in line and 'HP Universal Printing PCL 6' in line:
                        driver_name = 'HP Universal Printing PCL 6'
                        target_inf = inf_path
                        break
                    elif '=' in line and 'HP Universal Printing PS' in line:
                        driver_name = 'HP Universal Printing PS'
                        target_inf = inf_path
                        break
                if target_inf:
                    break
        except Exception:
            continue

    if not target_inf and arch_infs:
        # Fallback: use the first INF and try to parse driver name
        target_inf = arch_infs[0]
        try:
            with open(target_inf, 'r', errors='replace') as fh:
                for line in fh:
                    # [Manufacturer] section or similar
                    m = re.search(r'"([^"]+)"', line)
                    if m and ('HP' in m.group(1) or 'Print' in m.group(1)):
                        driver_name = m.group(1)
                        break
        except Exception:
            pass

    if not target_inf:
        log.error(f'No INF file found in {extract_dir}')
        return result

    result['inf'] = target_inf
    result['driver_name'] = driver_name

    # Collect all files from the same directory as the INF
    inf_dir = os.path.dirname(target_inf)
    driver_files = []
    for f in os.listdir(inf_dir):
        full = os.path.join(inf_dir, f)
        if os.path.isfile(full):
            driver_files.append(full)

    # Also look for files referenced in the INF but in parent/sibling dirs
    # (HP UPD sometimes has shared files in a common directory)
    try:
        with open(target_inf, 'r', errors='replace') as fh:
            content = fh.read()
            # Find CopyFiles references and source disk paths
            for line in content.splitlines():
                line = line.strip()
                if ',' in line and not line.startswith(';') and not line.startswith('['):
                    # Could be a file reference
                    parts = line.split(',')
                    for part in parts:
                        fname = part.strip().strip('"')
                        if '.' in fname and len(fname) < 80:
                            # Look for this file in the extract tree
                            matches = [f for f in all_files if os.path.basename(f).lower() == fname.lower()]
                            for match in matches:
                                if match not in driver_files:
                                    driver_files.append(match)
    except Exception:
        pass

    result['files'] = driver_files
    return result


def copy_driver_files(file_list: list[str], arch: str = 'x64') -> list[str]:
    """Copy driver files to the print$ share directory. Returns list of filenames."""
    dest = DRIVERS_X64 if arch == 'x64' else DRIVERS_W32X86
    os.makedirs(dest, exist_ok=True)
    copied = []
    for src in file_list:
        fname = os.path.basename(src)
        dst = os.path.join(dest, fname)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o644)
        copied.append(fname)
    log.info(f'Copied {len(copied)} driver files to {dest}')
    return copied


def register_driver_samba(driver_name: str, inf_file: str, arch: str = 'x64') -> tuple[bool, str]:
    """Register a printer driver with Samba using rpcclient.

    Parses the INF to find the required file names and calls rpcclient adddriver.
    """
    # For rpcclient, we need:
    # adddriver "Windows x64" "DriverName:driver_dll:data_file:config_file:help_file:NULL:RAW:dependent_file1,dependent_file2,..."

    # Parse the INF to find key files
    driver_dll = ''
    data_file = ''
    config_file = ''
    help_file = 'NULL'
    dependent_files = []

    inf_dir = os.path.dirname(inf_file)
    all_inf_files = [f for f in os.listdir(inf_dir) if os.path.isfile(os.path.join(inf_dir, f))]

    # Common patterns for HP UPD
    for f in all_inf_files:
        fl = f.lower()
        if fl.endswith('.dll') and 'drv' in fl:
            if not driver_dll:
                driver_dll = f
                config_file = f  # config_file is usually the same as driver DLL
        elif fl.endswith('.gpd') or fl.endswith('.ppd'):
            if not data_file:
                data_file = f
        elif fl.endswith('.hlp') or fl.endswith('.chm'):
            help_file = f

    # If we didn't find specific files, try parsing the INF
    if not driver_dll:
        try:
            with open(inf_file, 'r', errors='replace') as fh:
                for line in fh:
                    if 'DriverFile' in line and '=' in line:
                        val = line.split('=', 1)[1].strip().strip('"').strip(',')
                        if val:
                            driver_dll = val if '.' in val else val + '.dll'
                    elif 'DataFile' in line and '=' in line:
                        val = line.split('=', 1)[1].strip().strip('"').strip(',')
                        if val:
                            data_file = val
                    elif 'ConfigFile' in line and '=' in line:
                        val = line.split('=', 1)[1].strip().strip('"').strip(',')
                        if val:
                            config_file = val if '.' in val else val + '.dll'
                    elif 'HelpFile' in line and '=' in line:
                        val = line.split('=', 1)[1].strip().strip('"').strip(',')
                        if val:
                            help_file = val
        except Exception:
            pass

    if not driver_dll:
        return False, 'Could not determine driver DLL from INF'
    if not data_file:
        data_file = driver_dll  # Fallback
    if not config_file:
        config_file = driver_dll

    # Build dependent files list (all files except the main ones)
    main_files = {driver_dll.lower(), data_file.lower(), config_file.lower(), help_file.lower()}
    for f in all_inf_files:
        if f.lower() not in main_files and not f.lower().endswith('.inf'):
            dependent_files.append(f)

    dep_str = ','.join(dependent_files) if dependent_files else 'NULL'
    arch_str = 'Windows x64' if arch == 'x64' else 'Windows NT x86'

    # rpcclient command to add driver
    # Format: "DriverName:driver.dll:data.ppd:config.dll:help.hlp:NULL:RAW:dep1,dep2"
    driver_spec = f'{driver_name}:{driver_dll}:{data_file}:{config_file}:{help_file}:NULL:RAW:{dep_str}'

    rc, out, err = _run([
        'rpcclient', 'localhost', '-U', '%', '-N',
        '-c', f'adddriver "{arch_str}" "{driver_spec}"'
    ])

    if rc != 0:
        # Try with guest/anonymous auth variations
        rc, out, err = _run([
            'rpcclient', 'localhost', '-U', 'root%',
            '-c', f'adddriver "{arch_str}" "{driver_spec}"'
        ])

    if rc != 0:
        log.error(f'rpcclient adddriver failed: {err}')
        return False, f'rpcclient adddriver failed: {err}'

    log.info(f'Registered driver "{driver_name}" for {arch_str}')
    return True, 'ok'


def set_printer_driver(printer_name: str, driver_name: str) -> tuple[bool, str]:
    """Associate a registered driver with a Samba printer share."""
    rc, out, err = _run([
        'rpcclient', 'localhost', '-U', '%', '-N',
        '-c', f'setdriver "{printer_name}" "{driver_name}"'
    ])
    if rc != 0:
        rc, out, err = _run([
            'rpcclient', 'localhost', '-U', 'root%',
            '-c', f'setdriver "{printer_name}" "{driver_name}"'
        ])
    if rc != 0:
        log.error(f'rpcclient setdriver failed: {err}')
        return False, f'rpcclient setdriver failed: {err}'
    log.info(f'Set driver "{driver_name}" for printer "{printer_name}"')
    return True, 'ok'


def detect_manufacturer(model: str) -> str:
    """Detect the printer manufacturer from model string."""
    checks = [
        (_HP_MODELS, 'HP'), (_CANON_MODELS, 'Canon'), (_BROTHER_MODELS, 'Brother'),
        (_EPSON_MODELS, 'Epson'), (_RICOH_MODELS, 'Ricoh'), (_XEROX_MODELS, 'Xerox'),
        (_KONICA_MODELS, 'Konica Minolta'), (_KYOCERA_MODELS, 'Kyocera'),
        (_SAMSUNG_MODELS, 'Samsung'), (_LEXMARK_MODELS, 'Lexmark'),
    ]
    for pattern, mfr in checks:
        if pattern.search(model):
            return mfr
    return 'Unknown'


def get_supported_manufacturers() -> list[dict]:
    """Return list of all supported manufacturers and their driver info."""
    return [{'manufacturer': k, **v} for k, v in _DRIVER_DB.items()]


def suggest_driver_for_model(model: str) -> dict:
    """Suggest a Windows driver package based on detected printer model."""
    mfr = detect_manufacturer(model)
    if mfr != 'Unknown' and mfr in _DRIVER_DB:
        info = _DRIVER_DB[mfr].copy()
        info['manufacturer'] = mfr
        if mfr == 'HP':
            info['url'] = HP_UPD_PCL6_URL
            info['find_supported'] = True
        else:
            info['url'] = ''
            info['find_supported'] = False
        info['find_url_hint'] = 'Use GET /api/admin/printers/drivers/find to locate a working download URL'
        return info

    return {
        'manufacturer': 'Unknown',
        'driver_name': '',
        'url': '',
        'find_supported': False,
        'description': 'No automatic driver available. Upload a driver package or use "Generic / Text Only".',
    }


def is_driver_installed(driver_name: str) -> bool:
    """Check if a driver is already installed in print$/x64/."""
    # Check by looking for an INF file and the marker file we create
    marker = os.path.join(DRIVERS_DIR, '.installed_drivers')
    if os.path.isfile(marker):
        try:
            with open(marker, 'r') as f:
                installed = [line.strip() for line in f]
                return driver_name in installed
        except Exception:
            pass
    return False


def _mark_driver_installed(driver_name: str):
    """Record that a driver has been installed."""
    marker = os.path.join(DRIVERS_DIR, '.installed_drivers')
    existing = set()
    if os.path.isfile(marker):
        try:
            with open(marker, 'r') as f:
                existing = {line.strip() for line in f if line.strip()}
        except Exception:
            pass
    existing.add(driver_name)
    with open(marker, 'w') as f:
        f.write('\n'.join(sorted(existing)) + '\n')


def get_installed_drivers() -> list[str]:
    """Return list of installed driver names."""
    marker = os.path.join(DRIVERS_DIR, '.installed_drivers')
    if os.path.isfile(marker):
        try:
            with open(marker, 'r') as f:
                return [line.strip() for line in f if line.strip()]
        except Exception:
            pass
    return []


def install_driver_for_printer(printer_name: str, model: str = '',
                               url: str = '', driver_name: str = '') -> dict:
    """Full workflow: download, extract, register, and associate a driver.

    Args:
        printer_name: Samba share name (e.g., "HP_LaserJet")
        model: Detected printer model (for auto-suggestion)
        url: Override download URL
        driver_name: Override driver name

    Returns:
        dict with status, message, driver_name
    """
    ensure_dirs()

    # Determine driver to use
    if not url or not driver_name:
        suggestion = suggest_driver_for_model(model)
        if not suggestion.get('url') and not suggestion.get('manufacturer'):
            return {'status': 'error', 'msg': 'No driver available for this model.'}
        url = url or suggestion.get('url', '')
        driver_name = driver_name or suggestion.get('driver_name', '')

    # If the URL looks like the hardcoded one, try find_driver_url first to get a working one
    if not url or 'ftp.hp.com' in url:
        found = find_driver_url(manufacturer='HP', driver_type='pcl6')
        if found.get('status') == 'found':
            url = found['url']
            driver_name = driver_name or found.get('driver_name', HP_UPD_PCL6_DRIVER_NAME)
        elif not url:
            return {'status': 'error', 'msg': 'Could not find a working driver download URL.',
                    'find_result': found}

    # Check if already installed
    if is_driver_installed(driver_name):
        # Just associate with this printer
        ok, msg = set_printer_driver(printer_name, driver_name)
        if ok:
            return {'status': 'ok', 'msg': f'Driver "{driver_name}" already installed, associated with printer.',
                    'driver_name': driver_name}
        return {'status': 'error', 'msg': msg, 'driver_name': driver_name}

    # Download
    archive_name = os.path.basename(urlparse(url).path)
    archive_path = os.path.join(DRIVERS_CACHE, archive_name)

    if not os.path.isfile(archive_path):
        log.info(f'Downloading driver from {url}')
        if not download_file(url, archive_path):
            return {'status': 'error', 'msg': f'Failed to download driver from {url}'}

    # Extract
    extract_dir = tempfile.mkdtemp(prefix='drv_', dir=DRIVERS_CACHE)
    try:
        if not extract_driver_archive(archive_path, extract_dir):
            return {'status': 'error', 'msg': 'Failed to extract driver archive'}

        # Find driver files
        drv_info = _find_driver_files(extract_dir, 'x64')
        if not drv_info.get('files'):
            return {'status': 'error', 'msg': 'No driver files found in archive'}

        # Use detected driver name if we found one
        if drv_info.get('driver_name'):
            driver_name = drv_info['driver_name']

        # Copy to print$ share
        copied = copy_driver_files(drv_info['files'], 'x64')
        if not copied:
            return {'status': 'error', 'msg': 'Failed to copy driver files'}

        # Register with Samba
        ok, msg = register_driver_samba(driver_name, drv_info['inf'], 'x64')
        if not ok:
            return {'status': 'error', 'msg': msg, 'driver_name': driver_name,
                    'files_copied': len(copied)}

        # Associate with printer
        ok, msg = set_printer_driver(printer_name, driver_name)
        if not ok:
            # Driver is registered but association failed — still mark as installed
            _mark_driver_installed(driver_name)
            return {'status': 'partial', 'msg': f'Driver registered but setdriver failed: {msg}',
                    'driver_name': driver_name}

        _mark_driver_installed(driver_name)
        return {'status': 'ok', 'msg': f'Driver "{driver_name}" installed and associated.',
                'driver_name': driver_name, 'files_copied': len(copied)}

    finally:
        # Clean up extraction temp dir
        shutil.rmtree(extract_dir, ignore_errors=True)
