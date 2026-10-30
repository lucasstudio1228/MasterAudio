@echo off
setlocal
cd /d "%~dp0"
title Tempo 4.5x ^& Master Audio

set "PYTHON_EXE=python"
if exist "..\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=..\.venv\Scripts\python.exe"
  goto python_ok
)

where python >nul 2>&1
if errorlevel 1 (
  echo Khong tim thay Python. Hay cai Python 3.10+ va bat Add Python to PATH.
  echo Xem huong dan trong README.md.
  pause
  exit /b 1
)

:python_ok
"%PYTHON_EXE%" app.py
if errorlevel 1 (
  echo.
  echo Tool gap loi. Nhan phim bat ky de dong.
  pause >nul
)
endlocal
