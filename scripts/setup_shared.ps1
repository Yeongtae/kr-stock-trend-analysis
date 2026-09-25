[CmdletBinding()]
param()

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

$gitPath = Find-CommandPath "git"
if (-not $gitPath) {
    throw "Git을 찾지 못했습니다. Git for Windows를 설치한 뒤 다시 실행하세요."
}

if (-not (Test-Path -LiteralPath (Join-Path $root ".git"))) {
    & $gitPath init -b main
}

# .gitattributes가 줄바꿈을 결정하므로 Windows에서는 checkout만 CRLF로 둡니다.
$gitConfigWritable = $true
& $gitPath config core.autocrlf true 2>$null
if ($LASTEXITCODE -ne 0) {
    $gitConfigWritable = $false
}
& $gitPath config pull.rebase false 2>$null
if ($LASTEXITCODE -ne 0) {
    $gitConfigWritable = $false
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
if (-not $pythonPath) {
    $pyLauncher = Find-CommandPath "py"
    if ($pyLauncher) {
        $pythonPath = $pyLauncher
    }
}
if (-not $pythonPath) {
    throw "Python을 찾지 못했습니다. Python 3을 설치한 뒤 다시 실행하세요."
}

if ([IO.Path]::GetFileName($pythonPath) -eq "py.exe") {
    & $pythonPath -3 --version
} else {
    & $pythonPath --version
}

Write-Host "공유 작업환경 확인 완료: $root"
if (-not $gitConfigWritable) {
    Write-Warning "현재 .git 설정 파일을 쓸 수 없습니다. 복제한 다른 PC에서 다시 실행하면 Git 설정이 저장됩니다."
}
Write-Host "다음 단계: 원격 저장소를 등록한 뒤 git push -u origin main 을 실행하세요."
