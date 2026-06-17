"""Tests for model discovery and on-demand download."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from samwhispers.models import WHISPER_CPP_MODELS, ModelDownloader
from samwhispers.model_manifest import WHISPER_MANIFEST
from samwhispers.webconfig import list_whisper_models, save_config_dict


def test_downloadable_list_nonempty() -> None:
    assert "base.en" in WHISPER_CPP_MODELS


def test_status_defaults() -> None:
    s = ModelDownloader().status()
    assert s["downloading"] is False and s["done"] is False and s["error"] is None


def test_start_rejects_unknown_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ModelDownloader().start("not-a-model", tmp_path)


def test_start_rejects_concurrent_download(tmp_path: Path) -> None:
    d = ModelDownloader()
    d._state["downloading"] = True  # pretend one is running
    with pytest.raises(RuntimeError):
        d.start("base.en", tmp_path)


@respx.mock
def test_download_writes_file_and_tracks_progress(tmp_path: Path) -> None:
    from unittest.mock import patch

    artifact = WHISPER_MANIFEST["base.en"]
    data = b"ggml-model-bytes" * 500
    respx.get(artifact.url).mock(
        return_value=httpx.Response(200, content=data, headers={"content-length": str(len(data))})
    )
    d = ModelDownloader()
    d._state["downloading"] = True
    with patch("samwhispers.model_manifest.verify_file", return_value=True):
        d._download("base.en", tmp_path)
    st = d.status()
    assert st["done"] is True and st["downloading"] is False
    assert st["downloaded"] == len(data)
    out = tmp_path / "ggml-base.en.bin"
    assert out.read_bytes() == data
    assert st["path"] == str(out)
    assert not (tmp_path / "ggml-base.en.bin.part").exists()


@respx.mock
def test_download_error_sets_state_and_cleans_up(tmp_path: Path) -> None:
    artifact = WHISPER_MANIFEST["base.en"]
    respx.get(artifact.url).mock(return_value=httpx.Response(404))
    d = ModelDownloader()
    d._state["downloading"] = True
    d._download("base.en", tmp_path)
    st = d.status()
    assert st["error"] is not None and st["downloading"] is False and st["done"] is False
    assert not (tmp_path / "ggml-base.en.bin").exists()


def test_list_whisper_models_finds_bin_files(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "ggml-base.en.bin").write_bytes(b"x")
    (models_dir / "ggml-small.bin").write_bytes(b"x")

    config = tmp_path / "config.toml"
    save_config_dict(
        {"whisper": {"managed": False, "model_path": str(models_dir / "ggml-base.en.bin")}},
        config,
    )
    found = {m["label"] for m in list_whisper_models(config)}
    assert "ggml-base.en.bin" in found
    assert "ggml-small.bin" in found


# -- Custom model download tests --

@respx.mock
def test_start_custom_downloads_and_verifies(tmp_path: Path) -> None:
    from unittest.mock import patch

    from samwhispers.model_manifest import ModelArtifact

    artifact = ModelArtifact(
        name="custom-test",
        filename="ggml-custom-test.bin",
        url="https://huggingface.co/test/resolve/abc/ggml-custom-test.bin",
        revision="abc",
        sha256="a" * 64,
        size=8000,
    )
    data = b"custom-model-bytes" * 100
    respx.get(artifact.url).mock(
        return_value=httpx.Response(200, content=data, headers={"content-length": str(len(data))})
    )
    d = ModelDownloader()
    with patch("samwhispers.model_manifest.verify_file", return_value=True):
        d.start_custom(artifact, tmp_path)
        d._thread.join(timeout=5)
    st = d.status()
    assert st["done"] is True and st["downloading"] is False
    assert (tmp_path / "ggml-custom-test.bin").read_bytes() == data


def test_start_custom_rejects_concurrent(tmp_path: Path) -> None:
    from samwhispers.model_manifest import ModelArtifact

    artifact = ModelArtifact(
        name="x", filename="ggml-x.bin", url="http://x", revision="r", sha256="a" * 64,
    )
    d = ModelDownloader()
    d._state["downloading"] = True
    with pytest.raises(RuntimeError):
        d.start_custom(artifact, tmp_path)
