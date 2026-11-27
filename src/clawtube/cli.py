"""clawtube command-line interface.

    clawtube video <url-or-id>      -> single video transcript
    clawtube playlist <url-or-id>   -> every video in a playlist
    clawtube channel <url-or-@name> -> every video on a channel
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from .core.cache import TranscriptCache
from .core.errors import ClawtubeError
from .core.format import write_video_outputs
from . import __version__
from .core.ids import ParsedRef, channel_videos_url, parse_ref
from .core.metadata import configure_ydl, get_video_meta
from .core.playlist import extract_playlist
from .core.transcript import fetch_transcript

console = Console()


def _langs_arg(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in value.split(",") if x.strip()) if value else ()


def _whisper_preflight(args: argparse.Namespace) -> bool:
    """Fail fast when --whisper-fallback was asked for but cannot work."""
    if not args.whisper_fallback:
        return True
    from .core.whisper_fallback import missing_requirements

    problem = missing_requirements()
    if problem:
        console.print(f"[red]--whisper-fallback unavailable:[/red] {problem}")
        return False
    return True


def cmd_video(args: argparse.Namespace) -> int:
    if not _whisper_preflight(args):
        return 1
    ref = parse_ref(args.target)
    if ref.kind not in ("video",):
        console.print(f"[red]Not a video URL/id:[/red] {args.target}")
        return 1

    cache = TranscriptCache()
    langs = _langs_arg(args.langs)
    try:
        cached = cache.get(ref.id, langs)
        if cached:
            from .core.transcript import TranscriptResult, TranscriptSegment
            result = TranscriptResult(
                ref.id, cached["language"], cached["source"] + " (cached)",
                [TranscriptSegment(**s) for s in cached["segments"]],
            )
        else:
            result = fetch_transcript(ref.id, langs, args.whisper_fallback)
            cache.set(ref.id, langs, {
                "language": result.language, "source": result.source,
                "segments": [s.__dict__ for s in result.segments],
            })
        meta = get_video_meta(ref.id, fallback=True)
        path = write_video_outputs(Path(args.out), meta, result, args.formats.split(","))
    except ClawtubeError as exc:
        console.print(f"[red]Failed:[/red] {exc}", soft_wrap=True)
        return 1

    console.print(f"[green]Wrote[/green] {path}  (source: {result.source}, lang: {result.language})")
    return 0


def _run_bulk(source: str, args: argparse.Namespace) -> int:
    """Shared by `playlist` and `channel`: extract every video behind `source`
    (a playlist id/URL) with a progress bar and a failure summary."""
    if not _whisper_preflight(args):
        return 1
    langs = _langs_arg(args.langs)
    formats = args.formats.split(",")

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching playlist...", total=None)

        def on_progress(video_id: str, status: str, detail: str | None) -> None:
            if status == "total":
                total, already_done = (int(x) for x in (detail or "0 0").split())
                progress.update(task, total=total, completed=already_done, description="Fetching transcripts...")
                if already_done:
                    console.print(f"Resuming: {already_done}/{total} already done, skipping those.")
                return
            if status == "cooldown":
                # A whole-run pause because YouTube is rate-limiting us -
                # not a per-video result, so don't advance the bar.
                progress.update(task, description=f"cooldown: {detail}")
                return
            if status == "rate_limited":
                # Will be retried after a cooldown; not a finished video yet.
                progress.update(task, description=f"rate limited: {video_id}")
                return
            progress.update(task, advance=1, description=f"{status}: {video_id}")

        try:
            state = extract_playlist(
                source,
                Path(args.out),
                langs=langs,
                formats=formats,
                concurrency=args.concurrency,
                use_whisper_fallback=args.whisper_fallback,
                resume=not args.no_resume,
                limit=args.limit,
                on_progress=on_progress,
            )
        except Exception as exc:  # noqa: BLE001 - e.g. private/missing playlist
            console.print(f"[red]Could not list videos:[/red] {exc}")
            return 1

    summary = state.summary()
    console.print(f"\n[bold]Done.[/bold] {summary}")
    failed = [vid for vid, s in state.videos.items() if s.status == "failed"]
    if failed:
        console.print(f"[yellow]{len(failed)} video(s) failed:[/yellow]")
        for vid in failed:
            error = state.videos[vid].error or "unknown error"
            # Errors already start with the id ("abc123: ..."); don't repeat it.
            if error.startswith(f"{vid}: "):
                error = error[len(vid) + 2:]
            console.print(f"  - {vid}: {error}", soft_wrap=True)
        console.print("Re-run the same command to retry only the failed videos.")
    return 0 if not failed else 2


def cmd_playlist(args: argparse.Namespace) -> int:
    ref = parse_ref(args.target)
    if ref.kind == "video":
        # "watch?v=...&list=..." - the user ran `playlist`, so they want the list.
        list_id = parse_qs(urlparse(args.target if "://" in args.target else f"https://{args.target}").query).get("list")
        if list_id:
            ref = ParsedRef("playlist", list_id[0], args.target)
    if ref.kind == "channel":
        console.print(f"[red]That is a channel.[/red] Use: clawtube channel {args.target}")
        return 1
    if ref.kind != "playlist":
        console.print(f"[red]Not a playlist URL/id:[/red] {args.target}")
        return 1
    return _run_bulk(ref.id, args)


def cmd_channel(args: argparse.Namespace) -> int:
    ref = parse_ref(args.target)
    if ref.kind != "channel":
        console.print(f"[red]Not a channel URL/handle:[/red] {args.target}")
        return 1
    return _run_bulk(channel_videos_url(ref), args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clawtube",
        description="Local-first YouTube transcripts: one video, a playlist or a whole channel. No API key.",
        epilog=(
            "examples:\n"
            '  clawtube video "https://youtu.be/VIDEO_ID"\n'
            '  clawtube playlist "https://www.youtube.com/playlist?list=PLAYLIST_ID" --langs tr,en\n'
            "  clawtube channel @ChannelName --limit 20 --formats md,srt\n\n"
            "Run `clawtube <command> --help` for all options."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"clawtube {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = dict(
        langs=("--langs", {"default": "", "metavar": "CODES", "help": "Preferred caption languages in order, e.g. tr,en (default: the video's own language)"}),
        out=("--out", {"default": "./clawtube_out", "metavar": "DIR", "help": "Output directory (default: ./clawtube_out)"}),
        formats=("--formats", {"default": "md", "help": "Comma-separated output formats: md, srt (default: md)"}),
        cookies=("--cookies", {"default": None, "metavar": "FILE", "help": "Netscape cookies.txt for YouTube (fixes 'Sign in to confirm you're not a bot')"}),
        browser=("--cookies-from-browser", {"default": None, "metavar": "BROWSER[:PROFILE]", "help": "Use cookies from a browser, e.g. chrome or firefox:default"}),
        whisper=("--whisper-fallback", {"action": "store_true", "help": "Fall back to local Whisper when no captions exist"}),
    )

    p_video = sub.add_parser("video", help="Extract one video's transcript")
    p_video.add_argument("target", help="Video URL (watch, youtu.be, shorts) or 11-character video id")
    p_video.add_argument(common["langs"][0], **common["langs"][1])
    p_video.add_argument(common["out"][0], **common["out"][1])
    p_video.add_argument(common["formats"][0], **common["formats"][1])
    p_video.add_argument(common["whisper"][0], **common["whisper"][1])
    p_video.add_argument(common["cookies"][0], **common["cookies"][1])
    p_video.add_argument(common["browser"][0], **common["browser"][1])
    p_video.set_defaults(func=cmd_video)

    for name, func, help_text in (
        ("playlist", cmd_playlist, "Extract every video in a playlist"),
        ("channel", cmd_channel, "Extract every video on a channel"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "target",
            help="Playlist URL or id" if name == "playlist" else "Channel URL or @handle",
        )
        p.add_argument(common["langs"][0], **common["langs"][1])
        p.add_argument(common["out"][0], **common["out"][1])
        p.add_argument(common["formats"][0], **common["formats"][1])
        p.add_argument(common["whisper"][0], **common["whisper"][1])
        p.add_argument(common["cookies"][0], **common["cookies"][1])
        p.add_argument(common["browser"][0], **common["browser"][1])
        p.add_argument("--concurrency", type=int, default=4, metavar="N", help="Videos fetched in parallel (default: 4; lower it if YouTube rate-limits you)")
        p.add_argument("--limit", type=int, default=None, metavar="N", help="Only the first N videos (newest first for channels)")
        p.add_argument("--no-resume", action="store_true", help="Ignore any existing checkpoint and start over")
        p.set_defaults(func=func)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_ydl(cookies=args.cookies, cookies_from_browser=args.cookies_from_browser)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
