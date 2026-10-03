import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from reolapse import settings as settings_mod
from reolapse.server import updates
from reolapse.server.app import create_app
from reolapse.server.jobs import JobQueue


@pytest.fixture
def fake_keychain(monkeypatch, tmp_path):
    import keyring
    store = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, a, p: store.__setitem__((s, a), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, a: store.get((s, a)))
    monkeypatch.setattr(keyring, "delete_password", lambda s, a: store.pop((s, a), None))
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", tmp_path / "settings.json")
    import reolapse.server.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("REOLAPSE_APP_VERSION", "0.1.2")
    monkeypatch.setattr(updates.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *a, **k: None)  # don't actually open the dmg
    return store


def github(request):
    assert request.headers["authorization"] == "Bearer good-token"
    if request.url.path.endswith("/releases/latest"):
        return httpx.Response(200, json={"tag_name": "v0.1.3", "name": "v0.1.3", "body": "Ignore parked cars",
                                         "html_url": "https://github.com/x", "published_at": "2026-10-03",
                                         "assets": [{"id": 7, "name": "Reolapse-0.1.3-mac-arm64.dmg", "size": 5}]})
    if request.url.path.endswith("/releases/assets/7"):
        return httpx.Response(200, content=b"12345")
    return httpx.Response(404)


def test_version_compare():
    assert updates.parse_version("0.1.10") > updates.parse_version("0.1.9")
    assert updates.parse_version("v1.0") == (1, 0, 0)


def test_check_and_download(fake_keychain, tmp_path):
    up = updates.Updater(lambda: "truffshuff/ReoLapse",
                         client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(github)))
    with TestClient(create_app(JobQueue(), up)) as client:
        s = client.get("/api/update", params={"check": 1}).json()
        assert s["token_set"] is False and "token" in s["error"]
        s = client.put("/api/update/token", json={"token": "good-token"}).json()
        assert s["newer"] is True and s["latest"]["version"] == "0.1.3" and s["current"] == "0.1.2"
        assert client.post("/api/update/download").status_code == 200
        for _ in range(50):
            d = client.get("/api/update").json()["download"]
            if d["status"] != "downloading":
                break
            asyncio.run(asyncio.sleep(0.02))
        assert d["status"] == "done" and (tmp_path / "Downloads" / "Reolapse-0.1.3-mac-arm64.dmg").read_bytes() == b"12345"
    assert fake_keychain[("Reolapse GitHub", "release token")] == "good-token"
