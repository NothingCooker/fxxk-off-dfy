# 注册 Windows 计划任务：每 5 分钟跑一轮 dfy_guard（run --once --live）
# 用管理员 PowerShell 执行： powershell -ExecutionPolicy Bypass -File .\install_task.ps1
# 卸载： Unregister-ScheduledTask -TaskName "dfy_guard" -Confirm:$false

param(
    [int]$IntervalMinutes = 5,
    [switch]$DryRun,
    [string]$TaskName = "dfy_guard"
)

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $dir "dfy_guard.py"

if (-not (Test-Path $script)) { throw "找不到 $script" }
if (-not (Test-Path (Join-Path $dir "config.json"))) {
    Write-Host "config.json 不存在，先跑：python dfy_guard.py init" -ForegroundColor Yellow
}

$verb = if ($DryRun) { "check" } else { "run --once --live" }
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" $verb" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "B站大肥鱼机器人评论清理（$verb）" -Force | Out-Null

Write-Host "已注册计划任务 $TaskName（每 $IntervalMinutes 分钟，命令：$verb）" -ForegroundColor Green
Write-Host "查看：Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
