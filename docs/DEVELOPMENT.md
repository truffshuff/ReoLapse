# Developing Reolapse

- [Layout](#layout)
- [The pipeline](#the-pipeline)
- [Running from source](#running-from-source)
- [Command line](#command-line)
- [Local API](#local-api)
- [Desktop app](#desktop-app)
- [Releases](#releases)
- [Things that bit us](#things-that-bit-us)

## Layout

```
src/reolapse/          Python package (pipeline, CLI, API server)
  nvr.py               Reolink NVR access (reolink_aio): events, recording files, downloads, login
  fetch.py             download a day's recordings + NVR event list, remux to seekable MP4
  media.py             ffmpeg frame access (hardware decode for big streams), remux
  analyze.py           backgrounds, motion gate, YOLO-seg + ByteTrack, tubes, checkpoints
  tube_index.py        per-day index of objects (spans and box paths) for area search
  render.py            synopsis placement and video, trails / group shot / heatmap stills
  settings.py, config.py   app settings (JSON) and paths
  server/              FastAPI app, job queue/scheduler, library scan
app.nosync/            Electron + Svelte desktop app (".nosync" keeps node_modules out of iCloud)
  src/                 UI (Svelte 5, Vite)
  electron/            main process (server lifecycle, first-run setup window)
  scripts/prepare-release.sh   gathers what the installer bundles
tests/                 pytest
docs/                  user guide, this file, screenshots
.github/workflows/release.yml  builds the macOS app for v* tags
```

## The pipeline

Per camera and day, under the archive folder (`<archive>/<camera>/<YYYY-MM-DD>/`) and the working folder:

```
fetch    NVR ──► raw/<stream>_<start>.mp4     whole recording files (≈1 h each), remuxed seekable
               ► events.json                  the NVR's typed events (person/vehicle/animal/motion/…)
analyze  raw ──► bg/NNN.jpg                    median of the empty scene per 10 minutes (144 a day)
               ► tubes/*.pkl                   one per moving object: timestamped masked crops
               ► tubes/index.json              spans and box paths (area search)
               ► tubes/checkpoint.json         while running: resume point
render   tubes+bg ► out/synopsis_<style>_<color>.mp4 (+ .json timing sidecar), trails.jpg, strobe.jpg, heatmap.jpg
```

**Download.** The RLN16 NVR only serves *whole recording files* (`cmd=Download` with the search
result's name); its NvrDownload fragments and FLV playback fail. Files are fragmented MP4 with no
index, so every seek would re-read from the start; they are remuxed losslessly (`-c copy +faststart`)
on local disk and copied to the share once. Downloads retry with a fresh login (the NVR session expires
after about an hour).

**Find moving objects.** Frames are decoded at 5 or 10 fps (`ffmpeg`; streams wider than 2560 px use
VideoToolbox and are scaled on the GPU). A motion gate (low-res frame differencing, speckle removal,
thresholds tuned on a noisy 640×360 night stream) decides which frames reach YOLO; batches of 8 run at
fp16 on MPS and are tracked with ByteTrack. A track becomes a *tube* only while it moves; parked cars
never do, and an object that stops for a few seconds ends its tube. Stationary objects a moving one came
near (the parked car someone reaches into) are saved with it as *context*. For whole-day scans the
background medians are built from the same decoded frames, so each recording is read once. A checkpoint
every 2 minutes of footage makes the scan resumable.

**Render.** Each tube is placed near its real time of day (`video_t = day_t / 86400 × length`), shifted
by at most the allowed drift where that removes real overlap, and composited over cross-faded
background medians; tags show each frame's real time. The timing sidecar lets the app map time of day
to video time.

## Running from source

```
uv sync                       # Python 3.13 environment (.venv → .venv.nosync)
cp .env.example .env          # optional: NVR login for the CLI (the app uses Settings + Keychain)
uv run python -m pytest tests
```

Paths and settings: `REOLAPSE_DATA` (archive), `REOLAPSE_WORK` (working folder), `REOLAPSE_MODELS`,
`REOLAPSE_SETTINGS` (settings file; its folder also holds `jobs.json`, job logs and the server lock),
`REOLAPSE_UI_DIR`. Environment variables win over Settings.

## Command line

```
uv run reolapse probe   [--day D]                         # NVR channels, a day's events and sizes
uv run reolapse fetch   [--day D] [--camera C …] [--stream sub|main]
uv run reolapse analyze [--day D] [--fps 5] [--model yolo26n-seg.pt] [--imgsz 1280] [--types all] [--hours 7-9]
uv run reolapse render  [--day D] [--style cutout,ghost,outline] [--color category|time] [--drift 10] …
uv run reolapse run     …                                 # fetch + analyze + render
uv run reolapse serve   [--port 8765]
```

`--day` defaults to yesterday and `--camera` to the cameras in Settings. Every command prints plain-language
progress; with `REOLAPSE_PROGRESS=json` it also prints `@@{json}` progress lines (the app's job queue sets this).

## Local API

`reolapse serve` (127.0.0.1:8765) hosts the UI at `/` and:

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | archive/working folders, share reachable |
| `GET /api/days`, `GET /api/days/{day}/{camera}` | days and per-camera status, counts, hourly activity |
| `GET /api/days/{day}/{camera}/out/{file}` | videos (Range requests), stills, timing sidecars |
| `GET /api/days/{day}/{camera}/bg/{slot}` | background images |
| `GET /api/days/{day}/{camera}/clip?t=&dur=` | original footage from a time of day (stream copy) |
| `GET /api/days/{day}/{camera}/objects?regions=&categories=` | objects whose center entered any region (normalized x0,y0,x1,y1;…) |
| `GET /api/days/{day}/{camera}/objects/{id}/thumb` | an object's cutout |
| `GET/POST /api/jobs`, `GET /api/jobs/{id}`, `…/log` | queue (kind `run` becomes three jobs), status, full log file |
| `POST /api/jobs/{id}/cancel`, `…/retry`, `…/move?delta=±1` | cancel, retry, reorder |
| `GET /api/queue`, `POST /api/queue/pause`, `…/resume` | pause state |
| `GET /api/events` | server-sent events: `snapshot`, `job`, `queue` |
| `GET/PUT /api/settings` | settings |
| `GET/PUT /api/nvr`, `POST /api/nvr/test` | NVR login (password to the Keychain), connection test |
| `GET /api/models` | detection models and whether they're downloaded |

**Job queue.** Two lanes (download; processing) run concurrently, one job each; within a lane jobs start
in queue order; a day's steps keep their order. Jobs run as `python -m reolapse …` subprocesses in their
own process group; output segments ending in `\r` are live status, `\n` lines are log. The queue is saved
to `jobs.json`; on restart a job that was running resumes. The share is checked every 15 s and the queue
pauses itself while it's unreachable. Only one server may run (lock file).

## Desktop app

```
cd app.nosync
npm install
npm run build        # UI → dist/ (served by the Python server)
npm run app          # Electron; reuses a running server or starts one with `uv run`
```

UI development: `uv run reolapse serve`, `npm run dev` (Vite on :5173, proxies /api), `npm run app:dev`.

Packaged, the app carries the Python project, `uv` and a static ffmpeg as resources. On first launch (and
after an update) it runs `uv sync --frozen --no-dev --no-editable` into
`~/Library/Application Support/Reolapse/engine` with a uv-managed Python, then starts
`engine/bin/python -m reolapse serve` with the bundled tools on `PATH`.

## Releases

Bump nothing by hand: push a tag.

```
git tag -a v0.2.0 -m "Reolapse 0.2.0"
git push origin v0.2.0
```

`.github/workflows/release.yml` (macos-15, Apple Silicon) sets the app version from the tag, builds the
UI, runs `scripts/prepare-release.sh` (engine source, uv, ffmpeg/ffprobe from martin-riedl.de), packages
an ad-hoc-signed `.dmg` with electron-builder and publishes a GitHub Release with
`.github/release-notes.md`. *Run workflow* builds without releasing and keeps the `.dmg` as an artifact.

Local build: `npm run release:mac -- --config.directories.output=/tmp/reolapse-release`; the output must
be outside `~/Documents` (see below).

## Things that bit us

- **iCloud Desktop & Documents** marks files hidden and adds attributes: Python 3.13 skips hidden `.pth`
  files (hence `.venv.nosync` with a `.venv` symlink), npm replaces symlinked `node_modules` (hence the
  `app.nosync` folder), and `codesign` rejects files with Finder/provenance attributes (build outside
  `~/Documents`).
- **NVR passwords** with `& # %` or spaces break the Reolink web API.
- **`ELECTRON_RUN_AS_NODE`** is set in VS Code's terminal; the npm scripts unset it.
- **`grep -q` with `set -o pipefail`** fails when the producer gets SIGPIPE; capture output first.
- **Electron caching**: `index.html` must be served `no-cache` or an old build survives updates.
- **ffmpeg stderr**: captured, and reported only for real decode failures (a pause otherwise produces
  "Broken pipe" noise).
