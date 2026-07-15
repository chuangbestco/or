#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# FastAPI 0.128.8 supports Python 3.10–3.13.  Avoid Python 3.14 until its
# ecosystem wheels are broadly available, and select/download 3.13 via uv.
if ! command -v uv >/dev/null 2>&1; then
  echo "正在安装 uv（仅首次需要）…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then
  rm -rf .venv
  echo "正在准备兼容的 Python 3.13 运行环境…"
  uv venv --python 3.13 .venv
fi

echo "正在检查依赖…"
uv pip install --python .venv/bin/python -q -r requirements.txt
echo "已启动：http://127.0.0.1:8765"
exec env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
