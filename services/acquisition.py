"""Keep a bounded first visit through self-serve web signup."""
from datetime import datetime, timezone
import logging
import re
import secrets
from urllib.parse import urlsplit

from flask import current_app, request, session
from flask_login import current_user
from itsdangerous import BadData, URLSafeTimedSerializer

from config import DEFAULT_APP_BASE_URL, LEGACY_PUBLIC_HOSTS

logger = logging.getLogger(__name__)
COOKIE_NAME = 'agentflow_first_visit'
MAX_AGE = 30 * 24 * 60 * 60
PUBLIC_PAGES = {
    'main.landing': '/',
    'main.free_real_estate_crm': '/free-real-estate-crm',
    'main.follow_up_boss_alternative': '/follow-up-boss-alternative',
    'main.wise_agent_alternative': '/wise-agent-alternative',
    'main.kvcore_alternative': '/kvcore-alternative',
    'auth.register': '/register',
    'auth.login': '/login',
    'auth.terms_privacy': '/terms-privacy',
}
CAMPAIGN_FIELDS = {
    'utm_source': 'first_touch_utm_source',
    'utm_medium': 'first_touch_utm_medium',
    'utm_campaign': 'first_touch_utm_campaign',
    'utm_content': 'first_touch_campaign_variant',
    'utm_term': 'first_touch_utm_term',
    'ref': 'first_touch_ref',
}
FIRST_TOUCH_FIELDS = (
    'first_touch_at', 'first_touch_landing_path',
    'first_touch_referring_domain', 'first_touch_referrer_kind',
    *CAMPAIGN_FIELDS.values(),
)


def _enabled():
    return bool(current_app.config.get('POSTHOG_ENABLED'))


def _signer():
    return URLSafeTimedSerializer(
        current_app.secret_key, salt='agentflow-first-visit-v1',
    )


def _read_visit():
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 3800:
        return None
    try:
        visit = _signer().loads(token, max_age=MAX_AGE)
        if (
            isinstance(visit, dict)
            and re.fullmatch(r'visit_[0-9a-f]{32}', visit.get('visitor_id', ''))
            and isinstance(visit.get('properties'), dict)
        ):
            return visit
    except (BadData, TypeError, ValueError):
        pass
    return None


def _campaign_labels(values):
    """Accept campaign slugs, never URLs, email addresses or free-form text."""
    labels = {}
    for source, target in CAMPAIGN_FIELDS.items():
        value = values.get(source, '')
        if not isinstance(value, str):
            continue
        value = value.strip()
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._~-]{0,79}', value):
            if not re.search(r'\d{7,}', re.sub(r'[-._~]', '', value)):
                labels[target] = value
    return labels


def _host(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme in ('http', 'https') and re.fullmatch(
            r'[a-z0-9.-]{1,80}', host,
        ):
            return host
    except ValueError:
        pass
    return ''


def _own_hosts():
    return LEGACY_PUBLIC_HOSTS | {
        _host(current_app.config.get('APP_BASE_URL') or DEFAULT_APP_BASE_URL),
    }


def _cookie_options():
    # The legacy marketing hosts redirect to the app host. Only this analytics
    # cookie is shared; authentication cookies retain their existing scope.
    host = _host(request.url_root)
    domain = 'origentechnolog.com' if host in _own_hosts() and (
        host == 'origentechnolog.com' or host.endswith('.origentechnolog.com')
    ) else None
    return dict(
        path='/', domain=domain, httponly=True, samesite='Lax',
        secure=bool(domain) or request.is_secure
        or bool(current_app.config.get('SESSION_COOKIE_SECURE')),
    )


def init_acquisition_tracking(app):
    @app.after_request
    def remember_first_visit(response):
        try:
            path = PUBLIC_PAGES.get(request.endpoint)
            if not path:
                return response
            if not _enabled() or current_user.is_authenticated:
                if COOKIE_NAME in request.cookies:
                    response.delete_cookie(COOKIE_NAME, **_cookie_options())
                return response
            legacy_redirect = (
                response.status_code == 301
                and _host(request.url_root) in LEGACY_PUBLIC_HOSTS
                and _host(response.headers.get('Location', '')) in _own_hosts()
            )
            if (
                request.method != 'GET'
                or not (response.status_code == 200 or legacy_redirect)
                or _read_visit()
            ):
                return response

            referrer = _host(request.referrer or '')
            own_hosts = _own_hosts() | {_host(request.url_root)}
            properties = {
                'first_touch_at': datetime.now(timezone.utc).isoformat(),
                'first_touch_landing_path': path,
                'first_touch_referrer_kind': (
                    'internal' if referrer in own_hosts
                    else 'external' if referrer else 'direct_or_unknown'
                ),
                **_campaign_labels(request.args),
            }
            if referrer:
                properties['first_touch_referring_domain'] = referrer
            visit = {'visitor_id': f'visit_{secrets.token_hex(16)}', 'properties': properties}
            response.set_cookie(
                COOKIE_NAME, _signer().dumps(visit), max_age=MAX_AGE,
                **_cookie_options(),
            )
            response.cache_control.private = True
            response.cache_control.no_store = True
            response.vary.add('Cookie')
            from services.product_analytics import capture_anonymous
            capture_anonymous(
                'first_visit', distinct_id=visit['visitor_id'], properties=properties,
            )
        except Exception:
            logger.exception('First-visit tracking failed')
        return response


def _signup_properties(properties):
    result = {key: value for key, value in properties.items()
              if key in FIRST_TOUCH_FIELDS}
    # Keep existing signup reports working while making first-touch semantics
    # explicit. utm_content also has a safe campaign_variant mirror in PostHog.
    result.update({source: result[target] for source, target in CAMPAIGN_FIELDS.items()
                   if target in result})
    return result


def signup_attribution(user):
    """Link the signed visitor ID and return attribution for the signup log."""
    try:
        legacy = session.pop('signup_acquisition', {})
        if not _enabled():
            return {}
        visit = _read_visit()
        if not visit:
            # Existing pre-deploy register sessions have campaign labels only.
            return _signup_properties(_campaign_labels(legacy)) if isinstance(legacy, dict) else {}
        from services.product_analytics import alias_visitor
        alias_visitor(visit['visitor_id'], user)
        return _signup_properties(visit['properties'])
    except Exception:
        logger.exception('Signup attribution failed')
        return {}
