<#
.SYNOPSIS
  Copy a built DeadByMapper folder to %LOCALAPPDATA%\Programs\DeadByMapper and create Desktop +
  Start Menu shortcuts. Installing a COPY means rebuilding never breaks the running app or the
  shortcut. Per-user, no admin rights needed.
#>
param(
    [string]$Source = (Join-Path (Split-Path $PSScriptRoot -Parent) "dist\DeadByMapper"),
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\DeadByMapper")
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path (Join-Path $Source "DeadByMapper.exe"))) { throw "No build at $Source - run packaging\build.ps1 first" }

$running = Get-Process -Name "DeadByMapper" -ErrorAction SilentlyContinue
if ($running) { throw "DeadByMapper is running - close it (tray/pane) and run install again." }

Write-Host "==> installing to $InstallDir" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
robocopy $Source $InstallDir /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

$Exe = Join-Path $InstallDir "DeadByMapper.exe"
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) ""))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "DeadByMapper.lnk"))
    $lnk.TargetPath = $Exe
    $lnk.WorkingDirectory = $InstallDir
    $lnk.IconLocation = "$Exe,0"
    $lnk.Description = "DeadByMapper - glanceable minimap companion for Dead by Daylight"
    $lnk.Save()
    Write-Host "    shortcut: $(Join-Path $dir 'DeadByMapper.lnk')"
}
Write-Host "Installed. Double-click DeadByMapper on your Desktop." -ForegroundColor Green
