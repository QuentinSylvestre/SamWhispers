"""Tests for the config web server (FastAPI)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from samwhispers.history import HistoryStore
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

    @property
    def logs(self) -> list[str]:
        return ["line1", "line2"]

    def pause(self) -> None:
        self.calls.append("pause")
        self._state = WorkerState.PAUSED

    def resume(self) -> None:
        self.calls.append("resume")
        self._state = WorkerState.RUNNING

    def restart(self) -> None:
        self.calls.append("restart")

    def apply_config_change(self, restart_whisper: bool) -> None:
        self.calls.append(f"apply(whisper={restart_whisper})")


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
    assert {"chunked", "faster_whisper"} <= set(meta["stream_engines"])
    assert {"preview", "progressive"} <= set(meta["stream_modes"])


def test_status(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    assert client.get("/api/status").json()["state"] == "running"


def test_logs_endpoint(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    body = client.get("/api/logs").json()
    assert body["lines"] == ["line1", "line2"]


def test_autostart_status(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    with (
        patch("samwhispers.autostart.is_supported", return_value=True),
        patch("samwhispers.autostart.is_enabled", return_value=True),
    ):
        body = client.get("/api/autostart").json()
    assert body == {"supported": True, "enabled": True}


def test_autostart_enable(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    with (
        patch("samwhispers.autostart.is_supported", return_value=True),
        patch("samwhispers.autostart.enable") as enable,
        patch("samwhispers.autostart.disable") as disable,
        patch("samwhispers.autostart.is_enabled", return_value=True),
    ):
        body = client.put("/api/autostart", json={"enabled": True}).json()
    enable.assert_called_once()
    disable.assert_not_called()
    assert body["enabled"] is True


def test_autostart_disable(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    with (
        patch("samwhispers.autostart.is_supported", return_value=True),
        patch("samwhispers.autostart.disable") as disable,
        patch("samwhispers.autostart.is_enabled", return_value=False),
    ):
        body = client.put("/api/autostart", json={"enabled": False}).json()
    disable.assert_called_once()
    assert body["enabled"] is False


def test_autostart_unsupported(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    with patch("samwhispers.autostart.is_supported", return_value=False):
        assert client.put("/api/autostart", json={"enabled": True}).status_code == 400


def test_models_endpoint(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    body = client.get("/api/models").json()
    assert "whisper" in body and "faster_whisper" in body
    assert "base.en" in body["downloadable"]


def test_models_download_rejects_unknown(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, _ = client_and_sup
    assert client.post("/api/models/download", json={"name": "bogus"}).status_code == 400


def test_get_config_defaults(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    cfg = client.get("/api/config").json()
    assert cfg["hotkey"]["key"] == "ctrl+shift+space"


def test_put_config_saves_and_restarts(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, path = client_and_sup
    # Establish on-disk state with managed=False so a later hotkey edit leaves
    # whisper settings unchanged.
    cfg = client.get("/api/config").json()
    cfg["whisper"]["managed"] = False
    client.put("/api/config", json=cfg)
    sup.calls.clear()

    cfg["hotkey"]["key"] = "ctrl+alt+s"
    body = client.put("/api/config", json=cfg).json()
    assert body["saved"] is True and body["restarted"] is True
    # Hotkey-only change must not bounce whisper-server.
    assert body["whisper_restarted"] is False
    assert sup.calls == ["apply(whisper=False)"]
    assert path.is_file()


def test_put_config_whisper_change_restarts_whisper(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, _ = client_and_sup
    cfg = client.get("/api/config").json()
    cfg["whisper"]["managed"] = False  # changes whisper settings from the default
    body = client.put("/api/config", json=cfg).json()
    assert body["restarted"] is True and body["whisper_restarted"] is True
    assert sup.calls == ["apply(whisper=True)"]


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


@pytest.fixture
def history_client(tmp_path: Path) -> tuple[TestClient, HistoryStore]:
    store = HistoryStore(tmp_path / "history.db")
    app = create_app(None, config_path=tmp_path / "c.toml", history_store=store)
    return TestClient(app), store


def test_history_list_and_search(history_client: tuple[TestClient, HistoryStore]) -> None:
    client, store = history_client
    store.add("buy milk", language="en")
    store.add("meeting notes", language="en")

    res = client.get("/api/history").json()
    assert res["total"] == 2
    assert res["items"][0]["text"] == "meeting notes"  # recent first

    filtered = client.get("/api/history", params={"q": "milk"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["text"] == "buy milk"


def test_history_pagination(history_client: tuple[TestClient, HistoryStore]) -> None:
    client, store = history_client
    for i in range(5):
        store.add(f"e{i}")
    page = client.get("/api/history", params={"limit": 2, "offset": 0}).json()
    assert len(page["items"]) == 2 and page["total"] == 5


def test_history_delete_entry(history_client: tuple[TestClient, HistoryStore]) -> None:
    client, store = history_client
    rid = store.add("temp")
    assert client.delete(f"/api/history/{rid}").json()["deleted"] is True
    assert client.delete(f"/api/history/{rid}").status_code == 404


def test_history_clear(history_client: tuple[TestClient, HistoryStore]) -> None:
    client, store = history_client
    store.add("a")
    store.add("b")
    assert client.delete("/api/history").json()["deleted"] == 2
    assert client.get("/api/history").json()["total"] == 0
