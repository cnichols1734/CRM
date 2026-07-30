#!/usr/bin/env python3
"""Point Telegram at this deployment's B.O.B. webhook.

Usage:
    TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... \\
    TELEGRAM_WEBHOOK_PATH=... APP_BASE_URL=https://www.example.com \\
    python3 scripts/register_telegram_webhook.py
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    secret = os.getenv('TELEGRAM_WEBHOOK_SECRET')
    path = os.getenv('TELEGRAM_WEBHOOK_PATH')
    base = (os.getenv('APP_BASE_URL') or '').rstrip('/')

    missing = [name for name, val in [
        ('TELEGRAM_BOT_TOKEN', token),
        ('TELEGRAM_WEBHOOK_SECRET', secret),
        ('TELEGRAM_WEBHOOK_PATH', path),
        ('APP_BASE_URL', base),
    ] if not val]
    if missing:
        print(f'Missing: {", ".join(missing)}', file=sys.stderr)
        return 1

    url = f'{base}/webhooks/telegram/{path}'
    # Import after dotenv so Config is not required.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.messaging.telegram import TelegramTransport

    result = TelegramTransport(token).set_webhook(url, secret_token=secret)
    print(f'Registered webhook: {url}')
    print(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
