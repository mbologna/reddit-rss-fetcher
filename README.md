# reddit-rss-fetcher

[![CI](https://github.com/mbologna/reddit-rss-fetcher/actions/workflows/ci.yml/badge.svg)](https://github.com/mbologna/reddit-rss-fetcher/actions/workflows/ci.yml)
[MIT licensed](LICENSE).

reddit-rss-fetcher builds [RSS](https://en.wikipedia.org/wiki/RSS) feeds of your [Reddit](https://www.reddit.com/) front page and of chosen subreddits, and archives each subreddit post as a [Markdown](https://en.wikipedia.org/wiki/Markdown) file. In production it writes the files to a [Google Cloud Storage](https://cloud.google.com/storage) bucket and serves them from a [FastAPI](https://fastapi.tiangolo.com/) service on [Cloud Run](https://cloud.google.com/run) that requires a token. Without a bucket it writes to a local directory, for development.

## Why it exists

Reddit used to publish a private RSS feed of each user's front page (`reddit.com/.rss?feed=TOKEN&user=USERNAME`). Added to a feed reader such as [Feedly](https://feedly.com/), it lagged by hours, between Reddit throttling aggregators and Feedly's own polling interval. On 2026-11-13 Reddit stopped serving RSS altogether.

This project reads the front page and subreddits through the Reddit API with [PRAW](https://praw.readthedocs.io/) and writes static RSS files, so the feed reader subscribes to a URL you control. Reddit is also closing its public Data API: it has refused new apps since 2026-10-31 and shuts off the rest by March 2027. The project stops working then.

## What it writes

- `reddit-front-page.xml`, posts from the account's front page, with the subreddit as each item's byline: one new post a subreddit a day by default, each kept for `FRONT_PAGE_KEEP_HOURS` (48 by default), up to 150 items, dated by when it entered the feed. [The front page feed](#the-front-page-feed) has the rules. `front-page-state.json` records those posts between runs.
- `{subreddit}.xml` for each subreddit in `SUBREDDITS`: its top posts for `TOP_PERIOD`. Each item links to the Reddit thread and to an `archived copy`, `{subreddit}/{hash}.md`, holding the title, date, author, URL and self text. Archived files older than `ARCHIVE_DAYS` are deleted.
- Each item shows the post's images above its text: a gallery's first 20 images in order, a video's preview frame with a link to the video, or a link's preview image. Images use Reddit's rendition at most 1,080 px wide, and the first is also the item's `media:thumbnail`.
- Items are laid out as [feed-refinery](https://github.com/mbologna/feed-refinery)'s, so every feed reads alike: the images, the post's text cut to about 800 characters (at its last whole paragraph or sentence), a line with the link labelled with its site and the reading time, as `example.com · ●●○ 6 min read`, the top comment as Reddit ranks them (past stickied, deleted or removed ones) cut to about 300 characters, with a link to it, then `12 comments · 340 points · r/python` (with `archived copy` in subreddit feeds), the comments linking to the thread. The dots tell the length at a glance: `●○○` up to 3 minutes, `●●○` up to 7, `●●●` longer.
- A link post's reading time is the linked page's: the words of its first `<article>` or `<main>` with paragraphs (else of all its paragraphs) at 230 a minute, left out under 200 words, for a page that isn't HTML and when it fails to load 3 times. Links to Reddit, images and videos have nothing to read, so they get none. Each page is read once, at most 40 new ones a cycle; `reading-times.json` keeps the counts for as long as a feed lists the link. A self post's reading time is its whole text's, left out when the item shows 90% or more of it.
- `last-run`, a UTC ISO 8601 timestamp, written only after a cycle in which no step failed. A failed step does not stop the others.

```
output/
  reddit-front-page.xml      front page feed
  reddit-front-page          extension-less copy, for Feedly
  front-page-state.json      posts in the front page feed and when each first appeared
  reading-times.json         word counts of the pages link posts point to
  {subreddit}.xml            top posts feed
  {subreddit}/{hash}.md      archived post
  last-run                   time of the last cycle with no failed step
```

## The front page feed

Each fetch reads the top `FRONT_PAGE_LIMIT` (25 by default) hot posts of the front page. A feed reader shows each post once, however long the feed keeps it, so the number of posts the feed admits sets how many items a day arrive; `FRONT_PAGE_KEEP_HOURS` does not.

Of the posts it reads, the feed admits a subreddit's highest-ranked new post, then no other new post from that subreddit until a fetch at least 23 hours later. The window is an hour short of a day so that the fetch a day later counts even when it starts a few seconds early. `FRONT_PAGE_PER_SUBREDDIT` sets how many new posts a subreddit gets in that window; `0` admits every post. A post left out is not stored, so a later fetch can still admit it. An admitted post stays for `FRONT_PAGE_KEEP_HOURS` after it entered, or for as long as it is on the hot list, so a reader that was offline between two fetches still gets posts that came and went.

Big subreddits enter the hot list near its top and niche ones lower down, so reading fewer posts drops the niche subreddits first. One account's front page over 48 hours (2026-11-22 to 24), with the top 25 read every two hours:

| Rule | Items a day | Subreddits (of 46) | Share of the three biggest |
|---|---|---|---|
| Top 25, every post | 74 | 46 | 17% |
| Top 10, every post | 30 | 21 | 42% |
| Top 25, one a subreddit a day | 34 | 46 | 9% |
| Top 25, two a subreddit a day | 55 | 46 | 11% |

The first row is counted from `front-page-state.json`. The others filter the same posts by the rank at which each entered the list, so the top-10 row is a lower bound. Across fetches the cap admits whichever post comes first: in 26 of the 68 slots, a later post from the same subreddit entered the list higher than the one admitted. Admitting those as well would have brought 48 items a day.

Each fetch logs how many posts it listed, admitted and left out, and how many the feed holds, then the listing itself as JSON: `[post ID, subreddit, rank, 1 if in the feed]` for each post. Replayed from the logs, the listings give each post's rank over time, including the posts the cap left out.

## Quick start

```bash
cp .env.example .env   # fill in the Reddit credentials and SUBREDDITS
docker compose up --build
# feeds appear in ./output/
```

[Docker Compose](https://docs.docker.com/compose/) builds the image and runs the fetcher in local mode, writing to `./output` every `FETCH_INTERVAL_HOURS`. `docker-compose.yml` passes every fetcher variable in `.env` to the container except `GCS_BUCKET` (Compose runs local mode) and `OUTPUT_DIR` (fixed at `/output`, where `./output` is mounted). An unset or empty optional variable takes its default. Nothing serves the files in local mode, and archived-copy links are `file://` paths unless `BASE_URL` is set.

The container runs as uid `1000`. If `./output` already exists with another owner, run `chown -R 1000:1000 output` (or delete it and let Docker create it) before starting.

To get a client ID and secret, create a "script" app at <https://www.reddit.com/prefs/apps>.

## Commands

The package installs three console scripts:

- `reddit-rss-fetcher`, the image's command: `MODE=server` runs `reddit-rss-server`, anything else runs `reddit-rss-fetch`.
- `reddit-rss-fetch`: with `GCS_BUCKET` set, runs one cycle and exits; without it, writes to `OUTPUT_DIR` and repeats every `FETCH_INTERVAL_HOURS`.
- `reddit-rss-server`: the FastAPI service, run by [Uvicorn](https://uvicorn.dev/) on `0.0.0.0`, port `$PORT` (Cloud Run sets it; default `8080`).

`/app/run.sh`, the image's command before the code moved to `src/reddit_rss_fetcher/`, still works and runs `reddit-rss-fetcher`.

## Configuration

### Fetcher

| Variable | Required | Description |
|---|---|---|
| `REDDIT_CLIENT_ID` | yes | OAuth client ID of the Reddit script app |
| `REDDIT_CLIENT_SECRET` | yes | OAuth client secret |
| `REDDIT_USERNAME` | yes | Reddit account; its front page is the feed |
| `REDDIT_PASSWORD` | yes | Password of that account |
| `REDDIT_TOTP_SECRET` | no | TOTP secret (not a one-time code) if the account has two-factor authentication; the fetcher appends the current code to the password |
| `SUBREDDITS` | for the archiver | Comma-separated subreddits to archive; unset, the archiver is skipped |
| `BASE_URL` | for the archiver | Public base URL of the server, used in archived-copy links |
| `SERVE_TOKEN` | with the server | Appended to archived-copy links, because the server serves the archive only with the token |
| `GCS_BUCKET` | for GCS mode | Bucket to write to. Set, `reddit-rss-fetch` runs one cycle and exits |
| `OUTPUT_DIR` | no | Output directory without `GCS_BUCKET` (default `/output`) |
| `FETCH_INTERVAL_HOURS` | no | Hours between cycles without `GCS_BUCKET` (default `12`) |
| `ARCHIVE_DAYS` | no | Days to keep archived Markdown files (default `30`) |
| `TOP_PERIOD` | no | Period for top posts: `hour`, `day`, `week`, `month`, `year` or `all` (default `week`) |
| `TOP_LIMIT` | no | Top posts per subreddit (default `25`) |
| `FRONT_PAGE_LIMIT` | no | Hot posts read from the front page on each fetch (default `25`) |
| `FRONT_PAGE_PER_SUBREDDIT` | no | New front-page posts admitted per subreddit a day (default `1`; `0` admits every post) |
| `FRONT_PAGE_KEEP_HOURS` | no | Hours a front-page post stays in the feed after it first appeared (default `48`); a post still on the hot list always stays |

### Server

| Variable | Required | Description |
|---|---|---|
| `MODE` | for the image's command | `server` makes `reddit-rss-fetcher` start the server |
| `GCS_BUCKET` | yes | Bucket holding the fetched files |
| `SERVE_TOKEN` | yes | Token required in the `?token=` query parameter of every file |
| `FETCH_TOKEN` | for `/fetch` | Token required by `POST /fetch`. It is separate from `SERVE_TOKEN`, so whatever triggers fetches cannot read the feeds; unset, `/fetch` answers 503 |
| `STALE_AFTER_HOURS` | no | Age of `last-run` at which `/health` answers 503 (default `7`: three missed two-hour cycles) |
| `PORT` | no | Listening port (default `8080`) |

`POST /fetch` runs the fetcher in the server process, so the server also needs the fetcher's variables.

## The server

A single Cloud Run service with `MODE=server` serves the feeds and runs the fetches:

```
POST /fetch                                run a fetch cycle (body: {"token": "FETCH_TOKEN"})
GET  /health                               no token; 200, or 503 when last-run is stale or missing
GET  /reddit-front-page?token=TOKEN        the front page feed
GET  /reddit-front-page.xml?token=TOKEN    the same
GET  /{subreddit}/{hash}.md?token=TOKEN    an archived post
GET  /reddit-front-page                    401, no token
```

Any path is read from the bucket, and a path whose last segment has no extension gets `.xml` appended. A missing file answers 404. Without the token every path but `/health` answers 401, so a stranger learns nothing about which paths exist; the service publishes no API docs or OpenAPI schema.

Every response carries `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and a `Content-Security-Policy` that lets nothing run or frame the files. Files are `Cache-Control: private, no-cache`, because their URL holds the token; errors and `/health` are `no-store`.

`POST /fetch` runs a full cycle synchronously. It answers 200 when every step succeeded, 500 when one failed, 409 when a cycle is already running and 401 for a wrong token. Any scheduler that can send a POST can trigger it; mine is a [Cloudflare Workers Cron Trigger](https://developers.cloudflare.com/workers/configuration/cron-triggers/) every two hours.

`/health` answers `GET` and `HEAD` with `{"status":"ok","last_run":"…"}`, or with 503 and `{"status":"down","problems":[…]}` once `last-run` is older than `STALE_AFTER_HOURS` or absent.

The Cloud Run service, its bucket and IAM are managed with [OpenTofu](https://opentofu.org/) in a separate, private repository.

## Docker image

CI publishes the image to the GitHub Container Registry for `linux/amd64` and `linux/arm64` on every push to `main` that changes it:

```
ghcr.io/mbologna/reddit-rss-fetcher:latest
ghcr.io/mbologna/reddit-rss-fetcher:sha-<commit>
```

The same run deploys that image to the Cloud Run service by its digest; the `sha-<commit>` tags of the last five versions are the rollback window. Images carry only those two tags and no version number, and there is no copy on Docker Hub.

The image runs as uid `1000`, has no pip, and writes only to `/tmp` and `/output`, so its root filesystem can be read-only.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pre-commit install
```

The project uses [uv](https://docs.astral.sh/uv/), [Ruff](https://docs.astral.sh/ruff/) and [pytest](https://docs.pytest.org/); tests mock PRAW and the bucket. CI fails when coverage drops below `fail_under` in `pyproject.toml`, now 99%. The [pre-commit](https://pre-commit.com/) hooks also run [mypy](https://mypy-lang.org/), [Bandit](https://bandit.readthedocs.io/) and [hadolint](https://github.com/hadolint/hadolint).

Dependencies resolve only to releases at least 7 days old (`exclude-newer` in `pyproject.toml`), and the image installs exactly what `uv.lock` pins. To take a CVE fix released less than 7 days ago, add a temporary `exclude-newer-package = { <package> = false }` override under `[tool.uv]`, run `uv lock --upgrade-package <package>`, and drop the override once the release is a week old.

The project skeleton comes from the [Copier](https://copier.readthedocs.io/) template [python-app-template](https://github.com/mbologna/python-app-template), recorded in `.copier-answers.yml`. [Renovate](https://docs.renovatebot.com/) opens a pull request for each template release; `uvx copier update` applies one by hand.

## Why not an existing tool

- Reddit itself no longer serves `.rss` feeds: they answer 404.
- [RSSHub](https://docs.rsshub.app/), which builds feeds for sites without one, has no Reddit route.
- [RSS-Bridge](https://github.com/RSS-Bridge/rss-bridge)'s RedditBridge uses app-only OAuth, so it can't read the front page as the logged-in user. It also has no galleries and no archive.
- [Redlib](https://github.com/redlib-org/redlib), an alternative Reddit front end, works by impersonating the official app, against Reddit's terms.
- Every option built on the API, this one included, ends when Reddit's Data API closes, by March 2027.

Checked 2026-11-23.

## Versioning

`main` is what runs: each push to it that changes the image deploys it to Cloud Run ([Docker image](#docker-image)). Releases follow SemVer as a changelog: every Monday at 06:00 UTC [auto-release](https://github.com/mbologna/auto-release) tags `vX.Y.Z` from the [Conventional Commit](https://www.conventionalcommits.org/) types merged since the last tag (`feat` minor; `fix`, `perf`, `revert` patch; a breaking change major), or cuts a maintenance release for a week of only dependency updates. A push to `main` also tags a release at once when it fixes a CVE: a `fix(security)` commit, or a Dockerfile base image update that drops fixable HIGH or CRITICAL findings. The tag builds no image and deploys nothing; its [GitHub Release](https://github.com/mbologna/reddit-rss-fetcher/releases) lists the changes. See the [versioning standard](https://github.com/mbologna/.github/blob/main/VERSIONING.md).
