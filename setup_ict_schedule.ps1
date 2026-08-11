# ICT Predictor - Windows Task Scheduler setup.
#
# Registers a scheduled task that runs the agent automatically on weekdays.
#
# WHY A DESKTOP SESSION IS REQUIRED
#   The MetaTrader5 Python API attaches to a RUNNING MT5 terminal - it cannot
#   start or log into one itself. A task configured to "run whether user is
#   logged on or not" executes in a non-interactive session with no terminal,
#   so every cycle would report connect_failed. This task therefore runs only
#   when you are logged on, and you should set MT5 to start with Windows.
#
# TIMING
#   09:00 local sits inside the NY AM killzone (12:00-15:00 UTC) year-round:
#   09:00 EDT = 13:00 UTC, 09:00 EST = 14:00 UTC. Outside a killzone the agent
#   deliberately returns NO TRADE, so the schedule is aligned on purpose.
#
#   By default the task also REPEATS every 15 minutes for 3 hours. A single
#   daily fire samples one instant of a three-hour window - a liquidity sweep
#   that forms at 09:40 is invisible to a job that only runs at 09:00. Use
#   -Once if you genuinely want a single daily run.
#
# USAGE
#   .\setup_ict_schedule.ps1                 # weekdays 09:00, repeating to 12:00
#   .\setup_ict_schedule.ps1 -Once           # weekdays 09:00, single run
#   .\setup_ict_schedule.ps1 -At 08:30       # different start time
#   .\setup_ict_schedule.ps1 -Remove         # unregister the task
#
# Safe to re-run: an existing task with the same name is replaced.

param(
    [string]$At = "09:00",
    [int]$EveryMinutes = 15,
    [int]$ForHours = 3,
    [switch]$Once,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName   = "ICT Predictor - weekday killzone scan"
$InstallDir = "$HOME\ict_predictor_vps"
$Python     = Join-Path $InstallDir ".venv\Scripts\python.exe"
$Script     = Join-Path $InstallDir "run_ict_predictor_auto.py"
$LogDir     = Join-Path $InstallDir "data\ip_cron_logs"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName" -ForegroundColor Green
    } else {
        Write-Host "No task named '$TaskName' is registered." -ForegroundColor Yellow
    }
    exit 0
}

Write-Host "=== ICT Predictor - schedule setup ===" -ForegroundColor Cyan

if (-not (Test-Path $Python)) {
    Write-Host "Python venv not found at: $Python" -ForegroundColor Red
    Write-Host "Run setup_mt5_vps.ps1 first, or fix `$InstallDir at the top of this script."
    exit 1
}
if (-not (Test-Path $Script)) {
    Write-Host "Agent entry point not found at: $Script" -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Wrapper keeps a dated log per run so a failed cycle can be read after the
# fact - a scheduled task that writes nowhere is impossible to debug.
$Wrapper = Join-Path $InstallDir "run_ict_scheduled.cmd"
@"
@echo off
cd /d "$InstallDir"
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set DT=%%I
set STAMP=%DT:~0,8%
"$Python" "$Script" >> "$LogDir\ict_%STAMP%.log" 2>&1
"@ | Out-File -Encoding ascii $Wrapper

$action = New-ScheduledTaskAction -Execute $Wrapper

$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $At

if (-not $Once) {
    # Repetition has to be attached to the trigger after construction; the
    # -RepetitionInterval parameter is only valid on -Once triggers.
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $At `
        -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
        -RepetitionDuration (New-TimeSpan -Hours $ForHours)).Repetition
}

# Interactive logon type: MT5 must already be running in this desktop session.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ""
Write-Host "Registered: $TaskName" -ForegroundColor Green
Write-Host "  Runs      : Mon-Fri at $At (local time)"
if ($Once) {
    Write-Host "  Repeat    : none - single run per day"
    Write-Host "              NOTE: this samples ONE instant of the 3-hour killzone."
} else {
    Write-Host "  Repeat    : every $EveryMinutes min for $ForHours h (covers the NY AM killzone)"
}
Write-Host "  Runs as   : $env:USERNAME, only while logged on (MT5 must be running)"
Write-Host "  Logs      : $LogDir\ict_YYYYMMDD.log"
Write-Host ""
Write-Host "IMPORTANT - for this to actually trade:" -ForegroundColor Yellow
Write-Host "  1. Set MetaTrader 5 to start with Windows and stay logged in."
Write-Host "     The Python API attaches to a running terminal; it cannot log in itself."
Write-Host "  2. The agent is DRY-RUN unless IP_MT5_LIVE=1 is set in .env."
Write-Host "     Leave it dry-run until you have watched a few logged cycles."
Write-Host ""
Write-Host "Verify with:"
Write-Host "  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'   # fire once now to test"
Write-Host "  Get-Content '$LogDir\ict_*.log' -Tail 40"
