FROM python:3.15.2-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build

# uv from PyPI, hash-checked (requirements-uv.txt says why). Used in this
# stage only: the runtime stage gets the finished virtualenv.
COPY requirements-uv.txt /tmp/
RUN pip install --no-cache-dir --root-user-action=ignore --require-hashes -r /tmp/requirements-uv.txt
# Install exactly what uv.lock pins, checking its hashes; --locked fails the
# build if uv.lock no longer matches pyproject.toml. The project itself is not
# a package (package = false), so only its dependencies land in the venv.
# Bytecode is compiled here because the runtime image sets
# PYTHONDONTWRITEBYTECODE.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON=/usr/local/bin/python3 UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1
WORKDIR /tmp/build
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-editable

FROM python:3.15.2-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
COPY --from=build /opt/venv /opt/venv
# run.sh's python and uvicorn are the venv's.
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# The base image's pip (with the packages it vendors) and ensurepip's
# bundled pip wheel are never used at run time; removing them keeps their
# CVEs out of the image.
RUN /usr/local/bin/python3 -m pip uninstall -y --root-user-action=ignore pip \
    && rm -rf /usr/local/lib/python3*/ensurepip
WORKDIR /app
# A fixed UID and GID (useradd -r alone picks a system GID), so volumes and
# Kubernetes' runAsNonRoot see the same numbers on every build. The code is
# root-owned and read-only for the app user; only /output (local mode) is
# writable.
RUN groupadd -g 1000 appuser && useradd -r -u 1000 -g 1000 appuser \
    && mkdir -p /output && chown 1000:1000 /output
COPY --chmod=0755 fetcher.py server.py run.sh ./
USER 1000:1000
CMD ["/app/run.sh"]
