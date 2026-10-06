@echo off
rem UNC scripts are "remote" to the execution policy; bypass for this one file only
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
