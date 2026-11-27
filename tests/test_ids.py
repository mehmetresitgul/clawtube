import pytest

from clawtube.core.ids import ParsedRef, channel_videos_url, parse_ref


def test_watch_url():
    ref = parse_ref("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert ref.kind == "video"
    assert ref.id == "dQw4w9WgXcQ"


def test_short_url():
    ref = parse_ref("https://youtu.be/dQw4w9WgXcQ")
    assert ref.kind == "video"
    assert ref.id == "dQw4w9WgXcQ"


def test_bare_id():
    ref = parse_ref("dQw4w9WgXcQ")
    assert ref.kind == "video"
    assert ref.id == "dQw4w9WgXcQ"


def test_playlist_url():
    ref = parse_ref("https://www.youtube.com/playlist?list=PLFsQleAmcz5wg8-8f3ryd8LWL5x82vGey")
    assert ref.kind == "playlist"
    assert ref.id == "PLFsQleAmcz5wg8-8f3ryd8LWL5x82vGey"


def test_watch_url_with_list_and_video():
    ref = parse_ref("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLFsQleAmcz5wg8-8f3ryd8LWL5x82vGey")
    assert ref.kind == "video"
    assert ref.id == "dQw4w9WgXcQ"


def test_channel_handle():
    ref = parse_ref("https://www.youtube.com/@ExampleChannel")
    assert ref.kind == "channel"
    assert ref.id == "@ExampleChannel"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.youtube.com/@Naval", "https://www.youtube.com/@Naval/videos"),
        ("https://www.youtube.com/@Naval/videos", "https://www.youtube.com/@Naval/videos"),
        ("https://www.youtube.com/@Naval/shorts?si=abc", "https://www.youtube.com/@Naval/videos"),
        ("youtube.com/@Naval", "https://www.youtube.com/@Naval/videos"),
        ("https://www.youtube.com/channel/UCabc123/", "https://www.youtube.com/channel/UCabc123/videos"),
        ("https://www.youtube.com/c/SomeName/featured", "https://www.youtube.com/c/SomeName/videos"),
        ("https://www.youtube.com/user/olduser", "https://www.youtube.com/user/olduser/videos"),
    ],
)
def test_channel_videos_url(text, expected):
    ref = parse_ref(text)
    assert ref.kind == "channel"
    assert channel_videos_url(ref) == expected


def test_bare_channel_handle():
    ref = parse_ref("@TED")
    assert (ref.kind, ref.id) == ("channel", "@TED")
    assert channel_videos_url(ref) == "https://www.youtube.com/@TED/videos"


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/shorts/jNQXAC9IVRw",
    "https://youtube.com/shorts/jNQXAC9IVRw?feature=share",
    "https://www.youtube.com/live/jNQXAC9IVRw",
    "https://www.youtube.com/embed/jNQXAC9IVRw?start=10",
    "https://m.youtube.com/watch?v=jNQXAC9IVRw",
])
def test_other_video_url_shapes(url):
    assert parse_ref(url) == ParsedRef("video", "jNQXAC9IVRw", url)
