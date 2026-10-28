@echo off
rem Khởi động ACS trên Windows KHÔNG cần Docker/Redis.
rem   runacs.bat            -> ACS_ENV=prod, cổng 8000
rem   set ACS_ENV=dev ^& runacs.bat
setlocal
cd /d "%~dp0"

if "%ACS_ENV%"=="" set ACS_ENV=prod
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
if "%ACS_PORT%"=="" set ACS_PORT=8000

echo == ACS khoi dong: env=%ACS_ENV% port=%ACS_PORT% ==
%PY% manage.py migrate --noinput
if errorlevel 1 goto :error
%PY% manage.py check_deployment --skip-mqtt

start "ACS Order Workers" %PY% manage.py run_acs_workers
start "ACS MQTT Worker" %PY% manage.py run_mqtt_worker
%PY% -m daphne -b 0.0.0.0 -p %ACS_PORT% config.asgi:application
goto :eof

:error
echo Khoi dong that bai: kiem tra bien moi truong (.env.example) va PostgreSQL/MQTT.
exit /b 1
