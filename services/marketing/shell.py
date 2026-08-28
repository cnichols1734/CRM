"""The locked wrapper every marketing email renders into.

The chrome is the shipped offer-summary email: a 600px white column on #E8EBEE,
a slate masthead and footer, a teal hairline, Poppins. Tokens and bands live in
``services/email_chrome.py``. Marketing adds a hero, a signature block, and the
CAN-SPAM footer. It does not invent a third look.

Nothing in this module is author-editable. The compliance footer in particular
is assembled here rather than from blocks, so an agent cannot delete the
unsubscribe link or the license disclosure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from services.email_chrome import (
    ACCENT,
    ACCENT_DARK,
    BODY,
    CANVAS,
    CONTENT_WIDTH,
    FONT,
    FONT_HREF,
    FOG,
    FOOTER_MUTED,
    HAIRLINE,
    INK,
    MUTED,
    SLATE,
    TEAL,
    esc,
    footer_html,
    masthead_html,
    teal_rule,
)
from services.marketing.edit_marks import EDIT_CSS, mark

# Aliases the renderer already imports. INK_BODY is offer body copy, not the
# old navy. DISPLAY is the same face as body; the offer email does not use a
# second type family.
INK_BODY = BODY
INK_MUTED = SLATE
INK_FAINT = MUTED
INK_FAINTEST = FOOTER_MUTED
INK_SOFT = MUTED
DASHED = HAIRLINE
INSET_BG = FOG
INSET_BORDER = HAIRLINE
DISPLAY = FONT
FOOTER_BG = SLATE
FOOTER_BORDER = SLATE


@dataclass
class ShellContext:
    """Everything the wrapper needs that does not come from blocks."""

    # Header
    header_title: str = 'AgentFlow'
    logo_url: Optional[str] = None
    mark_url: Optional[str] = None
    wordmark_url: Optional[str] = None
    # Small uppercase label under the mark, usually the email's purpose:
    # "Market update", "Open house". Keeps the masthead from looking empty
    # when the template has no hero of its own.
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


def _header(ctx: ShellContext) -> str:
    return masthead_html(
        name=ctx.brokerage_name or ctx.header_title,
        mark_url=ctx.mark_url or ctx.logo_url,
        eyebrow=ctx.eyebrow,
    )


def hero(block: dict) -> str:
    """Title band in the offer-email language. Full width, own table row.

    Rendered here rather than in ``render.py`` because it is a shell element the
    author fills in, not a content block they can restyle.
    """
    parts: list[str] = []

    if block.get('eyebrow'):
        parts.append(
            f'<tr><td style="font-family:{FONT};font-size:11px;letter-spacing:3.5px;'
            f'text-transform:uppercase;color:{SLATE};padding-bottom:12px;">'
            f'{mark(esc(block["eyebrow"]), "eyebrow")}</td></tr>'
        )

    parts.append(
        f'<tr><td style="padding-bottom:18px;font-size:0;line-height:0;">'
        f'{teal_rule(44, 3)}</td></tr>'
    )

    accent = ''
    if block.get('accent'):
        accent = (
            f'<br><span style="display:block;padding-top:10px;font-size:16px;'
            f'line-height:24px;font-weight:400;letter-spacing:0;text-transform:none;'
            f'color:{ACCENT};">{mark(esc(block["accent"]), "accent")}</span>'
        )
    parts.append(
        f'<tr><td class="hero-title h1" style="font-family:{FONT};font-size:32px;'
        f'line-height:38px;font-weight:700;letter-spacing:-0.6px;'
        f'text-transform:uppercase;color:{INK};">'
        f'{mark(esc(block["title"]), "title")}{accent}</td></tr>'
    )

    if block.get('text'):
        parts.append(
            f'<tr><td style="padding-top:18px;font-family:{FONT};font-size:15px;'
            f'line-height:26px;font-weight:300;color:{BODY};">'
            f'{mark(esc(block["text"]), "text")}</td></tr>'
        )

    body = '\n'.join(parts)
    return f'''
                <tr>
                    <td bgcolor="#ffffff" class="hero-pad gutter" style="background-color:#ffffff;padding:44px 40px 8px 40px;">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                            {body}
                        </table>
                    </td>
                </tr>'''


def _unsubscribe(ctx: ShellContext) -> str:
    if ctx.unsubscribe_url:
        return (
            f'<a href="{esc(ctx.unsubscribe_url)}" style="color:{FOOTER_MUTED};'
            f'text-decoration:underline;">Unsubscribe</a>'
        )
    # Preview has no send row to key an opt-out to. Showing the row anyway
    # keeps the preview honest about what recipients will see.
    return f'<span style="color:{FOOTER_MUTED};">Unsubscribe</span>'


def _footer(ctx: ShellContext) -> str:
    """Advertising disclosure, mailing address, and unsubscribe.

    Assembled from org records so it cannot be edited out of a template.
    CAN-SPAM requires the address and a working opt-out; Texas advertising
    rules require the brokerage identification.

    Deliberately compliance-only. The agent's own name and contact details are
    the signature block's job, and carrying them here too made every email say
    the same thing twice.
    """
    return footer_html(
        name=ctx.brokerage_name or ctx.header_title,
        wordmark_url=ctx.wordmark_url,
        license_number=ctx.brokerage_license,
        address=ctx.brokerage_address,
        reason_line=ctx.reason_line,
        unsubscribe_html=_unsubscribe(ctx),
        year=ctx.resolved_year(),
    )


def _preheader(ctx: ShellContext) -> str:
    if not ctx.preheader:
        return ''
    # Trailing invisible characters stop the client from pulling body copy into
    # the inbox preview once the intended line runs out.
    padding = '&#847;&zwnj;&nbsp;' * 24
    return (
        '<div style="display:none;font-size:1px;line-height:1px;max-height:0;'
        'max-width:0;opacity:0;overflow:hidden;mso-hide:all;'
        f'font-family:Arial,sans-serif;color:{CANVAS};">'
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
                    <td bgcolor="#ffffff" class="content-padding gutter" style="background-color:#ffffff;padding:26px 40px 36px 40px;">
{content_html}
                    </td>
                </tr>'''

    return f'''<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:o="urn:schemas-microsoft-com:office:office" lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{esc(ctx.header_title)}</title>
<link href="{FONT_HREF}" rel="stylesheet">
<style type="text/css">
    html, body {{ margin:0 !important; padding:0 !important; width:100% !important; }}
    * {{ -ms-text-size-adjust:100%; -webkit-text-size-adjust:100%; }}
    table, td {{ mso-table-lspace:0pt; mso-table-rspace:0pt; border-collapse:collapse !important; }}
    img {{ -ms-interpolation-mode:bicubic; border:0; height:auto; line-height:100%; outline:none; text-decoration:none; display:block; }}
    a {{ text-decoration:none; }}
    a[x-apple-data-detectors], .unstyle-auto-detected-links a, .aBn {{
        border-bottom:0 !important; cursor:default !important; color:inherit !important;
        text-decoration:none !important; font-size:inherit !important; font-family:inherit !important;
        font-weight:inherit !important; line-height:inherit !important;
    }}
    .im {{ color:inherit !important; }}
    @media screen and (max-width:600px) {{
        .wrap   {{ width:100% !important; }}
        .gutter {{ padding-left:24px !important; padding-right:24px !important; }}
        .h1, .hero-title {{ font-size:26px !important; line-height:32px !important; }}
        .stat   {{ font-size:34px !important; }}
        .stat-cell {{ display:block !important; width:100% !important; padding-bottom:16px !important; }}
    }}
    {EDIT_CSS if editable else ''}
</style>
<!--[if mso]>
<style type="text/css">
    body, table, td, a, p, span, h1, h2, h3 {{ font-family:'Century Gothic', Arial, sans-serif !important; }}
</style>
<xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml>
<![endif]-->
</head>
<body style="margin:0;padding:0;width:100%;background-color:{CANVAS};">
{_preheader(ctx)}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{CANVAS};">
<tr>
<td align="center" style="padding:0;">
    <!--[if mso]><table role="presentation" width="{CONTENT_WIDTH}" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
    <table role="presentation" class="wrap" width="{CONTENT_WIDTH}" cellpadding="0" cellspacing="0" border="0" style="width:{CONTENT_WIDTH}px;max-width:{CONTENT_WIDTH}px;background-color:#ffffff;">
{_header(ctx)}{hero_html}{content}
{_footer(ctx)}
    </table>
    <!--[if mso]></td></tr></table><![endif]-->
    <div style="height:32px;line-height:32px;font-size:32px;">&nbsp;</div>
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
    lines.append(
        f'© {ctx.resolved_year()} {ctx.brokerage_name or ctx.header_title or "Origen Realty"}. '
        'Equal Housing Opportunity.'
    )

    return '\n'.join(lines)
