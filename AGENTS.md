# AGENTS.md

## Cursor Cloud specific instructions

### Overview

Flask-based multi-tenant CRM (Origen TechnolOG) for real estate agents. Single Python service, server-rendered with Jinja2 templates. See `CLAUDE.md` for full architecture, commands, and conventions.

### Running the dev server

```bash
DATABASE_URL="sqlite:////workspace/instance/crm_dev.db" python3 app.py
```

The app serves on **port 5011** in debug mode with hot-reload.

**Gotcha**: Without `DATABASE_URL`, the default SQLite URI in `config.py` uses a relative path (`sqlite:///instance/crm_dev.db`) which fails because Flask-SQLAlchemy doesn't resolve it correctly. Always use the absolute-path form above, or set `DATABASE_URL` to a PostgreSQL connection string if available.

### Database

- Without a `DATABASE_URL` secret, the app falls back to a local SQLite database at `/workspace/instance/crm_dev.db`. Tables are auto-created on first run via `db.create_all()`.
- With `DATABASE_URL` pointing to Supabase PostgreSQL, **the database is shared with production** -- be extremely careful with data mutations.
- For SQLite local dev, contact groups must be seeded before contacts can be created (the form requires at least one group).

### Testing

- **Unit tests**: `pytest tests/test_document_definitions.py` (no external services needed)
- **SendGrid test**: `pytest tests/test_sendgrid.py` requires `SENDGRID_API_KEY`
- **Playwright integration tests**: require the app to be running; see `CLAUDE.md` for commands
- No linter or formatter is configured for this project

### External service secrets (optional)

All are optional for basic local dev. The app starts and serves pages without any of them:

| Secret | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection (Supabase); omit for SQLite fallback |
| `SENDGRID_API_KEY` | Transactional email |
| `OPENAI_API_KEY` | AI features (chat, daily todo, action plans) |
| `SUPABASE_URL` / `SUPABASE_KEY` | File storage |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GMAIL_TOKEN_ENCRYPTION_KEY` | Gmail/Calendar integration |
| `RENTCAST_API_KEY` | Property data lookups |
