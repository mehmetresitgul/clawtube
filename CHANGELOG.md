# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `clawtube channel <url-or-@handle>` command, and `--limit N` for `playlist` and `channel`.
- `--cookies` and `--cookies-from-browser` to get past YouTube's bot check and age gates.
- `--version` flag.
- Clear up-front error when `--whisper-fallback` is used without `faster-whisper` or ffmpeg,
  and the reason is now shown when Whisper fails (audio download blocked, no speech, ...).
- Test suite (126 tests) for the fallback chain, yt-dlp retry/backoff, playlist orchestration
  (resume, cooldown retries, failure handling), output writers, state/cache, metadata, Whisper and
  the CLI. YouTube is fully mocked; no network is needed.
- Release workflow that builds the package and publishes to PyPI via trusted publishing when a
  `v*` tag is pushed, and a weekly live smoke test against YouTube.

### Changed
- Ready for public use: code of conduct, pull request template, feature-request template, a
  private route for security reports, Dependabot for GitHub Actions, and a "Responsible use"
  section in the README.
- README: install from GitHub with pipx/uv (not on PyPI yet), options table, correct cache location,
  and how to update so yt-dlp gets refreshed too. Clearer `clawtube --help` with examples.
- Requires Python 3.10+ and `yt-dlp[default]>=2025.11.12`. Older yt-dlp releases (the newest
  ones pip would pick on Python 3.9) no longer work with YouTube. The `[default]` extra brings
  yt-dlp's solver for YouTube's JavaScript challenge, and clawtube now enables any installed JS
  runtime (Deno, Node.js, Bun, QuickJS) instead of only Deno.
- Each video's YouTube page is loaded once instead of twice: the metadata comes from the same
  request that lists the caption tracks, which halves exposure to rate limits and bot checks.
- Without `--langs`, the yt-dlp tier picks the video's own language (manual captions first, then
  the original-language auto captions) instead of only looking for English.
- Playlist runs show a real progress total (and how many were already done when resuming), and
  start immediately instead of after a delay that grew with playlist size.

### Fixed
- Bot checks ("Sign in to confirm you're not a bot") on cloud/VPN IPs: clawtube now retries the
  video with other YouTube player clients (embedded player, then mobile web), which usually get
  through without cookies, and keeps using the one that worked for the rest of the run. From a
  cloud IP a 16-video playlist went from 3/16 to 16/16, and a full 155-video channel completed
  155/155 in under 4 minutes.
- A bot check was reported as "no captions". It now gets its own error with the
  `--cookies-from-browser` hint, and a playlist run stops after 3 bot checks in a row instead of
  making the same doomed request for every remaining video.
- Without `--langs`, a video with no language field could get a community translation (e.g.
  Arabic) instead of its original-language captions. Exact language codes now win over
  user-added tracks ("en" over "en-34kf3BCIwxA"), and such track ids are reported as plain "tr".
- Videos with dubbed audio tracks (e.g. a Turkish video with an English dub) kept the dub's
  language: the original is now taken from the track YouTube flags as original, or else from the
  title/description language.
- Without `--langs`, the timedtext tier took the first listed track, often a community translation
  (Arabic for an English video). It now picks the spoken language, preferring a human-made track.
- YouTube's soft rate limit (an empty caption file, or a redirect to its captcha page) was reported
  as "no captions"; it is now retried like a 429.
- Transcript cache bumped to v4 so transcripts picked by the old selection are refetched.
- A video with genuinely no captions on a network where the timedtext API is IP-blocked was
  treated as rate-limited and waited through ~7 minutes of cooldowns before failing.
- yt-dlp errors such as "Private video" or network failures are now shown as the reason instead
  of the generic "no captions" message; network errors are retried like rate limits.
- After the timedtext API reports an IP block, clawtube skips it for 10 minutes instead of
  spending a request on it for every video.
- `clawtube playlist` accepts a `watch?v=...&list=...` link and extracts the playlist.
- `youtube.com/shorts/...`, `/live/...` and `/embed/...` links are recognised as videos.
- yt-dlp subtitle parsing: YouTube's rolling auto-captions produced every line twice, leaked raw
  `<00:00:02.429><c>` timing tags into the transcript and dropped the first line of each cue.
  Transcripts are now clean, roughly half the size, and correctly timed.
- The on-disk transcript cache is versioned, so transcripts cached by older versions with the
  problems above are ignored instead of served again.
- A failed title/metadata lookup (bot check, rate limit) no longer discards an already-fetched
  transcript: metadata is retried, then replaced by placeholders.
- yt-dlp's own `ERROR:` lines no longer garble the progress display.
- Videos waiting for a rate-limit retry no longer advance the progress bar past 100%.
- Output file names never start with `-` or `.`, and use a neutral prefix when the title is unknown.
- Failing to list a playlist (private, missing) is reported instead of crashing with a traceback.

### Changed
- README install instructions now only list methods that work today; added Troubleshooting.
- The `clawtube-*` skills document the channel command and the cookie options.

## [0.1.0]

### Added
- `clawtube video` and `clawtube playlist` commands.
- Fallback chain: youtube-transcript-api, yt-dlp subtitles, optional local Whisper.
- Resumable playlist runs (`.clawtube_state.json`) and an on-disk transcript cache.
- Backoff and cooldown retries for YouTube rate limiting.
- Agent skills: `clawtube-full`, `clawtube-transcript`, `clawtube-playlist`.
