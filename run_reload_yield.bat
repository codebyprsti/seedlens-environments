@echo off
REM Run yield data reload and save output to log
cd /d "%~dp0"
echo Started at %date% %time%
python reload_yield_data.py
echo Finished at %date% %time%
pause
