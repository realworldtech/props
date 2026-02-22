# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PROPS is a self-hosted asset tracking platform for community organizations, built with Django. It manages props, costumes, gear, and equipment across storage locations with barcode/NFC scanning, AI-powered image analysis, and a check-in/check-out workflow. Licensed AGPL-3.0 by Real World Technology Solutions.

## Development Commands

```bash
# Bootstrap (creates .env, starts Garage S3, populates credentials)
./bootstrap.sh

# Start dev stack (hot reload, port 8003)
docker compose --profile dev up -d

# Start production stack (Traefik with auto-SSL)
docker compose --profile prod up -d

# Run tests (from repo root, use the venv)
.venv/bin/pytest

# Run tests inside Docker
docker compose exec web pytest

# Run a single test
pytest src/assets/tests.py::TestClassName::test_method_name

# Coverage
coverage run -m pytest && coverage report

# Code formatting
black src/
isort src/
flake8 src/

# Django management (inside Docker)
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py setup_groups
```

## Architecture

### Django Apps

- **`props/`** — Project config (settings, root URLs, WSGI/ASGI, context processors, custom S3 storage backend)
- **`accounts/`** — Custom user model (`CustomUser`) with email-based auth, registration with admin approval workflow, permission groups
- **`assets/`** — Core app: asset CRUD, scanning, check-in/out, stocktake, AI analysis, labels, export

### Services Layer (`assets/services/`)

Business logic is extracted into service modules rather than living in views:
- `ai.py` — Claude API image analysis (async via Celery)
- `barcode.py` — Code128 barcode generation
- `zebra.py` — ZPL label printing to Zebra printers
- `transactions.py` — Asset movement/checkout logic
- `merge.py` — Asset deduplication
- `export.py` — Excel export
- `bulk.py` — Bulk operations
- `permissions.py` — Permission checks
- `state.py` — Asset state management

### Key Patterns

- **HTMX + Django templates** for frontend interactivity (no SPA framework)
- **Tailwind CSS** for styling, **Alpine.js** via django-unfold
- **django-unfold** for admin UI theming (see Unfold Sidebar rule below)
- **Celery + Redis** for async tasks (image analysis in `assets/tasks.py`)
- **Garage** as S3-compatible object storage; media served via Django proxy view (`props/views.py:media_proxy`)
- **WhiteNoise** for static file serving
- Custom auth backend: `accounts.backends.EmailOrUsernameBackend`
- Unified asset lookup at `/a/<identifier>/` resolves both barcodes and NFC tags

### Unfold Sidebar Navigation

The Unfold sidebar has `show_all_applications: False`, which means **only models explicitly listed in the `UNFOLD["SIDEBAR"]["navigation"]` config in `settings.py` are visible in the admin**. When registering a new model with `@admin.register()`, you **must** also add a corresponding entry to the sidebar navigation — otherwise the model will be invisible to admins. Always audit the navigation list against registered models when adding or removing admin registrations.

### Permission Groups

Created by `setup_groups` management command: System Admin, Department Manager, Member, Viewer, Borrower.

### Docker Services

- `db` (PostgreSQL 17), `redis` (Redis 7), `garage` (S3 storage)
- `web`/`web-prod` (Django via Gunicorn)
- `celery-worker`, `celery-beat` (background tasks)
- `traefik` (prod profile only, reverse proxy with Let's Encrypt)

Profiles: `dev` (direct port 8003 access) and `prod` (behind Traefik).

### Configuration

All environment-specific values come from `.env` (see `.env.example`). Key variables:
- `DATABASE_URL`, `SECRET_KEY`, `DEBUG`
- `AWS_*` for S3/Garage storage
- `ANTHROPIC_API_KEY`, `AI_MODEL_NAME` for AI features
- `SITE_NAME`, `BARCODE_PREFIX`, `BRAND_PRIMARY_COLOR` for branding
- `ZEBRA_PRINTER_HOST` for label printing
- `EMAIL_HOST`, `DEFAULT_FROM_EMAIL` for email

Never hardcode domains, email addresses, or deployment-specific values — always use env vars or Django settings.

## Specification-Driven Development

This project follows a strict spec-first workflow. The specification documents may live in a separate private repository but be included in the working tree (e.g. as a git submodule or symlinked `docs/spec/` directory).

### Issues Before Implementation

When a user requests a change, feature, or improvement (e.g. "hey, can we do X?"), **encourage them to create a GitHub issue first** before jumping into code. Suggest it — don't gate on it.

1. **Start a brief discovery conversation** to understand the request. Ask about:
   - What they want and why (the use case / problem being solved)
   - Whether this is a bug, a new feature, or a change to existing behaviour
   - Whether they think it fits within current spec, extends it, or changes it (mirrors the feature request template's "Spec Consideration" section)
   - Any relevant context — affected areas of the app, edge cases, urgency
2. **Suggest creating an issue** using the repo's templates (`.github/ISSUE_TEMPLATE/`):
   - *Bug Report* — description, reproduction steps, expected vs actual, environment, logs
   - *Feature Request* — description, use case, proposed approach, spec consideration
3. **Offer to draft and file it** via `gh issue create`, or present it for the user to file manually.
4. If the user says "nah, just do it" — that's fine, proceed directly. The goal is to encourage the habit, not block progress.

Changes become issues before they become changes — but the user always has the final say.

### Preferred Skills

- **`/implement`** — Use for all implementation work. Plans from the spec, tracks progress, and verifies against requirements. Available at: https://github.com/realworldtech/claude-implement-skill
- **`/spec`** — Use for spec authoring and review (internal skill, not published).

Always prefer these skills over ad-hoc implementation when the spec is available.

### Rules

1. **Never break MoSCoW priorities.** If the spec marks a requirement as Must/Should/Could/Won't, implementation must respect that classification. Do not implement Won't items or deprioritise Must items.
2. **Use `/spec` and `/implement` skills** for all implementation work when the spec is available. Verify requirements against the spec before writing code.
3. **Every change must be compared against the spec.** Before implementing a new feature, change, or PR-requested modification:
   - Read the relevant spec sections
   - Determine whether the request is: (a) already covered by the spec, (b) a modification to an existing spec requirement, (c) a conflict with the spec, or (d) entirely new scope
   - **Always ask the user** which category it falls into — do not silently assume
4. **Spec-first, then code.** If a requested change conflicts with or extends the spec, the spec must be updated and approved before implementation proceeds. The spec is the source of truth.
5. **PR and issue review.** When reviewing PRs, issues, or feature requests, compare the proposed changes against the spec and identify where they align, conflict, or introduce new scope. Present this analysis to the user before taking action.

### Spec Location

Spec documents are expected at `docs/spec/` in the working tree. If the spec repo is not present, ask the user before proceeding with any implementation work that could conflict with undocumented requirements.

## Code Style

- **black** (line-length 79, target py312), **isort** (profile black), **flake8**
- Config in `pyproject.toml`
- isort sections: FUTURE, STDLIB, THIRDPARTY, DJANGO, FIRSTPARTY, LOCALFOLDER

## Testing

- pytest with `pytest-django`; config in `pyproject.toml` (`DJANGO_SETTINGS_MODULE = "props.settings"`)
- **Test layout** (reorganised Feb 2026):
  - `src/assets/tests/` — 12 feature-grouped unit/integration test files
  - `src/accounts/tests/` — 4 files (auth, registration, profile, admin)
  - `src/props/tests/` — 3 files (branding, infrastructure, non-functional)
  - `src/assets/tests/functional/` — behavioural tests derived from S10/S11/S12 spec sections
- Shared fixtures in `src/conftest.py` — provides `user`, `admin_user`, `member_user`, `viewer_user`, `client_logged_in`, `admin_client`, `dept_manager_client`, `department`, `category`, `location`, `asset`, hold list fixtures, etc.
- Functional test fixtures in `src/assets/tests/functional/conftest.py` — scenario-level fixtures (`active_asset`, `borrower_user`, `warehouse`, `serialised_asset_with_units`, `kit_with_components`, etc.)
- Tests use local filesystem storage (S3 overridden in conftest.py)
- Target 80%+ test coverage
- **Test-driven development is mandatory.** For every change: (1) write the test first, (2) run it with `pytest` and verify it fails, (3) implement the code, (4) run the test again and verify it passes. Do not skip the red-green cycle — the failing test must be executed, not just written.
- **Tests must pass in both environments.** After implementation, run `pytest` locally (venv) and also inside Docker with `docker compose exec web pytest`. If Docker is not running, ask the user to start it — do not skip the Docker verification.
- **Bug fix workflow.** When fixing a bug, always start by asking: "why didn't we catch this in testing?" Then, before writing any fix: (1) write a test that reproduces the bug, (2) run it and confirm it fails, (3) fix the bug, (4) run the test and confirm it passes. The bug is usually an edge case we hadn't considered — the test ensures we don't regress. Only skip the reproduction test if the bug genuinely cannot be tested (e.g. infrastructure-only issue), and note why.

### Functional test rules (S10/S11/S12 coverage)

The functional suite in `src/assets/tests/functional/` documents spec compliance. When writing or reviewing these tests, apply these rules:

**Test one acceptance criterion per test method — not one feature per test.**
Each user story (US-SA-xxx, US-DM-xxx, etc.) lists several acceptance criteria. Every acceptance criterion needs its own `test_` method. A test that only checks `response.status_code == 200` is not covering an acceptance criterion — it is only checking that the URL exists.

**The "sub-behaviour" trap.** The most common gap pattern in this codebase is:
> The feature exists and loads correctly, but a specific sub-behaviour required by the spec is wrong or missing.

Examples from audit (Feb 2026): hold list count computed but not rendered in template (#21); stocktake query includes parent location but not child locations (#22); checkout form shows `quantity` as max instead of `available_count` (#25); concurrent checkout error message doesn't name the current borrower (#34).

These are never caught by "does the page load?" tests. For every acceptance criterion that says the UI *shows*, *displays*, *includes*, *names*, or *requires*, write a test that asserts the specific content or constraint — not just HTTP 200.

**Write both positive and negative tests.**
- Positive: the feature works for the valid case
- Negative/boundary: the feature correctly rejects or handles the edge case

**Spec-gap tests use `xfail(strict=True)`.**
When you write a test that documents a known gap (feature missing or broken), mark it:
```python
@pytest.mark.xfail(strict=True, reason="GAP #N: one-line description (spec ref)")
```
`strict=True` means if the app is fixed and the test starts passing, pytest will report it as XPASS and flag it for promotion to a normal test. Do not use `xfail` without `strict=True`.

**Spec-gap audit cycle.**
After any significant implementation work, run:
```bash
.venv/bin/pytest src/assets/tests/functional/ -v --no-cov --tb=no -q 2>&1 | grep -c "xfail"
```
Then cross-reference the open `spec-gap` GitHub issues against the xfail list. For any open issue with no corresponding xfail test, add one. For any xfail test that now passes (XPASS), close the issue and promote the test.

### Form submission testing — Issue #5 pattern (REQUIRED)

Any test that submits a form **must** extract field names and values from the rendered HTML, not hardcode them. Hardcoded POST payloads bypass the template→view contract and let silent regressions through undetected.

**Wrong pattern (violates #5):**
```python
resp = client.post(url, {"borrower": borrower.pk, "destination_location": location.pk})
```

**Correct pattern (round-trip):**
```python
# 1. GET the form
get_resp = client.get(url)
assert get_resp.status_code == 200

# 2. Extract actual form field names/values from the rendered HTML
from bs4 import BeautifulSoup
soup = BeautifulSoup(get_resp.content, "html.parser")
form = soup.find("form")
# Build POST data from the rendered form — update only the fields you want to change
post_data = {
    inp["name"]: inp.get("value", "")
    for inp in form.find_all(["input", "select", "textarea"])
    if inp.get("name")
}
post_data["borrower"] = borrower.pk  # override specific field

# 3. POST the extracted data
resp = client.post(url, post_data)
```

The reference implementation is `TestApprovalFormTemplateIntegration` in `src/accounts/tests/test_registration.py`. BeautifulSoup is available in the test environment (`pip install beautifulsoup4`).

**Scope:** Apply the round-trip pattern to any test that verifies form submission *behaviour* (state changes, transactions created, etc.). Tests that deliberately probe arbitrary view input handling (e.g. security boundary tests) may hardcode payloads — but must note why.

**Audit rule:** When spec-coverage auditing, flag any test that POSTs hardcoded form data for a state-changing operation as a `#5-violation` requiring upgrade to the round-trip pattern.

## Dependencies

- Dependencies are managed with `pip-tools`: edit `requirements.in`, then compile with `pip-compile requirements.in` to regenerate `requirements.txt`.
- **Always regenerate `requirements.txt`** after adding or changing entries in `requirements.in`. Never commit a modified `requirements.in` without an updated `requirements.txt` to match.
