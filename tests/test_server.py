import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("GCS_BUCKET", "test-bucket")
os.environ.setdefault("SERVE_TOKEN", "test-token")

import pytest
from fastapi.testclient import TestClient
from google.cloud.exceptions import NotFound

import server

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def _reset_lock():
    """Guard against a stuck lock leaking between tests."""
    yield
    if server._fetch_lock.locked():
        server._fetch_lock.release()


def _mock_blob(content=b"<rss></rss>", content_type="application/xml", found=True):
    blob = MagicMock()
    if found:
        blob.download_as_bytes.return_value = content
        blob.content_type = content_type
    else:
        blob.download_as_bytes.side_effect = NotFound("missing")
    return blob


# ── GET /health (no auth) ─────────────────────────────────────────────────


def _get_health(timestamp=None, method="GET"):
    mock_bucket = MagicMock()
    if timestamp is None:
        mock_bucket.blob.return_value = _mock_blob(found=False)
    else:
        mock_bucket.blob.return_value = _mock_blob(content=timestamp.encode())
    with patch.object(server, "_gcs") as mock_gcs:
        mock_gcs.return_value.bucket.return_value = mock_bucket
        return client.request(method, "/health")


def test_health_requires_no_token():
    recent = datetime.now(UTC) - timedelta(hours=1)

    resp = _get_health(recent.isoformat())

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "last_run": recent.isoformat()}
    assert resp.headers["cache-control"] == "no-store"


def test_health_answers_head():
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    assert _get_health(recent, method="HEAD").status_code == 200


def test_health_is_503_when_stale():
    old = (datetime.now(UTC) - timedelta(hours=8)).isoformat()

    resp = _get_health(old)

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "down"
    assert body["problems"] == ["last good fetch 8.0 hours ago"]


def test_health_is_503_without_a_good_cycle():
    resp = _get_health(None)

    assert resp.status_code == 503
    assert resp.json() == {"status": "down", "problems": ["no good fetch cycle yet"]}


def test_last_run_is_gone():
    # The old health endpoint: now just a path the token-gated catch-all refuses.
    assert client.get("/last-run").status_code == 401


# ── GET /{path} ─────────────────────────────────────────────────────────────


def test_serve_rejects_missing_token():
    resp = client.get("/reddit-front-page")
    assert resp.status_code == 401


def test_serve_rejects_invalid_token():
    resp = client.get("/reddit-front-page?token=wrong")
    assert resp.status_code == 401


def test_serve_accepts_valid_token_and_returns_content():
    mock_blob = _mock_blob(content=b"<rss>ok</rss>", content_type="application/xml")
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    with patch.object(server, "_gcs") as mock_gcs:
        mock_gcs.return_value.bucket.return_value = mock_bucket
        resp = client.get(f"/reddit-front-page.xml?token={server.SERVE_TOKEN}")

    assert resp.status_code == 200
    assert resp.content == b"<rss>ok</rss>"


def test_serve_maps_extensionless_path_to_xml():
    mock_blob = _mock_blob()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    with patch.object(server, "_gcs") as mock_gcs:
        mock_gcs.return_value.bucket.return_value = mock_bucket
        resp = client.get(f"/reddit-front-page?token={server.SERVE_TOKEN}")

    assert resp.status_code == 200
    mock_bucket.blob.assert_called_once_with("reddit-front-page.xml")


def test_serve_returns_404_for_missing_blob():
    mock_blob = _mock_blob(found=False)
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    with patch.object(server, "_gcs") as mock_gcs:
        mock_gcs.return_value.bucket.return_value = mock_bucket
        resp = client.get(f"/worldnews.xml?token={server.SERVE_TOKEN}")

    assert resp.status_code == 404


# ── POST /fetch ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fetch_token(monkeypatch):
    monkeypatch.setattr(server, "FETCH_TOKEN", "fetch-token")


def test_fetch_is_disabled_without_fetch_token(monkeypatch):
    monkeypatch.setattr(server, "FETCH_TOKEN", "")
    resp = client.post("/fetch", json={"token": ""})
    assert resp.status_code == 503


def test_fetch_refuses_the_serve_token():
    resp = client.post("/fetch", json={"token": server.SERVE_TOKEN})
    assert resp.status_code == 401


def test_fetch_rejects_invalid_token():
    resp = client.post("/fetch", json={"token": "wrong"})
    assert resp.status_code == 401


def test_fetch_triggers_run_all_with_valid_token():
    with patch("fetcher.run_all", return_value=True) as mock_run_all:
        resp = client.post("/fetch", json={"token": server.FETCH_TOKEN})

    assert resp.status_code == 200
    mock_run_all.assert_called_once()


def test_fetch_returns_500_when_a_step_failed():
    with patch("fetcher.run_all", return_value=False):
        resp = client.post("/fetch", json={"token": server.FETCH_TOKEN})

    assert resp.status_code == 500
    assert not server._fetch_lock.locked()


def test_fetch_returns_409_when_already_in_progress():
    server._fetch_lock.acquire()
    try:
        resp = client.post("/fetch", json={"token": server.FETCH_TOKEN})
    finally:
        server._fetch_lock.release()

    assert resp.status_code == 409
