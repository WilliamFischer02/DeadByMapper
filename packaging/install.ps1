<#
.SYNOPSIS
  Copy a built DeadByMapper folder to %LOCALAPPDATA%\Programs\DeadByMapper and create Desktop +
  Start Menu shortcuts. Installing a COPY means rebuilding never breaks the running app or the
  shortcut. Per-user, no admin rights needed.
#>
param(
    [string]$Source = (Join-Path (Split-Path $PSScriptRoot -Parent) "dist\DeadByMapper"),
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\DeadByMapper"),
    [switch]$NoWarmup
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path (Join-Path $Source "DeadByMapper.exe"))) { throw "No build at $Source - run packaging\build.ps1 first" }

# Only a copy running FROM the install dir locks the files we are about to replace; a copy running
# from dist\ (e.g. a smoke test) does not matter.
$installedExe = [IO.Path]::GetFullPath((Join-Path $InstallDir "DeadByMapper.exe"))
$running = Get-Process -Name "DeadByMapper" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and ([IO.Path]::GetFullPath($_.Path) -ieq $installedExe) }
if ($running) { throw "DeadByMapper is running (pid $($running.Id -join ', ')) - close it and run install again." }

Write-Host "==> installing to $InstallDir" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
robocopy $Source $InstallDir /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

$Exe = Join-Path $InstallDir "DeadByMapper.exe"
$shell = New-Object -ComObject WScript.Shell
# GetFolderPath follows folder redirection (e.g. OneDrive\Desktop), unlike %USERPROFILE%\Desktop.
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "DeadByMapper.lnk"))
    $lnk.TargetPath = $Exe
    $lnk.WorkingDirectory = $InstallDir
    $lnk.IconLocation = "$Exe,0"
    $lnk.Description = "DeadByMapper - glanceable minimap companion for Dead by Daylight"
    $lnk.Save()
    Write-Host "    shortcut: $(Join-Path $dir 'DeadByMapper.lnk')"
}
if (-not $NoWarmup) {
    # Antivirus scans every freshly written DLL on first load, which made the first double-click take
    # ~6 s (later launches ~0.8 s). Pay that cost here: render the demo UI once offscreen and exit.
    # Demo mode only - it never looks at the game.
    Write-Host "==> warm-up launch (offscreen demo)" -ForegroundColor Cyan
    $warm = Join-Path $env:TEMP "DeadByMapper-warmup"
    $p = Start-Process -FilePath $Exe -ArgumentList "--demo", "--screenshot", "`"$warm`"", "--screenshot-delay", "0.5" `
        -PassThru -NoNewWindow -RedirectStandardOutput "$warm.out.txt" -RedirectStandardError "$warm.err.txt"
    $null = $p.Handle
    if (-not $p.WaitForExit(90000)) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Write-Warning "warm-up timed out (not fatal); see $warm.err.txt"
    } elseif ($p.ExitCode -ne 0) {
        Write-Warning "warm-up exited with $($p.ExitCode) (not fatal); see $warm.err.txt"
    } else {
        Remove-Item -Recurse -Force $warm, "$warm.out.txt", "$warm.err.txt" -ErrorAction SilentlyContinue
    }
}
Write-Host "Installed. Double-click DeadByMapper on your Desktop." -ForegroundColor Green
$global:LASTEXITCODE = 0  # robocopy leaves 1..7 (= success) behind; don't leak it to callers
