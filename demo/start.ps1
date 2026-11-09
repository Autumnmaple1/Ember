# Keep this bootstrap ASCII so Windows PowerShell 5 reads it without a BOM.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DemoArguments)

$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $demoRoot
Write-Host 'Checking Python installations...'
$candidates = New-Object 'System.Collections.Generic.List[string]'
$probe = 'import sys,sysconfig,json; print(json.dumps(dict(executable=sys.executable,version=list(sys.version_info[:3]),free_threaded=bool(sysconfig.get_config_var(''Py_GIL_DISABLED'')))))'

function Add-Python([string]$path) {
    if ($path -and -not $candidates.Contains($path)) { $candidates.Add($path) }
}

function Test-Python([string]$path, [string[]]$prefix = @()) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    # Store aliases can open the Store rather than running an interpreter.
    if ($path -match '\\Microsoft\\WindowsApps\\') { return $null }
    try {
        $raw = & $path @prefix -I -c $probe 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $info = ($raw -join "`n") | ConvertFrom-Json
        if ($info.version[0] -lt 3 -or ($info.version[0] -eq 3 -and $info.version[1] -lt 11) -or $info.free_threaded) { return $null }
        if (-not (Test-Path -LiteralPath $info.executable -PathType Leaf)) { return $null }
        return $info
    } catch { return $null }
}

if ($env:EMBER_PYTHON) {
    # An explicit override must be an executable path, never a command string.
    $selected = Test-Python $env:EMBER_PYTHON
    if (-not $selected) {
        Write-Host 'EMBER_PYTHON is invalid. Set it to an existing standard Python 3.11+ python.exe, or clear it.'
        exit 1
    }
} else {
    Add-Python (Join-Path $demoRoot 'runtime\.venv\Scripts\python.exe')
    Add-Python (Join-Path $projectRoot '.venv\Scripts\python.exe')
    Add-Python (Join-Path $projectRoot 'venv\Scripts\python.exe')
    if ($env:CONDA_PREFIX) { Add-Python (Join-Path $env:CONDA_PREFIX 'python.exe') }

    # Locate Conda without executing PATH python.bat wrappers or py's default.
    $condaRoots = New-Object 'System.Collections.Generic.List[string]'
    foreach ($command in @(Get-Command conda,python,python3 -All -ErrorAction SilentlyContinue)) {
        if ($command.Source) {
            $parent = Split-Path -Parent $command.Source
            if ((Split-Path -Leaf $parent) -in @('Scripts', 'condabin')) {
                $condaRoots.Add((Split-Path -Parent $parent))
            }
            if ([IO.Path]::GetExtension($command.Source) -eq '.exe') { Add-Python $command.Source }
        }
    }
    if ($env:CONDA_EXE) { $condaRoots.Add((Split-Path -Parent (Split-Path -Parent $env:CONDA_EXE))) }
    foreach ($homeBase in @($env:USERPROFILE, $env:LOCALAPPDATA)) {
        if ($homeBase) {
            $condaRoots.Add((Join-Path $homeBase 'anaconda3'))
            $condaRoots.Add((Join-Path $homeBase 'miniconda3'))
        }
    }
    foreach ($condaRoot in $condaRoots) {
        Add-Python (Join-Path $condaRoot 'envs\Ember\python.exe')
        Add-Python (Join-Path $condaRoot 'python.exe')
    }
    foreach ($candidate in $candidates) {
        $selected = Test-Python $candidate
        if ($selected) { break }
    }
    if (-not $selected) {
      foreach ($registryRoot in @('HKCU:\Software\Python', 'HKLM:\Software\Python', 'HKLM:\Software\WOW6432Node\Python')) {
        try {
            # PEP 514 uses Company/Tag/InstallPath. Do not recursively walk
            # third-party registry subtrees; some contain thousands of keys.
            foreach ($company in @(Get-ChildItem $registryRoot -ErrorAction SilentlyContinue)) {
              foreach ($tag in @(Get-ChildItem $company.PSPath -ErrorAction SilentlyContinue)) {
                $values = Get-ItemProperty (Join-Path $tag.PSPath 'InstallPath') -ErrorAction SilentlyContinue
                if ($values -and $values.ExecutablePath) { Add-Python $values.ExecutablePath }
                elseif ($values -and $values.'(default)') { Add-Python (Join-Path $values.'(default)' 'python.exe') }
              }
            }
        } catch { } # Restricted registries must not block direct executable checks.
      }
      foreach ($candidate in $candidates) {
        $selected = Test-Python $candidate
        if ($selected) { break }
      }
    }
    if (-not $selected) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($launcher) {
            # Explicit versions avoid a broken/free-threaded default registration.
            foreach ($version in @('-3.11', '-3.12', '-3.13', '-3.14')) {
                $selected = Test-Python $launcher.Source @($version)
                if ($selected) { break }
            }
        }
    }
    if (-not $selected) {
        Write-Host 'No working standard Python 3.11+ was found. Broken paths and Store aliases were skipped.'
        Write-Host 'Install Python, or set EMBER_PYTHON to the full path of a working python.exe.'
        exit 1
    }
}

Write-Host ('Using Python {0}: {1}' -f ($selected.version -join '.'), $selected.executable)
if ($DemoArguments -contains '--check-python') { exit 0 }
& $selected.executable -I (Join-Path $demoRoot 'launch.py') @DemoArguments
exit $LASTEXITCODE
