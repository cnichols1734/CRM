---
name: crm-analytics
description: >-
  Runs Origen CRM retention and activation analytics using PostHog MCP,
  Supabase activation_events, Railway cron health, and the retention playbook.
  Use when the user says CRM Analytics, asks why users are not staying, requests
  activation/retention metrics, D1/D7 return, lifecycle email volume, churn
  reasons, funnel drop-off, or the next product change to ship for retention.
---

# CRM Analytics

Jarvis retention brief for Origen TechnolOG. North star: **retain users after signup**. Measurement exists only to choose the next product change.

Canonical playbook: `documentation/activation_analytics.md`. Event catalog, HogQL, and Railway details: [reference.md](reference.md).

## When invoked

Default window: last 14 days (use 24h / 7d if the user specifies). Produce a brief, not a data dump.

1. **Data health** — event volume + Railway cron status
2. **Funnel + return** — activation and D1/D7
3. **Email / auth loop** — welcome + lifecycle send/click + login failures
4. **Stages + reasons** — retention stages, friction, churn_reason
5. **Verdict** — largest loss + one ship recommendation

## Tool routing

| Need | Source |
|---|---|
| Trends, funnels, retention, HogQL | PostHog MCP (`user-posthog`) — project **526258** |
| Operational truth / email send log | Supabase CRM `activation_events` (project `ikdcpkuwpaejbgyapzhr`) |
| Cron last run / next run | Railway CLI (`railway status`, service logs) |
| Platform numbers | `python scripts/activation_report.py` (prod env) |

PostHog dashboard: [New-user retention](https://us.posthog.com/project/526258/dashboard/1898511)

Discover PostHog tools with `GetMcpTools` / `search` before calling. Prefer `query-funnel`, `query-retention`, `query-trends` when they fit; use `execute-sql` for multi-event / stage / email breakdowns.

## Metric truth (do not violate)

- Unit of analysis: **user**, not org
- Customer cohort: active orgs, `is_platform_admin=false`
- **Activated:** ≥1 contact + ≥1 dated task subtype `Follow-up` / `Follow Up`
- Eligible denominators only (age ≥ 24h for 24h activation; age ≥ 7d for D2–D7)
- UTC day boundaries for sessions
- Opaque IDs only (`user_<id>`). Never request or echo PII from PostHog
- Autocapture is off; only allowlisted events count

## Standard run (copy progress)

```
CRM Analytics
- [ ] 1. Data health (PostHog volume + Railway crons)
- [ ] 2. Activation funnel + D1/D7
- [ ] 3. Email + auth loop (24h or requested window)
- [ ] 4. Stages + friction/churn reasons
- [ ] 5. Verdict + one product change
```

### 1. Data health

PostHog: event counts for the retention event set (see reference). Flag zeros on expected events after deploy.

Railway (linked project `scintillating-encouragement` / production):

- **Activation Lifecycle Cron** — hourly `python jobs/activation_lifecycle.py`
- **Retention Analytics Cron** — daily `python jobs/retention_analytics.py`
- **Task Reminder Cron** — unrelated to activation; ignore unless asked

Missing canvas edges on Task Reminder are cosmetic (plain env vars, not `${{refs}}`).

### 2. Activation + return

- Funnel: `account_created` → `dashboard_viewed` → `activation_path_selected` → `contact_created` → `follow_up_created` → `activation_completed` (24h window)
- Retention: `account_created` → returning `session_started` (Day, ~8 intervals)
- Abandonment: `activation_abandoned` breakdown by `last_step`
- Meaningful return: `meaningful_action` after signup

### 3. Email + auth (activation jobs)

From PostHog and/or Supabase `activation_events`:

| Event | Meaning |
|---|---|
| `welcome_email_sent` / `welcome_email_failed` / `welcome_email_clicked` | Signup welcome |
| `lifecycle_message_sent` | Hourly nudge (`event_data.stage`) |
| `lifecycle_message_clicked` | Nudge CTA click (stage-aware) |
| `email_delivered` / `email_bounced` / `email_dropped` / `email_deferred` | SendGrid webhook |
| `login_failed` / `password_reset_*` | Auth-blocked return |

Lifecycle stages: `no_contact_2h`, `no_follow_up_24h`, `stalled_3d` (max 3 sends/user, stops when activated).

For "emails sent last 24h", query `lifecycle_message_sent` + `welcome_email_sent` by stage/time. Summarize counts by stage; name users only if asked and only via DB (not PostHog).

### 4. Stages + reasons

- Person `retention_stage` distribution
- `retention_stage_changed` transitions
- `friction_response`, `churn_reason` (direct "why")

### 5. Verdict

End with:

1. **Largest loss** (stage or step) with numbers
2. **Confidence** (direct reason vs correlation)
3. **One ship** from the signal→action table in the playbook / reference
4. Link the PostHog dashboard

Do not recommend more paths, longer onboarding, or more lifecycle mail when deliverability/auth is the blocker.

## Output format

```markdown
## CRM Analytics (window)

**Headline:** one sentence

### Data health
- PostHog volume / gaps
- Cron status

### Activation & return
- Funnel / D1 / D7 / abandon hot spot

### Email & auth
- Welcome + lifecycle sends/clicks (by stage)
- Login failures if material

### Stages & reasons
- Stage mix; top friction/churn reasons

### Ship next
- One change + why (signal→action)
```

Stay Jarvis: concise, competent, one sharp line max. Prefer canvas only if the user wants a heavy visual artifact.

## Privacy

- No names/emails/phones/notes/brokerage in PostHog queries or briefs
- Replay only for register / zero-contact cohorts, masked
- Treat `activation_events` as internal ops data; redact emails unless the user explicitly needs recipient detail
