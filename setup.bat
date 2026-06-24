@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   PikPak 批量邀请注册 - 环境安装脚本
echo ============================================
echo.

REM ── 检查 Python ──
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.9+
    echo        下载地址: https://www.python.org/downloads/
    echo        安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo [检测] Python %%v

REM ── 检查 pip ──
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] pip 不可用，请重新安装 Python 并勾选 pip
    pause
    exit /b 1
)
echo [检测] pip 可用

REM ── 升级 pip ──
echo.
echo [1/2] 升级 pip（清华源）...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
if %errorlevel% neq 0 (
    echo [警告] pip 升级失败，尝试继续安装...
)

REM ── 安装依赖 ──
echo [2/2] 安装项目依赖...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo.
    echo [错误] 依赖安装失败，尝试使用阿里云镜像...
    pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    if %errorlevel% neq 0 (
        echo.
        echo [错误] 安装失败，请检查网络连接后重试
        pause
        exit /b 1
    )
)

REM ── 检查 Node.js ──
echo.
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] 未检测到 Node.js，验证码功能需要 Node.js
    echo        下载地址: https://nodejs.org/
    echo        安装后打开终端运行: node --version 确认
) else (
    for /f "tokens=1" %%v in ('node --version 2^>^&1') do echo [检测] Node.js %%v
)

echo.
echo ============================================
echo   环境安装完成！
echo   运行: python gui.py
echo ============================================
pause