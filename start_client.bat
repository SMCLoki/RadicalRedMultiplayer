@echo off
rem Radical Red Online - open mGBA with your ROM copy. Usage: start_client.bat [rom path]
cd /d "%~dp0"
if not exist work mkdir work
set "ROM=%~dp0work\rr.gba"
if not "%~1"=="" set "ROM=%~f1"
if not exist "%ROM%" (
  echo Could not find "%ROM%".
  echo Copy your Radical Red ROM into the work folder and name it rr.gba
  pause
  exit /b 1
)
set "MGBA=%ProgramFiles%\mGBA\mGBA.exe"
if not exist "%MGBA%" set "MGBA=%ProgramFiles(x86)%\mGBA\mGBA.exe"
if not exist "%MGBA%" set "MGBA=mGBA.exe"
start "" "%MGBA%" "%ROM%"
echo mGBA is starting.
echo In mGBA: Tools - Scripting... then File - Load script... and choose:
echo   %~dp0client\rr_mp.lua
echo (mGBA 0.10 cannot load scripts from the command line.)
timeout /t 15
