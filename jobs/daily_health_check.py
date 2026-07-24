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


def check_app_health() -> CheckResult:
    status, payload, latency_ms = _http_json(f'{APP_BASE_URL}/health')
    if status != 200 or not isinstance(payload, dict):
        return CheckResult(
            name='App /health',
            ok=False,
            detail=f'HTTP {status or "error"} in {latency_ms}ms',
            meta={'latency_ms': latency_ms, 'payload': payload},
        )

    health_status = payload.get('status', 'unknown')
    warnings = payload.get('warnings') or []
    checks = payload.get('checks') or {}
    db = checks.get('database') or {}
    external = checks.get('external') or {}
    memory = checks.get('memory') or {}
    bits = [
        f'status={health_status}',
        f'latency={latency_ms}ms',
        f"db={db.get('status', '?')} ({db.get('latency_ms', '?')}ms)",
        f"rss={memory.get('rss_mb', '?')}MB",
        f"version={payload.get('version', '?')}",
    ]
    for name, info in external.items():
        if isinstance(info, dict):
            bits.append(f"{name}={info.get('status', '?')}")

    ok = health_status == 'healthy' and status == 200
    warn = bool(warnings) or any(
        isinstance(info, dict) and info.get('status') not in ('connected', 'ok', None)
        for info in external.values()
    )
    detail = '; '.join(bits)
    if warnings:
        detail += f" | warnings: {', '.join(warnings)}"
    return CheckResult(
        name='App /health',
        ok=ok,
        warn=warn and ok,
        detail=detail,
        meta={'payload': payload, 'latency_ms': latency_ms},
    )


def check_homepage() -> CheckResult:
    status, _, latency_ms = _http_json(APP_BASE_URL + '/', timeout=15.0)
    # Homepage may redirect; treat 2xx/3xx as success.
    ok = 200 <= status < 400
    return CheckResult(
        name='Public site',
        ok=ok,
        detail=f'HTTP {status or "error"} in {latency_ms}ms',
        meta={'latency_ms': latency_ms},
    )


def check_redis_and_rq() -> CheckResult:
    redis_url = os.environ.get('REDIS_URL')
    if not redis_url:
        return CheckResult(
            name='Redis / RQ',
            ok=False,
            warn=True,
            detail='REDIS_URL not set',
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
                name='Redis / RQ',
                ok=False,
                detail=f'ping failed in {latency_ms}ms',
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

        depth_bits = ', '.join(f'{k}={v}' for k, v in depths.items() if v)
        detail = f'ping ok ({latency_ms}ms)'
        if depth_bits:
            detail += f'; queues: {depth_bits}'
        else:
            detail += '; queues empty'
        if failed:
            detail += f'; failed={failed}'

        warn = failed > 0 or any(v >= 25 for v in depths.values())
        return CheckResult(
            name='Redis / RQ',
            ok=True,
            warn=warn,
            detail=detail,
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
                name='Redis / RQ',
                ok=True,
                warn=True,
                detail='private Redis DNS unreachable from this host (checked on Railway cron)',
            )
        return CheckResult(
            name='Redis / RQ',
            ok=False,
            detail=f'error: {exc}',
        )


def check_database() -> CheckResult:
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        return CheckResult(
            name='Database',
            ok=False,
            warn=True,
            detail='DATABASE_URL not set',
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
        return CheckResult(
            name='Database',
            ok=True,
            warn=warn,
            detail=f'SELECT 1 ok ({latency_ms}ms)',
            meta={'latency_ms': latency_ms},
        )
    except Exception as exc:
        return CheckResult(
            name='Database',
            ok=False,
            detail=f'error: {exc}',
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
            name='Railway services',
            ok=True,
            warn=True,
            detail='Skipped (Railway CLI/API unavailable in this runtime)',
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
            bad.append(f'{name}:{status}')

    missing = [name for name in EXPECTED_SERVICES if name not in seen]
    detail = '; '.join(rows) if rows else 'No services found'
    detail += f' (via {source})'
    if missing:
        detail += f" | missing: {', '.join(missing)}"

    ok = not bad
    warn = bool(missing)
    return CheckResult(
        name='Railway services',
        ok=ok,
        warn=warn and ok,
        detail=detail if not bad else f"issues: {', '.join(bad)} | {detail}",
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


def render_email(checks: list[CheckResult], *, when_ct: datetime) -> tuple[str, str]:
    status = overall_status(checks)
    subject = f'[Origen] Daily health check — {status} — {when_ct.strftime("%b %-d, %Y")}'
    try:
        subject = subject  # %-d works on Unix
    except Exception:
        subject = f'[Origen] Daily health check — {status} — {when_ct.strftime("%b %d, %Y")}'

    rows_html = []
    for check in checks:
        if not check.ok:
            color = '#b91c1c'
            label = 'FAIL'
        elif check.warn:
            color = '#c2410c'
            label = 'WARN'
        else:
            color = '#15803d'
            label = 'OK'
        rows_html.append(
            '<tr>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;'
            f'font-weight:600;color:#0f172a">{_esc(check.name)}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;'
            f'color:{color};font-weight:700">{label}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;'
            f'color:#475569;font-size:13px">{_esc(check.detail)}</td>'
            '</tr>'
        )

    status_color = {
        'OK': '#15803d',
        'WARN': '#c2410c',
        'FAIL': '#b91c1c',
    }[status]

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{_esc(subject)}</title></head>
<body style="margin:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,
'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
  <div style="max-width:720px;margin:0 auto;padding:28px 16px;">
    <div style="background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:24px;">
      <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:1.2px;
         text-transform:uppercase;color:#ea580c;">Origen TechnolOG</p>
      <h1 style="margin:0 0 8px;font-size:22px;">Daily health check</h1>
      <p style="margin:0 0 18px;color:#64748b;font-size:14px;">
        {when_ct.strftime('%A, %b %-d %Y · %-I:%M %p %Z')}
      </p>
      <p style="margin:0 0 20px;">
        <span style="display:inline-block;background:{status_color};color:#fff;
              font-weight:700;font-size:13px;padding:6px 12px;border-radius:999px;">
          {status}
        </span>
      </p>
      <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
             style="border-collapse:collapse;border:1px solid #e2e8f0;border-radius:10px;">
        <thead>
          <tr style="background:#f8fafc;">
            <th align="left" style="padding:10px 12px;font-size:12px;color:#64748b;">Check</th>
            <th align="left" style="padding:10px 12px;font-size:12px;color:#64748b;">Status</th>
            <th align="left" style="padding:10px 12px;font-size:12px;color:#64748b;">Detail</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
      <p style="margin:20px 0 0;font-size:13px;color:#64748b;">
        Live probe:
        <a href="{_esc(APP_BASE_URL)}/health" style="color:#ea580c;">
          {_esc(APP_BASE_URL)}/health
        </a>
        ·
        <a href="{_esc(APP_BASE_URL)}/health/ui" style="color:#ea580c;">Health UI</a>
      </p>
    </div>
    <p style="text-align:center;color:#94a3b8;font-size:12px;margin-top:16px;">
      Automated Railway cron · Daily Health Check
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
            [{'name': c.name, 'ok': c.ok, 'warn': c.warn, 'detail': c.detail} for c in checks],
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
