@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Bebrid Magic Windows 1.0.2 - Construction

echo.
echo ============================================================
echo   BEBRID MAGIC WINDOWS 1.0.2
echo   Construction de BebridMagic.exe
echo ============================================================
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo Python n'est pas installe.
    where winget >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ERREUR : Python et winget sont introuvables.
        echo Installe Python 3.12 depuis python.org puis relance ce fichier.
        pause
        exit /b 1
    )
    echo Installation automatique de Python 3.12...
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :error
)

echo [1/4] Environnement Python...
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv 2>nul
    if errorlevel 1 py -3 -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto :error

echo [2/4] Installation des composants...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [3/4] Nettoyage...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [4/4] Construction de l'EXE...
".venv\Scripts\python.exe" -m PyInstaller ^
  --clean ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name BebridMagic ^
  app.py
if errorlevel 1 goto :error

if not exist "dist\BebridMagic.exe" goto :error

echo.
echo ============================================================
echo   TERMINE
echo ============================================================
echo.
echo EXE :
echo   %CD%\dist\BebridMagic.exe
echo.
explorer.exe /select,"%CD%\dist\BebridMagic.exe"
pause
exit /b 0

:error
echo.
echo ============================================================
echo   ECHEC
echo ============================================================
echo La fenetre reste ouverte pour lire l'erreur.
echo.
pause
exit /b 1
