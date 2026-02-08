# Contributing to PROPS

Thank you for your interest in contributing to PROPS. This document explains how to contribute effectively and what to expect during the review process.

## Development Approach

PROPS is built using **specification-driven development**. A formal specification governs what the application does and how it behaves. While the full specification is not publicly published, the intended behaviour can be inferred from the application itself.

All contributions are assessed against this specification:

- Changes that **align with** the specification are straightforward to accept.
- Changes that **extend** the specification (new features or behaviour) require a spec update before or alongside the code change.
- Changes that **conflict with or break** the specification will need discussion — we may adjust the spec, adjust the contribution, or decline the change.

This is how we verify that the application does what it is supposed to do. Please don't be discouraged if your contribution triggers a spec discussion — it's a normal part of the process.

## AI-Assisted Development

If you use [Claude Code](https://claude.ai/code) or other agentic coding tools, we recommend the [`/implement` skill](https://github.com/realworldtech/claude-implement-skill) for implementation work. It plans from spec documents, tracks progress against requirements, and verifies that changes stay aligned with the specification. This matches how we develop PROPS internally.

## How to Contribute

### 1. Open an Issue First

Before writing code, open an issue describing what you want to change and why. This lets us assess alignment with the specification early, before you invest time in implementation.

### 2. Fork and Branch

- Fork the repository
- Create a feature branch from `main` (e.g. `feature/add-widget` or `fix/broken-checkout`)
- Keep commits focused and well-described

### 3. Write Tests First

This project follows **test-driven development**. For every change:

1. Write a failing test that describes the expected behaviour
2. Run `pytest` and confirm it fails
3. Implement the code
4. Run `pytest` and confirm it passes
5. Verify tests also pass inside Docker: `docker compose exec web pytest`

Target 80%+ test coverage on changed code.

### 4. Follow Code Style

```bash
black src/        # Formatting (line-length 79)
isort src/        # Import sorting
flake8 src/       # Linting
```

Configuration is in `pyproject.toml`.

### 5. Update Dependencies Properly

If you add or change a dependency, edit `requirements.in` and regenerate `requirements.txt`:

```bash
pip-compile requirements.in
```

Never commit a modified `requirements.in` without the corresponding `requirements.txt` update.

### 6. Submit a Pull Request

Use the PR template provided. Your PR should include:

- **Summary** — what the change does and why
- **Spec alignment** — whether this implements an existing spec requirement, extends the spec, or is a new feature not yet in the spec
- **Test plan** — what tests were added or modified, and confirmation they pass in both local and Docker environments
- **Screenshots** — for any UI changes

## Pull Request Review

During review we will:

- Verify the change against the specification
- Run the test suite
- Check code style compliance
- Assess whether the spec needs updating to accommodate the change

PRs may take longer to merge if they require spec discussion. We appreciate your patience.

## Reporting Bugs

Please use the **Bug Report** issue template. Good bug reports include:

- Steps to reproduce the problem
- What you expected to happen vs what actually happened
- Logs (browser console, Django logs, Docker logs)
- Screenshots or screen recordings
- Your environment details (browser, OS, deployment method)

The more detail you provide, the faster we can investigate.

## License and Copyright

PROPS is dual-licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0) and a **commercial license**.

By submitting a contribution (pull request, patch, or any other form), you:

- Grant **Real World Technology Solutions** a perpetual, worldwide, non-exclusive, royalty-free, irrevocable license to use, reproduce, modify, distribute, and sublicense your contribution under both the AGPL-3.0 and any commercial license offered for PROPS.
- Confirm that you have the right to grant this license and that your contribution does not infringe any third-party rights.

This dual-license grant ensures that contributions can be included in both the open-source and commercially-licensed versions of PROPS.

## Questions?

Open an issue or reach out to the maintainers. We're happy to help you find the right approach before you start coding.
