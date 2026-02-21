# Stage 1: Compile MJML email templates
FROM node:22-slim AS email-builder
WORKDIR /build
RUN npm install mjml
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
        "https://github.com/tailwindlabs/tailwindcss/releases/download/v4.1.18/tailwindcss-${TW_ARCH}" && \
    chmod +x /usr/local/bin/tailwindcss
COPY src/tailwind/ src/tailwind/
COPY src/templates/ src/templates/
RUN tailwindcss -i src/tailwind/input.css -o src/static/css/tailwind.css --minify

# Stage 3: Main application
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    libgobject-2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Overwrite hand-written HTML emails with MJML-compiled versions
COPY --from=email-builder /build/src/templates/emails/*.html src/templates/emails/

# Overwrite committed CSS with freshly built Tailwind output
COPY --from=css-builder /build/src/static/css/tailwind.css src/static/css/tailwind.css

# Collect static files
RUN cd src && python manage.py collectstatic --noinput --clear 2>/dev/null || true

# Create non-root user
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

WORKDIR /app/src

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "props.wsgi:application"]
