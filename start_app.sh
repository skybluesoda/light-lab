#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${LIGHT_LAB_PORT:-8765}"
PYTHON="$HERE/app/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then "${PYTHON_BIN:-python3}" -m venv "$HERE/app/.venv"; fi
if ! "$PYTHON" -c 'import fastapi, uvicorn, pydantic' >/dev/null 2>&1; then
 "$PYTHON" -m pip install -r "$HERE/app/backend/requirements.txt"
fi
if [ ! -f "$HERE/app/frontend/dist/index.html" ]; then
 echo '화면 빌드가 필요합니다: cd app/frontend && npm install && npm run build' >&2
 exit 1
fi
cd "$HERE/app/backend"
echo "LIGHT LAB · http://127.0.0.1:$PORT"
exec "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
