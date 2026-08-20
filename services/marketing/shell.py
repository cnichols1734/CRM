"""The locked wrapper every marketing email renders into.

The design is the AgentFlow email system: a soft blue canvas, a white rounded
card, a light header bar, an optional dark gradient hero with a Fraunces
headline and an italic orange accent line, DM Sans body copy in slate, orange
calls to action, and a quiet footer. Marketing mail reads as part of the same
product family as the transactional mail rather than as a generic newsletter.

The branding is not ours, though. A "just checking in" note from an agent to her
past client should carry her brokerage, not AgentFlow. The header shows the org
logo or brokerage name, and the footer carries the license and address that
advertising rules require.

Nothing in this module is author-editable. The compliance footer in particular
is assembled here rather than from blocks, so an agent cannot delete the
unsubscribe link or the license disclosure.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from services.marketing.edit_marks import EDIT_CSS, mark

# Palette from the AgentFlow email system.
CANVAS = '#f0f4f8'
CARD = '#ffffff'
HERO_GRADIENT = 'linear-gradient(160deg, #0b1b2b 0%, #102a43 55%, #1a3a5c 100%)'
HERO_FALLBACK = '#102a43'
HERO_BODY = '#a8c3df'
HERO_INK = '#f8fafc'
ACCENT = '#f97316'
ACCENT_DARK = '#ea580c'
INK = '#102a43'
INK_BODY = '#334e68'
INK_MUTED = '#627d98'
INK_FAINT = '#829ab1'
INK_FAINTEST = '#9fb3c8'
INK_SOFT = '#94a3b8'
HAIRLINE = '#e2e8f0'
HAIRLINE_SOFT = '#eef2f7'
DASHED = '#cbd5e1'
FOOTER_BG = '#f8fafc'
FOOTER_BORDER = '#e8eff5'
INSET_BG = '#f8fafc'
INSET_BORDER = '#dbe4ee'

FONT = "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
# Display face for hero headlines and step numerals. Only Apple Mail and a few
# others load web fonts, so the Georgia fallback has to carry the same weight.
DISPLAY = "'Fraunces', Georgia, 'Times New Roman', serif"
CONTENT_WIDTH = 600


@dataclass
class ShellContext:
    """Everything the wrapper needs that does not come from blocks."""

    # Header
    header_title: str = 'AgentFlow'
    logo_url: Optional[str] = None
    # Small uppercase label opposite the brand, usually the email's purpose:
    # "Market update", "Open house". Keeps the header from looking empty.
    eyebrow: Optional[str] = None

    # Signature block and footer attribution
    agent_name: Optional[str] = None
    agent_title: Optional[str] = None
    agent_email: Optional[str] = None
    agent_phone: Optional[str] = None

    # Advertising disclosure. Sending is blocked upstream when these are unset.
    brokerage_name: Optional[str] = None
    brokerage_license: Optional[str] = None
    brokerage_address: Optional[str] = None

    # CAN-SPAM. A preview has no real send behind it, so this may be None and
    # the footer shows an inert placeholder instead of a working link.
    unsubscribe_url: Optional[str] = None
    reason_line: str = 'You are receiving this because you are a client or contact of ours.'

    preheader: Optional[str] = None
    year: Optional[int] = None

    def resolved_year(self) -> int:
        return self.year or datetime.utcnow().year


def esc(value: Optional[str]) -> str:
    return html.escape(value or '', quote=True)


def _header(ctx: ShellContext) -> str:
    """Light bar with the brokerage on the left and a purpose label opposite."""
    if ctx.logo_url:
        brand = (
            f'<img src="{esc(ctx.logo_url)}" alt="{esc(ctx.header_title)}" '
            f'height="28" style="display:block;max-height:28px;width:auto;border:0;">'
        )
    else:
        brand = (
            f'<span style="font-family:{FONT};font-size:16px;font-weight:700;'
            f'letter-spacing:0.2px;color:{INK};">{esc(ctx.header_title)}</span>'
        )

    eyebrow = ''
    if ctx.eyebrow:
        eyebrow = (
            f'<span style="font-family:{FONT};font-size:11px;font-weight:600;'
            f'color:{INK_SOFT};letter-spacing:1.2px;text-transform:uppercase;">'
            f'{esc(ctx.eyebrow)}</span>'
        )

    return f'''
                <tr>
                    <td class="header-padding" style="padding:22px 32px;background-color:{CARD};border-bottom:1px solid {HAIRLINE_SOFT};">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                            <tr>
                                <td align="left">{brand}</td>
                                <td align="right">{eyebrow}</td>
                            </tr>
                        </table>
                    </td>
                </tr>'''


def hero(block: dict) -> str:
    """The dark banner row. Full width, so it sits outside the padded content.

    Rendered here rather than in ``render.py`` because it is a shell element the
    author fills in, not a content block they can restyle.
    """
    parts: list[str] = []

    if block.get('eyebrow'):
        parts.append(
            f'<p style="margin:0 0 18px 0;font-family:{FONT};font-size:11px;'
            f'font-weight:700;color:{ACCENT};letter-spacing:2.4px;'
            f'text-transform:uppercase;">{mark(esc(block["eyebrow"]), "eyebrow")}</p>'
        )

    accent = ''
    if block.get('accent'):
        accent = (
            f'<br><em style="font-style:italic;color:{ACCENT};font-weight:500;">'
            f'{mark(esc(block["accent"]), "accent")}</em>'
        )
    parts.append(
        f'<h1 class="hero-title" style="margin:0 0 18px 0;font-family:{DISPLAY};'
        f'font-size:42px;line-height:1.07;font-weight:500;color:{HERO_INK};'
        f'-webkit-text-fill-color:{HERO_INK};letter-spacing:-0.5px;">'
        f'{mark(esc(block["title"]), "title")}{accent}</h1>'
    )

    if block.get('text'):
        parts.append(
            f'<p style="margin:0;font-family:{FONT};font-size:16px;'
            f'line-height:1.6;color:{HERO_BODY};max-width:448px;">'
            f'{mark(esc(block["text"]), "text")}</p>'
        )

    body = '\n'.join(parts)
    return f'''
                <tr>
                    <td class="hero-pad" bgcolor="{HERO_FALLBACK}" style="background-color:{HERO_FALLBACK};background:{HERO_GRADIENT};padding:48px 48px 44px;">
                        {body}
                    </td>
                </tr>'''


def _footer(ctx: ShellContext) -> str:
    """Advertising disclosure, mailing address, and unsubscribe.

    Assembled from org records so it cannot be edited out of a template.
    CAN-SPAM requires the address and a working opt-out; Texas advertising
    rules require the brokerage identification.

    Deliberately compliance-only. The agent's own name and contact details are
    the signature block's job, and carrying them here too made every email say
    the same thing twice.
    """
    lines: list[str] = []

    if ctx.brokerage_name:
        lines.append(
            f'<p style="margin:0 0 6px 0;font-family:{FONT};font-size:14px;'
            f'font-weight:700;color:{INK_BODY};">{esc(ctx.brokerage_name)}</p>'
        )
    if ctx.brokerage_license:
        lines.append(
            f'<p style="margin:0 0 12px 0;font-family:{FONT};font-size:12px;'
            f'color:{INK_FAINT};">License #{esc(ctx.brokerage_license)}</p>'
        )
    if ctx.brokerage_address:
        lines.append(
            f'<p style="margin:0 0 14px 0;font-family:{FONT};font-size:11px;'
            f'color:{INK_FAINTEST};">{esc(ctx.brokerage_address)}</p>'
        )

    lines.append(
        f'<p style="margin:0 0 4px 0;font-family:{FONT};font-size:11px;'
        f'color:{INK_FAINTEST};line-height:1.5;">{esc(ctx.reason_line)}</p>'
    )

    if ctx.unsubscribe_url:
        opt_out = (
            f'<a href="{esc(ctx.unsubscribe_url)}" style="color:{INK_MUTED};'
            f'text-decoration:underline;">Unsubscribe</a>'
        )
    else:
        # Preview has no send row to key an opt-out to. Showing the row anyway
        # keeps the preview honest about what recipients will see.
        opt_out = f'<span style="color:{INK_FAINTEST};">Unsubscribe</span>'

    lines.append(
        f'<p style="margin:0;font-family:{FONT};font-size:11px;'
        f'color:{INK_FAINTEST};">{opt_out}</p>'
    )

    body = '\n'.join(lines)
    return f'''
                <tr>
                    <td class="footer-padding" bgcolor="{FOOTER_BG}" style="background-color:{FOOTER_BG};border-top:1px solid {FOOTER_BORDER};padding:28px 40px;text-align:center;">
                        {body}
                    </td>
                </tr>'''


def _preheader(ctx: ShellContext) -> str:
    if not ctx.preheader:
        return ''
    # Trailing invisible characters stop the client from pulling body copy into
    # the inbox preview once the intended line runs out.
    padding = '&#847;&zwnj;&nbsp;' * 30
    return (
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
        + esc(ctx.preheader)
        + padding
        + '</div>'
    )


def wrap(
    content_html: str,
    ctx: ShellContext,
    hero_html: str = '',
    *,
    editable: bool = False,
) -> str:
    """Put rendered blocks inside the branded wrapper.

    ``hero_html`` is a separate argument because the hero is full-bleed and has
    to be its own table row, outside the padded content cell.
    """
    content = ''
    if content_html.strip():
        content = f'''
                <tr>
                    <td class="content-padding" style="padding:40px 48px 36px 48px;">
{content_html}
                    </td>
                </tr>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{esc(ctx.header_title)}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&display=swap" rel="stylesheet">
<style>
    body, table, td, p, a, li {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
    table, td {{ mso-table-lspace:0pt; mso-table-rspace:0pt; }}
    img {{ -ms-interpolation-mode:bicubic; border:0; height:auto; line-height:100%; outline:none; text-decoration:none; }}
    body {{ margin:0 !important; padding:0 !important; width:100% !important; font-family:{FONT}; background-color:{CANVAS}; }}
    @media only screen and (max-width:600px) {{
        .container {{ width:100% !important; }}
        .content-padding {{ padding:32px 24px !important; }}
        .header-padding {{ padding:20px 24px !important; }}
        .footer-padding {{ padding:24px 24px !important; }}
        .hero-pad {{ padding:36px 26px 32px !important; }}
        .hero-title {{ font-size:33px !important; }}
        .stat-cell {{ display:block !important; width:100% !important; padding-bottom:16px !important; }}
    }}
    {EDIT_CSS if editable else ''}
</style>
</head>
<body style="margin:0;padding:0;background-color:{CANVAS};">
{_preheader(ctx)}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" bgcolor="{CANVAS}" style="background-color:{CANVAS};">
    <tr>
        <td align="center" style="padding:40px 16px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="{CONTENT_WIDTH}" class="container" style="max-width:{CONTENT_WIDTH}px;background-color:{CARD};border-radius:18px;overflow:hidden;box-shadow:0 4px 32px rgba(16,42,67,0.08);">
{_header(ctx)}{hero_html}{content}
{_footer(ctx)}
            </table>
        </td>
    </tr>
</table>
</body>
</html>'''


def footer_text(ctx: ShellContext) -> str:
    """Plain-text counterpart of the compliance footer."""
    lines: list[str] = []

    disclosure = [ctx.brokerage_name]
    if ctx.brokerage_license:
        disclosure.append(f'License #{ctx.brokerage_license}')
    joined = ' · '.join(p for p in disclosure if p)
    if joined:
        lines.append(joined)
    if ctx.brokerage_address:
        lines.append(ctx.brokerage_address)

    lines.append('')
    lines.append(ctx.reason_line)
    if ctx.unsubscribe_url:
        lines.append(f'Unsubscribe: {ctx.unsubscribe_url}')

    return '\n'.join(lines)
