. "$PSScriptRoot\env.ps1"
if (Test-Path 'data\processes.json') { throw '请先停止服务，保证业务数据库与检查点备份一致。' }
& $ZhiXingPython scripts/backup.py
