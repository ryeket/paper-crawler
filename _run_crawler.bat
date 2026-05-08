@echo off
cd /d %~dp0
set LOGFILE=_logs\crawler_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%.log
set LOGFILE=%LOGFILE: =0%
python crawl_slow.py >> %LOGFILE% 2>&1
