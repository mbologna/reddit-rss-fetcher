# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A self-hosted Reddit RSS fetcher and subreddit archiver that runs on a schedule, writes static files to disk, and lets nginx serve them directly. Deployed as a single Docker container on sierra (Hetzner VPS, Ansible-managed via `~/Code/infra`).

Replaces the previous PHP+Varnish stack. The cache is now implicit: nginx serves whatever was last written. The fetch interval controls freshness.

## Repository Structure

```
fetcher.py            — main script (scheduled fetcher loop)
Dockerfile            — builds the Python image
requirements.txt      — Python dependencies
docker-compose.yml    — local development only (writes to ./output/)
.gitlab-ci.yml        — CI: builds and pushes image to GitLab registry on every branch push
deploy/
  k8s/               — base Kustomize manifests for Kubernetes (hotel)
    kustomization.yaml
    reddit-rss-fetcher.yaml  — Namespace, Secret, PVC, Deployment, Service, Ingress
```

## Architecture

- **`fetcher.py`** — runs in a loop, sleeping `FETCH_INTERVAL_HOURS` (default 12h) between cycles:
  - Fetches the authenticated Reddit front page RSS → writes `reddit-front-page.xml`
  - For each subreddit in `SUBREDDITS`: fetches top posts via PRAW → writes `{subreddit}.xml` + `{subreddit}/{md5}.md` archives
  - Writes `last-run` (UTC ISO timestamp) after each cycle
- Each component is skipped with a warning if its credentials are missing
- **Output directory** — bind-mounted at `/opt/reddit-rss-fetcher/output` on sierra; nginx serves it as static files
- **No web server in the container** — the container only writes files; sierra's nginx handles all HTTP

## CI/CD

`.gitlab-ci.yml` builds and pushes to the GitLab registry on every branch push:

- `registry.gitlab.com/mbologna/reddit-rss-fetcher/main:latest` — from `main` branch
- `registry.gitlab.com/mbologna/reddit-rss-fetcher/main:<sha>` — pinned by commit

The infra's docker-compose pulls `registry.gitlab.com/mbologna/reddit-rss-fetcher/main` (no tag = `:latest`). Watchtower on sierra auto-updates the container when a new image is pushed.

## Local Development

```bash
cp .env.example .env   # fill in credentials
docker compose up --build
# feeds written to ./output/
```

## Deployment: Sierra (current)

Managed by Ansible playbook `ansible/playbooks/sierra.yaml` in `~/Code/infra`.
Secrets in `ansible/host_vars/sierra/vault.yaml` (ansible-vault).

### nginx (already deployed via infra)

```nginx
location /reddit-rss-fetcher/ {
    alias /opt/reddit-rss-fetcher/output/;
    default_type application/xml;
    try_files $uri $uri.xml =404;

    location ~* \.md$ {
        default_type text/plain;
    }
}
```

The `try_files` directive preserves old Feedly URLs — `/reddit-rss-fetcher/reddit-front-page` (no extension) resolves to `reddit-front-page.xml` automatically.

### Feed URLs

- `https://michelebologna.net/reddit-rss-fetcher/reddit-front-page` (Feedly URL, preserved)
- `https://michelebologna.net/reddit-rss-fetcher/{subreddit}.xml`
- `https://michelebologna.net/reddit-rss-fetcher/{subreddit}/{hash}.md`
- `https://michelebologna.net/reddit-rss-fetcher/last-run` (health check)

## Deployment: Kubernetes / hotel (future)

Base manifests are in `deploy/k8s/`. To deploy on hotel via ArgoCD:

1. Copy `deploy/k8s/` to `infra/apps/reddit-rss-fetcher/base/`
2. Create `infra/apps/reddit-rss-fetcher/overlays/hotel/kustomization.yaml` patching:
   - Ingress host (`CHANGE_ME_DOMAIN` → actual domain)
   - Secret values (replace with Sealed Secrets or ESO)
   - StorageClass if needed (Longhorn is default on hotel)
3. ArgoCD auto-discovers the overlay and deploys

The k8s architecture uses a **sidecar pattern**: fetcher and nginx in the same pod sharing a PVC. nginx serves the same `/reddit-rss-fetcher/` path with the same `try_files` logic — URLs are identical to sierra.

## Environment Variables

Defined in `.env` (not committed), referenced in `docker-compose.yml`:

| Variable | Required | Description |
|---|---|---|
| `FEED_ID` | for front-page | Reddit private RSS feed token |
| `REDDIT_USER` | for front-page | Reddit username for front page feed URL |
| `SUBREDDITS` | for archiver | Comma-separated list of subreddits to archive |
| `REDDIT_CLIENT_ID` | for archiver | PRAW OAuth client ID |
| `REDDIT_CLIENT_SECRET` | for archiver | PRAW OAuth client secret |
| `REDDIT_USERNAME` | for archiver | Reddit username for PRAW auth |
| `REDDIT_PASSWORD` | for archiver | Reddit password for PRAW auth |
| `BASE_URL` | for archiver | Public base URL for archived links (e.g. `https://michelebologna.net/reddit-rss-fetcher`) |
| `FETCH_INTERVAL_HOURS` | no | Fetch interval in hours (default: `12`) |
| `ARCHIVE_DAYS` | no | Days to keep archived markdown files (default: `30`) |
| `TOP_PERIOD` | no | Period for top posts: `hour`, `day`, `week`, `month`, `year`, `all` (default: `week`) |
| `TOP_LIMIT` | no | Number of top posts per subreddit (default: `25`) |

## What Was Removed

| Removed | Replaced by |
|---|---|
| `reddit-front-page.php` | `reddit-front-page.xml` written by fetcher |
| `reddit-subreddit-top.php` | removed — was a dynamic endpoint (`?subreddit=X&period=Y`); configure via `SUBREDDITS` env var instead |
| `time.php` | `last-run` file written after each cycle |
| `default.vcl` + Varnish container | nginx serves static files; fetch interval controls cache TTL |
| `deploy.sh` | Ansible handles deployment; CI handles image builds |
| `.htaccess` | no longer needed (no PHP/Apache) |
| `Dockerfile` (PHP 8 Apache) | replaced with `python:3.12-slim` |
