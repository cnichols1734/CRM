"""Email marketing: templates, audiences, campaigns, and sending.

Layering, outermost first:

    routes/marketing/      HTTP surface
    services/marketing/    this package
      blocks.py            the authored content format and its JSON schema
      merge_fields.py      the closed set of personalization tokens
      render.py            blocks -> email HTML and plain text
      shell.py             the locked brand wrapper and compliance footer

Template markup is produced here rather than in SendGrid. SendGrid caps an
account at a few hundred dynamic templates and gives no tenant isolation, so
agent-authored templates cannot live there. Owning the markup also means the
compliance gate and the preview are ours.
"""
