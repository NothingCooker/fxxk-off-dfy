@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo  dfy_guard - 正式清理（命中即删除）
echo  按 Ctrl+C 退出
echo ==========================================
python dfy_guard.py run --live
pause
