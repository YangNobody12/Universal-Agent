@echo off
rem Universal Autocomplete + Kitsu the on-screen companion (start_universal_autocomplete.bat = classic window)
rem Python: %UA_PYTHON% if set, else the py launcher, else python on PATH
set "PY=%UA_PYTHON%"
if "%PY%"=="" where py >nul 2>nul && set "PY=py"
if "%PY%"=="" set "PY=python"
"%PY%" "%~dp0kitsu.py"
