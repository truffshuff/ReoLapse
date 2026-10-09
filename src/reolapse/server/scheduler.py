"""The nightly run: at the set time, queue the days that still need work, then clean up storage.

Each night (or the first time the app is open after that time, if the Mac was off or asleep) it looks
at yesterday and the few days before it (`nightly.catch_up_days`): for each included camera, a day
with nothing yet (or only part of a download) gets the whole pipeline, an analyzed day only a render,
and a rendered day nothing. Days already queued are left alone. Then, if `retention.auto` is on, the
storage cleanup runs (see reolapse.cleanup).

Turn it on in one Mac only: two Macs sharing a share would both queue the same days.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import cleanup
from .. import settings as settings_mod
from ..config import slug
from . import library


class Scheduler:
    def __init__(self, queue, state_dir: Path):
        self.queue = queue
        self.file = Path(state_dir) / "nightly.json"
        self.state = self._load()
        self.cleaning = False

    def _load(self) -> dict:
        try:
            return json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.state, indent=1))
        except OSError:
            pass

    # ------------------------------------------------------------ timing
    @staticmethod
    def _at(day: date, hhmm: str) -> datetime:
        h, m = (int(x) for x in hhmm.split(":"))
        return datetime(day.year, day.month, day.day, h, m)

    def status(self) -> dict:
        s = settings_mod.load()
        n = s["nightly"]
        now = datetime.now()
        nxt = self._at(now.date(), n["time"])
        if nxt <= now or self.state.get("last_run") == now.date().isoformat():
            nxt = self._at(now.date() + timedelta(days=1), n["time"])
        return {"enabled": n["enabled"], "time": n["time"], "next_run": nxt.isoformat(timespec="minutes") if n["enabled"] else None,
                **{k: self.state.get(k) for k in ("last_run", "last_queued", "last_cleanup")}}

    def due(self, now: datetime | None = None) -> bool:
        n = settings_mod.load()["nightly"]
        now = now or datetime.now()
        return bool(n["enabled"]) and now >= self._at(now.date(), n["time"]) \
            and self.state.get("last_run") != now.date().isoformat()

    async def loop(self, every: float = 60.0) -> None:
        while True:
            try:
                if self.due():
                    await self.run_now()
            except Exception as e:  # never let a bad night stop the scheduler
                self.state["last_error"] = str(e)
                self._save()
            await asyncio.sleep(every)

    # ------------------------------------------------------------ work
    def busy_days(self) -> set[tuple[str, str]]:
        """(camera, day) with a queued or running job of any kind."""
        return {(c, j.day) for j in self.queue.jobs.values() if j.status in ("queued", "running") for c in j.cameras}

    def plan_days(self, today: date | None = None) -> list[dict]:
        """What tonight's run would queue: [{kind, day, cameras}]."""
        s = settings_mod.load()
        today = today or date.today()
        busy = self.busy_days()
        out = []
        for back in range(1, max(1, int(s["nightly"].get("catch_up_days", 3))) + 1):
            day = (today - timedelta(days=back)).isoformat()
            need = {"run": [], "render": []}
            for camera in s["cameras"]:
                if (camera, day) in busy:
                    continue
                status = library.camera_day(camera, day, fresh=True)["status"]
                if status in ("empty", "fetched"):
                    need["run"].append(camera)
                elif status == "analyzed":
                    need["render"].append(camera)
            out += [{"kind": k, "day": day, "cameras": cams} for k, cams in need.items() if cams]
        return out

    async def run_now(self) -> dict:
        """Queue the work (oldest day first, so a day about to leave the NVR goes first), then clean up."""
        today = date.today()
        self.state["last_run"] = today.isoformat()
        work = sorted(await asyncio.to_thread(self.plan_days, today), key=lambda w: w["day"])
        for w in work:
            self.queue.submit(w["kind"], w["day"], w["cameras"], {"source": "nightly"})
        self.state["last_queued"] = [f"{w['day']}: {w['kind']} {', '.join(w['cameras'])}" for w in work]
        self._save()
        if settings_mod.load()["retention"].get("auto"):
            await self.clean()
        return self.status()

    async def clean(self) -> dict:
        """Run the storage cleanup now (skipping days with queued or running jobs)."""
        if self.cleaning:
            return {"busy": True}
        self.cleaning = True
        try:
            protected = {(slug(c), d) for c, d in self.busy_days()}
            p = await asyncio.to_thread(cleanup.plan, settings_mod.load()["retention"], protected)
            freed = await asyncio.to_thread(cleanup.execute, p, lambda *_: None)
            self.state["last_cleanup"] = {"at": time.strftime("%Y-%m-%dT%H:%M"), "freed": freed,
                                          "items": p.summary()}
            self._save()
            library._cache.clear()
            library._day_list.clear()
            return self.state["last_cleanup"]
        finally:
            self.cleaning = False
