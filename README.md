# PROPS

A self-hosted asset tracking platform for community organisations — manage props, costumes, lights, sound gear, set pieces, and more across multiple storage locations.

Built with Django, HTMX, and S3-compatible object storage.

## Features

- **Asset management** — categories, locations, custom tags, and full transaction history
- **Barcode & NFC** — auto-generated Code128 barcodes, camera scanning, NFC tag support (Android + iOS)
- **Check-in/check-out** — track who has what, with timestamped audit trail
- **Stocktake** — bulk verification of assets at a location
- **AI-powered capture** — optional image analysis for automatic categorisation (Claude API)
- **Zebra label printing** — direct printing to thermal label printers via ZPL
- **Self-hosted S3 storage** — uses Garage for zero-dependency object storage, or any S3-compatible provider
- **Multi-tenant branding** — configurable site name, colours, and barcode prefix
- **Mobile-first UI** — responsive design with Tailwind CSS + HTMX
- **Background tasks** — Celery + Redis for async operations

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Django 5.x, Python 3.12 |
| Database | PostgreSQL 17 |
| Frontend | Django templates, HTMX, Tailwind CSS |
| Object storage | Garage (S3-compatible) |
| Task queue | Celery + Redis |
| Deployment | Docker Compose, Traefik v3.6 |

## Quick Start

### 1. Clone and bootstrap

```bash
git clone git@github.com:realworldtech/props.git
cd props
cp .env.example .env
./bootstrap.sh
```

The bootstrap script starts Garage, creates the S3 bucket, and populates your `.env` with credentials.

### 2. Start the stack

```bash
# Development (hot reload, port 8003)
docker compose --profile dev up -d

# Production (Traefik with auto-SSL)
docker compose --profile prod up -d
```

### 3. Create an admin user

```bash
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py setup_groups
```

Access the application at http://localhost:8003 (dev) or your configured domain (prod).

## Configuration

Copy `.env.example` to `.env` and configure. Key variables:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key (generate for production) |
| `DATABASE_URL` | PostgreSQL connection string |
| `USE_S3` | Enable S3 storage (`True`/`False`) |
| `SITE_NAME` | Display name for the application |
| `BARCODE_PREFIX` | Prefix for generated barcodes |
| `ANTHROPIC_API_KEY` | Optional — enables AI image analysis |
| `ZEBRA_PRINTER_HOST` | Optional — enables direct label printing |
| `DOMAIN` | Production domain (for Traefik SSL) |

See `.env.example` for the full list with descriptions.

## User Roles

| Role | Capabilities |
|------|-------------|
| Admin | Full access — manage users, settings, all assets |
| Member | View assets, check-in/check-out, create assets |
| Viewer | Read-only access |

## NFC Support

- **Android** (Chrome 89+): Web NFC API works directly over HTTPS
- **iOS**: Program NFC tags with NDEF URL records pointing to `https://yourdomain.com/a/{barcode}/`

## Development

### Running tests

```bash
pytest
# or inside Docker:
docker compose exec web pytest
```

### Code style

```bash
black src/
isort src/
flake8 src/
```

Configuration is in `pyproject.toml`.

## License

Copyright (C) 2024-2026 Real World Technology Solutions

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU Affero General Public License** as published by the Free Software Foundation, version 3.

See [LICENSE](LICENSE) for the full text.

### Commercial Licensing

If you require a license that permits proprietary modifications or want to use PROPS without the AGPL-3.0 obligations, contact us at sales@rwts.com.au for commercial licensing options.
