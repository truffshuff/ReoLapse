#!/bin/zsh
# Usage: work/full_day.sh YYYY-MM-DD   -- analyze + render all fixed cameras for one day
cd "$(dirname "$0")/.."
DAY=$1; mkdir -p work; L=work/full_$DAY.log; : > $L
for cam in "Garage" "Porch Left" "Back Yard Right"; do
  extra=()
  [ -d "/Volumes/Photos/ReoLink/$(echo $cam | tr 'A-Z ' 'a-z_')/$DAY/bg" ] && extra=(--skip-bg)
  echo "### analyze $cam $(date +%T)" >> $L
  uv run --no-sync reolapse analyze --camera "$cam" --day $DAY $extra >> $L 2>&1 || echo "FAILED analyze $cam" >> $L
  echo "### render $cam $(date +%T)" >> $L
  uv run --no-sync reolapse render --camera "$cam" --day $DAY --style cutout,ghost,outline >> $L 2>&1 || echo "FAILED render $cam" >> $L
done
echo "### ALL DONE $(date +%T)" >> $L
