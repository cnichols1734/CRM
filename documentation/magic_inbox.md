# Magic Inbox — Operator Runbook

The Magic Inbox auto-creates contacts from any message sent to a user's
private forwarding address (e.g. `chris.nichols-7f3a4b8c@inbox.origentechnolog.com`).

This document covers what's wired up in the codebase, the operator
checklist for first-time provisioning, environment variables, day-to-day
debugging, and the known-good rollback path.

---

## Architecture in one paragraph

SendGrid Inbound Parse posts each forwarded message to
`POST /webhooks/sendgrid/inbound-parse`. We verify the shared-secret query
parameter or header, look the user up by their token (the `<slug>-<token>` part of the
recipient), persist a row in `inbound_messages`, archive the raw payload to
Supabase Storage, normalize the body and attachments, send the bundle to
`gpt-5.4-nano` (vision-capable) with a strict `json_schema`, dedupe
candidates against the user's existing contacts, create new `Contact` rows,
emit an in-app notification, and reply by email with a 24-hour signed undo
link. The webhook always returns `200 OK` so SendGrid never retries — even
parser bugs are recorded in `inbound_messages.status='failed'` instead of
filling the SendGrid dead-letter queue.

---

## Code map

| Concern | File |
| --- | --- |
| Webhook + UI routes | `routes/inbound_email.py` |
| Address provisioning | `services/inbox_provisioning.py` |
| SendGrid payload normalizer | `services/inbound_payload.py` |
| AI extraction (single call, vision + json_schema) | `services/ai_service.py` (`generate_contact_extraction`) |
| Orchestrator (dedupe → create → notify) | `services/contact_extraction.py` |
| Outbound receipt / welcome / over-limit emails | `services/sendgrid_outbound.py` |
| Models | `models.py` (`User.inbox_*`, `InboundMessage`) |
| Migration | `migrations/versions/add_inbox_and_inbound_messages.py` |
| Inbox UI page | `templates/inbox/home.html` |
| Sidebar / dashboard / contacts surfaces | `templates/base.html`, `templates/dashboard.html`, `templates/contacts/list.html`, `templates/auth/user_profile.html` |
| Backfill + announcement script | `scripts/backfill_inbox_addresses.py` |
| Tests | `tests/test_magic_inbox.py` |

---

## Environment variables

All optional for local dev. The webhook accepts unverified requests with a
warning if neither webhook auth variable is set, so developers can iterate
without SendGrid.

| Variable | Purpose | Default |
| --- | --- | --- |
| `INBOUND_EMAIL_DOMAIN` | Subdomain SendGrid Inbound Parse routes to. | `inbox.origentechnolog.com` |
| `SENDGRID_INBOUND_WEBHOOK_SECRET` | Shared secret for SendGrid Inbound Parse. Add it to the destination URL as `?secret=...`, or forward it as `X-Inbound-Secret`. **Set this in production.** | _unset_ |
| `SENDGRID_INBOUND_PUBLIC_KEY` | Optional SendGrid Signed Event Webhook public key fallback. Inbound Parse does not natively sign requests, so prefer `SENDGRID_INBOUND_WEBHOOK_SECRET`. | _unset_ |
| `INBOUND_REPLY_FROM` | From-address for receipt and welcome emails. Must be a verified SendGrid sender. | `info@origentechnolog.com` |
| `SENDGRID_INBOX_WELCOME_TEMPLATE_ID` | SendGrid Dynamic Template for the Magic Inbox welcome email. | `d-d89070c074554464a728867471e173e1` |
| `SENDGRID_INBOX_RECEIPT_TEMPLATE_ID` | SendGrid Dynamic Template for the "contacts added" receipt email. | `d-f3ef49fcfb80406ab22ec2d0bf87c0e7` |
| `INBOUND_RAW_BUCKET` | Supabase Storage bucket name for raw payload archives. | `inbound-email-raw` |
| `INBOX_EXTRACTION_MODEL` | Primary OpenAI model. | `gpt-5.4-nano` |
| `INBOX_EXTRACTION_FALLBACK_MODEL` | Fallback if primary returns auth/rate/parse error. | `gpt-5-mini` |

Reused secrets: `SENDGRID_API_KEY` (outbound replies), `OPENAI_API_KEY`
(extraction), `SUPABASE_URL` + `SUPABASE_KEY` (raw payload archive),
`SECRET_KEY` (used to sign undo tokens via `itsdangerous`).

---

## First-time operator checklist

These are the steps a human has to do once before the feature works in
production. Order matters.

1. **Buy / claim the subdomain.** `inbox.origentechnolog.com` (or whatever
   you set `INBOUND_EMAIL_DOMAIN` to).
2. **DNS — MX record.** Point the subdomain at SendGrid:
   ```
   inbox.origentechnolog.com.   MX   10 mx.sendgrid.net.
   ```
   (TTL 3600. Confirm with `dig mx inbox.origentechnolog.com +short`.)
3. **DNS — SPF (optional but recommended).** Many forwarders rewrite Return-Path,
   so SPF on the inbox subdomain isn't strictly required, but adding
   `v=spf1 include:sendgrid.net ~all` doesn't hurt.
4. **SendGrid → Settings → Inbound Parse.** Add a host:
   - Receiving Domain: `inbox.origentechnolog.com`
   - Destination URL: `https://app.origentechnolog.com/webhooks/sendgrid/inbound-parse?secret=<SENDGRID_INBOUND_WEBHOOK_SECRET>`
   - Tick **POST the raw, full MIME message** (we store it for debugging).
   - Tick **Check incoming emails for spam** (we read `spam_score`).
5. **SendGrid → Sender Authentication / Verified Senders.** Make sure
   `INBOUND_REPLY_FROM` is a verified outbound sender. In local testing,
   `info@origentechnolog.com` is known-good.
6. **Supabase → Storage → New bucket.** Create `inbound-email-raw`. Mark
   it private. Set lifecycle policy to delete after 30 days.
7. **Run the migration:**
   ```bash
   python3 manage_db.py upgrade
   ```
8. **Backfill existing users:**
   ```bash
   # Dry run first
   python3 scripts/backfill_inbox_addresses.py
   # Apply for real
   python3 scripts/backfill_inbox_addresses.py --commit
   # Optional: send the announcement email at the same time
   python3 scripts/backfill_inbox_addresses.py --commit --announce
   ```
9. **Smoke test:**
   - Log in as yourself. Visit `/inbox`. Confirm the address renders, the
     QR code displays, and the address copies.
   - From your personal email, forward any message to your inbox address.
     Within ~5 seconds you should see a new contact, an in-app
     notification, and a reply email with an Undo link.

---

## Local end-to-end test

Use this path before pointing SendGrid at a local ngrok tunnel. It exercises
the same webhook route and the same SendGrid-shaped multipart fields.

1. Confirm `.env` has the required local values:
   ```bash
   DATABASE_URL=sqlite:////Users/christophernichols/PycharmProjects/CRM/instance/crm_dev.db
   INBOUND_EMAIL_DOMAIN=inbox.origentechnolog.com
   SENDGRID_INBOUND_WEBHOOK_SECRET=<secret>
   INBOUND_REPLY_FROM=info@origentechnolog.com
   OPENAI_API_KEY=<key>
   SENDGRID_API_KEY=<key>
   SENDGRID_INBOX_WELCOME_TEMPLATE_ID=d-d89070c074554464a728867471e173e1
   SENDGRID_INBOX_RECEIPT_TEMPLATE_ID=d-f3ef49fcfb80406ab22ec2d0bf87c0e7
   SUPABASE_URL=<url>
   SUPABASE_KEY=<service_role_key>
   ```
2. Apply migrations and provision inbox addresses:
   ```bash
   python3 manage_db.py upgrade
   python3 scripts/backfill_inbox_addresses.py --commit
   python3 scripts/simulate_inbound.py who-am-i
   ```
3. Start the dev server on port 5011:
   ```bash
   python3 app.py
   ```
4. From another terminal, hit the local HTTP webhook:
   ```bash
   python3 scripts/simulate_inbound.py csv \
     --user chrisnichols17@gmail.com \
     --sender chrisnichols17@gmail.com
   ```
   Expected result: `http status : 200`, `status processed`, and new contacts
   listed under `created_contacts`.
5. Test other payload shapes:
   ```bash
   python3 scripts/simulate_inbound.py text --user chrisnichols17@gmail.com
   python3 scripts/simulate_inbound.py vcard --user chrisnichols17@gmail.com
   python3 scripts/simulate_inbound.py image --user chrisnichols17@gmail.com
   python3 scripts/simulate_inbound.py signature --user chrisnichols17@gmail.com
   ```
6. To test the real SendGrid inbound route locally, expose Flask with ngrok:
   ```bash
   ngrok http 5011
   ```
   Then set the SendGrid Inbound Parse destination URL to:
   ```text
   https://<ngrok-host>/webhooks/sendgrid/inbound-parse?secret=<SENDGRID_INBOUND_WEBHOOK_SECRET>
   ```
   Send an email to the user's Magic Inbox address and watch for a processed
   `inbound_messages` row, new contacts, an in-app notification, and the
   receipt email.

Known-good local smoke result on 2026-04-25:

- `csv` fixture posted to `http://127.0.0.1:5011/webhooks/sendgrid/inbound-parse`
- `InboundMessage id=4`, `status=processed`, `source_kind=csv`
- Contacts created: Janet Hill, Daniel Reyes, Aisha Khan
- Raw payload archived to `inbound-email-raw`
- In-app notifications created
- Receipt email send succeeds once `INBOUND_REPLY_FROM=info@origentechnolog.com`
  is loaded by the process

---

## How the address is constructed

```
<slug>-<token>+<plus_alias>@<INBOUND_EMAIL_DOMAIN>
└─human bit─┘ └─auth─┘ └─optional─┘
```

- `slug` is `first.last` slugified to ASCII (`Renée O'Brien` → `renee.o.brien`).
  Reserved system slugs like `admin` get `.user` appended.
- `token` is 8 chars from a 31-char base32 alphabet (no `0/O/1/I/l`),
  ~40 bits. **The token is the auth.** Anyone who can post to it can add
  a contact. Rotate via the profile page if leaked.
- `plus_alias` is optional. `+buyers` will drop the new contact into the
  user's `Buyers` ContactGroup (case-insensitive name match within their
  org). Unmatched aliases are recorded but otherwise ignored.
- The sender can also type a group instruction in the email body, such as
  `Group: Buyers` or `add these to Sphere`. The AI extracts the requested
  group name, and the app attaches contacts only when that group already
  exists in the user's org. Unmatched names are ignored so typos do not
  create new groups.

---

## Cost ceilings

The normalizer enforces these so a single weird payload can't spike the
OpenAI bill:

- 16 KB cap on the cleaned text body (after HTML stripping).
- First 5 image attachments only; rest counted in `skipped_images`.
- Images downscaled to 1024px long-edge, re-encoded as JPEG quality 82.
- CSV attachments capped at 500 data rows. Larger CSVs route to the
  existing CSV importer (out of scope for the inbox).

Per-call cost is recorded on `inbound_messages.ai_cost_cents` using the
model's published per-million-token pricing. Treat it as an estimate — the
authoritative number lives in the OpenAI billing console.

Rate limits (per 24h, in `routes/inbound_email.py`):

- 200 messages per user
- 1000 messages per organization

Exceeding either replies with a polite "limit reached" email and marks the
inbound row `over_limit` rather than returning a 4xx.

---

## Day-to-day debugging

### "I forwarded an email and nothing happened"

```sql
-- Did the webhook see it?
SELECT id, status, source_kind, error_message,
       sender_email, recipient_address, created_at, processed_at
  FROM inbound_messages
 WHERE user_id = <user_id>
 ORDER BY created_at DESC
 LIMIT 20;
```

Common values:

| `status` | Meaning |
| --- | --- |
| `received` | Webhook hit landed but the orchestrator hasn't finished. If this stays for >30s, check application logs for an unhandled exception. |
| `processed` | At least one new contact created. See `created_contact_ids`. |
| `rejected` | Empty payload, all candidates were dupes/self-references, or low-confidence with no email/phone. |
| `failed` | AI call or DB commit blew up. `error_message` has the trimmed exception. |
| `over_limit` | Daily user/org rate cap or contact-cap on the org. |

If there is **no row at all** for the user, the webhook didn't reach us:

- Check `dig mx inbox.origentechnolog.com +short` returns SendGrid's MX.
- Check SendGrid → Inbound Parse → Activity Feed for delivery attempts and
  any 4xx/5xx responses from our server.
- Check our server logs for `Magic Inbox: webhook signature invalid` (means
  `SENDGRID_INBOUND_PUBLIC_KEY` is wrong) or `unknown recipient` (means
  the user isn't yet provisioned — run the backfill script).

### Pulling the raw payload

```python
from services.supabase_storage import download_file
raw = download_file('inbound-email-raw', message.raw_storage_path)
```

The raw blob is the verbatim multipart body SendGrid posted, suitable for
re-feeding into the normalizer for repro tests.

---

## Rolling the feature back

The feature is additive. To disable without dropping data:

1. SendGrid → Inbound Parse → delete the `inbox.origentechnolog.com` host
   (mail will start bouncing back to senders cleanly).
2. Hide the sidebar nav item by removing the `<a href="…inbox_home">`
   block in `templates/base.html` (two spots: mobile + desktop).
3. Skip the dashboard onboarding card by deleting the `{% if … inbox_address …%}`
   block in `templates/dashboard.html`.

The `User.inbox_address`, `User.inbox_token`, and `inbound_messages` table
can stay in place — they're harmless if the webhook is unreachable.

To fully remove, downgrade the migration:

```bash
python3 manage_db.py downgrade <previous_revision>
```

---

## Tests

`pytest tests/test_magic_inbox.py` covers:

- Slug + token shape, reserved-slug handling
- Address provisioning idempotence + rotation
- `parse_recipient` accepts/rejects edge cases (wrong domain, short token,
  invalid alphabet, plus alias, mixed case)
- Undo token round-trip + tamper detection
- Normalizer: empty/text/HTML/vCard/CSV/image, attachment classification,
  CSV truncation, image-count cap
- Orchestrator: contact creation, dedupe, plus-alias group resolution,
  self-reference suppression, low-confidence drop, empty-payload reject
- `/inbox` UI route (auth + render)
- vCard download
- Onboarding dismiss persistence
- Webhook end-to-end happy path, AI failure, spam-score drop, unknown
  recipient — all returning 200

The AI is mocked. There are no live OpenAI or SendGrid calls in the test
suite.
