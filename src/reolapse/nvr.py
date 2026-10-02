"""Thin async wrapper around reolink_aio for the bits reolapse needs."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from reolink_aio.api import Host
from reolink_aio.enums import VodRequestType
from reolink_aio.typings import VOD_file, VOD_trigger

load_dotenv()

AI_TRIGGERS = VOD_trigger.PERSON | VOD_trigger.VEHICLE | VOD_trigger.ANIMAL


@asynccontextmanager
async def connect():
    host = os.environ.get("REOLINK_HOST")
    user = os.environ.get("REOLINK_USER")
    password = os.environ.get("REOLINK_PASSWORD")
    if not (host and user and password):
        raise SystemExit("Set REOLINK_HOST, REOLINK_USER and REOLINK_PASSWORD in .env (see .env.example)")
    port = int(os.environ.get("REOLINK_PORT", "80"))
    nvr = Host(host, user, password, port=port, use_https=False)
    try:
        await nvr.get_host_data()
        yield nvr
    finally:
        await nvr.logout()


def find_channel(nvr: Host, name: str) -> int:
    """Resolve a camera name (case-insensitive, spaces/underscores ignored) to an NVR channel."""
    want = name.lower().replace("_", "").replace(" ", "")
    for ch in nvr.channels:
        if nvr.camera_name(ch).lower().replace("_", "").replace(" ", "") == want:
            return ch
    names = ", ".join(f"{nvr.camera_name(c)!r}" for c in nvr.channels)
    raise SystemExit(f"No camera named {name!r}. Known: {names}")


async def event_files(nvr: Host, channel: int, start: datetime, end: datetime, stream: str = "sub"):
    """Return {trigger: [VOD_file]} for typed events in the window."""
    _, by_trigger = await nvr.baichuan.search_vod_type(channel=channel, start=start, end=end, stream=stream)
    return {t: files for t, files in by_trigger.items() if files}


async def recording_files(nvr: Host, channel: int, start: datetime, end: datetime, stream: str) -> list[VOD_file]:
    """Continuous-recording files (the NVR keeps ~1 hour per file) overlapping [start, end]."""
    _, files = await nvr.request_vod_files(channel, start, end, stream=stream)
    return sorted(files, key=lambda f: f.start_time)


async def download_file(nvr: Host, channel: int, vod: VOD_file, stream: str, dest: Path, progress=None) -> Path:
    """Download one recording file by its NVR name. Writes to .part and renames when complete.

    The RLN16 rejects NvrDownload-prepared fragments and FLV playback, but serves whole
    recording files via cmd=Download with the search result's name.
    """
    _, url = await nvr.get_vod_source(channel, vod.file_name, stream, VodRequestType.DOWNLOAD)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    n = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=120)) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(part, "wb") as fh:
                async for chunk in r.aiter_bytes(1 << 20):
                    fh.write(chunk)
                    n += len(chunk)
                    if progress:
                        progress(n)
    if n == 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"empty download for {vod}")
    part.rename(dest)
    return dest
