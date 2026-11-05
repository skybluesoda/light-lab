#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BLENDER="${BLENDER_BIN:-$ROOT/work/runtime/extracted/Blender/Blender.app/Contents/MacOS/Blender}"
if [ ! -x "$BLENDER" ]; then BLENDER='/Applications/Blender.app/Contents/MacOS/Blender'; fi
if [ ! -x "$BLENDER" ]; then echo 'Blender 실행 파일 경로를 확인하세요.' >&2;exit 1; fi
# This small check never imports bpy or starts Blender.
python3 - "$HERE" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
from preset_lab import check_graphics_access
try:check_graphics_access()
except RuntimeError as e:
 print(str(e),file=sys.stderr);raise SystemExit(1)
PY
OUT="$HERE/preview-approx"
mkdir -p "$OUT"
echo "라이트맵 없는 Eevee 실험 · 로그: $OUT/render.log"
if "$BLENDER" -b --factory-startup --python-exit-code 1 --python "$HERE/scene_approx.py" -- --output "$OUT" --indirect-mode approx --engines eevee --light-config "$HERE/config/approx-preview.json" --eevee-samples 128 --device CPU "$@" > "$OUT/render.log" 2>&1; then
 echo "완료: $OUT/eevee.png"
else
 code=$?;echo "실험 실패 ($code). 로그를 확인하세요." >&2;exit "$code"
fi
