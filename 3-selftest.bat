@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo  dfy_guard - 规则自测（离线，不需要 Cookie）
echo ==========================================
python dfy_guard.py selftest
pause
