@echo off
REM Complete build pipeline: exe + installer

cd /d "%~dp0"

echo ========================================
echo MNT Complete Build Pipeline
echo ========================================
echo.

REM Step 1: Build the executable
echo [1/2] Building MNT.exe...
echo.
call build_exe.cmd
if errorlevel 1 (
    echo.
    echo EXE BUILD FAILED. Aborting.
    exit /b 1
)

echo.
echo ========================================
echo [2/2] Creating installer...
echo.

REM Step 2: Check if Inno Setup is installed
set INNO_PATH=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set INNO_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set INNO_PATH=C:\Program Files\Inno Setup 6\ISCC.exe
) else if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set INNO_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
) else if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set INNO_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe
) else if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" (
    set INNO_PATH=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
)

if "%INNO_PATH%"=="" (
    echo.
    echo WARNING: Inno Setup 6 not found.
    echo Download from: https://jrsoftware.org/isdl.php
    echo.
    echo The executable is ready at: dist\MNT\MNT.exe
    echo To build the installer manually, install Inno Setup and run:
    echo   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
    echo.
    exit /b 0
)

REM Build the installer
echo Found Inno Setup at: %INNO_PATH%
echo Compiling installer.iss...
echo.
"%INNO_PATH%" installer.iss
if errorlevel 1 (
    echo.
    echo INSTALLER BUILD FAILED.
    exit /b 1
)

echo.
echo ========================================
echo BUILD COMPLETE
echo ========================================
echo.
echo Executable: %~dp0dist\MNT\MNT.exe
if exist "installer_output\MNT_Setup_1.0.0.exe" (
    echo Installer:  %~dp0installer_output\MNT_Setup_1.0.0.exe
    echo.
    echo The installer is ready to distribute.
) else (
    echo Installer:  Check installer_output\ folder
)
echo.
