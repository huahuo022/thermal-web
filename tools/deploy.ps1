<#
.SYNOPSIS
    推送本地改动，并让打印服务器自己拉取、重新部署、重启。

.DESCRIPTION
    1) 工作区有改动时提交（需要 -Message）
    2) git push 到当前分支
    3) POST /api/update/apply，让服务器自己去 pull + install.sh
    4) 轮询 /api/update?fetch=0，直到服务器的提交与刚推送的一致

.EXAMPLE
    $env:THERMAL_WEB_PASSWORD = "你的密码"
    .\tools\deploy.ps1 -Message "fix: 修正小票预览的对齐"

.EXAMPLE
    .\tools\deploy.ps1 -Message "feat: xxx" -BaseUrl "https://thermal-web.example.com:8080"
#>
[CmdletBinding()]
param(
    [string]$Message,
    [string]$BaseUrl = "https://thermal-web.example.com:8080",
    [string]$User = "admin",
    [string]$Password = $env:THERMAL_WEB_PASSWORD,
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not $Password) {
    throw "请先设置 THERMAL_WEB_PASSWORD 环境变量，或用 -Password 传入"
}

if (git status --porcelain) {
    if (-not $Message) { throw "工作区有未提交改动，请用 -Message 给出提交信息" }
    git add -A
    git commit -m $Message | Out-Host
}

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$head = (git rev-parse HEAD).Trim()
Write-Host "==> pushing $branch ($($head.Substring(0,7)))"
git push origin $branch | Out-Host

$auth = "Basic " + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${User}:${Password}"))
$headers = @{ Authorization = $auth }

Write-Host "==> asking the server to update"
$response = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/update/apply" -Headers $headers `
    -ContentType "application/json" -Body "{}" -TimeoutSec 30
$from = if ($response.from) { $response.from.short } else { "?" }
$to = if ($response.to) { $response.to.short } else { "?" }
Write-Host ("    {0} -> {1}  runner={2}" -f $from, $to, $response.runner)

Write-Host "==> waiting for the server to come back"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    try {
        $state = Invoke-RestMethod -Uri "$BaseUrl/api/update?fetch=0" -Headers $headers -TimeoutSec 20
        $serverHead = $state.current.sha
        if ($serverHead -eq $head) {
            Write-Host ("    server is on {0} - done" -f $state.current.short)
            exit 0
        }
        Write-Host ("    server still on {0}, waiting…" -f $state.current.short)
    } catch {
        Write-Host "    service is restarting…"
    }
}

throw "超时：服务器没有在 $TimeoutSeconds 秒内更新到 $($head.Substring(0,7))，请查看服务器上的 data/update.log"
