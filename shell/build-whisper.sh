#!/bin/sh
# Builds whisper.cpp's whisper-cli (the local speech engine for dictation) from one pinned release, as a single
# self-contained binary, and copies it to the given path. Builds are cached, so later packages reuse them.
# Usage: shell/build-whisper.sh path/to/whisper-cli
set -eu
TAG="v1.9.5"
COMMIT="d1be6fde11ac6e0407606b4e42fe72d34add8037"
out="${1:?Usage: build-whisper.sh path/to/whisper-cli}"
cache="${PIPKIN_BUILD_CACHE:-$HOME/Library/Caches/Pipkin-build}/whisper.cpp-$TAG-$(uname -m)"
if [ ! -x "$cache/whisper-cli" ]; then
  command -v cmake >/dev/null 2>&1 || { echo "Building the speech engine needs cmake (brew install cmake)." >&2; exit 1; }
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$TAG" https://github.com/ggml-org/whisper.cpp.git "$work/src"
  [ "$(git -C "$work/src" rev-parse HEAD)" = "$COMMIT" ] || { echo "whisper.cpp $TAG does not match the pinned commit." >&2; exit 1; }
  # GGML_NATIVE=OFF: never tune for the build machine's CPU. Metal is embedded, so there are no side files.
  cmake -S "$work/src" -B "$work/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_DEPLOYMENT_TARGET=12.0 \
    -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_OPENMP=OFF \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF -DWHISPER_SDL2=OFF -DWHISPER_CURL=OFF >/dev/null
  cmake --build "$work/build" --config Release --target whisper-cli -j "$(sysctl -n hw.ncpu)" >/dev/null
  if /usr/bin/otool -L "$work/build/bin/whisper-cli" | tail -n +2 | grep -v -e '^[[:space:]]*/usr/lib/' -e '^[[:space:]]*/System/' >/dev/null; then
    echo "whisper-cli links to libraries outside the system. Refusing to package it." >&2
    exit 1
  fi
  mkdir -p "$cache"
  cp "$work/build/bin/whisper-cli" "$cache/whisper-cli.tmp"
  cp "$work/src/LICENSE" "$cache/LICENSE"
  mv "$cache/whisper-cli.tmp" "$cache/whisper-cli"
fi
cp "$cache/whisper-cli" "$out"
chmod 755 "$out"
echo "Speech engine: whisper.cpp $TAG"
