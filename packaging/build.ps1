<#
.SYNOPSIS
  Build DeadByMapper.exe (PyInstaller one-folder) and, unless -NoInstall, install it to
  %LOCALAPPDATA%\Programs\DeadByMapper with a Desktop shortcut.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#>
param(
    [switch]$Clean,
    [switch]$NoInstall,
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "==> syncing environment" -ForegroundColor Cyan
uv sync --extra windows --group dev
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

Write-Host "==> generating schemas + icon" -ForegroundColor Cyan
uv run python tools/gen_schemas.py
if ($LASTEXITCODE -ne 0) { throw "schema generation failed" }
uv run python tools/make_icon.py
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

if (-not $SkipTests) {
    Write-Host "==> tests" -ForegroundColor Cyan
    uv run pytest -q -m "not needs_game"
    if ($LASTEXITCODE -ne 0) { throw "tests failed - not building" }
}

Write-Host "==> pyinstaller" -ForegroundColor Cyan
$piArgs = @("packaging/deadbymapper.spec", "--noconfirm")
if ($Clean) { $piArgs += "--clean" }
uv run pyinstaller @piArgs
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

$Exe = Join-Path $Root "dist\DeadByMapper\DeadByMapper.exe"
if (-not (Test-Path $Exe)) { throw "build output missing: $Exe" }

Write-Host "==> smoke test (headless demo, 5 s)" -ForegroundColor Cyan
$p = Start-Process -FilePath $Exe -ArgumentList "--demo", "--headless", "5" -PassThru -Wait -NoNewWindow
if ($p.ExitCode -ne 0) { throw "frozen app smoke test failed (exit $($p.ExitCode))" }

if (-not $NoInstall) {
    & (Join-Path $PSScriptRoot "install.ps1") -Source (Join-Path $Root "dist\DeadByMapper")
}
Write-Host "Done: $Exe" -ForegroundColor Green
