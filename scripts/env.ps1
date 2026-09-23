$ErrorActionPreference = 'Stop'
$ZhiXingRoot = Split-Path $PSScriptRoot -Parent
$ZhiXingParent = Split-Path $ZhiXingRoot -Parent
$ZhiXingCache = Join-Path $ZhiXingParent '.cache'
$ZhiXingPython = Join-Path $ZhiXingParent '.envs\zhixing\python.exe'
$env:CONDA_PKGS_DIRS = Join-Path $ZhiXingCache 'conda'
$env:PIP_CACHE_DIR = Join-Path $ZhiXingCache 'pip'
$env:npm_config_cache = Join-Path $ZhiXingCache 'npm'
$env:PNPM_HOME = Join-Path $ZhiXingCache 'pnpm'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ZhiXingCache 'playwright'
$env:TEMP = Join-Path $ZhiXingRoot '.tmp'
$env:TMP = $env:TEMP
$env:ZHIXING_DATA_DIR = Join-Path $ZhiXingRoot 'data'
$env:PYTHONUTF8 = '1'
foreach ($ZhiXingDir in @($env:CONDA_PKGS_DIRS,$env:PIP_CACHE_DIR,$env:npm_config_cache,$env:PNPM_HOME,$env:PLAYWRIGHT_BROWSERS_PATH,$env:TEMP,$env:ZHIXING_DATA_DIR,(Join-Path $ZhiXingRoot 'logs'))) {
    New-Item -ItemType Directory -Force -Path $ZhiXingDir | Out-Null
}
Set-Location $ZhiXingRoot

$env:HF_HOME = Join-Path $ZhiXingCache 'huggingface'
$env:TORCH_HOME = Join-Path $ZhiXingCache 'torch'
$env:XDG_CACHE_HOME = Join-Path $ZhiXingCache 'xdg'
$env:HF_HUB_DISABLE_XET = '1'
