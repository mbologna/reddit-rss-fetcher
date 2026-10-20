FROM python:3.15-slim@sha256:2da676046630d7bf14c6764ae1143dc7fa0377a53f94bf5a642f0b214aca5bce
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
