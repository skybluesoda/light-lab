#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BLENDER="${BLENDER_BIN:-$ROOT/work/runtime/extracted/Blender/Blender.app/Contents/MacOS/Blender}"
if [ ! -x "$BLENDER" ]; then
 BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
fi
if [ ! -x "$BLENDER" ]; then
 echo 'Blender 실행 파일이 없습니다. BLENDER_BIN에 실행 파일 경로를 지정하세요.' >&2
 exit 1
fi
mkdir -p "$HERE/renders-manual"
"$BLENDER" -b --factory-startup --python-exit-code 1 --python "$HERE/scene.py" -- --output "$HERE/renders-manual" "$@" > "$HERE/renders-manual/render.log" 2>&1 &
pid=$!
echo "렌더링 중. 로그: $HERE/renders-manual/render.log"
if wait "$pid"; then
 echo "완료: $HERE/renders-manual/render_report.json"
else
 code=$?
 echo "Blender 실패 (종료 코드 $code). render.log를 확인하세요." >&2
 exit "$code"
fi
