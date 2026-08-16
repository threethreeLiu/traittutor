# syntax=docker/dockerfile:1

FROM --platform=$BUILDPLATFORM node:22-slim AS frontend-builder
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm config set fetch-timeout 600000 \
    && npm config set fetch-retries 5 \
    && npm ci --legacy-peer-deps
COPY web/ ./
COPY traittutor/__version__.py /app/traittutor/__version__.py
RUN npm run build

FROM node:22-slim AS node-runtime

FROM python:3.11-slim AS backend-builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl git libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
COPY pyproject.toml README.md ./
COPY traittutor/ ./traittutor/
RUN pip install --prefix=/install .

FROM python:3.11-slim AS production
LABEL org.opencontainers.image.title="TraitTutor" \
      org.opencontainers.image.description="AI learning coach"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    NODE_ENV=production \
    TRAITTUTOR_IGNORE_PROCESS_ENV_OVERRIDES=1 \
    TRAITTUTOR_PAGE_SCHEMA_CSP=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates curl supervisor \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=backend-builder /install/ /usr/local/
COPY --from=frontend-builder /app/web/.next/standalone/ ./web/
COPY --from=frontend-builder /app/web/.next/static/ ./web/.next/static/
COPY --from=frontend-builder /app/web/public/ ./web/public/
COPY traittutor/ ./traittutor/
COPY scripts/ ./scripts/
COPY pyproject.toml ./
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md ./

RUN groupadd --system --gid 1000 traittutor \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin traittutor \
    && mkdir -p /app/data /etc/supervisor/conf.d \
    && install -m 755 scripts/container/entrypoint.sh /app/entrypoint.sh \
    && install -m 755 scripts/container/start-backend.sh /app/start-backend.sh \
    && install -m 755 scripts/container/start-frontend.sh /app/start-frontend.sh \
    && install -m 644 scripts/container/supervisord.conf /etc/supervisor/supervisord.conf \
    && chown -R traittutor:traittutor /app/data /app/web/.next

EXPOSE 8001 3782
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "/app/scripts/container/healthcheck.py"]
ENTRYPOINT ["/app/entrypoint.sh"]
