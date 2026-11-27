# Contributing

Thanks for helping! Please follow the [code of conduct](CODE_OF_CONDUCT.md). The short version:

```bash
git clone https://github.com/mehmetresitgul/clawtube.git
cd clawtube
pip install -e ".[dev]"
pytest
```

- Tests must run offline. Mock YouTube (see `tests/test_transcript.py` and `tests/test_playlist.py`
  for the patterns) rather than calling it.
- When you fix a parsing bug, add a fixture copied from real `yt-dlp` / `youtube-transcript-api`
  output as a regression test.
- Keep CLI behaviour documented: update the README and `skills/*/SKILL.md` when you add or change
  a flag, and add a line to `CHANGELOG.md` under `[Unreleased]`.
- One change per pull request, with a short description of the problem it solves.

## Reporting a bug

Please include the exact command, `clawtube --version`, `pip show yt-dlp youtube-transcript-api`
versions, and the error text. If YouTube is blocking you ("Sign in to confirm you're not a bot",
HTTP 429/403), say whether `--cookies-from-browser` changes anything; that is usually your network,
not a bug.
