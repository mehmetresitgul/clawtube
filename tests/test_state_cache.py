from pathlib import Path

from clawtube.core.cache import CACHE_VERSION, TranscriptCache
from clawtube.core.state import STATE_FILENAME, RunState


def test_state_roundtrip_and_resume(tmp_path):
    s = RunState(tmp_path)
    s.mark("a", "done", output_path="a.md")
    s.mark("b", "failed", error="boom")
    assert s.is_done("a") and not s.is_done("b") and not s.is_done("zzz")

    s2 = RunState(tmp_path)  # a fresh process re-reading the checkpoint
    assert s2.is_done("a")
    assert s2.videos["b"].error == "boom"
    assert s2.summary() == {"done": 1, "failed": 1}


def test_state_corrupt_file_starts_fresh(tmp_path):
    (tmp_path / STATE_FILENAME).write_text("{not json", encoding="utf-8")
    assert RunState(tmp_path).videos == {}


def test_cache_roundtrip(tmp_path):
    c = TranscriptCache(tmp_path)
    assert c.get("vid", ("en",)) is None
    c.set("vid", ("en",), {"language": "en", "segments": []})
    got = c.get("vid", ("en",))
    assert got["language"] == "en" and "_cached_at" in got


def test_cache_key_depends_on_langs(tmp_path):
    c = TranscriptCache(tmp_path)
    c.set("vid", ("en",), {"language": "en"})
    assert c.get("vid", ("tr",)) is None
    assert c.get("vid", ()) is None


def test_cache_corrupt_entry_is_a_miss(tmp_path):
    c = TranscriptCache(tmp_path)
    c.set("vid", ("en",), {"language": "en"})
    next(Path(c.root).glob("vid_*.json")).write_text("garbage", encoding="utf-8")
    assert c.get("vid", ("en",)) is None


def test_cache_ignores_entries_from_older_cache_versions(tmp_path):
    c = TranscriptCache(tmp_path)
    c.set("vid", ("en",), {"language": "en"})
    current = next(Path(c.root).glob("vid_*.json"))
    legacy = current.with_name(current.name.replace(f"_v{CACHE_VERSION}_", "_"))
    current.rename(legacy)  # what a pre-versioning clawtube wrote
    assert c.get("vid", ("en",)) is None
