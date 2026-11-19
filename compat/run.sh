#!/bin/sh
# Transitional: the image's command was /app/run.sh before the src/ layout.
# reddit-rss-fetcher reads MODE the same way (server, or the fetcher); this
# keeps a deployment that names /app/run.sh working. Remove once none does.
exec reddit-rss-fetcher "$@"
