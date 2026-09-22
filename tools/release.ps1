<#
.SYNOPSIS
    thermal-web 发布全流程：一条命令走完「预检 → 自检 → 推送 → 服务器更新 → 核对 → 验收指引」。

.DESCRIPTION
    固定流程，避免每次手工拼步骤：

      1. 读取本地配置（.release.env，不进公开仓库）
      2. 预检：git 状态、远端可达、工作区是否有改动
      3. 本地自检：python 语法、node 语法（可用 -SkipChecks 跳过）
      4. 提交 + 推送（有改动时必须给 -Message）
      5. 调 POST /api/update/apply，让服务器自己拉取并重启
      6. 轮询直到服务器的提交与本地一致
      7. 服务端核对：版本、工作区是否干净、服务状态、更新日志（需配置 ssh 别名）
      8. 打印验收清单，可选自动打开验收页面

.EXAMPLE
    # 首次：把配置写到 .release.env（之后就不用再传了）
    .\tools\release.ps1 -Url "https://your-host:8080" -Password "你的密码" -Ssh "你的ssh别名" -SaveConfig

    # 日常发布
    .\tools\release.ps1 -Message "fix: 修正小票预览对齐" -OpenBrowser

    # 已经手动推送过，只想让服务器更新并核对
    .\tools\release.ps1 -SkipPush
#>
[CmdletBinding()]
param(
    [string]$Message,
    [string]$Url = $env:THERMAL_WEB_URL,
    [string]$Password = $env:THERMAL_WEB_PASSWORD,
    [string]$Ssh = $env:THERMAL_WEB_SSH,
    [string]$RemoteRepo = "/root/thermal-web",
    [string]$RemoteDest = "/opt/thermal-web",
    [int]$TimeoutSeconds = 240,
    [switch]$SkipChecks,
    [switch]$SkipPush,
    [switch]$OpenBrowser,
    [switch]$SaveConfig
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$ConfigFile = Join-Path $Root ".release.env"

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Info($text) { Write-Host "    $text" }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Die($text) { Write-Host ""; Write-Host "!! $text" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 配置
if (-not (Test-Path $ConfigFile) -and -not $SaveConfig) {
    Warn ".release.env 不存在，本次只用命令行参数/环境变量"
}
if (Test-Path $ConfigFile) {
    foreach ($line in Get-Content $ConfigFile) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        $kv = $t.Split("=", 2)
        if ($kv.Count -ne 2) { continue }
        $key = $kv[0].Trim(); $value = $kv[1].Trim().Trim('"').Trim("'")
        switch ($key) {
            "THERMAL_WEB_URL" { if (-not $Url) { $Url = $value } }
            "THERMAL_WEB_PASSWORD" { if (-not $Password) { $Password = $value } }
            "THERMAL_WEB_SSH" { if (-not $Ssh) { $Ssh = $value } }
            "THERMAL_WEB_REPO" { $RemoteRepo = $value }
            "THERMAL_WEB_DEST" { $RemoteDest = $value }
        }
    }
}

if ($SaveConfig) {
    if (-not $Url) { Die "-SaveConfig 需要同时给出 -Url" }
    $lines = @(
        "# thermal-web 发布配置（本文件已被 .gitignore 忽略，不会进公开仓库）",
        "THERMAL_WEB_URL=$Url"
    )
    if ($Password) { $lines += "THERMAL_WEB_PASSWORD=$Password" }
    if ($Ssh) { $lines += "THERMAL_WEB_SSH=$Ssh" }
    Set-Content -Path $ConfigFile -Value $lines -Encoding UTF8
    Ok "已写入 $ConfigFile"
}

if (-not $Url) { Die "缺少服务地址：用 -Url 或环境变量 THERMAL_WEB_URL，或先跑一次 -SaveConfig" }
if (-not $Password) { Die "缺少密码：用 -Password 或环境变量 THERMAL_WEB_PASSWORD（可写进 .release.env）" }
$Url = $Url.TrimEnd("/")
$auth = "Basic " + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:${Password}"))
$headers = @{ Authorization = $auth }

Write-Host "thermal-web 发布" -ForegroundColor White
Info "仓库    : $Root"
Info "服务    : $Url"
Info "远程仓库: $(if ($Ssh) { "$($Ssh):$RemoteRepo" } else { '(未配置 ssh，跳过服务端核对)' })"

# ---------------------------------------------------------------- 1. 预检
Step "1/7 预检"
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$headBefore = (git rev-parse HEAD).Trim()
$dirty = @(git status --porcelain)
Info "分支 $branch，当前提交 $($headBefore.Substring(0,7))"
if ($dirty.Count) {
    Info "有 $($dirty.Count) 处未提交改动"
    if (-not $Message -and -not $SkipPush) { Die "有未提交改动，请用 -Message 给出提交信息" }
} else {
    Info "工作区干净"
}
$remoteHead = (git ls-remote --heads origin $branch 2>$null | ForEach-Object { $_.Split()[0] })
if (-not $remoteHead) { Die "拿不到 origin/$branch（检查网络与凭据）" }
Info "远端 $branch = $($remoteHead.Substring(0,7))"

# ---------------------------------------------------------------- 2. 自检
if ($SkipChecks) {
    Step "2/7 本地自检（已跳过）"
} else {
    Step "2/7 本地自检"
    $py = if (Test-Path (Join-Path $Root ".venv/Scripts/python.exe")) {
        Join-Path $Root ".venv/Scripts/python.exe"
    } else { "python" }
    $pyFiles = @(Get-ChildItem -Path $Root -Filter *.py -File | ForEach-Object { $_.Name })
    if ($pyFiles.Count) {
        $out = & $py -m py_compile @pyFiles 2>&1
        if ($LASTEXITCODE -ne 0) { Die "python 语法检查失败：`n$out" }
        Ok "python 语法 OK（$($pyFiles.Count) 个文件）"
    }
    if (Get-Command node -ErrorAction SilentlyContinue) {
        foreach ($js in @(Get-ChildItem -Path (Join-Path $Root "web") -Filter *.js -File)) {
            node --check $js.FullName 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { Die "JS 语法检查失败：$($js.Name)" }
        }
        Ok "前端 JS 语法 OK"
    } else {
        Warn "本机没有 node，跳过 JS 语法检查"
    }
}

# ---------------------------------------------------------------- 3+4. 提交 / 推送
Step "3/7 提交并推送"
if ($SkipPush) {
    Info "已指定 -SkipPush，跳过"
} else {
    if ($dirty.Count) {
        git add -A
        git commit -q -m $Message
        if ($LASTEXITCODE -ne 0) { Die "提交失败（检查 git user.name / user.email 是否配置）" }
        Ok "已提交：$Message"
    } else {
        Info "没有需要提交的改动"
    }
    git push origin $branch 2>&1 | ForEach-Object { Info $_ }
    if ($LASTEXITCODE -ne 0) { Die "推送失败" }
    Ok "已推送到 origin/$branch"
}
$head = (git rev-parse HEAD).Trim()
Info "本地提交 = $($head.Substring(0,7))"

# ---------------------------------------------------------------- 5. 触发更新
Step "4/7 触发服务器更新"
try {
    $apply = Invoke-RestMethod -Method Post -Uri "$Url/api/update/apply" -Headers $headers `
        -ContentType "application/json" -Body "{}" -TimeoutSec 30
} catch {
    Die "调用 /api/update/apply 失败：$($_.Exception.Message)"
}
$from = if ($apply.from) { $apply.from.short } else { "?" }
$to = if ($apply.to) { $apply.to.short } else { "?" }
Ok "$from -> $to    runner=$($apply.runner)"

# ---------------------------------------------------------------- 6. 等重启
Step "5/7 等待服务器更新完成"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$lastSeen = ""
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    try {
        $state = Invoke-RestMethod -Uri "$Url/api/update?fetch=0" -Headers $headers -TimeoutSec 20
        $serverHead = $state.current.sha
        if ($serverHead -eq $head) {
            Ok "服务器已切到 $($state.current.short)"
            break
        }
        if ($serverHead -ne $lastSeen) {
            $lastSeen = $serverHead
            Info "服务器当前 $($state.current.short)，等待重启…"
        }
    } catch {
        Info "服务正在重启…"
    }
}
if ($serverHead -ne $head) {
    Warn "超时：服务器没在 $TimeoutSeconds 秒内切到 $($head.Substring(0,7))"
    if ($Ssh) { Warn "去服务器上看：ssh $Ssh 'tail -20 $RemoteDest/data/update.log'" }
    exit 1
}

# ---------------------------------------------------------------- 7. 核对
Step "6/7 服务端核对"
try {
    $status = Invoke-RestMethod -Uri "$Url/api/status" -Headers $headers -TimeoutSec 20
    Ok "版本 $($status.version) / 服务启动于 $([DateTimeOffset]::FromUnixTimeSeconds($status.started_at).LocalDateTime)"
    if ($status.printer.available) { Ok "打印目标可用：$($status.printer.target)" }
    else { Warn "打印目标不可用：$($status.printer.target)" }
} catch {
    Warn "拿不到 /api/status：$($_.Exception.Message)"
}
try {
    $upd = Invoke-RestMethod -Uri "$Url/api/update?fetch=0" -Headers $headers -TimeoutSec 20
    Ok "工作区 dirty=$($upd.dirty)，落后 $($upd.behind) 个提交"
} catch { }
if ($Ssh) {
    $probe = & ssh -o BatchMode=yes $Ssh "systemctl is-active thermal-web; git -C $RemoteRepo log --oneline -1; tail -3 $RemoteDest/data/update.log" 2>&1
    foreach ($line in $probe) { if ($line.Trim()) { Info $line.Trim() } }
} else {
    Info "未配置 ssh 别名，跳过服务端日志核对"
}

# ---------------------------------------------------------------- 8. 验收
Step "7/7 验收"
Write-Host "    打开 $Url 验收：" -ForegroundColor White
Info "1) 登录后刷新页面（静态资源 no-store，普通刷新即可）"
Info "2) 标题栏版本 v$($status.version)；设置页「版本更新」里的提交应为 $($head.Substring(0,7))"
Info "3) 界面改动：六个页签逐个点一遍"
Info "4) 打印改动：先「试运行（不打印）」核对字节，再真打"
Info "5) 接口改动：curl -u admin:<密码> $Url/api/status"
if ($OpenBrowser) { Start-Process $Url }
Write-Host ""
Write-Host "完成：$($head.Substring(0,7)) 已上线" -ForegroundColor Green
