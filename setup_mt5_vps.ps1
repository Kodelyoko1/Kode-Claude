# ICT Predictor — Windows VPS bootstrap for live MetaTrader 5 order submission.
#
# Run this in PowerShell on a fresh Windows VPS (right-click PowerShell ->
# "Run as Administrator" if prompted) AFTER you've installed the MT5
# terminal and logged into your broker/demo account inside it at least once.
#
# What this does:
#   1. Checks for Git and Python (prints manual install links if missing —
#      Windows Server images often don't ship either).
#   2. Clones this repo's ICT Predictor branch.
#   3. Creates a venv and installs requirements.txt (this is where the
#      MetaTrader5 package actually installs — it's Windows-only, which is
#      why none of this works from a Linux dev box).
#   4. Writes a .env template (placeholders only — never commit real
#      credentials; this file stays local to the VPS).
#   5. Prints the exact next steps to test dry-run, then go live.
#
# Safe to re-run — it won't overwrite an existing .env or clone directory.

$ErrorActionPreference = "Stop"
$RepoUrl   = "https://github.com/Kodelyoko1/Kode-Claude.git"
$Branch    = "claude/ict-gold-crude-prediction-a7ddyy"
$InstallDir = "$HOME\ict_predictor_vps"

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

Write-Host "=== ICT Predictor — MT5 VPS setup ===" -ForegroundColor Cyan

if (-not (Test-Command git)) {
    Write-Host "Git not found. Install it first: https://git-scm.com/download/win" -ForegroundColor Yellow
    Write-Host "(Accept the default options in the installer, then re-run this script.)"
    exit 1
}
if (-not (Test-Command python)) {
    Write-Host "Python not found. Install it first: https://www.python.org/downloads/windows/" -ForegroundColor Yellow
    Write-Host "(Check 'Add python.exe to PATH' during install, then re-run this script.)"
    exit 1
}

if (Test-Path $InstallDir) {
    Write-Host "Found existing clone at $InstallDir — pulling latest instead of re-cloning." -ForegroundColor Green
    Push-Location $InstallDir
    git fetch origin $Branch
    git checkout $Branch
    git pull origin $Branch
    Pop-Location
} else {
    Write-Host "Cloning repo into $InstallDir ..." -ForegroundColor Green
    git clone --branch $Branch $RepoUrl $InstallDir
}

Push-Location $InstallDir

Write-Host "Creating virtual environment..." -ForegroundColor Green
python -m venv .venv
& ".\.venv\Scripts\pip.exe" install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt

$EnvPath = Join-Path $InstallDir ".env"
if (-not (Test-Path $EnvPath)) {
    Write-Host "Writing .env template (fill in your real values — this file is git-ignored)." -ForegroundColor Green
    @"
# Owner bypass for the paywall prompt — set any value
AGENT_PASSWORD=owner

# MetaTrader 5 login — fill these in with your real account details
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
# MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe   # only if not default install path

# Leave IP_MT5_LIVE unset (or 0) for dry-run. Set to 1 only once you've
# confirmed dry-run output looks correct against your real account balance.
IP_MT5_LIVE=0

# DEMO/TEST ACCOUNTS ONLY. Even with IP_MT5_LIVE=1, the agent refuses to
# place orders unless MT5 reports the connected account as demo/contest.
# Only set this to 1 if you deliberately intend to trade real money.
IP_MT5_ALLOW_REAL=0
"@ | Out-File -Encoding utf8 $EnvPath
} else {
    Write-Host ".env already exists — leaving it as-is." -ForegroundColor Yellow
}

Pop-Location

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host "Next steps:"
Write-Host "  1. Open MT5 on this VPS and log into your account (if you haven't already)."
Write-Host "  2. Edit $EnvPath and fill in MT5_LOGIN / MT5_PASSWORD / MT5_SERVER."
Write-Host "  3. cd `"$InstallDir`""
Write-Host "  4. .\.venv\Scripts\python.exe run_ict_predictor_auto.py --asset GC"
Write-Host "     -> Confirm the MT5 ORDER STATUS block says 'simulated' and sizes off your real balance."
Write-Host "  5. Only once that looks right, set IP_MT5_LIVE=1 in .env to arm live order submission."
