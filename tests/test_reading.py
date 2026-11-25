import json
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from reddit_rss_fetcher import reading
from reddit_rss_fetcher.reading import ReadingTimes, minutes, page_words, readable
from reddit_rss_fetcher.reading import fetch_page as real_fetch_page  # imported before the autouse stub replaces it

ARTICLE = "https://news.example/story"


def words(n, word="word"):
    return " ".join([word] * n)


def test_page_words_counts_the_first_article_with_paragraphs():
    page = (
        f"<header><p>{words(5)}</p></header>"
        "<article><h1>Title</h1></article>"  # no paragraphs: not the article
        f"<main><p>{words(300)}</p><div><p>{words(20)}</p></div></main>"
        f"<article><p>{words(500, 'related')}</p></article>"
    )
    assert page_words(page) == 320


def test_page_words_counts_every_paragraph_without_an_article():
    assert page_words(f"<p>{words(3)}</p><div>not a paragraph</div><p>{words(4)}</p>") == 7
    assert page_words("<p>unclosed paragraph<p>") == 2


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://news.example/story", True),
        ("http://www.blog.example/post", True),
        ("https://www.reddit.com/r/x/comments/1/", False),
        ("https://i.redd.it/a.jpg", False),
        ("https://v.redd.it/abc", False),
        ("https://imgur.com/gallery/x", False),
        ("https://youtu.be/x", False),
        ("https://cdn.example/photo.PNG", False),
        ("https://papers.example/paper.pdf", False),
        ("ftp://files.example/a", False),
        ("", False),
        (None, False),
    ],
)
def test_readable(url, expected):
    assert readable(url) is expected


@pytest.mark.parametrize(("count", "expected"), [(None, None), (0, None), (199, None), (200, 1), (2300, 10)])
def test_minutes(count, expected):
    assert minutes(count) == expected


def test_prefetch_reads_each_readable_page_once(pages):
    pages[ARTICLE] = f"<article><p>{words(2300)}</p></article>"
    times = ReadingTimes()
    times.prefetch([ARTICLE, ARTICLE, "https://i.redd.it/a.jpg"])
    assert times.minutes(ARTICLE) == 10
    assert times.minutes("https://i.redd.it/a.jpg") is None
    del pages[ARTICLE]  # a second read would fail
    times.prefetch([ARTICLE])
    assert times.minutes(ARTICLE) == 10


def test_a_page_that_isnt_html_has_no_reading_time(pages):
    pages[ARTICLE] = None
    times = ReadingTimes()
    times.prefetch([ARTICLE])
    assert times.counts[ARTICLE]["words"] == 0 and times.minutes(ARTICLE) is None


def test_a_failed_read_is_tried_again_up_to_max_attempts(pages):
    times = ReadingTimes()
    for _ in range(reading.MAX_ATTEMPTS):
        assert times.minutes(ARTICLE) is None
        times.prefetch([ARTICLE])  # ARTICLE isn't served: every read fails
    assert times.counts[ARTICLE] == {"words": 0, "attempts": reading.MAX_ATTEMPTS}
    pages[ARTICLE] = f"<p>{words(2300)}</p>"
    times.prefetch([ARTICLE])
    assert times.minutes(ARTICLE) is None  # given up


def test_prefetch_reads_at_most_fetches_pages_a_cycle(pages, monkeypatch):
    monkeypatch.setattr(reading, "FETCHES", 2)
    urls = [f"https://news.example/{n}" for n in range(3)]
    pages.update({u: f"<p>{words(460)}</p>" for u in urls})
    times = ReadingTimes()
    times.prefetch(urls)
    assert [times.minutes(u) for u in urls] == [2, 2, None]
    times.prefetch(urls)
    assert times.minutes(urls[2]) == 2


def test_save_keeps_the_links_listed_this_cycle_and_load_reads_them(pages):
    pages[ARTICLE] = f"<p>{words(460)}</p>"
    written = {}
    times = ReadingTimes({"https://old.example/gone": {"words": 900, "attempts": 0}})
    times.prefetch([ARTICLE])
    times.save(lambda path, content, content_type: written.update({path: content}))
    assert json.loads(written[reading.CACHE]) == {"version": 1, "links": {ARTICLE: {"words": 460, "attempts": 0}}}
    loaded = ReadingTimes.load(lambda path: written.get(path))
    assert loaded.minutes(ARTICLE) == 2


def test_save_after_a_failed_step_keeps_every_count():
    written = {}
    times = ReadingTimes({"https://old.example/kept": {"words": 900, "attempts": 0}})
    times.save(lambda path, content, content_type: written.update({path: content}), prune=False)
    assert "https://old.example/kept" in json.loads(written[reading.CACHE])["links"]


@pytest.mark.parametrize("raw", [None, b"not json", b'{"version": 1}', b'{"links": {"u": 5, "v": {"words": 1}}}'])
def test_load_ignores_what_it_cant_read(raw):
    times = ReadingTimes.load(lambda path: raw)
    assert set(times.counts) <= {"v"}


def _response(content_type, body=b"<p>hi</p>"):
    headers = Message()
    headers["Content-Type"] = content_type
    response = MagicMock()
    response.headers = headers
    response.read.return_value = body
    response.__enter__.return_value = response
    return response


@patch("urllib.request.urlopen")
def test_fetch_page_reads_html_only(urlopen):
    urlopen.return_value = _response("text/html; charset=latin-1", b"caf\xe9")
    assert real_fetch_page(ARTICLE) == "café"
    request = urlopen.call_args.args[0]
    assert request.get_header("User-agent") == reading.USER_AGENT
    assert urlopen.call_args.kwargs["timeout"] == reading.TIMEOUT
    urlopen.return_value = _response("application/pdf")
    assert real_fetch_page(ARTICLE) is None
