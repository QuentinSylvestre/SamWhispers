"""Local web server for the SamWhispers config UI.

Runs inside the supervisor (not the worker) so it survives worker restarts.
Bound to loopback only; there is no authentication, so it must never be
exposed beyond ``127.0.0.1``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from samwhispers.history import HistoryStore, default_db_path
from samwhispers.webconfig import (
    FASTER_WHISPER_MODELS,
    current_app_config,
    list_whisper_models,
    load_config_dict,
    requires_restart,
    save_config_dict,
)

if TYPE_CHECKING:
    from samwhispers.supervisor import WorkerSupervisor

log = logging.getLogger("samwhispers.web")

_WEB_DIR = Path(__file__).parent / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7891


def create_app(
    supervisor: WorkerSupervisor | None = None,
    config_path: str | Path | None = None,
    history_store: HistoryStore | None = None,
) -> FastAPI:
    """Build the FastAPI app. ``supervisor`` may be None for API-only testing."""
    app = FastAPI(title="SamWhispers", docs_url=None, redoc_url=None)
    store = history_store if history_store is not None else HistoryStore(default_db_path())

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_WEB_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        from samwhispers.config import (
            _VALID_MODES,
            _VALID_PROVIDERS,
            _VALID_STREAM_ENGINES,
            _VALID_STREAM_MODES,
            _VALID_TRAILING,
            LANGUAGE_NAMES,
            WHISPER_LANGUAGES,
        )

        languages = sorted(WHISPER_LANGUAGES, key=lambda c: (c != "auto", c))
        return {
            "languages": [{"code": c, "name": LANGUAGE_NAMES.get(c, c)} for c in languages],
            "modes": list(_VALID_MODES),
            "providers": list(_VALID_PROVIDERS),
            "trailing": list(_VALID_TRAILING),
            "stream_engines": list(_VALID_STREAM_ENGINES),
            "stream_modes": list(_VALID_STREAM_MODES),
        }

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        from samwhispers.models import WHISPER_CPP_MODELS

        return {
            "whisper": list_whisper_models(config_path),
            "faster_whisper": FASTER_WHISPER_MODELS,
            "downloadable": WHISPER_CPP_MODELS,
        }

    @app.post("/api/models/download")
    async def start_download(request: Request) -> dict[str, Any]:
        from samwhispers.models import downloader

        body: dict[str, Any] = await request.json()
        name = str(body.get("name", ""))
        dest_dir = Path(current_app_config(config_path).whisper.model_path).parent
        try:
            downloader.start(name, dest_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"started": True}

    @app.get("/api/models/download")
    def download_status() -> dict[str, Any]:
        from samwhispers.models import downloader

        return downloader.status()

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return {"state": supervisor.state.value if supervisor else "unknown"}

    @app.get("/api/logs")
    def get_logs() -> dict[str, Any]:
        return {"lines": supervisor.logs if supervisor else []}

    @app.get("/api/autostart")
    def get_autostart() -> dict[str, Any]:
        from samwhispers import autostart

        supported = autostart.is_supported()
        return {"supported": supported, "enabled": supported and autostart.is_enabled()}

    @app.put("/api/autostart")
    async def set_autostart(request: Request) -> dict[str, Any]:
        from samwhispers import autostart

        payload: dict[str, Any] = await request.json()
        if not autostart.is_supported():
            raise HTTPException(status_code=400, detail="Autostart not supported on this platform")
        try:
            if payload.get("enabled"):
                autostart.enable()
            else:
                autostart.disable()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Autostart update failed: {exc}") from exc
        return {"enabled": autostart.is_enabled()}

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return load_config_dict(config_path)

    @app.put("/api/config")
    async def put_config(request: Request) -> dict[str, Any]:
        payload: dict[str, Any] = await request.json()
        old_cfg = current_app_config(config_path)
        try:
            new_cfg = save_config_dict(payload, config_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        restarted = False
        whisper_restarted = False
        if supervisor is not None and requires_restart(old_cfg, new_cfg):
            whisper_restarted = old_cfg.whisper != new_cfg.whisper
            supervisor.apply_config_change(restart_whisper=whisper_restarted)
            restarted = True
        return {"saved": True, "restarted": restarted, "whisper_restarted": whisper_restarted}

    @app.get("/api/history")
    def get_history(limit: int = 50, offset: int = 0, q: str | None = None) -> dict[str, Any]:
        return {
            "items": store.list(limit=limit, offset=offset, search=q),
            "total": store.count(search=q),
        }

    @app.delete("/api/history/{entry_id}")
    def delete_history_entry(entry_id: int) -> dict[str, Any]:
        if not store.delete(entry_id):
            raise HTTPException(status_code=404, detail="Entry not found")
        return {"deleted": True}

    @app.delete("/api/history")
    def clear_history() -> dict[str, Any]:
        return {"deleted": store.clear()}

    @app.post("/api/worker/{action}")
    def worker_action(action: str) -> dict[str, Any]:
        if supervisor is None:
            raise HTTPException(status_code=503, detail="No supervisor attached")
        if action == "pause":
            supervisor.pause()
        elif action == "resume":
            supervisor.resume()
        elif action == "restart":
            supervisor.restart()
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
        return {"state": supervisor.state.value}

    return app


@dataclass
class WebServerHandle:
    """Handle to a running web server thread."""

    server: Any
    thread: threading.Thread
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def shutdown(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5.0)


def serve(
    app: FastAPI,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> WebServerHandle:
    """Start the app with uvicorn in a daemon thread; return a stop handle."""
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    # uvicorn skips installing signal handlers when not on the main thread,
    # so it coexists with the tray/signal handling in the supervisor.
    thread = threading.Thread(target=server.run, daemon=True, name="web-server")
    thread.start()
    return WebServerHandle(server=server, thread=thread, host=host, port=port)
