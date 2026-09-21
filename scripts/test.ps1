. "$PSScriptRoot\env.ps1"
$ZhiXingRestore = Test-Path -LiteralPath (Join-Path $ZhiXingRoot 'data\processes.json')
$ZhiXingTestExit = 1
try {
    if ($ZhiXingRestore) { & "$PSScriptRoot\stop.ps1" }
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
        throw '8000 端口有其他服务，请先释放端口后运行测试。'
    }
    & $ZhiXingPython "$PSScriptRoot\run_checks.py"
    $ZhiXingTestExit = $LASTEXITCODE
} finally {
    if ($ZhiXingRestore) { & "$PSScriptRoot\start.ps1" }
}
exit $ZhiXingTestExit
