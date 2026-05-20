#!/bin/sh
# =============================================================================
# Generate Windows client setup files for printer connectivity.
#
# Creates .reg and .ps1 installers that configure Windows 11 clients to work
# with this Samba print server (applying PointAndPrint + RPC Named Pipes fix).
#
# Usage:
#   SERVER_ADDRESSES="11.1.0.4,printers.local" ./generate-client-setup.sh
#   ./generate-client-setup.sh   # auto-detects primary IP from default route
#
# Output directory: $CONFIG_DIR/client-setup/{registry-install,powershell-install}
# =============================================================================

set -e

CONFIG_DIR="${CONFIG_DIR:-/configs}"
SETUP_DIR="$CONFIG_DIR/client-setup"
REG_DIR="$SETUP_DIR/registry-install"
PS_DIR="$SETUP_DIR/powershell-install"

# ---------------------------------------------------------------------------
# Determine server addresses
# ---------------------------------------------------------------------------
get_primary_ip() {
    # Get the source IP used to reach the default gateway
    ip -4 route get 1.1.1.1 2>/dev/null | awk '/src/ {for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1
}

get_netbios_name() {
    # Extract NetBIOS name from smb.conf if available
    grep -i '^\s*netbios name' /etc/samba/smb.conf 2>/dev/null | sed 's/.*=\s*//' | tr -d ' ' || echo ""
}

# Parse SERVER_ADDRESSES (comma or semicolon separated) or auto-detect
if [ -n "$SERVER_ADDRESSES" ]; then
    # Normalise separators: replace commas and semicolons with newlines
    ADDRESSES=$(echo "$SERVER_ADDRESSES" | tr ',;' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -v '^$')
else
    PRIMARY_IP=$(get_primary_ip)
    if [ -z "$PRIMARY_IP" ]; then
        echo "[client-setup] WARNING: Could not detect primary IP. Falling back to hostname."
        PRIMARY_IP=$(hostname -i 2>/dev/null | awk '{print $1}')
    fi
    ADDRESSES="$PRIMARY_IP"
fi

# Build semicolon-separated ServerList for registry (Windows format)
SERVER_LIST=$(echo "$ADDRESSES" | tr '\n' ';' | sed 's/;$//')
NETBIOS=$(get_netbios_name)
if [ -n "$NETBIOS" ] && ! echo "$SERVER_LIST" | grep -qi "$NETBIOS"; then
    SERVER_LIST="${SERVER_LIST};${NETBIOS}"
fi

echo "[client-setup] Server addresses: $SERVER_LIST"

# ---------------------------------------------------------------------------
# Create output directories
# ---------------------------------------------------------------------------
mkdir -p "$REG_DIR" "$PS_DIR"

# ---------------------------------------------------------------------------
# Generate .reg file
# ---------------------------------------------------------------------------
# Use printf to produce proper Windows line endings (\r\n)
REG_FILE="$REG_DIR/printer-setup.reg"
printf 'Windows Registry Editor Version 5.00\r\n' > "$REG_FILE"
printf '\r\n' >> "$REG_FILE"
printf '; ==========================================================================\r\n' >> "$REG_FILE"
printf '; Printer Setup - Windows 11 Point&Print + RPC Named Pipes Fix\r\n' >> "$REG_FILE"
printf '; ==========================================================================\r\n' >> "$REG_FILE"
printf '; This registry file configures Windows 11 to connect to the Samba print\r\n' >> "$REG_FILE"
printf '; server. Two issues are addressed:\r\n' >> "$REG_FILE"
printf ';\r\n' >> "$REG_FILE"
printf '; 1. RPC Named Pipes (RpcUseNamedPipeProtocol)\r\n' >> "$REG_FILE"
printf ';    Windows 11 22H2+ changed the Print Spooler to use RPC over TCP (port 135)\r\n' >> "$REG_FILE"
printf ';    instead of Named Pipes, as a mitigation for PrintNightmare (CVE-2021-34527).\r\n' >> "$REG_FILE"
printf ';    Samba standalone servers cannot serve RPC/TCP on port 135 (requires AD DC).\r\n' >> "$REG_FILE"
printf ';    This key reverts to Named Pipes which Samba fully supports.\r\n' >> "$REG_FILE"
printf ';\r\n' >> "$REG_FILE"
printf '; 2. Point and Print (PointAndPrint)\r\n' >> "$REG_FILE"
printf ';    After PrintNightmare, Windows 11 blocks driver installation from untrusted\r\n' >> "$REG_FILE"
printf ';    print servers. These keys whitelist this server and allow silent driver\r\n' >> "$REG_FILE"
printf ';    installation without UAC prompts.\r\n' >> "$REG_FILE"
printf ';\r\n' >> "$REG_FILE"
printf '; HOW TO USE:\r\n' >> "$REG_FILE"
printf ';   1. Double-click this file and confirm the UAC prompt\r\n' >> "$REG_FILE"
printf ';   2. Restart the Print Spooler service (or reboot)\r\n' >> "$REG_FILE"
printf ';   3. Browse \\\\%s and double-click the printer to install\r\n' "$(echo "$ADDRESSES" | head -1)" >> "$REG_FILE"
printf ';\r\n' >> "$REG_FILE"
printf '; Server addresses: %s\r\n' "$SERVER_LIST" >> "$REG_FILE"
printf '; Generated: %s\r\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')" >> "$REG_FILE"
printf '; ==========================================================================\r\n' >> "$REG_FILE"
printf '\r\n' >> "$REG_FILE"
printf '[HKEY_LOCAL_MACHINE\\Software\\Policies\\Microsoft\\Windows NT\\Printers\\RPC]\r\n' >> "$REG_FILE"
printf '"RpcUseNamedPipeProtocol"=dword:00000001\r\n' >> "$REG_FILE"
printf '\r\n' >> "$REG_FILE"
printf '[HKEY_LOCAL_MACHINE\\Software\\Policies\\Microsoft\\Windows NT\\Printers\\PointAndPrint]\r\n' >> "$REG_FILE"
printf '"RestrictDriverInstallationToAdministrators"=dword:00000000\r\n' >> "$REG_FILE"
printf '"NoWarningNoElevationOnInstall"=dword:00000001\r\n' >> "$REG_FILE"
printf '"UpdatePromptSettings"=dword:00000001\r\n' >> "$REG_FILE"
printf '"InForest"=dword:00000000\r\n' >> "$REG_FILE"
printf '"TrustedServers"=dword:00000001\r\n' >> "$REG_FILE"
printf '"ServerList"="%s"\r\n' "$SERVER_LIST" >> "$REG_FILE"

echo "[client-setup] Generated: $REG_FILE"

# ---------------------------------------------------------------------------
# Generate PowerShell installer script
# ---------------------------------------------------------------------------
PS_FILE="$PS_DIR/Install-PrinterSetup.ps1"
cat > "$PS_FILE" << 'PSEOF'
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Configures Windows 11 to connect to the Samba print server.

.DESCRIPTION
    This script applies two registry fixes required for Windows 11 clients to
    use Point&Print with a standalone Samba print server:

    1. RPC Named Pipes (RpcUseNamedPipeProtocol = 1)
       Windows 11 22H2+ defaults to RPC over TCP (port 135) for the Print
       Spooler. This was a mitigation for PrintNightmare (CVE-2021-34527).
       Samba standalone cannot serve RPC/TCP - only AD Domain Controllers can.
       This setting reverts to Named Pipes which Samba supports.

    2. Point and Print trust
       After PrintNightmare, Windows blocks driver installs from untrusted
       print servers. This whitelists the server for silent driver installation.

    After running this script:
      - Open Explorer and browse to \\<SERVER>
      - Authenticate with your printer credentials
      - Double-click the printer to install it
      - The printer will remain available permanently (if you saved credentials)

.NOTES
    Generated by the Printers container.
    CVE-2021-34527 (PrintNightmare): A critical Windows Print Spooler
    vulnerability discovered in June 2021 that allowed remote code execution.
    Microsoft's fix restricted how Windows clients interact with print servers,
    breaking compatibility with non-AD print servers like Samba.
#>

param(
    [switch]$Force,
    [switch]$RestartSpooler = $true
)

$ErrorActionPreference = 'Stop'

PSEOF

# Inject the server list as a PowerShell variable
printf '$ServerList = "%s"\n' "$SERVER_LIST" >> "$PS_FILE"
printf '$ServerAddresses = @(%s)\n\n' "$(echo "$ADDRESSES" | while read -r addr; do printf '"%s",' "$addr"; done | sed 's/,$//')" >> "$PS_FILE"

cat >> "$PS_FILE" << 'PSEOF'
Write-Host "`n=== Printer Server Setup ===" -ForegroundColor Cyan
Write-Host "Server(s): $ServerList"
Write-Host ""

# Check if already configured
$existing = Get-ItemProperty -Path "HKLM:\Software\Policies\Microsoft\Windows NT\Printers\RPC" -Name RpcUseNamedPipeProtocol -ErrorAction SilentlyContinue
if ($existing -and $existing.RpcUseNamedPipeProtocol -eq 1 -and -not $Force) {
    Write-Host "[OK] RPC Named Pipes already configured." -ForegroundColor Green
} else {
    # Apply RPC fix
    $rpcPath = "HKLM:\Software\Policies\Microsoft\Windows NT\Printers\RPC"
    if (-not (Test-Path $rpcPath)) { New-Item -Path $rpcPath -Force | Out-Null }
    Set-ItemProperty -Path $rpcPath -Name "RpcUseNamedPipeProtocol" -Value 1 -Type DWord
    Write-Host "[APPLIED] RPC Named Pipes enabled." -ForegroundColor Yellow
}

# Apply Point and Print settings
$pnpPath = "HKLM:\Software\Policies\Microsoft\Windows NT\Printers\PointAndPrint"
if (-not (Test-Path $pnpPath)) { New-Item -Path $pnpPath -Force | Out-Null }

$settings = @{
    RestrictDriverInstallationToAdministrators = 0
    NoWarningNoElevationOnInstall = 1
    UpdatePromptSettings = 1
    InForest = 0
    TrustedServers = 1
}

foreach ($key in $settings.Keys) {
    Set-ItemProperty -Path $pnpPath -Name $key -Value $settings[$key] -Type DWord
}
Set-ItemProperty -Path $pnpPath -Name "ServerList" -Value $ServerList -Type String
Write-Host "[APPLIED] Point and Print trust configured for: $ServerList" -ForegroundColor Yellow

# Restart Print Spooler
if ($RestartSpooler) {
    Write-Host "`nRestarting Print Spooler service..." -ForegroundColor Cyan
    Restart-Service -Name Spooler -Force
    Write-Host "[OK] Print Spooler restarted." -ForegroundColor Green
}

Write-Host "`n=== Setup Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
foreach ($addr in $ServerAddresses) {
    Write-Host "  1. Open Explorer -> \\$addr" -ForegroundColor White
}
Write-Host "  2. Enter your credentials (check 'Remember my credentials')" -ForegroundColor White
Write-Host "  3. Double-click the printer to install it" -ForegroundColor White
Write-Host "  4. Done! The printer will work permanently." -ForegroundColor White
Write-Host ""
PSEOF

echo "[client-setup] Generated: $PS_FILE"

# ---------------------------------------------------------------------------
# Generate IPP Printer installer (no registry keys required!)
# Uses Microsoft IPP Class Driver which auto-detects capabilities.
# ---------------------------------------------------------------------------
IPP_FILE="$PS_DIR/Install-IPPPrinter.ps1"
cat > "$IPP_FILE" << 'IPPEOF'
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs printers via IPP (Internet Printing Protocol) — NO registry keys needed.

.DESCRIPTION
    This script adds printers using the Microsoft IPP Class Driver which is built
    into Windows 10/11. Unlike the Samba/Point&Print method, this approach:

    - Requires NO registry modifications
    - Requires NO special Group Policy settings
    - Auto-detects printer capabilities:
        * Duplex (two-sided printing)
        * Color vs Black & White
        * Paper sizes (A4, Letter, Legal, etc.)
        * Print quality settings
        * Tray selection
    - Uses the modern IPP Everywhere standard
    - Future-proof (Apple, Google, Microsoft all converging on IPP)

    The Microsoft IPP Class Driver queries the print server via IPP
    Get-Printer-Attributes to discover all capabilities automatically.

.PARAMETER PrinterName
    Custom name for the printer in Windows. Defaults to server-provided name.

.PARAMETER Credential
    PSCredential object. If not provided, Windows will prompt interactively.

.NOTES
    Generated by the Printers container.
    This method works because CUPS (the print server) speaks native IPP and
    advertises printer capabilities that Windows can auto-discover.

    WHY IPP IS BETTER THAN SAMBA/Point&Print:
    - No PrintNightmare (CVE-2021-34527) registry workarounds needed
    - No driver download from server (Windows uses its built-in driver)
    - Capabilities are always up-to-date (queried live from server)
    - Works across subnets without NetBIOS/WINS
    - Industry standard (RFC 8011) supported by all modern OSes
#>

param(
    [string]$PrinterName,
    [switch]$Force,
    [switch]$ListOnly
)

$ErrorActionPreference = 'Stop'

IPPEOF

# Inject server addresses
printf '$ServerAddresses = @(%s)\n' "$(echo "$ADDRESSES" | while read -r addr; do printf '"%s",' "$addr"; done | sed 's/,$//')" >> "$IPP_FILE"
printf '$PrimaryServer = "%s"\n' "$(echo "$ADDRESSES" | head -1)" >> "$IPP_FILE"
printf '$CupsPort = 631\n\n' >> "$IPP_FILE"

# Query CUPS for available printers and inject them
PRINTER_LIST=""
if command -v lpstat >/dev/null 2>&1; then
    PRINTER_LIST=$(lpstat -a 2>/dev/null | awk '{print $1}' | tr '\n' ',' | sed 's/,$//')
fi
if [ -z "$PRINTER_LIST" ]; then
    PRINTER_LIST="laser"
fi
printf '$AvailablePrinters = @(%s)\n\n' "$(echo "$PRINTER_LIST" | tr ',' '\n' | while read -r p; do printf '"%s",' "$p"; done | sed 's/,$//')" >> "$IPP_FILE"

cat >> "$IPP_FILE" << 'IPPEOF'
Write-Host ""
Write-Host "=== IPP Printer Installation ===" -ForegroundColor Cyan
Write-Host "Server: $PrimaryServer : $CupsPort" -ForegroundColor White
Write-Host "Method: Microsoft IPP Class Driver (built-in, auto-detects capabilities)" -ForegroundColor White
Write-Host ""

# List available printers
Write-Host "Available printers on server:" -ForegroundColor Yellow
for ($i = 0; $i -lt $AvailablePrinters.Count; $i++) {
    $p = $AvailablePrinters[$i]
    Write-Host "  [$($i+1)] $p  ->  http://${PrimaryServer}:${CupsPort}/printers/$p" -ForegroundColor White
}
Write-Host ""

if ($ListOnly) { return }

# Determine which printers to install
$printersToInstall = @()
if ($PrinterName) {
    $printersToInstall = @($PrinterName)
} else {
    if ($AvailablePrinters.Count -eq 1) {
        $printersToInstall = $AvailablePrinters
    } else {
        Write-Host "Enter printer number(s) to install (comma-separated), or 'all':" -ForegroundColor Yellow
        $choice = Read-Host "Choice"
        if ($choice -eq 'all') {
            $printersToInstall = $AvailablePrinters
        } else {
            $indices = $choice -split ',' | ForEach-Object { [int]$_.Trim() - 1 }
            $printersToInstall = $indices | ForEach-Object { $AvailablePrinters[$_] }
        }
    }
}

# Check if Microsoft IPP Class Driver is available
$ippDriver = Get-PrinterDriver -Name "Microsoft IPP Class Driver" -ErrorAction SilentlyContinue
if (-not $ippDriver) {
    Write-Host "[INFO] Installing Microsoft IPP Class Driver from driver store..." -ForegroundColor Yellow
    try {
        Add-PrinterDriver -Name "Microsoft IPP Class Driver" -ErrorAction Stop
        Write-Host "[OK] Driver installed." -ForegroundColor Green
    } catch {
        Write-Host "[ERROR] Could not install Microsoft IPP Class Driver." -ForegroundColor Red
        Write-Host "  This driver should be built into Windows 10/11." -ForegroundColor Red
        Write-Host "  Try: pnputil /add-driver $env:SystemRoot\INF\prnms009.inf /install" -ForegroundColor Yellow
        exit 1
    }
}

# Install each printer
foreach ($printer in $printersToInstall) {
    $ippUrl = "http://${PrimaryServer}:${CupsPort}/printers/$printer"
    $displayName = if ($PrinterName -and $printersToInstall.Count -eq 1) { $PrinterName } else { "$printer ($PrimaryServer)" }

    Write-Host "`nInstalling: $displayName" -ForegroundColor Cyan
    Write-Host "  IPP URL: $ippUrl" -ForegroundColor Gray

    # Check if printer already exists
    $existing = Get-Printer -Name $displayName -ErrorAction SilentlyContinue
    if ($existing -and -not $Force) {
        Write-Host "  [SKIP] Printer '$displayName' already exists. Use -Force to reinstall." -ForegroundColor Yellow
        continue
    }
    if ($existing) {
        Remove-Printer -Name $displayName -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }

    # Remove existing port if present
    $existingPort = Get-PrinterPort -Name $ippUrl -ErrorAction SilentlyContinue
    if ($existingPort) {
        Remove-PrinterPort -Name $ippUrl -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }

    # Create printer port (IPP URL as port name — Windows IPP port monitor handles this)
    try {
        # Try native IPP port creation first
        & rundll32.exe printui.dll,PrintUIEntry /if /b "$displayName" /r "$ippUrl" /m "Microsoft IPP Class Driver" 2>$null
        Start-Sleep -Seconds 2

        # Verify it was created
        $installed = Get-Printer -Name $displayName -ErrorAction SilentlyContinue
        if ($installed) {
            Write-Host "  [OK] Printer installed successfully!" -ForegroundColor Green
            Write-Host "  [INFO] Capabilities (duplex, color, paper sizes) auto-detected." -ForegroundColor Green
        } else {
            throw "printui method did not create the printer"
        }
    } catch {
        Write-Host "  [FALLBACK] Trying alternative installation method..." -ForegroundColor Yellow
        try {
            # Fallback: Create port manually + add printer
            $portName = "IPP_${PrimaryServer}_${printer}"
            if (-not (Get-PrinterPort -Name $portName -ErrorAction SilentlyContinue)) {
                Add-PrinterPort -Name $portName -PrinterHostAddress $PrimaryServer -PortNumber $CupsPort
            }
            Add-Printer -Name $displayName -DriverName "Microsoft IPP Class Driver" -PortName $portName
            Write-Host "  [OK] Printer installed (TCP fallback)." -ForegroundColor Green
            Write-Host "  [NOTE] Some capabilities may need manual configuration." -ForegroundColor Yellow
        } catch {
            Write-Host "  [ERROR] Automated installation failed: $_" -ForegroundColor Red
            Write-Host "" -ForegroundColor White
            Write-Host "  MANUAL INSTALLATION (guaranteed to work):" -ForegroundColor Yellow
            Write-Host "  1. Open: Settings -> Bluetooth & Devices -> Printers" -ForegroundColor White
            Write-Host "  2. Click 'Add device' then 'Add manually'" -ForegroundColor White
            Write-Host "  3. Select 'Add a printer using an IP address or hostname'" -ForegroundColor White
            Write-Host "  4. Device type: IPP Device" -ForegroundColor White
            Write-Host "  5. Hostname: $PrimaryServer" -ForegroundColor White
            Write-Host "  6. Queue: /printers/$printer" -ForegroundColor White
            Write-Host "  7. Windows will auto-detect capabilities" -ForegroundColor White
            Write-Host "  8. Enter credentials when prompted" -ForegroundColor White
        }
    }
}

Write-Host "`n=== Installation Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "CAPABILITIES AUTO-DETECTED VIA IPP:" -ForegroundColor Cyan
Write-Host "  - Duplex (two-sided) printing: detected from server" -ForegroundColor White
Write-Host "  - Color / Black & White: detected from server" -ForegroundColor White
Write-Host "  - Paper sizes (A4, Letter, etc.): detected from server" -ForegroundColor White
Write-Host "  - Print quality options: detected from server" -ForegroundColor White
Write-Host ""
Write-Host "AUTHENTICATION:" -ForegroundColor Cyan
Write-Host "  When you print for the first time, Windows will ask for credentials." -ForegroundColor White
Write-Host "  Use the same username/password as the web UI (http://${PrimaryServer}:8082)." -ForegroundColor White
Write-Host "  Windows stores these permanently - you won't be asked again." -ForegroundColor White
Write-Host ""
Write-Host "PRINT HISTORY:" -ForegroundColor Cyan
Write-Host "  View your print logs at: http://${PrimaryServer}:8082" -ForegroundColor White
Write-Host ""
IPPEOF

echo "[client-setup] Generated: $IPP_FILE"

# ---------------------------------------------------------------------------
# Generate README for registry-install share
# ---------------------------------------------------------------------------
cat > "$REG_DIR/README.txt" << EOF
================================================================================
  PRINTER SETUP - Registry File (.reg)
================================================================================

  Server: $SERVER_LIST
  Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')

WHAT IS THIS?
  This .reg file configures your Windows 11 PC to connect to this print server.

WHY IS THIS NEEDED?
  Microsoft locked down printer driver installation after the "PrintNightmare"
  vulnerability (CVE-2021-34527, June 2021). This was a critical security flaw
  that allowed attackers to execute code via the Windows Print Spooler.

  The fix broke compatibility with non-Active-Directory print servers (like this
  Samba server). Two registry changes are required:

  1. RPC Named Pipes: Windows 11 22H2+ uses RPC/TCP (port 135) for printing.
     Samba standalone servers cannot serve this protocol. This fix reverts to
     the traditional Named Pipes transport.

  2. Point and Print Trust: Windows blocks driver downloads from untrusted
     servers. This fix whitelists this server ($SERVER_LIST).

HOW TO USE:
  1. Double-click "printer-setup.reg"
  2. Click "Yes" on the UAC prompt
  3. Click "Yes" on the Registry Editor confirmation
  4. Restart your PC (or run: net stop spooler && net start spooler)
  5. Open Explorer -> \\\\$(echo "$ADDRESSES" | head -1)
  6. Enter your credentials and check "Remember my credentials"
  7. Double-click the printer to install

AFTER SETUP:
  - The printer stays installed permanently (even after reboots)
  - If you saved credentials, printing works without any prompts forever
  - Your print history is viewable at http://$(echo "$ADDRESSES" | head -1):8082

SECURITY NOTE:
  This ONLY trusts this specific server ($SERVER_LIST) for driver installation.
  It does NOT disable security for other print servers.
================================================================================
EOF

echo "[client-setup] Generated: $REG_DIR/README.txt"

# ---------------------------------------------------------------------------
# Generate README for powershell-install share
# ---------------------------------------------------------------------------
cat > "$PS_DIR/README.txt" << EOF
================================================================================
  PRINTER SETUP - PowerShell Scripts
================================================================================

  Server: $SERVER_LIST
  Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')

  This share contains TWO installation methods:

  +---------------------------------------------------------------------------+
  | OPTION 1: Install-IPPPrinter.ps1  (RECOMMENDED - No registry changes!)    |
  +---------------------------------------------------------------------------+
  |                                                                           |
  | Uses the modern IPP protocol with Windows' built-in Microsoft IPP Class   |
  | Driver. NO registry modifications required. Auto-detects:                 |
  |   - Duplex (two-sided) printing capability                               |
  |   - Color vs Black & White                                                |
  |   - Paper sizes (A4, Letter, Legal, etc.)                                 |
  |   - Print quality settings                                                |
  |   - Tray selection                                                        |
  |                                                                           |
  | HOW TO USE:                                                               |
  |   1. Open PowerShell as Administrator                                     |
  |   2. Run: \\\\$(echo "$ADDRESSES" | head -1)\\powershell-install\\Install-IPPPrinter.ps1  |
  |   3. Follow prompts (select printer, enter credentials once)              |
  |                                                                           |
  | The Microsoft IPP Class Driver queries the server for capabilities        |
  | using the IPP Get-Printer-Attributes operation (RFC 8011).                |
  | This is the future-proof industry standard.                               |
  +---------------------------------------------------------------------------+

  +---------------------------------------------------------------------------+
  | OPTION 2: Install-PrinterSetup.ps1  (Samba/Point&Print - needs registry)  |
  +---------------------------------------------------------------------------+
  |                                                                           |
  | Traditional method: applies registry keys to allow Samba driver download. |
  | After running, browse \\\\$(echo "$ADDRESSES" | head -1) and double-click printer.          |
  |                                                                           |
  | Requires registry changes because of PrintNightmare (CVE-2021-34527).     |
  | See below for details.                                                    |
  +---------------------------------------------------------------------------+

COMPARISON:

  Feature                    | IPP (Option 1)        | Samba (Option 2)
  ---------------------------+-----------------------+---------------------
  Registry changes needed    | NO                    | YES (admin required)
  Driver download            | None (built-in)       | From server (print$)
  Capability auto-detect     | YES (duplex/color/    | Depends on driver
                             |  paper/quality)       |
  Works across subnets       | YES (IP-based)        | YES (IP-based)
  Requires Print Spooler fix | NO                    | YES (RPC Named Pipes)
  Future-proof               | YES (industry std)    | Legacy (being phased out)
  Auth method                | HTTP Basic (per-job)  | SMB (session-based)
  Credential persistence     | Windows stores them   | Credential Manager

ABOUT PrintNightmare (CVE-2021-34527):
  In June 2021, a critical vulnerability was discovered in the Windows Print
  Spooler service. It allowed Remote Code Execution via crafted print drivers
  and Local Privilege Escalation via Point and Print.

  Microsoft's response:
  - KB5005033 (August 2021): Restricted RPC to TCP-only (breaks Samba)
  - Required admin rights for all Point&Print driver installs
  - Added server trust verification for driver downloads

  The IPP method (Option 1) completely sidesteps these restrictions because
  it uses Windows' own built-in driver — no external driver download needed.

PRINT JOB LOGGING:
  Both installation methods (IPP and Samba) log print jobs to the SAME
  database in the SAME format. The logging happens at the CUPS level,
  which is the common path for ALL print jobs regardless of source:

    Samba path: Windows -> SMB auth -> Samba -> CUPS queue -> log
    IPP path:   Windows -> HTTP auth -> CUPS queue directly -> log
    Double-click (.cmd): Same as IPP path above

  Every print job is recorded with:
    - Username (who printed)
    - Document title
    - Page count
    - Color mode (color / black & white)
    - Printer name (which queue)
    - Timestamp (when)
    - File size

  View your print history at: http://$(echo "$ADDRESSES" | head -1):8082
  Same credentials work for printing AND viewing the web UI.
================================================================================
EOF

echo "[client-setup] Generated: $PS_DIR/README.txt"

# ---------------------------------------------------------------------------
# Generate per-printer .cmd files for double-click install/open-queue.
# These go in a dedicated share so users can browse and double-click.
# ---------------------------------------------------------------------------
CLICK_DIR="$SETUP_DIR/ipp-printers"
mkdir -p "$CLICK_DIR"

# Clean old .cmd files (printers may have been removed)
rm -f "$CLICK_DIR"/*.cmd "$CLICK_DIR"/README.txt

PRIMARY_ADDR=$(echo "$ADDRESSES" | head -1)

echo "$PRINTER_LIST" | tr ',' '\n' | while read -r PNAME; do
    [ -z "$PNAME" ] && continue
    CMD_FILE="$CLICK_DIR/${PNAME}.cmd"
    PS1_FILE="$CLICK_DIR/${PNAME}-install.ps1"

    # --- Generate the PowerShell install script (no quoting issues) ---
    cat > "$PS1_FILE" << 'PS1EOF'
# Auto-generated IPP printer installer — run elevated
param(
    [string]$PrinterName,
    [string]$IppUrl,
    [string]$ServerHost
)
$ErrorActionPreference = 'Stop'

Write-Host "Installing: $PrinterName" -ForegroundColor Cyan
Write-Host "Server:     $IppUrl" -ForegroundColor Cyan
Write-Host ""

# Check if already installed
$existing = Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[OK] Printer '$PrinterName' is already installed." -ForegroundColor Green
    exit 0
}

try {
    # Method 1: Add-Printer with IPP port
    Write-Host "Creating printer port..." -ForegroundColor Yellow
    $portExists = Get-PrinterPort -Name $IppUrl -ErrorAction SilentlyContinue
    if (-not $portExists) {
        Add-PrinterPort -Name $IppUrl -PrinterHostAddress $ServerHost -PortNumber 631
    }
    Write-Host "Adding printer with Microsoft IPP Class Driver..." -ForegroundColor Yellow
    Add-Printer -Name $PrinterName -DriverName "Microsoft IPP Class Driver" -PortName $IppUrl
    Write-Host ""
    Write-Host "[OK] Printer '$PrinterName' installed successfully!" -ForegroundColor Green
} catch {
    Write-Host "[!] Method 1 failed: $_" -ForegroundColor Yellow
    Write-Host "Trying alternative method (printui)..." -ForegroundColor Yellow
    try {
        $argStr = "printui.dll,PrintUIEntry /if /b `"$PrinterName`" /r `"$IppUrl`" /m `"Microsoft IPP Class Driver`""
        $proc = Start-Process rundll32.exe -ArgumentList $argStr -Wait -PassThru -NoNewWindow
        if ($proc.ExitCode -ne 0) { throw "printui exit code: $($proc.ExitCode)" }
        Write-Host "[OK] Installed via printui!" -ForegroundColor Green
    } catch {
        Write-Host ""
        Write-Host "[FAIL] Both methods failed: $_" -ForegroundColor Red
        Write-Host ""
        Write-Host "MANUAL INSTALL:" -ForegroundColor White
        Write-Host "  Settings > Printers > Add device > Add manually"
        Write-Host "  Type: IPP Device | Host: $ServerHost | Port: 631"
        Write-Host "  Queue: /printers/$($PrinterName.Split(' ')[0])"
        Write-Host ""
        Read-Host "Press Enter to close"
        exit 1
    }
}
PS1EOF

    # --- Generate the .cmd launcher ---
    cat > "$CMD_FILE" << CMDEOF
@echo off
setlocal EnableDelayedExpansion
REM === ${PNAME} — Double-click to install or open print queue ===
REM Server: ${PRIMARY_ADDR}:631 | Protocol: IPP | Driver: Microsoft IPP Class Driver

REM Handle UNC path (network share) — pushd maps a temp drive letter
pushd "%~dp0" 2>nul || cd /d "%SystemRoot%"

set "PRINTER_NAME=${PNAME} on ${PRIMARY_ADDR}"
set "IPP_URL=http://${PRIMARY_ADDR}:631/printers/${PNAME}"
set "SERVER_HOST=${PRIMARY_ADDR}"

REM Check if printer is already installed
powershell -NoProfile -Command "if (Get-Printer -Name '!PRINTER_NAME!' -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if !errorlevel! equ 0 (
    echo.
    echo   Printer "!PRINTER_NAME!" is already installed.
    echo   Opening print queue...
    echo.
    start "" rundll32 printui.dll,PrintUIEntry /o /n "!PRINTER_NAME!"
    goto :end
)

echo.
echo   ================================================================
echo   Installing printer: !PRINTER_NAME!
echo   Server: !IPP_URL!
echo   Driver: Microsoft IPP Class Driver (built-in, auto-detects caps)
echo   ================================================================
echo.
echo   Administrator rights are required for first-time installation.
echo   You will see a UAC prompt if not running as admin.
echo.

REM Copy install script to temp (in case UNC not accessible from elevated context)
set "INSTALL_PS1=%TEMP%\install-${PNAME}-%RANDOM%.ps1"
copy "%~dp0${PNAME}-install.ps1" "!INSTALL_PS1!" >nul 2>&1

REM Run elevated with parameters
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"!INSTALL_PS1!\" -PrinterName \"!PRINTER_NAME!\" -IppUrl \"!IPP_URL!\" -ServerHost \"!SERVER_HOST!\"'"
del "!INSTALL_PS1!" 2>nul

REM Verify installation
timeout /t 3 /nobreak >nul
powershell -NoProfile -Command "if (Get-Printer -Name '!PRINTER_NAME!' -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if !errorlevel! equ 0 (
    echo.
    echo   [OK] Printer installed successfully!
    echo   Opening print queue...
    start "" rundll32 printui.dll,PrintUIEntry /o /n "!PRINTER_NAME!"
) else (
    echo.
    echo   [!] Automated install may have failed.
    echo.
    echo   MANUAL FALLBACK:
    echo     1. Open Settings ^> Bluetooth ^& Devices ^> Printers
    echo     2. Click "Add device" then "Add manually"
    echo     3. Select "Add a printer using an IP address or hostname"
    echo     4. Device type: IPP Device
    echo     5. Hostname: ${PRIMARY_ADDR}   Port: 631
    echo     6. Queue: /printers/${PNAME}
    echo     7. Capabilities will be auto-detected
    echo.
    pause
)

:end
popd 2>nul
endlocal
CMDEOF
    echo "[client-setup] Generated: $CMD_FILE, $PS1_FILE"
done

# README for the ipp-printers share
cat > "$CLICK_DIR/README.txt" << EOF
================================================================================
  IPP PRINTERS — Double-Click to Install or Open Queue
================================================================================

  Server: $PRIMARY_ADDR
  Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')

HOW TO USE:
  1. Browse this folder (\\\\${PRIMARY_ADDR}\\ipp-printers)
  2. Double-click the printer you want (e.g., laser.cmd)
  3. First time: confirms UAC → installs via IPP → opens queue
     Already installed: just opens the print queue

WHAT HAPPENS ON INSTALL:
  - Uses Microsoft IPP Class Driver (built into Windows 10/11)
  - NO registry changes needed
  - NO driver download from server
  - Auto-detects capabilities from the print server:
      * Duplex (two-sided printing)
      * Color vs Black & White
      * Paper sizes (A4, Letter, Legal, etc.)
      * Print quality settings
      * Tray selection

AUTHENTICATION:
  When you print for the first time, Windows will prompt for credentials.
  Use the same login as the web UI (http://${PRIMARY_ADDR}:8082).
  Windows remembers them permanently.

PRINT JOB LOGGING (same for ALL methods — Samba, IPP, .cmd):
  Every print job is recorded regardless of how the printer was installed:
    - Username: who printed (from authentication)
    - Document: original document title
    - Pages: total page count
    - Color: color or black & white
    - Printer: which printer was used
    - When: date and time
    - Size: file size

  Both Samba and IPP paths go through the same CUPS print queue.
  The logging system captures jobs at the CUPS level, so the database
  and format are identical no matter which client method you use.

  Same credentials for: printing + web UI (view history)

PRINT HISTORY:
  View what you printed at: http://${PRIMARY_ADDR}:8082
================================================================================
EOF

echo "[client-setup] Generated: $CLICK_DIR/README.txt"
echo "[client-setup] Done. Shares ready at: $REG_DIR, $PS_DIR, $CLICK_DIR"
