import pytest

from reddit_rss_fetcher import reading


@pytest.fixture(autouse=True)
def pages(monkeypatch):
    """The linked pages tests serve, by URL: the tests never touch the network, and any other page fails to load."""
    served: dict[str, str | None] = {}

    def fetch_page(url):
        if url not in served:
            raise OSError(f"no network in tests: {url}")
        return served[url]

    monkeypatch.setattr(reading, "fetch_page", fetch_page)
    return served
