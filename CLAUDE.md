# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Flask-based multi-tenant CRM for Origen Realty real estate agents. Server-rendered Jinja2 templates with vanilla JavaScript (no frontend framework).

**Stack**: Flask 3.1, SQLAlchemy, Supabase PostgreSQL, Gunicorn, Railway hosting.

## Common Commands

```bash
# Run locally (port 5011, debug mode)
python3 app.py

# Install dependencies
pip install -r requirements.txt

# Database migrations
python3 manage_db.py status              # Check current head revision
python3 manage_db.py upgrade             # Apply pending migrations
python3 manage_db.py migrate "message"   # Auto-generate migration
python3 manage_db.py seed_orgs           # Seed task/transaction types for existing orgs

# Tests (Playwright integration tests)
pip install -r tests/requirements.txt && playwright install chromium
python tests/run_tests.py --base-url http://127.0.0.1:5011

# Unit tests
pytest tests/test_document_definitions.py
pytest tests/test_sendgrid.py
```

No linter or formatter is configured.

## Critical: Shared Production Database

Local development connects to the **same Supabase PostgreSQL database** as production. Be extremely careful with data mutations during development.

## Architecture

### Multi-Tenancy with Row-Level Security (RLS)

Every model has an `organization_id`. PostgreSQL RLS is enforced via `SET LOCAL app.current_org_id` in a `before_request` hook in `app.py`. Background jobs use `set_job_org_context()` from `jobs/base.py`. Session-cached org status expires every 5 minutes to reduce DB hits.

### Code Organization

- **`models.py`** — All SQLAlchemy models in a single file (~1480 lines)
- **`routes/`** — Flask Blueprints. Large features (transactions, reports) are sub-packages with their own modules
- **`services/`** — Business logic layer, separate from routes
- **`feature_flags.py`** — Per-tier feature access (free/pro/enterprise) with per-org overrides via `Organization.feature_flags` JSON column. Routes use `@feature_required('FEATURE_NAME')` decorator
- **`documents/*.yml`** — YAML-defined real estate document templates, loaded by `services/documents/loader.py` with field auto-mapping and DocuSeal e-signature integration
- **`jobs/`** — Background jobs with RLS-aware context pattern
- **`tier_config/tier_limits.py`** — Subscription tier limits (users, contacts, AI messages)

### AI Fallback Chain

`services/ai_service.py` provides centralized OpenAI access with automatic fallback: GPT-5.1 -> GPT-5-mini -> GPT-4.1-mini. AI feature routes each have custom prompts: `routes/action_plan.py`, `routes/daily_todo.py`, `routes/ai_chat.py`.

### External Integrations

- **Email**: SendGrid (primary) with Gmail SMTP fallback; Gmail OAuth for per-user sync
- **File Storage**: Supabase Storage
- **E-Signatures**: DocuSeal
- **Property Data**: RentCast API
- **Monitoring**: New Relic APM

## Database Migrations

**Always check the current head before creating a migration:**
```bash
python3 manage_db.py status
```

When writing migration files manually:
1. Set `down_revision` to the current head revision ID from status output
2. Use `IF NOT EXISTS` / `IF EXISTS` in SQL for safety
3. Run `python3 manage_db.py upgrade` after creating

If you see "Multiple head revisions are present", the migration's `down_revision` points to an old revision — update it to the actual current head.

## PR & Commit Conventions

Use conventional commit prefixes: `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `style:`, `test:`, `perf:`

Branch naming matches: `feat/description`, `fix/description`, etc.

Always branch from up-to-date `main` (`git checkout main && git pull origin main`) before starting work. Push branches for PR review — do not commit directly to main.

## Testing Rules

Do not auto-run UI tests unless explicitly asked. After completing a feature, ask before pushing a PR.
