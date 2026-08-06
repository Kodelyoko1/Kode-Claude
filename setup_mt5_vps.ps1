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
    Write-Host ""
    Write-Host "Let's fill in your MT5 login (stored locally in .env; never committed)." -ForegroundColor Green
    Write-Host "Press Enter to skip any field and edit the file by hand later." -ForegroundColor DarkGray

    $mt5Login    = Read-Host "  MT5 login (account number)"
    $mt5Password = Read-Host "  MT5 password"
    $mt5Server   = Read-Host "  MT5 server (e.g. MetaQuotes-Demo)"

    @"
# Owner bypass for the paywall prompt — set any value
AGENT_PASSWORD=owner

# MetaTrader 5 login
MT5_LOGIN=$mt5Login
MT5_PASSWORD=$mt5Password
MT5_SERVER=$mt5Server
# MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe   # only if not default install path

# Broker symbol names — run '--doctor' and it will tell you the exact values
# to put here if the defaults (XAUUSD / USOIL) don't exist on your broker.
# IP_MT5_SYMBOL_GC=XAUUSD
# IP_MT5_SYMBOL_CL=USOIL

# Leave IP_MT5_LIVE at 0 for dry-run. Set to 1 only once you've confirmed
# dry-run output looks correct against your real account balance.
IP_MT5_LIVE=0

# DEMO/TEST ACCOUNTS ONLY. Even with IP_MT5_LIVE=1, the agent refuses to
# place orders unless MT5 reports the connected account as demo/contest.
# Only set this to 1 if you deliberately intend to trade real money.
IP_MT5_ALLOW_REAL=0
"@ | Out-File -Encoding utf8 $EnvPath
    Write-Host "  Wrote $EnvPath" -ForegroundColor Green
} else {
    Write-Host ".env already exists — leaving it as-is." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Running preflight diagnostics..." -ForegroundColor Green
& ".\.venv\Scripts\python.exe" run_ict_predictor_auto.py --doctor

Pop-Location

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host "Read the diagnostics above. Fix any [X] items, then:"
Write-Host ""
Write-Host "  cd `"$InstallDir`""
Write-Host "  .\.venv\Scripts\python.exe run_ict_predictor_auto.py --doctor    # re-check"
Write-Host "  .\.venv\Scripts\python.exe run_ict_predictor_auto.py --asset GC  # single scan"
Write-Host "  .\.venv\Scripts\python.exe run_ict_predictor_auto.py             # full cycle"
Write-Host ""
Write-Host "Most common first-run fixes:" -ForegroundColor DarkGray
Write-Host "  - Open the MT5 desktop app and log in (the API needs a RUNNING terminal)."
Write-Host "  - If a symbol is 'not found', --doctor prints your broker's actual names."
Write-Host "  - Signals only appear during killzones (07-10 and 12-15 UTC)."
