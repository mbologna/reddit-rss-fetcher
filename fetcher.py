#!/usr/bin/env python3
"""
Reddit RSS fetcher and subreddit archiver.

Runs on a configurable interval (default 12h), writes static files
to OUTPUT_DIR for nginx to serve directly.

Outputs:
  reddit-front-page.xml      — authenticated Reddit front page feed
  {subreddit}.xml            — top-of-the-week RSS feed per subreddit
  {subreddit}/{hash}.md      — archived post (pruned after ARCHIVE_DAYS days)
  last-run                   — UTC ISO timestamp of last successful cycle
"""

import glob
import hashlib
import logging
import os
import time
from datetime import datetime, timezone

import praw
import requests
from feedgen.feed import FeedGenerator
from pytz import timezone as pytz_timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/output")

# Front page
FEED_ID = os.environ.get("FEED_ID", "")
REDDIT_USER = os.environ.get("REDDIT_USER", "")

# Subreddit archiver
SUBREDDITS = [s.strip() for s in os.environ.get("SUBREDDITS", "").split(",") if s.strip()]
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.environ.get("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.environ.get("REDDIT_PASSWORD", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
ARCHIVE_DAYS = int(os.environ.get("ARCHIVE_DAYS", "30"))
TOP_PERIOD = os.environ.get("TOP_PERIOD", "week")
TOP_LIMIT = int(os.environ.get("TOP_LIMIT", "25"))

FETCH_INTERVAL_HOURS = int(os.environ.get("FETCH_INTERVAL_HOURS", "12"))

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/110.0"


def fetch_front_page() -> None:
    if not FEED_ID or not REDDIT_USER:
        log.warning("FEED_ID or REDDIT_USER not set, skipping front page")
        return
    url = f"https://www.reddit.com/.rss?feed={FEED_ID}&user={REDDIT_USER}&limit=10"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    out = os.path.join(OUTPUT_DIR, "reddit-front-page.xml")
    with open(out, "w", encoding="utf-8") as f:
        f.write(r.text)
    log.info("Wrote %s", out)


def build_reddit_client() -> praw.Reddit:
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent="python:reddit-rss-fetcher:v2.0",
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
    )


def fetch_subreddit(reddit: praw.Reddit, subreddit_name: str) -> None:
    localtz = pytz_timezone("Europe/Rome")
    archive_dir = os.path.join(OUTPUT_DIR, subreddit_name)
    os.makedirs(archive_dir, exist_ok=True)

    fg = FeedGenerator()
    fg.id(f"https://reddit.com/r/{subreddit_name}/")
    fg.title(subreddit_name)
    fg.description(f"r/{subreddit_name} — top posts of the {TOP_PERIOD}")
    fg.link(href=f"https://reddit.com/r/{subreddit_name}", rel="alternate")
    fg.language("en")

    for post in reddit.subreddit(subreddit_name).top(TOP_PERIOD, limit=TOP_LIMIT):
        url_hashed = hashlib.md5(post.url.encode("utf-8")).hexdigest()
        md_path = os.path.join(archive_dir, url_hashed + ".md")
        dt_utc = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
        created = dt_utc.strftime("%Y-%m-%d %H:%M:%S")

        md_content = (
            f"# {post.title}\n\n"
            f"**Date:** {created} UTC  \n"
            f"**Author:** u/{post.author}  \n"
            f"**URL:** [{post.url}]({post.url})\n\n"
            f"---\n\n"
            f"{post.selftext}\n"
        )
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        article_url = (
            f"{BASE_URL}/{subreddit_name}/{url_hashed}.md"
            if BASE_URL
            else f"file://{md_path}"
        )
        fe = fg.add_entry()
        fe.id(post.id)
        fe.title(post.title)
        fe.link(href=article_url)
        fe.content(post.selftext + "\n\n" + post.url)
        fe.pubDate(dt_utc.astimezone(localtz))

    out = os.path.join(OUTPUT_DIR, f"{subreddit_name}.xml")
    fg.rss_file(out)
    log.info("Wrote %s", out)

    for article in glob.glob(os.path.join(archive_dir, "*.md")):
        age = (datetime.now() - datetime.fromtimestamp(os.stat(article).st_ctime)).days
        if age >= ARCHIVE_DAYS:
            os.remove(article)
            log.info("Pruned %s", article)


def fetch_subreddits() -> None:
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD]):
        log.warning("PRAW credentials not set, skipping subreddit archiver")
        return
    if not SUBREDDITS:
        log.warning("SUBREDDITS not set, skipping subreddit archiver")
        return

    log.info("Archiving subreddits: %s", ", ".join(SUBREDDITS))
    reddit = build_reddit_client()
    for subreddit_name in SUBREDDITS:
        fetch_subreddit(reddit, subreddit_name)


def write_health() -> None:
    path = os.path.join(OUTPUT_DIR, "last-run")
    with open(path, "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())


def run_all() -> None:
    log.info("Starting fetch cycle")
    for fn in (fetch_front_page, fetch_subreddits):
        try:
            fn()
        except Exception:
            log.exception("Failed in %s", fn.__name__)
    write_health()
    log.info("Fetch cycle complete, sleeping %dh", FETCH_INTERVAL_HOURS)


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    while True:
        run_all()
        time.sleep(FETCH_INTERVAL_HOURS * 3600)
