@echo off
REM Tao file .exe mo tool trong cua so ung dung (khong can cai them gi:
REM dung trinh bien dich C# co san trong Windows).
REM   build_exe.bat              -> ten exe theo thu muc tool
REM   build_exe.bat TenFile      -> TenFile.exe
setlocal
cd /d "%~dp0.."
set "NAME=%~1"
if "%NAME%"=="" for %%I in ("%CD%") do set "NAME=%%~nxI"
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
"%CSC%" /nologo /target:winexe /optimize+ /codepage:65001 /win32icon:launcher_exe\app.ico /reference:System.Windows.Forms.dll /out:"%NAME%.exe" launcher_exe\Launcher.cs
if errorlevel 1 ( echo [LOI] Build that bai. & exit /b 1 )
echo Da tao: %CD%\%NAME%.exe
