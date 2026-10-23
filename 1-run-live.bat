@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo ==========================================
echo  fxxk-off-dfy - 正式清理（命中即删除）
echo  按 Ctrl+C 退出
echo ==========================================
python fxxk_off_dfy.py run --live
pause
