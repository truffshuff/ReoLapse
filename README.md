# reolapse

A day of motion from a fixed Reolink camera, condensed into one short video and a few still images.

Every person, vehicle and animal that moved during the day is cut out of its original moment and
replayed over a time-lapse background of the empty scene, near its real time of day. A small
timestamp tag on each object shows when it was really there (the technique is often called *video
synopsis*).

## Pipeline

```
fetch    NVR -> REOLAPSE_DATA/<camera>/<day>/raw/*.mp4   (continuous recordings, sub stream)
                                          events.json   (NVR person/vehicle/animal/motion events)
analyze  raw -> bg/NNN.jpg     median background per 10 minutes
              tubes/*.pkl      masked crops of each moving object (YOLO-seg + ByteTrack on Apple GPU)
render   tubes + bg -> out/synopsis_<style>_<color>.mp4, out/trails.jpg, strobe.jpg, heatmap.jpg
```

Only frames inside the NVR's event windows are decoded, and YOLO runs only while a cheap motion check
sees change. Objects that never move (parked cars) are dropped, and an object that stops for a few
seconds ends its tube.

## Setup

```
cp .env.example .env      # NVR host + a dedicated user; password without & # % or spaces
uv sync
```

YOLO weights go in `models/` (e.g. `yolo26n-seg.pt`; Ultralytics downloads it on first use).
Raw footage and finished outputs live on the share (`REOLAPSE_DATA`, default `/Volumes/Photos/ReoLink`).
Set `REOLAPSE_WORK` (e.g. `~/Library/Caches/reolapse`) to keep backgrounds, object data and rendering on the
local drive; finished videos/stills are then copied to the share's `out/`. About 1 GB of object data per
camera-day accumulates there.

The venv lives in `.venv.nosync` (with a `.venv` symlink) because `~/Documents` is iCloud-synced,
and iCloud marks files hidden, which makes Python 3.13 skip the package's `.pth` file.

## Usage

```
uv run reolapse probe                       # channels, a day's event counts and recording sizes
uv run reolapse fetch   --day 2026-10-01    # ~5 GB / ~8 min per camera
uv run reolapse analyze --day 2026-10-01
uv run reolapse render  --day 2026-10-01 --style cutout,ghost,outline --color time
```

Cameras default to Garage, Porch Left and Back Yard Right; pass `--camera NAME` (repeatable) to choose.

Render options:

| option | meaning |
|---|---|
| `--style cutout,ghost,outline` | solid cutouts, 55% transparent, or cutouts with a coloured edge (one video each) |
| `--color category\|time` | tags/outlines coloured by object type or by time of day |
| `--only person,animal` | filter object types (separate videos per type if it gets cluttered) |
| `--length 300` | fix the video length; default is automatic from `--density` (avg objects on screen) |
| `--speed 2` | object playback speed vs real time |
| `--max-tube 20` | longest any one object stays on screen; longer ones play faster |

`reolapse run` does fetch, analyze and render in one go and accepts all of their options.

## API server (for the desktop app)

```
uv run reolapse serve            # http://127.0.0.1:8765
```

| endpoint | purpose |
|---|---|
| `GET /api/days` | every day with per-camera status (`fetched` / `analyzed` / `rendered`), object counts, hourly activity |
| `GET /api/days/{day}/{camera}` | one camera-day (`?fresh=1` skips the 15 s cache) |
| `GET /api/days/{day}/{camera}/out/{file}` | rendered videos/stills (Range requests, so video seeks) |
| `GET /api/days/{day}/{camera}/bg/{slot}` | 10-minute background images |
| `GET /api/days/{day}/{camera}/clip?t=SECONDS&dur=30` | original footage at a time of day, streamed without re-encoding |
| `POST /api/jobs` `{kind, day, cameras?, options?}` | queue fetch / analyze / render / run (one at a time; GPU) |
| `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel` | job status, log, cancel |
| `GET /api/events` | server-sent events: job snapshot, then live updates |
| `GET/PUT /api/settings` | cameras, analysis/render defaults, retention, nightly schedule (`~/Library/Application Support/reolapse/settings.json`) |
| `GET /api/nvr`, `POST /api/nvr/test` | who we connect as (credentials stay in `.env`), connection test |

Tests: `uv run python -m pytest tests`.
