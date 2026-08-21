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
#   The NY AM killzone is DEFINED as 07:00-10:00 New York local time, and a
#   Task Scheduler trigger is also expressed in local time. Starting at 07:00
#   with 3 hours of repetition therefore tracks the window EXACTLY, in both
#   seasons, with no DST arithmetic on either side:
#
#     winter (EST)  07:00-10:00 local = 12:00-15:00 UTC = the killzone
#     summer (EDT)  07:00-10:00 local = 11:00-14:00 UTC = the killzone
#
#   (An earlier default of 09:00 caught only the final hour of the window.)
#
#   The task REPEATS every 15 minutes across those 3 hours. A single daily
#   fire samples one instant - a liquidity sweep forming at 07:40 is invisible
#   to a job that only runs at 07:00. Use -Once if you want the single run.
#
#   NOTE: this alignment assumes the machine's clock is set to US Eastern. On
#   a VPS in another timezone, set -At to whatever local time corresponds to
#   07:00 New York, or set the VPS clock to Eastern.
#
# USAGE
#   .\setup_ict_schedule.ps1                 # weekdays 07:00-10:00 local (the NY AM killzone)
#   .\setup_ict_schedule.ps1 -Once           # weekdays 07:00, single run
#   .\setup_ict_schedule.ps1 -At 02:00       # London killzone instead (02:00-05:00 NY)
#   .\setup_ict_schedule.ps1 -Remove         # unregister the task
#
# Safe to re-run: an existing task with the same name is replaced.

param(
    [string]$At = "07:00",
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

# Runner keeps a dated log per run so a failed cycle can be read after the
# fact - a scheduled task that writes nowhere is impossible to debug.
#
# This is a PowerShell runner, not a .cmd. An earlier version used a batch
# wrapper that derived the date stamp from `wmic os get localdatetime`, but
# Microsoft REMOVED wmic in recent Windows 11 builds: the variable came back
# empty, the redirect target was malformed, and the wrapper died before Python
# started - producing a task that "ran" with no log at all. Get-Date has no
# such dependency, and staying in PowerShell also avoids cmd's quoting rules
# around paths with spaces.
$Runner = Join-Path $InstallDir "run_ict_scheduled.ps1"
@"
Set-Location -LiteralPath '$InstallDir'
# Belt and braces with the reconfigure() in run_ict_predictor_auto.py: Windows
# defaults redirected stdout to cp1252, which cannot encode the report emoji.
`$env:PYTHONIOENCODING = 'utf-8'
`$env:PYTHONUTF8 = '1'
`$stamp = Get-Date -Format 'yyyyMMdd'
`$log = Join-Path '$LogDir' ("ict_" + `$stamp + ".log")
"=== run started {0} ===" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') |
    Out-File -FilePath `$log -Append -Encoding utf8
& '$Python' '$Script' 2>&1 |
    Out-File -FilePath `$log -Append -Encoding utf8
`$code = `$LASTEXITCODE
if (`$null -eq `$code) { `$code = 1 }   # launch failed before Python set one
"=== run finished {0} (exit {1}) ===" -f (Get-Date -Format 'HH:mm:ss'), `$code |
    Out-File -FilePath `$log -Append -Encoding utf8
# Propagate Python's exit code to Task Scheduler. Without this the wrapper
# always returns 0, so LastTaskResult reads "success" for a run that crashed -
# which is exactly how a dead agent goes unnoticed for a week. Checking the
# task's own result should be enough to know the agent is healthy; before this
# it told you only that powershell.exe started.
exit `$code
"@ | Out-File -Encoding ascii $Runner

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Runner`""

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
    Write-Host "  Repeat    : every $EveryMinutes min for $ForHours h"
    Write-Host "              07:00-10:00 local == the NY AM killzone in both seasons."
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
