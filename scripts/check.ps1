. "$PSScriptRoot\env.ps1"
& $ZhiXingPython --version
& node --version
& $ZhiXingPython -m pip check
@{Python=$ZhiXingPython;CondaCache=$env:CONDA_PKGS_DIRS;PipCache=$env:PIP_CACHE_DIR;NpmCache=$env:npm_config_cache;BrowserCache=$env:PLAYWRIGHT_BROWSERS_PATH;Data=$env:ZHIXING_DATA_DIR;Temp=$env:TEMP} | Format-List
