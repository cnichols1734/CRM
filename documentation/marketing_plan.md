# Email Marketing Plan

Status: planning. Supersedes the earlier draft of this file, which assumed SendGrid-hosted templates and treated compliance as a "future consideration." Both assumptions are reversed here.

## What we're building

Agents pick a template, pick an audience (group, zip, city, owner), and send — once or as a drip. They can also generate brand-correct templates by describing what they want, review the result in-app, and reuse or share it. Everything they can do in the UI, they can also do from Claude/Cowork over MCP, except pressing Send.

Three products in one feature area:

1. **Campaigns** — audience → template → send once or drip, with a monitor.
2. **Template studio** — AI generates templates from a prompt, inside a locked brand shell, with a compliance gate.
3. **MCP tool family** — the same capabilities from an external agent, staged for human launch.

## Decisions locked

| Decision | Choice |
|---|---|
| Template storage | Our DB is source of truth. We render and substitute; SendGrid is transport only. |
| Sending identity | Marketing sends from a dedicated subdomain (`mail.origentechnolog.com`) with its own SendGrid domain authentication. Same brand, separate DKIM signing domain from transactional. |
| MCP send authority | MCP builds and stages. Launch requires a click in AgentFlow. No `launch_campaign` tool exists. |
| AI output | Structured content blocks rendered into a locked brand shell. Never raw HTML from the model. |
| Compliance in v1 | CAN-SPAM, suppression, Fair Housing linter, TREC disclosure, per-contact consent. All of it. |
| Drip enrollment | Audience is a snapshot at launch. No evergreen auto-enrollment in v1. |
| Drip auto-exit | None. Sequences run to completion. |
| Org-wide templates | Any agent can publish org-wide; admins can unpublish. |
| Tier gating | Enterprise, plus platform super-admins. Free and Pro stay off. Monthly send caps still apply. |
| Analytics in v1 | Delivery outcomes only (sent/delivered/bounced/failed/skipped). Open and click columns exist but stay unpopulated. |

### Why our DB and not SendGrid dynamic templates

SendGrid caps an account at roughly 300 dynamic templates and has no tenant isolation — every org's templates would share one flat namespace. With agents generating their own, we'd hit the ceiling and create a cross-tenant mess. Owning the HTML also gives us instant preview (no API round trip), real version history, a compliance gate we control, and MCP flows that don't depend on a third-party write succeeding. SendGrid keeps doing what it's good at: delivery.

The existing admin template sync tool (`routes/marketing.py`, `SendGridTemplate`) stays as-is for transactional templates. It is not part of this feature.

### Why a marketing subdomain

Every email today leaves from `info@origentechnolog.com` — the same domain as password resets and org invites. Once agents start sending campaigns, spam complaints accrue to that domain and take transactional mail down with them. A separate authenticated subdomain means a bad campaign damages marketing reputation and leaves account-critical mail intact. It costs one set of DNS records and no dedicated IP.

## Data model

New models in `models.py`. All tenant-scoped with `organization_id` and registered in the RLS policy list.

**`MarketingTemplate`** — `organization_id`, `created_by_id`, `name`, `description`, `category`, `subject`, `preheader`, `blocks` (JSON), `html_cached`, `text_cached`, `visibility` (`private`|`org`), `status` (`draft`|`ready`|`archived`), `source` (`ai`|`manual`|`system`), `version`, `compliance_state` (`pass`|`warn`|`blocked`), `compliance_findings` (JSON), `merge_fields_used` (JSON), `last_used_at`.

**`MarketingTemplateVersion`** — `template_id`, `version`, `blocks`, `subject`, `html`, `created_by_id`, `change_note`, `generated_by_ai`, `prompt`. Every save snapshots. Enables rollback and "edit it again with AI" without losing the good version.

**`MarketingAudience`** — `organization_id`, `user_id`, `name`, `filter` (JSON: groups, zips, cities, states, owners, consent rules), `is_saved`, `cached_count`, `cached_at`. Ad-hoc audiences built inside the wizard are stored with `is_saved=False`.

**`MarketingCampaign`** — `organization_id`, `user_id`, `name`, `kind` (`one_time`|`drip`), `status` (`draft`|`pending_review`|`scheduled`|`sending`|`active`|`paused`|`completed`|`cancelled`|`failed`), `audience_id`, `from_name`, `reply_to`, `scheduled_at`, `timezone`, `launched_at`, `completed_at`, `created_via` (`web`|`mcp`|`bob`), plus denormalized counters (`total_recipients`, `queued`, `sent`, `delivered`, `bounced`, `failed`, `skipped`, `unsubscribed`).

**`MarketingCampaignStep`** — `campaign_id`, `step_index`, `template_id`, `delay_days`, `send_hour_local`, `name`. A one-time campaign is one step at `delay_days=0`.

**`MarketingEnrollment`** — `campaign_id`, `contact_id`, `status`, `current_step_index`, `next_send_at`, `enrolled_at`, `stop_reason`. Unique on `(campaign_id, contact_id)`. This is the launch snapshot.

**`MarketingSend`** — one row per email. `campaign_id`, `step_id`, `enrollment_id`, `contact_id`, `template_id`, `user_id`, `to_email` (snapshot), `subject_rendered`, `status` (`queued`|`sending`|`sent`|`delivered`|`bounced`|`dropped`|`deferred`|`failed`|`skipped`), `skip_reason`, `scheduled_for`, `sent_at`, `delivered_at`, `opened_at`, `clicked_at`, `provider_message_id`, `error`, `attempt_count`, `unsubscribe_token`. Indexes on `(status, scheduled_for)`, `campaign_id`, `contact_id`, `provider_message_id`.

`opened_at` and `clicked_at` ship nullable and unpopulated. Turning engagement tracking on later is a webhook-handler change with no migration.

**`MarketingSuppression`** — `organization_id`, `email` (lowercased), `scope` (`org`|`platform`), `reason` (`unsubscribe`|`bounce`|`spam_report`|`manual`|`invalid`), `source_send_id`, `created_at`. Unique on `(organization_id, email)`.

Spam reports and hard bounces write `scope='platform'`. On a shared sending domain, one org's bad address list is everyone's deliverability problem, so those addresses are dead platform-wide.

**`Contact` additions** — `marketing_consent` (`unknown`|`opted_in`|`opted_out`, default `unknown`), `marketing_consent_source`, `marketing_consent_at`.

Default is `unknown` and `unknown` is sendable. CAN-SPAM is an opt-out regime and these are existing business contacts. Only `opted_out` blocks a send. Orgs that want stricter behavior get a setting that requires `opted_in`.

**No new `Organization` columns.** `broker_name`, `broker_license_number`, and `broker_address` already exist for document generation and are exactly what the compliance footer needs. We add a validation gate: an org cannot launch a campaign until all three are populated.

**`tier_config/tier_limits.py`** — add `monthly_marketing_sends`: free `0`, pro `2500`, enterprise `25000`. Override per-org as usual.

## The block schema

The design reference is the AgentFlow welcome email: soft blue canvas (`#f0f4f8`), rounded white card with a soft shadow, a light header bar carrying the brand and a small uppercase purpose label, a dark gradient hero with a Fraunces headline and an italic orange accent line, DM Sans body copy in slate, orange gradient calls to action, serif numerals on numbered steps, and a quiet compliance footer. `services/marketing/shell.py` and `render.py` encode it.

The AI is never handed that HTML and never writes HTML. It produces an ordered list of typed blocks, via `generate_structured_response` with a strict JSON schema, and the renderer supplies the design. That inversion is the point: the reference cannot be degraded by a model having an off day, and it can be improved for every existing template at once by editing one file.

| Block | Fields |
|---|---|
| `hero` | eyebrow, title, accent, text — the dark banner; first block only, one per email |
| `heading` | text, level (`h2` display serif, `h3` small caps label) |
| `paragraph` | text |
| `bullets` | items[] |
| `button` | label, url |
| `steps` | steps[] of title + text; numbered `01`–`04` automatically |
| `callout` | label, text — the tinted box for the one unmissable detail |
| `image` | image_url, alt, caption, link_url |
| `listing_card` | image_url, address, price, beds, baths, sqft, url, caption |
| `stat_row` | stats[] of value + label |
| `quote` | text, attribution |
| `divider` | — |
| `signature` | — (renders from the agent's profile) |

`hero`, `steps`, and `callout` are what carry the reference's character; `listing_card` and `stat_row` are the real-estate-specific payoff. Together they're what make a "just listed" or "market update" email look professionally built instead of like a wall of text.

The hero is full-bleed, so the shell renders it as its own table row outside the padded content cell, and validation rejects one anywhere but the top rather than quietly relocating it.

`services/marketing/render.py` is a pure function from blocks to `(html, text)`. Table-based, fully inline CSS, no JavaScript, no external stylesheets, and a real `text/plain` alternative part. The renderer is the only thing that emits HTML, so email-client compatibility is a property of one file we can test.

### Merge fields

A closed registry in `services/marketing/merge_fields.py`: `{{contact.first_name}}`, `{{contact.city}}`, `{{contact.zip}}`, `{{agent.full_name}}`, `{{agent.phone}}`, `{{agent.brokerage}}`, `{{org.name}}`, and so on. Each supports a fallback: `{{contact.first_name|there}}`.

Substitution is regex over the whitelist. Not Jinja, not even sandboxed Jinja. Template text is agent-authored and AI-generated, so an expression evaluator is a server-side template injection surface for no benefit. An unknown token fails validation at save time. A missing value at send time uses the fallback, or skips the contact with `skip_reason='missing_merge_field'` if there isn't one.

## Compliance engine

`services/marketing/compliance.py`. Runs on every template save and again at launch.

**Fair Housing linter.** A deterministic term list plus an AI review pass. The list covers protected-class language and its well-documented proxies: familial status ("perfect for families", "no kids", "empty nesters", "bachelor pad"), religion ("Christian community", "walk to St. Mary's"), race and national origin ("exclusive neighborhood", ethnic descriptors), disability, and safety/quality proxies ("safe neighborhood", "crime-free", "good schools"). Findings are `block` or `warn`. The AI pass catches novel phrasing the list misses and returns findings against a strict schema.

`block` prevents saving as `ready` and prevents launching. `warn` requires an explicit acknowledgement. Findings attach to the specific block so the UI can highlight the phrase and offer a targeted rewrite.

This is the part that turns a liability into a moat. Every agent knows Fair Housing language can end their career, and nobody else's marketing tool checks for it.

**TREC and advertising disclosure.** Brokerage name, agent's licensed name, and license number are rendered by the shell footer, not by the AI, so an agent can't delete them.

**CAN-SPAM.** Auto-injected unsubscribe link (per-send token), the org's physical mailing address from `broker_address`, and a plain "you're getting this because" line. `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers on every send.

**Content policy.** The agent's prompt is untrusted input. The generation system prompt is hardened against instruction injection, refuses off-topic and offensive requests, and output runs through a moderation pass before it reaches preview.

## Sending pipeline

**Launch** (`services/marketing/launch.py`):

1. Resolve the audience filter to contacts.
2. Filter: has an email, not suppressed (org or platform), not `opted_out`, deduplicated by email address.
3. Check the org's remaining monthly quota. Refuse the launch with an exact number if it would overrun.
4. Create `MarketingEnrollment` rows and `MarketingSend` rows at `status='queued'`.
5. Create `status='skipped'` rows for excluded contacts, with reasons.

That last step matters for trust. The UI can say "312 recipients, 47 excluded: 31 no email address, 12 unsubscribed, 4 previously bounced" instead of quietly sending to fewer people than the agent expected.

**Worker** (`jobs/marketing_outbox_worker.py`, cron every 5 minutes) — modeled directly on `jobs/notification_outbox_worker.py`, including re-calling `set_job_org_context` after every commit, since `SET LOCAL` is transaction-scoped.

Claims queued rows where `scheduled_for <= now`, batched per org. For efficiency, the layout renders once per step and merge fields become SendGrid substitution tokens, so one API call covers up to 1000 personalizations. Recipients needing a different body fall back to individual sends. Every send carries `custom_args` of `send_id`, `campaign_id`, `step_id`, `organization_id` for webhook attribution. Retries use exponential backoff on 429 and 5xx, hard-failing after three attempts. Per-org and global per-minute throttles protect the shared domain.

**Drip scheduler** (`jobs/marketing_drip_worker.py`, hourly) — advances `MarketingEnrollment` rows whose `next_send_at` has passed: create the send for the current step, increment the step index, compute the next `next_send_at` from the following step's `delay_days` at its `send_hour_local`. Mark complete when the steps run out.

**Bounce-rate circuit breaker.** If an org's rolling bounce rate crosses a threshold, auto-pause their campaigns and notify them. Given the shared sending domain, one agent with a stale purchased list can damage every customer's delivery. This is the guardrail that makes same-domain sending survivable.

**Webhook** — extend `routes/analytics_webhooks.py` to match `custom_args.send_id` and update `MarketingSend`. Hard bounces and spam reports write platform-scoped suppressions; unsubscribes write an org suppression and set `Contact.marketing_consent='opted_out'`.

**Unsubscribe** — `GET /u/<token>` and `POST /u/<token>`, public and unauthenticated, one click, no login wall, with a resubscribe link on the confirmation.

## UI

New `routes/marketing/` package (`campaigns.py`, `templates_.py`, `audiences.py`, `settings.py`), following the repo's convention of splitting larger features into sub-packages. All pages use existing `crm-*` primitives and `templates/components/ui.html` macros. `crm-segment` tabs across the top of `/marketing`.

**Overview** — `crm-kpi-grid`: sends this month against quota, active campaigns, delivered rate, bounce rate with a warning state. Recent campaigns table with status badges.

**Campaigns** — list and detail. Detail shows status, audience summary, steps, and the full send ledger with per-recipient outcomes and skip reasons. A polling Stimulus controller (modeled on `transaction_live_controller.js`) updates progress while sending.

**Templates** — cards filtered by Mine / Shared with org, with category badges, last-used dates, and a primary "Create with AI" action.

**Template studio** — the marquee screen. Two panes, one page:

- Left: prompt box, category picker, image dropzone, clickable merge-field chips, tone selector.
- Right: live preview in a sandboxed `<iframe srcdoc>` at 600px with a mobile/desktop toggle. In-app, never a new browser tab. `sandbox` without `allow-scripts`.
- Below the preview: the compliance panel. Green when clear; otherwise the offending phrase highlighted with a one-click fix that re-prompts only that block.
- Actions: Regenerate, edit any block inline, Save as draft, Save and make available, and a "Share with my organization" checkbox.

**Campaign wizard** — three full screens, no modal chains:

1. Pick a template from a card grid with thumbnails.
2. Build the audience — group chips, zip and city multi-selects, owner filter — with a live recipient count and an expandable exclusion breakdown.
3. Schedule — send now, schedule, or convert to a drip by adding steps with day offsets. Final preview merged against a real contact ("Preview as: Sarah Mitchell"). The launch button carries the recipient count in its label.

**Audiences** — saved segments, reusable across campaigns.

**Contact detail** — a Marketing section with that contact's campaign history, consent state, and an opt-out toggle.

**Org settings** — default from-name and reply-to, suppression list management, and the broker fields gate.

Stimulus controllers: `marketing_template_studio_controller.js`, `marketing_campaign_wizard_controller.js`, `marketing_campaign_monitor_controller.js`.

**Images** — a new public Supabase bucket, `marketing-assets`. Existing buckets are private with expiring signed URLs, which would break images in already-delivered email. Uploads are validated and resized to a 600px max width, matching the pattern in `routes/gmail_integration.py`.

## MCP

New `services/bob_tools/marketing.py` plus registry entries. Tools appear in MCP automatically via `select_tools` and `scope_for_tool`.

**Read tools** (`RISK_READ` → `read` scope): `list_email_templates`, `get_email_template`, `preview_email_template`, `list_marketing_audiences`, `estimate_audience`, `list_campaigns`, `get_campaign`.

**`get_email_template_guidelines`** — returns the block schema, merge-field registry, brand shell description, and content rules. This is the "give the external agent the standard layout" requirement, and it's what lets Cowork produce a correct template on the first try.

**Write tools** (`RISK_LOW_WRITE` → `write` scope):

- `create_email_template` — runs the same compliance engine and returns findings, so a blocked draft comes back with specific reasons the model can fix and retry. Returns `record_url` to the in-app preview.
- `update_email_template` — versioned, with a change note.
- `create_campaign` — creates at `status='draft'`, `created_via='mcp'`. Returns the exact recipient count, the exclusion breakdown, and a `record_url` to the review screen.
- `stage_campaign_for_review` — moves the draft to `pending_review`, creates an in-app notification, returns the launch URL.
- `add_marketing_suppression`, `set_contact_marketing_consent`.

**There is deliberately no `launch_campaign` tool.** MCP tool calls run precleared in `services/mcp/adapter.py` — high-risk writes execute immediately with no confirmation card, unlike in-app B.O.B. A send to 800 people is not something that should happen because a model inferred it. The absence of the tool is the safety mechanism, and it must be stated in both the tool descriptions and `services/mcp/instructions.py` so the model says "open this link to review and send" rather than hunting for a send tool.

Add `build_email_campaign` and `create_email_template` to `services/mcp/prompts.py` for discoverability, and extend `grouped_tool_names` in `adapter.py` so the new tools show on the OAuth consent screen.

### The flow this is built for

An agent is researching a neighborhood in Cowork and says "this would make a good drip for my Heritage Creek farm list." The model calls `get_email_template_guidelines`, drafts blocks, calls `create_email_template`, gets a Fair Housing warning back on one phrase, fixes it, calls `estimate_audience({zips:['78130']})` and reports "312 contacts, 18 excluded," builds a three-step campaign, and stages it. The agent opens one link, sees the exact rendered email and the exact recipient count, and clicks Launch. Roughly zero work, and nothing left the building without a human.

## Phasing

**Phase 0 — Foundations.** Models and migration, block renderer and brand shell, merge-field registry, compliance engine, suppression, unsubscribe endpoint, `mail.origentechnolog.com` domain authentication in SendGrid, feature flag and tier limits. Nothing user-visible.

**Phase 0.5 — Seed system templates.** Six house templates as `source='system'`, `visibility='org'`: Just checking in, Open house, Market update, Just listed, Just sold, Holiday.

This is out of the order you described, and it matters. Phase 1 is untestable and undemoable with an empty template list, and these same six become the few-shot examples that make Phase 2's AI output good. Building them by hand first is the cheapest way to de-risk both phases.

**Phase 1 — One-time campaigns.** Audience builder, campaign wizard, launch path, outbox worker, webhook attribution, campaign monitor, contact marketing history, bounce circuit breaker.

**Phase 1.5 — Drips.** Steps, enrollments, drip scheduler, pause and resume.

**Phase 2 — Template studio.** AI generation, block editing, live preview, compliance UI, versioning, image uploads, org sharing.

**Phase 3 — MCP.** Tool family, prompts, instructions, consent screen grouping.

## Risks

**Deliverability.** The dominant risk. Mitigated by the marketing subdomain, mandatory suppression handling, send quotas, the bounce circuit breaker, and platform-scoped suppression on spam reports. Not mitigated against agents importing purchased lists — that needs a terms-of-service answer alongside the technical one.

**Fair Housing liability.** A linter reduces exposure; it does not eliminate it. The generated-copy disclaimer and the agent's explicit acknowledgement of warnings both matter, and the finding log needs to be retained as evidence of a good-faith compliance process.

**Cost.** SendGrid bills per email. The tier caps are the cost control, and they need to be enforced at launch time, not discovered on an invoice.

**Scope.** Phase 2 is the most seductive and least essential part. Phase 1 with six good hand-built templates is a shippable product on its own. If something has to slip, it's the studio, not the compliance engine.
