@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo  dfy_guard - 体检模式（只打印，不删除）
echo ==========================================
python dfy_guard.py check
pause
