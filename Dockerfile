# syntax=docker/dockerfile:1
# Stage 1: Compile MJML email templates
FROM node:24-slim AS email-builder
WORKDIR /build
RUN npm install mjml@4.18.0
COPY src/templates/emails/mjml/ src/templates/emails/mjml/
RUN for f in src/templates/emails/mjml/[a-z]*.mjml; do \
      npx mjml "$f" -o "src/templates/emails/$(basename ${f%.mjml}.html)"; \
    done

# Stage 2: Build Tailwind CSS (standalone CLI, no Node.js required)
FROM debian:bookworm-slim AS css-builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN ARCH="$(dpkg --print-architecture)" && \
    if [ "$ARCH" = "arm64" ]; then TW_ARCH="linux-arm64"; else TW_ARCH="linux-x64"; fi && \
    curl -sLo /usr/local/bin/tailwindcss \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/tailwindcss-${TW_ARCH}" && \
    chmod +x /usr/local/bin/tailwindcss
COPY src/tailwind/ src/tailwind/
COPY src/templates/ src/templates/
RUN tailwindcss -i src/tailwind/input.css -o src/static/css/tailwind.css --minify

# Stage 3: Main application
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

ARG APP_VERSION=dev
ARG GIT_COMMIT=unknown
ENV APP_VERSION=${APP_VERSION}

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    libglib2.0-0t64 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies with uv, straight into /usr/local (no venv).
# uv and its cache are mounted at build time only so they never enter a layer.
COPY pyproject.toml uv.lock ./
ARG INSTALL_DEV=false
RUN --mount=type=bind,from=ghcr.io/astral-sh/uv:0.12,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    if [ "$INSTALL_DEV" = "true" ]; then uv sync --frozen; else uv sync --frozen --no-dev; fi

# Copy project files
COPY . .

# Overwrite hand-written HTML emails with MJML-compiled versions
COPY --from=email-builder /build/src/templates/emails/*.html src/templates/emails/

# Overwrite committed CSS with freshly built Tailwind output
COPY --from=css-builder /build/src/static/css/tailwind.css src/static/css/tailwind.css

# Collect static files (uses dev defaults for SECRET_KEY/DATABASE)
RUN cd src && python manage.py collectstatic --noinput --clear

# Create non-root user
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

WORKDIR /app/src

LABEL org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/realworldtech/props" \
      org.opencontainers.image.revision="${GIT_COMMIT}"

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "props.wsgi:application"]
