. "$PSScriptRoot\env.ps1"
if (-not (Test-Path -LiteralPath $ZhiXingPython)) { throw '请先运行 scripts\install.ps1' }
if (-not (Test-Path 'frontend\dist\index.html')) { throw '请先安装并构建前端' }
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw '8000 端口已被使用，请先停止旧服务。' }
$ZhiXingPidFile = Join-Path $ZhiXingRoot 'data\processes.json'
if (Test-Path -LiteralPath $ZhiXingPidFile) {
    $ZhiXingPrior = Get-Content -LiteralPath $ZhiXingPidFile -Raw | ConvertFrom-Json
    foreach ($ZhiXingEntry in $ZhiXingPrior) {
        if (Get-Process -Id $ZhiXingEntry.id -ErrorAction SilentlyContinue) { throw '已有服务进程，请先运行 stop.ps1' }
    }
}
$ZhiXingProcesses = @()
try {
    foreach ($ZhiXingComponent in @(@{name='api';arguments=@('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000')},@{name='worker';arguments=@('-m','backend.worker')})) {
        $ZhiXingProcess = Start-Process -FilePath $ZhiXingPython -ArgumentList $ZhiXingComponent.arguments -WorkingDirectory $ZhiXingRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $ZhiXingRoot "logs\$($ZhiXingComponent.name).out.log") -RedirectStandardError (Join-Path $ZhiXingRoot "logs\$($ZhiXingComponent.name).err.log")
        $ZhiXingProcesses += @{id=$ZhiXingProcess.Id;name=$ZhiXingComponent.name;started=$ZhiXingProcess.StartTime.ToUniversalTime().ToString('o')}
    }
    $ZhiXingProcesses | ConvertTo-Json | Set-Content -LiteralPath $ZhiXingPidFile -Encoding utf8
    Start-Sleep -Seconds 3
    foreach ($ZhiXingEntry in $ZhiXingProcesses) {
        if (-not (Get-Process -Id $ZhiXingEntry.id -ErrorAction SilentlyContinue)) { throw "$($ZhiXingEntry.name) 启动失败，请查看 logs。" }
    }
    $ZhiXingListener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if (-not $ZhiXingListener -or $ZhiXingListener.OwningProcess -ne $ZhiXingProcesses[0].id) { throw 'API 端口未由本次启动的服务监听。' }
    Invoke-RestMethod 'http://127.0.0.1:8000/api/health' | Format-List
    Write-Output '控制台：http://127.0.0.1:8000'
} catch {
    foreach ($ZhiXingEntry in $ZhiXingProcesses) { Stop-Process -Id $ZhiXingEntry.id -ErrorAction SilentlyContinue }
    throw
}
