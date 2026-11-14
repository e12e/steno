# Universal zero-Python installer for Steno (Windows 10 / 11 on AMD64 & ARM64 PowerShell)
# Steno - a modern annalist for your meetings - an automaton stenographer.
#
# Usage (PowerShell):
#   irm https://hypertekst.net/steno/install.ps1 | iex

$ErrorActionPreference = "Stop"

$DefaultStenoUrl = "https://github.com/e12e/steno/releases/latest/download/steno.py"
$DefaultSha256Url = "https://github.com/e12e/steno/releases/latest/download/SHA256SUMS.txt"
$StenoUrl = if ($env:STENO_URL) { $env:STENO_URL } else { $DefaultStenoUrl }
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
    if ($env:STENO_VERIFY_SHA256 -ne "0" -and ($StenoUrl -eq $DefaultStenoUrl -or $env:STENO_SHA256_URL)) {
        $SumsUrl = if ($env:STENO_SHA256_URL) { $env:STENO_SHA256_URL } else { $DefaultSha256Url }
        $SumsText = [string](Invoke-RestMethod -Uri $SumsUrl)
        $ExpectedMatch = [regex]::Match($SumsText, '(?mi)^([0-9a-f]{64})\s+\*?steno\.py\s*$')
        if (-not $ExpectedMatch.Success) {
            Remove-Item -Force $TargetScript -ErrorAction SilentlyContinue
            throw "Could not find steno.py checksum in $SumsUrl"
        }
        $ExpectedHash = $ExpectedMatch.Groups[1].Value.ToLowerInvariant()
        $ActualHash = (Get-FileHash -Algorithm SHA256 -Path $TargetScript).Hash.ToLowerInvariant()
        if ($ActualHash -ne $ExpectedHash) {
            Remove-Item -Force $TargetScript -ErrorAction SilentlyContinue
            throw "SHA-256 checksum mismatch for steno.py (expected $ExpectedHash, got $ActualHash)"
        }
        Write-Host "==> Verified SHA-256 checksum for steno.py ($ActualHash)"
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> Installing standalone uv runtime manager (no pre-installed Python required)..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}

$LauncherCmd = Join-Path $BinDir "steno.cmd"
$CmdContent = @"
@echo off
set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\uv\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
where uv >nul 2>nul || (
  echo Installing uv ^(standalone Python runtime^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
uv run --python 3.12 --script "$TargetScript" %*
"@
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($LauncherCmd, $CmdContent, $Utf8NoBom)

Write-Host "==> Installed Steno launcher to: $LauncherCmd"
Write-Host "    Run GUI:  $LauncherCmd"
Write-Host "    Run CLI:  $LauncherCmd --ui-lang en --help"
