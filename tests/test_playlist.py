import pytest

from clawtube.core import playlist as pl
from clawtube.core.errors import NoCaptionsAvailable, RateLimited
from clawtube.core.metadata import VideoMeta
from clawtube.core.state import RunState
from clawtube.core.transcript import TranscriptResult, TranscriptSegment

IDS = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    sleeps = []
    monkeypatch.setattr(pl.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(pl, "list_playlist_video_ids", lambda url, limit=None: list(IDS[:limit] if limit else IDS))
    monkeypatch.setattr(
        pl, "get_video_meta",
        lambda vid, fallback=False: VideoMeta(vid, f"Title {vid}", "ch", "20240101", 10, 1, "", []),
    )
    return sleeps


def _ok(vid, langs=(), whisper=False):
    return TranscriptResult(vid, "en", "timedtext", [TranscriptSegment(0, 1, f"text {vid}")])


def test_extracts_all_videos(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "fetch_transcript", _ok)
    events = []
    state = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path, on_progress=lambda *a: events.append(a))
    assert state.summary() == {"done": 3}
    assert len(list(tmp_path.glob("*.md"))) == 3
    assert events[0] == ("*", "total", "3 0")
    assert {e[1] for e in events[1:]} == {"done"}


def test_resume_skips_done_videos(tmp_path, monkeypatch):
    RunState(tmp_path).mark("aaaaaaaaaaa", "done", output_path="x")
    fetched = []
    monkeypatch.setattr(pl, "fetch_transcript", lambda v, l, w: fetched.append(v) or _ok(v))
    pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path)
    assert sorted(fetched) == ["bbbbbbbbbbb", "ccccccccccc"]


def test_no_resume_refetches_everything(tmp_path, monkeypatch):
    RunState(tmp_path).mark("aaaaaaaaaaa", "done", output_path="x")
    fetched = []
    monkeypatch.setattr(pl, "fetch_transcript", lambda v, l, w: fetched.append(v) or _ok(v))
    pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path, resume=False)
    assert sorted(fetched) == sorted(IDS)


def test_failure_is_recorded_and_does_not_stop_run(tmp_path, monkeypatch):
    def fetch(vid, langs, whisper):
        if vid == "bbbbbbbbbbb":
            raise NoCaptionsAvailable("none")
        return _ok(vid)

    monkeypatch.setattr(pl, "fetch_transcript", fetch)
    state = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path)
    assert state.summary() == {"done": 2, "failed": 1}
    assert state.videos["bbbbbbbbbbb"].error == "none"


def test_unexpected_exception_is_contained(tmp_path, monkeypatch):
    def fetch(vid, langs, whisper):
        raise ValueError("kaboom")

    monkeypatch.setattr(pl, "fetch_transcript", fetch)
    state = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path)
    assert state.summary() == {"failed": 3}
    assert "kaboom" in state.videos["aaaaaaaaaaa"].error


def test_rate_limited_videos_retry_after_cooldown(tmp_path, monkeypatch, fast):
    seen = {}

    def fetch(vid, langs, whisper):
        seen[vid] = seen.get(vid, 0) + 1
        if vid == "bbbbbbbbbbb" and seen[vid] == 1:
            raise RateLimited("throttled")
        return _ok(vid)

    monkeypatch.setattr(pl, "fetch_transcript", fetch)
    events = []
    state = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path, on_progress=lambda *a: events.append(a))
    assert state.summary() == {"done": 3}
    assert seen["bbbbbbbbbbb"] == 2 and seen["aaaaaaaaaaa"] == 1
    assert 30 in fast  # first cooldown step
    assert any(e[1] == "cooldown" for e in events)


def test_persistent_rate_limit_ends_as_failed_but_resumable(tmp_path, monkeypatch, fast):
    def fetch(vid, langs, whisper):
        raise RateLimited("throttled")

    monkeypatch.setattr(pl, "fetch_transcript", fetch)
    state = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path)
    assert state.summary() == {"failed": 3}
    assert "run the command again later" in state.videos["aaaaaaaaaaa"].error
    for cooldown in pl._COOLDOWN_SCHEDULE_SECONDS:
        assert cooldown in fast

    # A later run (YouTube calmed down) picks the failed ones up again.
    monkeypatch.setattr(pl, "fetch_transcript", _ok)
    # failed != done, so they are pending again
    state2 = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path)
    assert state2.summary() == {"done": 3}


def test_uses_transcript_cache_between_runs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pl, "fetch_transcript", lambda v, l, w: calls.append(v) or _ok(v))
    pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path / "one")
    pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path / "two")  # different out dir, same cache
    assert len(calls) == 3


def test_playlist_id_expanded_to_url(tmp_path, monkeypatch):
    urls = []
    monkeypatch.setattr(pl, "list_playlist_video_ids", lambda u, limit=None: urls.append(u) or [])
    pl.extract_playlist("PLabc", tmp_path)
    assert urls == ["https://www.youtube.com/playlist?list=PLabc"]


def test_limit_only_processes_first_n(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "fetch_transcript", _ok)
    state = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path, limit=2)
    assert sorted(state.videos) == sorted(IDS[:2])


def test_total_reports_already_done_on_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "fetch_transcript", _ok)
    pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path, limit=1)
    events = []
    pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path, on_progress=lambda *a: events.append(a))
    assert events[0] == ("*", "total", "3 1")


def test_bot_check_stops_fetching_the_rest(tmp_path, monkeypatch):
    from clawtube.core.errors import BotCheckRequired

    fetched = []

    def fetch(v, l, w):
        fetched.append(v)
        raise BotCheckRequired(f"{v}: sign in to confirm you're not a bot")

    monkeypatch.setattr(pl, "fetch_transcript", fetch)
    monkeypatch.setattr(pl, "_BOT_CHECK_STREAK_LIMIT", 2)
    state = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path, concurrency=1)
    assert fetched == IDS[:2]  # stops once the streak limit is hit
    assert state.summary() == {"failed": 3}
    assert "cookies" in state.videos[IDS[2]].error
    # Not marked done, so re-running (with cookies) retries all of them.
    assert not any(state.is_done(v) for v in IDS)


def test_occasional_bot_check_does_not_stop_the_run(tmp_path, monkeypatch):
    from clawtube.core.errors import BotCheckRequired

    def fetch(v, l, w):
        if v == IDS[0]:
            raise BotCheckRequired(f"{v}: bot check")
        return _ok(v)

    monkeypatch.setattr(pl, "fetch_transcript", fetch)
    monkeypatch.setattr(pl, "_BOT_CHECK_STREAK_LIMIT", 2)
    state = pl.extract_playlist("PLxxxxxxxxxxxxx", tmp_path, concurrency=1)
    assert state.summary() == {"failed": 1, "done": 2}


def test_start_throttle_spaces_out_requests(monkeypatch):
    clock = {"t": 100.0}
    slept = []
    monkeypatch.setattr(pl.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(pl.time, "sleep", lambda s: slept.append(round(s, 2)))
    th = pl._StartThrottle(0.3)
    th.wait(); th.wait(); th.wait()
    assert slept == [0.3, 0.6]
