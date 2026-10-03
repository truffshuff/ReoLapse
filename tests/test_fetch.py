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
