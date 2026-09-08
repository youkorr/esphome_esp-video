# One line on a Windows PC, and the panel becomes a screen.
#
#   irm https://raw.githubusercontent.com/youkorr/esphome_esp-video/main/components/wired_portall/windows/setup.ps1 | iex
#
# It installs nothing on the board. The board is already finished: it listens
# on its port and announces itself over mDNS from its ESPHome configuration.
# What this does is the Windows half -- the dependencies, the sender, and the
# login task -- so that none of it has to be typed.
#
# NOT TESTED ON WINDOWS by its author: there is no Windows where this was
# written. Every step is one that was run by hand successfully, and this is
# those steps in order; the joining together is what has not been proved.

$ErrorActionPreference = "Stop"
$Repo = "https://raw.githubusercontent.com/youkorr/esphome_esp-video/main"
$Sender = "$Repo/components/wired_portall/udisp_send.py"
$Home_ = Join-Path $env:LOCALAPPDATA "esphome-udisp"
$Target = Join-Path $Home_ "udisp_send.py"

function Say($text) { Write-Host "  $text" }

Write-Host ""
Write-Host "Portall -- setting this PC up to send a screen to a panel"
Write-Host ""

# --- Python -------------------------------------------------------------
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "Python is not installed. Install it from the Microsoft Store"
    Write-Host "or python.org, tick 'Add to PATH', and run this again."
    return
}
Say "Python: $((& python --version) 2>&1)"

# --- what it needs ------------------------------------------------------
Say "Installing mss, pillow, numpy and zeroconf ..."
& python -m pip install --quiet --upgrade mss pillow numpy zeroconf
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip could not install the dependencies. Nothing has changed."
    return
}

# --- the sender ---------------------------------------------------------
# Straight into the directory the login task runs from, so there is no second
# copy to fall out of step with the first.
New-Item -ItemType Directory -Force -Path $Home_ | Out-Null
Say "Fetching the sender ..."
# A cache-busting parameter: GitHub's raw view is behind one, and it has
# served an hour-old copy of this file before -- byte for byte.
$fresh = "$Sender" + "?t=" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
Invoke-WebRequest -Uri $fresh -OutFile $Target -UseBasicParsing
Say "Sender:  $Target"
Say (& python $Target --version)

# --- is there a panel to talk to? ---------------------------------------
Write-Host ""
Say "Looking for a panel on the network ..."
& python $Target --list-monitors

# --- the login task -----------------------------------------------------
Write-Host ""
Say "Installing the login task ..."
& python $Target --install-startup

# --- and start it now ---------------------------------------------------
$vbs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\esphome_udisp_send.vbs"
if (Test-Path $vbs) {
    Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Process wscript.exe -ArgumentList "`"$vbs`""
    Write-Host ""
    Write-Host "Running. The panel should be showing this screen within a second."
    Write-Host "It starts by itself at every login from now on -- no console, nothing to type."
    Write-Host ""
    Write-Host "  log:       $(Join-Path $Home_ 'udisp_send.log')"
    Write-Host "  to stop:   python `"$Target`" --uninstall-startup"
}
