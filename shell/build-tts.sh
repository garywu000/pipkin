#!/bin/sh
# Builds Pipkin's natural voice helper: pipkin-tts (tts/pipkin-tts.c) on top of sherpa-onnx built from one pinned
# release, plus the two libraries it needs. Copies pipkin-tts, libsherpa-onnx-c-api.dylib and libonnxruntime.dylib,
# with their licenses, into the given folder. Builds are cached.
# Usage: shell/build-tts.sh path/to/Resources
set -eu
TAG="v2.0.1"
COMMIT="af2d8d81dbfa61c4cea4c568da78f3982edca005"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
out="${1:?Usage: build-tts.sh path/to/Resources}"
cache="${PIPKIN_BUILD_CACHE:-$HOME/Library/Caches/Pipkin-build}/sherpa-onnx-$TAG-$(uname -m)"
if [ ! -f "$cache/lib/libsherpa-onnx-c-api.dylib" ]; then
  command -v cmake >/dev/null 2>&1 || { echo "Building the natural voice needs cmake (brew install cmake)." >&2; exit 1; }
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$TAG" https://github.com/k2-fsa/sherpa-onnx.git "$work/src"
  [ "$(git -C "$work/src" rev-parse HEAD)" = "$COMMIT" ] || { echo "sherpa-onnx $TAG does not match the pinned commit." >&2; exit 1; }
  # sherpa-onnx's own CMake pins its ONNX Runtime download by SHA-256.
  cmake -S "$work/src" -B "$work/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_DEPLOYMENT_TARGET=12.0 -DBUILD_SHARED_LIBS=ON \
    -DSHERPA_ONNX_ENABLE_C_API=ON -DSHERPA_ONNX_ENABLE_TTS=ON -DSHERPA_ONNX_ENABLE_BINARY=OFF -DSHERPA_ONNX_ENABLE_PORTAUDIO=OFF \
    -DSHERPA_ONNX_ENABLE_WEBSOCKET=OFF -DSHERPA_ONNX_ENABLE_SPEAKER_DIARIZATION=OFF -DSHERPA_ONNX_ENABLE_PYTHON=OFF \
    -DSHERPA_ONNX_ENABLE_TESTS=OFF >/dev/null
  cmake --build "$work/build" --config Release --target sherpa-onnx-c-api -j "$(sysctl -n hw.ncpu)" >/dev/null
  mkdir -p "$cache/lib" "$cache/include/sherpa-onnx/c-api" "$cache/licenses"
  cp -L "$work/build/lib/libsherpa-onnx-c-api.dylib" "$work/build/_deps/onnxruntime-src/lib/libonnxruntime.dylib" "$cache/lib/"
  cp "$work/src/sherpa-onnx/c-api/c-api.h" "$cache/include/sherpa-onnx/c-api/"
  cp "$work/src/LICENSE" "$cache/licenses/sherpa-onnx-LICENSE.txt"
  cp "$work/build/_deps/onnxruntime-src/LICENSE" "$cache/licenses/onnxruntime-LICENSE.txt"
  cp "$work/build/_deps/onnxruntime-src/ThirdPartyNotices.txt" "$cache/licenses/onnxruntime-ThirdPartyNotices.txt"
fi
xcrun clang -O2 -Wall -mmacosx-version-min=12.0 -I "$cache/include" "$repo/tts/pipkin-tts.c" -L "$cache/lib" \
  -lsherpa-onnx-c-api -Wl,-rpath,@loader_path -o "$out/pipkin-tts"
cp "$cache/lib/libsherpa-onnx-c-api.dylib" "$cache/lib/libonnxruntime.dylib" "$out/"
mkdir -p "$out/licenses"
cp "$cache/licenses/"* "$out/licenses/"
for binary in "$out/pipkin-tts" "$out/libsherpa-onnx-c-api.dylib" "$out/libonnxruntime.dylib"; do
  if /usr/bin/otool -L "$binary" | tail -n +2 | grep -v -e '^[[:space:]]*/usr/lib/' -e '^[[:space:]]*/System/' \
      -e '@rpath/libsherpa-onnx-c-api.dylib' -e '@rpath/libonnxruntime' >/dev/null; then
    echo "$binary links to libraries outside the system and the voice engine. Refusing to package it." >&2
    exit 1
  fi
done
echo "Natural voice: sherpa-onnx $TAG"
