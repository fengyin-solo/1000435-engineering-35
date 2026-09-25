#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYBIN="python3"

# 优先使用 .venv；已有的 .venv 可能来自别的机器（解释器软链失效），
# 这种情况下重建。系统不支持 venv（如未装 python3-venv）时回退到用户级安装。
if [ -x .venv/bin/python ] && .venv/bin/python -c "import fastapi" >/dev/null 2>&1; then
  PYBIN=".venv/bin/python"
elif python3 -m venv .venv >/dev/null 2>&1; then
  .venv/bin/pip install -q -r requirements.txt
  PYBIN=".venv/bin/python"
elif ! python3 -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  python3 -m pip install -q --user -r requirements.txt
fi

exec "$PYBIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
