"""The console scripts: the MODE dispatch and the two modes' entry points."""

import os
from unittest.mock import patch

os.environ.setdefault("GCS_BUCKET", "test-bucket")
os.environ.setdefault("SERVE_TOKEN", "test-token")

import pytest

import reddit_rss_fetcher
from reddit_rss_fetcher import fetcher, server


def test_mode_server_starts_the_server(monkeypatch):
    monkeypatch.setenv("MODE", "server")
    with patch("reddit_rss_fetcher.server.main") as serve, patch("reddit_rss_fetcher.fetcher.main") as fetch:
        reddit_rss_fetcher.main()
    serve.assert_called_once_with()
    fetch.assert_not_called()


@pytest.mark.parametrize("mode", [None, "", "fetcher"])
def test_any_other_mode_starts_the_fetcher(monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv("MODE", raising=False)
    else:
        monkeypatch.setenv("MODE", mode)
    with patch("reddit_rss_fetcher.server.main") as serve, patch("reddit_rss_fetcher.fetcher.main") as fetch:
        reddit_rss_fetcher.main()
    fetch.assert_called_once_with()
    serve.assert_not_called()


@pytest.mark.parametrize(("port", "expected"), [(None, 8080), ("9090", 9090)])
def test_server_listens_on_port(monkeypatch, port, expected):
    if port is None:
        monkeypatch.delenv("PORT", raising=False)
    else:
        monkeypatch.setenv("PORT", port)
    with patch("uvicorn.run") as run:
        server.main()
    run.assert_called_once_with(server.app, host="0.0.0.0", port=expected)


def test_fetcher_runs_one_cycle_in_gcs_mode(monkeypatch):
    monkeypatch.setattr(fetcher, "GCS_BUCKET", "test-bucket")
    with patch.object(fetcher, "run_all") as run_all:
        fetcher.main()
    run_all.assert_called_once_with()


def test_fetcher_loops_in_local_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "GCS_BUCKET", "")
    monkeypatch.setattr(fetcher, "OUTPUT_DIR", str(tmp_path / "output"))

    class Stop(Exception):
        pass

    with (
        patch.object(fetcher, "run_all") as run_all,
        patch.object(fetcher.time, "sleep", side_effect=[None, Stop]) as sleep,
        pytest.raises(Stop),
    ):
        fetcher.main()
    assert (tmp_path / "output").is_dir()
    assert run_all.call_count == 2
    sleep.assert_called_with(fetcher.FETCH_INTERVAL_HOURS * 3600)
