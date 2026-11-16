[CmdletBinding()]
param([ValidateRange(1, 300)][int]$StartupTimeoutSeconds = 60)

. "$PSScriptRoot\env.ps1"

function Get-ZhiXingListenerId {
    # netstat also works in ordinary user sessions where CIM access is denied.
    # An IPv4 listener has no remote peer: the remote endpoint is 0.0.0.0:0.
    $socketRows = & "$env:SystemRoot\System32\netstat.exe" -ano -p tcp
    if ($LASTEXITCODE -ne 0) { throw '无法读取 TCP 监听状态，请检查 netstat 是否可用。' }
    foreach ($socketRow in $socketRows) {
        if ($socketRow -match '^\s*TCP\s+(?:127\.0\.0\.1|0\.0\.0\.0):8000\s+0\.0\.0\.0:0\s+\S+\s+(\d+)\s*$') {
            [int]$Matches[1]
        }
    }
}
if (-not (Test-Path -LiteralPath $ZhiXingPython)) { throw '请先运行 scripts\install.ps1' }
if (-not (Test-Path 'frontend\dist\index.html')) { throw '请先安装并构建前端' }
$listenerIds = @(Get-ZhiXingListenerId)
if ($listenerIds.Count) { throw "8000 端口已在监听（PID：$($listenerIds -join ', ')）。请确认占用者；如果是之前启动的知行，请先运行 scripts\stop.ps1。" }
$ZhiXingPidFile = Join-Path $ZhiXingRoot 'data\processes.json'
if (Test-Path -LiteralPath $ZhiXingPidFile) {
    $ZhiXingPrior = Get-Content -LiteralPath $ZhiXingPidFile -Raw | ConvertFrom-Json
    foreach ($ZhiXingEntry in $ZhiXingPrior) {
        if (Get-Process -Id $ZhiXingEntry.id -ErrorAction SilentlyContinue) { throw '已有服务进程，请先运行 stop.ps1' }
    }
}
$ZhiXingProcesses = @()
$children = @{}
$http = $null
try {
    foreach ($ZhiXingComponent in @(@{name='api';arguments=@('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000')},@{name='worker';arguments=@('-m','backend.worker')})) {
        $ZhiXingProcess = Start-Process -FilePath $ZhiXingPython -ArgumentList $ZhiXingComponent.arguments -WorkingDirectory $ZhiXingRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $ZhiXingRoot "logs\$($ZhiXingComponent.name).out.log") -RedirectStandardError (Join-Path $ZhiXingRoot "logs\$($ZhiXingComponent.name).err.log")
        $children[$ZhiXingComponent.name] = $ZhiXingProcess
        $ZhiXingProcesses += @{id=$ZhiXingProcess.Id;name=$ZhiXingComponent.name;started=$ZhiXingProcess.StartTime.ToUniversalTime().ToString('o')}
    }
    $ZhiXingProcesses | ConvertTo-Json | Set-Content -LiteralPath $ZhiXingPidFile -Encoding utf8
    Add-Type -AssemblyName System.Net.Http
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $http = [System.Net.Http.HttpClient]::new($handler)
    $http.Timeout = [TimeSpan]::FromSeconds(2)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $waitReason = '尚未监听 8000 端口'
    $health = $null
    Write-Output '正在等待 API 就绪…'
    while ($clock.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds) {
        foreach ($entry in $ZhiXingProcesses) {
            $child = $children[$entry.name]
            if ($child.HasExited) {
                $exitCode = $child.ExitCode
                $exitInfo = if ($null -ne $exitCode) { "（退出码 $exitCode）" } else { '' }
                throw "$($entry.name) 在启动时退出$exitInfo。请查看 logs\$($entry.name).err.log。"
            }
        }
        $listenerIds = @(Get-ZhiXingListenerId)
        $foreignIds = @($listenerIds | Where-Object { $_ -ne $children['api'].Id })
        if ($foreignIds.Count) { throw "启动期间 8000 端口被其他进程占用（PID：$($foreignIds -join ', ')）。" }
        if ($listenerIds -contains $children['api'].Id) {
            $waitReason = '端口已监听，但 /api/health 尚未返回正常状态'
            try {
                $health = $http.GetStringAsync('http://127.0.0.1:8000/api/health').GetAwaiter().GetResult() | ConvertFrom-Json
            } catch {
                # A listener may accept connections before the health check responds.
            }
            if ($health.status -eq 'ok') { break }
        }
        Start-Sleep -Milliseconds 250
    }
    if ($health.status -ne 'ok') { throw "API 在 $StartupTimeoutSeconds 秒内未就绪：$waitReason。请查看 logs\api.err.log。" }
    $health | Format-List
    Write-Output '控制台：http://127.0.0.1:8000'
} catch {
    # Only clean up children started by this call, never a foreign listener.
    foreach ($child in $children.Values) {
        if (-not $child.HasExited) { Stop-Process -InputObject $child -ErrorAction SilentlyContinue }
    }
    throw
} finally {
    if ($http) { $http.Dispose() }
}
