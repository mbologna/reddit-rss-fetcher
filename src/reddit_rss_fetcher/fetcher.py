"""
Reddit RSS fetcher and subreddit archiver.

Writes static files to a GCS bucket when GCS_BUCKET is set (on Cloud Run,
server.py calls run_all() on POST /fetch), otherwise to OUTPUT_DIR on a
configurable interval (default 12h; local development).

Outputs:
  reddit-front-page.xml      — your Reddit front page (hot), built from the API:
                               one new post a subreddit a day, each kept 48 h
  reddit-front-page          — extension-less copy (for Feedly)
  front-page-state.json      — those posts and when each entered the feed
  reading-times.json         — the word counts of the pages link posts point to
  {subreddit}.xml            — top-of-the-week RSS feed per subreddit
  {subreddit}/{hash}.md      — archived post (pruned after ARCHIVE_DAYS days)
  last-run                   — UTC ISO timestamp of the last cycle with no failed step
"""

import glob
import hashlib
import html
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit

import praw
import pyotp
from feedgen.feed import FeedGenerator

from reddit_rss_fetcher import reading
from reddit_rss_fetcher.reading import ReadingTimes
from reddit_rss_fetcher.text import excerpt, holds_all, paragraphs, plain_paragraphs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/output")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

# Subreddit archiver
SUBREDDITS = [s.strip() for s in os.environ.get("SUBREDDITS", "").split(",") if s.strip()]
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.environ.get("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.environ.get("REDDIT_PASSWORD", "")
REDDIT_TOTP_SECRET = os.environ.get("REDDIT_TOTP_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
# The server's feed token: archived-copy links need it (empty in local mode)
SERVE_TOKEN = os.environ.get("SERVE_TOKEN", "")
ARCHIVE_DAYS = int(os.environ.get("ARCHIVE_DAYS", "30"))
TOP_PERIOD = os.environ.get("TOP_PERIOD", "week")
TOP_LIMIT = int(os.environ.get("TOP_LIMIT", "25"))

FRONT_PAGE_LIMIT = int(os.environ.get("FRONT_PAGE_LIMIT", "25"))
# A feed turns the hot ranking into a stream: every post that enters the list
# becomes an item. Big subreddits enter it near the top and niche ones lower,
# so a shallower list drops the niche subreddits first; a cap of new posts a
# subreddit a day cuts the volume and keeps them all (0: no cap).
FRONT_PAGE_PER_SUBREDDIT = int(os.environ.get("FRONT_PAGE_PER_SUBREDDIT", "1"))
# The front-page feed keeps every post it admitted in the last
# FRONT_PAGE_KEEP_HOURS, not only the current hot list, so a reader that was
# offline between two fetches still gets the posts that came and went.
FRONT_PAGE_KEEP_HOURS = int(os.environ.get("FRONT_PAGE_KEEP_HOURS", "48"))
FRONT_PAGE_MAX_ITEMS = 150
FRONT_PAGE_STATE = "front-page-state.json"

# Images in items: Reddit's rendition at most this wide (originals run to
# 9000 px), and a gallery's first images up to Reddit's own maximum.
MAX_IMAGE_WIDTH = 1080
GALLERY_LIMIT = 20

# Characters of a post's text an item shows: enough to judge it by, as in
# feed-refinery's items.
SHOWN_LIMIT = 800

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


def read_file(path) -> bytes | None:
    """What write_file wrote at path, or None when there is nothing there."""
    if GCS_BUCKET:
        from google.api_core.exceptions import NotFound

        try:
            return _gcs().bucket(GCS_BUCKET).blob(path).download_as_bytes(timeout=60)
        except NotFound:
            return None
    try:
        with open(os.path.join(OUTPUT_DIR, path), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _new_feed(title: str, description: str, link: str) -> FeedGenerator:
    fg = FeedGenerator()
    fg.id(link)
    fg.title(title)
    fg.description(description)
    fg.link(href=link, rel="alternate")
    fg.language("en")
    fg.load_extension("dc")
    fg.load_extension("media")
    return fg


def _get(obj, name):
    """An attribute of a Submission, or a key of a crosspost parent's dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _preview_image(preview) -> str | None:
    images = (preview or {}).get("images") or []
    if not images:
        return None
    fitting = [r for r in images[0].get("resolutions", []) if r["width"] <= MAX_IMAGE_WIDTH]
    best = max(fitting, key=lambda r: r["width"]) if fitting else images[0]["source"]
    return best["url"]


def _gallery_images(gallery_data, media_metadata) -> list[str]:
    urls = []
    for item in (gallery_data or {}).get("items", [])[:GALLERY_LIMIT]:
        meta = (media_metadata or {}).get(item["media_id"], {})
        if meta.get("status") != "valid":  # still processing, or removed
            continue
        if meta.get("e") == "AnimatedImage":
            url = meta["s"].get("gif")
        else:
            fitting = [p for p in meta.get("p", []) if p["x"] <= MAX_IMAGE_WIDTH]
            url = max(fitting, key=lambda p: p["x"])["u"] if fitting else meta["s"]["u"]
        if url:
            urls.append(url)
    return urls


def _media_sources(post) -> list:
    """The post, then the original of a crosspost, which holds the media."""
    return [post, *(_get(post, "crosspost_parent_list") or [])[:1]]


def _post_images(post) -> list[str]:
    """Gallery images, else the preview (image, video frame, link image)."""
    for source in _media_sources(post):
        urls = _gallery_images(_get(source, "gallery_data"), _get(source, "media_metadata"))
        if not urls:
            preview = _preview_image(_get(source, "preview"))
            urls = [preview] if preview else []
        if urls:
            # The API HTML-escapes these URLs (&amp; in the query string).
            return [html.unescape(url) for url in urls]
    return []


def _is_video(post) -> bool:
    return any(_get(source, "is_video") for source in _media_sources(post))


ENTRY_KEYS = ("id", "title", "link", "creator", "description", "thumbnail", "published")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _domain(url: str) -> str:
    host = urlsplit(url).hostname
    return host.removeprefix("www.") if host else url


def _entry(post, archive_url: str = "", minutes: int | None = None) -> dict:
    """A post as feed item data, laid out as feed-refinery's items: images, the text cut to SHOWN_LIMIT, the link and
    reading time, then the counts, bylined with its subreddit.

    The reading time follows the text, where one decides whether to read on:
    for a link post the linked page's, minutes; for a self post its whole
    text's, left out when the excerpt shows all of it.
    """
    permalink = f"https://www.reddit.com{post.permalink}"
    subreddit = f"r/{post.subreddit.display_name}"

    images = _post_images(post)
    body = [f'<p><img src="{html.escape(url)}" alt=""></p>' for url in images]
    if _is_video(post):
        # Readers drop <video>, and v.redd.it's fallback file has no sound:
        # the preview frame above plus a link is what works.
        body.append(f'<p><a href="{html.escape(permalink)}">▶ Video on Reddit</a></p>')
    text = plain_paragraphs(post.selftext_html or "")
    if shown := excerpt(text, SHOWN_LIMIT):
        body.append(paragraphs(shown))
    article = []
    if post.url != permalink:  # a link post; a self post's url is its thread
        article.append(f'<a href="{html.escape(post.url)}">{html.escape(_domain(post.url))}</a>')
    elif text and not holds_all(shown, words := len(text.split())):
        minutes = reading.minutes(words)
    if read := reading.minutes_read(minutes):
        article.append(read)
    if article:
        body.append("<p>" + " · ".join(article) + "</p>")
    stats = [
        f'<a href="{html.escape(permalink)}">{_plural(post.num_comments, "comment")}</a>',
        _plural(post.score, "point"),
        html.escape(subreddit),
    ]
    if archive_url:
        stats.append(f'<a href="{html.escape(archive_url)}">archived copy</a>')
    body.append("<p>" + " · ".join(stats) + "</p>")
    return {
        "id": post.fullname,
        "title": post.title,
        "link": permalink,
        "creator": subreddit,
        "description": "\n".join(body),
        "thumbnail": images[0] if images else None,
        "published": post.created_utc,
    }


def _add_entry(fg: FeedGenerator, entry: dict, published: float | None = None) -> None:
    """Append an _entry() item, dated published (default: when it was posted)."""
    fe = fg.add_entry(order="append")
    fe.id(entry["id"])
    fe.title(entry["title"])
    fe.link(href=entry["link"])
    # Feed readers show dc:creator as the byline: the subreddit says more
    # on a front page than the poster's name.
    fe.dc.dc_creator(entry["creator"])
    fe.description(entry["description"])
    if entry["thumbnail"]:
        # The picture card and magazine views show, instead of a guess.
        fe.media.thumbnail(url=entry["thumbnail"], group=None)
    when = entry["published"] if published is None else published
    fe.pubDate(datetime.fromtimestamp(when, tz=UTC))


def _add_post(fg: FeedGenerator, post, archive_url: str = "", minutes: int | None = None) -> None:
    _add_entry(fg, _entry(post, archive_url, minutes))


def _load_front_page_state() -> dict:
    """The posts the front-page feed listed, by id; empty when missing or unreadable."""
    raw = read_file(FRONT_PAGE_STATE)
    if raw is None:
        return {}
    try:
        posts = json.loads(raw)["posts"]
        return {
            pid: p
            for pid, p in posts.items()
            if isinstance(p, dict) and all(k in p for k in (*ENTRY_KEYS, "first_seen", "rank"))
        }
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        log.warning("Ignoring unreadable %s: %s", FRONT_PAGE_STATE, e)
        return {}


def _subreddit_full(posts: dict, creator: str, now: float) -> bool:
    """Whether creator's subreddit already had its new posts of the day."""
    if not FRONT_PAGE_PER_SUBREDDIT:
        return False
    # A day less an hour: the fetch a day later, which may start a few seconds
    # early, admits the next one.
    day_ago = now - 23 * 3600
    recent = sum(p["creator"] == creator and p["first_seen"] > day_ago for p in posts.values())
    return recent >= FRONT_PAGE_PER_SUBREDDIT


def fetch_front_page(reddit: praw.Reddit, times: ReadingTimes | None = None) -> None:
    """Write the account's front page as RSS.

    Reddit stopped serving RSS (including the private reddit.com/.rss?feed=
    front page) on 2026-11-13, so the feed is built from the API listing.
    """
    # The listing first: an API error leaves the previous files as they are.
    hot = list(reddit.front.hot(limit=FRONT_PAGE_LIMIT))
    times = ReadingTimes() if times is None else times
    times.prefetch(post.url for post in hot)
    now = time.time()
    posts = _load_front_page_state()
    new = capped = 0
    # Every listed post, its rank, and whether the feed has it: replayed from
    # the logs, the ranks show what the per-subreddit cap left out.
    listing = []
    for rank, post in enumerate(hot):
        entry = _entry(post, minutes=times.minutes(post.url))
        seen = posts.get(entry["id"])
        in_feed = seen is not None or not _subreddit_full(posts, entry["creator"], now)
        listing.append([entry["id"], entry["creator"], rank, int(in_feed)])
        if not in_feed:
            # Left out, not remembered: a later fetch may still admit it.
            capped += 1
            continue
        new += seen is None
        # Refresh what the post shows; keep when it first appeared and where.
        posts[entry["id"]] = {
            **entry,
            "first_seen": seen["first_seen"] if seen else now,
            "rank": seen["rank"] if seen else rank,
        }
    listed = {post.fullname for post in hot}
    cutoff = now - FRONT_PAGE_KEEP_HOURS * 3600
    kept = [p for pid, p in posts.items() if pid in listed or p["first_seen"] >= cutoff]
    # Newest first; posts that appeared in the same fetch keep their hot order.
    kept.sort(key=lambda p: (-p["first_seen"], p["rank"]))
    kept = kept[:FRONT_PAGE_MAX_ITEMS]
    log.info("Front page: %d listed, %d new, %d capped, %d in feed", len(hot), new, capped, len(kept))
    log.info("Front page listing: %s", json.dumps(listing, separators=(",", ":")))

    fg = _new_feed(
        "reddit: the front page",
        f"Hot posts from your Reddit front page, the last {FRONT_PAGE_KEEP_HOURS} hours",
        "https://www.reddit.com/",
    )
    # Dated by when the feed admitted a post, so a reader sorting by date
    # shows what is new to the feed on top, however old the post.
    for p in kept:
        _add_entry(fg, p, published=p["first_seen"])

    feed = fg.rss_str(pretty=True)
    write_file("reddit-front-page.xml", feed)
    write_file("reddit-front-page", feed)  # extension-less copy for Feedly
    state = {"version": 1, "posts": {p["id"]: p for p in kept}}
    write_file(FRONT_PAGE_STATE, json.dumps(state), content_type="application/json")


def build_reddit_client() -> praw.Reddit:
    password = REDDIT_PASSWORD
    if REDDIT_TOTP_SECRET:
        totp = pyotp.TOTP(REDDIT_TOTP_SECRET).now()
        password = f"{REDDIT_PASSWORD}:{totp}"
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        # Reddit's API rules: <platform>:<app ID>:<version> (by /u/<username>)
        user_agent=f"python:reddit-rss-fetcher:v2.0 (by /u/{REDDIT_USERNAME})",
        username=REDDIT_USERNAME,
        password=password,
    )


def _archive_url(md_rel_path: str) -> str:
    if not BASE_URL:
        return f"file://{os.path.join(OUTPUT_DIR, md_rel_path)}"
    # server.py serves the archive only with the feed's own token.
    query = f"?token={quote(SERVE_TOKEN)}" if SERVE_TOKEN else ""
    return f"{BASE_URL}/{md_rel_path}{query}"


def fetch_subreddit(reddit: praw.Reddit, subreddit_name: str, times: ReadingTimes | None = None) -> None:
    fg = _new_feed(
        subreddit_name,
        f"r/{subreddit_name} — top posts of the {TOP_PERIOD}",
        f"https://www.reddit.com/r/{subreddit_name}/",
    )

    top = list(reddit.subreddit(subreddit_name).top(time_filter=TOP_PERIOD, limit=TOP_LIMIT))
    times = ReadingTimes() if times is None else times
    times.prefetch(post.url for post in top)
    for post in top:
        url_hashed = hashlib.md5(post.url.encode("utf-8")).hexdigest()  # nosec B324: a file name, not a security hash
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
        _add_post(fg, post, _archive_url(md_rel_path), times.minutes(post.url))

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
            age = (datetime.now(tz=UTC) - datetime.fromtimestamp(os.stat(article).st_ctime, tz=UTC)).days
            if age >= ARCHIVE_DAYS:
                os.remove(article)
                log.info("Pruned %s", article)


def has_credentials() -> bool:
    return all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD])


def fetch_subreddits(reddit: praw.Reddit, times: ReadingTimes | None = None) -> None:
    if not SUBREDDITS:
        log.warning("SUBREDDITS not set, skipping subreddit archiver")
        return

    log.info("Archiving subreddits: %s", ", ".join(SUBREDDITS))
    for subreddit_name in SUBREDDITS:
        fetch_subreddit(reddit, subreddit_name, times)


def write_health() -> None:
    write_file(
        "last-run",
        datetime.now(UTC).isoformat(),
        "text/plain; charset=utf-8",
    )


def run_all() -> bool:
    """Run every step; True, and a new last-run, only when none failed.

    A failed step doesn't stop the others, but leaves last-run at the last
    good cycle, so /last-run goes stale and the uptime check notices.
    """
    log.info("Starting fetch cycle")
    if not has_credentials():
        log.error("PRAW credentials not set, nothing to fetch")
        return False
    # One client for both: it logs in once, with a single TOTP code.
    reddit = build_reddit_client()
    times = ReadingTimes.load(read_file)
    ok = True
    for fn in (fetch_front_page, fetch_subreddits):
        try:
            fn(reddit, times)
        except Exception:
            log.exception("Failed in %s", fn.__name__)
            ok = False
    try:
        times.save(write_file, prune=ok)
    except Exception:  # a cache: the next cycle reads the pages again
        log.exception("Failed to save the reading times")
    if not ok:
        log.error("Fetch cycle failed, last-run left unchanged")
        return False
    write_health()
    log.info("Fetch cycle complete")
    return True


def main() -> None:
    """Console script reddit-rss-fetch: one cycle in GCS mode, a loop otherwise."""
    if GCS_BUCKET:
        log.info("GCS_BUCKET=%s, running a single cycle", GCS_BUCKET)
        run_all()
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        while True:
            run_all()
            log.info("Sleeping %dh", FETCH_INTERVAL_HOURS)
            time.sleep(FETCH_INTERVAL_HOURS * 3600)
