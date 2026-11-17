@echo off
rem CamSensorSim-AE: removes the two After Effects plugins (asks for administrator rights).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-CameraSim.ps1" -Uninstall
