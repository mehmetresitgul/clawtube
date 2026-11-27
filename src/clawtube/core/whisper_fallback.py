"""Last-resort transcription for videos with zero caption tracks.

Downloads audio only (never video) via yt-dlp, then runs it through
faster-whisper entirely on-device. No API key, no upload to a third party -
this is the tier that guarantees clawtube can *always* produce a transcript,
which is the gap every timedtext/yt-dlp-subtitle-only tool leaves open.
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .errors import WhisperFailed
from .metadata import ydl_opts
from .transcript import TranscriptResult, TranscriptSegment


def missing_requirements() -> Optional[str]:
    """Human-readable reason the Whisper fallback cannot run, or None if it can."""
    if importlib.util.find_spec("faster_whisper") is None:
        return 'faster-whisper is not installed. Install it with: pip install "clawtube[whisper]"'
    if shutil.which("ffmpeg") is None:
        return "ffmpeg was not found on PATH (needed to extract audio). Install it from https://ffmpeg.org/"
    return None


def _download_audio(video_id: str, dest_dir: Path) -> Path:
    import yt_dlp

    outtmpl = str(dest_dir / f"{video_id}.%(ext)s")
    opts = ydl_opts(
        skip_download=False,
        format="bestaudio/best",
        outtmpl=outtmpl,
        postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    except yt_dlp.utils.DownloadError as exc:
        reason = str(exc).replace("ERROR: ", "").strip().splitlines()[0][:200]
        raise WhisperFailed(f"audio download failed: {reason}") from exc

    matches = sorted(dest_dir.glob(f"{video_id}.*"))
    if not matches:
        raise WhisperFailed("audio download produced no file")
    return matches[0]


def transcribe_audio(video_id: str, model_size: str = "base") -> TranscriptResult:
    """Raises WhisperFailed (with the reason) instead of returning nothing."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        # Optional dependency (`pip install clawtube[whisper]`) not installed.
        raise WhisperFailed('faster-whisper is not installed (pip install "clawtube[whisper]")') from exc

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = _download_audio(video_id, Path(tmp))

        model = WhisperModel(model_size, compute_type="int8")
        segments_iter, info = model.transcribe(str(audio_path), vad_filter=True)

        segments = [
            TranscriptSegment(start=s.start, duration=max(s.end - s.start, 0.0), text=s.text.strip())
            for s in segments_iter
            if s.text and s.text.strip()
        ]
        if not segments:
            raise WhisperFailed("no speech recognised in the audio")
        return TranscriptResult(video_id, info.language or "unknown", "whisper", segments)
