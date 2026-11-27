"""Video/playlist metadata via yt-dlp (title, channel, upload date, chapters).

We only ask yt-dlp for *metadata* here (no downloading of media), using
`extract_flat` where possible so a 300-video playlist doesn't trigger 300
full page parses just to get a list of ids.
"""

from __future__ import annotations

import json
import random
import shutil
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

import yt_dlp

from .errors import VideoUnavailable


@dataclass
class Chapter:
    title: str
    start_seconds: float


@dataclass
class VideoMeta:
    id: str
    title: str
    channel: Optional[str]
    upload_date: Optional[str]  # YYYYMMDD
    duration: Optional[float]
    view_count: Optional[int]
    description: Optional[str]
    chapters: List[Chapter] = field(default_factory=list)


class SilentLogger:
    """yt-dlp prints its own ERROR lines even with quiet=True; we surface
    failures ourselves, so keep them out of the progress display."""

    def debug(self, msg): ...
    def info(self, msg): ...
    def warning(self, msg): ...
    def error(self, msg): ...


# Options every yt-dlp call inherits; set once by the CLI (see configure_ydl).
_YDL_DEFAULTS: dict = {}


def configure_ydl(cookies: Optional[str] = None, cookies_from_browser: Optional[str] = None) -> None:
    """Route every yt-dlp call through the user's YouTube session. This is the
    way out of "Sign in to confirm you're not a bot" and age-restricted videos."""
    _YDL_DEFAULTS.clear()
    if cookies:
        _YDL_DEFAULTS["cookiefile"] = cookies
    if cookies_from_browser:
        # yt-dlp expects a tuple: (browser[, profile[, keyring[, container]]])
        _YDL_DEFAULTS["cookiesfrombrowser"] = tuple(cookies_from_browser.split(":"))


_JS_RUNTIMES = ("deno", "node", "bun", "quickjs")


def _available_js_runtimes() -> dict:
    """YouTube now serves a JavaScript challenge that yt-dlp solves with an
    external JS runtime (its "EJS" support). yt-dlp only enables Deno by
    default; turn on whichever supported runtime is actually installed so
    users with just Node (very common) get full extraction too."""
    found = {name: {} for name in _JS_RUNTIMES if shutil.which(name)}
    return found or {"deno": {}}


def ydl_opts(**overrides) -> dict:
    opts = {
        "logger": SilentLogger(),
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
        "js_runtimes": _available_js_runtimes(),
    }
    opts.update(_YDL_DEFAULTS)
    opts.update(overrides)
    return opts


_ydl_opts = ydl_opts  # historical name used inside this module


_META_RETRIES = 2

# Info dicts yt-dlp already fetched while getting subtitles, keyed by video id,
# so get_video_meta does not have to load the same watch page a second time.
_INFO_CACHE: dict = {}
_INFO_LOCK = threading.Lock()
_INFO_CACHE_MAX = 64


def remember_info(video_id: str, info: dict) -> None:
    with _INFO_LOCK:
        if len(_INFO_CACHE) >= _INFO_CACHE_MAX:
            _INFO_CACHE.pop(next(iter(_INFO_CACHE)))
        _INFO_CACHE[video_id] = info


def _take_info(video_id: str) -> Optional[dict]:
    with _INFO_LOCK:
        return _INFO_CACHE.pop(video_id, None)


# YouTube player clients to try, in order, when the default one gets the
# "Sign in to confirm you're not a bot" wall. The embedded-player client is
# rarely bot-checked (common on cloud/VPN IPs) and still lists every caption
# track; mweb is a second fallback. None = yt-dlp's own default clients.
_CLIENT_FALLBACKS = [None, "web_embedded", "mweb"]
_client_order = list(_CLIENT_FALLBACKS)
_client_lock = threading.Lock()


def _bot_checked(exc: Exception) -> bool:
    text = str(exc)
    return "confirm you" in text and "not a bot" in text


def _client_opts(client: Optional[str]) -> dict:
    if client is None:
        return {}
    return {"extractor_args": {"youtube": {"player_client": [client]}}}


def reset_client_order() -> None:
    with _client_lock:
        _client_order[:] = _CLIENT_FALLBACKS


def _has_captions(info: dict) -> bool:
    return bool(
        {k for k in (info.get("subtitles") or {}) if k != "live_chat"} or info.get("automatic_captions")
    )


def extract_video_info(video_id: str, need_captions: bool = False) -> dict:
    """yt-dlp info dict for one video (no download), trying the next player
    client whenever YouTube answers with a bot check - or, with
    need_captions, with a response that lists no caption tracks at all (a
    soft-blocked client looks exactly like that). Whichever client got
    through is tried first for the following videos, so a long playlist does
    not pay for the blocked client on every video.

    Raises yt-dlp's DownloadError if every client fails - the bot-check error
    if there was one, since that is the actionable one (use cookies). If a
    client answered but no client listed captions, that answer is returned,
    with "_clawtube_bot_checked" set when another client hit the bot wall."""
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    with _client_lock:
        order = list(_client_order)
    bot_error: Optional[Exception] = None
    captionless: Optional[dict] = None
    for client in order:
        try:
            with yt_dlp.YoutubeDL(ydl_opts(**_client_opts(client))) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            if _bot_checked(exc):
                bot_error = bot_error or exc
                continue
            if bot_error is None and captionless is None:
                # Private video, 429, network...: not something another
                # client fixes, and the first client gave a real answer.
                raise
            continue  # a fallback client failed differently; try the next
        info = info if isinstance(info, dict) else {}
        info["_clawtube_client"] = client
        if need_captions and not _has_captions(info):
            captionless = captionless or info
            continue
        if client != order[0]:
            with _client_lock:
                if client in _client_order:
                    _client_order.remove(client)
                _client_order.insert(0, client)
        return info
    if captionless is not None:
        if bot_error is not None:
            captionless["_clawtube_bot_checked"] = True
        return captionless
    raise bot_error  # type: ignore[misc]


def _is_transient(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "Too Many Requests" in text or "Sign in to confirm" in text


def _oembed_meta(video_id: str) -> Optional[VideoMeta]:
    """Title and channel from YouTube's oEmbed endpoint. It is not subject to
    the bot check that blocks the full video page, so it still works when
    yt-dlp cannot fetch metadata. No chapters/duration/date."""
    watch = urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}", safe="")
    url = f"https://www.youtube.com/oembed?format=json&url={watch}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
    except Exception:  # noqa: BLE001 - purely best-effort
        return None
    title = (data.get("title") or "").strip()
    if not title:
        return None
    return VideoMeta(video_id, title, data.get("author_name"), None, None, None, None, [])


def fallback_meta(video_id: str) -> VideoMeta:
    """Metadata used when YouTube refuses to serve the video page (bot check,
    rate limit) even though we already have its transcript: title/channel via
    oEmbed if possible, otherwise just the id."""
    return _oembed_meta(video_id) or VideoMeta(video_id, video_id, None, None, None, None, None, [])


def get_video_meta(video_id: str, fallback: bool = False) -> VideoMeta:
    """Fetch title/channel/chapters. With fallback=True a failure yields
    placeholder metadata instead of raising, so an already-fetched
    transcript is never thrown away just because the title lookup failed."""
    info = _take_info(video_id)
    for attempt in range(0 if info else _META_RETRIES + 1):
        try:
            info = extract_video_info(video_id)
            break
        except yt_dlp.utils.DownloadError as exc:
            if _is_transient(exc) and attempt < _META_RETRIES:
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            if fallback:
                return fallback_meta(video_id)
            raise VideoUnavailable(f"{video_id}: {exc}") from exc

    chapters = [
        Chapter(title=(c.get("title") or "").strip() or "Untitled", start_seconds=c.get("start_time") or 0.0)
        for c in (info.get("chapters") or [])
    ]

    return VideoMeta(
        id=info.get("id", video_id),
        title=info.get("title") or "Untitled",
        channel=info.get("channel") or info.get("uploader"),
        upload_date=info.get("upload_date"),
        duration=info.get("duration"),
        view_count=info.get("view_count"),
        description=info.get("description"),
        chapters=chapters,
    )


def list_playlist_video_ids(playlist_url: str, limit: Optional[int] = None) -> List[str]:
    """Fast id listing - does not fetch per-video metadata. `limit` keeps
    only the first N entries (newest first for a channel's Videos tab)."""
    overrides = {"extract_flat": "in_playlist"}
    if limit:
        overrides["playlistend"] = limit
    with yt_dlp.YoutubeDL(_ydl_opts(**overrides)) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
    entries = info.get("entries") or []
    ids = [e["id"] for e in entries if e and e.get("id")]
    return ids[:limit] if limit else ids
