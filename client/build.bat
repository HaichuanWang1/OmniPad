@echo off
setlocal enabledelayedexpansion

set GRADLE_VERSION=8.5
set GRADLE_DIR=%USERPROFILE%\.gradle\wrapper\dists\gradle-%GRADLE_VERSION%-bin

if not exist "%GRADLE_DIR%" (
    echo Downloading Gradle %GRADLE_VERSION%...
    mkdir "%GRADLE_DIR%" 2>nul
    powershell -Command "Invoke-WebRequest -Uri 'https://mirrors.cloud.tencent.com/gradle/gradle-%GRADLE_VERSION%-bin.zip' -OutFile '%TEMP%\gradle-%GRADLE_VERSION%-bin.zip'"
    powershell -Command "Expand-Archive -Path '%TEMP%\gradle-%GRADLE_VERSION%-bin.zip' -DestinationPath '%GRADLE_DIR%' -Force"
)

for /d %%i in ("%GRADLE_DIR%\gradle-%GRADLE_VERSION%*") do set GRADLE_HOME=%%i
if not defined GRADLE_HOME (
    echo ERROR: Cannot find Gradle distribution
    exit /b 1
)

echo Gradle home: %GRADLE_HOME%
"%GRADLE_HOME%\bin\gradle" assembleDebug -p "%~dp0" --no-daemon
