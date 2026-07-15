#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ ! -x .venv/bin/python ]; then
  echo "首次启动：正在创建本地运行环境…"
  env -u PYTHONPATH -u PYTHONHOME "$PYTHON_BIN" -m venv .venv
fi
echo "正在检查依赖…"
env -u PYTHONPATH -u PYTHONHOME .venv/bin/pip install -q -r requirements.txt
echo "已启动：http://127.0.0.1:8765"
exec env -u PYTHONPATH -u PYTHONHOME .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
