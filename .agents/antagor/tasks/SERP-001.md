---
id: SERP-001
title: Allow one revoked key in git history (gitleaks fingerprint)
status: in_progress
attempts: 0
blocked: false
blocked_reason: null
phase: null
tests_hash: null
publish_stage: null
created_by: architect
approved_by: architect
worker: claude-code
type: chore
owns_paths:
- .gitleaksignore
test_paths: []
docs: none
tools: []
worker_args: null
decision: false
visual: null
depends_on: []
budget_usd: null
epic: null
spec_refs: []
checks:
- gitleaks git --redact --no-banner .
resident: null
tool: null
pr: null
---

Create the file .gitleaksignore at the repo root with exactly these two lines:
# Revoked key in git history (README example, 2026-09-23); the value is not kept here.
cb53fbe9a89bffb0e2d95a073d7414edfe437c37:README.md:generic-api-key:267
Then 'gitleaks git --redact --no-banner .' must report no leaks. Do not write any key value.

## Read first

- `README.md`

## Out of scope

- No key value anywhere; no change to .gitleaks.toml; no history rewrite.
