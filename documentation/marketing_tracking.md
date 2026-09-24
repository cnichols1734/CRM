# Campaign engagement tracking

Marketing still sends through the campaign owner's connected Gmail account. The
Google OAuth scopes are unchanged. Tracking is added to production campaign
messages immediately before delivery; template tests and CRM previews are not
tracked. Existing sent emails cannot be backfilled.

Each send has a frozen tracked subject, HTML and plain-text body. Website links
redirect through AgentFlow, and HTML includes a unique one-pixel image. Phone,
email, unsubscribe and existing tracking links are excluded. Tokens contain an
organization ID and 256 random bits. A click can redirect only to its stored
HTTP or HTTPS destination; query parameters cannot change that destination.

The outbox saves this payload before contacting Gmail, so an immediate recipient
request can resolve its token. Retries reuse the payload and tokens. Workers
recheck queued rows under a PostgreSQL row lock before sending. An unexpected
exception after that commit marks the delivery outcome unknown and asks the
agent to check Gmail Sent before sending again. A later worker run also flags
tracked sends left in the sending state for over an hour after a process exit.

## Counts and access

Campaign recipient totals count each email address once across follow-ups.
Each step counts addresses for that step. Opens and clicks on individual send
rows are event counts. Clicks do not imply a recorded open. Old emails show
tracking unavailable; scheduled emails do not show zero engagement.

Identical requests from the same client, for the same event or link, are grouped
into ten-second windows. HEAD requests do not create events. Known scanner or
prefetch requests and recognized logged-in sender activity appear in history but
are excluded from engagement counts. A keyed digest supports duplicate detection
without storing raw IP addresses or user-agent strings.

These are recorded requests, not verified human actions. Image blocking and
caching can hide opens; privacy proxies and scanners can create them. Forwarded
messages and a sender's Gmail Sent copy carry the recipient's tokens. We cannot
reliably identify every automated request or sender view. AgentFlow does not try
to bypass mail privacy protections.

Reporting requires the campaign owner's session and existing organization access.
Recipient history also rechecks current contact ownership. Public endpoints
return only a pixel or a redirect, never recipient data. All three tracking tables
have forced organization RLS on PostgreSQL, and no grants to the Supabase `anon`
or `authenticated` API roles.

Campaign pages poll while visible, including after completion. Counts update
without a page reload and preserve open previews and activity panels. History
shows the latest 100 events. The sent-email list also includes opens and clicks.

## Migration and validation

Alembic revision `add_marketing_tracking` follows `add_offer_non_realty_items`.
It creates three tables and their indexes. It does not alter existing send or
campaign rows. PostgreSQL migration statements use short lock and statement
timeouts. Deploy the schema before the application and workers.

On 2026-09-24 UTC the migration was applied to the CRM production Supabase project
through the connector. The Alembic head was checked before advancing it. Existing
send counts and fingerprints matched afterward, and the new tables had no
Supabase security advisor findings. Pre-existing advisor findings were unchanged.

The developer's local SQLite database was backed up and the additive migration
was applied directly because that database has no Alembic version. No historical
version was assumed or stamped on it. The normal `scripts/manage_db.py upgrade`
path was separately tested from a reconstructed pre-change SQLite schema.

Run backend checks with:

```sh
python -m pytest tests/test_marketing_*.py -q
```

The browser test is opt-in and uses a local Flask server, SQLite fixtures and
simulated recipient requests. It does not send mail or access production:

```sh
RUN_MARKETING_BROWSER=1 python -m pytest tests/test_marketing_tracking_browser.py -q
npm run build
```

The Gmail boundary is mocked in delivery tests. Tests inspect the actual encoded
MIME message and exercise the real tracking endpoints and reporting queries.
No live client emails were sent for validation.
