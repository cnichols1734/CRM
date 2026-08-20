"""The locked wrapper every marketing email renders into.

The layout is lifted from the transactional templates in ``email_templates/``:
600px table, dark header, orange hairline, white card, DM Sans. Keeping that
system means marketing mail reads as part of the same product rather than as a
generic newsletter.

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

# Matched to email_templates/ so marketing and transactional mail agree.
CANVAS = '#f0f4f8'
CARD = '#ffffff'
HEADER_DARK = '#102a43'
HEADER_GRADIENT = 'linear-gradient(135deg, #1e2d3d 0%, #102a43 100%)'
ACCENT = '#f97316'
ACCENT_DARK = '#ea580c'
INK = '#102a43'
INK_BODY = '#486581'
INK_MUTED = '#627d98'
INK_FAINT = '#829ab1'
INK_FAINTEST = '#9fb3c8'
HAIRLINE = '#e2e8f0'
FOOTER_BG = '#f8fafc'
FOOTER_BORDER = '#e8eff5'
INSET_BG = '#f8fafc'

FONT = "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
CONTENT_WIDTH = 600


@dataclass
class ShellContext:
    """Everything the wrapper needs that does not come from blocks."""

    # Header
    header_title: str = 'AgentFlow'
    logo_url: Optional[str] = None

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
    if ctx.logo_url:
        brand = (
            f'<img src="{esc(ctx.logo_url)}" alt="{esc(ctx.header_title)}" '
            f'width="180" style="display:block;margin:0 auto;max-width:180px;'
            f'height:auto;border:0;">'
        )
    else:
        brand = (
            f'<h1 style="margin:0;font-family:{FONT};font-size:24px;'
            f'font-weight:700;letter-spacing:0.3px;color:#ffffff;">'
            f'{esc(ctx.header_title)}</h1>'
        )

    return f'''
        <tr>
            <td class="header-padding" bgcolor="{HEADER_DARK}" style="background-color:{HEADER_DARK};background:{HEADER_GRADIENT};padding:32px 40px;text-align:center;">
                {brand}
            </td>
        </tr>
        <tr>
            <td bgcolor="{ACCENT}" style="height:4px;background-color:{ACCENT};background:linear-gradient(90deg, {ACCENT} 0%, #fb923c 50%, {ACCENT} 100%);line-height:4px;font-size:0;">&nbsp;</td>
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

    disclosure_parts = [ctx.brokerage_name]
    if ctx.brokerage_license:
        disclosure_parts.append(f'License #{ctx.brokerage_license}')
    disclosure = ' · '.join(part for part in disclosure_parts if part)
    if disclosure:
        lines.append(
            f'<p style="margin:0 0 4px 0;font-family:{FONT};font-size:12px;'
            f'font-weight:600;color:#334e68;">{esc(disclosure)}</p>'
        )

    if ctx.brokerage_address:
        lines.append(
            f'<p style="margin:0 0 12px 0;font-family:{FONT};font-size:11px;'
            f'color:{INK_FAINTEST};">{esc(ctx.brokerage_address)}</p>'
        )

    lines.append(
        f'<p style="margin:0 0 6px 0;font-family:{FONT};font-size:11px;'
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
            <td bgcolor="{FOOTER_BG}" style="background-color:{FOOTER_BG};border-top:1px solid {FOOTER_BORDER};padding:26px 40px;text-align:center;">
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


def wrap(content_html: str, ctx: ShellContext) -> str:
    """Put rendered blocks inside the branded wrapper."""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{esc(ctx.header_title)}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
    body, table, td, p, a, li {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
    table, td {{ mso-table-lspace:0pt; mso-table-rspace:0pt; }}
    img {{ -ms-interpolation-mode:bicubic; border:0; height:auto; line-height:100%; outline:none; text-decoration:none; }}
    body {{ margin:0 !important; padding:0 !important; width:100% !important; font-family:{FONT}; background-color:{CANVAS}; }}
    @media only screen and (max-width:600px) {{
        .container {{ width:100% !important; }}
        .content-padding {{ padding:32px 24px !important; }}
        .header-padding {{ padding:26px 24px !important; }}
        .stat-cell {{ display:block !important; width:100% !important; padding-bottom:16px !important; }}
    }}
</style>
</head>
<body style="margin:0;padding:0;background-color:{CANVAS};">
{_preheader(ctx)}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" bgcolor="{CANVAS}" style="background-color:{CANVAS};">
    <tr>
        <td align="center" style="padding:32px 16px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="{CONTENT_WIDTH}" class="container" style="max-width:{CONTENT_WIDTH}px;background-color:{CARD};border-radius:16px;overflow:hidden;">
{_header(ctx)}
                <tr>
                    <td class="content-padding" style="padding:40px 48px 36px 48px;">
{content_html}
                    </td>
                </tr>
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
