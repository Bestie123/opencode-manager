# OpenCode Session Manager - PowerShell launcher
# Usage: Right-click -> Run with PowerShell, or: .\start.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find Python
$python = $null

# Try py launcher first
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
    $args = @("-3") + @($args)
}

# Try python in PATH
if (-not $python -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $version = & python --version 2>&1
    if ($version -match "Python 3") {
        $python = "python"
    }
}

if (-not $python) {
    Write-Host "Python 3.10+ not found!" -ForegroundColor Red
    Write-Host "Install: winget install Python.Python.3.13"
    exit 1
}

Write-Host "Using: $python" -ForegroundColor Green
& $python @("-3", "$scriptDir\run.py") @args
