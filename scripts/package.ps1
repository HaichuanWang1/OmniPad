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

.PARAMETER BuildExe
    额外用 PyInstaller 生成 server_ui.exe 一并打包，需要先 pip install pyinstaller。
    默认关闭：exe 体积大、未签名会触发 SmartScreen，且多数用户已装 Python。

.EXAMPLE
    pwsh scripts/package.ps1 -Target server

.EXAMPLE
    pwsh scripts/package.ps1 -BuildExe
#>
[CmdletBinding()]
param(
    [ValidateSet('server', 'client', 'all')]
    [string]$Target = 'all',

    [string]$Version,

    [switch]$BuildExe
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ServerDir = Join-Path $RepoRoot 'server'
$DistDir = Join-Path $RepoRoot 'dist'
$ClientDir = Join-Path $RepoRoot 'client'

# 发布包内容白名单。新增运行时模块必须加到这里，否则脚本会报错提醒你。
$RuntimeFiles = @(
    'server.py'             # 无头模式入口
    'server_ui.py'          # GUI 入口
    'handlers.py'           # 协议处理器（两个入口共用）
    'pairing.py'            # 配对令牌
    'protocol.py'           # 消息分派
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
    param([string]$ReleaseVersion)

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

        # 用户拿到 zip 时最需要知道的三件事：怎么启动、要不要装东西、令牌在哪。
        $readme = @"
OmniPad 服务端 v$ReleaseVersion
================================

运行要求：Windows 10/11 + Python 3.10 或更高版本。

启动方式（在本目录下执行）：
    python server_ui.py      图形界面（推荐）
    python server.py         无头模式

首次启动会生成配对令牌，显示在窗口顶部，并在手机端首次连接时填入。
令牌保存在 pairing_token.txt，删除该文件即可重新生成。

本服务端只依赖 Python 标准库，不需要 pip install。
"@
        # 用 UTF-8 BOM 写出，否则记事本打开中文会乱码。
        $readmePath = Join-Path $stage '使用说明.txt'
        [System.IO.File]::WriteAllText($readmePath, $readme, (New-Object System.Text.UTF8Encoding $true))

        if ($BuildExe) {
            Write-Host '正在用 PyInstaller 生成 server_ui.exe ...'
            Push-Location $ServerDir
            try {
                & python -m PyInstaller --noconfirm --onefile --windowed `
                    --name server_ui --distpath $stage --workpath (Join-Path $stage '_build') `
                    --specpath (Join-Path $stage '_build') server_ui.py
                if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（退出码 $LASTEXITCODE）" }
            }
            finally { Pop-Location }

            Remove-Item (Join-Path $stage '_build') -Recurse -Force -ErrorAction SilentlyContinue
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
            # 排序保证条目顺序稳定，不受文件系统枚举顺序影响。
            foreach ($file in Get-ChildItem $SourceDir -File | Sort-Object Name) {
                $entry = $archive.CreateEntry(
                    $file.Name, [System.IO.Compression.CompressionLevel]::Optimal)
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
if ($Target -in @('server', 'all')) { $produced += New-ServerPackage $releaseVersion }
if ($Target -in @('client', 'all')) { $produced += New-ClientPackage $releaseVersion }

Write-Host ''
Write-Host '产出：' -ForegroundColor Green
foreach ($f in $produced) {
    $item = Get-Item $f
    $hash = (Get-FileHash $f -Algorithm SHA256).Hash
    Write-Host ("  {0}" -f $item.Name)
    Write-Host ("    {0:N0} 字节  SHA256 {1}" -f $item.Length, $hash)
}
