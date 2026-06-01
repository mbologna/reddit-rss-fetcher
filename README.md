The file isn't in the repo, so I'll return the rewritten content directly.

---

# reddit-rss-fetcher

A self-hosted Reddit RSS fetcher and subreddit archiver. Runs on a configurable schedule, writes static XML/Markdown files to a GCS bucket (Cloud Run Job) or local disk (self-hosted), and serves them through a token-authenticated FastAPI proxy.

## Why this exists

Reddit provides a personal RSS feed (your front page at `reddit.com/.rss?feed=TOKEN&user=USERNAME`), but adding it directly to a feed reader like Feedly results in stale content. The feed can lag by hours or more, caused by a combination of Reddit throttling requests from aggregators and Feedly's own polling intervals (free plans refresh anywhere from every 30 minutes to once a day). The result is that your personalised Reddit feed in Feedly is often many hours behind.

This project works around that by self-hosting the fetch: a small Python process runs on a configurable schedule, pulls the feed, and writes static files. Your feed reader subscribes to your own URL, which always reflects the latest fetch with no third-party caching in the way.

## What it does

- **Front page feed:** fetches your authenticated Reddit front page RSS and writes `reddit-front-page.xml`
- **Subreddit archiver:** for each configured subreddit, fetches top posts via PRAW, writes `{subreddit}.xml` + `{subreddit}/{hash}.md` archive files (pruned after `ARCHIVE_DAYS` days)
- **Health check:** writes `last-run` (UTC ISO timestamp) after each cycle
- **Auth proxy** (`server.py`): FastAPI service that reads files from GCS and requires a `?token=` query parameter on feed endpoints; `/last-run` is public

## Quick start

```bash
cp .env.example .env   # fill in credentials
docker compose up --build
# feeds written to ./output/
```

## Environment variables

### Fetcher (`fetcher.py`)

| Variable | Required | Description |
|---|---|---|
| `FEED_ID` | for front-page | Reddit private RSS feed token |
| `REDDIT_USER` | for front-page | Reddit username for front page feed URL |
| `SUBREDDITS` | for archiver | Comma-separated list of subreddits to archive |
| `REDDIT_CLIENT_ID` | for archiver | PRAW OAuth client ID |
| `REDDIT_CLIENT_SECRET` | for archiver | PRAW OAuth client secret |
| `REDDIT_USERNAME` | for archiver | Reddit username for PRAW auth |
| `REDDIT_PASSWORD` | for archiver | Reddit password for PRAW auth |
| `BASE_URL` | for archiver | Public base URL for archived post links |
| `GCS_BUCKET` | for GCS mode | Bucket name (enables Cloud Run Job mode: single cycle + exit) |
| `OUTPUT_DIR` | for local mode | Output directory when GCS_BUCKET is not set (default: `/output`) |
| `FETCH_INTERVAL_HOURS` | no | Fetch interval in hours (default: `12`, local mode only) |
| `ARCHIVE_DAYS` | no | Days to keep archived markdown files (default: `30`) |
| `TOP_PERIOD` | no | Period for top posts: `hour`, `day`, `week`, `month`, `year`, `all` (default: `week`) |
| `TOP_LIMIT` | no | Number of top posts per subreddit (default: `25`) |

To get a Reddit OAuth client ID and secret, create a "script" app at <https://www.reddit.com/prefs/apps>.

### Server (`server.py`)

| Variable | Required | Description |
|---|---|---|
| `GCS_BUCKET` | yes | GCS bucket containing the fetched files |
| `SERVE_TOKEN` | yes | Token required on `?token=` query parameter for feed endpoints |

Set `MODE=server` to start `server.py` instead of `fetcher.py` (the `run.sh` entrypoint reads this).

## Output structure

```
output/
  reddit-front-page.xml       — authenticated front page feed
  reddit-front-page           — extension-less copy (for Feedly)
  {subreddit}.xml             — top posts RSS feed
  {subreddit}/{hash}.md       — archived post (title, author, URL, selftext)
  last-run                    — UTC timestamp of last successful cycle
```

## Serving

### Cloud Run (GCP)

The fetcher runs as a Cloud Run Job (triggered by Cloud Scheduler every 12 hours) and writes to a private GCS bucket. A companion Cloud Run Service (`server.py`, `MODE=server`) reads from that bucket and serves files with token auth:

```
GET /last-run                           — 200, no auth (health check)
GET /reddit-front-page?token=TOKEN      — 200, XML feed
GET /reddit-front-page.xml?token=TOKEN  — same
GET /reddit-front-page                  — 401 (missing token)
```

Infrastructure: `../infra/terraform/reddit-rss-fetcher/`

### Self-hosted (nginx)

Run the fetcher locally and serve `OUTPUT_DIR` with nginx:

```nginx
server {
    server_name your-domain.example.com;
    root /path/to/output;

    location / {
        default_type application/xml;
        try_files $uri $uri.xml =404;

        location ~* \.md$ {
            default_type text/plain;
        }
    }
}
```

## Docker image

Pre-built images are published to the GitHub Container Registry on every push to `main`:

```
ghcr.io/mbologna/reddit-rss-fetcher:latest
ghcr.io/mbologna/reddit-rss-fetcher:<sha>
```

The entrypoint (`run.sh`) selects the mode based on `MODE`:
- `MODE=server` → `uvicorn server:app --host 0.0.0.0 --port 8080`
- anything else → `python -u fetcher.py`

## Kubernetes

Base Kustomize manifests are in `deploy/k8s/`. The deployment uses a **sidecar pattern**: fetcher and nginx in the same pod sharing a PVC. See the manifests for details.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

Linting: `ruff check . && ruff format --check .`