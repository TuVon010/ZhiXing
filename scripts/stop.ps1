. "$PSScriptRoot\env.ps1"
$ZhiXingPidFile = Join-Path $ZhiXingRoot 'data\processes.json'
if (Test-Path -LiteralPath $ZhiXingPidFile) {
    foreach ($ZhiXingEntry in (Get-Content -LiteralPath $ZhiXingPidFile -Raw | ConvertFrom-Json)) {
        $ZhiXingProcess = Get-Process -Id $ZhiXingEntry.id -ErrorAction SilentlyContinue
        if ($ZhiXingProcess -and $ZhiXingProcess.Path -eq $ZhiXingPython -and $ZhiXingProcess.StartTime.ToUniversalTime().Ticks -eq ([datetime]$ZhiXingEntry.started).ToUniversalTime().Ticks) {
            Stop-Process -Id $ZhiXingEntry.id
            Wait-Process -Id $ZhiXingEntry.id -Timeout 10 -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $ZhiXingPidFile
}
Write-Output '项目服务已停止。'
