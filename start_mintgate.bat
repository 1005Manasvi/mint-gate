@echo off
rem Double-click to start Mint-Gate locally: Docker (Postgres + Redis), a local Hardhat chain with the
rem contracts deployed, the indexer, both workers and the API server (which also serves the frontend),
rem then opens http://127.0.0.1:5000. Close the opened windows to stop everything.
title Mint-Gate launcher
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [error] venv not found in %CD%\venv - see README "Run locally".
    pause
    exit /b 1
)

rem 1. Docker Desktop must be running for Postgres + Redis.
docker info >nul 2>&1
if errorlevel 1 (
    echo Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    for /l %%i in (1,1,60) do (
        timeout /t 5 /nobreak >nul
        docker info >nul 2>&1 && goto docker_ready
    )
    echo [error] Docker Desktop did not start within 5 minutes.
    pause
    exit /b 1
)
:docker_ready
echo Docker is running.

rem 2. Everything else: run_local.ps1 opens one window per service.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1"
if errorlevel 1 (
    echo [error] run_local.ps1 failed - see the messages above.
    pause
    exit /b 1
)

rem 3. Open the app as soon as the API answers (the analysis worker loads its models meanwhile).
echo Waiting for the server on http://127.0.0.1:5000 ...
powershell -NoProfile -Command "for ($i = 0; $i -lt 120; $i++) { try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/healthz -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 2 } }"
start "" "http://127.0.0.1:5000"
echo Mint-Gate is running. Close the service windows to stop it.
timeout /t 10 >nul
