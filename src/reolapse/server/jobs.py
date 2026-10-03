"""A one-at-a-time queue of pipeline jobs, each run as a `python -m reolapse` subprocess.

Jobs run sequentially because analysis owns the GPU. The subprocess is started with
REOLAPSE_PROGRESS=json; its output is read as segments ending in "\\r" or "\\n":

- `@@{json}`            -> job.progress (the progress bar)
- text ending in "\\r"   -> job.status, an in-place line (download speed, frames rendered…)
- text ending in "\\n"   -> job.log, and the job's full log file

Scheduling: two lanes run side by side, one job each -- "download" (network: fetch) and
"process" (GPU/CPU: analyze, render) -- so the next day can download while an earlier day is
analyzed. Within a lane jobs start in queue order (reorderable), but a day's steps keep their
order: its analysis waits for its download, its render for its analysis. "Everything" is
queued as those three separate jobs.

The queue is saved to disk, so jobs survive a server restart: queued jobs stay queued, and a
job that was running is resumed (each pipeline step skips work that's already finished).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .. import config
from .. import settings as settings_mod

KINDS = ("fetch", "analyze", "render", "run")
STEPS = ("fetch", "analyze", "render")  # what "run" (Everything) expands into
STAGE = {"fetch": 0, "analyze": 1, "render": 2, "run": 0}
LANE = {"fetch": "download", "analyze": "process", "render": "process", "run": "process"}
STEP_NAME = {"fetch": "download", "analyze": "object finding", "render": "rendering", "run": "processing"}
KIND_LABEL = {"run": "Everything (download, analyze, render)", "fetch": "Download", "analyze": "Find moving objects",
              "render": "Render videos and stills"}
ONLY_NAME = {"person": "people", "vehicle": "vehicles", "animal": "animals"}
STATE_DIR = settings_mod.SETTINGS_PATH.parent
HISTORY = 50  # finished jobs kept in the list


def _dur(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m {sec % 60:02d}s"


@dataclass
class Job:
    kind: str
    day: str
    cameras: list[str]
    options: dict = field(default_factory=dict)
    id: int = 0
    status: str = "queued"  # queued | running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    returncode: int | None = None
    progress: dict = field(default_factory=dict)
    status_line: str = ""
    order: float = 0.0  # position in the queue (lower runs first)
    group: int | None = None  # jobs queued together by "Everything"
    waiting: str = ""  # why a queued job can't start yet
    log: deque = field(default_factory=lambda: deque(maxlen=400))
    proc: asyncio.subprocess.Process | None = field(default=None, repr=False)
    interrupted: bool = field(default=False, repr=False)  # stopped by a pause: goes back to the queue

    FIELDS = ("id", "kind", "day", "cameras", "options", "status", "created", "started", "finished",
              "returncode", "progress", "status_line", "order", "group", "waiting")

    @property
    def lane(self) -> str:
        return LANE[self.kind]

    def summary(self, with_log: bool = False) -> dict:
        d = {k: getattr(self, k) for k in self.FIELDS}
        if with_log:
            d["log"] = list(self.log)
        return d


def pipeline_args(job: Job, cfg: dict) -> list[str]:
    """CLI arguments for a job: settings provide defaults, job.options override them."""
    args = [job.kind, "--day", job.day]
    for cam in job.cameras:
        args += ["--camera", cam]
    a = {**cfg["analysis"], **{k: v for k, v in job.options.items() if k in cfg["analysis"]}}
    r = {**cfg["render"], **{k: v for k, v in job.options.items() if k in cfg["render"]}}
    if job.kind in ("analyze", "run"):
        args += ["--fps", str(a["fps"]), "--types", str(a["types"]), "--model", str(a["model"]),
                 "--imgsz", str(a["imgsz"])]
        if job.options.get("skip_bg"):
            args.append("--skip-bg")
    if job.kind in ("render", "run"):
        styles = r["styles"] if isinstance(r["styles"], list) else [r["styles"]]
        args += ["--style", ",".join(styles), "--color", str(r["color"]), "--drift", str(r["drift"]),
                 "--speed", str(r["speed"]), "--density", str(r["density"]), "--max-tube", str(r["max_tube"])]
        only = [k for k in job.options.get("only", []) if k in ("person", "vehicle", "animal")]
        if only:  # a one-off render of just these types
            args += ["--only", ",".join(only)]
        elif r.get("per_type"):
            args += ["--per-type", ",".join(r["per_type"])]
    return args


def default_command(job: Job) -> list[str]:
    return [sys.executable, "-m", "reolapse", *pipeline_args(job, settings_mod.load())]


async def _keep_awake_while(pid: int) -> None:
    """macOS: stop idle sleep until the job process exits (caffeinate exits with it)."""
    if sys.platform != "darwin":
        return
    try:
        await asyncio.create_subprocess_exec("caffeinate", "-i", "-w", str(pid),
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        pass


def _terminate(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the job's whole process group (the job and its ffmpeg children)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


class JobQueue:
    def __init__(self, command_for: Callable[[Job], list[str]] = default_command, state_dir: Path | None = None):
        self.command_for = command_for
        self.state_dir = state_dir or STATE_DIR
        self.jobs: dict[int, Job] = {}
        self._listeners: set[asyncio.Queue] = set()
        self._tasks: set[asyncio.Task] = set()
        self._started = False
        self._next_id = 1
        self.paused = False
        self.pause_reason = ""
        self.auto_paused = False  # paused because the share went away; resumes when it's back
        self.share_check: Callable[[], bool] = lambda: config.DATA_ROOT.is_dir()

    # -- persistence
    @property
    def _state_file(self) -> Path:
        return self.state_dir / "jobs.json"

    def log_path(self, job: Job) -> Path:
        return self.state_dir / "logs" / f"job-{job.id}.log"

    def _save(self) -> None:
        keep = sorted(self.jobs.values(), key=lambda j: j.id)
        finished = [j for j in keep if j.status not in ("queued", "running")]
        drop = {j.id for j in finished[:-HISTORY]} if len(finished) > HISTORY else set()
        data = {"paused": self.paused, "reason": self.pause_reason, "auto": self.auto_paused,
                "jobs": [j.summary() for j in keep if j.id not in drop]}
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self._state_file)
        except OSError:
            pass

    def _restore(self) -> None:
        try:
            saved = json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            return
        if isinstance(saved, dict):
            self.paused, self.pause_reason, self.auto_paused = saved["paused"], saved["reason"], saved["auto"]
            saved = saved["jobs"]
        for d in saved:
            job = Job(**{k: d[k] for k in Job.FIELDS if k in d})
            job.order = job.order or float(job.id)
            if job.status == "running":  # the server stopped mid-job: pick it up again
                job.status = "queued"
                self._write_log(job, "--- The server restarted while this job was running; resuming. "
                                     "Steps that already finished are skipped.")
            self.jobs[job.id] = job
            self._next_id = max(self._next_id, job.id + 1)
        try:  # the in-memory log tail, so the app shows recent lines after a restart
            for job in self.jobs.values():
                f = self.log_path(job)
                if f.exists():
                    job.log.extend(f.read_text(errors="replace").splitlines()[-400:])
        except OSError:
            pass

    # -- lifecycle
    def start(self, watch_share: bool = True) -> None:
        if not self._started:
            self._started = True
            self._restore()
            self._schedule()
            if watch_share:
                self._spawn(self._watch_share())

    async def stop(self) -> None:
        """Server shutdown (e.g. the app quits): running jobs stop and stay 'running' in the saved
        queue, so the next start resumes them from their checkpoints instead of marking them failed."""
        for t in list(self._tasks):
            t.cancel()
        for job in self.jobs.values():
            if job.status == "running":
                self._write_log(job, "Stopped because Reolapse closed; it will continue next time.")
            if job.proc and job.proc.returncode is None:
                _terminate(job.proc)
        self._save()

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _share_ok(self) -> bool:
        """Is the archive share reachable? (A dead SMB mount can hang a stat, so time-limit it.)"""
        try:
            return await asyncio.wait_for(asyncio.to_thread(self.share_check), timeout=10)
        except (asyncio.TimeoutError, OSError):
            return False

    async def _watch_share(self, every: float = 15.0) -> None:
        """Pause the queue when the share disappears (laptop away from home); resume when it's back."""
        while True:
            ok = await self._share_ok()
            if not ok and not self.paused:
                self.pause("the network share isn't reachable", auto=True)
            elif ok and self.paused and self.auto_paused:
                self.resume()
            await asyncio.sleep(every)

    # -- pause / resume
    def queue_state(self) -> dict:
        return {"paused": self.paused, "reason": self.pause_reason, "auto": self.auto_paused}

    def pause(self, reason: str = "paused", auto: bool = False) -> None:
        """Stop starting jobs, and stop running ones now; they go back to the front of the queue and
        continue from their last checkpoint on resume (downloads keep finished files, analysis keeps
        the objects found so far)."""
        self.paused, self.pause_reason, self.auto_paused = True, reason, auto
        for job in self.jobs.values():
            if job.status == "running" and not job.interrupted:
                job.interrupted = True  # also covers a job whose process is only just starting
                self._write_log(job, f"Paused ({reason}); it will continue from its last checkpoint.")
                if job.proc and job.proc.returncode is None:
                    _terminate(job.proc)
        self._save()
        self._publish_queue()
        self._schedule()

    def resume(self) -> None:
        self.paused, self.pause_reason, self.auto_paused = False, "", False
        self._save()
        self._publish_queue()
        self._schedule()

    def _publish_queue(self) -> None:
        for q in list(self._listeners):
            try:
                q.put_nowait({"queue": self.queue_state()})
            except asyncio.QueueFull:
                pass

    # -- API (call from the event loop: the HTTP handlers are async)
    def submit(self, kind: str, day: str, cameras: list[str], options: dict | None = None) -> list[Job]:
        """Queue a job; "run" (Everything) becomes download + find objects + render jobs."""
        if kind not in KINDS:
            raise ValueError(f"unknown job kind {kind!r}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("day must be YYYY-MM-DD")
        kinds = list(STEPS) if kind == "run" else [kind]
        last = max((j.order for j in self.jobs.values()), default=0.0)
        created = []
        for k in kinds:
            job = Job(k, day, list(cameras), options or {}, id=self._next_id, order=last + 1 + len(created))
            self._next_id += 1
            if len(kinds) > 1:
                job.group = created[0].id if created else job.id
            self.jobs[job.id] = job
            when = date.fromisoformat(day).strftime("%a %b %-d")
            only = ", ".join(ONLY_NAME.get(o, o) for o in job.options.get("only", []))
            self._write_log(job, f"Queued: {KIND_LABEL[k]}{f' ({only} only)' if only else ''} for {when}; "
                                 f"cameras: {', '.join(job.cameras)}")
            created.append(job)
        self._save()
        self._schedule()
        return created

    def cancel(self, job_id: int) -> Job:
        job = self.jobs[job_id]
        if job.status == "queued":
            job.status = "cancelled"
            job.finished = time.time()
            self._write_log(job, "Cancelled before it started.")
            self._save()
            self._schedule()
        elif job.status == "running" and job.proc:
            job.status = "cancelled"
            _terminate(job.proc)
        return job

    def retry(self, job_id: int) -> Job:
        """Put a failed or cancelled job back in the queue (at the end)."""
        job = self.jobs[job_id]
        if job.status not in ("failed", "cancelled"):
            raise ValueError("only failed or cancelled jobs can be retried")
        job.status, job.returncode, job.finished, job.progress, job.status_line = "queued", None, None, {}, ""
        job.order = max(j.order for j in self.jobs.values()) + 1
        self._write_log(job, "Retry requested; queued again.")
        self._save()
        self._schedule()
        return job

    def move(self, job_id: int, delta: int) -> Job:
        """Move a queued job up (delta < 0) or down among the queued jobs."""
        job = self.jobs[job_id]
        if job.status != "queued":
            raise ValueError("only queued jobs can be moved")
        queued = sorted((j for j in self.jobs.values() if j.status == "queued"), key=lambda j: j.order)
        i = queued.index(job)
        queued.insert(max(0, min(len(queued) - 1, i + delta)), queued.pop(i))
        base = min(j.order for j in queued)
        for n, j in enumerate(queued):
            j.order = base + n
        self._save()
        self._schedule()
        return job

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    # -- scheduling
    def _blocker(self, job: Job) -> str:
        """Why a queued job can't start yet because of its day's earlier steps ('' = it can)."""
        earlier = [o for o in self.jobs.values() if o is not job and o.day == job.day
                   and STAGE[o.kind] < STAGE[job.kind] and set(o.cameras) & set(job.cameras)]
        for o in sorted(earlier, key=lambda o: o.order):
            if o.status in ("queued", "running"):
                return f"waits for this day's {STEP_NAME[o.kind]} (job {o.id})"
        for stage in sorted({STAGE[o.kind] for o in earlier}):
            latest = max((o for o in earlier if STAGE[o.kind] == stage), key=lambda o: o.id)
            if latest.status in ("failed", "cancelled"):
                return f"this day's {STEP_NAME[latest.kind]} {latest.status} (job {latest.id}); retry it to continue"
        return ""

    def _schedule(self) -> None:
        """Start the next runnable job in each idle lane and refresh the 'waiting' reasons."""
        running_lanes = {j.lane for j in self.jobs.values() if j.status == "running"}
        queued = sorted((j for j in self.jobs.values() if j.status == "queued"), key=lambda j: j.order)
        to_start = []
        for job in queued:
            if self.paused:
                reason = f"queue paused: {self.pause_reason}"
                if reason != job.waiting:
                    job.waiting = reason
                    self._publish(job)
                continue
            reason = self._blocker(job)
            if not reason and job.lane in running_lanes:
                reason = f"next in the {job.lane} lane" if job.lane not in {j.lane for j in to_start} else ""
            if not reason and job.lane not in running_lanes:
                to_start.append(job)
                running_lanes.add(job.lane)
                continue
            if reason != job.waiting:
                job.waiting = reason
                self._publish(job)
        for job in to_start:
            job.waiting = ""
            job.status = "running"  # claim the lane now; _run fills in the rest
            self._spawn(self._run(job))

    # -- internals
    def _publish(self, job: Job, line: str | None = None) -> None:
        event = {"job": job.summary()}
        if line is not None:
            event["line"] = line
        for q in list(self._listeners):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer: it will resync from the next snapshot

    def _write_log(self, job: Job, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        job.log.append(f"{stamp}  {line}")
        try:
            f = self.log_path(job)
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d')} {stamp}  {line}\n")
        except OSError:
            pass
        self._publish(job, job.log[-1])

    async def _run(self, job: Job) -> None:
        job.status, job.started, job.status_line = "running", time.time(), ""
        self._save()
        self._write_log(job, "Started.")
        env = {**os.environ, "REOLAPSE_PROGRESS": "json", "PYTHONUNBUFFERED": "1"}
        try:
            job.proc = await asyncio.create_subprocess_exec(
                *self.command_for(job), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
                start_new_session=True)  # own process group: stopping it also stops its ffmpeg helpers
            if job.interrupted or job.status == "cancelled":  # paused/cancelled while it was starting
                _terminate(job.proc)
            await _keep_awake_while(job.proc.pid)
            buf = ""
            while chunk := await job.proc.stdout.read(4096):
                buf += chunk.decode(errors="replace")
                parts = re.split(r"([\r\n])", buf)
                buf = parts.pop()  # unterminated tail
                for text, end in zip(parts[0::2], parts[1::2]):
                    self._segment(job, text, end)
            if buf:  # unterminated tail when the process ends: an in-place status, not a log line
                self._segment(job, buf, "\r")
            job.returncode = await job.proc.wait()
        except Exception as e:  # spawn failure etc.
            self._write_log(job, f"Could not run the job: {e}")
            job.returncode = -1
        job.status_line = ""
        if job.returncode != 0 and not job.interrupted and job.status != "cancelled" and not await self._share_ok():
            job.interrupted = True  # failed because the share went away: pause rather than fail
            self.pause("the network share isn't reachable", auto=True)
        if job.interrupted:
            job.interrupted, job.status, job.proc, job.progress = False, "queued", None, {}
            job.order = min((j.order for j in self.jobs.values()), default=0.0) - 1  # first when resumed
            self._save()
            self._publish(job)
            self._schedule()
            return
        job.finished = time.time()
        took = _dur(job.finished - job.started)
        if job.status == "cancelled":
            self._write_log(job, f"Cancelled after {took}.")
        elif job.returncode == 0:
            job.status = "done"
            self._write_log(job, f"Finished in {took}.")
        else:
            job.status = "failed"
            self._write_log(job, f"Failed after {took} (exit code {job.returncode}). The lines above show where it stopped.")
        job.proc = None
        self._save()
        self._publish(job)
        self._schedule()

    def _segment(self, job: Job, text: str, end: str) -> None:
        text = text.rstrip()
        if not text:
            return
        if text.startswith("@@"):
            try:
                job.progress = json.loads(text[2:])
            except json.JSONDecodeError:
                self._write_log(job, text)
                return
            self._publish(job)
        elif end == "\r":  # in-place status: shown live, not logged
            job.status_line = text.strip()
            self._publish(job)
        else:
            job.status_line = ""
            self._write_log(job, text)
