@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo ==========================================
echo  fxxk-off-dfy - 规则自测（离线，不需要 Cookie）
echo ==========================================
python fxxk_off_dfy.py selftest
pause
