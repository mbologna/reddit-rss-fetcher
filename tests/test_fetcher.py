import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from reddit_rss_fetcher import fetcher

# ── fetch_front_page ──────────────────────────────────────────────────────────


def _make_mock_front_post(
    post_id="xyz789",
    title="Front <Page> Post",
    url="https://example.com/article",
    permalink="/r/python/comments/xyz789/front_page_post/",
    selftext_html="<p>Body &amp; more</p>",
    created_utc=1700000000.0,
    author="poster",
    subreddit="python",
    **media,
):
    """A Submission stand-in. A namespace, not a MagicMock: a MagicMock would
    invent every media attribute (preview, gallery_data, ...) as truthy."""
    fields = {
        "preview": None,
        "gallery_data": None,
        "media_metadata": None,
        "crosspost_parent_list": None,
        "is_video": False,
    }
    fields.update(media)
    return SimpleNamespace(
        id=post_id,
        fullname=f"t3_{post_id}",
        title=title,
        url=url,
        permalink=permalink,
        selftext="Body & more",
        selftext_html=selftext_html,
        created_utc=created_utc,
        author=author,
        subreddit=SimpleNamespace(display_name=subreddit),
        **fields,
    )


def test_fetch_front_page_writes_rss_from_api(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "FRONT_PAGE_LIMIT", 10)
    mock_reddit = MagicMock()
    mock_reddit.front.hot.return_value = [
        _make_mock_front_post(),
        _make_mock_front_post(post_id="second", title="Second", selftext_html=None, subreddit="rust"),
    ]

    fetcher.fetch_front_page(mock_reddit)

    mock_reddit.front.hot.assert_called_once_with(limit=10)
    xml = (tmp_path / "reddit-front-page.xml").read_bytes()
    assert (tmp_path / "reddit-front-page").read_bytes() == xml
    items = ET.fromstring(xml).findall("./channel/item")
    # Hot ranking order, not feedgen's default prepend.
    assert [i.findtext("title") for i in items] == ["Front <Page> Post", "Second"]
    first = items[0]
    assert first.findtext("guid") == "t3_xyz789"
    assert first.findtext("link") == "https://www.reddit.com/r/python/comments/xyz789/front_page_post/"
    dc = "{http://purl.org/dc/elements/1.1/}creator"
    assert first.findtext(dc) == "r/python"
    description = first.findtext("description")
    assert "<p>Body &amp; more</p>" in description
    assert "r/python" in description
    assert "poster" not in xml.decode()
    assert '<a href="https://example.com/article">[link]</a>' in description


def test_fetch_front_page_propagates_api_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    mock_reddit = MagicMock()
    mock_reddit.front.hot.side_effect = Exception("HTTP 429")

    with pytest.raises(Exception, match="HTTP 429"):
        fetcher.fetch_front_page(mock_reddit)

    assert not (tmp_path / "reddit-front-page.xml").exists()


# ── front page history ────────────────────────────────────────────────────────

HOUR = 3600


def _run_front(tmp_path, monkeypatch, now, *posts):
    """One fetch at time now; returns the items' guids and the saved state."""
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher.time, "time", lambda: now)
    mock_reddit = MagicMock()
    mock_reddit.front.hot.return_value = list(posts)
    fetcher.fetch_front_page(mock_reddit)
    items = ET.parse(tmp_path / "reddit-front-page.xml").findall("./channel/item")
    state = json.loads((tmp_path / fetcher.FRONT_PAGE_STATE).read_text())
    return [i.findtext("guid") for i in items], state


def test_front_page_keeps_posts_that_left_the_hot_list(tmp_path, monkeypatch, caplog):
    t = 1_800_000_000
    a, b, c = (_make_mock_front_post(post_id=x, title=x, subreddit=x) for x in "abc")
    guids, _ = _run_front(tmp_path, monkeypatch, t, a, b)
    assert guids == ["t3_a", "t3_b"]
    # Two hours later b has dropped off the hot list and c is new.
    caplog.clear()
    with caplog.at_level("INFO"):
        guids, state = _run_front(tmp_path, monkeypatch, t + 2 * HOUR, c, a)
    assert guids == ["t3_c", "t3_a", "t3_b"]
    assert "Front page: 2 listed, 1 new, 0 capped, 3 in feed" in caplog.messages
    assert state["posts"]["t3_a"]["first_seen"] == t
    assert state["posts"]["t3_c"]["first_seen"] == t + 2 * HOUR


def test_front_page_dates_items_by_first_listing_and_refreshes_them(tmp_path, monkeypatch):
    t = 1_800_000_000
    post = _make_mock_front_post(post_id="a", title="Old title", created_utc=1_700_000_000.0)
    _run_front(tmp_path, monkeypatch, t, post)
    post.title = "New title"
    _run_front(tmp_path, monkeypatch, t + HOUR, post)
    item = ET.parse(tmp_path / "reddit-front-page.xml").find("./channel/item")
    assert item.findtext("title") == "New title"
    assert item.findtext("pubDate") == "Fri, 15 Jan 2027 08:00:00 +0000"  # t, not the post's own date


def test_front_page_forgets_posts_after_keep_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "FRONT_PAGE_KEEP_HOURS", 3)
    t = 1_800_000_000
    a, b = (_make_mock_front_post(post_id=x, subreddit=x) for x in "ab")
    _run_front(tmp_path, monkeypatch, t, a)
    guids, state = _run_front(tmp_path, monkeypatch, t + 4 * HOUR, b)
    assert guids == ["t3_b"]
    assert list(state["posts"]) == ["t3_b"]
    # A post still on the hot list stays however long ago it first appeared.
    guids, _ = _run_front(tmp_path, monkeypatch, t + 9 * HOUR, b)
    assert guids == ["t3_b"]


def test_front_page_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "FRONT_PAGE_MAX_ITEMS", 2)
    t = 1_800_000_000
    _run_front(tmp_path, monkeypatch, t, _make_mock_front_post(post_id="old"))
    guids, _ = _run_front(
        tmp_path,
        monkeypatch,
        t + HOUR,
        *(_make_mock_front_post(post_id=x, subreddit=x) for x in "xy"),
    )
    assert guids == ["t3_x", "t3_y"]


def test_front_page_admits_one_new_post_a_subreddit_a_day(tmp_path, monkeypatch, caplog):
    t = 1_800_000_000
    a, b, c = (_make_mock_front_post(post_id=x, subreddit="python") for x in "abc")
    other = _make_mock_front_post(post_id="o", subreddit="rust")
    # The higher-ranked post of a subreddit gets in; the other waits.
    with caplog.at_level("INFO"):
        guids, _ = _run_front(tmp_path, monkeypatch, t, a, b, other)
    assert guids == ["t3_a", "t3_o"]
    assert "Front page: 3 listed, 2 new, 1 capped, 2 in feed" in caplog.messages
    # An admitted post stays and refreshes; a new one waits out the day.
    guids, _ = _run_front(tmp_path, monkeypatch, t + 22 * HOUR, a, b)
    assert guids == ["t3_a", "t3_o"]
    guids, state = _run_front(tmp_path, monkeypatch, t + 23 * HOUR, b, c)
    assert guids == ["t3_b", "t3_a", "t3_o"]
    assert state["posts"]["t3_b"]["first_seen"] == t + 23 * HOUR


def test_front_page_per_subreddit_cap_can_be_raised_or_disabled(tmp_path, monkeypatch):
    posts = [_make_mock_front_post(post_id=x, subreddit="python") for x in "abc"]
    monkeypatch.setattr(fetcher, "FRONT_PAGE_PER_SUBREDDIT", 2)
    guids, _ = _run_front(tmp_path, monkeypatch, 1_800_000_000, *posts)
    assert guids == ["t3_a", "t3_b"]
    monkeypatch.setattr(fetcher, "FRONT_PAGE_PER_SUBREDDIT", 0)
    guids, _ = _run_front(tmp_path, monkeypatch, 1_800_000_000 + HOUR, *posts)
    assert guids == ["t3_c", "t3_a", "t3_b"]


@pytest.mark.parametrize(
    "state",
    [
        "not json",
        "[]",
        '{"posts": 3}',
        '{"posts": {"t3_z": {"title": "missing fields"}}}',
    ],
)
def test_front_page_ignores_an_unreadable_state(tmp_path, monkeypatch, state):
    (tmp_path / fetcher.FRONT_PAGE_STATE).write_text(state)
    guids, saved = _run_front(tmp_path, monkeypatch, 1_800_000_000, _make_mock_front_post(post_id="a"))
    assert guids == ["t3_a"]
    assert list(saved["posts"]) == ["t3_a"]


def test_read_file_from_gcs(fake_bucket):
    from google.api_core.exceptions import NotFound

    blob = fake_bucket.blob.return_value
    blob.download_as_bytes.return_value = b"{}"
    assert fetcher.read_file("state.json") == b"{}"
    blob.download_as_bytes.side_effect = NotFound("gone")
    assert fetcher.read_file("state.json") is None


# ── images in items ───────────────────────────────────────────────────────────

MEDIA = "{http://search.yahoo.com/mrss/}thumbnail"


def _preview(source, *widths):
    """A preview block: the original plus one rendition per width."""
    return {
        "images": [
            {
                "source": {"url": source, "width": 4000, "height": 3000},
                "resolutions": [
                    {
                        "url": f"https://preview.redd.it/a.jpg?width={w}&amp;s=x",
                        "width": w,
                        "height": w,
                    }
                    for w in widths
                ],
            }
        ]
    }


def _front_item(tmp_path, monkeypatch, post):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    mock_reddit = MagicMock()
    mock_reddit.front.hot.return_value = [post]
    fetcher.fetch_front_page(mock_reddit)
    return ET.parse(tmp_path / "reddit-front-page.xml").find("./channel/item")


def test_image_post_shows_the_widest_fitting_rendition(tmp_path, monkeypatch):
    post = _make_mock_front_post(
        url="https://i.redd.it/abc.jpeg",
        selftext_html="<p>caption</p>",
        preview=_preview("https://i.redd.it/abc.jpeg", 320, 1080, 2160),
    )

    item = _front_item(tmp_path, monkeypatch, post)

    url = "https://preview.redd.it/a.jpg?width=1080&s=x"  # unescaped
    description = item.findtext("description")
    assert description.startswith(f'<p><img src="{url.replace("&", "&amp;")}"')
    assert "<p>caption</p>" in description  # the text follows the image
    assert item.find(MEDIA).get("url") == url


def test_preview_without_fitting_rendition_uses_the_original(tmp_path, monkeypatch):
    post = _make_mock_front_post(preview=_preview("https://i.redd.it/o.png", 2160))

    item = _front_item(tmp_path, monkeypatch, post)

    assert item.find(MEDIA).get("url") == "https://i.redd.it/o.png"


def _gallery(*entries):
    """gallery_data and media_metadata for (media_id, metadata) pairs."""
    return {
        "gallery_data": {"items": [{"media_id": mid} for mid, _ in entries]},
        "media_metadata": dict(entries),
    }


def _still(name):
    return {
        "status": "valid",
        "e": "Image",
        "s": {"u": f"https://preview.redd.it/{name}.jpg?full", "x": 4000},
        "p": [
            {"u": f"https://preview.redd.it/{name}.jpg?w=640&amp;s=1", "x": 640},
            {"u": f"https://preview.redd.it/{name}.jpg?w=1080&amp;s=1", "x": 1080},
        ],
    }


def test_gallery_shows_every_valid_image_in_order(tmp_path, monkeypatch):
    animated = {
        "status": "valid",
        "e": "AnimatedImage",
        "s": {"gif": "https://i.redd.it/c.gif", "mp4": "https://i.redd.it/c.mp4"},
    }
    post = _make_mock_front_post(
        url="https://www.reddit.com/gallery/xyz789",
        **_gallery(
            ("b", _still("b")),
            ("gone", {"status": "failed"}),
            ("a", _still("a")),
            ("c", animated),
        ),
    )

    item = _front_item(tmp_path, monkeypatch, post)

    srcs = re.findall(r'<img src="([^"]+)"', item.findtext("description"))
    assert srcs == [
        "https://preview.redd.it/b.jpg?w=1080&amp;s=1",
        "https://preview.redd.it/a.jpg?w=1080&amp;s=1",
        "https://i.redd.it/c.gif",
    ]
    assert item.find(MEDIA).get("url") == "https://preview.redd.it/b.jpg?w=1080&s=1"


def test_gallery_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "GALLERY_LIMIT", 2)
    post = _make_mock_front_post(**_gallery(*[(n, _still(n)) for n in ("a", "b", "c")]))

    item = _front_item(tmp_path, monkeypatch, post)

    assert item.findtext("description").count("<img ") == 2


def test_crosspost_takes_media_from_the_original(tmp_path, monkeypatch):
    post = _make_mock_front_post(
        crosspost_parent_list=[_gallery(("a", _still("a")))],
    )

    item = _front_item(tmp_path, monkeypatch, post)

    assert "preview.redd.it/a.jpg" in item.findtext("description")


def test_video_shows_its_frame_and_a_link(tmp_path, monkeypatch):
    post = _make_mock_front_post(
        url="https://v.redd.it/vid",
        is_video=True,
        preview=_preview("https://external-preview.redd.it/frame.jpg"),
    )

    item = _front_item(tmp_path, monkeypatch, post)

    description = item.findtext("description")
    assert "frame.jpg" in description
    thread = "https://www.reddit.com/r/python/comments/xyz789/front_page_post/"
    assert f'<p><a href="{thread}">▶ Video on Reddit</a></p>' in description


def test_post_without_media_has_no_image(tmp_path, monkeypatch):
    post = _make_mock_front_post(url="https://en.wikipedia.org/wiki/X")

    item = _front_item(tmp_path, monkeypatch, post)

    assert "<img " not in item.findtext("description")
    assert "Video on Reddit" not in item.findtext("description")
    assert item.find(MEDIA) is None


# ── fetch_subreddit ───────────────────────────────────────────────────────────


def _make_mock_post(
    post_id="abc123",
    title="Test Post Title",
    selftext="Post body content",
    created_utc=1700000000.0,
    author="testuser",
    subreddit="testsubreddit",
):
    """A self post: its url is its own thread."""
    permalink = f"/r/{subreddit}/comments/{post_id}/test_post_title/"
    post = _make_mock_front_post(
        post_id=post_id,
        title=title,
        url=f"https://www.reddit.com{permalink}",
        permalink=permalink,
        selftext_html=f"<p>{selftext}</p>",
        created_utc=created_utc,
        author=author,
        subreddit=subreddit,
    )
    post.selftext = selftext
    return post


def test_fetch_subreddit_writes_rss_and_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "BASE_URL", "https://example.com/reddit-rss-fetcher")
    monkeypatch.setattr(fetcher, "TOP_PERIOD", "week")
    monkeypatch.setattr(fetcher, "TOP_LIMIT", 25)

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.top.return_value = [_make_mock_post()]

    fetcher.fetch_subreddit(mock_reddit, "testsubreddit")

    rss_path = tmp_path / "testsubreddit.xml"
    assert rss_path.exists()
    ET.parse(rss_path)  # raises if invalid XML

    archive_dir = tmp_path / "testsubreddit"
    assert archive_dir.is_dir()
    md_files = list(archive_dir.glob("*.md"))
    assert len(md_files) == 1
    md = md_files[0].read_text()
    assert "Test Post Title" in md
    assert "Post body content" in md


def test_fetch_subreddit_links_thread_and_tokenised_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "BASE_URL", "https://example.com")
    monkeypatch.setattr(fetcher, "SERVE_TOKEN", "s3cr&t")
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.top.return_value = [_make_mock_post()]

    fetcher.fetch_subreddit(mock_reddit, "testsubreddit")

    item = ET.parse(tmp_path / "testsubreddit.xml").find("./channel/item")
    thread = "https://www.reddit.com/r/testsubreddit/comments/abc123/test_post_title/"
    assert item.findtext("link") == thread
    description = item.findtext("description")
    (md,) = (tmp_path / "testsubreddit").glob("*.md")
    archive = f"https://example.com/testsubreddit/{md.name}?token=s3cr%26t"
    assert f'<a href="{archive}">[archived copy]</a>' in description
    # A self post links its thread once, as [comments], not also as [link].
    assert "[link]" not in description
    assert f'<a href="{thread}">[comments]</a>' in description


def test_fetch_subreddit_archive_link_without_token(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "BASE_URL", "https://example.com")
    monkeypatch.setattr(fetcher, "SERVE_TOKEN", "")
    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.top.return_value = [_make_mock_post()]

    fetcher.fetch_subreddit(mock_reddit, "testsubreddit")

    description = ET.parse(tmp_path / "testsubreddit.xml").findtext("./channel/item/description")
    assert '.md">[archived copy]</a>' in description


def test_fetch_subreddit_uses_correct_subreddit_name(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "BASE_URL", "")
    monkeypatch.setattr(fetcher, "TOP_PERIOD", "week")
    monkeypatch.setattr(fetcher, "TOP_LIMIT", 10)

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.top.return_value = [_make_mock_post()]

    fetcher.fetch_subreddit(mock_reddit, "worldnews")

    mock_reddit.subreddit.assert_called_once_with("worldnews")
    assert (tmp_path / "worldnews.xml").exists()
    assert (tmp_path / "worldnews").is_dir()


# ── fetch_subreddits ──────────────────────────────────────────────────────────


def test_fetch_subreddits_skips_when_no_subreddits(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(fetcher, "SUBREDDITS", [])
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    mock_reddit = MagicMock()

    fetcher.fetch_subreddits(mock_reddit)

    assert "SUBREDDITS not set" in caplog.text
    mock_reddit.subreddit.assert_not_called()
    assert not list(tmp_path.glob("*.xml"))


def test_fetch_subreddits_fetches_multiple(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "SUBREDDITS", ["askreddit", "worldnews"])
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "BASE_URL", "")
    monkeypatch.setattr(fetcher, "TOP_PERIOD", "week")
    monkeypatch.setattr(fetcher, "TOP_LIMIT", 5)

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.top.return_value = [_make_mock_post()]

    fetcher.fetch_subreddits(mock_reddit)

    assert (tmp_path / "askreddit.xml").exists()
    assert (tmp_path / "worldnews.xml").exists()


# ── write_health ──────────────────────────────────────────────────────────────


def test_write_health_creates_valid_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))

    fetcher.write_health()

    health = tmp_path / "last-run"
    assert health.exists()
    ts = datetime.fromisoformat(health.read_text())
    assert ts.tzinfo is not None  # must be timezone-aware


# ── run_all ───────────────────────────────────────────────────────────────────


def _set_credentials(monkeypatch, value):
    for name in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USERNAME",
        "REDDIT_PASSWORD",
    ):
        monkeypatch.setattr(fetcher, name, value)


def test_run_all_skips_fetching_without_credentials(tmp_path, monkeypatch, caplog):
    _set_credentials(monkeypatch, "")
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))

    with patch("reddit_rss_fetcher.fetcher.build_reddit_client") as build:
        assert fetcher.run_all() is False

    build.assert_not_called()
    assert "PRAW credentials not set" in caplog.text
    assert not (tmp_path / "last-run").exists()


def test_run_all_shares_one_client(tmp_path, monkeypatch):
    _set_credentials(monkeypatch, "x")
    monkeypatch.setattr(fetcher, "SUBREDDITS", ["python"])
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "BASE_URL", "")
    mock_reddit = MagicMock()
    mock_reddit.front.hot.return_value = [_make_mock_front_post()]
    mock_reddit.subreddit.return_value.top.return_value = [_make_mock_post()]

    with patch("reddit_rss_fetcher.fetcher.build_reddit_client", return_value=mock_reddit) as build:
        assert fetcher.run_all() is True

    build.assert_called_once_with()
    assert (tmp_path / "reddit-front-page.xml").exists()
    assert (tmp_path / "python.xml").exists()
    assert (tmp_path / "last-run").exists()


def test_run_all_continues_on_partial_failure(tmp_path, monkeypatch, caplog):
    """If one step fails, run_all logs it, runs the rest, and reports failure."""
    _set_credentials(monkeypatch, "x")
    monkeypatch.setattr(fetcher, "SUBREDDITS", ["python"])
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "BASE_URL", "")
    mock_reddit = MagicMock()
    mock_reddit.front.hot.side_effect = Exception("network error")
    mock_reddit.subreddit.return_value.top.return_value = [_make_mock_post()]

    with patch("reddit_rss_fetcher.fetcher.build_reddit_client", return_value=mock_reddit):
        assert fetcher.run_all() is False  # must not raise

    assert "network error" in caplog.text
    assert (tmp_path / "python.xml").exists()
    assert not (tmp_path / "last-run").exists()  # health not refreshed


# ── storage and archive pruning ───────────────────────────────────────────────


@pytest.fixture
def fake_bucket(monkeypatch):
    """GCS mode with a mocked client; returns the bucket mock."""
    monkeypatch.setattr(fetcher, "GCS_BUCKET", "feeds")
    monkeypatch.setattr(fetcher, "_gcs_client", None)
    client = MagicMock()
    with patch("google.cloud.storage.Client", return_value=client):
        yield client.bucket.return_value


def test_write_file_uploads_encoded_text_to_gcs(fake_bucket):
    fetcher.write_file("sub.xml", "héllo")

    blob = fake_bucket.blob.return_value
    fake_bucket.blob.assert_called_once_with("sub.xml")
    blob.upload_from_string.assert_called_once_with("héllo".encode(), content_type="application/xml", timeout=60)
    assert blob.cache_control == "public, max-age=43200"


def test_write_file_reuses_the_gcs_client(fake_bucket):
    fetcher.write_file("a", b"1")
    first = fetcher._gcs_client
    fetcher.write_file("b", b"2")
    assert fetcher._gcs_client is first


def test_write_file_writes_bytes_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "GCS_BUCKET", "")
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))

    fetcher.write_file("sub/post.bin", b"\x00\x01")

    assert (tmp_path / "sub" / "post.bin").read_bytes() == b"\x00\x01"


def test_prune_archives_deletes_old_gcs_blobs(fake_bucket, monkeypatch):
    monkeypatch.setattr(fetcher, "ARCHIVE_DAYS", 30)
    now = datetime.now(fetcher.UTC)
    old, new = MagicMock(), MagicMock()
    old.time_created = now - fetcher.timedelta(days=31)
    new.time_created = now - fetcher.timedelta(days=1)
    fake_bucket.list_blobs.return_value = [old, new]

    fetcher._prune_archives("python")

    fake_bucket.list_blobs.assert_called_once_with(prefix="python/")
    old.delete.assert_called_once_with()
    new.delete.assert_not_called()


def test_prune_archives_removes_expired_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "GCS_BUCKET", "")
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "a.md").write_text("x")
    (tmp_path / "python" / "keep.txt").write_text("x")

    # Age 0 days already reaches a 0-day limit.
    monkeypatch.setattr(fetcher, "ARCHIVE_DAYS", 0)
    fetcher._prune_archives("python")

    assert not (tmp_path / "python" / "a.md").exists()
    assert (tmp_path / "python" / "keep.txt").exists()


def test_prune_archives_keeps_recent_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "GCS_BUCKET", "")
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "ARCHIVE_DAYS", 30)
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "a.md").write_text("x")

    fetcher._prune_archives("python")

    assert (tmp_path / "python" / "a.md").exists()


# ── build_reddit_client ───────────────────────────────────────────────────────


def test_build_reddit_client_appends_totp_code(monkeypatch):
    monkeypatch.setattr(fetcher, "REDDIT_PASSWORD", "pw")
    monkeypatch.setattr(fetcher, "REDDIT_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    with (
        patch("reddit_rss_fetcher.fetcher.pyotp.TOTP") as totp,
        patch("reddit_rss_fetcher.fetcher.praw.Reddit") as reddit,
    ):
        totp.return_value.now.return_value = "123456"
        fetcher.build_reddit_client()

    totp.assert_called_once_with("JBSWY3DPEHPK3PXP")
    assert reddit.call_args.kwargs["password"] == "pw:123456"


def test_build_reddit_client_names_the_account_in_the_user_agent(monkeypatch):
    monkeypatch.setattr(fetcher, "REDDIT_USERNAME", "someone")
    with patch("reddit_rss_fetcher.fetcher.praw.Reddit") as reddit:
        fetcher.build_reddit_client()

    assert reddit.call_args.kwargs["user_agent"].endswith(" (by /u/someone)")


def test_build_reddit_client_without_totp(monkeypatch):
    monkeypatch.setattr(fetcher, "REDDIT_PASSWORD", "pw")
    monkeypatch.setattr(fetcher, "REDDIT_TOTP_SECRET", "")
    with patch("reddit_rss_fetcher.fetcher.praw.Reddit") as reddit:
        fetcher.build_reddit_client()

    assert reddit.call_args.kwargs["password"] == "pw"
