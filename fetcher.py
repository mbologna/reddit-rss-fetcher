#!/usr/bin/env python3
"""
Reddit RSS fetcher and subreddit archiver.

Runs on a configurable interval (default 12h), writes static files
to OUTPUT_DIR for nginx to serve directly, or to a GCS bucket when
GCS_BUCKET is set (on Cloud Run, server.py calls run_all() on POST /fetch).

Outputs:
  reddit-front-page.xml      — your Reddit front page (hot), built from the API
  reddit-front-page          — extension-less copy (for Feedly)
  {subreddit}.xml            — top-of-the-week RSS feed per subreddit
  {subreddit}/{hash}.md      — archived post (pruned after ARCHIVE_DAYS days)
  last-run                   — UTC ISO timestamp of last successful cycle
"""

import glob
import hashlib
import html
import logging
import os
import time
from datetime import UTC, datetime, timedelta

import praw
import pyotp
from feedgen.feed import FeedGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/output")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

# Subreddit archiver
SUBREDDITS = [
    s.strip() for s in os.environ.get("SUBREDDITS", "").split(",") if s.strip()
]
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.environ.get("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.environ.get("REDDIT_PASSWORD", "")
REDDIT_TOTP_SECRET = os.environ.get("REDDIT_TOTP_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
ARCHIVE_DAYS = int(os.environ.get("ARCHIVE_DAYS", "30"))
TOP_PERIOD = os.environ.get("TOP_PERIOD", "week")
TOP_LIMIT = int(os.environ.get("TOP_LIMIT", "25"))

FRONT_PAGE_LIMIT = int(os.environ.get("FRONT_PAGE_LIMIT", "10"))

FETCH_INTERVAL_HOURS = int(os.environ.get("FETCH_INTERVAL_HOURS", "12"))

_gcs_client = None


def _gcs():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage

        _gcs_client = storage.Client()
    return _gcs_client


def write_file(path, content, content_type="application/xml"):
    """Write content to GCS or local filesystem depending on GCS_BUCKET."""
    if GCS_BUCKET:
        if isinstance(content, str):
            content = content.encode("utf-8")
        blob = _gcs().bucket(GCS_BUCKET).blob(path)
        blob.cache_control = "public, max-age=43200"
        blob.upload_from_string(content, content_type=content_type, timeout=60)
    else:
        out_path = os.path.join(OUTPUT_DIR, path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        if isinstance(content, bytes):
            with open(out_path, "wb") as f:
                f.write(content)
        else:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
    log.info("Wrote %s", path)


def fetch_front_page(reddit: praw.Reddit) -> None:
    """Write the account's front page as RSS.

    Reddit stopped serving RSS (including the private reddit.com/.rss?feed=
    front page) on 2026-11-13, so the feed is built from the API listing.
    """
    fg = FeedGenerator()
    fg.id("https://www.reddit.com/")
    fg.title("reddit: the front page")
    fg.description("Hot posts from your Reddit front page")
    fg.link(href="https://www.reddit.com/", rel="alternate")
    fg.language("en")
    fg.load_extension("dc")

    for post in reddit.front.hot(limit=FRONT_PAGE_LIMIT):
        permalink = f"https://www.reddit.com{post.permalink}"
        subreddit = f"r/{post.subreddit.display_name}"
        body = post.selftext_html or ""
        footer = (
            f"<p>{html.escape(subreddit)} "
            f'<a href="{html.escape(post.url)}">[link]</a> '
            f'<a href="{html.escape(permalink)}">[comments]</a></p>'
        )

        fe = fg.add_entry(order="append")
        fe.id(f"t3_{post.id}")
        fe.title(post.title)
        fe.link(href=permalink)
        # Feed readers show dc:creator as the byline: the subreddit says more
        # on a front page than the poster's name.
        fe.dc.dc_creator(subreddit)
        fe.description(body + footer)
        fe.pubDate(datetime.fromtimestamp(post.created_utc, tz=UTC))

    feed = fg.rss_str(pretty=True)
    write_file("reddit-front-page.xml", feed)
    write_file("reddit-front-page", feed)  # extension-less copy for Feedly


def build_reddit_client() -> praw.Reddit:
    password = REDDIT_PASSWORD
    if REDDIT_TOTP_SECRET:
        totp = pyotp.TOTP(REDDIT_TOTP_SECRET).now()
        password = f"{REDDIT_PASSWORD}:{totp}"
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent="python:reddit-rss-fetcher:v2.0",
        username=REDDIT_USERNAME,
        password=password,
    )


def fetch_subreddit(reddit: praw.Reddit, subreddit_name: str) -> None:
    fg = FeedGenerator()
    fg.id(f"https://reddit.com/r/{subreddit_name}/")
    fg.title(subreddit_name)
    fg.description(f"r/{subreddit_name} — top posts of the {TOP_PERIOD}")
    fg.link(href=f"https://reddit.com/r/{subreddit_name}", rel="alternate")
    fg.language("en")

    for post in reddit.subreddit(subreddit_name).top(
        time_filter=TOP_PERIOD, limit=TOP_LIMIT
    ):
        url_hashed = hashlib.md5(post.url.encode("utf-8")).hexdigest()
        md_rel_path = f"{subreddit_name}/{url_hashed}.md"
        dt_utc = datetime.fromtimestamp(post.created_utc, tz=UTC)
        created = dt_utc.strftime("%Y-%m-%d %H:%M:%S")

        author = post.author or "[deleted]"
        md_content = (
            f"# {post.title}\n\n"
            f"**Date:** {created} UTC  \n"
            f"**Author:** u/{author}  \n"
            f"**URL:** [{post.url}]({post.url})\n\n"
            f"---\n\n"
            f"{post.selftext}\n"
        )
        write_file(md_rel_path, md_content, "text/plain; charset=utf-8")

        article_url = (
            f"{BASE_URL}/{subreddit_name}/{url_hashed}.md"
            if BASE_URL
            else f"file://{os.path.join(OUTPUT_DIR, md_rel_path)}"
        )
        fe = fg.add_entry()
        fe.id(post.id)
        fe.title(post.title)
        fe.link(href=article_url)
        fe.content(post.selftext + "\n\n" + post.url)
        fe.pubDate(dt_utc)

    write_file(f"{subreddit_name}.xml", fg.rss_str(pretty=True))

    _prune_archives(subreddit_name)


def _prune_archives(subreddit_name: str) -> None:
    if GCS_BUCKET:
        cutoff = datetime.now(UTC) - timedelta(days=ARCHIVE_DAYS)
        for blob in _gcs().bucket(GCS_BUCKET).list_blobs(prefix=f"{subreddit_name}/"):
            if blob.time_created < cutoff:
                blob.delete()
                log.info("Pruned %s", blob.name)
    else:
        archive_dir = os.path.join(OUTPUT_DIR, subreddit_name)
        for article in glob.glob(os.path.join(archive_dir, "*.md")):
            age = (
                datetime.now(tz=UTC)
                - datetime.fromtimestamp(os.stat(article).st_ctime, tz=UTC)
            ).days
            if age >= ARCHIVE_DAYS:
                os.remove(article)
                log.info("Pruned %s", article)


def has_credentials() -> bool:
    return all(
        [REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD]
    )


def fetch_subreddits(reddit: praw.Reddit) -> None:
    if not SUBREDDITS:
        log.warning("SUBREDDITS not set, skipping subreddit archiver")
        return

    log.info("Archiving subreddits: %s", ", ".join(SUBREDDITS))
    for subreddit_name in SUBREDDITS:
        fetch_subreddit(reddit, subreddit_name)


def write_health() -> None:
    write_file(
        "last-run",
        datetime.now(UTC).isoformat(),
        "text/plain; charset=utf-8",
    )


def run_all() -> None:
    log.info("Starting fetch cycle")
    if not has_credentials():
        log.warning("PRAW credentials not set, skipping front page and archiver")
    else:
        # One client for both: it logs in once, with a single TOTP code.
        reddit = build_reddit_client()
        for fn in (fetch_front_page, fetch_subreddits):
            try:
                fn(reddit)
            except Exception:
                log.exception("Failed in %s", fn.__name__)
    write_health()
    log.info("Fetch cycle complete")


if __name__ == "__main__":
    if GCS_BUCKET:
        log.info("GCS_BUCKET=%s, running a single cycle", GCS_BUCKET)
        run_all()
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        while True:
            run_all()
            log.info("Sleeping %dh", FETCH_INTERVAL_HOURS)
            time.sleep(FETCH_INTERVAL_HOURS * 3600)
