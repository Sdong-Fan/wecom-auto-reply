@echo off
chcp 65001 >nul
title 企业微信智能客服

rem ── 切到脚本所在目录 ────────────────────────────────────
cd /d "%~dp0"

rem ── 检查 .env（第一次运行自动生成，不再直接退出）──────────
if not exist ".env" (
    if exist ".env.example" (
        echo [提示] 第一次运行：已按 .env.example 生成 .env
        echo        稍后在界面「设置」里填自己的模型 API Key 即可。
        copy /y ".env.example" ".env" >nul
    ) else (
        echo [错误] 未找到 .env 与 .env.example！
        echo.
        echo 请复制 .env.example 为 .env，然后填入你的 DEEPSEEK_API_KEY：
        echo   copy .env.example .env
        echo   notepad .env
        echo.
        pause
        exit /b 1
    )
)

rem ── 创建运行目录 ────────────────────────────────────────
if not exist "data" mkdir data
if not exist "data\chat_raw" mkdir data\chat_raw
if not exist "data\context" mkdir data\context
if not exist "data\pending" mkdir data\pending
if not exist "data\qdrant" mkdir data\qdrant
if not exist "data\state" mkdir data\state
if not exist "logs" mkdir logs

rem ── 公共环境变量 ────────────────────────────────────────
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_ENABLE_HF_TRANSFER=1
set KMP_DUPLICATE_LIB_OK=TRUE

rem == 模式一：已打包，存在 启动.exe ==
if exist "%~dp0启动.exe" (
    set TCL_LIBRARY=%~dp0_internal\_tcl_data
    set TK_LIBRARY=%~dp0_internal\_tk_data
    set PATH=%~dp0_internal;%~dp0;%PATH%
    echo 正在启动企业微信智能客服，打包模式...
    echo.
    "%~dp0启动.exe"
    set RESULT=%ERRORLEVEL%
    goto checked
)

rem == 模式二：源码模式，使用本目录 .venv ==
if exist "%~dp0.venv\Scripts\python.exe" (
    echo 正在启动企业微信智能客服，源码模式...
    echo.
    "%~dp0.venv\Scripts\python.exe" "%~dp0main.py"
    set RESULT=%ERRORLEVEL%
    goto checked
)

echo [错误] 没有找到可执行的程序。
echo.
echo   打包模式需要: 启动.exe
echo   源码模式需要: .venv\Scripts\python.exe
echo.
echo 自行打包: python scripts\build.py
echo.
pause
exit /b 1

:checked
if not "%RESULT%"=="0" (
    echo.
    echo [错误] 程序异常退出，错误码: %RESULT%
    echo 请检查 logs\monitor.log 了解详情。
    pause
)