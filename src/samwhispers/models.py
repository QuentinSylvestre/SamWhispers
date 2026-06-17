"""Download whisper.cpp model files on demand, with progress tracking.

Models are fetched from the official whisper.cpp Hugging Face repo. Only names
from ``WHISPER_CPP_MODELS`` are accepted, so a request can't craft an arbitrary
URL or destination path. A single download runs at a time; progress is polled
via the web UI.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("samwhispers.models")

# Downloadable whisper.cpp model names (-> ggml-<name>.bin).
WHISPER_CPP_MODELS = [
    "tiny.en",
    "tiny",
    "base.en",
    "base",
    "small.en",
    "small",
    "medium.en",
    "medium",
    "large-v1",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
]

_HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
_CHUNK = 1 << 20  # 1 MiB


class ModelDownloader:
    """Single-flight background downloader with pollable progress."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "downloading": False,
            "name": "",
            "downloaded": 0,
            "total": 0,
            "done": False,
            "error": None,
            "path": None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self, name: str, dest_dir: Path | str) -> None:
        """Begin downloading ``name`` into ``dest_dir`` (raises if one is running)."""
        if name not in WHISPER_CPP_MODELS:
            raise ValueError(f"Unknown model: {name!r}")
        with self._lock:
            if self._state["downloading"]:
                raise RuntimeError("A download is already in progress")
            self._state = {
                "downloading": True,
                "name": name,
                "downloaded": 0,
                "total": 0,
                "done": False,
                "error": None,
                "path": None,
            }
        self._thread = threading.Thread(
            target=self._download, args=(name, Path(dest_dir)), daemon=True, name="model-download"
        )
        self._thread.start()

    def _set(self, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)

    def _download(self, name: str, dest_dir: Path) -> None:
        from samwhispers.model_manifest import WHISPER_MANIFEST, verify_file

        artifact = WHISPER_MANIFEST.get(name)
        url = artifact.url if artifact else f"{_HF_BASE}/ggml-{name}.bin"
        dest = dest_dir / f"ggml-{name}.bin"
        tmp = dest.with_name(dest.name + ".part")
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    self._set(total=total)
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_bytes(_CHUNK):
                            f.write(chunk)
                            self._set(downloaded=self.status()["downloaded"] + len(chunk))
            # Verify hash before accepting
            if artifact and not verify_file(tmp, artifact.sha256):
                from samwhispers.model_manifest import compute_sha256

                actual = compute_sha256(tmp)
                log.error(
                    "Hash mismatch for %s: expected %s, got %s",
                    name, artifact.sha256[:16], actual[:16],
                )
                tmp.unlink(missing_ok=True)
                self._set(
                    error=f"Hash mismatch for {name}. Try downloading again from the model manager.",
                    downloading=False, done=False,
                )
                return
            tmp.replace(dest)
            log.info("Downloaded model %s -> %s", name, dest)
            self._set(done=True, downloading=False, path=str(dest))
        except Exception as exc:
            log.exception("Model download failed: %s", name)
            tmp.unlink(missing_ok=True)
            self._set(error=str(exc), downloading=False, done=False)


# Module-level singleton used by the web server.
downloader = ModelDownloader()


def delete_model(name: str, models_dir: Path | str) -> Path:
    """Delete a downloaded model file. Returns the deleted path."""
    if name not in WHISPER_CPP_MODELS:
        raise ValueError(f"Unknown model: {name!r}")
    path = Path(models_dir) / f"ggml-{name}.bin"
    if not path.is_file():
        raise FileNotFoundError(f"Model not found: {path}")
    path.unlink()
    log.info("Deleted model %s at %s", name, path)
    return path
