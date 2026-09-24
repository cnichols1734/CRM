"""First-party tracking for Gmail campaigns. No mailbox read access is used."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone
from html import escape, unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from flask import request
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError

from models import (MarketingSend, MarketingTracking, MarketingTrackingLink,
                    MarketingTrackingEvent, db)
from services.marketing.links import base_url

_TOKEN = re.compile(r'([1-9][0-9]{0,9})\.[A-Za-z0-9_-]{43}\Z')
_HREF = re.compile(r'''(\bhref\s*=\s*)(["'])(.*?)\2''', re.I | re.S)
_URL = re.compile(r'https?://[^\s<>]+')
FILTERS = ('all', 'opened', 'clicked', 'none')


def token(org_id):
    return f'{org_id}.{secrets.token_urlsafe(32)}'


def token_org(value):
    match = _TOKEN.fullmatch(value or '')
    return int(match[1]) if match and int(match[1]) <= 2147483647 else None


def public_url(kind, value):
    suffix = '.gif' if kind == 'open' else ''
    return f'{base_url()}/email/track/{kind}/{value}{suffix}'


def safe_destination(value):
    if not value or len(value) > 8192 or any(ord(c) < 32 for c in value) or '\\' in value:
        return False
    try:
        url = urlsplit(value)
        return url.scheme.lower() in ('http', 'https') and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def trackable(value):
    if not safe_destination(value):
        return False
    path = urlsplit(value).path.lower()
    return '/email/unsubscribe/' not in path and '/email/track/' not in path


class _Links(HTMLParser):
    """Change only anchor hrefs. Preserve email styles and Outlook comments."""
    def __init__(self, get_link):
        super().__init__(convert_charrefs=False)
        self.get_link = get_link
        self.parts = []
        self.active = None
        self.label = []

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        if tag == 'a':
            href = dict(attrs).get('href')
            self.active = self.get_link(href) if href else None
            self.label = []
            if self.active:
                raw = _HREF.sub(lambda m: m[1] + m[2] + escape(public_url('click', self.active.token), quote=True) + m[2], raw, count=1)
        self.parts.append(raw)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'a' and self.active:
            label = ' '.join(''.join(self.label).split())
            if label:
                self.active.label = label[:300]
            self.active = None
        self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        self.parts.append(data)
        if self.active:
            self.label.append(data)

    def handle_entityref(self, name):
        self.parts.append(f'&{name};')
        if self.active:
            self.label.append(unescape(f'&{name};'))

    def handle_charref(self, name):
        self.parts.append(f'&#{name};')
        if self.active:
            self.label.append(unescape(f'&#{name};'))

    def handle_comment(self, data):
        self.parts.append(f'<!--{data}-->')

    def handle_decl(self, decl):
        self.parts.append(f'<!{decl}>')


def prepare(send, subject, html, text):
    existing = MarketingTracking.query.filter_by(send_id=send.id, organization_id=send.organization_id).first()
    if existing:
        return existing
    tracking = MarketingTracking(organization_id=send.organization_id, send_id=send.id,
                                 token=token(send.organization_id), subject=subject[:300],
                                 html_body='', text_body='')
    db.session.add(tracking)
    db.session.flush()
    links = {}

    def get_link(destination):
        if not trackable(destination):
            return None
        if destination not in links:
            row = MarketingTrackingLink(organization_id=send.organization_id, tracking_id=tracking.id,
                token=token(send.organization_id), destination=destination,
                label=urlsplit(destination).hostname[:300])
            db.session.add(row)
            links[destination] = row
        return links[destination]

    parser = _Links(get_link)
    parser.feed(html)
    parser.close()
    body = ''.join(parser.parts)
    pixel = f'<img src="{public_url("open", tracking.token)}" width="1" height="1" alt="" style="border:0;width:1px;height:1px;" />'
    tracking.html_body = re.sub(r'</body\s*>', lambda m: pixel + m[0], body, count=1, flags=re.I) if re.search(r'</body\s*>', body, re.I) else body + pixel

    def rewrite(match):
        raw = match[0]
        destination = raw if raw in links else raw.rstrip('.,;!?')
        while destination.endswith(')') and destination.count(')') > destination.count('('):
            destination = destination[:-1]
        link = get_link(destination)
        return public_url('click', link.token) + raw[len(destination):] if link else raw

    tracking.text_body = _URL.sub(rewrite, text)
    db.session.flush()
    return tracking


def classify(sender_id=None, actor_id=None):
    if actor_id is not None and actor_id == sender_id:
        return 'sender'
    ua = request.headers.get('User-Agent', '').lower()
    purpose = (request.headers.get('Purpose', '') + request.headers.get('Sec-Purpose', '')).lower()
    if not ua or any(x in ua for x in ('bot', 'crawler', 'spider', 'headless', 'proofpoint', 'mimecast', 'barracuda', 'python-requests', 'curl/')) or 'prefetch' in purpose:
        return 'automated'
    # Image proxies alone do not establish whether a person read a message.
    return 'observed'


def record(tracking, kind, *, link=None, actor_id=None, now=None):
    now = now or datetime.utcnow()
    classification = classify(tracking.send.user_id, actor_id)
    # Coalesce identical requests within ten seconds, without storing raw IPs.
    bucket = int(now.replace(tzinfo=timezone.utc).timestamp()) // 10
    identity = f'{kind}:{link.id if link else 0}:{classification}:{request.remote_addr}:{request.headers.get("User-Agent", "")}:{bucket}'
    key = hmac.new(tracking.token.encode(), identity.encode(), hashlib.sha256).hexdigest()
    event = MarketingTrackingEvent(organization_id=tracking.organization_id, tracking_id=tracking.id,
        link_id=link.id if link else None, kind=kind, classification=classification,
        occurred_at=now, dedupe_key=key)
    try:
        with db.session.begin_nested():
            db.session.add(event)
            db.session.flush()
    except IntegrityError:
        return False
    return True


def totals_query():
    event = MarketingTrackingEvent
    return db.session.query(
        event.tracking_id.label('tracking_id'),
        func.sum(case((db.and_(event.kind == 'open', event.classification == 'observed'), 1), else_=0)).label('opens'),
        func.sum(case((db.and_(event.kind == 'click', event.classification == 'observed'), 1), else_=0)).label('clicks'),
        func.sum(case((event.classification != 'observed', 1), else_=0)).label('excluded'),
        func.max(case((event.classification == 'observed', event.occurred_at))).label('last_activity'),
    )


def per_send(send_ids, org_id):
    if not send_ids:
        return {}
    records = db.session.query(MarketingTracking.id, MarketingTracking.send_id).filter(MarketingTracking.send_id.in_(send_ids), MarketingTracking.organization_id == org_id).all()
    ids = [r.id for r in records]
    totals = {row.tracking_id: row for row in totals_query().filter(MarketingTrackingEvent.tracking_id.in_(ids), MarketingTrackingEvent.organization_id == org_id).group_by(MarketingTrackingEvent.tracking_id)} if ids else {}
    return {r.send_id: dict(tracking_id=r.id, opens=int(totals[r.id].opens or 0) if r.id in totals else 0,
        clicks=int(totals[r.id].clicks or 0) if r.id in totals else 0,
        excluded=int(totals[r.id].excluded or 0) if r.id in totals else 0,
        last_activity=totals[r.id].last_activity if r.id in totals else None) for r in records}


def campaign_summary(campaign):
    """Count addresses once across a campaign and separately within each step."""
    campaign_tracking = db.session.query(MarketingTracking.id).join(MarketingSend).filter(
        MarketingSend.campaign_id == campaign.id, MarketingSend.organization_id == campaign.organization_id)
    events = totals_query().filter(
        MarketingTrackingEvent.organization_id == campaign.organization_id,
        MarketingTrackingEvent.tracking_id.in_(campaign_tracking),
    ).group_by(MarketingTrackingEvent.tracking_id).subquery()
    query = db.session.query(
        func.count(MarketingTracking.id).label('tracked'),
        func.count(MarketingSend.id).label('sent'),
        func.count(func.distinct(case((events.c.opens > 0, func.lower(MarketingSend.to_email))))).label('opened'),
        func.count(func.distinct(case((events.c.clicks > 0, func.lower(MarketingSend.to_email))))).label('clicked'),
        func.coalesce(func.sum(events.c.opens), 0).label('opens'),
        func.coalesce(func.sum(events.c.clicks), 0).label('clicks'),
        func.coalesce(func.sum(events.c.excluded), 0).label('excluded'),
    ).select_from(MarketingSend).outerjoin(MarketingTracking, MarketingTracking.send_id == MarketingSend.id).outerjoin(events, events.c.tracking_id == MarketingTracking.id).filter(
        MarketingSend.organization_id == campaign.organization_id, MarketingSend.campaign_id == campaign.id,
        MarketingSend.sent_at.isnot(None))
    summary = dict(query.one()._mapping)
    steps = {r.step_id: {k: v for k,v in r._mapping.items() if k != 'step_id'} for r in query.add_columns(MarketingSend.step_id).group_by(MarketingSend.step_id)}
    return dict(summary=summary, steps=steps)


def revision(campaign):
    return str(db.session.query(func.max(MarketingTrackingEvent.id)).join(MarketingTracking).join(MarketingSend).filter(
        MarketingSend.campaign_id == campaign.id, MarketingSend.organization_id == campaign.organization_id,
        MarketingTrackingEvent.organization_id == campaign.organization_id,
    ).scalar() or 0)
