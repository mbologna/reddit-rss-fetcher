#!/usr/bin/env python3
"""
HTTP server for the reddit-rss-fetcher output.

Reads files from the private GCS bucket and serves them with token auth.
/health is unauthenticated: 200, or 503 once the last good cycle is older
than STALE_AFTER_HOURS (or there is none).
POST /fetch runs a fetch cycle with its own token, FETCH_TOKEN, so whatever
triggers fetches can't read the feeds; 500 if a step failed.

Usage:
    SERVE_TOKEN=<secret> GCS_BUCKET=reddit.michelebologna.net uvicorn server:app --host 0.0.0.0 --port 8080
"""

import os
import secrets
import threading
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from google.cloud import storage
from google.cloud.exceptions import NotFound
from pydantic import BaseModel

GCS_BUCKET = os.environ["GCS_BUCKET"]
SERVE_TOKEN = os.environ["SERVE_TOKEN"]
# Unset: /fetch is disabled. Never falls back to SERVE_TOKEN, the read token.
FETCH_TOKEN = os.environ.get("FETCH_TOKEN", "")
# Three missed 2-hourly cycles: one or two failures (Reddit hiccups) are no
# alert, three in a row are.
STALE_AFTER = timedelta(hours=float(os.environ.get("STALE_AFTER_HOURS", "7")))

app = FastAPI(docs_url=None, redoc_url=None)

_gcs_client = None
_fetch_lock = threading.Lock()


def _gcs():
    global _gcs_client
    if _gcs_client is None:
        _gcs_client = storage.Client()
    return _gcs_client


def _read_blob(path: str) -> tuple[bytes, str]:
    """Download a GCS object, raising 404 if it does not exist."""
    try:
        blob = _gcs().bucket(GCS_BUCKET).blob(path)
        content = blob.download_as_bytes(timeout=10)
        ct = blob.content_type or "application/octet-stream"
        return content, ct
    except NotFound:
        raise HTTPException(status_code=404, detail="Not found")


class FetchRequest(BaseModel):
    token: str = ""


@app.post("/fetch")
def trigger_fetch(req: FetchRequest):
    """Trigger a new fetch cycle. Requires FETCH_TOKEN."""
    if not FETCH_TOKEN:
        raise HTTPException(
            status_code=503, detail="Fetch disabled: FETCH_TOKEN is not set"
        )
    if not secrets.compare_digest(req.token.encode(), FETCH_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not _fetch_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Fetch already in progress")
    try:
        from fetcher import run_all

        ok = run_all()
    finally:
        _fetch_lock.release()
    if not ok:
        # The caller (a cron) records the run as failed; the logs say which step.
        raise HTTPException(status_code=500, detail="Fetch cycle failed")
    return Response(status_code=200)


def _health(code: int, **body) -> JSONResponse:
    return JSONResponse(body, status_code=code, headers={"Cache-Control": "no-store"})


# HEAD too: UptimeRobot probes with HEAD first.
@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    """200 when the last good fetch cycle is recent (no auth), 503 otherwise."""
    try:
        content, _ = _read_blob("last-run")
    except HTTPException:
        return _health(503, status="down", problems=["no good fetch cycle yet"])
    last_run = datetime.fromisoformat(content.decode().strip())
    age = datetime.now(UTC) - last_run
    if age > STALE_AFTER:
        hours = age.total_seconds() / 3600
        return _health(
            503,
            status="down",
            last_run=last_run.isoformat(),
            problems=[f"last good fetch {hours:.1f} hours ago"],
        )
    return _health(200, status="ok", last_run=last_run.isoformat())


@app.get("/{path:path}")
def serve(path: str, token: str = ""):
    """Serve a feed file from GCS. Requires ?token=<SERVE_TOKEN>."""
    if not secrets.compare_digest(token.encode(), SERVE_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Invalid token")
    # Map extension-less paths to .xml (Feedly subscribes to reddit-front-page)
    filename = path.rsplit("/", 1)[-1]
    gcs_path = path if "." in filename else path + ".xml"
    content, ct = _read_blob(gcs_path)
    return Response(content=content, media_type=ct)
