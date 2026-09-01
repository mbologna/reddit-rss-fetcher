FROM python:3.14-slim@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fetcher.py .
COPY server.py .
COPY run.sh .
RUN chmod +x run.sh
CMD ["/app/run.sh"]
