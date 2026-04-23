"""Tests for text post-processing module."""

from __future__ import annotations

from samwhispers.config import PostprocessConfig
from samwhispers.postprocess import TextPostprocessor


def _make(
    collapse_newlines: bool = True,
    collapse_spaces: bool = True,
    trim: bool = True,
    trailing: str = "newline",
) -> TextPostprocessor:
    return TextPostprocessor(
        PostprocessConfig(
            collapse_newlines=collapse_newlines,
            collapse_spaces=collapse_spaces,
            trim=trim,
            trailing=trailing,
        )
    )


def test_collapse_newlines() -> None:
    pp = _make()
    assert pp.normalize("hello\nworld") == "hello world"


def test_collapse_multiple_newlines() -> None:
    pp = _make()
    assert pp.normalize("hello\n\nworld") == "hello world"


def test_collapse_spaces() -> None:
    pp = _make(collapse_newlines=False)
    assert pp.normalize("hello   world") == "hello world"


def test_trim() -> None:
    pp = _make(collapse_newlines=False, collapse_spaces=False)
    assert pp.normalize("  hello  ") == "hello"


def test_all_disabled_passthrough() -> None:
    pp = _make(collapse_newlines=False, collapse_spaces=False, trim=False)
    assert pp.normalize("  hello\n  world  ") == "  hello\n  world  "


def test_trailing_newline() -> None:
    pp = _make(trailing="newline")
    assert pp.finalize("hello") == "hello\n"


def test_trailing_space() -> None:
    pp = _make(trailing="space")
    assert pp.finalize("hello") == "hello "


def test_trailing_none() -> None:
    pp = _make(trailing="none")
    assert pp.finalize("hello") == "hello"


def test_trailing_double_newline() -> None:
    pp = _make(trailing="double_newline")
    assert pp.finalize("hello") == "hello\n\n"


def test_trailing_tab() -> None:
    pp = _make(trailing="tab")
    assert pp.finalize("hello") == "hello\t"


def test_finalize_empty_string() -> None:
    pp = _make(trailing="newline")
    assert pp.finalize("") == ""


def test_normalize_empty_string() -> None:
    pp = _make()
    assert pp.normalize("") == ""


def test_full_pipeline_normalize_then_finalize() -> None:
    pp = _make()
    raw = " The batch max decoding.\nThe RSSI aggregation.\nThe dispenser hardware. "
    normalized = pp.normalize(raw)
    assert normalized == "The batch max decoding. The RSSI aggregation. The dispenser hardware."
    final = pp.finalize(normalized)
    assert final == "The batch max decoding. The RSSI aggregation. The dispenser hardware.\n"


def test_whitespace_only_input() -> None:
    pp = _make()
    assert pp.normalize("   \n\n   ") == ""
    assert pp.finalize("") == ""
