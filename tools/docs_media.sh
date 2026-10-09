#!/bin/bash
# Turn tools/docs_media.cjs output into the files the docs use:
#   docs/screenshots/<name>.jpg   (1440 px wide)
#   docs/media/<name>.mp4         (H.264, 10 fps)
#   docs/media/<name>.gif         (960 px wide, 8 fps, for inline previews in Markdown)
# usage: tools/docs_media.sh <capture folder> <repo root>
set -e
src="$1"; repo="${2:-.}"
mkdir -p "$repo/docs/screenshots" "$repo/docs/media"
for f in "$src"/*.png; do
  name=$(basename "$f" .png)
  w=$(sips -g pixelWidth "$f" | awk '/pixelWidth/ {print $2}')
  target=$(( w > 1440 ? 1440 : w ))
  sips -s format jpeg -s formatOptions 82 --resampleWidth "$target" "$f" --out "$repo/docs/screenshots/$name.jpg" >/dev/null
done
for d in "$src"/frames-*/; do
  name=$(basename "$d"); name=${name#frames-}
  ffmpeg -v error -y -framerate 10 -i "$d/%05d.jpg" -vf "scale=1440:-2" -c:v libx264 -pix_fmt yuv420p -crf 26 \
    -movflags +faststart "$repo/docs/media/$name.mp4"
  ffmpeg -v error -y -framerate 10 -i "$d/%05d.jpg" \
    -vf "fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" \
    "$repo/docs/media/$name.gif"
done
ls -la "$repo/docs/media"
