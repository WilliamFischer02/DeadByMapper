<#
.SYNOPSIS
  Visual QA: render the demo UI offscreen at 100% / 150% / 200% scale into qa\screens\<scale>\.
  Review these PNGs after any UI change (docs/UI_DESIGN.md#crispness-checklist).
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
foreach ($scale in @("1", "1.5", "2")) {
    $env:QT_SCALE_FACTOR = $scale
    uv run python -m deadbymapper --demo --screenshot "qa/screens/$scale" --screenshot-delay 45
}
Remove-Item Env:\QT_SCALE_FACTOR
Write-Host "Screens in qa\screens\" -ForegroundColor Green
