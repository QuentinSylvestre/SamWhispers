"""Tests for the config web server (FastAPI)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from samwhispers.supervisor import WorkerState
from samwhispers.webserver import create_app


class FakeSupervisor:
    """Minimal stand-in implementing the bits the web server uses."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._state = WorkerState.RUNNING

    @property
    def state(self) -> WorkerState:
        return self._state

    def pause(self) -> None:
        self.calls.append("pause")
        self._state = WorkerState.PAUSED

    def resume(self) -> None:
        self.calls.append("resume")
        self._state = WorkerState.RUNNING

    def restart(self) -> None:
        self.calls.append("restart")


@pytest.fixture
def client_and_sup(tmp_path: Path) -> tuple[TestClient, FakeSupervisor, Path]:
    sup = FakeSupervisor()
    path = tmp_path / "config.toml"
    app = create_app(sup, config_path=path)  # type: ignore[arg-type]
    return TestClient(app), sup, path


def test_index_served(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    res = client.get("/")
    assert res.status_code == 200
    assert "SamWhispers" in res.text


def test_meta(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    meta = client.get("/api/meta").json()
    assert {"hold", "toggle"} <= set(meta["modes"])
    assert any(lang["code"] == "auto" for lang in meta["languages"])
    assert "openai" in meta["providers"]


def test_status(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    assert client.get("/api/status").json()["state"] == "running"


def test_get_config_defaults(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    cfg = client.get("/api/config").json()
    assert cfg["hotkey"]["key"] == "ctrl+shift+space"


def test_put_config_saves_and_restarts(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, path = client_and_sup
    cfg = client.get("/api/config").json()
    cfg["whisper"]["managed"] = False
    cfg["hotkey"]["key"] = "ctrl+alt+s"
    res = client.put("/api/config", json=cfg)
    assert res.status_code == 200
    body = res.json()
    assert body["saved"] is True and body["restarted"] is True
    assert "restart" in sup.calls
    assert path.is_file()


def test_put_config_no_change_does_not_restart(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, path = client_and_sup
    cfg = client.get("/api/config").json()
    cfg["whisper"]["managed"] = False
    client.put("/api/config", json=cfg)  # first write establishes on-disk state
    sup.calls.clear()
    again = client.put("/api/config", json=cfg).json()  # identical payload
    assert again["restarted"] is False
    assert sup.calls == []


def test_put_config_invalid_returns_400(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, path = client_and_sup
    cfg = client.get("/api/config").json()
    cfg["hotkey"]["mode"] = "bogus"
    res = client.put("/api/config", json=cfg)
    assert res.status_code == 400
    assert "mode" in res.json()["detail"]
    assert "restart" not in sup.calls


def test_worker_actions(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, sup, _ = client_and_sup
    assert client.post("/api/worker/pause").json()["state"] == "paused"
    assert client.post("/api/worker/resume").json()["state"] == "running"
    assert client.post("/api/worker/restart").status_code == 200
    assert sup.calls == ["pause", "resume", "restart"]


def test_worker_unknown_action(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    assert client.post("/api/worker/frobnicate").status_code == 400


def test_worker_action_without_supervisor(tmp_path: Path) -> None:
    app = create_app(None, config_path=tmp_path / "c.toml")
    client = TestClient(app)
    assert client.post("/api/worker/pause").status_code == 503
