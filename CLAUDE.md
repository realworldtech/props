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
pytest src/assets/tests/test_views.py::TestClassName::test_method_name

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

## Branching and Release Workflow

- **`main`** — production branch. Only updated via PR from `develop`.
- **`develop`** — integration branch. All feature work merges here first.
- **Feature branches** — branch from `develop`, PR back to `develop`.
- **Release flow**: `feature-branch → develop (PR) → main (PR)`. Never PR directly to `main` from a feature branch.
- **Hotfixes**: branch from `main`, PR to `main`, then cherry-pick or merge back to `develop`.
- When committing directly to `develop` (e.g. small fixes), no PR is needed for the `develop` commit itself — the PR happens when `develop` merges to `main`.

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
- `holdlists.py` — Hold list management
- `kits.py` — Kit/component assembly
- `pdf.py` — PDF generation
- `permissions.py` — Permission checks
- `print_dispatch.py` — Print dispatch routing
- `serial.py` — Serial number management
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

Spec documents are expected at `specs/` in the working tree. If the spec repo is not present, ask the user before proceeding with any implementation work that could conflict with undocumented requirements.

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

- **One `test_` method per acceptance criterion**, not per feature. `status_code == 200` is not coverage.
- **Sub-behaviour trap:** a feature can exist and load correctly while a required sub-behaviour is missing (value computed but not rendered; constraint enforced server-side but wrong value in form; error message too vague). For every criterion that says the UI *shows/displays/includes/names/requires*, assert that specific content — not just HTTP 200.
- Write a **positive test** (valid case works) and a **negative/boundary test** (invalid case is rejected) for each criterion.
- **Known gaps:** `@pytest.mark.xfail(strict=True, reason="GAP #N: description (spec ref)")`. `strict=True` is mandatory — XPASS means the gap is closed and the test needs promotion to a regular test.
- **Spec-gap audit cycle:** after significant implementation, run `pytest src/assets/tests/functional/ -v --no-cov -q 2>&1 | grep -c xfail` and cross-reference open `spec-gap` GitHub issues. Add xfail tests for any uncovered open issue; promote any XPASS tests and close the corresponding issue.
- **Full coverage audit:** start from the spec sections (S10a/b/c/d, S11, S12), enumerate every `- [ ]` acceptance criterion bullet, and verify each has a corresponding test. Do not start from the issue list — that only covers known gaps.

### Form submission testing — Issue #5 (REQUIRED)

Any test verifying a state-changing form submission **must** use the round-trip pattern: GET the form, parse the rendered HTML with stdlib `html.parser` to extract actual field names/values, then POST those extracted values. Never hardcode form field names — they bypass the template→view contract and allow silent regressions when either side changes.

Reference implementation: `TestApprovalFormTemplateIntegration` in `src/accounts/tests/test_registration.py`.

Exception: security boundary tests that deliberately probe arbitrary view input may hardcode payloads, but must comment why.

### Affordance exposure testing (REQUIRED)

Testing that a POST to a URL works only proves the endpoint exists. It does not prove a user can find it. For every user-facing feature, also test that the upstream page exposes the relevant button, link, or form. Example: after testing that `POST /assets/<pk>/checkout/` works, also test that `GET /assets/<pk>/` renders a link to `/assets/<pk>/checkout/`. This catches the recurring pattern of functionality fully implemented in views/services but never wired into any template (the kit checkout service is the canonical example).

Pattern:
```python
# Not just "does the action work?"
resp = admin_client.post(reverse("assets:asset_checkout", args=[asset.pk]), data)
assert resp.status_code == 302

# Also "is there an affordance on the referring page?"
detail_resp = admin_client.get(reverse("assets:asset_detail", args=[asset.pk]))
assert reverse("assets:asset_checkout", args=[asset.pk]).encode() in detail_resp.content
```

## Dependencies

- Dependencies are managed with `pip-tools`: edit `requirements.in`, then compile with `pip-compile requirements.in` to regenerate `requirements.txt`.
- **Always regenerate `requirements.txt`** after adding or changing entries in `requirements.in`. Never commit a modified `requirements.in` without an updated `requirements.txt` to match.

## Plan Execution Notes

- **Background agents cannot run Bash.** Plans that assume parallel background agents will execute shell commands fail silently. Use a single approved script instead of parallel agent dispatch for any shell-based work.
- **N-files operations → one script.** For repetitive "run this command on each of N files" tasks, write a script and approve it once. Faster, consistent output, one approval.
- **Single-session synthesis → collapse steps.** Multi-step synthesis tasks (cross-reference → write → validate → assemble) can be done in one pass when working in a single session. The step breakdown only matters for handoffs between sessions or people.
