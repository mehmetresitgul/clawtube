import sys
import types

import pytest

from clawtube.core import transcript as t
from clawtube.core.errors import NoCaptionsAvailable, RateLimited


def _res(source):
    return t.TranscriptResult("vid", "en", source, [t.TranscriptSegment(0, 1, "hi")])


def test_is_rate_limited():
    assert t._is_rate_limited(Exception("HTTP Error 429: Too Many Requests"))
    assert t._is_rate_limited(Exception("Too Many Requests"))
    assert not t._is_rate_limited(Exception("Video unavailable"))


def test_vtt_time_to_seconds():
    assert t._vtt_time_to_seconds("00:00:01.500") == 1.5
    assert t._vtt_time_to_seconds("01:02:03.000") == 3723
    assert t._vtt_time_to_seconds("02:03.250") == 123.25
    assert t._vtt_time_to_seconds("00:00:01,500") == 1.5


def test_parse_vtt():
    vtt = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.500 align:start position:0%\n"
        "Hello\nworld\n\n"
        "00:00:04.000 --> 00:00:05.000\n"
        "Second cue\n\n"
        "00:00:06.000 --> 00:00:07.000\n\n"
    )
    segs = t._parse_vtt(vtt)
    assert [(s.start, s.duration, s.text) for s in segs] == [
        (1.0, 2.5, "Hello world"),
        (4.0, 1.0, "Second cue"),
    ]


# Shape copied from real yt-dlp output for YouTube auto-captions: word-timing
# tags, whitespace-only lines inside cues, and each cue repeating the last line.
ROLLING_VTT = (
    "WEBVTT\nKind: captions\nLanguage: en\n\n"
    "00:00:00.000 --> 00:00:02.090 align:start position:0%\n \n"
    "hey<00:00:00.210><c> this</c><00:00:00.450><c> is</c><00:00:00.599><c> nivi</c>"
    "<00:00:01.050><c> you're</c><00:00:01.380><c> listening</c><00:00:01.860><c> to</c>"
    "<00:00:01.979><c> the</c>\n\n"
    "00:00:02.090 --> 00:00:02.100 align:start position:0%\n"
    "hey this is nivi you're listening to the\n \n\n"
    "00:00:02.100 --> 00:00:05.329 align:start position:0%\n"
    "hey this is nivi you're listening to the\n"
    "navall<00:00:02.429><c> podcast</c><00:00:03.120><c> this</c><00:00:03.959><c> is</c>\n\n"
    "00:00:05.329 --> 00:00:05.339 align:start position:0%\n"
    "navall podcast this is\n \n\n"
    "00:00:05.339 --> 00:00:07.550 align:start position:0%\n"
    "navall podcast this is\n"
    "one<00:00:05.700><c> giant</c> &gt;&gt; Tom &amp; Jerry\n"
)


def test_parse_vtt_rolling_autocaptions_strips_tags_and_duplicates():
    segs = t._parse_vtt(ROLLING_VTT)
    assert [s.text for s in segs] == [
        "hey this is nivi you're listening to the",
        "navall podcast this is",
        "one giant >> Tom & Jerry",
    ]
    assert [s.start for s in segs] == [0.0, 2.1, 5.339]
    # Durations are the cue that introduced each line, not the ~10ms repeat cues.
    assert segs[0].duration == pytest.approx(2.09)
    assert segs[1].duration == pytest.approx(5.329 - 2.1)
    assert not any("<" in s.text for s in segs)


def test_parse_vtt_keeps_repeated_line_when_not_adjacent():
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nNo.\n\n"
        "00:00:02.000 --> 00:00:03.000\nMaybe.\n\n"
        "00:00:03.000 --> 00:00:04.000\nNo.\n"
    )
    assert [s.text for s in t._parse_vtt(vtt)] == ["No.", "Maybe.", "No."]


def test_parse_vtt_empty():
    assert t._parse_vtt("WEBVTT\n") == []


@pytest.fixture(autouse=True)
def reset_timedtext_block(monkeypatch):
    monkeypatch.setattr(t, "_timedtext_blocked_until", 0.0)


def _patch_chain(monkeypatch, timedtext, ytdlp, whisper=None):
    """ytdlp: (result, kind[, reason])"""
    if len(ytdlp) == 2:
        ytdlp = (*ytdlp, None)
    monkeypatch.setattr(t, "_via_timedtext", lambda v, l: timedtext)
    monkeypatch.setattr(t, "_via_yt_dlp", lambda v, l: ytdlp)
    calls = []
    monkeypatch.setattr(t, "_via_whisper", lambda v: calls.append(v) or whisper)
    return calls


def test_chain_prefers_timedtext(monkeypatch):
    _patch_chain(monkeypatch, (_res("timedtext"), False), (_res("yt-dlp"), t.YT_OK))
    assert t.fetch_transcript("vid").source == "timedtext"


def test_chain_falls_back_to_yt_dlp(monkeypatch):
    _patch_chain(monkeypatch, (None, False), (_res("yt-dlp"), t.YT_OK))
    assert t.fetch_transcript("vid").source == "yt-dlp"


def test_chain_whisper_only_when_enabled(monkeypatch):
    calls = _patch_chain(monkeypatch, (None, False), (None, t.YT_NO_SUBS), whisper=_res("whisper"))
    with pytest.raises(NoCaptionsAvailable):
        t.fetch_transcript("vid")
    assert calls == []
    assert t.fetch_transcript("vid", use_whisper_fallback=True).source == "whisper"
    assert calls == ["vid"]


def test_chain_no_captions_message_mentions_whisper_hint(monkeypatch):
    _patch_chain(monkeypatch, (None, False), (None, t.YT_NO_SUBS))
    with pytest.raises(NoCaptionsAvailable, match="--whisper-fallback"):
        t.fetch_transcript("vid")


def test_chain_rate_limited_when_yt_dlp_throttled(monkeypatch):
    _patch_chain(monkeypatch, (None, True), (None, t.YT_RATE_LIMITED, "HTTP Error 429"))
    with pytest.raises(RateLimited, match="429"):
        t.fetch_transcript("vid")


def test_chain_network_error_is_retryable(monkeypatch):
    _patch_chain(monkeypatch, (None, False), (None, t.YT_NETWORK, "Connection reset"))
    with pytest.raises(RateLimited, match="network error"):
        t.fetch_transcript("vid")


def test_chain_blocked_timedtext_but_no_subs_is_no_captions(monkeypatch):
    # Timedtext IP-blocked, yet yt-dlp loaded the page fine and found no
    # caption tracks: that is a definite answer, not a reason for cooldowns.
    _patch_chain(monkeypatch, (None, True), (None, t.YT_NO_SUBS))
    with pytest.raises(NoCaptionsAvailable):
        t.fetch_transcript("vid")


def test_chain_bot_check_suggests_cookies(monkeypatch):
    from clawtube.core.errors import BotCheckRequired

    _patch_chain(monkeypatch, (None, True), (None, t.YT_BOT_CHECK, "Sign in to confirm you're not a bot"))
    with pytest.raises(BotCheckRequired, match="--cookies-from-browser"):
        t.fetch_transcript("vid")


def test_chain_reports_real_yt_dlp_error(monkeypatch):
    _patch_chain(monkeypatch, (None, False), (None, t.YT_ERROR, "Private video"))
    with pytest.raises(NoCaptionsAvailable, match="Private video"):
        t.fetch_transcript("vid")


def test_chain_whisper_rescues_rate_limited(monkeypatch):
    _patch_chain(monkeypatch, (None, True), (None, t.YT_RATE_LIMITED), whisper=_res("whisper"))
    assert t.fetch_transcript("vid", use_whisper_fallback=True).source == "whisper"


def test_error_classifiers():
    assert t._is_bot_check(Exception("ERROR: [youtube] x: Sign in to confirm you\u2019re not a bot."))
    assert t._is_network_error(Exception("Unable to connect to proxy"))
    assert not t._is_network_error(Exception("Private video"))
    assert t._short_reason(Exception("ERROR: [youtube] abcdefghijk: Private video. Sign in\nmore")) == "Private video. Sign in"


# --- _via_yt_dlp --------------------------------------------------------

VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"


def _track(url):
    return [{"ext": "json3", "url": url + "&fmt=json3"}, {"ext": "vtt", "url": url}]


INFO = {
    "id": "vid",
    "title": "T",
    "language": "en",
    "subtitles": {},
    "automatic_captions": {"en-orig": _track("auto-en-orig"), "de": _track("auto-de")},
}


def _fake_yt_dlp(monkeypatch, extract, bodies=None):
    """extract: callable(n) -> info dict (or raises); bodies: url -> bytes."""
    calls = {"extract": 0, "urls": []}

    class DownloadError(Exception):
        pass

    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            assert download is False
            calls["extract"] += 1
            return extract(calls["extract"])

        def urlopen(self, url):
            calls["urls"].append(url)
            return Resp((bodies or {}).get(url, VTT.encode()))

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = FakeYDL
    mod.utils = types.SimpleNamespace(DownloadError=DownloadError)
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    return calls, DownloadError


def test_yt_dlp_success_uses_original_language_auto_track(monkeypatch):
    from clawtube.core import metadata

    calls, _ = _fake_yt_dlp(monkeypatch, lambda n: INFO)
    result, kind, reason = t._via_yt_dlp("vid", ())
    assert (kind, reason) == (t.YT_OK, None)
    assert result.source == "yt-dlp"
    assert result.language == "en"
    assert result.segments[0].text == "Hello"
    assert calls["urls"] == ["auto-en-orig"]
    # The info dict is kept so metadata does not need a second page load.
    assert metadata._take_info("vid") is INFO


def test_yt_dlp_respects_language_preference(monkeypatch):
    calls, _ = _fake_yt_dlp(monkeypatch, lambda n: INFO)
    result, kind, _ = t._via_yt_dlp("vid", ("de", "en"))
    assert kind == t.YT_OK and result.language == "de"
    assert calls["urls"] == ["auto-de"]


def test_yt_dlp_retries_on_429_then_succeeds(monkeypatch):
    holder = {}

    def extract(n):
        if n < 3:  # first two attempts throttled, third works
            raise holder["E"]("HTTP Error 429: Too Many Requests")
        return INFO

    calls, holder["E"] = _fake_yt_dlp(monkeypatch, extract)
    result, kind, _ = t._via_yt_dlp("vid", ())
    assert calls["extract"] == 3
    assert kind == t.YT_OK and result is not None


def test_yt_dlp_gives_up_after_max_retries(monkeypatch):
    holder = {}

    def extract(n):
        raise holder["E"]("HTTP Error 429")

    calls, holder["E"] = _fake_yt_dlp(monkeypatch, extract)
    result, kind, _ = t._via_yt_dlp("vid", ())
    assert result is None and kind == t.YT_RATE_LIMITED
    assert calls["extract"] == t._MAX_RETRIES + 1


BOT = "ERROR: [youtube] vid: Sign in to confirm you\u2019re not a bot. Use --cookies"


def test_yt_dlp_bot_check_tries_every_client_then_gives_up(monkeypatch):
    holder = {}

    def extract(n):
        raise holder["E"](BOT)

    calls, holder["E"] = _fake_yt_dlp(monkeypatch, extract)
    result, kind, reason = t._via_yt_dlp("vid", ())
    assert calls["extract"] == 3  # default, web_embedded, mweb - no retry loop on top
    assert result is None and kind == t.YT_BOT_CHECK
    assert reason.startswith("Sign in to confirm")


def test_yt_dlp_bot_check_falls_back_to_other_client_and_remembers_it(monkeypatch):
    from clawtube.core import metadata

    holder = {"clients": []}

    def extract(n):
        if n == 1:
            raise holder["E"](BOT)
        return INFO

    calls, holder["E"] = _fake_yt_dlp(monkeypatch, extract)
    result, kind, _ = t._via_yt_dlp("vid", ())
    assert kind == t.YT_OK and calls["extract"] == 2
    # Next video starts straight with the client that got through.
    assert metadata._client_order[0] == "web_embedded"
    t._via_yt_dlp("vid2", ())
    assert calls["extract"] == 3


def test_yt_dlp_fallback_client_other_error_still_tries_the_next(monkeypatch):
    holder = {}

    def extract(n):
        if n == 1:
            raise holder["E"](BOT)
        if n == 2:
            raise holder["E"]("ERROR: [youtube] vid: Playback on other websites has been disabled")
        return INFO

    calls, holder["E"] = _fake_yt_dlp(monkeypatch, extract)
    result, kind, _ = t._via_yt_dlp("vid", ())
    assert kind == t.YT_OK and calls["extract"] == 3


def test_yt_dlp_non_rate_limit_error_does_not_retry(monkeypatch):
    holder = {}

    def extract(n):
        raise holder["E"]("ERROR: [youtube] vid: Video unavailable")

    calls, holder["E"] = _fake_yt_dlp(monkeypatch, extract)
    result, kind, reason = t._via_yt_dlp("vid", ())
    assert calls["extract"] == 1
    assert (result, kind, reason) == (None, t.YT_ERROR, "Video unavailable")


def test_yt_dlp_empty_caption_body_is_a_soft_rate_limit(monkeypatch):
    calls, _ = _fake_yt_dlp(monkeypatch, lambda n: INFO, bodies={"auto-en-orig": b""})
    result, kind, reason = t._via_yt_dlp("vid", ())
    assert result is None and kind == t.YT_RATE_LIMITED and "empty" in reason
    assert len(calls["urls"]) == t._MAX_RETRIES + 1


def test_yt_dlp_no_caption_tracks(monkeypatch):
    calls, _ = _fake_yt_dlp(monkeypatch, lambda n: {"id": "vid", "subtitles": {}, "automatic_captions": {}})
    assert t._via_yt_dlp("vid", ()) == (None, t.YT_NO_SUBS, None)
    assert calls["extract"] == 3  # every client asked before concluding "no captions"


def test_yt_dlp_captionless_client_falls_through_to_one_with_captions(monkeypatch):
    # A soft-blocked client answers without error but lists no caption tracks.
    calls, _ = _fake_yt_dlp(monkeypatch, lambda n: INFO if n == 2 else {"id": "vid"})
    result, kind, _ = t._via_yt_dlp("vid", ())
    assert kind == t.YT_OK and calls["extract"] == 2


def test_yt_dlp_bot_check_plus_captionless_reports_bot_check(monkeypatch):
    holder = {}

    def extract(n):
        if n == 1:
            raise holder["E"](BOT)
        return {"id": "vid"}

    calls, holder["E"] = _fake_yt_dlp(monkeypatch, extract)
    result, kind, _ = t._via_yt_dlp("vid", ())
    assert result is None and kind == t.YT_BOT_CHECK


def test_pick_track_prefers_manual_then_original_language():
    info = {
        "language": "tr",
        "subtitles": {"live_chat": _track("chat"), "en": _track("man-en"), "tr": _track("man-tr")},
        "automatic_captions": {"tr-orig": _track("auto-tr")},
    }
    assert t._pick_track(info, ()) == ("tr", "man-tr")
    assert t._pick_track(info, ("en",)) == ("en", "man-en")
    assert t._pick_track(info, ("fr",)) is None
    info["subtitles"] = {}
    assert t._pick_track(info, ()) == ("tr", "auto-tr")
    assert t._pick_track({"automatic_captions": {"de": _track("x")}}, ()) is None
    # No "language" field: the -orig auto track tells us it is English, so a
    # community translation listed first must not win.
    info = {
        "subtitles": {"ar": _track("man-ar"), "en": _track("man-en")},
        "automatic_captions": {"en-orig": _track("auto-en")},
    }
    assert t._pick_track(info, ()) == ("en", "man-en")


def test_pick_track_multi_audio_video_keeps_original_language():
    # Seen live (Evrim Agaci): a Turkish video with an English dub. The client
    # was served the dub, so "language" is "en" and both -orig tracks exist.
    info = {
        "language": "en",
        "title": "Antidepresanlar Çalışıyor mu?",
        "description": "Şu anda psikolojiniz nasıl? Kendinizi huzurlu, mutlu hissediyor musunuz?",
        "formats": [{"format_id": "18", "language": "en", "language_preference": -1}],
        "subtitles": {"en-US": _track("man-en"), "de": _track("man-de")},
        "automatic_captions": {"en-orig": _track("auto-en"), "tr-orig": _track("auto-tr"), "tr": _track("tr-tl")},
    }
    assert t._pick_track(info, ()) == ("tr", "auto-tr")
    assert t._pick_track(info, ("en",)) == ("en-US", "man-en")
    # Formats that flag the original track explicitly win outright.
    info["title"] = info["description"] = ""
    info["formats"] = [{"language": "tr", "language_preference": 10}]
    assert t._pick_track(info, ()) == ("tr", "auto-tr")


def test_pick_track_single_audio_trusts_reported_language():
    # Seen live: preference -1 also appears on ordinary single-audio videos,
    # so it is no evidence of a dub.
    info = {
        "language": "tr",
        "formats": [{"format_id": "18", "language": "tr", "language_preference": -1}],
        "subtitles": {"live_chat": _track("chat")},
        "automatic_captions": {"tr-orig": _track("auto-tr"), "en": _track("auto-en")},
    }
    assert t._pick_track(info, ()) == ("tr", "auto-tr")


def test_text_language_score():
    assert t._text_language_score("Işık hızını neden hala ölçemedik?", "tr") > t._text_language_score(
        "Işık hızını neden hala ölçemedik?", "en")
    assert t._text_language_score("Why is the speed of light so hard to measure", "en") > 0
    assert t._text_language_score("Привет мир", "ru") > 0


def test_pick_track_prefers_exact_code_and_cleans_track_ids():
    # Shapes seen live: user-added tracks carry an id suffix.
    info = {
        "language": "tr",
        "subtitles": {"en-eEY6OEpapPo": _track("man-en"), "tr-KZrcWfPZoKc": _track("man-tr")},
        "automatic_captions": {"tr-orig": _track("auto-tr")},
    }
    assert t._pick_track(info, ()) == ("tr", "man-tr")
    assert t._pick_track(info, ("en",)) == ("en", "man-en")
    info["subtitles"]["en"] = _track("man-en-exact")
    assert t._pick_track(info, ("en",)) == ("en", "man-en-exact")
    assert [t._clean_lang(c) for c in ("pt-BR", "es-419", "zh-Hans", "en-orig")] == ["pt-BR", "es-419", "zh-Hans", "en"]


# --- _via_timedtext -----------------------------------------------------

def _fake_yta(monkeypatch, api_cls):
    pkg = types.ModuleType("youtube_transcript_api")
    pkg.YouTubeTranscriptApi = api_cls
    errors = types.ModuleType("youtube_transcript_api._errors")

    class NoTranscriptFound(Exception): ...
    class RequestBlocked(Exception): ...
    class TranscriptsDisabled(Exception): ...
    class VideoUnavailable(Exception): ...

    for c in (NoTranscriptFound, RequestBlocked, TranscriptsDisabled, VideoUnavailable):
        setattr(errors, c.__name__, c)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", pkg)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api._errors", errors)
    return errors


class _Snip:
    def __init__(self, start, duration, text):
        self.start, self.duration, self.text = start, duration, text


def test_timedtext_with_langs(monkeypatch):
    class Fetched(list):
        language_code = "tr"

    class Api:
        def fetch(self, vid, languages):
            assert languages == ["tr", "en"]
            return Fetched([_Snip(0, 1, "merhaba")])

    _fake_yta(monkeypatch, Api)
    result, limited = t._via_timedtext("vid", ("tr", "en"))
    assert not limited
    assert (result.source, result.language, result.segments[0].text) == ("timedtext", "tr", "merhaba")


def test_timedtext_without_langs_uses_first_listed(monkeypatch):
    class Fetched(list): ...

    class Transcript:
        language_code = "de"

        def fetch(self):
            return Fetched([_Snip(1, 2, "hallo")])

    class Api:
        def list(self, vid):
            return [Transcript()]

    Transcript.is_generated = False

    _fake_yta(monkeypatch, Api)
    result, _ = t._via_timedtext("vid", ())
    assert result.language == "de"


class _Listed:
    def __init__(self, code, generated):
        self.language_code, self.is_generated = code, generated


def test_pick_listed_transcript_prefers_spoken_language():
    # Seen live: the first listed track was a community translation (ar).
    pick = t._pick_listed_transcript
    assert pick([_Listed("ar", False), _Listed("en", False), _Listed("en", True)]).language_code == "en"
    tr = pick([_Listed("en", False), _Listed("tr", True)])
    assert (tr.language_code, tr.is_generated) == ("tr", True)
    assert pick([_Listed("en-US", False), _Listed("tr", False), _Listed("tr", True)]).is_generated is False
    assert pick([_Listed("zh", False), _Listed("en-GB", False)]).language_code == "en-GB"
    assert pick([]) is None


def test_timedtext_captcha_redirect_flags_block(monkeypatch):
    class Api:
        def fetch(self, vid, languages):
            raise RuntimeError("Max retries exceeded with url: /sorry/index?continue=...")

    _fake_yta(monkeypatch, Api)
    assert t._via_timedtext("vid", ("en",)) == (None, True)


def test_timedtext_blocked_flags_rate_limit(monkeypatch):
    holder = {}

    class Api:
        def fetch(self, vid, languages):
            raise holder["errors"].RequestBlocked()

    holder["errors"] = _fake_yta(monkeypatch, Api)
    assert t._via_timedtext("vid", ("en",)) == (None, True)


def test_timedtext_block_skips_further_requests(monkeypatch):
    holder = {"n": 0}

    class Api:
        def fetch(self, vid, languages):
            holder["n"] += 1
            raise holder["errors"].RequestBlocked()

    holder["errors"] = _fake_yta(monkeypatch, Api)
    assert t._via_timedtext("vid", ("en",)) == (None, True)
    assert t._via_timedtext("vid2", ("en",)) == (None, True)
    assert holder["n"] == 1


def test_timedtext_no_transcript_is_not_rate_limit(monkeypatch):
    holder = {}

    class Api:
        def fetch(self, vid, languages):
            raise holder["errors"].NoTranscriptFound()

    holder["errors"] = _fake_yta(monkeypatch, Api)
    assert t._via_timedtext("vid", ("en",)) == (None, False)


def test_timedtext_unexpected_error_falls_through(monkeypatch):
    class Api:
        def fetch(self, vid, languages):
            raise RuntimeError("parse error")

    _fake_yta(monkeypatch, Api)
    assert t._via_timedtext("vid", ("en",)) == (None, False)


def test_chain_whisper_failure_reason_is_reported(monkeypatch):
    from clawtube.core.errors import WhisperFailed

    _patch_chain(monkeypatch, (None, False), (None, t.YT_NO_SUBS))

    def boom(v):
        raise WhisperFailed("audio download failed: HTTP Error 403")

    monkeypatch.setattr(t, "_via_whisper", boom)
    with pytest.raises(NoCaptionsAvailable, match="Whisper fallback also failed \\(audio download failed: HTTP Error 403\\)"):
        t.fetch_transcript("vid", use_whisper_fallback=True)
