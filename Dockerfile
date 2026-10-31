FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8
WORKDIR /app
RUN useradd -r -u 1000 appuser
# uv from PyPI, hash-checked (requirements-uv.txt says why).
COPY requirements-uv.txt /tmp/
RUN pip install --no-cache-dir --root-user-action=ignore --require-hashes -r /tmp/requirements-uv.txt
COPY pyproject.toml uv.lock ./
# Install the locked runtime dependencies with their hashes; --locked fails
# the build if uv.lock no longer matches pyproject.toml
RUN uv export --quiet --locked --no-dev --no-emit-project --no-header -o /tmp/requirements.txt \
    && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY fetcher.py .
COPY server.py .
COPY run.sh .
RUN chmod +x run.sh && chown -R appuser:appuser /app && mkdir -p /output && chown appuser:appuser /output
USER appuser
CMD ["/app/run.sh"]
