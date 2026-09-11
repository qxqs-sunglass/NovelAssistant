@echo off
chcp 65001 >nul
setlocal

rem 兼容 venv / .venv 两种虚拟环境目录名
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON=venv\Scripts\python.exe"
) else (
    echo [ERROR] 未找到虚拟环境，请先运行 setup_env.bat 安装环境
    pause
    exit /b 1
)

"%PYTHON%" app.py
if errorlevel 1 pause
endlocal