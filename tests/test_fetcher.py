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
