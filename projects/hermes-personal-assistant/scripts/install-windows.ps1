$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$nodeVersion = node --version
if ($nodeVersion -notmatch '^v22\.') { throw '请安装 Node.js 22.22.2 或兼容的 22.x 补丁版本' }
New-Item -ItemType Directory -Force '.codex-tmp' | Out-Null
$env:TEMP = (Resolve-Path '.codex-tmp').Path
$env:TMP = $env:TEMP
npm ci
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
npm run setup
if ($LASTEXITCODE -ne 0) { throw '配置初始化失败' }
npm run build
if ($LASTEXITCODE -ne 0) { throw '构建失败' }
npm run browser:install
if ($LASTEXITCODE -ne 0) { throw '浏览器安装失败' }
npm test
if ($LASTEXITCODE -ne 0) { throw '测试失败' }
Write-Host '安装完成。运行 scripts/start-windows.ps1 启动；npm run login 重新登录平台。'
