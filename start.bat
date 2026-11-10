@echo off
REM ============================================================
REM  COVER AUDIO TOOL - START (double-click de chay)
REM  Chay server tren cong 8200 va tu mo trinh duyet.
REM  (Khac cong 8100 cua tool Cover nhac day du -> chay song song duoc)
REM ============================================================
setlocal
cd /d "%~dp0"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua cai dat. Hay chay install.bat truoc.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   COVER AUDIO TOOL dang khoi dong...
echo   Trinh duyet se tu mo tai: http://localhost:8200
echo   (De DONG ung dung: dong cua so nay hoac nhan Ctrl+C)
echo ============================================================
echo.

start "" cmd /c "timeout /t 4 >nul & start "" http://localhost:8200"

.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8200

endlocal
