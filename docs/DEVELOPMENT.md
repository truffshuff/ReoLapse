# Developing Reolapse

- [Layout](#layout)
- [The pipeline](#the-pipeline)
- [Running from source](#running-from-source)
- [Command line](#command-line)
- [Local API](#local-api)
- [Desktop app](#desktop-app)
- [Releases](#releases)
- [Docs screenshots and videos](#docs-screenshots-and-videos)
- [Things that bit us](#things-that-bit-us)

## Layout

```
src/reolapse/          Python package (pipeline, CLI, API server)
  nvr.py               Reolink NVR access (reolink_aio): events, recording files, downloads, login
  fetch.py             download a day's recordings + NVR event list, remux to seekable MP4
  media.py             ffmpeg frame access (hardware decode for big streams), remux
  analyze.py           backgrounds, motion gate, YOLO-seg + ByteTrack, tubes, checkpoints
  tube_index.py        per-day index of objects (spans and box paths) for area search; parked-car filter
  review.py            corrections (tubes/corrections.json) and ignore zones, applied wherever objects are read
  render.py            synopsis placement (TimeMap: even/adaptive clock) and video, trails / group shot / heatmap stills
  sync.py              copying a day's analysis results to the share for other Macs
  cleanup.py           storage cleanup under the retention settings
  settings.py, config.py   app settings (JSON) and paths (DayPaths reads the newer of local/share copies)
  server/              FastAPI app (app.py), job queue (jobs.py), nightly scheduler (scheduler.py),
                       library scan (library.py), update check (updates.py)
app.nosync/            Electron + Svelte desktop app (".nosync" keeps node_modules out of iCloud)
  src/                 UI (Svelte 5, Vite)
  electron/            main process (server lifecycle, first-run setup, PDF reports) and preload bridge
  scripts/prepare-release.sh   gathers what the installer bundles
tests/                 pytest
tools/                 synthetic demo data and the docs' screenshots/videos (see below)
docs/                  user guide, this file, screenshots/, media/ (videos)
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
               ► tubes/analysis.json           how it was analyzed: stream, sizes, model, imgsz, fps, types, when
               ► tubes/_pruned/*.pkl           set aside: vehicles that never really travelled
               ► tubes/corrections.json        hand corrections: {id: person|vehicle|animal|hidden}
               ► tubes/_thumbs/                cached cutouts for the app
render   tubes+bg ► out/synopsis_<style>_<color>.mp4 (+ .json timing sidecar), trails.jpg, strobe.jpg, heatmap.jpg
                  ► out/synopsis_<style>_<color>_<type>.mp4   only person / vehicle / animal (optional)
                  ► out/trails_<type>.jpg, strobe_<type>.jpg, heatmap_<type>.jpg   per-type stills
```

**Download.** The RLN16 NVR only serves *whole recording files* (`cmd=Download` with the search
result's name); its NvrDownload fragments and FLV playback fail. Files are fragmented MP4 with no
index, so every seek would re-read from the start; they are remuxed losslessly (`-c copy +faststart`)
on local disk and copied to the share once. Downloads retry with a fresh login (the NVR session expires
after about an hour).

**Find moving objects.** Frames are decoded at 5 or 10 fps (`ffmpeg`; streams wider than 2560 px use
VideoToolbox and are scaled on the GPU). A motion gate (low-res frame differencing, speckle removal,
thresholds tuned on a noisy 640×360 night stream) decides which frames reach YOLO; batches of 8 run at
fp16 on MPS and are tracked with ByteTrack. A track becomes a *tube* only while it moves, and an object
that stops for a few seconds ends its tube. That short-window test is fooled by a parked car whose box
wobbles, so a vehicle tube is kept only if its centre gets at least 0.5 × its median box diagonal away
from its (median-smoothed) start (`tube_index.really_moved`; people and animals aren't filtered, since
those that barely travel were real). A tube's category comes from its per-frame classes summed by category (`analyze.vote`), with one bias:
an animal-majority track that is *person* in ≥ ⅓ of frames is a person (posed or cut-off people get
called dog/bird, not the reverse); the per-frame votes are stored in the tube as `votes`. Rejected tubes are moved to `tubes/_pruned/` at the end of a scan,
and `reolapse prune` applies the same check to days analyzed before it existed. Stationary objects a moving one came
near (the parked car someone reaches into) are saved with it as *context*. For whole-day scans the
background medians are built from the same decoded frames, so each recording is read once. A checkpoint
every 2 minutes of footage makes the scan resumable.

**Render.** Each tube is placed near its real time of day through a `TimeMap` (even: `video_t = day_t /
86400 × length`; adaptive: 5-minute knots whose video time follows the objects in them plus a 15% floor;
the knots go in the sidecar's `map`), shifted
by at most the allowed drift where that removes real overlap, and composited over cross-faded
background medians; tags show each frame's real time. The timing sidecar lets the app map time of day
to video time. *Only* renders (`--only person`, or `render.per_type` in Settings, which adds
`--per-type` to app jobs) filter the tubes by category and size the video for that smaller set; an
`--only` render writes that type's stills. A full render writes the whole-day stills plus one set per
type present; `--no-video` (job option `no_video`) makes just the stills. `publish` copies only files
whose size or mtime differ from the share's copy.

## Running from source

```
uv sync                       # Python 3.13 environment (.venv → .venv.nosync)
cp .env.example .env          # optional: NVR login for the CLI (the app uses Settings + Keychain)
uv run python -m pytest tests
```

Paths and settings: `REOLAPSE_DATA` (archive), `REOLAPSE_WORK` (working folder), `REOLAPSE_MODELS`,
`REOLAPSE_SETTINGS` (settings file; its folder also holds `jobs.json`, job logs and the server lock),
`REOLAPSE_UI_DIR`. Environment variables win over Settings.

**Review.** Corrections and per-camera ignore zones (`settings.ignore`) are applied at read time by
`review.apply` (object index → counts, hourly charts, area queries, search, trends, reports) and
`render.reviewed` (renders): a corrected object takes the new category, a hidden one and one whose box
centre stays in ignore zones ≥ 90% of the time are left out. The zones also mask the motion gate.

**Two Macs.** `sync.share_results` copies `tubes/` and `bg/` to the share at the end of an analysis (via a
`.incoming` staging folder that's swapped in); `DayPaths._existing` reads the local or shared copy,
whichever has the newer `analysis.json`/`index.json` (`bg/.complete`).

**Memory guard.** `analyze.memory_used()` reads the process footprint (`proc_pid_rusage`
`phys_footprint`, which includes swapped memory; resident size shrinks while swapping). At a checkpoint
above `REOLAPSE_MEMORY_LIMIT_GB` (default 60% of RAM), after `torch.mps.empty_cache()`, the scan exits
with code 75 and the queue restarts it from the checkpoint.

## Command line

```
uv run reolapse probe   [--day D]                         # NVR channels, a day's events and sizes
uv run reolapse fetch   [--day D] [--camera C …] [--stream sub|main]
uv run reolapse analyze [--day D] [--fps 5] [--model yolo26n-seg.pt] [--imgsz 1280] [--types all] [--hours 7-9]
                        [--camera-settings '{"Cam": {"imgsz": 960}}'] [--done-since EPOCH]
uv run reolapse render  [--day D] [--style cutout,ghost,outline] [--color category|time] [--drift 10]
                        [--clock even|adaptive] [--second-clock]
                        [--only person,vehicle,animal] [--per-type person,…] [--no-stills] [--no-video] …
uv run reolapse run     …                                 # fetch + analyze + render
uv run reolapse prune   [--day D] [--camera C …]         # set aside vehicles that never travelled
uv run reolapse cleanup [--dry-run]                       # delete what the retention settings allow
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
| `GET /api/days/{day}/{camera}/objects/{id}/thumb?size=` | an object's cutout (cached in `tubes/_thumbs`; `size` → small JPEG for reports) |
| `PUT /api/days/{day}/{camera}/objects/{id}/correction` | `{"value": "person"\|"vehicle"\|"animal"\|"hidden"\|null}` |
| `GET /api/cameras/{camera}/scene` | the camera's latest midday picture (for drawing zones) |
| `GET /api/search?start=&end=&cameras=&types=&after=&before=&area=&min_seconds=` | objects across days |
| `GET /api/trends?start=&end=&cameras=` | per camera and day: counts and hourly-by-type; per saved area: counts by day |
| `GET /api/nightly?preview=`, `POST /api/nightly/run` | nightly schedule, last run, what it would queue; run now |
| `GET /api/storage?refresh=`, `POST /api/storage/cleanup` | free space, what cleanup would free; clean up now |
| `GET /api/storage/sharing`, `POST /api/storage/share` | days this Mac has that the share doesn't; copy them |
| `PUT /api/kept/{day}` `{"note"}`, `DELETE /api/kept/{day}` | days the cleanup never deletes (`settings.kept`) |
| `GET/POST /api/jobs`, `GET /api/jobs/{id}`, `…/log` | queue (kind `run` becomes three jobs), status, full log file |
| `POST /api/jobs/{id}/cancel`, `…/retry`, `…/move?delta=±1` | cancel, retry, reorder |
| `DELETE /api/jobs/{id}`, `DELETE /api/jobs` | remove one finished job, or all of them (with logs) |
| `GET /api/queue`, `POST /api/queue/pause`, `…/resume` | pause state |
| `GET /api/events` | server-sent events: `snapshot`, `job`, `queue`, `removed` |
| `GET/PUT /api/settings` | settings |
| `GET/PUT /api/nvr`, `POST /api/nvr/test` | NVR login (password to the Keychain), connection test |
| `GET /api/models` | detection models and whether they're downloaded |
| `GET /api/update?check=&force=` | app version, latest GitHub release and notes (checked at most hourly) |
| `PUT /api/update/token`, `POST /api/update/download` | GitHub token to the Keychain; download the `.dmg` to ~/Downloads and open it |

**Job queue.** Two lanes (download; processing) run concurrently, one job each; within a lane jobs start
in queue order; a day's steps keep their order. Jobs run as `python -m reolapse …` subprocesses in their
own process group; output segments ending in `\r` are live status, `\n` lines are log. The queue is saved
to `jobs.json`; on restart a job that was running resumes. The share is checked every 15 s and the queue
pauses itself while it's unreachable. Only one server may run (lock file).

A *Find moving objects* job fixes its analysis settings (`fps`, `types`, `model`, `imgsz`, `per_camera`)
into its options when queued (`jobs.pin_analysis`), so Settings changes never alter a queued or paused
job (a checkpoint only resumes with identical settings). It passes `--done-since <created>` so a resumed
job skips cameras it already finished. Exit code 75 (memory guard) requeues it at once. A cancelled
earlier step doesn't block a day's later steps; a failed one does until retried.

**Nightly run.** `server/scheduler.py` checks every minute; once a day after `nightly.time` (or the next
time the server runs after it) it queues yesterday and `catch_up_days` before: `run` for days with
nothing or a partial download, `render` for analyzed days, nothing for rendered or queued days; then,
with `retention.auto`, `cleanup`. State in `nightly.json` next to `jobs.json`.

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
`engine/bin/python -m reolapse serve` with the bundled tools on `PATH` and `REOLAPSE_APP_VERSION` set
to the app's version (from source the server reads `app.nosync/package.json`). The app also sets
`REOLAPSE_PARENT_PID` to its own pid, and the server shuts down gracefully once that process is gone:
a server that outlives its app (crash, force quit, a WindowServer restart ending the login session)
can no longer reach the Metal shader compiler, so every scan would fail with "Unable to reach
MTLCompilerService", and the next app launch would find it answering and reuse it.

**PDF reports.** `#/report?day=&cams=&types=&per=` is a print-styled page; `electron/preload.cjs`
exposes `window.reolapse.savePdf(hash)`, and the main process loads that hash with `print=1` in a
hidden window, waits for `window.__reportReady` (set once data and every image have loaded), and
`printToPDF`s it (with `color-scheme: light`, or the margins print dark). Report images ask for print sizes (`out/…?w=1600`, `bg/…?w=1600`, cached under
`<working folder>/_scaled`; cutouts `?size=240`). Outside Electron the button falls back to
`window.print()`.

**Updates.** The app is ad-hoc signed, so it can't update itself in place (Squirrel/electron-updater
need a Developer ID signature). `server/updates.py` asks the GitHub API for the latest release of
`settings.updates.repo` (with a fine-grained token from the Keychain, "Reolapse GitHub", only if the
repository is private), compares versions, and streams the `.dmg` asset to `~/Downloads` and opens it; the
user drags it into Applications. The UI checks once at launch when `updates.check_on_launch` is on.

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
Installed apps find the new version through `releases/latest`, so a release must not be a draft or
pre-release and must carry the `.dmg` asset; that happens when the build finishes (about 10 minutes).

Local build: `npm run release:mac -- --config.directories.output=/tmp/reolapse-release`; the output must
be outside `~/Documents` (see below).

## Docs screenshots and videos

Every picture and video in the README and user guide comes from a synthetic demo, so the repository
contains no real footage:

```
uv run python tools/make_demo.py demo            # painted scenes, drawn people/cars/animals, a week of days
cd demo && REOLAPSE_DATA=archive REOLAPSE_WORK=work REOLAPSE_SETTINGS=$PWD/settings.json \
  uv run --project .. reolapse render --day 2026-10-12 --style cutout,ghost,outline --per-type person \
  --clock adaptive --second-clock
REOLAPSE_DATA=archive REOLAPSE_WORK=work REOLAPSE_SETTINGS=$PWD/settings.json REOLAPSE_APP_VERSION=demo \
  REOLAPSE_UI_DIR=../app.nosync/dist uv run --project .. reolapse serve --port 8767 &
cd ../app.nosync && npx electron ../tools/docs_media.cjs ../demo/capture http://127.0.0.1:8767
cd .. && tools/docs_media.sh demo/capture .     # → docs/screenshots/*.jpg, docs/media/*.mp4 + *.gif
```

`make_demo.py` writes real Reolapse data (tubes, index, backgrounds, `analysis.json`, an hour of
"recorded" footage); `docs_media.cjs` drives the app with a visible pointer and records ~10 fps frames.

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
- **MPS is asynchronous**: Ultralytics' NMS time limit also counted the wait for the forward pass and
  emptied the rest of a batch when it tripped; it's lifted for scans (`analyze._no_nms_time_limit`).
- **PyTorch's MPS cache** can grow over a day-long scan until the Mac swaps (a scan ran at a third of its
  speed at 14 GB); it's emptied at every checkpoint and the memory guard restarts the scan if needed.
- **Settings pages that send the whole form** overwrite values saved elsewhere since (the NVR login,
  report areas): the server ignores `nvr`, and the page leaves out `areas`.
