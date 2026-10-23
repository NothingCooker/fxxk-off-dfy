@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if exist config.json goto run
echo 首次使用：先点「获取方法」拿 Cookie，填进界面后点「校验登录」再「保存配置」。
:run
python fxxk_off_dfy_gui.py
if errorlevel 1 pause
