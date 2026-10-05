#Requires -Version 5.1
<#
.SYNOPSIS
    产出符合发布规范的 dist/ 资产。

.DESCRIPTION
    版本号唯一来源是仓库根目录的 VERSION 文件，本脚本与 Gradle 都读它，
    因此不会再出现「APK 叫 app-release.apk、zip 叫 server-vX.zip」这类漂移。

    服务端包的内容由 RUNTIME_FILES 白名单决定，而不是「把 server/ 整个拷过去」。
    历史上正是手工拷贝让 test_client.py（开发脚本）和 0 字节的 __init__.py
    混进了每一个发布包。为防新增模块被静默漏掉，脚本会检查 server/ 下每个
    .py 是否已被明确归类，未归类的文件会让脚本报错退出 —— 宁可停下，
    也不要发出一个残包。

.PARAMETER Target
    server / client / all，默认 all。

.PARAMETER Version
    覆盖版本号，默认读 VERSION 文件。

.PARAMETER SkipExe
    跳过 PyInstaller 打包 OmniPad-Server.exe。

    默认**会**打包：exe 才是普通用户实际拿到的东西（不需要装 Python），
    把它排除在默认路径之外，等于发布流程里最关键的一步从来没被验证过。
    需要快速迭代或离线时才用它跳过。

.EXAMPLE
    pwsh scripts/package.ps1 -Target server

.EXAMPLE
    pwsh scripts/package.ps1 -Target server -SkipExe
#>
[CmdletBinding()]
param(
    [ValidateSet('server', 'client', 'all')]
    [string]$Target = 'all',

    [string]$Version,

    [switch]$SkipExe
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot 'server'
$DistDir = Join-Path $RepoRoot 'dist'
$ClientDir = Join-Path $RepoRoot 'client'

# 发布包内容白名单。新增运行时模块必须加到这里，否则脚本会报错提醒你。
$RuntimeFiles = @(
    'server.py'             # 唯一入口：GUI / 无头 / --status / --stop
    'server_ui.py'          # Tkinter GUI 实现
    'handlers.py'           # 协议处理器（两个入口共用）
    'pairing.py'            # 配对令牌
    'protocol.py'           # 消息分派
    'state.py'              # 连接状态机与运行状态快照
    'runtime.py'            # 数据目录 / 状态文件 / 单实例 / 日志
    'control.py'            # 本机控制通道（--status / --stop 靠它）
    'tray.py'               # 系统托盘图标（纯 ctypes）
    'tcp_server.py'         # TCP 服务器
    'input_controller.py'   # SendInput 注入
    'requirements.txt'
)

# 明确不随发布包分发的文件。任何既不在此、也不在白名单里的 .py 都会中断打包。
$DevOnlyPatterns = @(
    '^test_.*\.py$'         # 测试与手工联调脚本
)

function Get-ReleaseVersion {
    if ($Version) { return $Version.Trim() }

    $versionFile = Join-Path $RepoRoot 'VERSION'
    if (-not (Test-Path $versionFile)) {
        throw "找不到版本号来源：$versionFile"
    }
    $v = (Get-Content $versionFile -Raw).Trim()
    if (-not $v) { throw "$versionFile 是空的" }
    return $v
}

function Assert-ServerFilesClassified {
    <# 每个 .py 要么随包发布，要么被显式排除；否则报错。 #>
    $unclassified = @()
    foreach ($f in Get-ChildItem $ServerDir -Filter '*.py' -File) {
        if ($RuntimeFiles -contains $f.Name) { continue }
        if ($DevOnlyPatterns | Where-Object { $f.Name -match $_ }) { continue }
        $unclassified += $f.Name
    }
    if ($unclassified.Count -gt 0) {
        throw @"
server/ 下有未归类的 Python 文件，拒绝打包：
  $($unclassified -join ', ')

请把它们加入本脚本的 `$RuntimeFiles（随包发布）或 `$DevOnlyPatterns（仅开发用），
以免新模块被静默漏出发布包，或开发脚本被静默打进去。
"@
    }
}

function New-ServerPackage {
    param([string]$ReleaseVersion, [switch]$SkipExe)

    Assert-ServerFilesClassified

    foreach ($name in $RuntimeFiles) {
        $src = Join-Path $ServerDir $name
        if (-not (Test-Path $src)) { throw "白名单里的文件不存在：server/$name" }
    }

    $stage = Join-Path ([System.IO.Path]::GetTempPath()) "omnipad-pkg-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $stage -Force | Out-Null

    try {
        foreach ($name in $RuntimeFiles) {
            Copy-Item (Join-Path $ServerDir $name) (Join-Path $stage $name)
        }

        # 托盘图标与 exe 图标。源码运行（python server.py）时托盘也要用，
        # 所以它必须随包分发，不能只塞进 exe。
        $assets = Join-Path $ServerDir 'assets'
        if (Test-Path $assets) {
            Copy-Item $assets (Join-Path $stage 'assets') -Recurse
        }

        # 用户拿到 zip 时最需要知道的三件事：怎么启动、要不要装东西、令牌在哪。
        $readme = @"
OmniPad 服务端 v$ReleaseVersion
================================

运行要求：Windows 10/11（64 位）。

启动方式（二选一，都在本目录下）：

    OmniPad-Server.exe        双击即可，不需要装 Python（推荐）
    python server.py          需要 Python 3.10 或更高版本

同一个 exe 也能当命令行工具用：

    OmniPad-Server.exe --status           查看运行状态
    OmniPad-Server.exe --status --json    机器可读的状态（JSON）
    OmniPad-Server.exe --stop             停止正在运行的实例
    OmniPad-Server.exe --headless         无头模式（不开窗口）
    OmniPad-Server.exe --port 5801        换一个端口

退出码：0 成功 / 1 失败 / 2 已在运行 / 3 未在运行。

首次启动会生成配对令牌，显示在窗口顶部（也可用 --status 查看），
在手机端首次连接时填入。令牌保存在数据目录的 pairing_token.txt，
删除该文件即可重新生成（手机端需要重新配对）。

数据目录：
    OmniPad-Server.exe   %APPDATA%\OmniPad
    python server.py     本目录

数据目录里有 pairing_token.txt（配对令牌）、server_status.json（运行状态）、
logs\server.log（日志）。状态文件是纯文本，任何时候都能看出服务端在不在跑、
谁连着、为什么断开。

Windows 可能弹出 SmartScreen 提示 —— 本程序没有做代码签名，
点「更多信息 → 仍要运行」即可。

本服务端只依赖 Python 标准库，源码运行不需要 pip install。
"@
        # 用 UTF-8 BOM 写出，否则记事本打开中文会乱码。
        $readmePath = Join-Path $stage '使用说明.txt'
        [System.IO.File]::WriteAllText($readmePath, $readme, (New-Object System.Text.UTF8Encoding $true))

        if (-not $SkipExe) {
            New-ServerExe -ReleaseVersion $ReleaseVersion -Stage $stage
        }

        $outFile = Join-Path $DistDir "omnipad-server-v$ReleaseVersion.zip"
        Remove-Item $outFile -Force -ErrorAction SilentlyContinue
        New-DeterministicZip -SourceDir $stage -Destination $outFile

        return $outFile
    }
    finally {
        Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function New-ServerExe {
    <#
        打两个 exe：

        OmniPad-Server.exe      图形子系统（--windowed）。双击即用，不弹黑框。
        OmniPad-Server-CLI.exe  控制台子系统（--console）。命令行用。

        为什么不是一个：Windows 的子系统标志是二选一，而两种用法对它的要求正好相反。
        图形子系统的程序，cmd / PowerShell **不会等待它结束** ——
        `OmniPad-Server.exe --status --json | ConvertFrom-Json` 拿到的是空，
        退出码也拿不到。控制台子系统的程序则会弹出一个黑框。

        试过「控制台子系统 + 启动时判断要不要隐藏黑框」：靠
        GetConsoleProcessList 判断的启发式在本机实测不可靠（Explorer 双击给的是 2
        而不是 1），靠父进程名判断又会被 PyInstaller onefile 的自我重启挡住。
        与其赌一个启发式，不如老实地打两个 exe。

        CLI 那个排除了 tkinter（省约 3 MB），所以它打不开图形界面 ——
        无参数运行时会给一句明确提示，而不是抛 ImportError。
    #>
    param([string]$ReleaseVersion, [string]$Stage)

    Write-Host '正在用 PyInstaller 生成 exe ...'

    & python -c "import PyInstaller" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw @'
没有找到 PyInstaller，无法生成 exe。安装：
    python -m pip install pyinstaller
（离线环境可以用 -SkipExe 跳过，但那样发出去的包需要用户自己装 Python。）
'@
    }

    $buildDir = Join-Path $Stage '_build'
    New-Item -ItemType Directory -Path $buildDir -Force | Out-Null

    $versionFile = Join-Path $buildDir 'version_info.txt'
    & python (Join-Path $PSScriptRoot 'make_version_info.py') $ReleaseVersion $versionFile | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "生成版本资源失败（退出码 $LASTEXITCODE）" }

    $icon = (Resolve-Path (Join-Path $ServerDir 'assets\omnipad.ico')).Path
    $entry = (Resolve-Path (Join-Path $ServerDir 'server.py')).Path
    $paths = (Resolve-Path $ServerDir).Path
    $assets = (Resolve-Path (Join-Path $ServerDir 'assets')).Path
    $versionSource = (Resolve-Path (Join-Path $RepoRoot 'VERSION')).Path

    # PyInstaller 认这个变量来做可复现构建：PE 头与归档条目的时间戳都取它，
    # 否则同一个源码每次产出的 exe 字节都不同，发布说明里的 SHA256 就没意义了。
    # 2000-01-01，与 zip 里固定的条目时间一致。
    #
    # PYTHONHASHSEED 同样必须固定：PyInstaller 内部用 set 收集模块，字符串哈希
    # 每个进程都不同，模块在 PYZ 归档里的顺序就跟着变 —— 两次构建能差出一千多字节。
    $previousEpoch = $env:SOURCE_DATE_EPOCH
    $previousHashSeed = $env:PYTHONHASHSEED
    $env:SOURCE_DATE_EPOCH = '946684800'
    $env:PYTHONHASHSEED = '0'
    try {
        $builds = @(
            @{ Name = 'OmniPad-Server';     Console = $false; Excludes = @() },
            @{ Name = 'OmniPad-Server-CLI'; Console = $true;  Excludes = @('tkinter', '_tkinter') }
        )

        foreach ($build in $builds) {
            $arguments = @(
                '-m', 'PyInstaller', '--noconfirm', '--onefile', '--clean',
                '--name', $build.Name,
                '--icon', $icon,
                '--version-file', $versionFile,
                '--add-data', "$versionSource;.",
                '--add-data', "$assets;assets",
                '--paths', $paths,
                '--distpath', $Stage,
                '--workpath', $buildDir,
                '--specpath', $buildDir
            )
            if (-not $build.Console) { $arguments += '--windowed' } else { $arguments += '--console' }
            foreach ($module in $build.Excludes) { $arguments += @('--exclude-module', $module) }
            $arguments += $entry

            # Out-Host 是必须的：直接调用会让 PyInstaller 的 stdout 流进管道，
            # 混进本函数的返回值里，调用方拿到的就不是文件路径了。
            & python @arguments 2>&1 | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（$($build.Name)，退出码 $LASTEXITCODE）" }

            $produced = Join-Path $Stage "$($build.Name).exe"
            if (-not (Test-Path $produced)) { throw "PyInstaller 没有产出 $produced" }
            Write-Host ("  {0}  {1:N0} 字节" -f "$($build.Name).exe", (Get-Item $produced).Length)
        }
    }
    finally {
        if ($null -eq $previousEpoch) { Remove-Item Env:\SOURCE_DATE_EPOCH -ErrorAction SilentlyContinue }
        else { $env:SOURCE_DATE_EPOCH = $previousEpoch }
        if ($null -eq $previousHashSeed) { Remove-Item Env:\PYTHONHASHSEED -ErrorAction SilentlyContinue }
        else { $env:PYTHONHASHSEED = $previousHashSeed }
    }

    Remove-Item $buildDir -Recurse -Force -ErrorAction SilentlyContinue
}

function New-DeterministicZip {
    <#
        压缩时不写入当前时间，条目时间戳固定为 2000-01-01。

        用 Compress-Archive 会把打包时刻写进条目，导致同样的源码每次产出不同的
        zip 和不同的 SHA256 —— 那样发布说明里的校验值就没有意义了，也无法靠
        重新构建来核对已发布的包。
    #>
    param([string]$SourceDir, [string]$Destination)

    Add-Type -AssemblyName System.IO.Compression -ErrorAction SilentlyContinue

    # ZIP 格式的时间戳下限是 1980，取 2000 留足余量。
    $fixedTime = [System.DateTimeOffset]::new(2000, 1, 1, 0, 0, 0, [System.TimeSpan]::Zero)

    $fileStream = [System.IO.File]::Create($Destination)
    try {
        $archive = New-Object System.IO.Compression.ZipArchive(
            $fileStream, [System.IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            # 递归 + 按完整路径排序：条目顺序稳定，不受文件系统枚举顺序影响。
            # 目录也要走，assets/ 就在子目录里。
            foreach ($file in Get-ChildItem $SourceDir -File -Recurse | Sort-Object FullName) {
                $relative = $file.FullName.Substring($SourceDir.Length).TrimStart('\', '/')
                $relative = $relative -replace '\\', '/'
                $entry = $archive.CreateEntry(
                    $relative, [System.IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = $fixedTime
                $entryStream = $entry.Open()
                try {
                    $input = [System.IO.File]::OpenRead($file.FullName)
                    try { $input.CopyTo($entryStream) } finally { $input.Dispose() }
                }
                finally { $entryStream.Dispose() }
            }
        }
        finally { $archive.Dispose() }
    }
    finally { $fileStream.Dispose() }
}

function New-ClientPackage {
    param([string]$ReleaseVersion)

    $gradlew = Join-Path $ClientDir 'gradlew.bat'
    if (-not (Test-Path $gradlew)) { throw "找不到 $gradlew" }

    Write-Host '正在构建 release APK ...'
    Push-Location $ClientDir
    try {
        # gradlew.bat 在本机即使 BUILD SUCCESSFUL 也会返回 1，所以判定输出而不是退出码。
        & $gradlew assembleRelease 2>&1 | Tee-Object -Variable gradleOutput | Out-Null
        if (-not ($gradleOutput -match 'BUILD SUCCESSFUL')) {
            $gradleOutput | Select-Object -Last 40 | ForEach-Object { Write-Host $_ }
            throw 'Gradle 构建失败'
        }
    }
    finally { Pop-Location }

    $built = Join-Path $ClientDir 'app\build\outputs\apk\release\app-release.apk'
    if (-not (Test-Path $built)) { throw "Gradle 没有产出 $built" }

    $outFile = Join-Path $DistDir "OmniPad-v$ReleaseVersion.apk"
    Copy-Item $built $outFile -Force
    return $outFile
}

# ---- 主流程 ----

$releaseVersion = Get-ReleaseVersion
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

Write-Host "OmniPad 打包 v$releaseVersion (target=$Target)" -ForegroundColor Cyan

$produced = @()
if ($Target -in @('server', 'all')) { $produced += New-ServerPackage $releaseVersion -SkipExe:$SkipExe }
if ($Target -in @('client', 'all')) { $produced += New-ClientPackage $releaseVersion }

Write-Host ''
Write-Host '产出：' -ForegroundColor Green
foreach ($f in $produced) {
    $item = Get-Item $f
    $hash = (Get-FileHash $f -Algorithm SHA256).Hash
    Write-Host ("  {0}" -f $item.Name)
    Write-Host ("    {0:N0} 字节  SHA256 {1}" -f $item.Length, $hash)
}
