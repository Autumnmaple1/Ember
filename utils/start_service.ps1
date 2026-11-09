param(
    [ValidateSet('start', 'stop')][string]$Action = 'start',
    [string]$ConfigFile = 'deploy/service.env',
    [ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$ProjectName = 'ember-service',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not [IO.Path]::IsPathRooted($ConfigFile)) { $ConfigFile = Join-Path $projectRoot $ConfigFile }
$ConfigFile = [IO.Path]::GetFullPath($ConfigFile)
$configDirectory = Split-Path -Parent $ConfigFile
$codeFile = Join-Path $configDirectory 'private/owner-code.txt'
$previousConfig = $env:EMBER_SERVICE_ENV

function Invoke-Docker([string[]]$DockerArguments) {
    & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker 命令执行失败，请查看上方错误。' }
}

function Test-DockerReady {
    # Native stderr during engine startup must not terminate Windows PowerShell 5.
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & docker info --format '{{.ServerVersion}}' *> $null; return $LASTEXITCODE -eq 0 }
    finally { $ErrorActionPreference = $oldPreference }
}

function Get-ServiceValue([string]$Name) {
    $value = ''
    foreach ($line in Get-Content -LiteralPath $ConfigFile -Encoding UTF8) {
        if ($line -match ('^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*=\s*(.*)$')) {
            $value = $Matches[1].Trim()
            if ($value.Length -ge 2 -and (($value.StartsWith("'") -and $value.EndsWith("'")) -or
                    ($value.StartsWith('"') -and $value.EndsWith('"')))) {
                $value = $value.Substring(1, $value.Length - 2)
            } else { $value = ($value -replace '\s+#.*$', '').Trim() }
        }
    }
    return $value
}

try {
    if (-not (Get-Command docker -CommandType Application -ErrorAction SilentlyContinue)) {
        throw '未找到 Docker。请先安装 Docker Desktop，启用 Linux 容器后重试。'
    }
    Invoke-Docker -DockerArguments @('compose', 'version', '--short')
    if (-not (Test-DockerReady)) {
        if ($Action -eq 'stop') { throw 'Docker 引擎未运行，无法确认服务状态；无需为停止服务启动 Docker。' }
        $desktop = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
        if (-not (Test-Path -LiteralPath $desktop)) { throw '请打开 Docker Desktop，等待引擎就绪后重试。' }
        Write-Host '正在启动 Docker Desktop，等待引擎就绪…'
        Start-Process -FilePath $desktop -WindowStyle Hidden
        $deadline = [DateTime]::UtcNow.AddSeconds(120)
        while (-not (Test-DockerReady)) {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Docker 启动超时。请查看 Docker Desktop 状态后重试。' }
            Start-Sleep -Seconds 2
        }
    }
    $env:EMBER_SERVICE_ENV = $ConfigFile
    $compose = @('compose', '--env-file', $ConfigFile, '-f', (Join-Path $projectRoot 'compose.service.yml'), '-p', $ProjectName)
    if ($Action -eq 'stop') {
        if (-not (Test-Path -LiteralPath $ConfigFile)) { throw '找不到服务配置，未执行停止操作。请恢复原 service.env。' }
        Invoke-Docker ($compose + @('stop'))
        Write-Host 'Ember 容器服务已停止，数据卷与配置均已保留。'
        exit 0
    }
    if (-not (Test-Path -LiteralPath $ConfigFile)) {
        if ((Split-Path -Leaf $ConfigFile) -ne 'service.env') { throw '首次生成的配置文件名必须为 service.env。' }
        if (Test-Path -LiteralPath $codeFile) { throw '访问码已存在但配置缺失，请恢复原 service.env；不会重新生成数据库密码。' }
        $existingVolumes = @(& docker volume ls --filter "label=com.docker.compose.project=$ProjectName" --format '{{.Name}}')
        if ($LASTEXITCODE -ne 0) { throw '无法检查已有数据卷，停止初始化。' }
        if ($existingVolumes.Count) { throw '发现已有服务数据卷但配置缺失，请恢复原 service.env；不会创建新的数据库密码。' }
        Write-Host '首次启动：构建运行镜像，并在容器内生成独立配置，无需本机 Python 或 Node.js。'
        Invoke-Docker -DockerArguments @('build', '-t', 'ember-service:local', $projectRoot)
        New-Item -ItemType Directory -Force -Path $configDirectory | Out-Null
        $setup = @('run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--entrypoint', 'python',
            '--mount', "type=bind,source=$projectRoot/utils/service_setup.py,target=/tmp/service_setup.py,readonly",
            '--mount', "type=bind,source=$configDirectory,target=/setup/deploy")
        $sourceConfig = Join-Path $projectRoot '.env'
        if (Test-Path -LiteralPath $sourceConfig) {
            $setup += @('--mount', "type=bind,source=$sourceConfig,target=/setup/source.env,readonly")
        }
        Invoke-Docker ($setup + @('ember-service:local', '/tmp/service_setup.py', '--directory', '/setup/deploy', '--from-env', '/setup/source.env'))
    }
    $required = @('LARGE_LLM_MODEL', 'LARGE_LLM_API_KEY', 'LARGE_LLM_BASE_URL',
        'SMALL_LLM_MODEL', 'SMALL_LLM_API_KEY', 'SMALL_LLM_BASE_URL',
        'EMBEDDING_MODEL', 'EMBEDDING_API_KEY', 'EMBEDDING_BASE_URL', 'PG_PASSWORD', 'EMBER_ACCESS_CODE_HASH')
    $missing = @($required | Where-Object { -not (Get-ServiceValue $_) })
    if ($missing.Count) { throw ('请在 ' + $ConfigFile + ' 中补齐配置项后重试：' + ($missing -join ', ')) }
    Invoke-Docker ($compose + @('config', '--quiet'))
    Write-Host '正在构建并启动 Ember，首次下载依赖可能较慢…'
    Invoke-Docker ($compose + @('up', '-d', '--build', '--wait', '--wait-timeout', '240'))
    $url = Get-ServiceValue 'EMBER_PUBLIC_URL'
    $parsed = $null
    if (-not [Uri]::TryCreate($url, [UriKind]::Absolute, [ref]$parsed) -or $parsed.Scheme -notin @('http', 'https')) {
        Write-Host '服务已就绪。请根据 service.env 中的端口与域名访问网页。'
    } else {
        Write-Host ('服务已就绪：' + $url)
        if (-not $NoBrowser) {
            try { Start-Process -FilePath $url }
            catch { Write-Host '未能自动打开浏览器，服务已运行；请手动打开上述地址。' }
        }
    }
    Write-Host ('服务配置：' + $ConfigFile)
    Write-Host ('登录访问码文件：' + $codeFile + '（请自行查看，脚本不会显示访问码）')
    Write-Host '关闭此窗口不会停止容器；停止请运行 stop_service.bat。'
} catch {
    Write-Host ('[失败] ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host '配置和数据卷均已保留。详细排查步骤见 docs/service-deployment.md。'
    exit 1
} finally {
    $env:EMBER_SERVICE_ENV = $previousConfig
}
