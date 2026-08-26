@echo off
REM Packages the MNT dashboard as dist\MNT\MNT.exe.
REM
REM One folder, not one file. A --onefile build unpacks the whole scientific
REM stack to a temp directory on every launch, and this app launches itself
REM again for training jobs - so one-file would pay that unpacking cost
REM repeatedly, on top of a slower start every time.
REM
REM Data lives NEXT TO the exe, never inside the bundle: config.py points
REM at the executable's folder when frozen. The bundle is replaced wholesale on
REM every rebuild, so a model saved inside it would be destroyed by the next
REM build.
REM
REM torch and tabpfn are excluded deliberately. They lost the bake-off recorded
REM in config.py - lightgbm +46bp against MLP +23bp and TabPFN -8bp - and
REM PRODUCTION_SIGNAL is lightgbm. Bundling torch would add over a gigabyte
REM to ship two models the project measured and rejected.

cd /d "%~dp0"

echo Building MNT.exe with PyInstaller...
py -3.13 -m PyInstaller --noconfirm --clean MNT.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED.
    exit /b 1
)

REM The spec file already handles collection of lightgbm, sklearn, yfinance,
REM curl_cffi, and excludes torch, tabpfn, matplotlib, etc.

echo.
echo Done: %~dp0dist\MNT\MNT.exe
echo.
echo The executable is in dist\MNT\ - move the whole folder to move the program.
echo The exe needs artifacts\ and data_cache\ beside it for models and data.
