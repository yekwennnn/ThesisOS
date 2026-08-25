# syntax=docker/dockerfile:1.7

# Production builds should replace this tag with an approved digest, for example
# --build-arg PYTHON_IMAGE=python@sha256:<verified-digest>.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Copy only the files needed to build the Python distribution. This prevents the
# web checkout, local workspaces, credentials, and test artifacts entering the
# image build graph.
COPY pyproject.toml README.md ./
COPY src ./src
COPY schemas ./schemas
COPY prompts ./prompts

RUN python -m pip wheel --wheel-dir /wheels .


FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PATH=/usr/local/bin:/usr/bin:/bin \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    THESISOS_WORKSPACE=/var/lib/thesisos

RUN groupadd --gid "${APP_GID}" thesisos \
    && useradd --uid "${APP_UID}" --gid thesisos \
        --home-dir /home/thesisos --create-home --shell /usr/sbin/nologin thesisos \
    && install -d -o thesisos -g thesisos -m 0750 /var/lib/thesisos \
    && install -d -o thesisos -g thesisos -m 0750 /home/thesisos

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels --no-compile thesisos \
    && rm -rf /wheels

USER ${APP_UID}:${APP_GID}
WORKDIR /var/lib/thesisos

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3); sys.exit(0 if r.status==200 else 1)"]

# One process owns one filesystem workspace. Scale by assigning separate
# workspaces, or first replace local persistence with a concurrency-safe store.
CMD ["python", "-m", "uvicorn", "thesisos.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
