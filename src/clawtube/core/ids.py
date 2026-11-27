"""URL / ID parsing helpers.

YouTube exposes the same resource (video, playlist, channel) through a
handful of URL shapes. Centralizing the parsing here means every other
module can just ask for "the video id" or "the playlist id" and not worry
about whether the user pasted a youtu.be link, a full watch URL with extra
query params, or a bare 11-char id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{13,}$")


@dataclass(frozen=True)
class ParsedRef:
    kind: str  # "video" | "playlist" | "channel" | "unknown"
    id: str
    raw: str


def parse_ref(text: str) -> ParsedRef:
    """Classify a pasted URL/id as a video, playlist, or channel reference."""
    text = text.strip()

    if _VIDEO_ID_RE.match(text):
        return ParsedRef("video", text, text)

    if re.fullmatch(r"@[\w.-]+", text):
        return ParsedRef("channel", text, text)

    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.netloc or "").lower().replace("www.", "").replace("m.", "")
    qs = parse_qs(parsed.query)
    path_parts = [p for p in parsed.path.split("/") if p]

    if host == "youtu.be" and path_parts:
        return ParsedRef("video", path_parts[0][:11], text)

    if host in ("youtube.com", "music.youtube.com"):
        if "list" in qs and qs["list"]:
            playlist_id = qs["list"][0]
            # A "watch?v=...&list=..." link is a video *inside* a playlist.
            # We treat it as a playlist reference when there's no explicit
            # video id, otherwise the caller decides which one they want.
            if "v" not in qs:
                return ParsedRef("playlist", playlist_id, text)
        if "v" in qs and qs["v"]:
            return ParsedRef("video", qs["v"][0], text)
        if (
            len(path_parts) >= 2
            and path_parts[0] in ("shorts", "live", "embed", "v")
            and _VIDEO_ID_RE.match(path_parts[1][:11])
        ):
            return ParsedRef("video", path_parts[1][:11], text)
        if path_parts and path_parts[0] == "playlist" and "list" in qs:
            return ParsedRef("playlist", qs["list"][0], text)
        if path_parts and path_parts[0] in ("channel", "c", "user"):
            return ParsedRef("channel", "/".join(path_parts[:2]), text)
        if path_parts and path_parts[0].startswith("@"):
            return ParsedRef("channel", path_parts[0], text)

    if _PLAYLIST_ID_RE.match(text) and text.startswith(("PL", "UU", "FL", "LL", "RD")):
        return ParsedRef("playlist", text, text)

    return ParsedRef("unknown", text, text)


def video_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def video_timestamp_url(video_id: str, seconds: float) -> str:
    return f"https://youtu.be/{video_id}?t={int(seconds)}"


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def channel_videos_url(ref: "ParsedRef") -> str:
    """URL of a channel's "Videos" tab, which yt-dlp lists like a playlist.

    Accepts whatever parse_ref recognised as a channel: /@handle,
    /channel/UC..., /c/name or /user/name, with or without a trailing tab.
    """
    if "://" in ref.raw or ref.raw.startswith(("youtube.com", "www.youtube.com", "m.youtube.com")):
        parsed = urlparse(ref.raw if "://" in ref.raw else f"https://{ref.raw}")
        parts = [p for p in parsed.path.split("/") if p]
    else:
        parts = [p for p in ref.id.split("/") if p]
    tabs = ("videos", "shorts", "streams", "playlists", "featured", "about", "community", "live")
    if parts and parts[-1] in tabs:
        parts = parts[:-1]
    # Only the channel identity matters: "@handle" or "channel|c|user/<name>".
    if parts and parts[0].startswith("@"):
        parts = parts[:1]
    else:
        parts = parts[:2]
    return "https://www.youtube.com/" + "/".join(parts) + "/videos"
