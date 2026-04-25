# AGENTS.md

This is the canonical AI agent guide for this repository. If an agent-specific file in this repo says to follow another file, follow this one first.

## Priority Reading

### For all work

Read this file first for runtime, architecture, data-safety, and repo conventions.

### For UI and design work

Read `.impeccable.md` for design context and anti-slop guardrails, then `STYLEGUIDE.md` for concrete tokens, components, and implementation patterns.

If the agent supports installed skills and the global `impeccable` skill set is available, use it as the general design method. Treat `.impeccable.md` and `STYLEGUIDE.md` as the repo-specific source of truth.

If `.impeccable.md`, `STYLEGUIDE.md`, and the current page disagree, preserve the shipped CRM design language unless the user explicitly asks for a redesign.

### For copy, docs, prompts, and user-facing text

Read `skill.md`. Use it for humanizing and tightening prose, not for changing technical names, code, migrations, schema details, or precise product behavior.

## Project Overview

Flask-based multi-tenant CRM (Origen TechnolOG) for real estate agents. Single Python service, server-rendered with Jinja2 templates and vanilla JavaScript with a progressive Stimulus/Tailwind frontend rewrite.

**Stack**: Flask 3.1, SQLAlchemy, Supabase PostgreSQL, SQLite for local fallback, Gunicorn, Railway hosting, Tailwind CSS 3.x, Vite, Stimulus.

## Common Commands

```bash
# Run locally (port 5011, debug mode)
DATABASE_URL="sqlite:////workspace/instance/crm_dev.db" python3 app.py

# Install dependencies
pip install -r requirements.txt

# Database migrations
python3 manage_db.py status
python3 manage_db.py upgrade
python3 manage_db.py migrate "message"
python3 manage_db.py seed_orgs

# Playwright integration tests
pip install -r tests/requirements.txt
playwright install chromium
python tests/run_tests.py --base-url http://127.0.0.1:5011

# Unit tests
pytest tests/test_document_definitions.py
pytest tests/test_sendgrid.py
```

## Git Start-Of-Work Rule

Before starting code changes, check the current branch and sync against GitHub `main`.

1. Run `git status --short --branch` to see the branch and any existing work.
2. Run `git fetch origin main` to update the remote `main` ref.
3. If the working tree is clean and the task should start from `main`, switch to `main`, pull the latest `origin/main`, then create a feature/fix branch from that updated `main`.
4. If the working tree is dirty, do not pull, rebase, reset, checkout, or stash without the user's approval. Report the dirty files and ask whether to preserve, stash, commit, or move the work.

## Frontend Design Rules

This repo already has an established product UI. Do not import generic SaaS aesthetics or marketing-page habits into the app.

- Treat the product as a daily operational tool for agents and coordinators, not a startup landing page.
- Preserve the existing visual system: dark slate shell, light working surfaces, restrained borders, orange accent, flat cards, and clear hierarchy.
- Reuse shared primitives before inventing new ones: `crm-*` classes in `frontend/styles/app.css`, macros in `templates/components/ui.html`, and tokens in `tailwind.config.js`.
- Keep layouts practical and information-dense enough for real work. Favor left-aligned structure, clear sections, and predictable actions over decorative symmetry.
- On legacy pages, move toward the shared CRM patterns when practical. Do not bolt on a second unrelated design language.

### Anti-Slop Guardrails

- No purple-blue gradient hero language, glow-heavy dark mode, glassmorphism, or generic "AI startup" styling.
- No nested-card-on-card grids, side-tab accent borders, giant rounded pills everywhere, or gratuitous drop shadows.
- No centered-everything app layouts, oversized empty-state illustrations, or fake dashboard drama.
- No decorative typography swaps that fight the existing product. For product UI, keep the current body-font approach unless the user explicitly asks for a rebrand.
- No bounce or elastic motion. Motion should be brief, purposeful, and easy to remove for reduced-motion users.
- No vague, overexplained UX copy. Labels, empty states, and helper text should be direct and useful.

## Running The Dev Server

```bash
DATABASE_URL="sqlite:////workspace/instance/crm_dev.db" python3 app.py
```

The app serves on **port 5011** in debug mode with hot-reload.

**Gotcha**: Without `DATABASE_URL`, the default SQLite URI in `config.py` uses a relative path (`sqlite:///instance/crm_dev.db`) which fails because Flask-SQLAlchemy does not resolve it correctly. Always use the absolute-path form above, or set `DATABASE_URL` to a PostgreSQL connection string if available.

## Database Safety

- Without a `DATABASE_URL` secret, the app falls back to a local SQLite database at `/workspace/instance/crm_dev.db`. Tables are auto-created on first run via `db.create_all()`.
- With `DATABASE_URL` pointing to Supabase PostgreSQL, the database is shared with production. Be extremely careful with data mutations.
- For SQLite local dev, contact groups must be seeded before contacts can be created because the form requires at least one group.

## Testing

- Unit tests: `pytest tests/test_document_definitions.py`
- SendGrid test: `pytest tests/test_sendgrid.py` requires `SENDGRID_API_KEY`
- Playwright integration tests require the app to be running and the test dependencies installed
- No linter or formatter is configured for this project
- Do not auto-run UI tests unless the user asks

## Architecture Snapshot

### Multi-Tenancy with RLS

Every model has an `organization_id`. PostgreSQL RLS is enforced via `SET LOCAL app.current_org_id` in a `before_request` hook in `app.py`. Background jobs use `set_job_org_context()` from `jobs/base.py`. Session-cached org status expires every 5 minutes to reduce DB hits.

### Code Organization

- `models.py`: all SQLAlchemy models live in one file
- `routes/`: Flask blueprints, with larger features split into sub-packages
- `services/`: business logic layer separate from routes
- `feature_flags.py`: per-tier feature access with per-org overrides in `Organization.feature_flags`
- `documents/*.yml`: YAML-defined real estate document templates loaded by `services/documents/loader.py`
- `jobs/`: background jobs with RLS-aware context handling
- `tier_config/tier_limits.py`: subscription-tier limits

### AI Fallback Chain

`services/ai_service.py` provides centralized OpenAI access with automatic fallback: GPT-5.1 -> GPT-5-mini -> GPT-4.1-mini. AI feature routes each have custom prompts in `routes/action_plan.py`, `routes/daily_todo.py`, and `routes/ai_chat.py`.

### External Integrations

- Email: SendGrid primary, Gmail SMTP fallback, Gmail OAuth for per-user sync
- File storage: Supabase Storage
- E-signatures: DocuSeal
- Property data: RentCast API
- Monitoring: New Relic APM

## Database Migrations

Always check the current head before creating a migration:

```bash
python3 manage_db.py status
```

When writing migration files manually:

1. Set `down_revision` to the current head revision ID from the status output.
2. Use `IF NOT EXISTS` and `IF EXISTS` in SQL for safety.
3. Run `python3 manage_db.py upgrade` after creating the migration.

If you see "Multiple head revisions are present", the migration's `down_revision` points to an old revision. Update it to the actual current head.

## PR And Commit Conventions

- Use conventional commit prefixes: `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `style:`, `test:`, `perf:`
- Branch naming should match: `feat/description`, `fix/description`, and so on
- Always branch from an up-to-date `main`
- Push branches for review instead of committing directly to `main`

## External Service Secrets

All are optional for basic local dev. The app starts and serves pages without any of them:

| Secret | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection (Supabase); omit for SQLite fallback |
| `SENDGRID_API_KEY` | Transactional email |
| `OPENAI_API_KEY` | AI features (chat, daily todo, action plans) |
| `SUPABASE_URL` / `SUPABASE_KEY` | File storage |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GMAIL_TOKEN_ENCRYPTION_KEY` | Gmail and Calendar integration |
| `RENTCAST_API_KEY` | Property data lookups |
