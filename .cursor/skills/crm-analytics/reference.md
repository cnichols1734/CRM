# CRM Analytics reference

Companion to [SKILL.md](SKILL.md). Full prose playbook: `documentation/activation_analytics.md`.

## PostHog

- Org: Origen TechnolOG
- Project: Default project **526258**
- Dashboard: [New-user retention](https://us.posthog.com/project/526258/dashboard/1898511)
- Insights on that dashboard: Activation funnel (24h), D1/D7 retention, abandon by last step, login failures + welcome clicks

## Railway (production)

Project: `scintillating-encouragement` (`f64a9388-3778-45e6-9755-97b41a746a1c`)

| Service | Schedule (UTC) | Command |
|---|---|---|
| Activation Lifecycle Cron | `0 * * * *` | `python jobs/activation_lifecycle.py` |
| Retention Analytics Cron | `0 15 * * *` | `python jobs/retention_analytics.py` |
| Task Reminder Cron | `0 14 * * *` | `python jobs/task_reminder.py` (not activation) |
| Daily Health Check Cron | `0 14 * * *` | `python jobs/daily_health_check.py` (9:00 AM CT / CDT; emails ops digest) |

Lifecycle template env: `SENDGRID_ACTIVATION_LIFECYCLE_TEMPLATE_ID=d-9f6eaf6cb88340e2b6cdfcf6375b68ca`  
SendGrid template name: **OGT: Activation Lifecycle Nudge**

## Supabase

CRM project: `ikdcpkuwpaejbgyapzhr`  
Table: `activation_events` (`event`, `event_data` JSON, `user_id`, `organization_id`, `created_at`)

Example — lifecycle emails last 24h:

```sql
SELECT event,
       event_data->>'stage' AS stage,
       count(*) AS n
FROM activation_events
WHERE created_at >= now() - interval '24 hours'
  AND event IN (
    'lifecycle_message_sent',
    'lifecycle_message_clicked',
    'welcome_email_sent',
    'welcome_email_failed',
    'welcome_email_clicked'
  )
GROUP BY 1, 2
ORDER BY 1, 2;
```

## Retention stages

`activation_observing`, `unactivated_no_path`, `unactivated_path_stalled`,
`unactivated_returning`, `activated_observing`, `activated_retained`,
`activated_idle`, `established_active`, `established_at_risk`,
`established_dormant`

## Core events

**Funnel / return:** `account_created`, `dashboard_viewed`, `activation_path_selected`, `contact_created`, `follow_up_created`, `activation_completed`, `session_started`, `meaningful_action`, `activation_abandoned`, `activation_step_viewed`, `nav_away_during_activation`

**Email / auth:** `welcome_email_sent`, `welcome_email_failed`, `welcome_email_clicked`, `lifecycle_message_sent`, `lifecycle_message_clicked`, `email_delivered`, `email_bounced`, `email_dropped`, `email_deferred`, `login_failed`, `login_succeeded`, `password_reset_requested`, `password_reset_completed`

**Reasons / stages:** `friction_response`, `churn_reason`, `retention_stage_changed`, `retention_baseline_snapshot`

**Paths:** `csv_import_*`, `inbox_address_copied`, `inbound_message_received`, `inbound_processing_failed`, `surface_viewed`, `feature_gate_hit`

## First HogQL checks

```sql
SELECT event, count(), uniq(person_id)
FROM events
WHERE timestamp >= now() - INTERVAL 14 DAY
  AND event IN (
    'account_created', 'dashboard_viewed', 'activation_path_selected',
    'contact_created', 'follow_up_created', 'activation_completed',
    'session_started', 'meaningful_action', 'activation_abandoned',
    'welcome_email_sent', 'welcome_email_clicked', 'lifecycle_message_sent',
    'lifecycle_message_clicked', 'login_failed',
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

```sql
SELECT
  properties.stage AS stage,
  count() AS sends,
  uniq(person_id) AS users
FROM events
WHERE timestamp >= now() - INTERVAL 1 DAY
  AND event = 'lifecycle_message_sent'
GROUP BY stage
ORDER BY sends DESC
```

(If `properties.stage` is empty, check `properties.event_data.stage` or mirror shape from a sample row.)

## Lifecycle email copy map

| stage | Subject (approx) | CTA |
|---|---|---|
| `no_contact_2h` | Add one contact to get started | Add a contact |
| `no_follow_up_24h` | Schedule your next follow-up | Schedule follow-up |
| `stalled_3d` | Stuck on setup? | Open my dashboard (+ churn reason links) |

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

Cadence: every 5–10 eligible new users → one `activation_experience_version` change → remeasure.

## Targets (initial)

- 60% start a path
- 40% activate in 24h
- 25% meaningful work on days 2–7

## Local scripts

```bash
python scripts/activation_report.py
python scripts/retention_baseline.py   # one-time; idempotent
```
