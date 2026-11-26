"""Reading times: a self post's words, or for a link post the linked page's, at 230 a minute.

A media post has nothing to read, so of links only one to another site gets
one. Each page is read once: the counts stay in reading-times.json between
cycles, for as long as a feed lists the link.
"""

import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

WORDS_PER_MINUTE = 230
READING_MIN = 200  # words: fewer usually means a page its scripts fill in, or only the lede
# Before the reading time, how long the read is at a glance: up to 3 minutes,
# up to 7, longer. Dots, not a colour: a long article isn't one to skip.
LENGTHS = ((3, "●○○"), (7, "●●○"))
LONG_READ = "●●●"
CACHE = "reading-times.json"
# New pages read a cycle, WORKERS at a time: a cycle runs inside POST /fetch,
# which Cloud Run cuts off at 300 seconds. The rest wait for the next cycle.
FETCHES = 40
WORKERS = 8
TIMEOUT = 10  # seconds a page
MAX_BYTES = 2_000_000
MAX_ATTEMPTS = 3  # reads of a page before it counts as having no reading time
USER_AGENT = "reddit-rss-fetcher (+https://github.com/mbologna/reddit-rss-fetcher)"
# Links to Reddit itself (crossposts, galleries), and to images and videos:
# nothing to read.
MEDIA_HOSTS = ("reddit.com", "redd.it", "imgur.com", "youtube.com", "youtu.be", "streamable.com", "gfycat.com")
MEDIA_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".gifv", ".webp", ".mp4", ".pdf")
CONTENT = ("article", "main")


class _Words(HTMLParser):
    """Counts the words of a page's paragraphs: all of them, and those in its first <article> or <main> that has
    any, which leaves out related stories, teasers and comments in later ones."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.words = 0
        self.content_words = 0
        self.in_paragraph = False
        self.content_depth = 0  # open <article>/<main> elements, while looking at the first with paragraphs
        self.content_done = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "p":
            self.in_paragraph = True
        elif tag in CONTENT and not self.content_done:
            self.content_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self.in_paragraph = False
        elif tag in CONTENT and self.content_depth:
            self.content_depth -= 1
            if not self.content_depth and self.content_words:
                self.content_done = True

    def handle_data(self, data: str) -> None:
        if not self.in_paragraph:
            return
        words = len(data.split())
        self.words += words
        if self.content_depth and not self.content_done:
            self.content_words += words


def page_words(page: str) -> int:
    """The words of a page's article: its first <article> or <main> that has paragraphs, else all its paragraphs."""
    parser = _Words()
    parser.feed(page)
    parser.close()
    return parser.content_words or parser.words


def fetch_page(url: str) -> str | None:
    """The page at url, when it's HTML; None for anything else. Raises on a network or HTTP error."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # nosec B310: http(s) only, see readable()
        if response.headers.get_content_type() != "text/html":
            return None
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(MAX_BYTES).decode(charset, errors="replace")


def readable(url: str | None) -> bool:
    """Whether url may be an article: a web page on another site than Reddit, not an image or a video."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").removeprefix("www.")
    if parts.scheme not in ("http", "https") or not host:
        return False
    if any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS):
        return False
    return not parts.path.lower().endswith(MEDIA_SUFFIXES)


def minutes(words: int | None) -> int | None:
    """Minutes to read that many words; None for too few to tell."""
    if not words or words < READING_MIN:
        return None
    return max(1, round(words / WORDS_PER_MINUTE))


def minutes_read(n: int | None) -> str | None:
    """The reading time in words after its dots, as "●●○ 6 min read"."""
    if not n:
        return None
    dots = next((d for limit, d in LENGTHS if n <= limit), LONG_READ)
    return f"{dots} {n} min read"


class ReadingTimes:
    """The links' word counts, by URL: {"words": n or None, "attempts": n}. Words stay None until a read succeeds,
    and after MAX_ATTEMPTS failed reads become 0."""

    def __init__(self, counts: dict | None = None) -> None:
        self.counts: dict[str, dict] = counts or {}
        self.listed: set[str] = set()  # the links this cycle's feeds list: what save() keeps

    @classmethod
    def load(cls, read_file) -> ReadingTimes:
        """The counts read_file finds at CACHE; none when it's missing or unreadable."""
        raw = read_file(CACHE)
        if raw is None:
            return cls()
        try:
            counts = json.loads(raw)["links"]
            return cls({url: c for url, c in counts.items() if isinstance(c, dict) and "words" in c})
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            log.warning("Ignoring unreadable %s: %s", CACHE, e)
            return cls()

    def save(self, write_file, prune: bool = True) -> None:
        """Write the counts, with prune only those of the links listed this cycle: the others' posts have left
        every feed. A cycle with a failed step may not have listed them all, so it keeps every count."""
        links = {url: c for url, c in self.counts.items() if url in self.listed or not prune}
        write_file(CACHE, json.dumps({"version": 1, "links": links}), content_type="application/json")

    def _wanted(self, url: str) -> bool:
        count = self.counts.get(url)
        return count is None or (count["words"] is None and count.get("attempts", 0) < MAX_ATTEMPTS)

    def _read(self, url: str) -> None:
        count = self.counts.setdefault(url, {"words": None, "attempts": 0})
        try:
            page = fetch_page(url)
        except Exception as e:  # any error: one odd page must not fail the cycle
            count["attempts"] = count.get("attempts", 0) + 1
            if count["attempts"] >= MAX_ATTEMPTS:
                count["words"] = 0
            log.info("Reading time of %s: %s", urlsplit(url).hostname, type(e).__name__)
            return
        count["words"] = page_words(page) if page else 0

    def prefetch(self, urls) -> None:
        """Read, in parallel, the pages of the readable links not counted yet, at most FETCHES."""
        readable_urls = [u for u in dict.fromkeys(urls) if readable(u)]
        self.listed.update(readable_urls)
        todo = [u for u in readable_urls if self._wanted(u)][:FETCHES]
        if todo:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                list(pool.map(self._read, todo))
            log.info("Reading times: %d pages read", len(todo))

    def minutes(self, url: str | None) -> int | None:
        """The link's reading time, when its page was read and has enough words."""
        if not readable(url):
            return None
        return minutes((self.counts.get(url or "") or {}).get("words"))
