FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
WORKDIR /app
RUN useradd -r -u 1000 appuser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fetcher.py .
COPY server.py .
COPY run.sh .
RUN chmod +x run.sh && chown -R appuser:appuser /app && mkdir -p /output && chown appuser:appuser /output
USER appuser
CMD ["/app/run.sh"]
