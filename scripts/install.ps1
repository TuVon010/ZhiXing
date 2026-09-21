. "$PSScriptRoot\env.ps1"
if (-not (Test-Path -LiteralPath $ZhiXingPython)) {
    & D:\Anaconda\python.exe "$PSScriptRoot\create_environment.py" (Split-Path $ZhiXingPython -Parent)
    if ($LASTEXITCODE -ne 0) { throw 'Conda 环境创建失败' }
}
$ZhiXingRequirements = if (Test-Path 'requirements.lock.txt') {'requirements.lock.txt'} else {'requirements.txt'}
& $ZhiXingPython -m pip install -r $ZhiXingRequirements
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }
& npm.cmd ci --prefix frontend
if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
& $ZhiXingPython scripts/export_openapi.py
Push-Location frontend
try {
    & npm.cmd run types
    if ($LASTEXITCODE -ne 0) { throw '接口类型生成失败' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
} finally { Pop-Location }
if (-not (Test-Path '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Write-Output '安装完成。运行 .\scripts\start.ps1 启动。'
