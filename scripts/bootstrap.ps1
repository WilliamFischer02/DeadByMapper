<#
.SYNOPSIS
  One-time developer setup on Windows: uv, Python 3.12, dependencies, generated data, tests.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> installing uv" -ForegroundColor Cyan
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "==> python + dependencies" -ForegroundColor Cyan
uv python install 3.12
uv sync --extra windows --group dev
if ($LASTEXITCODE -ne 0) { throw "uv sync failed - check the windows extra pins in pyproject.toml" }

Write-Host "==> generated data" -ForegroundColor Cyan
uv run python tools/gen_map_stubs.py
uv run python tools/gen_schemas.py
uv run python tools/validate_maps.py

Write-Host "==> tests" -ForegroundColor Cyan
uv run pytest -q -m "not needs_game"

Write-Host ""
Write-Host "Ready. See it work with no game running:" -ForegroundColor Green
Write-Host "    uv run python -m deadbymapper --demo"
