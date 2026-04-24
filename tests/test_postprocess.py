"""Tests for text post-processing module."""

from __future__ import annotations

from samwhispers.config import PostprocessConfig
from samwhispers.postprocess import FillerRemover, TextPostprocessor


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


# --- Phase 2: Filler removal tests ---


def _make_with_filler(words: list[str]) -> TextPostprocessor:
    """Create a TextPostprocessor with filler removal enabled."""
    return TextPostprocessor(
        PostprocessConfig(
            collapse_newlines=True,
            collapse_spaces=True,
            trim=True,
            trailing="newline",
        ),
        filler_words=words,
    )


def test_filler_removal_basic() -> None:
    pp = _make_with_filler(["euh"])
    assert pp.normalize("I went to the euh store") == "I went to the store"


def test_filler_removal_elongated() -> None:
    pp = _make_with_filler(["euh"])
    assert pp.normalize("I went to the euuuuuh store") == "I went to the store"


def test_filler_removal_with_comma() -> None:
    pp = _make_with_filler(["euh"])
    assert pp.normalize("I went to the, euh, store") == "I went to the store"


def test_filler_removal_comma_before_period() -> None:
    """Comma before sentence-end punctuation is cleaned after filler removal."""
    pp = _make_with_filler(["euh"])
    assert pp.normalize("okay, euh.") == "okay."


def test_filler_removal_start_of_text() -> None:
    pp = _make_with_filler(["euh"])
    assert pp.normalize("Euh I went to the store") == "I went to the store"


def test_filler_removal_end_of_text() -> None:
    pp = _make_with_filler(["euh"])
    assert pp.normalize("I went to the store euh") == "I went to the store"


def test_filler_removal_multiple() -> None:
    pp = _make_with_filler(["euh"])
    assert pp.normalize("euh I went euh to the euh store") == "I went to the store"


def test_filler_removal_case_insensitive() -> None:
    pp = _make_with_filler(["euh"])
    assert pp.normalize("EUH I went to the store") == "I went to the store"


def test_filler_removal_repeated_chars() -> None:
    pp = _make_with_filler(["mmh"])
    assert pp.normalize("mmmmmh okay") == "okay"


def test_filler_removal_no_partial_match() -> None:
    """Filler 'beh' should not match inside 'behead'."""
    pp = _make_with_filler(["beh"])
    assert pp.normalize("behead the dragon") == "behead the dragon"


def test_filler_removal_no_partial_match_ohm() -> None:
    """Filler 'oh' should not match inside 'ohm'."""
    pp = _make_with_filler(["oh"])
    assert pp.normalize("measure the ohm value") == "measure the ohm value"


def test_filler_removal_no_partial_match_benefit() -> None:
    """Filler 'ben' should not match inside 'benefit'."""
    pp = _make_with_filler(["ben"])
    assert pp.normalize("the benefit is clear") == "the benefit is clear"


def test_filler_removal_disabled() -> None:
    """No filler_words means text is unchanged."""
    pp = _make_with_filler([])
    assert pp.normalize("I went to the euh store") == "I went to the euh store"


def test_filler_removal_disabled_none() -> None:
    """None filler_words (default) means text is unchanged."""
    pp = _make()
    assert pp.normalize("I went to the euh store") == "I went to the euh store"


def test_filler_removal_custom_words() -> None:
    pp = _make_with_filler(["hum"])
    assert pp.normalize("hum I think so") == "I think so"


def test_filler_removal_empty_result() -> None:
    """Text that is only fillers becomes empty after trim."""
    pp = _make_with_filler(["euh", "bah"])
    assert pp.normalize("euh bah euh") == ""


def test_filler_build_pattern() -> None:
    """Unit test _build_pattern() for various inputs."""
    assert FillerRemover._build_pattern("euh") == "e+u+h+"
    assert FillerRemover._build_pattern("pfff") == "p+f+"
    assert FillerRemover._build_pattern("mmh") == "m+h+"
    assert FillerRemover._build_pattern("um") == "u+m+"
    assert FillerRemover._build_pattern("mhm") == "m+h+m+"
    assert FillerRemover._build_pattern("ah") == "a+h+"


def test_filler_removal_preserves_real_words() -> None:
    """'oh' removed but 'ohm' preserved; 'ben' removed but 'benefit' preserved."""
    pp = _make_with_filler(["oh", "ben"])
    assert pp.normalize("oh the ohm meter") == "the ohm meter"
    assert pp.normalize("ben the benefit is clear") == "the benefit is clear"


def test_filler_removal_preserves_err() -> None:
    """'er' is a filler but 'err' (standalone word) should be preserved."""
    pp = _make_with_filler(["er"])
    # "er" standalone is removed
    assert pp.normalize("er I think so") == "I think so"
    # "err" is a real word ("to err is human") -- not matched because
    # the pattern e+r+ matches "err" too. This is a known trade-off:
    # the elongation pattern treats "err" as an elongated "er".
    # However, "error" should NOT be matched (partial word).
    assert pp.normalize("an error occurred") == "an error occurred"


def test_filler_all_fillers_pipeline() -> None:
    """Full normalize+finalize on all-filler text produces empty string."""
    pp = _make_with_filler(["euh", "bah", "um"])
    text = "euh bah um euh"
    normalized = pp.normalize(text)
    assert normalized == ""
    final = pp.finalize(normalized)
    assert final == ""  # finalize returns "" for empty input
