[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$collectorDir = [IO.Path]::GetFullPath((Join-Path $scriptDir ".."))
$versionFile = Join-Path $collectorDir "wh6_collector\version.py"
$versionMatch = Select-String -LiteralPath $versionFile -Pattern '^\s*CLIENT_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw "无法从版本源读取 CLIENT_VERSION：$versionFile"
}
$version = $versionMatch.Matches[0].Groups[1].Value
$releaseDir = Join-Path $collectorDir ("releases\" + $version)
$releaseName = "WH6成交采集器-$version.exe"
$releasePath = Join-Path $releaseDir $releaseName
$hashPath = Join-Path $releaseDir "$releaseName.sha256"
$readmePath = Join-Path $releaseDir "README-$version.txt"
$venvDir = Join-Path $collectorDir ".build-venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

function Stop-Build([string]$Message) {
    Write-Host "`n[失败] $Message" -ForegroundColor Red
    if (-not $NoPause) {
        Read-Host "按回车关闭窗口"
    }
    exit 1
}

function Invoke-Native([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败（退出码 $LASTEXITCODE）：$FilePath"
    }
}

function Find-Python311 {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "${env:ProgramFiles(x86)}\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand) {
        try {
            $selected = (& $pyCommand.Source -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($selected) {
                return $selected.ToString().Trim()
            }
        } catch {
            # Continue with the ordinary python command lookup.
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        try {
            $pythonVersion = (& $pythonCommand.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Select-Object -First 1)
            if ($pythonVersion -eq "3.11") {
                return $pythonCommand.Source
            }
        } catch {
            # Treat an unusable python command as missing.
        }
    }
    return $null
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "当前 Windows 不是 64 位系统，暂不支持构建 Windows x64 便携程序。"
    }

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    Write-Host "[1/5] 检查 Python 3.11 x64" -ForegroundColor Cyan
    $python = Find-Python311
    if (-not $python -and -not $SkipDependencyInstall) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            $answer = Read-Host "未找到 Python 3.11。是否使用 winget 自动安装（Y/N）"
            if ($answer -match "^(y|yes)$") {
                Invoke-Native $winget.Source @(
                    "install", "--id", "Python.Python.3.11", "--exact", "--scope", "user", "--silent",
                    "--accept-package-agreements", "--accept-source-agreements"
                )
                $python = Find-Python311
            }
        }
    }
    if (-not $python) {
        throw "未找到 Python 3.11。请在构建环境安装 Python 3.11 x64 后重试。"
    }
    $pythonVersion = (& $python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Select-Object -First 1)
    if ($pythonVersion -ne "3.11") {
        throw "找到的 Python 版本为 $pythonVersion，不是要求的 3.11。"
    }

    Write-Host "[2/5] 准备构建环境" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Invoke-Native $python @("-m", "venv", $venvDir)
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Python 虚拟环境创建失败：$venvDir"
    }
    $pythonBits = (& $venvPython -c "import struct; print(struct.calcsize('P') * 8)" 2>$null | Select-Object -First 1)
    if ($pythonBits -ne "64") {
        throw "构建 Python 为 $pythonBits 位，不是 Windows x64 所需的 64 位。"
    }
    Invoke-Native $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip")
    Invoke-Native $venvPython @(
        "-m", "pip", "install", "--disable-pip-version-check", "-r",
        (Join-Path $collectorDir "requirements-windows.txt")
    )

    Write-Host "[3/5] 生成单文件便携程序" -ForegroundColor Cyan
    $buildDir = Join-Path $collectorDir "build"
    $distDir = Join-Path $collectorDir "dist"
    if (Test-Path -LiteralPath $buildDir) {
        Remove-Item -LiteralPath $buildDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $distDir) {
        Remove-Item -LiteralPath $distDir -Recurse -Force
    }
    Push-Location $collectorDir
    try {
        Invoke-Native $venvPython @(
            "-m", "PyInstaller", "--clean", "--noconfirm",
            (Join-Path $collectorDir "WH6成交采集器.spec")
        )
    } finally {
        Pop-Location
    }
    $bundleExe = Join-Path $collectorDir "dist\WH6成交采集器.exe"
    if (-not (Test-Path -LiteralPath $bundleExe)) {
        throw "PyInstaller 未生成预期文件：$bundleExe"
    }

    Write-Host "[4/5] 写入版本化发布目录和 SHA-256" -ForegroundColor Cyan
    Copy-Item -LiteralPath $bundleExe -Destination $releasePath -Force
    $hash = (Get-FileHash -LiteralPath $releasePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $hashPath -Value "$hash *$releaseName" -Encoding ASCII
    Copy-Item -LiteralPath (Join-Path $scriptDir "README.md") -Destination $readmePath -Force

    Write-Host "[5/5] 执行安全检查" -ForegroundColor Cyan
    $forbidden = @("service_role", "DATABASE_URL")
    $scriptPath = [IO.Path]::GetFullPath($MyInvocation.MyCommand.Definition)
    $scanFiles = Get-ChildItem -LiteralPath $collectorDir -Recurse -File |
        Where-Object {
            $_.FullName -ne $scriptPath -and
            $_.FullName -notlike "$venvDir\*" -and
            $_.FullName -notlike "$(Join-Path $collectorDir 'build')\*" -and
            $_.FullName -notlike "$(Join-Path $collectorDir 'dist')\*" -and
            $_.FullName -notlike "$releaseDir\*" -and
            $_.Extension -in @(".py", ".ps1", ".cmd", ".spec", ".txt")
        }
    foreach ($file in $scanFiles) {
        $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop
        foreach ($needle in $forbidden) {
            if ($content -like "*$needle*") {
                throw "安全校验失败：$needle 出现在 $($file.FullName)"
            }
        }
    }

    Write-Host "`n构建完成。" -ForegroundColor Green
    Write-Host "便携程序：$releasePath"
    Write-Host "SHA-256：$hash"
    Write-Host "目标电脑只需双击该 EXE；首次运行选择 WH6 Record 并输入 Web 连接码。"
} catch {
    Stop-Build $_.Exception.Message
}

if (-not $NoPause) {
    Read-Host "按回车关闭窗口"
}
exit 0
