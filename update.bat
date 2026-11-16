@echo off
REM ============================================================
REM  COVER AUDIO TOOL - CAP NHAT (patch) - KHONG can xoa ban cu
REM  Tai ban moi nhat tu GitHub, chep de len thu muc tool.
REM  GIU NGUYEN: .venv, data (setting + app.db), media, outputs,
REM  stems, Projects, Trash, .env, dang nhap Suno/nhaccuatui.
REM  Bam dup file nay (dat trong thu muc tool, canh start.bat).
REM ============================================================
setlocal
REM Tu chep chinh minh ra thu muc tam roi chay ban sao, vi ban cap nhat
REM se ghi de len update.bat trong luc dang chay.
if /i not "%~1"=="--run" (
    copy /y "%~f0" "%TEMP%\masteraudio_update.bat" >nul
    "%TEMP%\masteraudio_update.bat" --run "%~dp0."
)
cd /d "%~2"

echo.
echo ============================================================
echo   COVER AUDIO TOOL - Dang cap nhat...
echo   Thu muc tool: %CD%
echo ============================================================
echo.

if not exist "backend\main.py" (
    echo [LOI] File update.bat phai nam trong thu muc tool ^(canh start.bat^).
    goto :fail
)
if not exist ".venv\Scripts\python.exe" (
    echo [LOI] May chua cai tool. Hay chay install.bat truoc.
    goto :fail
)

REM --- Tool dang chay thi khong cap nhat ---
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8200 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
    echo [DUNG] Tool dang chay ^(cong 8200^).
    echo   Doi cac viec dang chay xong, DONG cua so start.bat, roi bam lai update.bat.
    goto :fail
)

set "WORK=%TEMP%\masteraudio_update"
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%"

echo [1/3] Tai ban moi nhat tu GitHub...
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/lucasstudio1228/MasterAudio/archive/refs/heads/main.zip' -OutFile '%WORK%\main.zip'"
if errorlevel 1 (
    echo [LOI] Khong tai duoc. Kiem tra mang Internet roi thu lai.
    goto :fail
)

echo [2/3] Giai nen...
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Expand-Archive -LiteralPath '%WORK%\main.zip' -DestinationPath '%WORK%\x' -Force"
if not exist "%WORK%\x\MasterAudio-main\patch\apply_update.ps1" (
    echo [LOI] File tai ve khong dung dinh dang.
    goto :fail
)

echo [3/3] Chep ban moi vao thu muc tool...
powershell -NoProfile -ExecutionPolicy Bypass -File "%WORK%\x\MasterAudio-main\patch\apply_update.ps1" -Src "%WORK%\x\MasterAudio-main" -Dst "%CD%"
if errorlevel 1 (
    echo [LOI] Cap nhat chua xong. Chup man hinh nay gui nguoi ho tro.
    goto :fail
)

rmdir /s /q "%WORK%" >nul 2>&1
echo.
echo ============================================================
echo   CAP NHAT XONG! Bam dup "Cover Music by Lucas Studio" ngoai Desktop
echo   (hoac CoverMusic.exe / start.bat) de mo lai tool.
echo ============================================================
echo.
pause
exit /b

:fail
echo.
pause
exit /b
