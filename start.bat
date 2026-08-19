@echo off
title AHMU Simulator
cd /d "%~dp0"

REM Prefer the project venv if it already exists
if exist "backend\venv\Scripts\python.exe" goto :run_venv

REM Fallback 1: python on PATH
where python >nul 2>nul
if %errorlevel% equ 0 goto :run_path

REM Fallback 2: managed Python 3.13
if exist "C:\Users\huzhe\.workbuddy\binaries\python\versions\3.13.12\python.exe" goto :run_managed

REM Fallback 3: system Python 3.8
if exist "C:\Users\huzhe\AppData\Local\Programs\Python\Python38\python.exe" goto :run_sys38

echo [ERROR] Python not found.
echo Please install Python and add it to PATH.
pause
exit /b 1

:run_venv
echo [INFO] Using project venv Python...
"backend\venv\Scripts\python.exe" start.py
goto :end

:run_path
echo [INFO] Using Python from PATH...
python start.py
goto :end

:run_managed
echo [INFO] Using managed Python 3.13...
"C:\Users\huzhe\.workbuddy\binaries\python\versions\3.13.12\python.exe" start.py
goto :end

:run_sys38
echo [INFO] Using system Python 3.8...
"C:\Users\huzhe\AppData\Local\Programs\Python\Python38\python.exe" start.py
goto :end

:end
pause
