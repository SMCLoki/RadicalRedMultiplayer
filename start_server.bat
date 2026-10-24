@echo off
rem Radical Red Online - start the relay server. Extra options are passed through, e.g.
rem   start_server.bat --host 0.0.0.0      (let other PCs / Tailscale friends connect)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server\rr_server.py %*
) else (
  python server\rr_server.py %*
)
echo.
echo Server stopped.
pause
