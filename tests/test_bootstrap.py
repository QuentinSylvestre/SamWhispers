"""Tests for the setup/bootstrap helper (non-network parts)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from samwhispers import bootstrap


def test_default_config_text_points_at_binary_and_model(tmp_path: Path) -> None:
    text = bootstrap.default_config_text(tmp_path / "whisper-server", tmp_path / "ggml-base.en.bin")
    assert "managed = true" in text
    assert "whisper-server" in text
    assert "ggml-base.en.bin" in text


def test_write_config_creates_and_skips_existing(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    assert bootstrap.write_config(cfg, tmp_path / "bin", tmp_path / "m.bin") is True
    assert cfg.exists()
    # second call leaves it untouched unless forced
    assert bootstrap.write_config(cfg, tmp_path / "bin", tmp_path / "m.bin") is False
    assert bootstrap.write_config(cfg, tmp_path / "bin", tmp_path / "m.bin", force=True) is True


def test_ensure_model_skips_when_present(tmp_path: Path) -> None:
    existing = tmp_path / "ggml-base.en.bin"
    existing.write_bytes(b"x")
    with patch.object(bootstrap.ModelDownloader, "_download") as dl:
        result = bootstrap.ensure_model("base.en", tmp_path)
    dl.assert_not_called()
    assert result == existing


def test_ensure_model_rejects_unknown(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        bootstrap.ensure_model("not-a-model", tmp_path)


def test_ensure_whisper_server_uses_existing(tmp_path: Path) -> None:
    whisper_dir = tmp_path / "whisper.cpp"
    binp = bootstrap.server_bin_path(whisper_dir)
    binp.parent.mkdir(parents=True)
    binp.write_bytes(b"x")
    with (
        patch.object(bootstrap, "build_whisper_from_source") as build,
        patch.object(bootstrap, "download_whisper_prebuilt_windows") as dl,
    ):
        result = bootstrap.ensure_whisper_server(whisper_dir, build=False)
    build.assert_not_called()
    dl.assert_not_called()
    assert result == binp


def test_build_from_source_requires_toolchain(tmp_path: Path) -> None:
    with patch.object(bootstrap.shutil, "which", return_value=None):
        with pytest.raises(SystemExit):
            bootstrap.build_whisper_from_source(tmp_path / "whisper.cpp")
