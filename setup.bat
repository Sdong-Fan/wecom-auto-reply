@echo off
chcp 65001 >nul
title 企业微信智能客服 - 安装

echo =======================================
echo   企业微信智能客服 - 一键安装
echo =======================================
echo.

set "APP_DIR=%~dp0"
set "APP_DIR=%APP_DIR:~0,-1%"

rem ── 检查 .env ──────────────────────────────────────────
if not exist "%APP_DIR%\.env" (
    echo [1/3] 创建 .env 配置文件...
    copy "%APP_DIR%\.env.example" "%APP_DIR%\.env" >nul
    echo   请编辑 .env 填入你的 DEEPSEEK_API_KEY：
    start notepad "%APP_DIR%\.env"
    echo.
    echo   填好 API key 后保存关闭记事本，然后按任意键继续...
    pause >nul
) else (
    echo [1/3] .env 已存在，跳过
)

rem ── 创建桌面快捷方式 ────────────────────────────────────
echo [2/3] 创建桌面快捷方式...

set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT=%DESKTOP%\企业微信智能客服.url"

(
echo [InternetShortcut]
echo URL=file:///%APP_DIR:\=/%/启动.bat
echo IconIndex=0
echo IconFile=%APP_DIR%\启动.exe
) > "%SHORTCUT%"

echo   桌面快捷方式已创建

echo [3/3] 安装完成！
echo.
echo =======================================
echo   安装完成！双击桌面上的 "企业微信智能客服" 启动。
echo =======================================
echo.
pause