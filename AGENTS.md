# AGENTS.md — reddit-rss-fetcher

Self-hosted Reddit RSS fetcher and subreddit archiver: authenticated feeds and Markdown exports behind a token-protected FastAPI proxy, on Cloud Run or Kubernetes. User-facing docs: `README.md`.

## Layout

- `src/reddit_rss_fetcher/__init__.py`: `main()` is the console script
  `reddit-rss-fetcher`, the image's command: `MODE=server` starts the
  server, anything else the fetcher.
- `src/reddit_rss_fetcher/fetcher.py`: the front-page feed and the
  subreddit archiver; console script `reddit-rss-fetch` (one cycle with
  `GCS_BUCKET`, a loop writing to `OUTPUT_DIR` without).
- `src/reddit_rss_fetcher/server.py`: the FastAPI web service (feeds behind
  a token, `/health`, `POST /fetch`); console script `reddit-rss-server`,
  uvicorn on `$PORT` (Cloud Run sets it), default 8080. It reads
  `GCS_BUCKET` and `SERVE_TOKEN` at import.
- `compat/run.sh`: the image's former command, `/app/run.sh`, kept for
  deployments that still name it; remove when none does.
- `tests/`: pytest; Reddit (PRAW) and GCS are mocked.
- `Dockerfile`: two stages, `uv sync --locked` into `/opt/venv`, then a
  runtime without pip.
- `.github/workflows/ci.yml`: every image built on main is pushed and
  deployed to the Cloud Run service (`deploy_type: service`).

## Conventions

- Conventional Commits, header at most 72 characters. CI lints every commit
  with [commitlint-config-mbologna](https://github.com/mbologna/commitlint-config)
  and rejects `fixup!` commits; pull requests merge with a merge commit.
- No releases or version tags: every push to main that changes the image
  deploys it as `sha-<commit>`. `pyproject.toml`'s version stays `0.0.0`.
- Coverage may not drop below `fail_under` in `pyproject.toml`; raise it
  when tests are added.
- Dependencies: `uv add <pkg>`; uv.lock is committed and the image installs
  it with `--locked`. `exclude-newer` keeps releases younger than 7 days out.
- The image: non-root (uid 1000), no pip, nothing written outside `/tmp`
  or `/output` (local mode, a mounted volume).

## Template

The skeleton comes from https://github.com/mbologna/python-app-template
(`.copier-answers.yml`). Renovate's copier manager opens a pull request
for each template release; resolve conflicts in favour of this project's
code. By hand: `uvx copier update`, then `uv lock` if dependencies changed.
