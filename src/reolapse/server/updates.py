"""Checking GitHub Releases for a newer Reolapse, and fetching its installer.

The repository is private, so the GitHub API needs a token: a fine-grained, read-only token
(Contents: read) for the one repository, kept in the system keychain. macOS won't auto-install an
app that isn't signed with an Apple developer certificate, so an update is downloaded to
~/Downloads and its disk image opened; the person drags the new app into Applications.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

KEYRING_SERVICE = "Reolapse GitHub"
KEYRING_ACCOUNT = "release token"
API = "https://api.github.com"
CHECK_EVERY = 3600.0


def app_version() -> str:
    """The desktop app's version (the packaged app passes it in), else this checkout's package.json."""
    if os.environ.get("REOLAPSE_APP_VERSION"):
        return os.environ["REOLAPSE_APP_VERSION"]
    try:
        import json

        pkg = Path(__file__).resolve().parents[3] / "app.nosync" / "package.json"
        return json.loads(pkg.read_text())["version"]
    except Exception:
        return "0.0.0"


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("-")[0])
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def get_token() -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        return None


def set_token(token: str | None) -> None:
    import keyring

    if token:
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, token.strip())
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        except Exception:
            pass


def _headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Reolapse"}


class Updater:
    def __init__(self, repo_for, client_factory=lambda: httpx.AsyncClient(timeout=30, follow_redirects=True)):
        self.repo_for = repo_for  # callable -> "owner/name" (from settings)
        self.client_factory = client_factory
        self.state: dict = {"checked_at": None, "latest": None, "error": None}
        self.download: dict = {"status": "idle", "done": 0, "total": 0, "path": None, "error": None}
        self._task: asyncio.Task | None = None

    def status(self) -> dict:
        current = app_version()
        latest = self.state.get("latest")
        newer = bool(latest and parse_version(latest["version"]) > parse_version(current))
        return {"current": current, "token_set": bool(get_token()), "repo": self.repo_for(),
                "newer": newer, **self.state, "download": self.download}

    async def check(self, force: bool = False) -> dict:
        if not force and self.state["checked_at"] and time.time() - self.state["checked_at"] < CHECK_EVERY:
            return self.status()
        token = get_token()
        if not token:
            self.state.update(error="Add a GitHub token below to check for updates.", checked_at=None)
            return self.status()
        try:
            async with self.client_factory() as client:
                r = await client.get(f"{API}/repos/{self.repo_for()}/releases/latest", headers=_headers(token))
            if r.status_code in (401, 403):
                raise RuntimeError("GitHub refused the token (expired, or missing Contents: read on this repository)")
            if r.status_code == 404:
                raise RuntimeError("no release found (or the token can't see this repository)")
            r.raise_for_status()
            rel = r.json()
            asset = next((a for a in rel.get("assets", []) if a["name"].endswith(".dmg")), None)
            self.state.update(checked_at=time.time(), error=None, latest={
                "version": rel["tag_name"].lstrip("v"), "name": rel.get("name") or rel["tag_name"],
                "notes": rel.get("body") or "", "published_at": rel.get("published_at"), "url": rel.get("html_url"),
                "asset": {"id": asset["id"], "name": asset["name"], "size": asset["size"]} if asset else None})
        except Exception as e:
            self.state.update(checked_at=time.time(), error=str(e) or type(e).__name__)
        return self.status()

    def start_download(self) -> dict:
        latest = self.state.get("latest")
        if not latest or not latest.get("asset"):
            raise ValueError("check for updates first")
        if self.download["status"] == "downloading":
            return self.download
        self.download = {"status": "downloading", "done": 0, "total": latest["asset"]["size"], "path": None, "error": None}
        self._task = asyncio.get_running_loop().create_task(self._download(latest["asset"]))
        return self.download

    async def _download(self, asset: dict) -> None:
        dest = Path.home() / "Downloads" / asset["name"]
        part = dest.with_name(dest.name + ".part")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            async with self.client_factory() as client:
                async with client.stream("GET", f"{API}/repos/{self.repo_for()}/releases/assets/{asset['id']}",
                                         headers=_headers(get_token() or "", "application/octet-stream")) as r:
                    r.raise_for_status()
                    with open(part, "wb") as fh:
                        async for chunk in r.aiter_bytes(1 << 20):
                            fh.write(chunk)
                            self.download["done"] += len(chunk)
            part.replace(dest)
            self.download.update(status="done", path=str(dest))
            if sys.platform == "darwin":  # mount it: Finder shows Reolapse and the Applications folder
                subprocess.Popen(["open", str(dest)])
        except Exception as e:
            part.unlink(missing_ok=True)
            self.download.update(status="failed", error=str(e) or type(e).__name__)
