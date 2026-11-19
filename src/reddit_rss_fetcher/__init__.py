"""Self-hosted Reddit RSS fetcher and subreddit archiver, and its token-protected server."""

import os


def main() -> None:
    """Console script reddit-rss-fetcher, the image's command.

    MODE=server starts the HTTP server (reddit-rss-server), anything else the
    fetcher (reddit-rss-fetch). Each module is imported only when chosen: the
    server reads GCS_BUCKET and SERVE_TOKEN at import, which the fetcher's
    local mode doesn't set.
    """
    if os.environ.get("MODE", "") == "server":
        from reddit_rss_fetcher.server import main as serve

        serve()
    else:
        from reddit_rss_fetcher.fetcher import main as fetch

        fetch()
