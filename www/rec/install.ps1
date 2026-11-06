# Universal zero-Python installer for Steno (Windows 10 / 11 PowerShell)
# Steno - a modern annalist for your meetings - an automaton stenographer.
#
# Usage (PowerShell):
#   irm https://hypertekst.net/steno/install.ps1 | iex

$ErrorActionPreference = "Stop"

$StenoUrl = if ($env:STENO_URL) { $env:STENO_URL } else { "https://hypertekst.net/steno/steno.py" }
$InstallDir = if ($env:STENO_INSTALL_DIR) { $env:STENO_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Steno" }
$BinDir = if ($env:STENO_BIN_DIR) { $env:STENO_BIN_DIR } else { Join-Path $env:USERPROFILE ".local\bin" }

Write-Host "==> Installing Steno — a modern annalist for your meetings — an automaton stenographer"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

$TargetScript = Join-Path $InstallDir "steno.py"
$LocalCandidate = if ($PSScriptRoot) { Join-Path $PSScriptRoot "steno.py" } else { "" }

if ($LocalCandidate -and (Test-Path $LocalCandidate)) {
    Copy-Item -Force $LocalCandidate $TargetScript
} else {
    Write-Host "==> Downloading steno.py from $StenoUrl ..."
    Invoke-RestMethod -Uri $StenoUrl -OutFile $TargetScript
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> Installing standalone uv runtime manager (no pre-installed Python required)..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}

$LauncherCmd = Join-Path $BinDir "steno.cmd"
$CmdContent = @"
@echo off
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
where uv >nul 2>nul || (
  echo Installing uv ^(standalone Python runtime^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
uv run --python 3.12 --script "$TargetScript" %*
"@
Set-Content -Path $LauncherCmd -Value $CmdContent -Encoding UTF8

Write-Host "==> Installed Steno launcher to: $LauncherCmd"
Write-Host "    Run GUI:  $LauncherCmd"
Write-Host "    Run CLI:  $LauncherCmd --ui-lang en --help"
