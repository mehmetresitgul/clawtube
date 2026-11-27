# Security policy

clawtube runs locally and sends nothing to third-party services besides YouTube itself.

- If you use `--cookies` or `--cookies-from-browser`, the cookies are passed to yt-dlp only and are
  never written to the output directory, cache or state file. Treat a `cookies.txt` like a password.
- Security fixes go into the latest version on `main` (and the latest release, once published).
- Please report vulnerabilities privately through GitHub's
  ["Report a vulnerability"](https://github.com/mehmetresitgul/clawtube/security/advisories/new)
  form instead of a public issue.
