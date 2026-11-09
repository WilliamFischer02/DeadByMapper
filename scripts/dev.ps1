<#
.SYNOPSIS
  Run DeadByMapper from source. Examples:
    scripts\dev.ps1 --demo
    scripts\dev.ps1 --record              # live, recording the session for offline dev
    scripts\dev.ps1 --replay $env:LOCALAPPDATA\DeadByMapper\recordings\2026-11-08_013000
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
uv run python -m deadbymapper @args
