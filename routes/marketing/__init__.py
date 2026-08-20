"""Marketing HTTP surface.

Two blueprints, split by who is allowed to reach them:

    marketing         the agent-facing app. Everything here is login_required.
    marketing_public  the links inside a sent email. No session, no CSRF token,
                      reached by recipients who are not our users.

Keeping them apart means a public route can never pick up an auth assumption by
sitting next to one, which is the failure mode that matters on an endpoint that
mutates data for anonymous callers.
"""
from flask import Blueprint

marketing = Blueprint('marketing', __name__)
marketing_public = Blueprint('marketing_public', __name__)

# Imported for their side effect of attaching routes to the blueprints above.
from routes.marketing import public  # noqa: E402,F401
from routes.marketing import sendgrid_templates  # noqa: E402,F401

__all__ = ['marketing', 'marketing_public']
