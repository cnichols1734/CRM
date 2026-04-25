"""
Simulate a SendGrid Inbound Parse webhook hit against the local Flask app.

Useful for iterating on the Magic Inbox pipeline without re-routing real
SendGrid traffic through ngrok every time. The script POSTs a realistic
multipart payload — same field names SendGrid Inbound Parse uses — to
``http://127.0.0.1:5011/webhooks/sendgrid/inbound-parse``.

Usage:

    # Make sure the dev server is up first:
    #   set -a && source ./.env && set +a
    #   python3 app.py

    # Find your inbox address:
    #   python3 scripts/simulate_inbound.py --who-am-i

    # Send a plain text email with a contact in the body:
    python3 scripts/simulate_inbound.py text

    # Email with a signature block at the bottom:
    python3 scripts/simulate_inbound.py signature

    # Forward a .vcf attachment:
    python3 scripts/simulate_inbound.py vcard

    # Synthesized 'business card' image (requires Pillow):
    python3 scripts/simulate_inbound.py image

    # CSV of leads:
    python3 scripts/simulate_inbound.py csv

    # Use the +alias suffix to drop into a contact group:
    python3 scripts/simulate_inbound.py text --plus buyers

    # Skip the OpenAI call and use a canned 'Sarah Chen' response:
    python3 scripts/simulate_inbound.py text --mock-ai

    # Send to a specific user (default: first user that has an inbox):
    python3 scripts/simulate_inbound.py text --user chrisnichols17@gmail.com

    # Point at a deployed instance instead of localhost:
    python3 scripts/simulate_inbound.py text \\
        --base-url https://app.origentechnolog.com

The script reads ``SENDGRID_INBOUND_WEBHOOK_SECRET`` from the environment
(or .env) and appends it as ``?secret=...`` so the webhook accepts the hit
exactly the way real SendGrid traffic would.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Best-effort load of .env so the script "just works" when invoked with
# `python3 scripts/simulate_inbound.py` from a fresh shell.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / '.env', override=True)
except Exception:
    pass

import requests  # noqa: E402

logger = logging.getLogger('simulate_inbound')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s — %(message)s')


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES = {
    'text': {
        'subject': 'New lead — please add',
        'text': (
            "Hey, just met Sarah Chen at the open house in Mont Belvieu. "
            "She's looking in the $400k range, single family. "
            "Email: sarah.chen@example.com, cell 281-555-0142.\n\n"
            "Would love it if you could follow up Monday."
        ),
    },
    'signature': {
        'subject': 'Re: 123 Maple Ln — buyer interest',
        'text': (
            "Thanks for the showing. We'd like to put in an offer.\n"
            "Talk soon,\n\n"
            "—\n"
            "Marcus Whitman\n"
            "Senior Investment Officer\n"
            "Whitman Capital LLC\n"
            "marcus@whitmancap.example\n"
            "Direct: (713) 555-0188\n"
            "Mobile: 713.555.0190\n"
            "1500 Louisiana St, Suite 2200, Houston TX 77002\n"
        ),
    },
    'vcard': {
        'subject': 'Card from Realtor mixer',
        'text': 'Met this guy last night. Shared his vCard.',
        'attachment': {
            'filename': 'card.vcf',
            'mime': 'text/vcard',
            'data': (
                "BEGIN:VCARD\r\n"
                "VERSION:3.0\r\n"
                "FN:Priya Patel\r\n"
                "N:Patel;Priya;;;\r\n"
                "ORG:Lone Star Mortgage\r\n"
                "TITLE:Senior Loan Officer\r\n"
                "EMAIL;TYPE=INTERNET;TYPE=PREF:priya@lonestarmtg.example\r\n"
                "TEL;TYPE=CELL,VOICE:+12815550174\r\n"
                "ADR;TYPE=WORK:;;1200 Smith St;Houston;TX;77002;USA\r\n"
                "END:VCARD\r\n"
            ).encode('utf-8'),
        },
    },
    'csv': {
        'subject': 'Leads from this weekend',
        'text': 'CSV attached.',
        'attachment': {
            'filename': 'leads.csv',
            'mime': 'text/csv',
            'data': (
                "first_name,last_name,email,phone,notes\n"
                "Janet,Hill,janet.hill@example.com,2815550133,Open house Sat\n"
                "Daniel,Reyes,dreyes@example.com,7135550109,Wants 4bd in Katy\n"
                "Aisha,Khan,aisha.k@example.com,8325550120,Pre-approved\n"
            ).encode('utf-8'),
        },
    },
    'image': {
        'subject': 'Card from broker breakfast',
        'text': 'Snapped this card. Can you add him?',
        # Image attachment is generated on the fly so we don't ship a binary.
    },
}


# Canned AI response for --mock-ai. Mirrors the json_schema shape.
MOCK_AI_PAYLOAD = {
    'contacts': [{
        'first_name': 'Sarah',
        'last_name': 'Chen',
        'email': 'sarah.chen@example.com',
        'phone': '2815550142',
        'street_address': None,
        'city': 'Mont Belvieu',
        'state': 'TX',
        'zip_code': None,
        'notes': 'Buyer ~$400k. Met at open house.',
        'confidence': 'high',
    }],
    '_meta': {
        'model': 'gpt-5.4-nano',
        'tokens_in': 220,
        'tokens_out': 80,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_recipient(user_email: str | None) -> tuple[str, str]:
    """Boot the Flask app context just long enough to look up an inbox.

    Returns ``(recipient_address, login_email)``.
    """
    from app import create_app
    from models import User

    application = create_app()
    with application.app_context():
        q = User.query.filter(User.inbox_address.isnot(None))
        if user_email:
            q = q.filter(User.email.ilike(user_email))
        user = q.order_by(User.id.asc()).first()
        if user is None:
            raise SystemExit(
                'No user with an inbox address found. Run:\n'
                '  set -a && source ./.env && set +a\n'
                '  python3 scripts/backfill_inbox_addresses.py --commit'
            )
        return user.inbox_address, user.email


def _make_image_bytes(name: str = 'Marcus Whitman',
                      title: str = 'Senior Investment Officer',
                      org: str = 'Whitman Capital',
                      email: str = 'marcus@whitmancap.example',
                      phone: str = '(713) 555-0188') -> bytes:
    """Render a tiny PNG that looks vaguely like a business card.

    The AI cares about pixels, not aesthetics — we just need it to be a
    real, decodable image with text on it.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise SystemExit('Pillow is required for the `image` fixture. '
                         'Install with: python3 -m pip install --break-system-packages '
                         'Pillow') from e

    img = Image.new('RGB', (640, 360), color=(252, 250, 245))
    draw = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 36)
        font_med = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 22)
        font_sm = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 18)
    except Exception:
        font_big = font_med = font_sm = ImageFont.load_default()

    draw.rectangle((0, 0, 640, 12), fill=(15, 23, 42))
    draw.text((40, 60), name, fill=(15, 23, 42), font=font_big)
    draw.text((40, 110), title, fill=(100, 116, 139), font=font_med)
    draw.text((40, 142), org, fill=(100, 116, 139), font=font_med)
    draw.line((40, 200, 600, 200), fill=(226, 232, 240), width=1)
    draw.text((40, 220), email, fill=(15, 23, 42), font=font_sm)
    draw.text((40, 250), phone, fill=(15, 23, 42), font=font_sm)
    draw.text((40, 280), '1500 Louisiana St, Suite 2200, Houston TX 77002',
              fill=(15, 23, 42), font=font_sm)

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def _build_request(fixture_name: str, recipient: str, sender: str,
                   plus_alias: str | None, spam_score: float) -> tuple[dict, dict]:
    """Build the form + files dicts for a SendGrid-shaped POST."""
    if fixture_name not in FIXTURES:
        raise SystemExit(f'Unknown fixture: {fixture_name}. '
                         f'Pick one of: {", ".join(FIXTURES)}')

    if plus_alias:
        local, _, domain = recipient.partition('@')
        recipient = f'{local}+{plus_alias}@{domain}'

    fixture = FIXTURES[fixture_name]
    form = {
        'to': recipient,
        'from': f'Test Sender <{sender}>',
        'subject': fixture.get('subject', '(test)'),
        'text': fixture.get('text', ''),
        'envelope': json.dumps({'to': [recipient], 'from': sender}),
        'spam_score': str(spam_score),
        'spam_report': '',
        'attachments': '0',
    }

    files = {}
    if fixture_name == 'image':
        png = _make_image_bytes()
        files['attachment1'] = ('card.png', png, 'image/png')
        form['attachments'] = '1'
        form['attachment-info'] = json.dumps({'attachment1': {
            'filename': 'card.png', 'name': 'attachment1',
            'type': 'image/png',
        }})
    elif 'attachment' in fixture:
        a = fixture['attachment']
        files['attachment1'] = (a['filename'], a['data'], a['mime'])
        form['attachments'] = '1'
        form['attachment-info'] = json.dumps({'attachment1': {
            'filename': a['filename'], 'name': 'attachment1',
            'type': a['mime'],
        }})

    return form, files


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _post_via_http(url: str, form: dict, files: dict, secret: str | None,
                   timeout: int = 30) -> requests.Response:
    """POST to a running Flask app over HTTP (the realistic path)."""
    if secret:
        sep = '&' if '?' in url else '?'
        url = f'{url}{sep}secret={secret}'
    return requests.post(url, data=form, files=files, timeout=timeout)


def _post_via_test_client(form: dict, files: dict, *,
                          mock_ai: bool,
                          secret: str | None) -> tuple[int, str]:
    """Hit the webhook through Flask's test client without spawning the
    server. Useful when you want the AI mocked for fast offline iteration.
    """
    from app import create_app
    application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    test_files = {k: (io.BytesIO(v[1]), v[0], v[2]) for k, v in files.items()}
    payload = {**form, **test_files}
    url = '/webhooks/sendgrid/inbound-parse'
    if secret:
        url = f'{url}?secret={secret}'

    with application.test_client() as client:
        if mock_ai:
            with patch(
                'services.contact_extraction.generate_contact_extraction',
                return_value=MOCK_AI_PAYLOAD,
            ):
                rv = client.post(url, data=payload,
                                 content_type='multipart/form-data')
        else:
            rv = client.post(url, data=payload,
                             content_type='multipart/form-data')
        return rv.status_code, rv.get_data(as_text=True)


def _print_result(message_id: int | None, login_email: str) -> None:
    """Look up the just-created InboundMessage and pretty-print it."""
    from app import create_app
    from models import Contact, InboundMessage, User

    application = create_app()
    with application.app_context():
        user = User.query.filter(User.email.ilike(login_email)).first()
        if user is None:
            return

        msg = (InboundMessage.query
               .filter_by(user_id=user.id)
               .order_by(InboundMessage.id.desc())
               .first())
        if msg is None:
            print('  (no InboundMessage row was written — '
                  'likely the recipient lookup failed)')
            return

        contacts = []
        for cid in (msg.created_contact_ids or []):
            c = Contact.query.get(cid)
            if c is not None:
                contacts.append(c)

        print('')
        print(f'  InboundMessage  id={msg.id}')
        print(f'    status        {msg.status}')
        print(f'    source_kind   {msg.source_kind}')
        if msg.plus_alias:
            print(f'    plus_alias    {msg.plus_alias}')
        if msg.ai_model:
            print(f'    ai_model      {msg.ai_model}'
                  f'  tokens_in={msg.ai_tokens_in}'
                  f'  tokens_out={msg.ai_tokens_out}'
                  f'  cost~{msg.ai_cost_cents}¢')
        if msg.error_message:
            print(f'    error         {msg.error_message}')
        if contacts:
            print(f'    created_contacts:')
            for c in contacts:
                print(f'      - id={c.id}  {c.first_name} {c.last_name}'
                      f'  email={c.email or "—"}  phone={c.phone or "—"}')
        elif msg.status == 'processed':
            print('    (created_contact_ids set but rows not found)')
        print('')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('fixture', nargs='?', default='text',
                        choices=list(FIXTURES) + ['who-am-i'],
                        help='Which payload to send (default: text). '
                             'Pass "who-am-i" to just print your inbox.')
    parser.add_argument('--user', default=None,
                        help='Email of the user to address. '
                             'Defaults to the first user with an inbox.')
    parser.add_argument('--plus', default=None,
                        help='Plus-alias to append (e.g. buyers, sphere).')
    parser.add_argument('--sender', default='you@example.com',
                        help='From: address on the simulated email.')
    parser.add_argument('--spam-score', type=float, default=0.0)
    parser.add_argument('--base-url', default='http://127.0.0.1:5011',
                        help='Where the dev server is listening.')
    parser.add_argument('--secret', default=None,
                        help='Override SENDGRID_INBOUND_WEBHOOK_SECRET.')
    parser.add_argument('--mock-ai', action='store_true',
                        help='Skip OpenAI; force a canned Sarah Chen contact.')
    parser.add_argument('--in-process', action='store_true',
                        help='Hit the webhook via Flask test client instead '
                             'of HTTP. Required for --mock-ai. Useful when '
                             'the dev server is not running.')
    # Positional alias also for `who-am-i`.
    parser.add_argument('--who-am-i', action='store_true',
                        help='Print your inbox address and exit.')
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    if args.fixture == 'who-am-i' or args.who_am_i:
        recipient, login_email = _resolve_recipient(args.user)
        print(f'  inbox address : {recipient}')
        print(f'  login email   : {login_email}')
        return 0

    recipient, login_email = _resolve_recipient(args.user)
    secret = args.secret or os.getenv('SENDGRID_INBOUND_WEBHOOK_SECRET')

    form, files = _build_request(
        args.fixture, recipient, sender=args.sender,
        plus_alias=args.plus, spam_score=args.spam_score,
    )

    print('')
    print(f'  fixture       : {args.fixture}')
    print(f'  recipient     : {form["to"]}')
    print(f'  sender        : {form["from"]}')
    print(f'  attachments   : {form.get("attachments", "0")}')
    print(f'  mock-ai       : {args.mock_ai}')
    if args.in_process or args.mock_ai:
        print('  transport     : in-process Flask test client')
        if args.mock_ai and not args.in_process:
            logger.info('--mock-ai implies --in-process. Switching transport.')
        status, body = _post_via_test_client(form, files,
                                             mock_ai=args.mock_ai,
                                             secret=secret)
    else:
        url = f'{args.base_url.rstrip("/")}/webhooks/sendgrid/inbound-parse'
        print(f'  transport     : HTTP POST → {url}')
        try:
            rv = _post_via_http(url, form, files, secret)
        except requests.ConnectionError as e:
            raise SystemExit(
                f'Could not reach {url}. Is the dev server running?\n'
                f'  set -a && source ./.env && set +a\n'
                f'  python3 app.py\n\n'
                f'Underlying error: {e}'
            )
        status, body = rv.status_code, rv.text
    print(f'  http status   : {status}')

    # Give the orchestrator a beat to finish writing rows when running
    # against a live server.
    if not args.in_process and not args.mock_ai:
        time.sleep(0.5)

    _print_result(None, login_email)

    return 0 if status == 200 else 1


if __name__ == '__main__':
    sys.exit(main())
