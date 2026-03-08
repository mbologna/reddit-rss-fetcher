#!/usr/bin/env python3
"""
Reddit RSS fetcher.

Runs on a configurable interval (default 12h), writes static XML files
to OUTPUT_DIR for nginx to serve directly. Replaces the PHP+Varnish stack.

Outputs:
  reddit-front-page.xml   — authenticated Reddit front page feed
  last-run                — UTC ISO timestamp of last successful cycle
"""

import logging
import os
import time
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/output")
FEED_ID = os.environ.get("FEED_ID", "")
REDDIT_USER = os.environ.get("REDDIT_USER", "")
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


def write_health() -> None:
    path = os.path.join(OUTPUT_DIR, "last-run")
    with open(path, "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())


def run_all() -> None:
    log.info("Starting fetch cycle")
    for fn in (fetch_front_page,):
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
