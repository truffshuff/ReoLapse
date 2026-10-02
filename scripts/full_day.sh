#!/bin/zsh
# Usage: scripts/full_day.sh YYYY-MM-DD [camera ...]
# Analyze + render each camera for one day (default: the three fixed cameras); log in work/full_<day>.log.
# Reuses a camera's existing backgrounds (on the work area or the share) instead of rebuilding them.
cd "$(dirname "$0")/.."
DAY=$1; shift
cams=("$@"); (( ${#cams} )) || cams=("Garage" "Porch Left" "Back Yard Right")
mkdir -p work; L=work/full_$DAY.log; : > $L
for cam in "${cams[@]}"; do
  bgs=$(uv run --no-sync python -c "import sys; from datetime import date; from reolapse.config import DayPaths; p=DayPaths(sys.argv[1], date.fromisoformat(sys.argv[2])); print(int(p.backgrounds.is_dir() and any(p.backgrounds.glob('*.jpg'))))" "$cam" "$DAY")
  extra=(); [ "$bgs" = 1 ] && extra=(--skip-bg)
  echo "### analyze $cam $(date +%T) ${extra[*]}" >> $L
  uv run --no-sync reolapse analyze --camera "$cam" --day $DAY $extra >> $L 2>&1 || echo "FAILED analyze $cam" >> $L
  echo "### render $cam $(date +%T)" >> $L
  uv run --no-sync reolapse render --camera "$cam" --day $DAY --style cutout,ghost,outline >> $L 2>&1 || echo "FAILED render $cam" >> $L
done
echo "### ALL DONE $(date +%T)" >> $L
