# 注册 Windows 计划任务：每 5 分钟跑一轮 fxxk-off-dfy（run --once）
# 用管理员 PowerShell 执行： powershell -ExecutionPolicy Bypass -File .\install_task.ps1
# 卸载： Unregister-ScheduledTask -TaskName "fxxk-off-dfy" -Confirm:$false

param(
    [int]$IntervalMinutes = 5,
    [switch]$DryRun,
    [switch]$Live,
    [string]$TaskName = "fxxk-off-dfy"
)

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $dir "fxxk_off_dfy.py"

if (-not (Test-Path $script)) { throw "找不到 $script" }
if (-not (Test-Path (Join-Path $dir "config.json"))) {
    Write-Host "config.json 不存在，先跑：python fxxk_off_dfy.py init" -ForegroundColor Yellow
}

# 三种模式：
#   默认       -> run --once        跟着 config.json 的 dry_run 走（推荐先这样跑一天）
#   -Live      -> run --once --live 强制真删，忽略 config 的 dry_run
#   -DryRun    -> check             只体检、不落生产 state（每轮都会全量重扫，不要长期挂）

if ($DryRun) {
    $verb = "check"
    Write-Host "注意：-DryRun 用体检模式，不写 state，每轮都会把可见评论全部重判一次。" -ForegroundColor Yellow
} elseif ($Live) {
    $verb = "run --once --live"
    Write-Host "注意：-Live 会真的删除评论（忽略 config.json 的 dry_run）。" -ForegroundColor Yellow
} else {
    $verb = "run --once"
    Write-Host "按 config.json 的 dry_run 执行；要开删请把 dry_run 改成 false 或用 -Live。" -ForegroundColor Cyan
}
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" $verb" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "B站大肥鱼机器人评论清理（$verb）" -Force | Out-Null

Write-Host "已注册计划任务 $TaskName（每 $IntervalMinutes 分钟，命令：$verb）" -ForegroundColor Green
Write-Host "查看：Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
