"""Local web server for the SamWhispers config UI.

Runs inside the supervisor (not the worker) so it survives worker restarts.
Bound to loopback only; there is no authentication, so it must never be
exposed beyond ``127.0.0.1``.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from samwhispers.history import HistoryStore, default_db_path
from samwhispers.webconfig import (
    FASTER_WHISPER_MODELS,
    current_app_config,
    list_whisper_models,
    load_config_dict,
    requires_restart,
    sanitize_secret_values,
    save_config_dict,
)

if TYPE_CHECKING:
    from samwhispers.supervisor import WorkerSupervisor

log = logging.getLogger("samwhispers.web")

_WEB_DIR = Path(__file__).parent / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7891
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-samwhispers-csrf"


def expected_origins(host: str, port: int) -> set[str]:
    """Same-origin browser origins that may talk to the local web UI."""
    origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    }
    normalized_host = host.strip().lower().strip("[]")
    if _is_loopback_host(normalized_host):
        display_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
        origins.add(f"http://{display_host}:{port}")
    return origins


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _split_host_header(host_header: str) -> tuple[str, int | None]:
    host = host_header.strip()
    if not host or any(ch in host for ch in "/\\@"):
        return "", None
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return "", None
        hostname = host[1:end]
        rest = host[end + 1 :]
        if not rest:
            return hostname.lower(), None
        if not rest.startswith(":"):
            return "", None
        port_text = rest[1:]
    elif host.count(":") == 1:
        hostname, port_text = host.rsplit(":", 1)
    elif ":" in host:
        return host.lower(), None
    else:
        return host.lower(), None
    try:
        port = int(port_text)
    except ValueError:
        return "", None
    return hostname.lower().rstrip("."), port


def _host_is_trusted(host_header: str | None, port: int) -> bool:
    if not host_header:
        return False
    host, host_port = _split_host_header(host_header)
    return host_port == port and _is_loopback_host(host)


def _origin_from_referer(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or port is None:
        return None
    hostname = parsed.hostname.lower()
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{parsed.scheme}://{display_host}:{port}"


def _origin_or_referer_error(request: Request, port: int) -> str | None:
    expected = expected_origins(str(request.app.state.web_host), port)
    origin = request.headers.get("origin")
    if origin is not None:
        if origin == "null" or origin.rstrip("/") not in expected:
            return "Untrusted browser origin"
        return None
    referer = request.headers.get("referer")
    if referer:
        referer_origin = _origin_from_referer(referer)
        if referer_origin not in expected:
            return "Untrusted browser referer"
    return None


def _apply_cors_headers(request: Request, response: Response, port: int) -> None:
    origin = request.headers.get("origin")
    if origin is None or origin.rstrip("/") not in expected_origins(
        str(request.app.state.web_host), port
    ):
        return
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS, POST, PUT, DELETE"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-SamWhispers-CSRF"
    response.headers["Vary"] = "Origin"


def _config_redaction_context(config_path: str | Path | None) -> dict[str, Any]:
    try:
        return load_config_dict(config_path, redact=False)
    except Exception:
        pass
    if config_path is None:
        return {}
    context: dict[str, Any] = {"cleanup": {"openai": {}, "anthropic": {}}}
    section: tuple[str, str] | None = None
    try:
        for line in Path(config_path).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "[cleanup.openai]":
                section = ("cleanup", "openai")
                continue
            if stripped == "[cleanup.anthropic]":
                section = ("cleanup", "anthropic")
                continue
            if stripped.startswith("["):
                section = None
                continue
            if section is None or not stripped.startswith("api_key"):
                continue
            _, _, raw_value = stripped.partition("=")
            value = raw_value.strip().strip("\"'")
            if value:
                context[section[0]][section[1]]["api_key"] = value
    except OSError:
        return {}
    return context


def _safe_config_error(message: str, config_path: str | Path | None, *posted: dict[str, Any]) -> str:
    return sanitize_secret_values(message, _config_redaction_context(config_path), *posted)


def _vad_server_changed(old: Any, new: Any) -> bool:
    """Server-side VAD fields that require whisper-server restart."""
    return bool(
        old.enabled != new.enabled
        or old.model_path != new.model_path
        or old.threshold != new.threshold
        or old.min_speech_duration_ms != new.min_speech_duration_ms
        or old.min_silence_duration_ms != new.min_silence_duration_ms
        or old.max_speech_duration_s != new.max_speech_duration_s
        or old.speech_pad_ms != new.speech_pad_ms
        or old.samples_overlap != new.samples_overlap
    )


def create_app(
    supervisor: WorkerSupervisor | None = None,
    config_path: str | Path | None = None,
    history_store: HistoryStore | None = None,
    stop_callback: Any | None = None,
    web_host: str = DEFAULT_HOST,
    web_port: int = DEFAULT_PORT,
) -> FastAPI:
    """Build the FastAPI app. ``supervisor`` may be None for API-only testing."""
    app = FastAPI(title="SamWhispers", docs_url=None, redoc_url=None)
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.state.web_host = web_host
    app.state.web_port = web_port

    @app.middleware("http")
    async def local_web_guard(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        port = int(request.app.state.web_port)
        if not _host_is_trusted(request.headers.get("host"), port):
            return JSONResponse({"detail": "Untrusted Host header"}, status_code=403)

        is_api = request.url.path.startswith("/api/")
        if is_api:
            origin_error = _origin_or_referer_error(request, port)
            if origin_error:
                return JSONResponse({"detail": origin_error}, status_code=403)

        if is_api and request.method == "OPTIONS":
            response = Response(status_code=204)
            _apply_cors_headers(request, response, port)
            return response

        if (
            is_api
            and request.method not in SAFE_METHODS
            and request.headers.get(CSRF_HEADER) != request.app.state.csrf_token
        ):
            return JSONResponse({"detail": "Missing or invalid CSRF token"}, status_code=403)

        response = await call_next(request)
        _apply_cors_headers(request, response, port)
        return response

    store = history_store if history_store is not None else HistoryStore(default_db_path())

    # Serve bundled brand assets (favicon, logo, PWA icons) under /static.
    app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
        return html.replace("__SAMWHISPERS_CSRF_TOKEN__", app.state.csrf_token)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(_WEB_DIR / "favicon" / "favicon.ico")

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def apple_touch_icon() -> FileResponse:
        return FileResponse(_WEB_DIR / "pwa" / "apple-touch-icon.png")

    @app.get("/site.webmanifest", include_in_schema=False)
    def webmanifest() -> FileResponse:
        return FileResponse(
            _WEB_DIR / "site.webmanifest", media_type="application/manifest+json"
        )

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
        import importlib.util

        return {
            "languages": [{"code": c, "name": LANGUAGE_NAMES.get(c, c)} for c in languages],
            "modes": list(_VALID_MODES),
            "providers": list(_VALID_PROVIDERS),
            "trailing": list(_VALID_TRAILING),
            "stream_engines": list(_VALID_STREAM_ENGINES),
            "stream_modes": list(_VALID_STREAM_MODES),
            "faster_whisper_available": importlib.util.find_spec("faster_whisper") is not None,
        }

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        from samwhispers.models import WHISPER_CPP_MODELS

        whisper_list = list_whisper_models(config_path)
        # Derive which known models are already on disk
        downloaded_names: list[str] = []
        for m in WHISPER_CPP_MODELS:
            filename = f"ggml-{m}.bin"
            if any(item["label"] == filename for item in whisper_list):
                downloaded_names.append(m)
        return {
            "whisper": whisper_list,
            "faster_whisper": FASTER_WHISPER_MODELS,
            "downloadable": WHISPER_CPP_MODELS,
            "downloaded": downloaded_names,
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

    @app.delete("/api/models")
    async def delete_model_endpoint(request: Request) -> dict[str, Any]:
        from samwhispers.models import delete_model

        body: dict[str, Any] = await request.json()
        name = str(body.get("name", ""))
        # Guard: block deletion of the active model
        cfg = current_app_config(config_path)
        active_path = Path(cfg.whisper.model_path)
        dest_dir = active_path.parent
        target = dest_dir / f"ggml-{name}.bin"
        if target.resolve() == active_path.resolve():
            raise HTTPException(
                status_code=409,
                detail="Cannot delete active model. Switch to a different model first.",
            )
        try:
            deleted = delete_model(name, dest_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True, "path": str(deleted)}

    @app.delete("/api/vad")
    async def delete_vad_model(request: Request) -> dict[str, Any]:
        cfg = current_app_config(config_path)
        # Guard: block deletion if VAD is enabled and path matches
        models_dir = Path(cfg.whisper.model_path).parent
        vad_path = models_dir / "ggml-silero-v6.2.0.bin"
        if cfg.vad.enabled and Path(cfg.vad.model_path).resolve() == vad_path.resolve():
            raise HTTPException(
                status_code=409,
                detail="Cannot delete active VAD model. Disable VAD or clear the path first.",
            )
        if not vad_path.is_file():
            raise HTTPException(status_code=404, detail="VAD model not found")
        vad_path.unlink()
        return {"deleted": True, "path": str(vad_path)}

    @app.post("/api/vad/download")
    def download_vad_model() -> dict[str, Any]:
        from samwhispers.bootstrap import ensure_vad_model

        models_dir = Path(current_app_config(config_path).whisper.model_path).parent
        try:
            path = ensure_vad_model(models_dir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"downloaded": True, "path": str(path)}

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
        try:
            return load_config_dict(config_path)
        except Exception as exc:
            detail = _safe_config_error(str(exc), config_path)
            raise HTTPException(status_code=500, detail=f"Config load failed: {detail}") from exc

    @app.put("/api/config")
    async def put_config(request: Request) -> dict[str, Any]:
        payload: dict[str, Any] = await request.json()
        try:
            old_cfg = current_app_config(config_path)
            new_cfg = save_config_dict(payload, config_path)
        except ValueError as exc:
            detail = _safe_config_error(str(exc), config_path, payload)
            raise HTTPException(status_code=400, detail=detail) from exc
        except OSError as exc:
            detail = _safe_config_error(str(exc), config_path, payload)
            raise HTTPException(status_code=500, detail=f"Config save failed: {detail}") from exc

        restarted = False
        whisper_restarted = False
        if supervisor is not None and requires_restart(old_cfg, new_cfg):
            whisper_restarted = (
                old_cfg.whisper != new_cfg.whisper
                or _vad_server_changed(old_cfg.vad, new_cfg.vad)
            )
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

    @app.post("/api/supervisor/shutdown")
    def supervisor_shutdown() -> dict[str, Any]:
        if supervisor is None:
            raise HTTPException(status_code=503, detail="No supervisor attached")
        supervisor.request_shutdown()
        if stop_callback is not None:
            stop_callback()
        return {"shutting_down": True}

    @app.post("/api/supervisor/restart")
    def supervisor_restart() -> dict[str, Any]:
        if supervisor is None:
            raise HTTPException(status_code=503, detail="No supervisor attached")
        supervisor.request_relaunch()
        if stop_callback is not None:
            stop_callback()
        return {"restarting": True}

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

    app.state.web_host = host
    app.state.web_port = port
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    # uvicorn skips installing signal handlers when not on the main thread,
    # so it coexists with the tray/signal handling in the supervisor.
    thread = threading.Thread(target=server.run, daemon=True, name="web-server")
    thread.start()
    return WebServerHandle(server=server, thread=thread, host=host, port=port)
