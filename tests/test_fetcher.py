import xml.etree.ElementTree as ET
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import fetcher


# ── fetch_front_page ──────────────────────────────────────────────────────────


def test_fetch_front_page_skips_when_no_credentials(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(fetcher, "FEED_ID", "")
    monkeypatch.setattr(fetcher, "REDDIT_USER", "")
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))

    fetcher.fetch_front_page()

    assert not (tmp_path / "reddit-front-page.xml").exists()
    assert "skipping front page" in caplog.text


def test_fetch_front_page_writes_xml(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "FEED_ID", "testtoken")
    monkeypatch.setattr(fetcher, "REDDIT_USER", "testuser")
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))

    mock_response = MagicMock()
    mock_response.text = "<rss><channel><title>Test</title></channel></rss>"
    mock_response.raise_for_status = MagicMock()

    with patch("fetcher.requests.get", return_value=mock_response) as mock_get:
        fetcher.fetch_front_page()

    out = tmp_path / "reddit-front-page.xml"
    assert out.exists()
    assert out.read_text() == "<rss><channel><title>Test</title></channel></rss>"

    call_url = mock_get.call_args[0][0]
    assert "testtoken" in call_url
    assert "testuser" in call_url
    assert "limit=10" in call_url


def test_fetch_front_page_propagates_http_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "FEED_ID", "testtoken")
    monkeypatch.setattr(fetcher, "REDDIT_USER", "testuser")
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("HTTP 429")

    with patch("fetcher.requests.get", return_value=mock_response):
        with pytest.raises(Exception, match="HTTP 429"):
            fetcher.fetch_front_page()


# ── fetch_subreddit ───────────────────────────────────────────────────────────


def _make_mock_post(
    post_id="abc123",
    title="Test Post Title",
    url="https://reddit.com/r/testsubreddit/comments/abc123",
    selftext="Post body content",
    created_utc=1700000000.0,
    author="testuser",
):
    mock_post = MagicMock()
    mock_post.id = post_id
    mock_post.title = title
    mock_post.url = url
    mock_post.selftext = selftext
    mock_post.created_utc = created_utc
    mock_post.author = author
    return mock_post


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


def test_fetch_subreddits_skips_when_no_credentials(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(fetcher, "REDDIT_CLIENT_ID", "")
    monkeypatch.setattr(fetcher, "REDDIT_CLIENT_SECRET", "")
    monkeypatch.setattr(fetcher, "REDDIT_USERNAME", "")
    monkeypatch.setattr(fetcher, "REDDIT_PASSWORD", "")
    monkeypatch.setattr(fetcher, "SUBREDDITS", ["askreddit"])
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))

    fetcher.fetch_subreddits()

    assert "PRAW credentials not set" in caplog.text
    assert not list(tmp_path.glob("*.xml"))


def test_fetch_subreddits_skips_when_no_subreddits(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(fetcher, "REDDIT_CLIENT_ID", "id")
    monkeypatch.setattr(fetcher, "REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setattr(fetcher, "REDDIT_USERNAME", "user")
    monkeypatch.setattr(fetcher, "REDDIT_PASSWORD", "pass")
    monkeypatch.setattr(fetcher, "SUBREDDITS", [])
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))

    fetcher.fetch_subreddits()

    assert "SUBREDDITS not set" in caplog.text
    assert not list(tmp_path.glob("*.xml"))


def test_fetch_subreddits_fetches_multiple(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher, "REDDIT_CLIENT_ID", "id")
    monkeypatch.setattr(fetcher, "REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setattr(fetcher, "REDDIT_USERNAME", "user")
    monkeypatch.setattr(fetcher, "REDDIT_PASSWORD", "pass")
    monkeypatch.setattr(fetcher, "SUBREDDITS", ["askreddit", "worldnews"])
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "BASE_URL", "")
    monkeypatch.setattr(fetcher, "TOP_PERIOD", "week")
    monkeypatch.setattr(fetcher, "TOP_LIMIT", 5)

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value.top.return_value = [_make_mock_post()]

    with patch("fetcher.build_reddit_client", return_value=mock_reddit):
        fetcher.fetch_subreddits()

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


def test_run_all_continues_on_partial_failure(tmp_path, monkeypatch, caplog):
    """If one fetcher fails, run_all logs the error and continues."""
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(fetcher, "FEED_ID", "tok")
    monkeypatch.setattr(fetcher, "REDDIT_USER", "u")

    with patch("fetcher.requests.get", side_effect=Exception("network error")):
        fetcher.run_all()  # must not raise

    assert "network error" in caplog.text
    assert (tmp_path / "last-run").exists()  # health still written
