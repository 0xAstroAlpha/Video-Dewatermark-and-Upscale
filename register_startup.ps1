# register_startup.ps1 - Registers the API server as a Windows Task Scheduler job
# Run this ONCE as Administrator to set up auto-start on login

$TaskName = "VideoAPI_Dewatermark"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ScriptPath = Join-Path $ProjectDir "start_api.ps1"

# Remove old task if exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Create the scheduled task action
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Minimized -File `"$ScriptPath`"" `
    -WorkingDirectory $ProjectDir

# Trigger: run at logon of current user
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Settings: restart on failure (up to 3 times, 1 min apart), run indefinitely
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

# Register the task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Auto-starts Video Dewatermark API on port 8288 at user login" `
    -RunLevel Highest `
    -Force

Write-Host ""
Write-Host "SUCCESS: Task '$TaskName' registered." -ForegroundColor Green
Write-Host "The API will auto-start on next login at http://localhost:8288/" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start it NOW without rebooting, run:" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
