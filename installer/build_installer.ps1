<#
.SYNOPSIS
    Build the VCDS Toolkit Windows installer.

.DESCRIPTION
    1. Bundles the desktop GUI into a self-contained folder with PyInstaller.
    2. If Inno Setup (iscc.exe) is available, compiles a Setup.exe installer.
       Otherwise the PyInstaller folder is left as a portable build.

    Run from the repository root or from the installer/ folder. Uses the active
    Python (prefer your venv: .\.venv\Scripts\Activate.ps1 first).

.EXAMPLE
    .\installer\build_installer.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

# Resolve repo root (this script lives in <root>\installer).
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
Set-Location $Root

Write-Host "==> Repo root: $Root" -ForegroundColor Cyan

# Read the version from pyproject.toml (single source of truth).
$Version = & $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
$Version = $Version.Trim()
Write-Host "==> Version: $Version" -ForegroundColor Cyan

# Ensure build tooling + the project itself are installed.
Write-Host "==> Installing PyInstaller and the project (with GUI extras)..." -ForegroundColor Cyan
& $Python -m pip install --upgrade pip pyinstaller | Out-Null
# Install GUI deps plus mcp/certifi so the spec can bundle the MCP server + CA store.
& $Python -m pip install -e ".[gui]" "mcp>=1.2.0" "certifi>=2023.0" | Out-Null

# 1) PyInstaller bundle.
Write-Host "==> Running PyInstaller..." -ForegroundColor Cyan
& $Python -m PyInstaller "installer\vcds_gui.spec" --clean --noconfirm
$BundleDir = Join-Path $Root "dist\VCDS Toolkit"
if (-not (Test-Path (Join-Path $BundleDir "VCDS Toolkit.exe"))) {
    throw "PyInstaller did not produce 'VCDS Toolkit.exe' in $BundleDir"
}
Write-Host "==> Bundle: $BundleDir" -ForegroundColor Green

# 2) Inno Setup installer (optional).
$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $Iscc) {
    foreach ($cand in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $cand) { $Iscc = $cand; break }
    }
}

if ($Iscc) {
    Write-Host "==> Compiling installer with Inno Setup..." -ForegroundColor Cyan
    & $Iscc "installer\vcds-toolkit.iss" "/DMyAppVersion=$Version"
    $Setup = Join-Path $Root "installer\Output\VCDS-Toolkit-Setup-$Version.exe"
    Write-Host "==> Installer: $Setup" -ForegroundColor Green
} else {
    Write-Warning "Inno Setup (iscc.exe) not found. Skipped installer compile."
    Write-Warning "Install it from https://jrsoftware.org/isdl.php, then re-run, OR"
    Write-Warning "ship the portable folder: $BundleDir"
}

Write-Host "Done." -ForegroundColor Green
