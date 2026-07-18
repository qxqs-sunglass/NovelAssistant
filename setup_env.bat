@echo off
chcp 65001 >nul
echo ========================================
echo   小说创作助手 - 环境安装
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未检测到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [INFO] Python 版本:
python --version

:: 创建虚拟环境
if exist venv (
    echo [INFO] 虚拟环境已存在，跳过创建
) else (
    echo [INFO] 创建虚拟环境...
    python -m venv venv
)

:: 激活并安装依赖
echo [INFO] 安装依赖...
call venv\Scripts\activate.bat
pip install -r requirements.txt

echo.
echo ========================================
echo   [OK] 环境安装完成！
echo ========================================
pause
