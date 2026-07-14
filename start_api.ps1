# start_api.ps1 - Production startup script for Video Dewatermark & Upscale API
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ProjectDir

Write-Host "[Startup] Working directory: $ProjectDir" -ForegroundColor Cyan

# 1. Kill any process currently occupying port 8288
Write-Host "[Startup] Checking for existing process on port 8288..." -ForegroundColor Yellow
$portCheck = netstat -ano | findstr ":8288 " | findstr "LISTENING"
if ($portCheck) {
    $pid = ($portCheck -split '\s+')[-1]
    Write-Host "[Startup] Killing PID $pid on port 8288..." -ForegroundColor Red
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# 2. Clean up leftover temp files from crashed sessions (older than 1 hour)
Write-Host "[Startup] Cleaning up old temp uploads..." -ForegroundColor Yellow
$tempDir = Join-Path $ProjectDir "temp_uploads"
if (Test-Path $tempDir) {
    $cutoff = (Get-Date).AddHours(-1)
    Get-ChildItem $tempDir | Where-Object { $_.LastWriteTime -lt $cutoff } | ForEach-Object {
        Write-Host "  Removing stale: $($_.Name)"
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

# 3. Clean up orphaned ProPainter results directories
Write-Host "[Startup] Cleaning up orphaned ProPainter results..." -ForegroundColor Yellow
$propainterDir = Join-Path $ProjectDir "ProPainter"
if (Test-Path $propainterDir) {
    Get-ChildItem $propainterDir -Directory | Where-Object { $_.Name -like "results_run_*" } | ForEach-Object {
        Write-Host "  Removing: $($_.Name)"
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# 4. Start the API server
Write-Host "[Startup] Starting API server on port 8288..." -ForegroundColor Green
python -m uvicorn app_api:app --host 0.0.0.0 --port 8288 --log-level info
