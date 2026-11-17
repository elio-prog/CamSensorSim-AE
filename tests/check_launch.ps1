# Verifie la chaine de lancement utilisee par le panneau, sans After Effects :
# fichier de travail -> .bat -> .vbs -> moteur Python -> fichier d'etat -> resultat.
#   powershell -ExecutionPolicy Bypass -File tests\check_launch.ps1 "<un fichier EXR de la sequence>" <numero d'image>
param([Parameter(Mandatory = $true)][string]$SrcFile, [int]$Frame = 0)

$root = Split-Path -Parent $PSScriptRoot
$work = Join-Path $env:TEMP ("camsim_launch_" + (Get-Date -Format "HHmmss"))
New-Item -ItemType Directory -Force $work | Out-Null
$base = Join-Path $work "preview_test"
$job = @{ src_file = $SrcFile; mode = "preview"; profile = "sonya7s2"; status = "$base.status.txt"
          preview_png = "$base.png"; settings = @{ iso = 6400; row_noise_gain = 4 } }
if ($Frame -gt 0) { $job.first = $Frame }
[System.IO.File]::WriteAllText("$base.json", ($job | ConvertTo-Json -Compress))

$py = Join-Path $root ".venv\Scripts\python.exe"
$engine = Join-Path $root "engine\camsim\job.py"
$cmd = "`"$py`" -I `"$engine`" --job `"$base.json`" > `"$base.log.txt`" 2>&1"
[System.IO.File]::WriteAllText("$base.bat", "rem CameraSim`r`n@echo off`r`nchcp 65001 >nul`r`n$cmd`r`n")
$vbsLine = 'CreateObject("WScript.Shell").Run "cmd /c """"' + "$base.bat" + '""""", 7, False'
[System.IO.File]::WriteAllText("$base.vbs", $vbsLine + "`r`n", [System.Text.Encoding]::Default)

$t0 = Get-Date
& "$env:WINDIR\System32\wscript.exe" "$base.vbs"
$launch = ((Get-Date) - $t0).TotalSeconds
$state = ""
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Path "$base.status.txt") {
        $st = Get-Content "$base.status.txt" -Raw
        if ($st -match "state=(done|error)") { $state = $Matches[1]; break }
    }
}
$total = ((Get-Date) - $t0).TotalSeconds
"lancement rendu a l'appelant en {0:N2} s (non bloquant si proche de 0)" -f $launch
"etat final : $state apres {0:N1} s" -f $total
if (Test-Path "$base.status.txt") { Get-Content "$base.status.txt" }
if ($state -ne "done") { "--- journal ---"; if (Test-Path "$base.log.txt") { Get-Content "$base.log.txt" -Tail 15 } ; exit 1 }
$png = Get-Item "$base.png"
"planche : {0} ({1:N0} Ko)" -f $png.FullName, ($png.Length / 1KB)
exit 0
