. "$PSScriptRoot\env.ps1"
$ZhiXingSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-RestMethod 'http://127.0.0.1:8000/api/session' -WebSession $ZhiXingSession | Out-Null
$ZhiXingBody = @{message_id=[guid]::NewGuid().ToString();source='demo';text='明天下午三点组会，今晚整理实验结果发给导师。';conversation_id='demo'} | ConvertTo-Json
Invoke-RestMethod 'http://127.0.0.1:8000/api/messages' -Method Post -WebSession $ZhiXingSession -Headers @{'X-ZhiXing-Local'='1'} -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($ZhiXingBody))
