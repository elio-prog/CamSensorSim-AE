@echo off
rem CamSensorSim-AE: installs the two After Effects plugins (asks for administrator rights).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-CameraSim.ps1"
