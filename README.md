# reddit-rss-fetcher

A self-hosted Reddit RSS fetcher and subreddit archiver. Runs on a configurable schedule, writes static XML/Markdown files to disk, and lets nginx serve them directly — no database, no web framework.

## What it does

- **Front page feed** — fetches your authenticated Reddit front page RSS and writes `reddit-front-page.xml`
- **Subreddit archiver** — for each configured subreddit, fetches top posts via PRAW, writes `{subreddit}.xml` + `{subreddit}/{hash}.md` archive files (pruned after `ARCHIVE_DAYS` days)
- **Health check** — writes `last-run` (UTC ISO timestamp) after each cycle

Each component is skipped with a warning if its credentials are not provided, so you can use either or both features.

## Quick start

```bash
cp .env.example .env   # fill in credentials
docker compose up --build
# feeds written to ./output/
```

## Environment variables

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
| `FETCH_INTERVAL_HOURS` | no | Fetch interval in hours (default: `12`) |
| `ARCHIVE_DAYS` | no | Days to keep archived markdown files (default: `30`) |
| `TOP_PERIOD` | no | Period for top posts: `hour`, `day`, `week`, `month`, `year`, `all` (default: `week`) |
| `TOP_LIMIT` | no | Number of top posts per subreddit (default: `25`) |

To get a Reddit OAuth client ID and secret, create a "script" app at <https://www.reddit.com/prefs/apps>.

## Output structure

```
output/
  reddit-front-page.xml       — authenticated front page feed
  {subreddit}.xml             — top posts RSS feed
  {subreddit}/{hash}.md       — archived post (title, author, URL, selftext)
  last-run                    — UTC timestamp of last successful cycle
```

## Serving with nginx

The container only writes files. Serve the output directory with nginx:

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

The `try_files` directive allows extension-less URLs — `/reddit-front-page` resolves to `reddit-front-page.xml` automatically.

## Docker image

Pre-built images are published to the GitHub Container Registry on every push to `main`:

```
ghcr.io/mbologna/reddit-rss-fetcher:latest
ghcr.io/mbologna/reddit-rss-fetcher:<sha>
```

## Kubernetes

Base Kustomize manifests are in `deploy/k8s/`. The deployment uses a **sidecar pattern**: fetcher and nginx in the same pod sharing a PVC. See the manifests for details.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

Linting: `ruff check . && ruff format --check .`
