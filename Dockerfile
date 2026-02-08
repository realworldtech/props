FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Build Tailwind CSS (standalone CLI, no Node.js required)
RUN ARCH="$(dpkg --print-architecture)" && \
    if [ "$ARCH" = "arm64" ]; then TW_ARCH="linux-arm64"; else TW_ARCH="linux-x64"; fi && \
    curl -sLo /usr/local/bin/tailwindcss \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/v4.1.18/tailwindcss-${TW_ARCH}" && \
    chmod +x /usr/local/bin/tailwindcss && \
    tailwindcss -i src/static/css/input.css -o src/static/css/tailwind.css --minify && \
    rm /usr/local/bin/tailwindcss

# Collect static files
RUN cd src && python manage.py collectstatic --noinput --clear 2>/dev/null || true

# Create non-root user
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

WORKDIR /app/src

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "props.wsgi:application"]
