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
  PRINTER SETUP - PowerShell Script
================================================================================

  Server: $SERVER_LIST
  Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')

WHAT IS THIS?
  This PowerShell script configures your Windows 11 PC to connect to this
  print server. It does the same thing as the .reg file but with better
  feedback and error handling.

WHY IS THIS NEEDED?
  Microsoft locked down printer driver installation after the "PrintNightmare"
  vulnerability (CVE-2021-34527, June 2021). This was a critical security flaw
  that allowed remote code execution via the Windows Print Spooler service.

  After the fix, Windows 11:
  - Uses RPC/TCP (port 135) instead of Named Pipes for print server comms
  - Blocks driver installation from untrusted print servers
  - Requires administrator approval for Point&Print driver installs

  This Samba print server cannot serve RPC/TCP (only AD Domain Controllers can),
  and is not domain-joined, so both issues must be resolved on each client.

HOW TO USE:
  Option A - Run from this share (recommended):
    1. Open PowerShell as Administrator
    2. Run: \\\\$(echo "$ADDRESSES" | head -1)\\powershell-install\\Install-PrinterSetup.ps1
    3. Follow the on-screen instructions

  Option B - Copy and run locally:
    1. Copy Install-PrinterSetup.ps1 to your Desktop
    2. Right-click -> "Run with PowerShell" (must be Admin)

  Note: If you get "script execution is disabled", run first:
    Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process

AFTER SETUP:
  - Open Explorer -> \\\\$(echo "$ADDRESSES" | head -1)
  - Enter your credentials and check "Remember my credentials"
  - Double-click the printer to install
  - The printer stays installed permanently
  - Print history viewable at http://$(echo "$ADDRESSES" | head -1):8082

ABOUT PrintNightmare (CVE-2021-34527):
  Discovered in June 2021, PrintNightmare was a critical vulnerability in the
  Windows Print Spooler service. It allowed:
  - Remote Code Execution (RCE) via crafted print driver packages
  - Local Privilege Escalation via the Point and Print mechanism

  Microsoft's response (KB5005033 and later):
  - Restricted RPC transport to TCP-only (breaks Samba Named Pipes)
  - Required admin rights for all Point&Print driver installs
  - Added server trust verification for driver downloads

  These mitigations are correct for untrusted environments but break legitimate
  Samba print servers. This script specifically whitelists ONLY this server.
================================================================================
EOF

echo "[client-setup] Generated: $PS_DIR/README.txt"
echo "[client-setup] Done. Shares ready at: $REG_DIR and $PS_DIR"
