#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
APP="$ROOT/work/runtime/extracted/Blender/Blender.app"
BLENDER="${BLENDER_BIN:-$APP/Contents/MacOS/Blender}"
if [ ! -x "$BLENDER" ]; then BLENDER='/Applications/Blender.app/Contents/MacOS/Blender'; fi
OUT="$HERE/renders-v11"
if [ ! -f "$OUT/lightmaps/denoise_report.json" ]; then
 "$APP/Contents/Resources/4.5/python/bin/python3.11" "$HERE/prepare_lightmaps.py" --source "$HERE/renders-v10/lightmaps" --output "$OUT/lightmaps"
fi
echo "노이즈 제거한 라이트맵으로 렌더링합니다. 로그: $OUT/render.log"
if "$BLENDER" -b --factory-startup --python-exit-code 1 --python "$HERE/refine_lightmaps.py" -- --source "$HERE/renders-v10/light_lab.blend" --output "$OUT" > "$OUT/render.log" 2>&1; then
 echo "완료: $OUT"
else
 code=$?; echo "실패 ($code). 로그를 확인하세요." >&2;exit "$code"
fi
