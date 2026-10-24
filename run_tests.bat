@echo off
rem Runs the offline test suites. tests\test_client.py is skipped unless "pip install lupa" was done.
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% server\test_server.py || goto :fail
%PY% tests\test_client.py || goto :fail
%PY% tests\test_visuals.py || goto :fail
%PY% tests\test_battle.py || goto :fail
%PY% tests\test_talk.py || goto :fail
%PY% server\soak.py --minutes 0.5 || goto :fail
echo ALL TESTS PASSED
pause
exit /b 0
:fail
echo TESTS FAILED
pause
exit /b 1
