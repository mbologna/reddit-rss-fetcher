#!/bin/sh
# Docker entrypoint: start either the fetcher (default) or the HTTP server.
# Set MODE=server to run the FastAPI serving layer.
case "${MODE:-}" in
    server) exec uvicorn server:app --host 0.0.0.0 --port 8080 ;;
    *)      exec python -u fetcher.py ;;
esac
