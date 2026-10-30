$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
node scripts/service.mjs start
if ($LASTEXITCODE -ne 0) { throw '服务启动失败，请检查 data/service.log' }
