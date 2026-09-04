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
# Current repository artifact path: collector\releases\0.2.1
$releaseDir = Join-Path $collectorDir ("releases\" + $version)
$releaseRelativeDir = "..\releases\0.2.1"
if ($version -ne "0.2.1") {
    $releaseRelativeDir = "..\releases\$version"
}
$venvDir = Join-Path $collectorDir ".build-venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$setupName = "WH6成交采集器-0.2.1-Setup.exe"
if ($version -ne "0.2.1") {
    $setupName = "WH6成交采集器-{0}-Setup.exe" -f $version
}
$setupPath = Join-Path $releaseDir $setupName

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
            $version = (& $pythonCommand.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Select-Object -First 1)
            if ($version -eq "3.11") {
                return $pythonCommand.Source
            }
        } catch {
            # Treat an unusable python command as missing.
        }
    }
    return $null
}

function Find-Iscc {
    if ($env:WH6_ISCC_PATH -and (Test-Path -LiteralPath $env:WH6_ISCC_PATH)) {
        return $env:WH6_ISCC_PATH
    }
    $command = Get-Command iscc -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    return $null
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "当前 Windows 不是 64 位系统，暂不支持构建 Windows x64 安装包。"
    }

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    Write-Host "[1/6] 检查 Python 3.11" -ForegroundColor Cyan
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
        throw "未找到 Python 3.11。请安装 Python 3.11 x64 后重新双击本脚本，或确保 winget 可用。"
    }
    $pythonVersion = (& $python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Select-Object -First 1)
    if ($pythonVersion -ne "3.11") {
        throw "找到的 Python 版本为 $pythonVersion，不是要求的 3.11。请安装 Python 3.11 x64 后重试。"
    }

    Write-Host "[2/6] 准备构建环境" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Invoke-Native $python @("-m", "venv", $venvDir)
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Python 虚拟环境创建失败：$venvDir"
    }
    Invoke-Native $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip")
    Invoke-Native $venvPython @(
        "-m", "pip", "install", "--disable-pip-version-check", "-r",
        (Join-Path $collectorDir "requirements-windows.txt")
    )

    Write-Host "[3/6] 生成 Windows 自包含程序" -ForegroundColor Cyan
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

    Write-Host "[4/6] 检查 Inno Setup 6" -ForegroundColor Cyan
    $iscc = Find-Iscc
    if (-not $iscc -and -not $SkipDependencyInstall) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            $answer = Read-Host "未找到 Inno Setup 6。是否使用 winget 自动安装（Y/N）"
            if ($answer -match "^(y|yes)$") {
                Invoke-Native $winget.Source @(
                    "install", "--id", "JRSoftware.InnoSetup", "--exact", "--scope", "user", "--silent",
                    "--accept-package-agreements", "--accept-source-agreements"
                )
                $iscc = Find-Iscc
            }
        }
    }
    if (-not $iscc) {
        throw "未找到 Inno Setup 6 编译器。请安装 Inno Setup 6，或设置 WH6_ISCC_PATH 后重试。"
    }

    Write-Host "[5/6] 封装 $setupName" -ForegroundColor Cyan
    Push-Location $scriptDir
    try {
        Invoke-Native $iscc @(
            "/DMyAppVersion=$version",
            "/DMyAppReleaseDir=$releaseRelativeDir",
            "/DMyAppOutputBaseFilename=WH6成交采集器-$version-Setup",
            (Join-Path $scriptDir "WH6成交采集器.iss")
        )
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $setupPath)) {
        throw "Inno Setup 未生成预期文件：$setupPath"
    }

    Write-Host "[6/6] 校验安装包和来源文件" -ForegroundColor Cyan
    $forbidden = @("ltm-web-gt13.onrender.com", "service_role", "DATABASE_URL")
    $scriptPath = [IO.Path]::GetFullPath($MyInvocation.MyCommand.Definition)
    $scanFiles = Get-ChildItem -LiteralPath $collectorDir -Recurse -File |
        Where-Object {
            $_.FullName -ne $scriptPath -and
            $_.FullName -notlike "$venvDir\*" -and
            $_.FullName -notlike "$(Join-Path $collectorDir 'build')\*" -and
            $_.FullName -notlike "$(Join-Path $collectorDir 'dist')\*" -and
            $_.FullName -notlike "$releaseDir\*" -and
            $_.Extension -in @(".py", ".ps1", ".cmd", ".iss", ".spec", ".txt")
        }
    foreach ($file in $scanFiles) {
        $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop
        foreach ($needle in $forbidden) {
            if ($content -like "*$needle*") {
                throw "安全校验失败：$needle 出现在 $($file.FullName)"
            }
        }
    }
    $hash = (Get-FileHash -LiteralPath $setupPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $releaseDir "$setupName.sha256") -Value "$hash *$setupName" -Encoding ASCII
    Copy-Item -LiteralPath (Join-Path $scriptDir "README.md") -Destination (Join-Path $releaseDir "README-$version.txt") -Force

    Write-Host "`n构建完成。" -ForegroundColor Green
    Write-Host "安装包：$setupPath"
    Write-Host "SHA-256：$hash"
    Write-Host "下一步：双击安装包；首次启动时选择 WH6 Record 目录并输入 Web 测试版连接码。"
} catch {
    Stop-Build $_.Exception.Message
}

if (-not $NoPause) {
    Read-Host "按回车关闭窗口"
}
exit 0
