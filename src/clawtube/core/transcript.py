"""The fallback chain that makes clawtube resilient where single-method
tools give up:

1. youtube-transcript-api  - hits YouTube's timedtext endpoint directly.
   Fast, no video download, works for the vast majority of videos that
   have *any* caption track (manual or auto-generated).
2. yt-dlp subtitle extraction - falls back here when (1) fails. yt-dlp
   speaks a different protocol path and supports cookies-from-browser,
   which recovers videos that block the plain timedtext API.
3. Local Whisper transcription (optional, --whisper-fallback) - for the
   remaining videos that truly have no caption track at all. Downloads
   audio only (not video) and transcribes on-device, so it never needs an
   API key either.

Every level returns the same normalized `TranscriptSegment` shape so the
rest of the pipeline (formatting, caching) doesn't care which source won.
"""

from __future__ import annotations

import html
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .errors import BotCheckRequired, ClawtubeError, NoCaptionsAvailable, RateLimited
from .metadata import extract_video_info, remember_info, ydl_opts

_MAX_RETRIES = 3

# Once the timedtext API reports an IP block (typical on cloud/VPN IPs) it
# stays blocked for a while, so stop wasting a request per video on it and go
# straight to yt-dlp for this long.
_TIMEDTEXT_BLOCK_SECONDS = 600
_timedtext_blocked_until = 0.0
_timedtext_lock = threading.Lock()

_BOT_CHECK_HINT = (
    "YouTube wants you to sign in to confirm you're not a bot. Re-run with "
    "--cookies-from-browser chrome (or firefox, edge, ...) or --cookies cookies.txt"
)


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "Too Many Requests" in text


def _is_bot_check(exc: Exception) -> bool:
    text = str(exc)
    return "Sign in to confirm" in text or "confirm you\u2019re not a bot" in text or "confirm you're not a bot" in text


_NETWORK_MARKERS = (
    "Unable to connect to proxy",
    "Tunnel connection failed",
    "Connection reset",
    "Connection refused",
    "timed out",
    "Temporary failure in name resolution",
    "Network is unreachable",
    "Name or service not known",
    "getaddrinfo failed",
)


def _is_network_error(exc: Exception) -> bool:
    text = str(exc)
    return any(m in text for m in _NETWORK_MARKERS)


def _short_reason(exc: Exception) -> str:
    """First line of a yt-dlp error, without its "ERROR: [youtube] id:" prefix."""
    line = (str(exc).strip().splitlines() or [""])[0]
    line = re.sub(r"^ERROR:\s*", "", line)
    line = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", line)
    return line[:200]


@dataclass
class TranscriptSegment:
    start: float
    duration: float
    text: str


@dataclass
class TranscriptResult:
    video_id: str
    language: str
    source: str  # "timedtext" | "yt-dlp" | "whisper"
    segments: List[TranscriptSegment]


def _block_timedtext() -> None:
    global _timedtext_blocked_until
    with _timedtext_lock:
        _timedtext_blocked_until = time.time() + _TIMEDTEXT_BLOCK_SECONDS


def _pick_listed_transcript(transcripts: list):
    """From youtube-transcript-api's listing (manual tracks first, then
    auto-generated), choose the video's own language: the auto-generated
    track is speech recognition of the audio, so its language is the
    spoken one; prefer a manual track in that language, else the auto one.
    Without any auto track, a manual English track, else the first one."""
    if not transcripts:
        return None
    manual = [t for t in transcripts if not getattr(t, "is_generated", False)]
    generated = [t for t in transcripts if getattr(t, "is_generated", False)]
    if generated:
        spoken = generated[0].language_code.split("-")[0].lower()
        for t in manual:
            if t.language_code.lower() == spoken:
                return t
        for t in manual:
            if t.language_code.split("-")[0].lower() == spoken:
                return t
        return generated[0]
    for t in manual:
        if t.language_code.split("-")[0].lower() == "en":
            return t
    return manual[0]


def _via_timedtext(video_id: str, langs: Tuple[str, ...]) -> Tuple[Optional[TranscriptResult], bool]:
    """Returns (result, was_rate_limited). was_rate_limited is True when the
    failure was YouTube throttling/blocking us rather than this video
    genuinely having no captions."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            NoTranscriptFound,
            RequestBlocked,
            TranscriptsDisabled,
            VideoUnavailable as YTAVideoUnavailable,
        )
    except ImportError:
        return None, False

    global _timedtext_blocked_until
    if time.time() < _timedtext_blocked_until:
        return None, True

    api = YouTubeTranscriptApi()
    try:
        if langs:
            fetched = api.fetch(video_id, languages=list(langs))
            lang_code = fetched.language_code
        else:
            # No language preference: the video's own language, not just
            # the first listed track (often a community translation).
            transcript = _pick_listed_transcript(list(api.list(video_id)))
            if transcript is None:
                return None, False
            fetched = transcript.fetch()
            lang_code = transcript.language_code
    except RequestBlocked:
        # IpBlocked / RequestBlocked: YouTube is throttling this IP, not
        # telling us this video lacks captions. Worth retrying later.
        _block_timedtext()
        return None, True
    except (NoTranscriptFound, TranscriptsDisabled, YTAVideoUnavailable, StopIteration):
        return None, False
    except Exception as exc:
        # A redirect to google.com/sorry (captcha) or a 429 is a block too.
        if _is_rate_limited(exc) or "/sorry/" in str(exc):
            _block_timedtext()
            return None, True
        # Any other transient failure (network hiccup, parsing) - let the
        # next tier in the chain try instead of hard-failing the whole run.
        return None, False

    segments = [TranscriptSegment(s.start, s.duration, s.text) for s in fetched]
    if not segments:
        return None, False
    return TranscriptResult(video_id, lang_code, "timedtext", segments), False


# Outcome kinds reported by _via_yt_dlp.
YT_OK = "ok"
YT_NO_SUBS = "no_subs"  # page loaded fine, video simply has no matching captions
YT_RATE_LIMITED = "rate_limited"  # HTTP 429 even after retries
YT_NETWORK = "network"  # connection problem, worth retrying later
YT_BOT_CHECK = "bot_check"  # "Sign in to confirm you're not a bot"
YT_ERROR = "error"  # anything else (private, removed, members-only...)


def _via_yt_dlp(video_id: str, langs: Tuple[str, ...]) -> Tuple[Optional[TranscriptResult], str, Optional[str]]:
    """Returns (result, kind, reason). `kind` says *why* there is no result
    (see the YT_* constants) so the caller can tell "this video has no
    captions" apart from "YouTube is blocking us right now".

    One page fetch gives us both the caption track list and the metadata
    (stashed for get_video_meta), then we download just the one track we
    picked - fewer requests means fewer 429s and bot checks."""
    import yt_dlp

    hit_rate_limit = False
    last_reason: Optional[str] = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            info = extract_video_info(video_id, need_captions=True)
            remember_info(video_id, info)
            track = _pick_track(info, langs)
            if track is None:
                if info.get("_clawtube_bot_checked"):
                    # Only a bot-checked client might have had the captions.
                    return None, YT_BOT_CHECK, "no caption tracks visible without passing YouTube's bot check"
                return None, YT_NO_SUBS, None
            lang, track_url = track
            with yt_dlp.YoutubeDL(ydl_opts()) as ydl, ydl.urlopen(track_url) as resp:
                raw = resp.read()
        except yt_dlp.utils.DownloadError as exc:
            last_reason = _short_reason(exc)
            if _is_bot_check(exc):
                return None, YT_BOT_CHECK, last_reason
            # YouTube rate-limits caption requests hard once several videos
            # in a playlist run get fetched back to back. Back off and retry
            # a few times instead of marking the video as failed on the
            # first 429.
            if _is_rate_limited(exc) or _is_network_error(exc):
                hit_rate_limit = hit_rate_limit or _is_rate_limited(exc)
                if attempt < _MAX_RETRIES:
                    time.sleep((2 ** attempt) + random.uniform(0, 1))
                    continue
                return None, (YT_RATE_LIMITED if hit_rate_limit else YT_NETWORK), last_reason
            return None, YT_ERROR, last_reason
        except Exception as exc:  # noqa: BLE001 - subtitle download (HTTP) errors
            last_reason = _short_reason(exc)
            if _is_rate_limited(exc) or _is_network_error(exc):
                hit_rate_limit = hit_rate_limit or _is_rate_limited(exc)
                if attempt < _MAX_RETRIES:
                    time.sleep((2 ** attempt) + random.uniform(0, 1))
                    continue
                return None, (YT_RATE_LIMITED if hit_rate_limit else YT_NETWORK), last_reason
            return None, YT_ERROR, f"subtitle download failed: {last_reason}"

        text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
        segments = _parse_vtt(text)
        if not segments:
            if not text.strip():
                # The track is listed but YouTube sent an empty body: that
                # is its soft rate limit (a 429 usually follows), not a
                # video without captions. Back off like any other 429.
                hit_rate_limit = True
                last_reason = "YouTube returned an empty caption file (rate limiting)"
                if attempt < _MAX_RETRIES:
                    time.sleep((2 ** attempt) + random.uniform(0, 1))
                    continue
                return None, YT_RATE_LIMITED, last_reason
            return None, YT_NO_SUBS, None
        return TranscriptResult(video_id, lang, "yt-dlp", segments), YT_OK, None

    return None, (YT_RATE_LIMITED if hit_rate_limit else YT_NETWORK), last_reason


def _vtt_url(formats) -> Optional[str]:
    for f in formats or []:
        if f.get("ext") == "vtt" and f.get("url"):
            return f["url"]
    return None


def _lang_matches(code: str, want: str) -> bool:
    code, want = code.lower(), want.lower()
    return code == want or code.startswith(want + "-")


def _clean_lang(code: str) -> str:
    """"en-orig" -> "en"; a user-added track id like "tr-KZrcWfPZoKc" -> "tr".
    Real regional/script tags ("pt-BR", "es-419", "zh-Hans") are kept."""
    code = code.replace("-orig", "")
    base, _, rest = code.partition("-")
    return base if len(rest) > 4 else code


# Small, deliberately conservative language hints, used only to tell which
# of several audio tracks is the original (see _original_language).
_SCRIPT_LANGS = (
    ("ru", r"[\u0400-\u04FF]"), ("uk", r"[\u0400-\u04FF]"), ("el", r"[\u0370-\u03FF]"),
    ("ar", r"[\u0600-\u06FF]"), ("fa", r"[\u0600-\u06FF]"), ("iw", r"[\u0590-\u05FF]"),
    ("he", r"[\u0590-\u05FF]"), ("hi", r"[\u0900-\u097F]"), ("th", r"[\u0E00-\u0E7F]"),
    ("ko", r"[\uAC00-\uD7AF]"), ("ja", r"[\u3040-\u30FF]"), ("zh", r"[\u4E00-\u9FFF]"),
)
_STOPWORDS = {
    "en": "the and is are you that this with for what how why not",
    "tr": "ve bir bu da de ile ne için mi mı nasıl neden çok daha gibi",
    "es": "el la los las que de y es por para una con cómo",
    "pt": "o a os as que de e é para uma com não como",
    "fr": "le la les des que et est pour une avec pas comment",
    "de": "der die das und ist nicht mit für ein eine wie warum",
    "it": "il la che di e è per una con non come",
    "nl": "de het een en is van dat niet met voor hoe",
    "id": "dan yang di ini itu dengan untuk tidak apa",
}
_LETTERS = {"tr": "çğışöüİ", "de": "ßäöü", "es": "ñ¿¡", "pt": "ãõç", "fr": "çèêàœ"}


def _text_language_score(text: str, lang: str) -> float:
    for code, pattern in _SCRIPT_LANGS:
        if code == lang:
            return len(re.findall(pattern, text)) / 3
    words = re.findall(r"[^\W\d_]+", text.lower())
    score = float(sum(w in _STOPWORDS.get(lang, "").split() for w in words))
    score += sum(text.count(ch) for ch in _LETTERS.get(lang, "")) / 2
    return score


def _original_language(info: dict, auto: dict) -> Tuple[str, set]:
    """-> (original language or "", languages that are dubs).

    A video with several audio tracks (dubs) has one "<lang>-orig" auto
    track per audio track, and yt-dlp's "language" is whichever track the
    client happened to be served - often a dub, depending on the network.
    yt-dlp flags the original track when YouTube says so (preference 10);
    otherwise the title and description, which are written in the original
    language, decide between the candidates."""
    for f in info.get("formats") or []:
        if f.get("language") and (f.get("language_preference") or 0) >= 10:
            original = f["language"].lower()
            others = {k[:-5].lower() for k in auto if k.endswith("-orig")} - {original}
            return original, others

    orig_tracks = [k[: -len("-orig")].lower() for k in auto if k.endswith("-orig")]
    language = (info.get("language") or "").lower()
    if len(orig_tracks) < 2:
        return language or (orig_tracks[0] if orig_tracks else ""), set()

    text = f"{info.get('title') or ''}\n{(info.get('description') or '')[:1500]}"
    scores = {c: _text_language_score(text, c) for c in orig_tracks}
    best = max(scores.values())
    leaders = [c for c in orig_tracks if scores[c] == best]
    if best > 0 and len(leaders) == 1:
        original = leaders[0]
    else:
        original = language if language in orig_tracks else orig_tracks[0]
    return original, set(orig_tracks) - {original}


def _pick_track(info: dict, langs: Tuple[str, ...]) -> Optional[Tuple[str, str]]:
    """Choose one caption track from yt-dlp's info dict -> (lang, vtt_url).

    Manual captions beat auto-generated ones. With a language preference we
    honour its order; without one we aim for the video's own language, never
    a machine translation of the auto captions into some other language."""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}
    original, dubs = _original_language(info, auto)

    def first(tracks: dict, want: Optional[str]) -> Optional[Tuple[str, str]]:
        # Exact code first ("en"), then regional ("en-US"), then anything
        # else with that prefix (e.g. "en-34kf3BCIwxA", a user-added track).
        codes = [c for c in tracks if want is None or _lang_matches(c, want)]
        if want is not None:
            codes.sort(key=lambda c: (c.lower() != want.lower(), len(c) != len(want) + 3))
        for code in codes:
            url = _vtt_url(tracks[code])
            if url:
                return _clean_lang(code), url
        return None

    if langs:
        for want in langs:
            hit = first(manual, want) or first(auto, want)
            if hit:
                return hit
        return None

    orig_auto = {k: v for k, v in auto.items() if k.endswith("-orig") and k[:-5].lower() not in dubs}
    candidates = []
    if original:
        candidates.append((manual, original))
        candidates.append((orig_auto, original))
    candidates.append((orig_auto, None))
    if original:
        candidates.append((auto, original))
    candidates.append((manual, "en"))
    candidates.append((auto, "en"))
    candidates.append((manual, None))
    for tracks, want in candidates:
        hit = first(tracks, want)
        if hit:
            return hit
    return None


_VTT_TAG_RE = re.compile(r"<[^>]*>")


def _clean_vtt_line(line: str) -> str:
    """Drop inline timing/style tags (<00:00:02.429>, <c>, <i>) and HTML entities."""
    return " ".join(html.unescape(_VTT_TAG_RE.sub("", line)).split())


def _parse_vtt(text: str) -> List[TranscriptSegment]:
    """WebVTT cue parser for yt-dlp's output.

    YouTube's auto-generated captions are "rolling": every cue repeats the
    previous cue's line(s) and adds one new line, and lines carry inline
    word-timing tags. We strip the tags and keep only lines that were not
    already shown by the previous cue, so each spoken line appears once.
    """
    segments: List[TranscriptSegment] = []
    lines = text.splitlines()
    prev_cue_lines: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            start_str, _, end_str = line.partition("-->")
            start = _vtt_time_to_seconds(start_str.strip())
            end_raw = end_str.strip().split(" ")[0]
            end = _vtt_time_to_seconds(end_raw)
            i += 1
            cue_lines = []
            # A cue ends at a truly empty line. YouTube's rolling captions
            # put whitespace-only lines (" ") *inside* cues, so a
            # strip()-based check would cut them short and lose the text.
            while i < len(lines) and lines[i].rstrip("\r\n") != "":
                cleaned = _clean_vtt_line(lines[i])
                if cleaned:
                    cue_lines.append(cleaned)
                i += 1
            new_lines = [l for l in cue_lines if l not in prev_cue_lines]
            if cue_lines:
                prev_cue_lines = cue_lines
            joined = " ".join(new_lines).strip()
            if joined:
                segments.append(TranscriptSegment(start, max(end - start, 0.0), joined))
        else:
            i += 1
    return segments


def _vtt_time_to_seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def _via_whisper(video_id: str) -> Optional[TranscriptResult]:
    try:
        from .whisper_fallback import transcribe_audio
    except ImportError:
        return None
    return transcribe_audio(video_id)


def fetch_transcript(
    video_id: str,
    langs: Tuple[str, ...] = (),
    use_whisper_fallback: bool = False,
) -> TranscriptResult:
    result, _ = _via_timedtext(video_id, langs)
    if result is not None:
        return result

    result, kind, reason = _via_yt_dlp(video_id, langs)
    if result is not None:
        return result

    whisper_reason: Optional[str] = None
    if use_whisper_fallback:
        try:
            result = _via_whisper(video_id)
        except ClawtubeError as exc:
            whisper_reason = str(exc)
        else:
            if result is not None:
                return result

    if kind == YT_BOT_CHECK:
        raise BotCheckRequired(f"{video_id}: {_BOT_CHECK_HINT}")

    if kind in (YT_RATE_LIMITED, YT_NETWORK):
        # The failure was YouTube throttling us (or the network dropping),
        # not this video lacking captions. The caller (playlist
        # orchestration) treats this differently: worth another try after
        # a cooldown instead of giving up on the video for good.
        what = "network error" if kind == YT_NETWORK else "rate-limited by YouTube"
        detail = f": {reason}" if reason else ""
        raise RateLimited(f"{video_id}: {what} (retry after a cooldown){detail}")

    if use_whisper_fallback:
        hint = f" and Whisper fallback also failed ({whisper_reason})" if whisper_reason else " and Whisper fallback also failed"
    else:
        hint = " (try --whisper-fallback)"
    if kind == YT_ERROR and reason:
        raise NoCaptionsAvailable(f"{video_id}: {reason}{hint if use_whisper_fallback else ''}")
    raise NoCaptionsAvailable(f"{video_id}: no captions via timedtext or yt-dlp{hint}")
