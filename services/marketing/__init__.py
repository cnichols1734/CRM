"""Email marketing: templates, audiences, campaigns, and sending.

Layering, outermost first:

    routes/marketing/      HTTP surface
    services/bob_tools/marketing.py   B.O.B. / MCP handlers (no launch tool)
    services/marketing/    this package
      blocks.py            the authored content format and its JSON schema
      merge_fields.py      the closed set of personalization tokens
      render.py            blocks -> email HTML and plain text
      shell.py             the locked brand wrapper and compliance footer
      compliance.py        Fair Housing linter
      suppression.py       addresses we will not email, and opt-out tokens
      sending_config.py    sender identity, monthly quota
      audience.py          filter -> contacts + exclusion breakdown
      templates.py         validate, lint, version, cache
      launch.py            enrollments, send rows, pause/resume/cancel
      send.py              per-recipient render + the creator's Gmail account
      attribution.py       webhook events + bounce circuit breaker
      drip.py              advance due enrollments
      studio.py            AI produces blocks, never HTML
      assets.py            public image uploads
      system_templates.py  starter library seeded per org
      links.py             absolute URLs that go inside a sent email

Templates live in our database. Campaigns and tests use the agent's connected
Google mailbox, with no SendGrid fallback. Attribution still accepts events
for earlier SendGrid emails. Gmail sends keep their message ID and sent status;
they do not generate SendGrid delivery or bounce events.

There is no launch path over MCP. External agents stage a draft; a human
clicks Launch in AgentFlow.
"""
