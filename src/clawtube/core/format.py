"""Output writers: per-video Markdown, a combined playlist doc, and raw
SRT/VTT export. Timestamps are rendered as clickable youtu.be links so a
human (or an agent) can jump straight to the moment in the source video."""

from __future__ import annotations

from pathlib import Path
from typing import List

from .ids import video_timestamp_url
from .metadata import VideoMeta
from .transcript import TranscriptResult


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_markdown(meta: VideoMeta, result: TranscriptResult) -> str:
    lines = [
        f"# {meta.title}",
        "",
        f"- **Channel:** {meta.channel or 'unknown'}",
        f"- **Video:** https://www.youtube.com/watch?v={meta.id}",
        f"- **Upload date:** {meta.upload_date or 'unknown'}",
        f"- **Duration:** {_hms(meta.duration) if meta.duration else 'unknown'}",
        f"- **Transcript source:** {result.source} ({result.language})",
        "",
    ]

    if meta.chapters:
        lines.append("## Chapters")
        for c in meta.chapters:
            lines.append(f"- [{_hms(c.start_seconds)}]({video_timestamp_url(meta.id, c.start_seconds)}) {c.title}")
        lines.append("")

    lines.append("## Transcript")
    lines.append("")
    for seg in result.segments:
        ts = _hms(seg.start)
        link = video_timestamp_url(meta.id, seg.start)
        lines.append(f"**[{ts}]({link})** {seg.text}")

    return "\n".join(lines) + "\n"


def to_srt(result: TranscriptResult) -> str:
    def srt_time(t: float) -> str:
        ms = int(round((t - int(t)) * 1000))
        h, rem = divmod(int(t), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for i, seg in enumerate(result.segments, start=1):
        end = seg.start + seg.duration
        blocks.append(f"{i}\n{srt_time(seg.start)} --> {srt_time(end)}\n{seg.text}\n")
    return "\n".join(blocks)


def combined_markdown(entries: List[tuple[VideoMeta, TranscriptResult]]) -> str:
    parts = [f"# Playlist transcript ({len(entries)} videos)", ""]
    for meta, result in entries:
        parts.append(to_markdown(meta, result))
        parts.append("\n---\n")
    return "\n".join(parts)


def write_video_outputs(out_dir: Path, meta: VideoMeta, result: TranscriptResult, formats: List[str]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in meta.title)[:80]
    # No leading "-" or "." (breaks shell tools / hides the file). When metadata
    # was unavailable the title is just the id, so use a neutral prefix instead.
    safe_title = safe_title.strip().lstrip("-._ ")
    if not safe_title or meta.title == meta.id:
        safe_title = "video"
    base = out_dir / f"{safe_title}_{meta.id}"

    md_path = base.with_suffix(".md")
    md_path.write_text(to_markdown(meta, result), encoding="utf-8")

    if "srt" in formats:
        base.with_suffix(".srt").write_text(to_srt(result), encoding="utf-8")

    return md_path
