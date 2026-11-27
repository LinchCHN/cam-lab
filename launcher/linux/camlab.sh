#!/bin/bash
# camlab.sh — Linux 双击/命令行启动器 (逻辑同 macOS 版)
set -e
cd "$(dirname "$0")/../.."

echo "==== Cam-Lab 启动器 ===="
PY="$(command -v python3)"
[ -z "$PY" ] && { echo "未找到 python3, 请先安装"; exit 1; }
echo "Python: $PY ($($PY --version 2>&1))"

if ! "$PY" -c "import cv2, numpy, PySide6" 2>/dev/null; then
    echo "首次运行: 创建虚拟环境 .venv 并安装依赖..."
    "$PY" -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    .venv/bin/pip install --quiet -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
        || .venv/bin/pip install --quiet -r requirements.txt
    PYBIN=".venv/bin/python"
else
    PYBIN="$PY"
fi

echo "启动 Cam-Lab ..."
exec "$PYBIN" -m camlab
