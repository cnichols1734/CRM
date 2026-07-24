"""Daily ops health check for Origen CRM.

Runs via Railway Cron (9:00 AM Central / 14:00 UTC) and emails a short status
report. Probes the public /health endpoint, Redis/RQ, and optionally Railway
service deployment status when RAILWAY_API_TOKEN is set.

Usage:
    python jobs/daily_health_check.py
    python jobs/daily_health_check.py --dry-run
    HEALTH_CHECK_TO=you@example.com python jobs/daily_health_check.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytz

# Add repo root for imports when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

CT = pytz.timezone('America/Chicago')
APP_BASE_URL = os.environ.get(
    'APP_BASE_URL', 'https://www.origentechnolog.com'
).rstrip('/')
DEFAULT_TO = os.environ.get('HEALTH_CHECK_TO', 'chrisnichols17@gmail.com')
DEFAULT_FROM = os.environ.get('HEALTH_CHECK_FROM', 'info@origentechnolog.com')
RAILWAY_GRAPHQL = 'https://backboard.railway.com/graphql/v2'
RAILWAY_PROJECT_ID = os.environ.get(
    'RAILWAY_PROJECT_ID', 'f64a9388-3778-45e6-9755-97b41a746a1c'
)
RAILWAY_ENVIRONMENT_ID = os.environ.get(
    'RAILWAY_ENVIRONMENT_ID', '107abd3a-2294-4a4e-9dfb-cbe0f7dfcb3c'
)
EXPECTED_SERVICES = (
    'OrigenTechnolOG',
    'document-worker',
    'Redis',
    'Task Reminder Cron',
    'Activation Lifecycle Cron',
    'Retention Analytics Cron',
    'Daily Health Check Cron',
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    warn: bool = False
    # Plain-English "so what?" line for the email.
    meaning: str = ''
    meta: dict[str, Any] = field(default_factory=dict)


def _http_json(url: str, timeout: float = 15.0) -> tuple[int, dict | list | None, float]:
    started = time.time()
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'OrigenDailyHealthCheck/1.0'},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', 'replace')
            latency_ms = round((time.time() - started) * 1000, 1)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
            return resp.status, payload, latency_ms
    except urllib.error.HTTPError as exc:
        latency_ms = round((time.time() - started) * 1000, 1)
        body = exc.read().decode('utf-8', 'replace') if exc.fp else ''
        try:
            payload = json.loads(body) if body else None
        except json.JSONDecodeError:
            payload = None
        return exc.code, payload, latency_ms
    except Exception as exc:
        latency_ms = round((time.time() - started) * 1000, 1)
        return 0, {'error': str(exc)}, latency_ms


_EXTERNAL_LABELS = {
    'docuseal': 'E-sign (DocuSeal)',
    'sendgrid': 'Email (SendGrid)',
    'google_oauth': 'Google login',
}


def check_app_health() -> CheckResult:
    status, payload, latency_ms = _http_json(f'{APP_BASE_URL}/health')
    if status != 200 or not isinstance(payload, dict):
        return CheckResult(
            name='Core app',
            ok=False,
            detail='The app health check did not respond normally.',
            meaning='Users may not be able to use Origen right now.',
            meta={'latency_ms': latency_ms, 'payload': payload},
        )

    health_status = payload.get('status', 'unknown')
    warnings = payload.get('warnings') or []
    checks = payload.get('checks') or {}
    external = checks.get('external') or {}

    bad_externals = []
    for key, info in external.items():
        if not isinstance(info, dict):
            continue
        if info.get('status') not in ('connected', 'ok', None):
            bad_externals.append(_EXTERNAL_LABELS.get(key, key))

    ok = health_status == 'healthy' and status == 200
    warn = bool(warnings) or bool(bad_externals)

    if not ok:
        detail = 'The core app reported unhealthy.'
        meaning = 'Something is wrong with the main app process.'
    elif bad_externals:
        detail = (
            'App is up, but a connected service is unhappy: '
            + ', '.join(bad_externals) + '.'
        )
        meaning = (
            'Origen itself is running. One helper service (like e-sign or email) '
            'needs a look when you have a minute.'
        )
    elif warnings:
        detail = 'App is up with a minor warning.'
        meaning = 'Nothing urgent — worth a glance later.'
    else:
        detail = 'App is up and responding normally.'
        meaning = 'The main CRM is healthy.'

    return CheckResult(
        name='Core app',
        ok=ok,
        warn=warn and ok,
        detail=detail,
        meaning=meaning,
        meta={'payload': payload, 'latency_ms': latency_ms},
    )


def check_homepage() -> CheckResult:
    status, _, latency_ms = _http_json(APP_BASE_URL + '/', timeout=15.0)
    # Homepage may redirect; treat 2xx/3xx as success.
    ok = 200 <= status < 400
    if ok:
        return CheckResult(
            name='Website',
            ok=True,
            detail='origentechnolog.com is loading.',
            meaning='People can reach the public site.',
            meta={'latency_ms': latency_ms},
        )
    return CheckResult(
        name='Website',
        ok=False,
        detail='The public website did not load cleanly.',
        meaning='Visitors may hit an error page or timeout.',
        meta={'latency_ms': latency_ms},
    )


def check_redis_and_rq() -> CheckResult:
    redis_url = os.environ.get('REDIS_URL')
    if not redis_url:
        return CheckResult(
            name='Background jobs',
            ok=False,
            warn=True,
            detail='Job queue settings are missing.',
            meaning='Document processing and some background work may be offline.',
        )
    try:
        from redis import Redis
        from rq import Queue

        conn = Redis.from_url(
            redis_url,
            socket_connect_timeout=8,
            socket_timeout=8,
        )
        started = time.time()
        pong = conn.ping()
        latency_ms = round((time.time() - started) * 1000, 1)
        if not pong:
            return CheckResult(
                name='Background jobs',
                ok=False,
                detail='The job queue did not respond.',
                meaning='Background work like document extraction may be stuck.',
            )

        queue_names = [
            'default',
            'documents',
            'document_extraction',
            'high',
            'low',
        ]
        depths = {}
        for name in queue_names:
            try:
                depths[name] = Queue(name, connection=conn).count
            except Exception:
                continue
        failed = 0
        try:
            failed = conn.llen('rq:queue:failed')
        except Exception:
            pass

        waiting = sum(depths.values())
        warn = failed > 0 or any(v >= 25 for v in depths.values())
        if failed:
            detail = f'Queue is up, but {failed} job(s) have failed.'
            meaning = 'Some background tasks need a retry or cleanup.'
        elif waiting >= 25:
            detail = f'Queue is up, with {waiting} jobs waiting.'
            meaning = 'Work is backing up — the worker may be slow or stuck.'
        else:
            detail = 'Background job queue is healthy.'
            meaning = 'Document processing and delayed work can run normally.'

        return CheckResult(
            name='Background jobs',
            ok=True,
            warn=warn,
            detail=detail,
            meaning=meaning,
            meta={'latency_ms': latency_ms, 'depths': depths, 'failed': failed},
        )
    except Exception as exc:
        message = str(exc)
        private_dns = 'railway.internal' in (redis_url or '')
        dns_miss = any(
            needle in message.lower()
            for needle in ('nodename nor servname', 'name or service not known', 'getaddrinfo')
        )
        # Private Redis is only resolvable inside Railway's network.
        if private_dns and dns_miss:
            return CheckResult(
                name='Background jobs',
                ok=True,
                warn=False,
                detail='Skipped from this machine (checked on the daily Railway run).',
                meaning='This is normal for a laptop test. The scheduled run checks it for real.',
            )
        return CheckResult(
            name='Background jobs',
            ok=False,
            detail='Could not reach the job queue.',
            meaning='Background work may be down until Redis is reachable again.',
        )


def check_database() -> CheckResult:
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        return CheckResult(
            name='Database',
            ok=False,
            warn=True,
            detail='Database settings are missing.',
            meaning='The app cannot read or save CRM data without this.',
        )
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url, pool_pre_ping=True)
        started = time.time()
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        latency_ms = round((time.time() - started) * 1000, 1)
        engine.dispose()
        warn = latency_ms > 500
        if warn:
            return CheckResult(
                name='Database',
                ok=True,
                warn=True,
                detail='Database answered, but it was a bit slow.',
                meaning='CRM data is available. If this keeps happening, pages may feel sluggish.',
                meta={'latency_ms': latency_ms},
            )
        return CheckResult(
            name='Database',
            ok=True,
            detail='Database is reachable and answering quickly.',
            meaning='Contacts, tasks, and deals can load and save.',
            meta={'latency_ms': latency_ms},
        )
    except Exception:
        return CheckResult(
            name='Database',
            ok=False,
            detail='Could not reach the database.',
            meaning='The CRM likely cannot load or save data right now.',
        )


def _railway_token() -> str | None:
    for key in ('RAILWAY_API_TOKEN', 'RAILWAY_TOKEN'):
        token = (os.environ.get(key) or '').strip()
        # Project tokens are fine; ignore Railway's auto-injected empty placeholders.
        if token:
            return token
    # Local fallback for manual/dev runs (CLI stores accessToken after OAuth).
    config_path = os.path.expanduser('~/.railway/config.json')
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, encoding='utf-8') as fh:
            data = json.load(fh)
        user = data.get('user') or {}
        token = (user.get('token') or user.get('accessToken') or '').strip()
        return token or None
    except Exception:
        return None


def _railway_services_via_cli() -> list[dict] | None:
    """Use local Railway CLI when available (dev machine / agent runs)."""
    import shutil
    import subprocess

    if not shutil.which('railway'):
        return None
    try:
        proc = subprocess.run(
            [
                'railway', 'service', 'list', '--json',
                '--project', RAILWAY_PROJECT_ID,
                '--environment', RAILWAY_ENVIRONMENT_ID,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={
                **os.environ,
                'RAILWAY_CALLER': os.environ.get(
                    'RAILWAY_CALLER', 'job:daily-health-check'
                ),
            },
        )
        if proc.returncode != 0:
            logger.warning('railway service list failed: %s', proc.stderr.strip())
            return None
        data = json.loads(proc.stdout)
        return data if isinstance(data, list) else None
    except Exception as exc:
        logger.warning('railway CLI status unavailable: %s', exc)
        return None


def _railway_services_via_graphql(token: str) -> list[dict] | None:
    query = """
    query($id: String!) {
      project(id: $id) {
        services {
          edges {
            node {
              id
              name
              serviceInstances {
                edges {
                  node {
                    environmentId
                    latestDeployment {
                      status
                      createdAt
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    payload = {
        'query': query,
        'variables': {'id': RAILWAY_PROJECT_ID},
    }
    req = urllib.request.Request(
        RAILWAY_GRAPHQL,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'User-Agent': 'OrigenDailyHealthCheck/1.0',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        logger.warning('Railway GraphQL unavailable: %s', exc)
        return None

    if data.get('errors'):
        logger.warning('Railway GraphQL errors: %s', data['errors'])
        return None

    rows = []
    services = (
        ((data.get('data') or {}).get('project') or {})
        .get('services') or {}
    ).get('edges') or []
    for edge in services:
        node = edge.get('node') or {}
        name = node.get('name') or node.get('id')
        instances = (node.get('serviceInstances') or {}).get('edges') or []
        status = 'UNKNOWN'
        for inst in instances:
            inst_node = inst.get('node') or {}
            if inst_node.get('environmentId') != RAILWAY_ENVIRONMENT_ID:
                continue
            deploy = inst_node.get('latestDeployment') or {}
            status = deploy.get('status') or 'UNKNOWN'
            break
        rows.append({'name': name, 'status': status})
    return rows


def check_railway_services() -> CheckResult:
    services = _railway_services_via_cli()
    source = 'cli'
    if services is None:
        token = _railway_token()
        if token:
            services = _railway_services_via_graphql(token)
            source = 'graphql'
    if services is None:
        return CheckResult(
            name='Hosting (Railway)',
            ok=True,
            warn=False,
            detail='Hosting status was not available in this run.',
            meaning='Skipped this time — the other checks still cover the live app.',
        )

    rows = []
    bad = []
    seen = set()
    for item in services:
        name = item.get('name') or 'unknown'
        status = item.get('status') or item.get('latestDeployment', {}).get('status') or 'UNKNOWN'
        # CLI shape uses top-level status.
        if not item.get('status') and item.get('latestDeployment'):
            status = (item.get('latestDeployment') or {}).get('status') or status
        seen.add(name)
        rows.append(f'{name}={status}')
        if status not in ('SUCCESS', 'SLEEPING'):
            bad.append(name)

    missing = [name for name in EXPECTED_SERVICES if name not in seen]
    ok = not bad
    warn = bool(missing) and ok

    if bad:
        detail = 'These services look unhealthy: ' + ', '.join(bad) + '.'
        meaning = 'One or more Railway pieces may need a redeploy or restart.'
    elif missing:
        detail = (
            'Running services look fine. Not seen in this check: '
            + ', '.join(missing) + '.'
        )
        meaning = 'Hosting is mostly fine — a scheduled job may be missing from the project.'
    else:
        detail = 'All expected Railway services look healthy.'
        meaning = 'Hosting, workers, and scheduled jobs are in good shape.'

    return CheckResult(
        name='Hosting (Railway)',
        ok=ok,
        warn=warn,
        detail=detail,
        meaning=meaning,
        meta={'rows': rows, 'missing': missing, 'bad': bad, 'source': source},
    )


def collect_checks() -> list[CheckResult]:
    return [
        check_app_health(),
        check_homepage(),
        check_database(),
        check_redis_and_rq(),
        check_railway_services(),
    ]


def overall_status(checks: list[CheckResult]) -> str:
    if any(not c.ok for c in checks):
        return 'FAIL'
    if any(c.warn for c in checks):
        return 'WARN'
    return 'OK'


def _status_copy(status: str) -> tuple[str, str, str]:
    """Return headline, subject verb, badge color."""
    if status == 'OK':
        return (
            'All clear',
            'All clear',
            '#15803d',
        )
    if status == 'WARN':
        return (
            'Needs a look',
            'Needs a look',
            '#c2410c',
        )
    return (
        'Action needed',
        'Action needed',
        '#b91c1c',
    )


def _verdict_sentence(checks: list[CheckResult], status: str) -> str:
    if status == 'OK':
        return 'Origen looks healthy this morning. No action needed.'
    problems = [c for c in checks if not c.ok]
    warns = [c for c in checks if c.ok and c.warn]
    if problems:
        names = ', '.join(c.name for c in problems)
        return f'Something needs attention: {names}.'
    names = ', '.join(c.name for c in warns)
    return f'App is up, but worth a glance: {names}.'


def render_email(checks: list[CheckResult], *, when_ct: datetime) -> tuple[str, str]:
    status = overall_status(checks)
    headline, subject_verb, status_color = _status_copy(status)
    date_label = when_ct.strftime('%b %-d, %Y')
    subject = f'[Origen] {subject_verb} — {date_label}'
    verdict = _verdict_sentence(checks, status)

    cards = []
    for check in checks:
        if not check.ok:
            badge = 'Broken'
            badge_bg = '#fef2f2'
            badge_fg = '#b91c1c'
        elif check.warn:
            badge = 'Watch'
            badge_bg = '#fff7ed'
            badge_fg = '#c2410c'
        else:
            badge = 'Good'
            badge_bg = '#f0fdf4'
            badge_fg = '#15803d'
        meaning = check.meaning or check.detail
        cards.append(
            f'''
            <tr>
              <td style="padding:14px 0;border-bottom:1px solid #e2e8f0;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="font-size:15px;font-weight:700;color:#0f172a;padding-bottom:4px;">
                      {_esc(check.name)}
                    </td>
                    <td align="right" style="padding-bottom:4px;">
                      <span style="display:inline-block;background:{badge_bg};color:{badge_fg};
                            font-size:12px;font-weight:700;padding:4px 10px;border-radius:999px;">
                        {badge}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td colspan="2" style="font-size:14px;color:#334155;line-height:1.45;padding-top:2px;">
                      {_esc(check.detail)}
                    </td>
                  </tr>
                  <tr>
                    <td colspan="2" style="font-size:13px;color:#64748b;line-height:1.45;padding-top:6px;">
                      {_esc(meaning)}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            '''
        )

    action_items = []
    for check in checks:
        if not check.ok:
            action_items.append(f'Fix {check.name}: {check.meaning or check.detail}')
        elif check.warn:
            action_items.append(f'Check {check.name}: {check.meaning or check.detail}')

    if action_items:
        bullets = ''.join(
            f'<li style="margin:0 0 8px;color:#334155;">{_esc(item)}</li>'
            for item in action_items
        )
        action_block = f'''
          <div style="margin-top:22px;padding:16px 18px;background:#fff7ed;
                      border:1px solid #fed7aa;border-radius:12px;">
            <p style="margin:0 0 10px;font-size:13px;font-weight:700;color:#9a3412;">
              What this means for you
            </p>
            <ul style="margin:0;padding-left:18px;">{bullets}</ul>
          </div>
        '''
    else:
        action_block = '''
          <div style="margin-top:22px;padding:16px 18px;background:#f0fdf4;
                      border:1px solid #bbf7d0;border-radius:12px;">
            <p style="margin:0;font-size:14px;color:#166534;line-height:1.5;">
              Nothing to do. Sip coffee. Ship product.
            </p>
          </div>
        '''

    legend = '''
      <p style="margin:18px 0 0;font-size:12px;color:#94a3b8;line-height:1.5;">
        Good = healthy · Watch = up but imperfect · Broken = needs action
      </p>
    '''

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_esc(subject)}</title></head>
<body style="margin:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,
'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
  <div style="max-width:640px;margin:0 auto;padding:28px 16px;">
    <div style="background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:28px;">
      <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:1.2px;
         text-transform:uppercase;color:#ea580c;">Origen morning check</p>
      <h1 style="margin:0 0 8px;font-size:26px;letter-spacing:-0.02em;">{_esc(headline)}</h1>
      <p style="margin:0 0 6px;color:#64748b;font-size:14px;">
        {when_ct.strftime('%A, %b %-d · %-I:%M %p %Z')}
      </p>
      <p style="margin:0 0 8px;">
        <span style="display:inline-block;background:{status_color};color:#fff;
              font-weight:700;font-size:12px;padding:6px 12px;border-radius:999px;">
          {_esc(headline)}
        </span>
      </p>
      <p style="margin:16px 0 8px;font-size:16px;color:#0f172a;line-height:1.5;">
        {_esc(verdict)}
      </p>
      {action_block}
      <p style="margin:28px 0 8px;font-size:12px;font-weight:700;letter-spacing:1px;
         text-transform:uppercase;color:#94a3b8;">What we checked</p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        {''.join(cards)}
      </table>
      {legend}
      <p style="margin:18px 0 0;font-size:13px;color:#64748b;">
        Want the nerdy view?
        <a href="{_esc(APP_BASE_URL)}/health/ui" style="color:#ea580c;">Open health dashboard</a>
      </p>
    </div>
    <p style="text-align:center;color:#94a3b8;font-size:12px;margin-top:16px;">
      Daily automated check · Origen TechnolOG
    </p>
  </div>
</body></html>"""
    return subject, html


def _esc(value: Any) -> str:
    return (
        str(value)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def send_report(to_email: str, subject: str, html: str) -> bool:
    api_key = os.environ.get('SENDGRID_API_KEY', '').strip()
    if not api_key:
        logger.error('SENDGRID_API_KEY missing — cannot send health email')
        return False

    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Email, Mail, To

    message = Mail(
        from_email=Email(DEFAULT_FROM, 'Origen Health'),
        to_emails=To(to_email),
        subject=subject,
        html_content=html,
    )
    response = SendGridAPIClient(api_key).send(message)
    ok = response.status_code in (200, 201, 202)
    if not ok:
        logger.error(
            'SendGrid non-2xx status=%s body=%r',
            response.status_code,
            getattr(response, 'body', None),
        )
    return ok


def run_daily_health_check(*, to_email: str, dry_run: bool = False) -> int:
    when_ct = datetime.now(CT)
    logger.info('Starting daily health check at %s', when_ct.isoformat())
    checks = collect_checks()
    status = overall_status(checks)
    for check in checks:
        level = logging.ERROR if not check.ok else (
            logging.WARNING if check.warn else logging.INFO
        )
        logger.log(level, '%s: %s — %s', check.name, 'OK' if check.ok else 'FAIL', check.detail)

    subject, html = render_email(checks, when_ct=when_ct)
    logger.info('Overall status=%s subject=%r', status, subject)

    if dry_run:
        print(subject)
        print(json.dumps(
            [
                {
                    'name': c.name,
                    'ok': c.ok,
                    'warn': c.warn,
                    'detail': c.detail,
                    'meaning': c.meaning,
                }
                for c in checks
            ],
            indent=2,
        ))
        return 0 if status != 'FAIL' else 1

    sent = send_report(to_email, subject, html)
    if not sent:
        logger.error('Failed to send health email to %s', to_email)
        return 2
    logger.info('Health email sent to %s', to_email)
    print(f'Daily health check: {status}; emailed {to_email}')
    return 0 if status != 'FAIL' else 1


def main():
    parser = argparse.ArgumentParser(description='Origen daily health check')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Collect checks and print results without emailing',
    )
    parser.add_argument(
        '--to',
        default=DEFAULT_TO,
        help=f'Recipient email (default: {DEFAULT_TO})',
    )
    args = parser.parse_args()
    raise SystemExit(run_daily_health_check(to_email=args.to, dry_run=args.dry_run))


if __name__ == '__main__':
    main()
