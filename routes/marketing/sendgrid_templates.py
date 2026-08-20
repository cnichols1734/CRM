"""Old SendGrid admin routes. Not part of campaign marketing.

GET /marketing/templates used to render a leftover admin console. Keep the
path as a bounce to the library so old bookmarks do not land on a dead page.
The POST/preview admin endpoints are gone.
"""
from flask import redirect, url_for
from flask_login import login_required

from routes.marketing import marketing


@marketing.route('/marketing/templates')
@login_required
def templates_list():
    return redirect(url_for('marketing.library'))
