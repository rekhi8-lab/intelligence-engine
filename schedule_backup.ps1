# schedule_backup.ps1
# Creates a Windows Task Scheduler task to back up the Intelligence Engine
# to Google Drive every Monday at 9:00 AM.
#
# Run once as Administrator:
#   powershell -ExecutionPolicy Bypass -File schedule_backup.ps1

$TaskName    = "IntelligenceBackup"
$PythonPath  = (Get-Command python -ErrorAction SilentlyContinue).Source
$ScriptPath  = "C:\Users\Acer\Desktop\Trend Scraper\gdrive_backup.py"
$WorkingDir  = "C:\Users\Acer\Desktop\Trend Scraper"
$LogFile     = "C:\Users\Acer\Desktop\Trend Scraper\backup.log"

if (-not $PythonPath) {
    Write-Host "[!] Python not found in PATH. Install Python and try again." -ForegroundColor Red
    exit 1
}

Write-Host "Python found at: $PythonPath"

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

# Build the action — runs: python gdrive_backup.py backup >> backup.log 2>&1
$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "`"$ScriptPath`" backup >> `"$LogFile`" 2>&1" `
    -WorkingDirectory $WorkingDir

# Trigger: every Monday at 09:00
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday `
    -At "09:00AM"

# Run as current user, only when logged on
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

# Settings
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# Register the task
Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $Action `
    -Trigger   $Trigger `
    -Principal $Principal `
    -Settings  $Settings `
    -Description "Weekly Google Drive backup for Women's Health Intelligence Engine" `
    -Force | Out-Null

Write-Host ""
Write-Host "Task created successfully: $TaskName" -ForegroundColor Green
Write-Host "Schedule: Every Monday at 9:00 AM"
Write-Host "Script:   $ScriptPath"
Write-Host "Log:      $LogFile"
Write-Host ""
Write-Host "To view or edit: open Task Scheduler > Task Scheduler Library > $TaskName"
Write-Host "To run now:      python gdrive_backup.py backup"
Write-Host ""

# Offer to run a backup right now
$run = Read-Host "Run a backup right now to test? (y/n)"
if ($run -eq "y") {
    Write-Host "Running backup..."
    Set-Location $WorkingDir
    & $PythonPath $ScriptPath backup
}
