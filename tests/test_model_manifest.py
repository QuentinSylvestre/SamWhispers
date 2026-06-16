"""Tests for model manifest and hash verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from samwhispers.model_manifest import (
    WHISPER_MANIFEST,
    VAD_ARTIFACT,
    ModelArtifact,
    compute_sha256,
    get_artifact,
    verify_cached_model,
    verify_file,
)


def test_manifest_has_all_models() -> None:
    expected = [
        "tiny.en", "tiny", "base.en", "base",
        "small.en", "small", "medium.en", "medium",
        "large-v1", "large-v2", "large-v3", "large-v3-turbo",
    ]
    for name in expected:
        assert name in WHISPER_MANIFEST
        a = WHISPER_MANIFEST[name]
        assert a.filename == f"ggml-{name}.bin"
        assert a.sha256 and len(a.sha256) == 64
        assert "resolve/" in a.url
        assert a.revision


def test_vad_artifact_defined() -> None:
    assert VAD_ARTIFACT.filename == "ggml-silero-v6.2.0.bin"
    assert VAD_ARTIFACT.sha256 and len(VAD_ARTIFACT.sha256) == 64


def test_verify_file_correct_hash(tmp_path: Path) -> None:
    content = b"hello world"
    p = tmp_path / "test.bin"
    p.write_bytes(content)
    expected = compute_sha256(p)
    assert verify_file(p, expected) is True


def test_verify_file_wrong_hash(tmp_path: Path) -> None:
    p = tmp_path / "test.bin"
    p.write_bytes(b"hello world")
    assert verify_file(p, "0" * 64) is False


def test_verify_file_missing(tmp_path: Path) -> None:
    assert verify_file(tmp_path / "nope.bin", "abc") is False


def test_get_artifact_known() -> None:
    a = get_artifact("base.en")
    assert a is not None
    assert a.name == "base.en"


def test_get_artifact_unknown() -> None:
    assert get_artifact("nonexistent") is None


def test_verify_cached_model_match(tmp_path: Path) -> None:
    # Create a file that matches the manifest hash
    artifact = WHISPER_MANIFEST["tiny.en"]
    p = tmp_path / artifact.filename
    p.write_bytes(b"fake")
    fake_hash = compute_sha256(p)
    with patch.dict(WHISPER_MANIFEST, {"tiny.en": ModelArtifact(
        name="tiny.en", filename=artifact.filename,
        url=artifact.url, revision=artifact.revision,
        sha256=fake_hash,
    )}):
        assert verify_cached_model("tiny.en", tmp_path) is True


def test_verify_cached_model_mismatch(tmp_path: Path) -> None:
    artifact = WHISPER_MANIFEST["tiny.en"]
    p = tmp_path / artifact.filename
    p.write_bytes(b"wrong content")
    assert verify_cached_model("tiny.en", tmp_path) is False


def test_verify_cached_model_missing_file(tmp_path: Path) -> None:
    assert verify_cached_model("tiny.en", tmp_path) is None


def test_verify_cached_model_unknown_name(tmp_path: Path) -> None:
    assert verify_cached_model("nonexistent", tmp_path) is None
