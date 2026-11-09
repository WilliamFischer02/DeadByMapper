<#
.SYNOPSIS
  Visual QA: render the demo UI at 100% / 150% / 200% scale into qa\screens\<scale>\.
  Review these PNGs after any UI change (docs/UI_DESIGN.md#crispness-checklist).

.PARAMETER Platform
  "windows" (default): the real platform plugin - DirectWrite text, exactly what the user sees.
  The windows briefly appear on screen while rendering. "offscreen": headless (CI / no display);
  its FreeType font database falls back from Segoe UI Variable, so text looks condensed.

.PARAMETER Delay
  Seconds of demo to run before the grab (the simulated survivor needs ~20 s to lock).
#>
param(
    [ValidateSet("windows", "offscreen")][string]$Platform = "windows",
    [int]$Delay = 20,
    [string[]]$Scales = @("1", "1.5", "2")
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:QT_QPA_PLATFORM = $Platform
foreach ($scale in $Scales) {
    $env:QT_SCALE_FACTOR = $scale
    uv run python -m deadbymapper --demo --screenshot "qa/screens/$scale" --screenshot-delay $Delay
}
Remove-Item Env:\QT_SCALE_FACTOR
Remove-Item Env:\QT_QPA_PLATFORM
Write-Host "Screens in qa\screens\ (platform: $Platform)" -ForegroundColor Green
