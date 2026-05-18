#!/usr/bin/env python3
"""
HTTP server for the reddit-rss-fetcher output.

Reads files from the private GCS bucket and serves them with token auth.
The /last-run endpoint is unauthenticated (used as a health check).

Usage:
    SERVE_TOKEN=<secret> GCS_BUCKET=reddit.michelebologna.net uvicorn server:app --host 0.0.0.0 --port 8080
"""

import os
import secrets

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from google.cloud import storage
from google.cloud.exceptions import NotFound

GCS_BUCKET = os.environ["GCS_BUCKET"]
SERVE_TOKEN = os.environ["SERVE_TOKEN"]

app = FastAPI(docs_url=None, redoc_url=None)

_gcs_client = None


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


@app.get("/last-run")
def health():
    """Return the UTC timestamp of the last successful fetch cycle (no auth)."""
    content, _ = _read_blob("last-run")
    return Response(content=content, media_type="text/plain; charset=utf-8")


@app.get("/{path:path}")
def serve(path: str, token: str = ""):
    """Serve a feed file from GCS. Requires ?token=<SERVE_TOKEN>."""
    if not secrets.compare_digest(token.encode(), SERVE_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Invalid token")
    # Map extension-less paths to .xml (mirrors the nginx try_files behaviour)
    filename = path.rsplit("/", 1)[-1]
    gcs_path = path if "." in filename else path + ".xml"
    content, ct = _read_blob(gcs_path)
    return Response(content=content, media_type=ct)
