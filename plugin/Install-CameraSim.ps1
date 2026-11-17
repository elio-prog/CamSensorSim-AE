# CamSensorSim-AE installer: copies CameraSim.aex and CameraSimAnalog.aex into every After Effects found on this PC.
#   Install-CameraSim.ps1            install
#   Install-CameraSim.ps1 -Uninstall remove
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$plugins = @("CameraSim.aex", "CameraSimAnalog.aex")

# Administrator rights are needed to write into C:\Program Files: relaunch elevated if necessary.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$($MyInvocation.MyCommand.Path)`"")
    if ($Uninstall) { $argList += "-Uninstall" }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

function Pause-End($msg) {
    Write-Host ""
    Write-Host $msg
    Read-Host "Press Enter to close"
    exit
}

if (Get-Process -Name "AfterFX" -ErrorAction SilentlyContinue) {
    Pause-End "After Effects is running. Close it, then run the installer again."
}

# Every After Effects installation (standard and beta), on every drive that has a Program Files folder.
$roots = @("$env:ProgramFiles\Adobe")
$found = @()
foreach ($r in $roots) {
    if (Test-Path $r) {
        $found += Get-ChildItem $r -Directory -Filter "Adobe After Effects*" |
            ForEach-Object { Join-Path $_.FullName "Support Files\Plug-ins" } |
            Where-Object { Test-Path $_ }
    }
}
if ($found.Count -eq 0) {
    Pause-End "No After Effects installation found in $env:ProgramFiles\Adobe. Copy the .aex files manually into ...\Support Files\Plug-ins."
}

foreach ($dir in $found) {
    $dest = Join-Path $dir "CameraSim"
    if ($Uninstall) {
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force; Write-Host "Removed: $dest" }
        foreach ($p in $plugins) {                         # copies made by hand directly in Plug-ins
            $old = Join-Path $dir $p
            if (Test-Path $old) { Remove-Item $old -Force; Write-Host "Removed: $old" }
        }
        continue
    }
    New-Item -ItemType Directory -Force $dest | Out-Null
    foreach ($p in $plugins) {
        $src = Join-Path $here $p
        if (-not (Test-Path $src)) { Pause-End "Missing file: $src" }
        Unblock-File $src -ErrorAction SilentlyContinue   # files from a downloaded ZIP are marked as unsafe
        Copy-Item $src $dest -Force
        # Remove an older copy placed by hand directly in Plug-ins (After Effects would load it twice).
        $old = Join-Path $dir $p
        if (Test-Path $old) { Remove-Item $old -Force }
    }
    Write-Host "Installed in: $dest"
}

if ($Uninstall) { Pause-End "CameraSim removed." }
Pause-End "Done. Start After Effects: the effects are under Effect > CameraSim."
