---
name: clawtube-full
description: Extract YouTube transcripts for a single video, a full playlist, or a channel - locally, with no API key. Use this when the user pastes a YouTube video/playlist/channel link and asks for a transcript, captions, summary, or bulk transcript extraction.
---

# clawtube-full

You have access to the `clawtube` CLI, which fetches YouTube transcripts through a fallback chain (direct captions → yt-dlp subtitles → optional local Whisper) so it works even on videos without manual captions. No API key or account is required.

## When to use this skill

- The user pastes a YouTube video URL and asks to summarize, transcribe, or read it.
- The user pastes a playlist URL and wants transcripts for every video in it.
- The user pastes a channel URL/handle and wants recent uploads transcribed.
- The user wants an existing large transcript job resumed after an interruption.

## How to use it

1. If `clawtube` is not on PATH, install it: `pipx install "git+https://github.com/mehmetresitgul/clawtube.git"` (or `pip install` with the same URL; for the local-transcription fallback for videos with no captions at all, use `"clawtube[whisper] @ git+https://github.com/mehmetresitgul/clawtube.git"`).
2. For a single video:
   ```bash
   clawtube video "<video-url-or-id>" --langs <preferred-lang-codes> --out <output-dir>
   ```
   - Leave out `--langs` to get the video's own language; pass it only when the user wants a specific language (e.g. `--langs en` for an English transcript of a Turkish video, if one exists).
3. For a playlist (this is the common case - one link, every video in it):
   ```bash
   clawtube playlist "<playlist-url-or-id>" --langs <preferred-lang-codes> --out <output-dir> --concurrency 4
   ```
   - Add `--whisper-fallback` if the user wants videos with no captions transcribed anyway (slower, fully local).
   - Add `--formats md,srt` if the user wants SRT files alongside Markdown.
   - Playlist runs are resumable: if the command is interrupted, running it again with the same `--out` directory picks up only the videos that haven't finished yet.
4. For a channel link or `@handle`, use the channel command (newest videos first; `--limit` keeps it to the latest N, which is wise for big channels - ask the user how many if they didn't say):
   ```bash
   clawtube channel "<channel-url-or-@handle>" --limit 20 --langs <preferred-lang-codes> --out <output-dir>
   ```
5. Read back the resulting Markdown file(s) from `--out` (one `<title>_<video id>.md` per video; default directory `./clawtube_out`) and answer the user's actual question (summarize, quote, search within it, etc.) - don't just dump the raw file unless they asked for the raw transcript.
6. clawtube handles YouTube's pushback itself: it switches player clients on bot checks and waits out rate limits (a long pause in a playlist run is a cooldown, not a hang - don't kill it). If videos still fail with a bot-check message, tell the user and suggest `--cookies-from-browser chrome` (or `--cookies cookies.txt`); re-running later also works because runs are resumable.
7. If the command reports failed videos, tell the user which ones and why (the reason is printed for each: private, bot check, no captions and no `--whisper-fallback`, etc.) rather than silently ignoring them. Exit code 2 means the run finished but some videos failed.

## Notes

- Nothing is uploaded to a third-party API - captions come from YouTube directly or from local Whisper inference.
- Repeated requests for the same video are served from a local cache, so re-summarizing a video already fetched is instant.
