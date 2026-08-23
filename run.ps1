<#
.SYNOPSIS
    Set up the environment and launch the Signature Mouse Signer (Windows).

.DESCRIPTION
    Creates a .venv on first run, installs requirements.txt into it, then
    starts the app. Re-runs reuse the existing venv and only reinstall when
    requirements.txt has changed since the last install.

.PARAMETER Recreate
    Delete and rebuild the virtual environment from scratch.

.PARAMETER SkipInstall
    Launch immediately without checking or installing dependencies.

.EXAMPLE
    .\run.ps1
    .\run.ps1 -Recreate
#>

[CmdletBinding()]
param(
    [switch]$Recreate,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $Root '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Requirements = Join-Path $Root 'requirements.txt'
$Stamp = Join-Path $VenvDir '.requirements.sha256'

function Find-Python
{
    # 'py -3' is the most reliable launcher on Windows; fall back to python3/python.
    if (Get-Command py -ErrorAction SilentlyContinue)
    {
        $v = & py -3 -c 'import sys; print(sys.version_info[:2])' 2>$null
        if ($?) { return @{ Exe = 'py'; Args = @('-3') } }
    }
    foreach ($name in @('python3', 'python'))
    {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        # Skip the Microsoft Store alias stub, which exits without running.
        if ($cmd -and $cmd.Source -notlike '*WindowsApps*')
        {
            return @{ Exe = $cmd.Source; Args = @() }
        }
        if ($cmd)
        {
            $out = & $cmd.Source '-c' 'print(1)' 2>$null
            if ($? -and $out -eq '1') { return @{ Exe = $cmd.Source; Args = @() } }
        }
    }
    throw "No usable Python 3 found on PATH. Install Python 3.9+ from https://python.org and re-run."
}

if ($Recreate -and (Test-Path $VenvDir))
{
    Write-Host '==> Removing existing .venv' -ForegroundColor Yellow
    Remove-Item -Recurse -Force $VenvDir
}

if (-not (Test-Path $VenvPython))
{
    $py = Find-Python
    Write-Host "==> Creating virtual environment in .venv" -ForegroundColor Cyan
    & $py.Exe @($py.Args + @('-m', 'venv', $VenvDir))
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtual environment." }
}

if (-not $SkipInstall)
{
    $hash = (Get-FileHash $Requirements -Algorithm SHA256).Hash
    $previous = if (Test-Path $Stamp) { (Get-Content $Stamp -Raw).Trim() } else { '' }

    if ($hash -ne $previous)
    {
        Write-Host '==> Installing dependencies' -ForegroundColor Cyan
        # No pip self-upgrade here: on Windows, replacing pip while it runs
        # leaves a broken '~ip' directory in site-packages.
        & $VenvPython -m pip install --disable-pip-version-check -r $Requirements
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
        Set-Content -Path $Stamp -Value $hash -Encoding utf8
    }
    else
    {
        Write-Host '==> Dependencies up to date' -ForegroundColor DarkGray
    }
}

Write-Host '==> Starting Signature Mouse Signer' -ForegroundColor Green

# Anchor imports on the repo root so the script works from any working directory.
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root;$env:PYTHONPATH" } else { $Root }

& $VenvPython -m app @args
exit $LASTEXITCODE
