import pytest

from clawtube.core.metadata import Chapter, VideoMeta
from clawtube.core.transcript import TranscriptResult, TranscriptSegment


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep the transcript cache (~/.cache/clawtube) out of the real home dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


@pytest.fixture
def meta():
    return VideoMeta(
        id="dQw4w9WgXcQ",
        title="How Transformers Work",
        channel="Example Channel",
        upload_date="20240101",
        duration=3725,
        view_count=10,
        description="",
        chapters=[Chapter("Intro", 0), Chapter("Attention", 75)],
    )


@pytest.fixture
def result():
    return TranscriptResult(
        video_id="dQw4w9WgXcQ",
        language="en",
        source="timedtext",
        segments=[
            TranscriptSegment(0.0, 2.5, "Welcome back."),
            TranscriptSegment(3725.4, 1.0, "Late line."),
        ],
    )


@pytest.fixture(autouse=True)
def reset_ydl_defaults():
    """cli.main() configures process-wide yt-dlp defaults; don't leak them between tests."""
    from clawtube.core import metadata

    yield
    metadata.configure_ydl()
    metadata.reset_client_order()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Tests are offline by contract; anything that forgets to mock fails loudly."""
    import urllib.request

    def blocked(*a, **kw):
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
