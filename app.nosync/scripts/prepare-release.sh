#!/usr/bin/env bash
# Gather what the installer carries besides Electron itself (macOS, Apple Silicon):
#   build-resources/backend  the Python project (pyproject, lockfile, source) for `uv sync`
#   build-resources/bin      uv, ffmpeg, ffprobe (static builds; ffmpeg has VideoToolbox)
#   dist/                    the built UI (npm run build)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=..
OUT=build-resources
rm -rf "$OUT"
mkdir -p "$OUT/backend" "$OUT/bin"

cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$ROOT/.python-version" "$ROOT/README.md" "$OUT/backend/"
rsync -a --exclude '__pycache__' "$ROOT/src" "$OUT/backend/"

tmp=$(mktemp -d)
curl -fsSL -o "$tmp/uv.tgz" https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz
tar -xzf "$tmp/uv.tgz" -C "$tmp"
cp "$tmp/uv-aarch64-apple-darwin/uv" "$OUT/bin/"
for tool in ffmpeg ffprobe; do
  curl -fsSL -o "$tmp/$tool.zip" "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/$tool.zip"
  unzip -o -q "$tmp/$tool.zip" -d "$OUT/bin"
done
chmod +x "$OUT/bin/"*
rm -rf "$tmp"

filters=$("$OUT/bin/ffmpeg" -hide_banner -filters)  # (not piped into grep -q: SIGPIPE + pipefail)
[[ "$filters" == *scale_vt* ]] || { echo "ffmpeg build lacks scale_vt" >&2; exit 1; }
[ -f dist/index.html ] || npm run build
# codesign refuses files carrying Finder info / provenance attributes (common under ~/Documents)
xattr -cr "$OUT" dist node_modules/electron/dist 2>/dev/null || true
echo "release resources ready:"; du -sh "$OUT"/* dist
