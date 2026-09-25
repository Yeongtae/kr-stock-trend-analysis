[CmdletBinding()]
param(
    [string]$EndDate
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

function Find-CommandPath([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return $null
}

$pythonPath = $null
$projectPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $projectPython) {
    $pythonPath = $projectPython
}
if (-not $pythonPath -and $env:USERPROFILE) {
    $codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $codexPython) {
        $pythonPath = $codexPython
    }
}
if (-not $pythonPath) {
    $pythonPath = Find-CommandPath "python"
}

$pythonArgs = @()
if (-not $pythonPath) {
    $pythonPath = Find-CommandPath "py"
    if ($pythonPath) {
        $pythonArgs += "-3"
    }
}
if (-not $pythonPath) {
    throw "Python을 찾지 못했습니다. .\scripts\setup_shared.ps1를 실행하거나 Python 3을 설치하세요."
}

$invokeArgs = @($pythonArgs + (Join-Path $root "scripts\weekly_pipeline.py"))
if ($EndDate) {
    $invokeArgs += @("--end-date", $EndDate)
}

& $pythonPath @invokeArgs
exit $LASTEXITCODE
