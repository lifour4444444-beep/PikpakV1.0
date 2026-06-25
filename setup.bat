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
for /f "tokens=2" %%v in ('python --version 2^>^&1') set PYVER=%%v
echo [检测] Python %PYVER%

REM ── 检查 Python 版本 >= 3.9 ──
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)
if %PYMAJOR% lss 3 (
    echo [错误] 需要 Python 3.9+，当前版本: %PYVER%
    pause
    exit /b 1
)
if %PYMAJOR% equ 3 if %PYMINOR% lss 9 (
    echo [错误] 需要 Python 3.9+，当前版本: %PYVER%
    pause
    exit /b 1
)

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
echo [1/3] 升级 pip（清华源）...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
if %errorlevel% neq 0 (
    echo [警告] pip 升级失败，尝试继续安装...
)

REM ── 安装 Python 依赖 ──
echo [2/3] 安装 Python 依赖...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo.
    echo [警告] 清华源安装失败，尝试阿里云镜像...
    pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    if %errorlevel% neq 0 (
        echo.
        echo [错误] Python 依赖安装失败，请检查网络连接后重试
        pause
        exit /b 1
    )
)

REM ── 检查 Node.js ──
echo.
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] 未检测到 Node.js，验证码功能需要 Node.js 18+
    echo        下载地址: https://nodejs.org/
    echo        安装后重新运行此脚本
    echo.
    echo ============================================
    echo   Python 环境安装完成！
    echo   运行: python gui.py
    echo ============================================
    pause
    exit /b 0
)
for /f "tokens=1" %%v in ('node --version 2^>^&1') do set NODEVER=%%v
echo [检测] Node.js %NODEVER%

REM ── 检查 Node.js 版本 >= 18 ──
for /f "tokens=1 delims=." %%a in ("%NODEVER:~1%") do set NODEMAJOR=%%a
if %NODEMAJOR% lss 18 (
    echo [警告] 需要 Node.js 18+，当前版本: %NODEVER%
    echo        下载地址: https://nodejs.org/
    echo        验证码功能可能无法正常使用
    echo.
    echo ============================================
    echo   Python 环境安装完成！
    echo   运行: python gui.py
    echo ============================================
    pause
    exit /b 0
)

REM ── 安装 Node.js 依赖 ──
echo [3/3] 安装 Node.js 依赖...
npm install
if %errorlevel% neq 0 (
    echo [警告] Node.js 依赖安装失败，验证码功能可能无法使用
)

echo.
echo ============================================
echo   环境安装完成！
echo   运行: python gui.py
echo ============================================
pause