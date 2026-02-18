# Spec Pipeline: Issue-to-Implementation Automation

**Date:** February 2026
**Author:** Andrew Yager / RWTS
**Status:** Approved design, pending implementation

## Overview

A set of Claude Code skills that automate the pipeline from GitHub issue triage through spec patching, implementation, verification, and closure. The pipeline is an orchestration layer that delegates heavy lifting to existing `/implement` and `/spec` skills.

## Goals

1. Standardise how GitHub issues are assessed against the product spec
2. Ensure every code change has a corresponding spec change reviewed first
3. Keep the private spec (`realworldtech/props-spec`) out of public GitHub comments
4. Leverage existing `/implement` and `/implement verify` for TDD implementation
5. Give the operator full control at every stage — no auto-merging, no auto-posting without review

## Repository

**New repo:** `realworldtech/claude-issue-pipeline`

```
claude-issue-pipeline/
├── skills/
│   ├── spec-pipeline-triage/
│   │   └── SKILL.md
│   ├── spec-pipeline-spec-patch/
│   │   └── SKILL.md
│   ├── spec-pipeline-implement/
│   │   └── SKILL.md
│   ├── spec-pipeline-verify/
│   │   └── SKILL.md
│   └── spec-pipeline-close/
│       └── SKILL.md
├── README.md
└── CLAUDE.md
```

Skills are installed by symlinking into `~/.claude/skills/` as with existing RWTS skills.

## Shared Conventions

| Convention | Value |
|-----------|-------|
| Spec branch naming | `issue/<number>/spec` (in props-spec repo) |
| Implementation branch naming | `issue/<number>/impl` (in PROPS repo) |
| Worktree location | `.worktrees/issue-<number>/` (in PROPS repo) |
| Pipeline state file | `.issue-pipeline/<number>.md` (in PROPS repo) |
| GitHub labels | `triaged`, `needs-info`, `spec-impact:covered`, `spec-impact:extends`, `spec-impact:conflicts`, `spec-impact:new-scope`, `implementing`, `ready-for-review` |

Both `.worktrees/` and `.issue-pipeline/` should be in `.gitignore`.

## Stage 1: `/spec-pipeline-triage`

**Invocation:** `/spec-pipeline-triage 42`

**Purpose:** Fetch a GitHub issue, assess whether it has enough information to act on, determine how it impacts the product spec, and produce a high-level public summary.

### Process

1. **Fetch issue** via `gh issue view <number> --json title,body,labels,comments`
2. **Completeness check:**
   - Does the issue have a clear problem statement or feature request?
   - Does it have reproduction steps (bugs) or a user story (features)?
   - Is there enough context to map to spec sections?
3. **If insufficient — three-way flow:**
   - **Ask the operator first:** "Do you have additional context for this issue?" The operator may know what the requester meant even if they didn't write it clearly.
   - **If operator provides context:** Proceed with enriched understanding. Note in pipeline file that context was operator-supplied.
   - **If operator has no context:** Post a GitHub comment with intelligent clarifying questions. Add label `needs-info`. Stop.
4. **Spec impact analysis:**
   - Read `specs/props/spec.md` and relevant `sections/` files
   - Classify the issue:
     - (a) Already covered by spec
     - (b) Extends the spec
     - (c) Conflicts with the spec
     - (d) Entirely new scope
   - Suggest MoSCoW priority
5. **Output — presented to operator before posting:**
   - GitHub comment draft: high-level summary, impact classification, pseudocode outline of changes, testing approach. **No spec content quoted.**
   - Labels to apply
   - Internal analysis in `.issue-pipeline/<number>.md` with full spec section references
6. **On operator approval:** Post the comment, apply labels, save pipeline state.

## Stage 2: `/spec-pipeline-spec-patch`

**Invocation:** `/spec-pipeline-spec-patch 42`

**Precondition:** Stage 1 complete (issue triaged)

### Process

1. Read triage analysis from `.issue-pipeline/<number>.md`
2. Read affected spec sections from `specs/props/sections/`
3. Create branch `issue/<number>/spec` in the spec repo
4. **Generate spec edits:**
   - Modify affected section files with proposed changes
   - Maintain MoSCoW priority markers
   - Add/update test strategy for each new or modified requirement
   - Ensure requirement IDs are consistent with existing numbering
5. **Generate test outline:**
   - For each new/modified requirement, describe how it would be tested
   - Reference existing test patterns from the codebase
6. **Present to operator:**
   - Show diff of proposed spec changes
   - Show test strategy summary
   - Wait for approval
7. **On approval:** Commit to spec branch, update pipeline state

## Stage 3: `/spec-pipeline-implement`

**Invocation:** `/spec-pipeline-implement 42`

**Precondition:** Stage 2 complete (spec patch approved)

### Process

1. Read approved spec changes and test strategy from pipeline state
2. Create worktree: `git worktree add .worktrees/issue-<number> -b issue/<number>/impl`
3. **Delegate to `/implement`** within the worktree:
   - The spec patch output defines the requirements
   - The test strategy becomes the TDD plan
   - `/implement` handles the red-green cycle
4. Track progress in pipeline state
5. When implementation is complete:
   - Push branch
   - Create draft PR linked to the issue
   - Update pipeline state

**Key:** This stage is a thin orchestration wrapper. `/implement` does the actual work.

## Stage 4: `/spec-pipeline-verify`

**Invocation:** `/spec-pipeline-verify 42`

**Precondition:** Stage 3 complete (implementation exists)

### Process

1. Enter worktree at `.worktrees/issue-<number>/`
2. **Delegate to `/implement verify`** to check implementation against spec requirements
3. Run test suite: `pytest`
4. Run linting: `black --check`, `isort --check`, `flake8`
5. Run Docker tests if available: `docker compose exec web pytest`
6. **Report:**
   - Pass/fail summary
   - Coverage delta
   - Any spec coverage gaps
7. Update pipeline state with verification results

## Stage 5: `/spec-pipeline-close`

**Invocation:** `/spec-pipeline-close 42`

**Precondition:** Stage 4 passed (verification complete)

### Process

1. Ensure implementation PR exists and is linked to the issue
2. Ensure spec branch exists and is ready to merge
3. Post final GitHub comment summarising:
   - What was implemented (high-level, no spec content)
   - Test coverage summary
   - Links to implementation PR and spec branch
4. Suggest next steps: merge spec branch, merge implementation PR, close issue
5. **Does not auto-merge** — operator decides when to merge

## State File Format

`.issue-pipeline/<number>.md`:

```markdown
# Issue #<number>: <title>

## Status
- [x] Triage (YYYY-MM-DD)
- [x] Spec Patch (YYYY-MM-DD)
- [ ] Implementation
- [ ] Verification
- [ ] Closed

## Triage
- Classification: extends-spec | covered | conflicts | new-scope
- Affected sections: S2.3, S4.1
- MoSCoW: MUST | SHOULD | COULD
- Operator context: <any context provided by operator>
- GitHub comment: posted | pending

## Spec Patch
- Branch: issue/<number>/spec (in props-spec repo)
- Changes: <summary of what was modified>
- Test strategy: <summary of testing approach>

## Implementation
- Worktree: .worktrees/issue-<number>/
- Branch: issue/<number>/impl
- PR: #<pr-number>

## Verification
- Tests: pass | fail
- Linting: pass | fail
- Spec coverage: pass | fail
- Docker tests: pass | fail | skipped
```

## Privacy Model

- The spec repo (`props-spec`) is private to RWTS
- GitHub comments on issues contain **only** high-level summaries, pseudocode, and testing approaches
- Spec section IDs (e.g. S2.3) may be referenced internally but **not** in public comments
- Full spec content stays in the pipeline state file and spec branch, never posted publicly

## Future Work (Out of Scope)

- **PR triage:** Assessing incoming PRs against the spec (second phase, as noted by user)
- **GitHub Actions automation:** Could add lightweight CI to label/notify, but manual skill invocation is the primary interface
- **Multi-project support:** The skill structure is project-agnostic by design, but initial implementation targets PROPS only
