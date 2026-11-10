# Retention analytics + MCP playbook

The CRM database is the operational source of truth. PostHog is the diagnostic
view Jarvis uses through MCP to decide the next product change that retains
users after signup.

## Number-one goal

Retain users after account creation. Measurement exists only to drive product
changes. If a signal does not map to a fix we can ship, do not add it.

## Required environment

```bash
POSTHOG_PROJECT_TOKEN=phc_...
POSTHOG_HOST=https://us.i.posthog.com
POSTHOG_SESSION_REPLAY=true
ACTIVATION_EXPERIENCE_VERSION=retention_v2
ACTIVATION_EVENT_SCHEMA_VERSION=2
SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY=...   # optional but recommended
APP_BASE_URL=https://agentflow.origentechnolog.com
```

Use the PostHog project token, not a personal API key. Keep the PostHog personal
API key only in MCP configuration.

## Keep the bill at $0

Leave the PostHog project without a payment method and set the monthly billing
limit to `$0`. Autocapture stays disabled. Replay runs only on registration and
the zero-contact activation dashboard. Inputs and rendered text are masked.

## First visit through signup

With `POSTHOG_ENABLED`, the server records `first_visit` on the first successful
anonymous GET to the homepage, a CRM offer/comparison page, registration, login
or terms page. A signed, HttpOnly cookie keeps that visit for 30 days. Later
navigation, campaign links and form retries do not replace it or extend its
expiry. The analytics cookie is shared between the legacy marketing hosts and
the canonical app host so redirects preserve the source. Other hosts use a
host-only cookie. Authentication cookie settings do not change.

Self-serve signup links the opaque visitor ID to `user_<id>` in PostHog, stores
the attribution on the CRM `account_created` event and mirrors it to immutable
PostHog person properties. The cookie clears after successful authentication.
Analytics failure does not block registration or remove the CRM attribution.
Existing signup `utm_*` and `ref` fields remain available with first-touch values;
`source=self_serve` still describes the signup method.

Use these event/person properties for acquisition reports:

- `first_touch_at` and `first_touch_landing_path`
- `first_touch_referring_domain` and `first_touch_referrer_kind`
- `first_touch_utm_source`, `first_touch_utm_medium`, `first_touch_utm_campaign`
- `first_touch_campaign_variant`, from `utm_content`
- `first_touch_utm_term` and `first_touch_ref`

The referrer kind is `external`, `internal` or `direct_or_unknown`. An internal
referrer is not evidence of an external acquisition source. Missing referrers
cannot distinguish a typed URL from a private message or a browser that removed
the header. The generic browser `$initial_*` fields may still begin at the
registration page; use the explicit `first_touch_*` properties for these reports.

Only the referring hostname and an allowlisted public landing path are stored.
Referrer paths, query strings, fragments and arbitrary landing query parameters
are discarded. Campaign labels accept 1–80 characters using letters, numbers,
dots, underscores, hyphens and tildes. Email addresses, URLs, free-form text and
phone-like digit sequences are rejected. Use campaign slugs such as
`nov-launch`, and never put personal details in campaign labels.

This measures a browser's first tracked visit within the cookie window, not a
unique human or every page view. Anonymous server visits may include crawlers.
Deleted, expired or blocked cookies and switching devices can break continuity.
Direct POST signups without a previous tracked visit have no first-visit record.
Old signup sessions retain sanitized campaign labels but cannot recover a
missing landing page or referrer. There is no historical backfill.

CRM owns this change. Native AgentFlow and ClientFlow APIs and payloads are
unchanged. No migration, mobile release or PostHog setting change is required.
Deploy the CRM change before checking new signups. Existing PostHog configuration
controls collection; no extra pageview capture or session replay is enabled.

## Metric definitions

- **Unit of analysis:** user, not organization.
- **Customer cohort:** active orgs with `is_platform_admin=false`.
- **Activated:** user has at least one contact and at least one dated follow-up
  task of subtype `Follow-up` / `Follow Up`.
- **24h activation rate:** activated within 24h / users with age ≥ 24h.
- **D1 return:** authenticated `session_started` on the calendar day after signup.
- **D2–D7 return:** any `session_started` between 1 and 7 days after signup,
  denominator only users age ≥ 7 days.
- **Meaningful action:** contact created, follow-up created, task completed,
  CSV import completed, or inbox contact created.
- **UTC day boundaries** for `session_started` and `surface_viewed`.

## Canonical event context

Server events include privacy-safe context when available:

`event_schema_version`, `activation_experience_version`, `source`, `surface`,
`subscription_tier`, `account_age_days`, `activated`, `selected_path`,
`highest_funnel_step`, plus event-specific enums/counts.

Never send names, emails, phones, addresses, notes, free-form text, brokerage
names, filenames, CSV row content, or inbox payloads.

## Retention stages

Mutually exclusive current stages, updated on milestones and by
`python jobs/retention_analytics.py`:

- `activation_observing`
- `unactivated_no_path`
- `unactivated_path_stalled`
- `unactivated_returning`
- `activated_observing`
- `activated_retained`
- `activated_idle`
- `established_active`
- `established_at_risk`
- `established_dormant`

Auth failure, email failure, abandonment step, nudge ignored, and friction
reason are diagnostic flags, not competing stages.

## Explicit clickstream

Autocapture is off. Decision-relevant controls emit:

- `ui_element_viewed`
- `ui_interaction`
- `form_field_interaction` (allowlisted fields only, never values)
- `ui_error_shown`
- `activation_step_viewed`
- `activation_abandoned`
- `nav_away_during_activation`

Properties are allowlisted enums: `surface`, `component`, `action`, `target`,
`step`, `path`.

## Scheduled jobs

```bash
# Hourly
python jobs/activation_lifecycle.py

# Daily
python jobs/retention_analytics.py

# One-time after deploy
python scripts/retention_baseline.py
python scripts/activation_report.py
```

Configure these as Railway cron jobs. Lifecycle is capped at three messages and
stops when `is_user_activated(user)` is true.

## MCP analyst playbook

When asked why users are not staying, Jarvis will:

1. Validate data completeness and cohort eligibility.
2. Size each retention stage and compare with the prior experience version.
3. Locate the largest funnel or return-loop loss.
4. Break it down by path, source, device, error, email delivery, and reason.
5. Reconstruct the allowlisted interaction sequence before the drop-off.
6. Inspect masked recordings only for register / zero-contact cohorts.
7. Separate correlation from direct evidence and state confidence.
8. Recommend one concrete product change tied to the dominant signal.
9. Re-query the next eligible cohort after shipping.

### First HogQL checks

```sql
SELECT event, count(), uniq(distinct_id)
FROM events
WHERE timestamp >= now() - INTERVAL 14 DAY
  AND event IN (
    'account_created', 'dashboard_viewed', 'activation_path_selected',
    'contact_created', 'follow_up_created', 'activation_completed',
    'session_started', 'meaningful_action', 'activation_abandoned',
    'welcome_email_sent', 'welcome_email_clicked', 'login_failed',
    'friction_response', 'churn_reason', 'retention_stage_changed'
  )
GROUP BY event
ORDER BY count() DESC
```

```sql
SELECT
  properties.retention_stage AS stage,
  count() AS users
FROM persons
WHERE properties.retention_stage IS NOT NULL
GROUP BY stage
ORDER BY users DESC
```

## Signal → action

| Dominant signal | Ship this | Do not do |
|---|---|---|
| No path selected | Simplify chooser to one primary CTA | Add more paths |
| Abandon at contact step | Fewer fields / clearer mobile UX | Add optional fields |
| Abandon at follow-up step | Stronger Tomorrow default | Custom date as default |
| Submit failures | Fix the dominant `error_code` | Blame the user |
| CSV failures | Fix mapper/preview for that code | Force only manual |
| Inbox copy, zero contacts | Improve inbox instructions or bury path | Lead welcome with inbox |
| Welcome delivered, low click | Rewrite subject/CTA | Longer email |
| Bounce/drop/defer | Fix deliverability | More lifecycle mail |
| Auth-blocked returns | Clearer login/recovery | Blame product value |
| Activated then idle | Today surface + due-day loop | More signup ads |
| Direct `unclear_value` | Show payoff earlier | Feature tour |
| Direct `no_time` / `too_much_setup` | Shorten path, preserve progress | Longer onboarding |

Review cadence: every 5–10 eligible new users, pick the single biggest stage or
failure mode, ship one `activation_experience_version` change, then remeasure.

## Operational surfaces

- Platform admin activation section
- `python scripts/activation_report.py`
- PostHog dashboard **New-user retention** created via MCP after deploy
- This document as Jarvis's MCP runbook

## Privacy checklist

- Opaque IDs only (`user_<id>`)
- Shared PII blocklists in Python and browser
- No autocapture / pageviews
- Replay masked and limited to register + zero-contact dashboard
- `activation_events` RLS enabled with no tenant policy; service role only

## Platform reports

Platform admins can open `/platform/dashboard` for signup source totals and 30-day
activity across the filtered organizations. Organization names link to a report
with saved CRM counts, saved BOB message counts, task status totals, a daily
meaningful-action chart, activation milestones and recent recorded activity.

The source belongs to the organization's earliest recorded signup. `Not captured`
means the original visit is missing. `Direct / unknown` means a captured first visit
had no external referrer. Historical signup UTM tags can still appear without a
captured first visit. No source is inferred for older accounts.

Activity charts include today and the preceding 29 UTC dates. Daily user visits
are recorded at most once per user per UTC day. They do not measure exact sessions
or time spent. Scheduled retention changes and automated emails are excluded from
latest activity and the recent timeline. Counts reflect stored records, while
activity events are historical and can remain after a record is deleted.

The metrics aggregator stores only counts in `organization_metrics.usage_summary`.
The org page's **Refresh counts** button updates that snapshot. The existing
**Refresh All Metrics** action updates active customer organizations. Snapshot
timestamps stay visible, and missing snapshots are never presented as zero usage.
No contact, inbox or BOB message content is loaded into these reports.

Deployment adds the nullable `usage_summary` column after `add_home_facts` through
the existing boot migration command. Older app versions ignore it. This change
is CRM-only, with no mobile contract or companion release requirement.
