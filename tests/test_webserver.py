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

    def request_shutdown(self) -> None:
        self.calls.append("shutdown")

    def request_relaunch(self) -> None:
        self.calls.append("relaunch")


def _client(app: object, port: int = 7891) -> TestClient:
    return TestClient(app, base_url=f"http://127.0.0.1:{port}")


def _csrf_headers(
    client: TestClient,
    *,
    origin: str | None = "http://127.0.0.1:7891",
    token: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["Origin"] = origin
    headers["X-SamWhispers-CSRF"] = token if token is not None else client.app.state.csrf_token
    return headers


@pytest.fixture
def client_and_sup(tmp_path: Path) -> tuple[TestClient, FakeSupervisor, Path]:
    sup = FakeSupervisor()
    path = tmp_path / "config.toml"
    app = create_app(sup, config_path=path)  # type: ignore[arg-type]
    return _client(app), sup, path


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
        body = client.put(
            "/api/autostart",
            json={"enabled": True},
            headers=_csrf_headers(client),
        ).json()
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
        body = client.put(
            "/api/autostart",
            json={"enabled": False},
            headers=_csrf_headers(client),
        ).json()
    disable.assert_called_once()
    assert body["enabled"] is False


def test_autostart_unsupported(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    with patch("samwhispers.autostart.is_supported", return_value=False):
        assert (
            client.put(
                "/api/autostart",
                json={"enabled": True},
                headers=_csrf_headers(client),
            ).status_code
            == 400
        )


def test_models_endpoint(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    body = client.get("/api/models").json()
    assert "whisper" in body and "faster_whisper" in body
    assert "base.en" in body["downloadable"]


def test_models_download_rejects_unknown(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, _ = client_and_sup
    assert (
        client.post(
            "/api/models/download",
            json={"name": "bogus"},
            headers=_csrf_headers(client),
        ).status_code
        == 400
    )


def test_get_config_defaults(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    cfg = client.get("/api/config").json()
    assert cfg["hotkey"]["key"] == "ctrl+shift+space"


def test_get_config_redacts_provider_keys(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, path = client_and_sup
    path.write_text(
        "\n".join(
            [
                "[whisper]",
                "managed = false",
                "[cleanup.openai]",
                'api_key = "sk-persisted-secret"',
                "[cleanup.anthropic]",
                'api_key = "ant-persisted-secret"',
            ]
        ),
        encoding="utf-8",
    )
    res = client.get("/api/config")
    body = res.json()
    assert "sk-persisted-secret" not in res.text
    assert "ant-persisted-secret" not in res.text
    assert body["cleanup"]["openai"]["api_key"] == "__SAMWHISPERS_SECRET_SET__"
    assert body["cleanup"]["anthropic"]["api_key"] == "__SAMWHISPERS_SECRET_SET__"


def test_put_config_saves_and_restarts(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, path = client_and_sup
    # Establish on-disk state with managed=False so a later hotkey edit leaves
    # whisper settings unchanged.
    cfg = client.get("/api/config").json()
    cfg["whisper"]["managed"] = False
    client.put("/api/config", json=cfg, headers=_csrf_headers(client))
    sup.calls.clear()

    cfg["hotkey"]["key"] = "ctrl+alt+s"
    body = client.put("/api/config", json=cfg, headers=_csrf_headers(client)).json()
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
    body = client.put("/api/config", json=cfg, headers=_csrf_headers(client)).json()
    assert body["restarted"] is True and body["whisper_restarted"] is True
    assert sup.calls == ["apply(whisper=True)"]


def test_put_config_no_change_does_not_restart(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, path = client_and_sup
    cfg = client.get("/api/config").json()
    cfg["whisper"]["managed"] = False
    client.put("/api/config", json=cfg, headers=_csrf_headers(client))
    sup.calls.clear()
    again = client.put("/api/config", json=cfg, headers=_csrf_headers(client)).json()
    assert again["restarted"] is False
    assert sup.calls == []


def test_put_config_invalid_returns_400(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, path = client_and_sup
    cfg = client.get("/api/config").json()
    cfg["hotkey"]["mode"] = "bogus"
    res = client.put("/api/config", json=cfg, headers=_csrf_headers(client))
    assert res.status_code == 400
    assert "mode" in res.json()["detail"]
    assert "restart" not in sup.calls


def test_config_validation_error_redacts_posted_and_persisted_secrets(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, path = client_and_sup
    path.write_text(
        "\n".join(
            [
                "[whisper]",
                "managed = false",
                "[cleanup.openai]",
                'api_key = "sk-persisted-secret"',
            ]
        ),
        encoding="utf-8",
    )
    cfg = client.get("/api/config").json()
    cfg["cleanup"]["openai"]["api_key"] = "sk-new-secret"
    cfg["hotkey"]["mode"] = "bogus"

    res = client.put("/api/config", json=cfg, headers=_csrf_headers(client))

    assert res.status_code == 400
    assert "sk-new-secret" not in res.text
    assert "sk-persisted-secret" not in res.text


def test_config_save_error_redacts_posted_and_persisted_secrets(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, path = client_and_sup
    path.write_text(
        "\n".join(
            [
                "[whisper]",
                "managed = false",
                "[cleanup.openai]",
                'api_key = "sk-persisted-secret"',
            ]
        ),
        encoding="utf-8",
    )
    cfg = client.get("/api/config").json()
    cfg["cleanup"]["openai"]["api_key"] = "sk-new-secret"

    with patch(
        "samwhispers.webserver.save_config_dict",
        side_effect=OSError("cannot write sk-new-secret sk-persisted-secret"),
    ):
        res = client.put("/api/config", json=cfg, headers=_csrf_headers(client))

    assert res.status_code == 500
    assert "sk-new-secret" not in res.text
    assert "sk-persisted-secret" not in res.text


def test_config_load_error_redacts_persisted_secrets(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, path = client_and_sup
    path.write_text(
        "\n".join(
            [
                "[whisper]",
                "managed = false",
                "[cleanup.openai]",
                'api_key = "sk-persisted-secret"',
            ]
        ),
        encoding="utf-8",
    )

    with patch(
        "samwhispers.webserver.load_config_dict",
        side_effect=ValueError("cannot load sk-persisted-secret"),
    ):
        res = client.get("/api/config")

    assert res.status_code == 500
    assert "sk-persisted-secret" not in res.text


def test_worker_actions(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, sup, _ = client_and_sup
    assert client.post("/api/worker/pause", headers=_csrf_headers(client)).json()["state"] == "paused"
    assert (
        client.post("/api/worker/resume", headers=_csrf_headers(client)).json()["state"]
        == "running"
    )
    assert client.post("/api/worker/restart", headers=_csrf_headers(client)).status_code == 200
    assert sup.calls == ["pause", "resume", "restart"]


def test_worker_unknown_action(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    assert client.post("/api/worker/frobnicate", headers=_csrf_headers(client)).status_code == 400


def test_worker_action_without_supervisor(tmp_path: Path) -> None:
    app = create_app(None, config_path=tmp_path / "c.toml")
    client = _client(app)
    assert client.post("/api/worker/pause", headers=_csrf_headers(client)).status_code == 503


def test_hostile_host_rejected(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    assert client.get("/api/status", headers={"Host": "evil.test:7891"}).status_code == 403


def test_host_without_effective_port_rejected(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, _ = client_and_sup
    assert client.get("/api/status", headers={"Host": "127.0.0.1"}).status_code == 403


def test_mutating_api_requires_csrf(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    assert (
        client.put(
            "/api/autostart",
            json={"enabled": True},
            headers={"Origin": "http://127.0.0.1:7891"},
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/autostart",
            json={"enabled": True},
            headers=_csrf_headers(client, token="wrong"),
        ).status_code
        == 403
    )


def test_missing_origin_with_valid_csrf_is_allowed(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, _ = client_and_sup
    with (
        patch("samwhispers.autostart.is_supported", return_value=True),
        patch("samwhispers.autostart.enable"),
        patch("samwhispers.autostart.is_enabled", return_value=True),
    ):
        res = client.put(
            "/api/autostart",
            json={"enabled": True},
            headers=_csrf_headers(client, origin=None),
        )
    assert res.status_code == 200


def test_hostile_origin_and_null_origin_rejected(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, _ = client_and_sup
    for origin in ("http://evil.test:7891", "null"):
        res = client.post("/api/worker/pause", headers=_csrf_headers(client, origin=origin))
        assert res.status_code == 403


def test_hostile_referer_rejected(client_and_sup: tuple[TestClient, FakeSupervisor, Path]) -> None:
    client, _, _ = client_and_sup
    headers = _csrf_headers(client, origin=None)
    headers["Referer"] = "http://evil.test:7891/page"
    assert client.post("/api/worker/pause", headers=headers).status_code == 403


def test_dynamic_custom_port_cors() -> None:
    app = create_app(None, web_port=9000)
    client = _client(app, port=9000)

    allowed = client.options(
        "/api/status",
        headers={
            "Origin": "http://localhost:9000",
            "Access-Control-Request-Method": "GET",
        },
    )
    rejected = client.options(
        "/api/status",
        headers={
            "Origin": "http://localhost:7891",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:9000"
    assert rejected.status_code == 403


def test_supervisor_lifecycle_requires_csrf(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, _ = client_and_sup
    # Without CSRF token, supervisor lifecycle is rejected
    assert (
        client.post(
            "/api/supervisor/restart",
            headers={"Origin": "http://127.0.0.1:7891"},
        ).status_code
        == 403
    )
    # With valid CSRF token, it works
    assert (
        client.post(
            "/api/supervisor/restart",
            headers=_csrf_headers(client),
        ).status_code
        == 200
    )
    assert sup.calls == ["relaunch"]


def test_supervisor_shutdown_requires_csrf(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, sup, _ = client_and_sup
    assert (
        client.post(
            "/api/supervisor/shutdown",
            headers={"Origin": "http://127.0.0.1:7891"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/supervisor/shutdown",
            headers=_csrf_headers(client),
        ).status_code
        == 200
    )
    assert sup.calls == ["shutdown"]


def test_supervisor_lifecycle_rejects_hostile_browser_inputs(
    client_and_sup: tuple[TestClient, FakeSupervisor, Path],
) -> None:
    client, _, _ = client_and_sup
    assert (
        client.post(
            "/api/supervisor/restart",
            headers={"Origin": "http://evil.test:7891"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/supervisor/restart",
            headers={"Host": "evil.test:7891"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/supervisor/shutdown",
            headers={"Referer": "http://evil.test:7891/page"},
        ).status_code
        == 403
    )


@pytest.fixture
def history_client(tmp_path: Path) -> tuple[TestClient, HistoryStore]:
    store = HistoryStore(tmp_path / "history.db")
    app = create_app(None, config_path=tmp_path / "c.toml", history_store=store)
    return _client(app), store


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
    assert client.delete(f"/api/history/{rid}", headers=_csrf_headers(client)).json()["deleted"] is True
    assert client.delete(f"/api/history/{rid}", headers=_csrf_headers(client)).status_code == 404


def test_history_clear(history_client: tuple[TestClient, HistoryStore]) -> None:
    client, store = history_client
    store.add("a")
    store.add("b")
    assert client.delete("/api/history", headers=_csrf_headers(client)).json()["deleted"] == 2
    assert client.get("/api/history").json()["total"] == 0
