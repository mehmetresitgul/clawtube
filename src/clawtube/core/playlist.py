"""Playlist orchestration: resolve -> fetch (concurrent, resumable) -> write.

This is the piece that turns "one playlist link" into "every transcript in
it, on disk, as Markdown" - the core ask behind clawtube.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .cache import TranscriptCache
from .errors import BotCheckRequired, ClawtubeError, RateLimited
from .format import write_video_outputs
from .ids import playlist_url as build_playlist_url
from .metadata import get_video_meta, list_playlist_video_ids
from .state import RunState
from .transcript import TranscriptResult, TranscriptSegment, fetch_transcript

ProgressCallback = Callable[[str, str, Optional[str]], None]  # video_id, status, detail

# Escalating cooldowns (seconds) applied when a whole batch of videos comes
# back rate-limited rather than genuinely caption-less - YouTube's IP-level
# throttling clears on its own after a short wait, so we pause the entire
# run and retry those videos instead of reporting them as failed.
_COOLDOWN_SCHEDULE_SECONDS = [30, 60, 120, 240]

# Minimum gap between two workers starting a YouTube fetch. Spacing the starts
# out is what keeps `concurrency` workers from bursting into HTTP 429s.
_START_GAP_SECONDS = 0.3

_BOT_CHECK_SKIP = "skipped: YouTube kept asking for a bot check; re-run with --cookies-from-browser"

# clawtube already falls back to other YouTube player clients on a bot check,
# so one occasional bot check is noise. This many in a row with no success in
# between means YouTube has walled us off: stop instead of hammering it.
_BOT_CHECK_STREAK_LIMIT = 3


class _BotCheckBreaker:
    def __init__(self, limit: int):
        self.limit = limit
        self._streak = 0
        self._lock = threading.Lock()
        self.tripped = threading.Event()

    def success(self) -> None:
        with self._lock:
            self._streak = 0

    def bot_check(self) -> None:
        with self._lock:
            self._streak += 1
            if self._streak >= self.limit:
                self.tripped.set()

    def is_set(self) -> bool:
        return self.tripped.is_set()


class _StartThrottle:
    def __init__(self, gap: float):
        self.gap = gap
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.gap
        delay = start - now
        if delay > 0:
            time.sleep(delay)


def _process_one(
    video_id: str,
    langs: Tuple[str, ...],
    formats: List[str],
    out_dir: Path,
    cache: TranscriptCache,
    use_whisper_fallback: bool,
    throttle: Optional[_StartThrottle] = None,
    abort: Optional[_BotCheckBreaker] = None,
) -> Tuple[str, str, Optional[str]]:
    """Returns (video_id, status, error_or_path)."""
    try:
        cached = cache.get(video_id, langs)
        if cached:
            result = TranscriptResult(
                video_id=video_id,
                language=cached["language"],
                source=cached["source"] + " (cached)",
                segments=[TranscriptSegment(**s) for s in cached["segments"]],
            )
        else:
            if abort is not None and abort.is_set():
                return video_id, "failed", _BOT_CHECK_SKIP
            if throttle is not None:
                throttle.wait()
            result = fetch_transcript(video_id, langs, use_whisper_fallback)
            if abort is not None:
                abort.success()
            cache.set(
                video_id,
                langs,
                {
                    "language": result.language,
                    "source": result.source,
                    "segments": [s.__dict__ for s in result.segments],
                },
            )

        meta = get_video_meta(video_id, fallback=True)
        path = write_video_outputs(out_dir, meta, result, formats)
        return video_id, "done", str(path)
    except RateLimited as exc:
        return video_id, "rate_limited", str(exc)
    except BotCheckRequired as exc:
        if abort is not None:
            abort.bot_check()
        return video_id, "failed", str(exc)
    except ClawtubeError as exc:
        return video_id, "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad video must not kill the run
        return video_id, "failed", f"unexpected error: {exc}"


def extract_playlist(
    playlist_id_or_url: str,
    out_dir: Path,
    langs: Tuple[str, ...] = (),
    formats: Optional[List[str]] = None,
    concurrency: int = 4,
    use_whisper_fallback: bool = False,
    resume: bool = True,
    limit: Optional[int] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> RunState:
    formats = formats or ["md"]
    url = playlist_id_or_url if "://" in playlist_id_or_url else build_playlist_url(playlist_id_or_url)

    out_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(out_dir)
    if not resume:
        state.videos.clear()

    video_ids = list_playlist_video_ids(url, limit)
    pending = [v for v in video_ids if not state.is_done(v)]
    if on_progress:
        on_progress("*", "total", f"{len(video_ids)} {len(video_ids) - len(pending)}")

    cache = TranscriptCache()
    throttle = _StartThrottle(_START_GAP_SECONDS)
    abort = _BotCheckBreaker(_BOT_CHECK_STREAK_LIMIT)

    cooldown_round = 0
    while pending:
        rate_limited: List[str] = []

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            # Submissions are instant; the throttle inside each worker
            # spaces out the actual YouTube requests, so the progress bar
            # starts moving right away even for a 300-video playlist.
            futures = {
                pool.submit(
                    _process_one, vid, langs, formats, out_dir, cache, use_whisper_fallback, throttle, abort
                ): vid
                for vid in pending
            }

            for future in as_completed(futures):
                video_id, status, detail = future.result()
                if status == "done":
                    state.mark(video_id, "done", output_path=detail)
                elif status == "rate_limited":
                    # Don't mark state yet - this isn't a real failure, it's
                    # YouTube throttling us. Retry it after a cooldown.
                    rate_limited.append(video_id)
                else:
                    state.mark(video_id, "failed", error=detail)
                if on_progress:
                    on_progress(video_id, status, detail)

        if not rate_limited or abort.is_set():
            for vid in rate_limited if abort.is_set() else []:
                state.mark(vid, "failed", error=_BOT_CHECK_SKIP)
                if on_progress:
                    on_progress(vid, "failed", _BOT_CHECK_SKIP)
            break

        if cooldown_round >= len(_COOLDOWN_SCHEDULE_SECONDS):
            # Given it every chance the schedule allows - report these as
            # genuinely failed for now, but the run is still resumable.
            for vid in rate_limited:
                state.mark(
                    vid,
                    "failed",
                    error="still rate-limited by YouTube after several cooldown retries; run the command again later",
                )
                if on_progress:
                    on_progress(vid, "failed", "still rate-limited after cooldown retries")
            break

        cooldown = _COOLDOWN_SCHEDULE_SECONDS[cooldown_round]
        if on_progress:
            on_progress(
                "*",
                "cooldown",
                f"YouTube is rate-limiting us - pausing {cooldown}s before retrying "
                f"{len(rate_limited)} video(s) (round {cooldown_round + 1}/{len(_COOLDOWN_SCHEDULE_SECONDS)})",
            )
        time.sleep(cooldown)
        pending = rate_limited
        cooldown_round += 1

    return state
