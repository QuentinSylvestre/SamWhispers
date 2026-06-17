"""Curated and user-pinned model artifact metadata.

Built-in models are pinned to immutable Hugging Face revisions with SHA256
hashes. Users can pin additional models from Hugging Face discovery or manual
URL downloads (which also require SHA256).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("samwhispers.models")

# Immutable revision for all built-in whisper.cpp artifacts.
# Pinned 2026-06-17 from https://huggingface.co/ggerganov/whisper.cpp
_WHISPER_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
_WHISPER_REPO = "ggerganov/whisper.cpp"

# VAD model pinned revision.
_VAD_REVISION = "9ffd54a1e1ee413ddf265af9913beaf518d1639b"
_VAD_REPO = "ggml-org/whisper-vad"


@dataclass(frozen=True)
class ModelArtifact:
    name: str
    filename: str
    url: str
    revision: str
    sha256: str
    size: int | None = None


# Built-in Whisper model manifest.
# SHA256 values from Hugging Face LFS metadata at the pinned revision.
WHISPER_MANIFEST: dict[str, ModelArtifact] = {
    "tiny.en": ModelArtifact(
        name="tiny.en", filename="ggml-tiny.en.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-tiny.en.bin",
        revision=_WHISPER_REVISION,
        sha256="921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f",
        size=77704715,
    ),
    "tiny": ModelArtifact(
        name="tiny", filename="ggml-tiny.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-tiny.bin",
        revision=_WHISPER_REVISION,
        sha256="be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
        size=77691713,
    ),
    "base.en": ModelArtifact(
        name="base.en", filename="ggml-base.en.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-base.en.bin",
        revision=_WHISPER_REVISION,
        sha256="a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
        size=147964211,
    ),
    "base": ModelArtifact(
        name="base", filename="ggml-base.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-base.bin",
        revision=_WHISPER_REVISION,
        sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        size=147951465,
    ),
    "small.en": ModelArtifact(
        name="small.en", filename="ggml-small.en.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-small.en.bin",
        revision=_WHISPER_REVISION,
        sha256="c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
        size=487614201,
    ),
    "small": ModelArtifact(
        name="small", filename="ggml-small.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-small.bin",
        revision=_WHISPER_REVISION,
        sha256="1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
        size=487601967,
    ),
    "medium.en": ModelArtifact(
        name="medium.en", filename="ggml-medium.en.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-medium.en.bin",
        revision=_WHISPER_REVISION,
        sha256="cc37e93478338ec7700281a7ac30a10128929eb8f427dda2e865faa8f6da4356",
        size=1533774781,
    ),
    "medium": ModelArtifact(
        name="medium", filename="ggml-medium.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-medium.bin",
        revision=_WHISPER_REVISION,
        sha256="6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208",
        size=1533763059,
    ),
    "large-v1": ModelArtifact(
        name="large-v1", filename="ggml-large-v1.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-large-v1.bin",
        revision=_WHISPER_REVISION,
        sha256="7d99f41a10525d0206bddadd86760181fa920438b6b33237e3118ff6c83bb53d",
        size=3094623691,
    ),
    "large-v2": ModelArtifact(
        name="large-v2", filename="ggml-large-v2.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-large-v2.bin",
        revision=_WHISPER_REVISION,
        sha256="9a423fe4d40c82774b6af34115b8b935f34152246eb19e80e376071d3f999487",
        size=3094623691,
    ),
    "large-v3": ModelArtifact(
        name="large-v3", filename="ggml-large-v3.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-large-v3.bin",
        revision=_WHISPER_REVISION,
        sha256="64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2",
        size=3095033483,
    ),
    "large-v3-turbo": ModelArtifact(
        name="large-v3-turbo", filename="ggml-large-v3-turbo.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-large-v3-turbo.bin",
        revision=_WHISPER_REVISION,
        sha256="1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
        size=1624555275,
    ),
}

VAD_ARTIFACT = ModelArtifact(
    name="silero-vad",
    filename="ggml-silero-v6.2.0.bin",
    url=f"https://huggingface.co/{_VAD_REPO}/resolve/{_VAD_REVISION}/ggml-silero-v6.2.0.bin",
    revision=_VAD_REVISION,
    sha256="2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
    size=885098,
)


def custom_models_path() -> Path:
    """Path to the custom (pinned) models JSON registry."""
    from samwhispers.history import resolve_data_dir

    return resolve_data_dir() / "custom_models.json"


def load_custom_models() -> dict[str, ModelArtifact]:
    """Load custom models registry. Returns empty dict on missing/corrupt file."""
    import json

    p = custom_models_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load custom models registry: %s", exc)
        return {}
    result: dict[str, ModelArtifact] = {}
    for filename, entry in data.items():
        result[filename] = ModelArtifact(
            name=entry.get("name", filename),
            filename=filename,
            url=entry["url"],
            revision=entry["revision"],
            sha256=entry["sha256"],
            size=entry.get("size"),
        )
    return result



def _atomic_json_write(p: Path, data: object) -> None:
    """Write JSON atomically with advisory file locking on the target."""
    import json
    import os
    import sys
    import tempfile

    p.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2).encode("utf-8")

    # Lock the target file (or create it) to serialize concurrent writers.
    lock_path = p.with_suffix(".lock")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Write to temp, then replace atomically.
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            os.write(tmp_fd, content)
        finally:
            os.close(tmp_fd)
        os.replace(tmp_name, str(p))
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True) if "tmp_name" in dir() else None
        raise
    finally:
        os.close(lock_fd)


def save_custom_model(artifact: ModelArtifact) -> None:
    """Add or update a custom model in the registry (atomic write)."""
    import json

    p = custom_models_path()

    # Load existing
    existing: dict[str, dict[str, object]] = {}
    if p.is_file():
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    existing[artifact.filename] = {
        "name": artifact.name,
        "url": artifact.url,
        "revision": artifact.revision,
        "sha256": artifact.sha256,
        "size": artifact.size,
    }

    _atomic_json_write(p, existing)


def remove_custom_model(filename: str) -> bool:
    """Remove a custom model from the registry. Returns True if it existed."""
    import json

    p = custom_models_path()
    if not p.is_file():
        return False

    try:
        existing = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    if filename not in existing:
        return False

    del existing[filename]
    _atomic_json_write(p, existing)
    return True


def verify_file(path: Path, expected_sha256: str) -> bool:
    """Verify a file's SHA256 hash. Returns True if it matches."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest() == expected_sha256


def compute_sha256(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def get_artifact(name: str) -> ModelArtifact | None:
    """Look up a built-in artifact by name."""
    return WHISPER_MANIFEST.get(name)


def verify_cached_model(name: str, models_dir: Path) -> bool | None:
    """Verify a cached model file against the manifest.

    Returns True if hash matches, False if mismatch, None if not in manifest
    or file doesn't exist.
    """
    artifact = WHISPER_MANIFEST.get(name)
    if artifact is None:
        return None
    path = models_dir / artifact.filename
    if not path.is_file():
        return None
    return verify_file(path, artifact.sha256)
