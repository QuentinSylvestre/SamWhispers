"""Tests for the SQLite transcription history store."""

from __future__ import annotations

from pathlib import Path

from samwhispers.history import HistoryStore


def _store(tmp_path: Path, **kw: object) -> HistoryStore:
    return HistoryStore(tmp_path / "history.db", **kw)  # type: ignore[arg-type]


def test_add_and_get(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rid = store.add("hello world", language="en", duration_ms=1500, cleanup_used=True)
    entry = store.get(rid)
    assert entry is not None
    assert entry["text"] == "hello world"
    assert entry["language"] == "en"
    assert entry["duration_ms"] == 1500
    assert entry["cleanup_used"] is True
    assert entry["created_at"]


def test_list_is_recent_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add("first")
    store.add("second")
    store.add("third")
    texts = [e["text"] for e in store.list()]
    assert texts == ["third", "second", "first"]


def test_list_pagination(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i in range(5):
        store.add(f"entry {i}")
    page1 = store.list(limit=2)
    page2 = store.list(limit=2, before_id=page1[-1]["id"])
    assert [e["text"] for e in page1] == ["entry 4", "entry 3"]
    assert [e["text"] for e in page2] == ["entry 2", "entry 1"]
    assert store.count() == 5


def test_search(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add("buy milk and eggs")
    store.add("schedule the meeting")
    store.add("translated note", translated_text="meeting notes")
    assert {e["text"] for e in store.list(search="meeting")} == {
        "schedule the meeting",
        "translated note",
    }
    assert store.count(search="milk") == 1
    assert store.count(search="nonexistent") == 0


def test_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rid = store.add("temp")
    assert store.delete(rid) is True
    assert store.get(rid) is None
    assert store.delete(rid) is False  # already gone


def test_clear(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add("a")
    store.add("b")
    assert store.clear() == 2
    assert store.count() == 0


def test_retention_prunes_oldest(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=3)
    for i in range(6):
        store.add(f"entry {i}")
    assert store.count() == 3
    assert [e["text"] for e in store.list()] == ["entry 5", "entry 4", "entry 3"]


def test_unlimited_when_max_entries_zero(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=0)
    for i in range(10):
        store.add(f"e{i}")
    assert store.count() == 10


def test_persists_across_instances(tmp_path: Path) -> None:
    _store(tmp_path).add("persisted")
    reopened = _store(tmp_path)
    assert reopened.count() == 1
    assert reopened.list()[0]["text"] == "persisted"
