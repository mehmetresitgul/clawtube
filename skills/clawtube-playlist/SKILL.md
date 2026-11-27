---
name: clawtube-playlist
description: Extract transcripts for every video in a YouTube playlist, locally, no API key. Use when the user pastes a playlist link and wants transcripts, summaries, or research across the whole playlist.
---

# clawtube-playlist

Use the `clawtube` CLI to bulk-extract transcripts for an entire playlist. If it is not on PATH, install it: `pipx install clawtube` (or `pip install clawtube`; for the local-transcription fallback for videos with no captions at all, `pipx install "clawtube[whisper]"`).

```bash
clawtube playlist "<playlist-url-or-id>" --langs <preferred-lang-codes> --out <output-dir> --concurrency 4
```

- Add `--whisper-fallback` so videos with no caption track are transcribed locally instead of skipped.
- For a whole channel use `clawtube channel "<channel-url-or-@handle>" --limit <N>` instead (newest videos first).
- clawtube gets past most bot checks and waits out rate limits on its own; a long pause mid-run is a cooldown, not a hang. If videos still fail with a bot-check message, add `--cookies-from-browser chrome` (or `--cookies cookies.txt`), or re-run later - runs are resumable.
- Leave out `--langs` to get each video's own language.
- Add `--formats md,srt` for SRT export alongside Markdown.
- The run is resumable - if it's interrupted, re-run the exact same command with the same `--out` directory and only the unfinished videos will be (re)fetched.
- After it finishes, report any failed videos and why (private, age-restricted, no captions available) instead of silently dropping them.
- Read the per-video Markdown files (`<title>_<video id>.md` in `--out`) to answer the user's actual question - summarizing across the playlist, searching for a topic, comparing videos, etc.
