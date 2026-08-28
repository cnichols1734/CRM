"""Shared email chrome from the shipped offer-summary mail.

``templates/email/offer_summary.html`` is the source of truth: a 600px white
column on #E8EBEE, a slate masthead and footer, a teal hairline, Poppins with
Century Gothic behind it. Marketing emails render into this same wrapper.
Do not invent a third language.

Brand orange (#f97316 / #ea580c) is for calls to action. The old marketing
navy hero (#102a43) is gone.
"""
from __future__ import annotations

import html
from typing import Optional

# Palette copied from templates/email/offer_summary.html. Keep these in
# lockstep with that file.
FONT = "'Poppins','Century Gothic','Helvetica Neue',Helvetica,Arial,sans-serif"
CANVAS = '#E8EBEE'
SLATE = '#6C7F93'
SLATE_DARK = '#5A6B7D'
TEAL = '#4EC8CD'
TEAL_TINT = '#E6F7F8'
TEAL_INK = '#0F4A4D'
INK = '#101113'
INK_SOFT = '#1A1D20'
INK_RULE = '#2A2E33'
FOG = '#F2F4F5'
BODY = '#4A5560'
MUTED = '#8B96A0'
DARK_BODY = '#B9C3CC'
HAIRLINE = '#E1E6EA'
FOOTER_INK = '#E4EAEF'
FOOTER_MUTED = '#C9D4DE'

# Product accent. Buttons and action links only.
ACCENT = '#f97316'
ACCENT_DARK = '#ea580c'

CONTENT_WIDTH = 600
FONT_HREF = (
    'https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700'
    '&display=swap'
)


def esc(value: Optional[str]) -> str:
    return html.escape(value or '', quote=True)


def _text(value: Optional[str]) -> Optional[str]:
    cleaned = (value or '').strip()
    return cleaned or None


def _absolute_asset(path: Optional[str]) -> Optional[str]:
    """Absolute, because a relative src is a broken image in an inbox."""
    from services.marketing.links import base_url

    cleaned = _text(path)
    if not cleaned or cleaned.startswith(('http://', 'https://')):
        return cleaned
    return f"{base_url()}/{cleaned.lstrip('/')}"


def brand_assets(organization) -> dict[str, Optional[str]]:
    """Marks for the slate masthead and footer.

    An organization that uploaded its own logo gets that in the masthead, and
    the footer falls back to the brokerage name in type: we know our own
    wordmark reads on the slate band, and we know nothing about theirs.
    """
    from config import Config

    own_logo = _text(getattr(organization, 'logo_url', None)) if organization else None
    name = None
    if organization is not None:
        name = (
            _text(getattr(organization, 'broker_name', None))
            or _text(getattr(organization, 'name', None))
        )
    return {
        'name': name,
        'mark_url': _absolute_asset(own_logo or Config.CLIENT_EMAIL_BRAND_MARK),
        'wordmark_url': None if own_logo else _absolute_asset(
            Config.CLIENT_EMAIL_BRAND_WORDMARK
        ),
        'license': _text(getattr(organization, 'broker_license_number', None))
        if organization is not None else None,
        'address': _text(getattr(organization, 'broker_address', None))
        if organization is not None else None,
    }


def teal_rule(width: int = 44, height: int = 3) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td width="{width}" height="{height}" bgcolor="{TEAL}" '
        f'style="width:{width}px;height:{height}px;background-color:{TEAL};'
        f'font-size:0;line-height:0;">&nbsp;</td></tr></table>'
    )


def masthead_html(
    *,
    name: Optional[str] = None,
    mark_url: Optional[str] = None,
    eyebrow: Optional[str] = None,
) -> str:
    """Centered slate band. Same mark treatment as the offer email."""
    if mark_url:
        brand = (
            f'<img src="{esc(mark_url)}" width="82" height="70" '
            f'alt="{esc(name or "Brokerage")}" '
            f'style="display:block;width:82px;height:70px;border:0;">'
        )
    else:
        brand = (
            f'<span style="font-family:{FONT};font-size:15px;font-weight:600;'
            f'letter-spacing:2.6px;text-transform:uppercase;color:#ffffff;">'
            f'{esc(name or "")}</span>'
        )

    eyebrow_html = ''
    if eyebrow:
        eyebrow_html = (
            f'<div style="font-family:{FONT};font-size:11px;font-weight:400;'
            f'letter-spacing:2.4px;text-transform:uppercase;color:{FOOTER_INK};'
            f'padding-top:12px;">{esc(eyebrow)}</div>'
        )

    return f'''
                <tr>
                    <td align="center" bgcolor="{SLATE}" class="header-padding" style="background-color:{SLATE};padding:26px 24px 24px 24px;">
                        {brand}{eyebrow_html}
                    </td>
                </tr>'''


def footer_html(
    *,
    name: Optional[str] = None,
    wordmark_url: Optional[str] = None,
    license_number: Optional[str] = None,
    address: Optional[str] = None,
    reason_line: Optional[str] = None,
    unsubscribe_html: Optional[str] = None,
    year: int,
) -> str:
    """Slate footer, then a darker legal band. Unsubscribe is marketing-only."""
    brand_row = ''
    if wordmark_url:
        brand_row = f'''
                    <tr>
                        <td align="center" style="padding-bottom:18px;">
                            <img src="{esc(wordmark_url)}" width="112" height="78" alt="{esc(name or "Brokerage")}" style="display:block;width:112px;height:78px;border:0;">
                        </td>
                    </tr>'''
    elif name:
        brand_row = f'''
                    <tr>
                        <td align="center" style="font-family:{FONT};font-size:13px;letter-spacing:2.6px;text-transform:uppercase;color:#ffffff;padding-bottom:16px;">{esc(name)}</td>
                    </tr>'''

    details: list[str] = []
    if license_number:
        details.append(f'Brokerage license #{esc(license_number)}')
    if address:
        details.append(esc(address))
    detail_html = '<br>'.join(details)

    legal: list[str] = []
    if reason_line:
        legal.append(esc(reason_line))
    if unsubscribe_html:
        legal.append(unsubscribe_html)
    legal.append(f'&copy; {year} {esc(name or "Origen Realty")}. Equal Housing Opportunity.')
    legal_html = '<br><br>'.join(legal)

    return f'''
                <tr>
                    <td bgcolor="{SLATE}" class="footer-padding gutter" style="background-color:{SLATE};padding:34px 40px 22px 40px;">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                            {brand_row}
                            <tr>
                                <td align="center" style="font-size:0;line-height:0;padding-bottom:18px;">
                                    {teal_rule(44, 2)}
                                </td>
                            </tr>
                            <tr>
                                <td align="center" class="unstyle-auto-detected-links" style="font-family:{FONT};font-size:12px;line-height:21px;font-weight:300;color:{FOOTER_INK};">
                                    {detail_html}
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
                <tr>
                    <td bgcolor="{SLATE_DARK}" class="footer-padding gutter" style="background-color:{SLATE_DARK};padding:18px 40px 22px 40px;">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                            <tr>
                                <td align="center" style="font-family:{FONT};font-size:11px;line-height:19px;font-weight:300;color:{FOOTER_MUTED};">
                                    {legal_html}
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>'''
