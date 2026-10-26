@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

rem Main window. Already running? It just switches to the existing one.
python -m appproxy.cli gui %*
