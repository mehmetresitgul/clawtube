---
name: clawtube-transcript
description: Get a single YouTube video's transcript with timestamps, locally, no API key. Use when the user pastes one YouTube video link and wants it summarized, transcribed, or quoted.
---

# clawtube-transcript

Use the `clawtube` CLI to fetch a single video's transcript. If it is not on PATH, install it: `pipx install clawtube` (or `pip install clawtube`; for the local-transcription fallback for videos with no captions at all, `pipx install "clawtube[whisper]"`).

```bash
clawtube video "<video-url-or-id>" --out <output-dir>
```

Add `--langs <codes>` (e.g. `--langs en` or `--langs tr,en`) only if the user wants a specific language; by default you get the video's own language. The command prints the path of the Markdown file it wrote.

If it reports a bot check, suggest `--cookies-from-browser chrome`. If it reports no captions found, retry with `--whisper-fallback` to transcribe the audio locally instead of giving up. Read the resulting Markdown file to answer the user's actual question rather than just printing the raw transcript, unless they asked for the raw text.
