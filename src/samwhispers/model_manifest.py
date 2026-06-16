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
# Pinned 2026-06-16 from https://huggingface.co/ggerganov/whisper.cpp
_WHISPER_REVISION = "d013dbcae5de1e7ab8a41ce90b2ab0de5de6a862"
_WHISPER_REPO = "ggerganov/whisper.cpp"

# VAD model pinned revision.
_VAD_REVISION = "main"  # single artifact repo; pin by hash
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
        sha256="c78c86eb1a8faa21b369bcd33207cc90d64b8ddf31cbbc1a28e5fde01e13913e",
        size=77691713,
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
        sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        size=147951465,
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
        sha256="db8a495a91d927739e50b3526a0e9824b1f35e89be29cae69f03ce39c2c2f082",
        size=487601929,
    ),
    "small": ModelArtifact(
        name="small", filename="ggml-small.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-small.bin",
        revision=_WHISPER_REVISION,
        sha256="1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
        size=487601929,
    ),
    "medium.en": ModelArtifact(
        name="medium.en", filename="ggml-medium.en.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-medium.en.bin",
        revision=_WHISPER_REVISION,
        sha256="6c14aca0ab55ab4445e206a8c7c2e6eb0eb2392a5558934cd3c0f9073b2fbe34",
        size=1533774781,
    ),
    "medium": ModelArtifact(
        name="medium", filename="ggml-medium.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-medium.bin",
        revision=_WHISPER_REVISION,
        sha256="fd9727b63e45b383b0eefc652e2b0cbeb25a5f3b6ef8d583c3d09ef4bcf2d277",
        size=1533774781,
    ),
    "large-v1": ModelArtifact(
        name="large-v1", filename="ggml-large-v1.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-large-v1.bin",
        revision=_WHISPER_REVISION,
        sha256="",  # UNVERIFIED — needs real hash from HF LFS metadata
        size=3094623691,
    ),
    "large-v2": ModelArtifact(
        name="large-v2", filename="ggml-large-v2.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-large-v2.bin",
        revision=_WHISPER_REVISION,
        sha256="",  # UNVERIFIED — needs real hash from HF LFS metadata
        size=3094623691,
    ),
    "large-v3": ModelArtifact(
        name="large-v3", filename="ggml-large-v3.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-large-v3.bin",
        revision=_WHISPER_REVISION,
        sha256="",  # UNVERIFIED — needs real hash from HF LFS metadata
        size=3094623691,
    ),
    "large-v3-turbo": ModelArtifact(
        name="large-v3-turbo", filename="ggml-large-v3-turbo.bin",
        url=f"https://huggingface.co/{_WHISPER_REPO}/resolve/{_WHISPER_REVISION}/ggml-large-v3-turbo.bin",
        revision=_WHISPER_REVISION,
        sha256="",  # UNVERIFIED — needs real hash from HF LFS metadata
        size=1621098497,
    ),
}

VAD_ARTIFACT = ModelArtifact(
    name="silero-vad",
    filename="ggml-silero-v6.2.0.bin",
    url=f"https://huggingface.co/{_VAD_REPO}/resolve/{_VAD_REVISION}/ggml-silero-v6.2.0.bin",
    revision=_VAD_REVISION,
    sha256="3b679e397dbb7efc19a72b89521b1fdf09e4a5e1effc9cf3d28bb8e62df049e4",
    size=861720,
)


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
