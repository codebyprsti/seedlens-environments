@echo off
REM Run yield data reload and save all output to reload_yield.log
cd /d "%~dp0"
echo Started at %date% %time%
python reload_yield_data.py > reload_yield.log 2>&1
echo Finished at %date% %time%
type reload_yield.log
pause
