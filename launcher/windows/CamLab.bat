@echo off
rem CamLab.bat - Windows 双击启动器
rem 首次运行: 自动创建虚拟环境并安装依赖, 之后双击即用
cd /d "%~dp0..\.."

echo ==== Cam-Lab 启动器 ====

rem 1) 找 Python (py 启动器 / python3 / python)
set PY=
py -3 -c "import sys" >nul 2>&1 && set PY=py -3
if not defined PY ( python3 --version >nul 2>&1 && set PY=python3 )
if not defined PY ( python --version >nul 2>&1 && set PY=python )
if not defined PY (
    echo 未找到 Python3, 请先安装: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo Python: %PY%

rem 2) 依赖就绪检查
%PY% -c "import cv2, numpy, PySide6" >nul 2>&1
if errorlevel 1 (
    echo 首次运行: 创建虚拟环境 .venv 并安装依赖...
    %PY% -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --quiet --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    python -m pip install --quiet -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    set PYBIN=.venv\Scripts\python.exe
) else (
    set PYBIN=%PY%
)

echo 启动 Cam-Lab ...
%PYBIN% -m camlab
if errorlevel 1 pause
