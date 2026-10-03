import asyncio
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from reolapse import config, fetch
from reolapse.media import is_fragmented

TZ = timezone(timedelta(hours=-4))


@pytest.fixture
def frag_mp4(tmp_path):
    p = tmp_path / "frag.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
                    "-c:v", "libx264", "-movflags", "frag_keyframe+empty_moov", str(p)], check=True)
    return p


def test_fetch_camera_downloads_remuxes_and_logs(tmp_path, monkeypatch, capsys, frag_mp4):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "share")
    monkeypatch.setattr(fetch, "LOCAL_TMP", tmp_path / "local")
    day = date(2026, 9, 30)
    vods = [SimpleNamespace(start_time=datetime(2026, 9, 30, h, 0, tzinfo=TZ), end_time=datetime(2026, 9, 30, h, 59, tzinfo=TZ),
                            size=frag_mp4.stat().st_size) for h in (0, 1)]

    async def event_files(nvr, ch, start, end, stream="sub"):
        return {fetch.nvrmod.VOD_trigger.PERSON: [SimpleNamespace(start_time=vods[0].start_time, end_time=vods[0].end_time)]}

    async def recording_files(nvr, ch, start, end, stream):
        return vods

    async def download_file(nvr, ch, vod, stream, dest, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(frag_mp4, dest)
        progress and progress(dest.stat().st_size)
        return dest

    monkeypatch.setattr(fetch.nvrmod, "find_channel", lambda nvr, cam: 2)
    monkeypatch.setattr(fetch.nvrmod, "event_files", event_files)
    monkeypatch.setattr(fetch.nvrmod, "recording_files", recording_files)
    monkeypatch.setattr(fetch.nvrmod, "download_file", download_file)

    asyncio.run(fetch.fetch_camera(None, "Garage", day, "main"))
    raw = tmp_path / "share" / "garage" / "2026-09-30" / "raw"
    files = sorted(raw.iterdir())
    assert [f.name for f in files] == ["main_20260930_000000.mp4", "main_20260930_010000.mp4"]
    assert not any(is_fragmented(f) for f in files)  # remuxed to seekable
    assert not list((tmp_path / "local").iterdir())  # local temp files cleaned up
    index = json.loads((raw.parent / "files.json").read_text())
    assert [e["stream"] for e in index] == ["main", "main"]
    out = capsys.readouterr().out
    assert "Clear (main stream)" in out and "NVR events: 1 person" in out
    assert "made seekable" in out and "copied to the share" in out and "Garage finished" in out

    # second run: everything already there
    asyncio.run(fetch.fetch_camera(None, "Garage", day, "main"))
    assert "already downloaded, skipping" in capsys.readouterr().out


def test_download_retries_with_fresh_login(tmp_path, monkeypatch):
    import httpx
    from reolapse import nvr as nvrmod

    calls = {"n": 0, "logins": 0}

    class FakeNVR:
        async def get_vod_source(self, ch, name, stream, kind):
            return "video/mp4", "http://nvr.test/download?token=SECRET123"

        async def logout(self):
            pass

        async def login(self):
            calls["logins"] += 1

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(404)  # expired session
        return httpx.Response(200, content=b"x" * 1000)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(nvrmod.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    real_sleep = asyncio.sleep
    monkeypatch.setattr(nvrmod.asyncio, "sleep", lambda s: real_sleep(0))
    vod = SimpleNamespace(file_name="f", start_time=datetime(2026, 9, 29, 15, tzinfo=TZ))
    logs = []
    out = asyncio.run(nvrmod.download_file(FakeNVR(), 23, vod, "main", tmp_path / "a.mp4", log=logs.append))
    assert out.read_bytes() == b"x" * 1000 and calls["logins"] == 1
    assert "404" in logs[0] and "SECRET123" not in " ".join(logs)

    calls["n"] = -10  # every try fails
    monkeypatch.setattr(nvrmod.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(nvrmod.DownloadError) as err:
        asyncio.run(nvrmod.download_file(FakeNVR(), 23, vod, "main", tmp_path / "b.mp4", log=logs.append))
    assert "after 3 tries" in str(err.value) and "SECRET123" not in str(err.value)
