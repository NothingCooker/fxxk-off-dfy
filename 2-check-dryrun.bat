@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo ==========================================
echo  fxxk-off-dfy - 体检模式（只打印，不删除）
echo ==========================================
python fxxk_off_dfy.py check
pause
