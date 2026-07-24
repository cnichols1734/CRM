# Activation analytics setup

The CRM database is the operational source of truth. PostHog is the diagnostic
view for funnels and masked session replay.

## Required environment

```bash
POSTHOG_PROJECT_TOKEN=phc_...
POSTHOG_HOST=https://us.i.posthog.com
POSTHOG_SESSION_REPLAY=true
```

Use the PostHog project token, not a personal API key. The browser receives this
token by design.

## Keep the bill at $0

In PostHog billing, leave the project without a payment method and set the
monthly billing limit to `$0`. Confirm that usage is configured to stop when
the free allowance is exhausted.

Autocapture is disabled in code. Replay runs only on registration and the
zero-contact dashboard. Inputs and rendered text are masked, and recording
stops after activation.

## Dashboard definition

Create one dashboard named `New-user activation` with:

1. Funnel: `registration_viewed` → `registration_started` →
   `account_created` → `dashboard_viewed` → `activation_path_selected` →
   `contact_created` → `follow_up_created` → `activation_completed`.
2. Conversion window: 24 hours.
3. Retention: `account_created` followed by `session_started`, shown for day 1
   and days 2–7.
4. Breakdowns: `path`, `utm_source`, `utm_campaign`, `source`, and device type.
5. Friction: `friction_response`, broken down by `reason`.

The platform-admin dashboard and `python scripts/activation_report.py` provide
the same core operational rates without depending on PostHog.

## AI access

Connect PostHog's MCP server with a read-only personal API key. Keep that key in
the MCP configuration, never in the CRM environment or frontend. This lets an
agent inspect events, funnels, trends, and masked recordings without access to
customer contact data.

