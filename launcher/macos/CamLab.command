#!/bin/bash
# CamLab.command — macOS 双击启动器
# 首次运行: 自动创建虚拟环境并安装依赖(国内镜像加速), 之后双击即用
# 放在仓库根目录的 launcher/macos/ 下, 也可拷到桌面单独使用
set -e
cd "$(dirname "$0")/../.."           # → 仓库根(含 camlab/)

echo "==== Cam-Lab 启动器 ===="

# 1) 找一个可用的 Python (优先 conda 环境, 其次系统)
PY=""
for cand in \
    "$HOME/miniforge3/bin/python3" \
    "$HOME/anaconda3/bin/python3" \
    "$HOME/miniconda3/bin/python3" \
    "$(command -v python3)" ; do
    [ -n "$cand" ] && [ -x "$cand" ] && PY="$cand" && break
done
[ -z "$PY" ] && { echo "未找到 Python3, 请先安装: https://www.python.org/downloads/"; exit 1; }
echo "Python: $PY ($($PY --version 2>&1))"

# 2) 依赖就绪检查
if ! "$PY" -c "import cv2, numpy, PySide6" 2>/dev/null; then
    echo "首次运行: 创建虚拟环境 .venv 并安装依赖(约1-2分钟)..."
    "$PY" -m venv .venv
    PIP=".venv/bin/pip"
    "$PIP" install --quiet --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    "$PIP" install --quiet -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
        || "$PIP" install --quiet -r requirements.txt
    PYBIN=".venv/bin/python"
else
    PYBIN="$PY"
fi

echo "启动 Cam-Lab ..."
exec "$PYBIN" -m camlab
