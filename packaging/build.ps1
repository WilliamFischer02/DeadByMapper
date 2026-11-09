<#
.SYNOPSIS
  Build DeadByMapper.exe (PyInstaller one-folder), smoke-test it and, unless -NoInstall, install it to
  %LOCALAPPDATA%\Programs\DeadByMapper with Desktop + Start Menu shortcuts.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Clean -SkipTests -NoInstall
#>
param(
    [switch]$Clean,
    [switch]$NoInstall,
    [switch]$SkipTests,
    [int]$SmokeSeconds = 5,
    [int]$SmokeTimeoutSeconds = 90
)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$Watch = [Diagnostics.Stopwatch]::StartNew()

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

$Dist = Join-Path $Root "dist\DeadByMapper"
$Exe = Join-Path $Dist "DeadByMapper.exe"
if (-not (Test-Path $Exe)) { throw "build output missing: $Exe" }

# The exe is windowed (no console), so stdout/stderr are redirected to files and checked explicitly.
Write-Host "==> smoke test (--demo --headless $SmokeSeconds, timeout $SmokeTimeoutSeconds s)" -ForegroundColor Cyan
$SmokeDir = Join-Path $Root "packaging\build"
New-Item -ItemType Directory -Force -Path $SmokeDir | Out-Null
$SmokeOut = Join-Path $SmokeDir "smoke-stdout.txt"
$SmokeErr = Join-Path $SmokeDir "smoke-stderr.txt"
$p = Start-Process -FilePath $Exe -ArgumentList "--demo", "--headless", "$SmokeSeconds" -PassThru -NoNewWindow `
    -RedirectStandardOutput $SmokeOut -RedirectStandardError $SmokeErr
$null = $p.Handle  # cache the handle, otherwise ExitCode can read back as $null after exit
if (-not $p.WaitForExit($SmokeTimeoutSeconds * 1000)) {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    throw "smoke test timed out after $SmokeTimeoutSeconds s (stderr: $SmokeErr)"
}
$p.WaitForExit()
$stdout = (Get-Content $SmokeOut -Raw -ErrorAction SilentlyContinue) + ""
$stderr = (Get-Content $SmokeErr -Raw -ErrorAction SilentlyContinue) + ""
Write-Host ("    exit code {0}: {1}" -f $p.ExitCode, $stdout.Trim())
if ($p.ExitCode -ne 0 -or $stdout -notmatch "mode=demo" -or $stderr -cmatch "Traceback|\sCRITICAL\s|\sERROR\s") {
    Write-Host $stderr -ForegroundColor Yellow
    throw "frozen app smoke test failed (exit $($p.ExitCode)); output in $SmokeOut / $SmokeErr"
}

$files = Get-ChildItem $Dist -Recurse -File
$mb = [math]::Round(($files | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ("    dist: {0} MB in {1} files" -f $mb, $files.Count)

if (-not $NoInstall) {
    & (Join-Path $PSScriptRoot "install.ps1") -Source $Dist
}
Write-Host ("Done in {0:N0} s: {1}" -f $Watch.Elapsed.TotalSeconds, $Exe) -ForegroundColor Green
