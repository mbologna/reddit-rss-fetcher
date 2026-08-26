FROM python:3.14-slim@sha256:83ff1d245a3d57d04152252d3ef9cb361494d0b3395abd65a5ebe91c401c8e83
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fetcher.py .
COPY server.py .
COPY run.sh .
RUN chmod +x run.sh
CMD ["/app/run.sh"]
