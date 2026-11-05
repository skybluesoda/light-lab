#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BLENDER="${BLENDER_BIN:-$ROOT/work/runtime/extracted/Blender/Blender.app/Contents/MacOS/Blender}"
if [ ! -x "$BLENDER" ]; then BLENDER='/Applications/Blender.app/Contents/MacOS/Blender'; fi
if [ ! -f "$HERE/renders-v8/light_lab.blend" ]; then echo 'v8 장면 파일이 필요합니다.' >&2; exit 1; fi
mkdir -p "$HERE/diagnostics-v8"
echo "비교 이미지 4장을 생성합니다. 로그: $HERE/diagnostics-v8/render.log"
if "$BLENDER" -b --factory-startup --python-exit-code 1 --python "$HERE/diagnose.py" -- --source "$HERE/renders-v8/light_lab.blend" --output "$HERE/diagnostics-v8" > "$HERE/diagnostics-v8/render.log" 2>&1; then
 echo "완료: $HERE/diagnostics-v8"
else
 code=$?; echo "진단 실패 ($code). 로그를 확인하세요." >&2; exit "$code"
fi
