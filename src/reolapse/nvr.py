"""Thin async wrapper around reolink_aio for the bits reolapse needs."""

from __future__ import annotations

import asyncio
import os
import re
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


KEYRING_SERVICE = "Reolapse NVR"


def credentials() -> dict:
    """NVR address and login: REOLINK_* environment variables (e.g. a .env in a source checkout)
    win; otherwise the app's settings, with the password from the system keychain."""
    if os.environ.get("REOLINK_HOST") and os.environ.get("REOLINK_USER") and os.environ.get("REOLINK_PASSWORD"):
        return {"host": os.environ["REOLINK_HOST"], "user": os.environ["REOLINK_USER"],
                "password": os.environ["REOLINK_PASSWORD"], "port": int(os.environ.get("REOLINK_PORT", "80")),
                "source": ".env"}
    from .settings import load

    s = load()["nvr"]
    password = None
    if s["host"] and s["user"]:
        try:
            import keyring

            password = keyring.get_password(KEYRING_SERVICE, f"{s['user']}@{s['host']}")
        except Exception:
            password = None
    return {"host": s["host"], "user": s["user"], "password": password, "port": int(s["port"] or 80), "source": "app"}


def save_credentials(host: str, user: str, port: int = 80, password: str | None = None) -> None:
    """Store the NVR address/login in settings and the password in the system keychain."""
    from .settings import save

    save({"nvr": {"host": host.strip(), "user": user.strip(), "port": int(port or 80)}})
    if password:
        import keyring

        keyring.set_password(KEYRING_SERVICE, f"{user.strip()}@{host.strip()}", password)


@asynccontextmanager
async def connect():
    c = credentials()
    if not (c["host"] and c["user"] and c["password"]):
        raise SystemExit("The NVR address and login aren't set yet: enter them in Settings → NVR connection")
    nvr = Host(c["host"], c["user"], c["password"], port=c["port"], use_https=False)
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


class DownloadError(RuntimeError):
    pass


def _redact(text: str) -> str:
    """Keep NVR credentials and session tokens out of logs."""
    return re.sub(r"(token|password|user)=[^&\s']*", r"\1=***", text)


async def download_file(nvr: Host, channel: int, vod: VOD_file, stream: str, dest: Path, progress=None,
                        attempts: int = 3, log=print) -> Path:
    """Download one recording file by its NVR name. Writes to .part and renames when complete.

    The RLN16 rejects NvrDownload-prepared fragments and FLV playback, but serves whole
    recording files via cmd=Download with the search result's name.

    Retries: the NVR's session token lapses after about an hour (it then answers 404/401), and
    Wi-Fi drops happen, so a failed attempt logs in afresh and tries again.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, attempts + 1):
        try:
            _, url = await nvr.get_vod_source(channel, vod.file_name, stream, VodRequestType.DOWNLOAD)
            n = 0
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=120)) as client:
                async with client.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise DownloadError(f"the NVR answered {r.status_code} {r.reason_phrase}")
                    with open(part, "wb") as fh:
                        async for chunk in r.aiter_bytes(1 << 20):
                            fh.write(chunk)
                            n += len(chunk)
                            if progress:
                                progress(n)
            if n == 0:
                raise DownloadError("the NVR sent an empty file")
            part.rename(dest)
            return dest
        except (DownloadError, httpx.HTTPError, OSError) as e:
            part.unlink(missing_ok=True)
            why = _redact(str(e)) or type(e).__name__
            if attempt == attempts:
                raise DownloadError(f"couldn't download {vod.start_time:%H:%M} after {attempts} tries: {why}") from None
            wait = 10 * attempt
            log(f"\n   download attempt {attempt} failed ({why}); logging in to the NVR again and retrying in {wait}s")
            await asyncio.sleep(wait)
            try:  # a fresh session: the usual cause is an expired token
                await nvr.logout()
                await nvr.login()
            except Exception as le:
                log(f"   (logging in again failed: {_redact(str(le))})")
    raise AssertionError("unreachable")
