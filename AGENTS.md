# AGENTS.md — reddit-rss-fetcher

Self-hosted Reddit RSS fetcher and subreddit archiver: authenticated feeds and Markdown exports behind a token-protected FastAPI proxy, on Cloud Run or Kubernetes. User-facing docs: `README.md`.

## Layout

- `src/reddit_rss_fetcher/__init__.py`: `main()` is the console script
  `reddit-rss-fetcher`, the image's command: `MODE=server` starts the
  server, anything else the fetcher.
- `src/reddit_rss_fetcher/fetcher.py`: the front-page feed and the
  subreddit archiver; console script `reddit-rss-fetch` (one cycle with
  `GCS_BUCKET`, a loop writing to `OUTPUT_DIR` without).
- `src/reddit_rss_fetcher/reading.py`: link posts' reading times: reads
  each linked page once, counts its article's words, and keeps the counts
  in `reading-times.json`; also the dots before a reading time.
- `src/reddit_rss_fetcher/text.py`: plain text and excerpts out of a post's
  HTML, a copy of feed-refinery's `text.py` rules so items read like its
  items: change both together.
- `src/reddit_rss_fetcher/server.py`: the FastAPI web service (feeds behind
  a token, `/health`, `POST /fetch`); console script `reddit-rss-server`,
  uvicorn on `$PORT` (Cloud Run sets it), default 8080. It reads
  `GCS_BUCKET` and `SERVE_TOKEN` at import.
- `compat/run.sh`: the image's former command, `/app/run.sh`, kept for
  deployments that still name it; remove when none does.
- `tests/`: pytest; Reddit (PRAW) and GCS are mocked, and the `pages`
  fixture (`conftest.py`) serves linked pages: tests never touch the network.
- `Dockerfile`: two stages, `uv sync --locked` into `/opt/venv`, then a
  runtime without pip.
- `.github/workflows/ci.yml`: every image built on main is pushed and
  deployed to the Cloud Run service (`deploy_type: service`); the
  `auto-release` job tags the weekly changelog release.

## Conventions

- Conventional Commits, header at most 72 characters. The commit subjects
  that reach main are the changelog: `feat`/`fix`/`perf` release, `refactor`,
  `test`, `docs`, `ci`, `chore` don't (a dependency update to runtime files
  goes into the weekly maintenance release). CI lints every commit with
  [commitlint-config-mbologna](https://github.com/mbologna/commitlint-config)
  and rejects `fixup!` commits; pull requests merge with a merge commit.
- Every push to main that changes the image publishes it as `latest` and
  `sha-<commit>` and deploys it, by digest, to the Cloud Run service. Images
  carry no version.
- Coverage may not drop below `fail_under` in `pyproject.toml`; raise it
  when tests are added.
- Dependencies: `uv add <pkg>`; uv.lock is committed and the image installs
  it with `--locked`. `exclude-newer` keeps releases younger than 7 days out.
- The image: non-root (uid 1000), no pip, nothing written outside `/tmp`
  or `/output` (local mode, a mounted volume).

## Versioning

SemVer with changelog-only releases: `vX.Y.Z` tags, each with a GitHub
Release of the commit subjects, per the
[versioning standard](https://github.com/mbologna/.github/blob/main/VERSIONING.md).

- A tag builds no image and deploys nothing: `main` is what runs on Cloud
  Run.
- Never create, move or delete tags, and never edit a version number to
  cut a release: auto-release tags from the commit types (`feat` minor;
  `fix`, `perf`, `revert` patch; `!` or a `BREAKING CHANGE:` footer major)
  every Monday at 06:00 UTC, and cuts a maintenance release when only
  dependency updates landed.
- Write Conventional Commit subjects; they decide the next version.
- `version` in `pyproject.toml` is a placeholder that stays `0.0.0`.

## Template

The skeleton comes from https://github.com/mbologna/python-app-template
(`.copier-answers.yml`). Renovate's copier manager opens a pull request
for each template release; resolve conflicts in favour of this project's
code. By hand: `uvx copier update`, then `uv lock` if dependencies changed.
