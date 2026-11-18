import xml.etree.ElementTree as ET
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import fetcher

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
):
    post = MagicMock()
    post.id = post_id
    post.fullname = f"t3_{post_id}"
    post.title = title
    post.url = url
    post.permalink = permalink
    post.selftext = "Body & more"
    post.selftext_html = selftext_html
    post.created_utc = created_utc
    post.author = author
    post.subreddit.display_name = subreddit
    return post


def test_fetch_front_page_writes_rss_from_api(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "FRONT_PAGE_LIMIT", 10)
    mock_reddit = MagicMock()
    mock_reddit.front.hot.return_value = [
        _make_mock_front_post(),
        _make_mock_front_post(post_id="second", title="Second", selftext_html=None),
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
    assert (
        first.findtext("link")
        == "https://www.reddit.com/r/python/comments/xyz789/front_page_post/"
    )
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

    description = ET.parse(tmp_path / "testsubreddit.xml").findtext(
        "./channel/item/description"
    )
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

    with patch("fetcher.build_reddit_client") as build:
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

    with patch("fetcher.build_reddit_client", return_value=mock_reddit) as build:
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

    with patch("fetcher.build_reddit_client", return_value=mock_reddit):
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
    blob.upload_from_string.assert_called_once_with(
        "héllo".encode(), content_type="application/xml", timeout=60
    )
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
        patch("fetcher.pyotp.TOTP") as totp,
        patch("fetcher.praw.Reddit") as reddit,
    ):
        totp.return_value.now.return_value = "123456"
        fetcher.build_reddit_client()

    totp.assert_called_once_with("JBSWY3DPEHPK3PXP")
    assert reddit.call_args.kwargs["password"] == "pw:123456"


def test_build_reddit_client_without_totp(monkeypatch):
    monkeypatch.setattr(fetcher, "REDDIT_PASSWORD", "pw")
    monkeypatch.setattr(fetcher, "REDDIT_TOTP_SECRET", "")
    with patch("fetcher.praw.Reddit") as reddit:
        fetcher.build_reddit_client()

    assert reddit.call_args.kwargs["password"] == "pw"
