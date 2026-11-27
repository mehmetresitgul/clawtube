import pytest

from clawtube.core import metadata as md
from clawtube.core.errors import VideoUnavailable


class DownloadError(Exception):
    pass


def _fake_ydl(monkeypatch, outcomes):
    """outcomes: list of dict (returned info) or Exception (raised) per call."""
    calls = {"n": 0}

    class FakeYDL:
        def __init__(self, opts): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def extract_info(self, url, download=False):
            out = outcomes[min(calls["n"], len(outcomes) - 1)]
            calls["n"] += 1
            if isinstance(out, Exception):
                raise out
            return out

    monkeypatch.setattr(md.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(md.yt_dlp.utils, "DownloadError", DownloadError)
    monkeypatch.setattr(md.time, "sleep", lambda s: None)
    return calls


def test_meta_parsed(monkeypatch):
    _fake_ydl(monkeypatch, [{
        "id": "vid", "title": "T", "uploader": "U", "upload_date": "20240101",
        "duration": 10, "chapters": [{"title": " Intro ", "start_time": 0}, {"start_time": 5}],
    }])
    m = md.get_video_meta("vid")
    assert (m.title, m.channel) == ("T", "U")
    assert [c.title for c in m.chapters] == ["Intro", "Untitled"]


def test_meta_retries_transient_errors(monkeypatch):
    calls = _fake_ydl(monkeypatch, [
        DownloadError("Sign in to confirm you're not a bot"),
        DownloadError("HTTP Error 429"),
        {"id": "vid", "title": "T"},
    ])
    assert md.get_video_meta("vid").title == "T"
    assert calls["n"] == 3


def test_meta_permanent_error_raises_without_retry(monkeypatch):
    calls = _fake_ydl(monkeypatch, [DownloadError("Video unavailable")])
    with pytest.raises(VideoUnavailable):
        md.get_video_meta("vid")
    assert calls["n"] == 1


def test_meta_fallback_keeps_pipeline_alive(monkeypatch):
    _fake_ydl(monkeypatch, [DownloadError("Sign in to confirm you're not a bot")])
    monkeypatch.setattr(md, "_oembed_meta", lambda v: None)
    m = md.get_video_meta("vid", fallback=True)
    assert (m.id, m.title, m.channel, m.chapters) == ("vid", "vid", None, [])


def test_list_playlist_video_ids(monkeypatch):
    _fake_ydl(monkeypatch, [{"entries": [{"id": "a"}, None, {"id": "b"}, {"title": "no id"}]}])
    assert md.list_playlist_video_ids("url") == ["a", "b"]


def test_configure_ydl_cookies():
    md.configure_ydl(cookies="/tmp/c.txt", cookies_from_browser="firefox:default")
    opts = md.ydl_opts(skip_download=False)
    assert opts["cookiefile"] == "/tmp/c.txt"
    assert opts["cookiesfrombrowser"] == ("firefox", "default")
    assert opts["skip_download"] is False and opts["quiet"] is True
    md.configure_ydl()
    assert "cookiefile" not in md.ydl_opts() and "cookiesfrombrowser" not in md.ydl_opts()


def test_silent_logger_swallows_everything():
    log = md.ydl_opts()["logger"]
    for method in (log.debug, log.info, log.warning, log.error):
        assert method("noise") is None


def test_meta_fallback_uses_oembed_title_and_channel(monkeypatch):
    import io

    _fake_ydl(monkeypatch, [DownloadError("Sign in to confirm you're not a bot")])
    seen = {}

    def fake_urlopen(url, timeout):
        seen["url"] = url
        return io.BytesIO(b'{"title": "Real Title", "author_name": "Real Channel"}')

    monkeypatch.setattr(md.urllib.request, "urlopen", fake_urlopen)
    m = md.get_video_meta("vid", fallback=True)
    assert (m.title, m.channel) == ("Real Title", "Real Channel")
    assert "oembed" in seen["url"] and "watch%3Fv%3Dvid" in seen["url"]


def test_oembed_failure_falls_back_to_id(monkeypatch):
    def boom(url, timeout):
        raise OSError("offline")

    monkeypatch.setattr(md.urllib.request, "urlopen", boom)
    assert md._oembed_meta("vid") is None
    assert md.fallback_meta("vid").title == "vid"


def test_meta_reuses_info_from_subtitle_fetch(monkeypatch):
    calls = _fake_ydl(monkeypatch, [DownloadError("should not be called")])
    md.remember_info("vid", {"id": "vid", "title": "Cached", "channel": "C", "chapters": None})
    m = md.get_video_meta("vid")
    assert (m.title, m.channel, calls["n"]) == ("Cached", "C", 0)
    # Used once, then forgotten: a later lookup goes back to YouTube.
    with pytest.raises(VideoUnavailable):
        md.get_video_meta("vid")


def test_js_runtimes_enable_whatever_is_installed(monkeypatch):
    monkeypatch.setattr(md.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    assert md.ydl_opts()["js_runtimes"] == {"node": {}}
    monkeypatch.setattr(md.shutil, "which", lambda name: None)
    assert md.ydl_opts()["js_runtimes"] == {"deno": {}}
