# Releasing

1. Update `version` in `pyproject.toml` and `__version__` in `src/clawtube/__init__.py`
   (they must match), and move the `[Unreleased]` notes in `CHANGELOG.md` under the new version.
2. Merge to `main` with CI green, then run the *Live smoke test* workflow (Actions tab, "Run workflow") to confirm it still works against the real YouTube.
3. Tag and push: `git tag v0.1.0 && git push origin v0.1.0`.
   The `Release` workflow tests, builds, checks the tag against the package version and publishes to PyPI.

## One-time PyPI setup

- Create the `clawtube` project on PyPI (or add a *pending publisher* before the first upload).
- Under the project's *Publishing* settings add a trusted publisher:
  owner `mehmetresitgul`, repo `clawtube`, workflow `release.yml`, environment `pypi`.
- Create a `pypi` environment in the GitHub repo settings (optionally require manual approval).

## Manual fallback

```bash
pip install build twine
python -m build
twine check --strict dist/*
twine upload dist/*
```
