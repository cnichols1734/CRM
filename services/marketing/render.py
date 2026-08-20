"""Blocks to email HTML and plain text.

The only module that emits marketing markup, which makes email-client
compatibility a property of one testable file. Everything is tables and inline
styles because that is what Outlook understands, and every author-supplied
string is escaped on the way in.

Merge tokens survive rendering untouched. Filling them happens later, per
recipient, in ``personalize``, so one render can serve a whole campaign step.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

from services.marketing import merge_fields as mf
from services.marketing.blocks import validate_blocks
from services.marketing.edit_marks import mark, pop as pop_edit, push as push_edit, wrap_html
from services.marketing.shell import (
    ACCENT,
    ACCENT_DARK,
    DASHED,
    DISPLAY,
    FONT,
    HAIRLINE,
    INK,
    INK_BODY,
    INK_FAINT,
    INK_MUTED,
    INSET_BG,
    INSET_BORDER,
    ShellContext,
    esc,
    footer_text,
    hero,
    wrap,
)


@dataclass
class RenderedEmail:
    html: str
    text: str


def _paragraph(text: str) -> str:
    # Author copy is plain text; a blank line is the only formatting we honor,
    # and it becomes a real paragraph break.
    chunks = [c.strip() for c in text.split('\n\n') if c.strip()]
    out = []
    for chunk in chunks:
        body = esc(chunk).replace('\n', '<br>')
        out.append(
            f'<p style="margin:0 0 18px 0;font-family:{FONT};font-size:15.5px;'
            f'color:{INK_BODY};line-height:1.7;">{body}</p>'
        )
    return wrap_html('\n'.join(out), 'text')


def _heading(block: dict) -> str:
    """h2 is a Fraunces section title; h3 is a smaller DM Sans label.

    Two different jobs rather than two sizes of the same thing: the serif reads
    as a new chapter, the sans as a subhead inside one.
    """
    if block.get('level', 'h2') == 'h3':
        return (
            f'<h3 style="margin:0 0 12px 0;font-family:{FONT};font-size:11px;'
            f'font-weight:700;color:{INK_MUTED};letter-spacing:1.6px;'
            f'text-transform:uppercase;">{mark(esc(block["text"]), "text")}</h3>'
        )
    return (
        f'<h2 style="margin:0 0 16px 0;font-family:{DISPLAY};font-size:28px;'
        f'font-weight:500;color:{INK};line-height:1.15;letter-spacing:-0.3px;">'
        f'{mark(esc(block["text"]), "text")}</h2>'
    )


def _bullets(block: dict) -> str:
    items = '\n'.join(
        f'<li style="margin:0 0 8px 0;">{mark(esc(item), "items", item=index)}</li>'
        for index, item in enumerate(block.get('items', []))
    )
    return (
        f'<ul style="margin:0 0 20px 0;padding-left:22px;font-family:{FONT};'
        f'font-size:15.5px;color:{INK_BODY};line-height:1.6;">{items}</ul>'
    )


def _button(block: dict) -> str:
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td align="center" style="padding:6px 0 30px 0;">
        <a href="{esc(block["url"])}" style="display:inline-block;font-family:{FONT};background-color:{ACCENT};background:linear-gradient(135deg, {ACCENT} 0%, {ACCENT_DARK} 100%);color:#ffffff;font-size:15px;font-weight:600;text-decoration:none;padding:15px 38px;border-radius:10px;box-shadow:0 8px 22px rgba(249,115,22,0.32);">{mark(esc(block["label"]), "label")}</a>
    </td></tr>
</table>'''


def _steps(block: dict) -> str:
    """Numbered items with serif numerals in their own column."""
    rows = []
    entries = block.get('steps') or []
    for position, entry in enumerate(entries, start=1):
        last = position == len(entries)
        body = ''
        if entry.get('text'):
            body = (
                f'<p style="margin:0;font-family:{FONT};font-size:14px;'
                f'color:{INK_MUTED};line-height:1.6;">{mark(esc(entry["text"]), "steps", item=position - 1, key="text")}</p>'
            )
        rows.append(f'''<tr>
        <td valign="top" style="padding:0 0 {'0' if last else '22px'} 0;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                <tr>
                    <td valign="top" width="44" style="font-family:{DISPLAY};font-size:28px;font-weight:500;color:{ACCENT};line-height:1;padding-top:2px;">{position:02d}</td>
                    <td valign="top">
                        <p style="margin:0 0 4px 0;font-family:{FONT};font-size:16px;font-weight:600;color:{INK};line-height:1.4;">{mark(esc(entry["title"]), "steps", item=position - 1, key="title")}</p>
                        {body}
                    </td>
                </tr>
            </table>
        </td>
    </tr>''')

    joined = '\n'.join(rows)
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 26px 0;">
{joined}
</table>'''


def _callout(block: dict) -> str:
    label = ''
    if block.get('label'):
        label = (
            f'<p style="margin:0 0 6px 0;font-family:{FONT};font-size:10px;'
            f'font-weight:700;color:{ACCENT};letter-spacing:1.3px;'
            f'text-transform:uppercase;">{mark(esc(block["label"]), "label")}</p>'
        )
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:{INSET_BG};border:1px solid {INSET_BORDER};border-radius:12px;margin:0 0 24px 0;">
    <tr><td style="padding:16px 18px;">
        {label}
        <p style="margin:0;font-family:{FONT};font-size:15px;font-weight:600;color:{INK};line-height:1.55;">{mark(esc(block["text"]), "text")}</p>
    </td></tr>
</table>'''


def _image(block: dict) -> str:
    img = (
        f'<img src="{esc(block["image_url"])}" alt="{esc(block["alt"])}" '
        f'width="504" style="display:block;width:100%;max-width:504px;height:auto;'
        f'border-radius:10px;border:0;">'
    )
    if block.get('link_url'):
        img = f'<a href="{esc(block["link_url"])}">{img}</a>'

    caption = ''
    if block.get('caption'):
        caption = (
            f'<p style="margin:8px 0 0 0;font-family:{FONT};font-size:12px;'
            f'color:{INK_FAINT};text-align:center;">{mark(esc(block["caption"]), "caption")}</p>'
        )

    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:0 0 24px 0;">{img}{caption}</td></tr>
</table>'''


def _listing_card(block: dict) -> str:
    photo = ''
    if block.get('image_url'):
        photo = (
            f'<img src="{esc(block["image_url"])}" alt="{esc(block["address"])}" '
            f'width="504" style="display:block;width:100%;max-width:504px;'
            f'height:auto;border:0;">'
        )

    specs = []
    for key, suffix in (('beds', 'bd'), ('baths', 'ba'), ('sqft', 'sq ft')):
        value = block.get(key)
        if value:
            specs.append(f'{mark(esc(value), key)} {suffix}')
    spec_line = ' &nbsp;·&nbsp; '.join(specs)

    rows = [
        f'<p style="margin:0 0 4px 0;font-family:{FONT};font-size:17px;'
        f'font-weight:700;color:{INK};">{mark(esc(block["address"]), "address")}</p>'
    ]
    if block.get('price'):
        rows.append(
            f'<p style="margin:0 0 6px 0;font-family:{FONT};font-size:20px;'
            f'font-weight:700;color:{ACCENT_DARK};">{mark(esc(block["price"]), "price")}</p>'
        )
    if spec_line:
        rows.append(
            f'<p style="margin:0 0 6px 0;font-family:{FONT};font-size:13px;'
            f'color:{INK_MUTED};">{spec_line}</p>'
        )
    if block.get('caption'):
        rows.append(
            f'<p style="margin:0 0 6px 0;font-family:{FONT};font-size:13px;'
            f'color:{INK_BODY};line-height:1.6;">{mark(esc(block["caption"]), "caption")}</p>'
        )
    if block.get('url'):
        rows.append(
            f'<p style="margin:8px 0 0 0;font-family:{FONT};font-size:14px;">'
            f'<a href="{esc(block["url"])}" style="color:{ACCENT_DARK};'
            f'font-weight:600;text-decoration:none;">View the listing &rarr;</a></p>'
        )

    detail = '\n'.join(rows)
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border:1px solid {HAIRLINE};border-radius:12px;overflow:hidden;margin:0 0 24px 0;">
    {f'<tr><td>{photo}</td></tr>' if photo else ''}
    <tr><td style="padding:18px 20px;">{detail}</td></tr>
</table>'''


def _stat_row(block: dict) -> str:
    stats = block.get('stats', [])
    if not stats:
        return ''
    width = f'{100 // len(stats)}%'
    cells = []
    for index, stat in enumerate(stats):
        cells.append(
            f'<td class="stat-cell" width="{width}" align="center" '
            f'style="padding:4px 8px;vertical-align:top;">'
            f'<p style="margin:0 0 2px 0;font-family:{FONT};font-size:22px;'
            f'font-weight:700;color:{INK};">{mark(esc(stat.get("value")), "stats", item=index, key="value")}</p>'
            f'<p style="margin:0;font-family:{FONT};font-size:11px;'
            f'font-weight:600;color:{INK_FAINT};text-transform:uppercase;'
            f'letter-spacing:0.5px;">{mark(esc(stat.get("label")), "stats", item=index, key="label")}</p></td>'
        )
    joined = '\n'.join(cells)
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:{INSET_BG};border-radius:10px;margin:0 0 24px 0;">
    <tr><td style="padding:20px 12px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>{joined}</tr></table></td></tr>
</table>'''


def _quote(block: dict) -> str:
    attribution = ''
    if block.get('attribution'):
        attribution = (
            f'<p style="margin:10px 0 0 0;font-family:{FONT};font-size:13px;'
            f'font-weight:600;color:{INK_MUTED};">{mark(esc(block["attribution"]), "attribution")}</p>'
        )
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 24px 0;">
    <tr><td style="background-color:{INSET_BG};border-left:4px solid {ACCENT};border-radius:10px;padding:18px 22px;">
        <p style="margin:0;font-family:{FONT};font-size:16px;color:{INK};line-height:1.65;font-style:italic;">{mark(esc(block["text"]), "text")}</p>
        {attribution}
    </td></tr>
</table>'''


def _divider() -> str:
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr><td style="padding:4px 0 26px 0;"><div style="height:1px;background-color:{HAIRLINE};line-height:1px;font-size:0;">&nbsp;</div></td></tr>
</table>'''


def _signature(ctx: ShellContext) -> str:
    """Rendered from the agent's own record, never from author input."""
    if not ctx.agent_name:
        return ''
    lines = [
        f'<p style="margin:0 0 2px 0;font-family:{FONT};font-size:15px;'
        f'font-weight:700;color:{INK};">{esc(ctx.agent_name)}</p>'
    ]
    if ctx.agent_title:
        lines.append(
            f'<p style="margin:0 0 2px 0;font-family:{FONT};font-size:13px;'
            f'color:{INK_MUTED};">{esc(ctx.agent_title)}</p>'
        )
    if ctx.brokerage_name:
        lines.append(
            f'<p style="margin:0 0 2px 0;font-family:{FONT};font-size:13px;'
            f'color:{INK_MUTED};">{esc(ctx.brokerage_name)}</p>'
        )
    detail = ' · '.join(p for p in (ctx.agent_phone, ctx.agent_email) if p)
    if detail:
        lines.append(
            f'<p style="margin:0;font-family:{FONT};font-size:13px;'
            f'color:{INK_FAINT};">{esc(detail)}</p>'
        )
    body = '\n'.join(lines)
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:6px 0 0 0;">
    <tr><td style="border-top:1px dashed {DASHED};padding:22px 0 0 0;">{body}</td></tr>
</table>'''


def render_block(block: dict, ctx: ShellContext) -> str:
    kind = block['type']
    if kind == 'heading':
        return _heading(block)
    if kind == 'paragraph':
        return _paragraph(block['text'])
    if kind == 'bullets':
        return _bullets(block)
    if kind == 'button':
        return _button(block)
    if kind == 'steps':
        return _steps(block)
    if kind == 'callout':
        return _callout(block)
    if kind == 'image':
        return _image(block)
    if kind == 'listing_card':
        return _listing_card(block)
    if kind == 'stat_row':
        return _stat_row(block)
    if kind == 'quote':
        return _quote(block)
    if kind == 'divider':
        return _divider()
    if kind == 'signature':
        return _signature(ctx)
    # 'hero' is handled by the shell: it is full-bleed and cannot live inside
    # the padded content cell.
    return ''


def _collapse_rules(blocks: list[dict]) -> list[dict]:
    """Drop a divider sitting directly above a signature.

    The signature draws its own top rule, so the pair renders as a double line.
    Authors and the model both reach for divider-then-signature by instinct, so
    this is handled here rather than left to every template to get right.
    """
    out: list[dict] = []
    for index, block in enumerate(blocks):
        is_divider = block['type'] == 'divider'
        next_is_signature = (
            index + 1 < len(blocks) and blocks[index + 1]['type'] == 'signature'
        )
        if is_divider and next_is_signature:
            continue
        out.append(block)
    return out


def _skipped_dividers(blocks: list[dict]) -> set[int]:
    skipped: set[int] = set()
    for index, block in enumerate(blocks):
        next_is_signature = (
            index + 1 < len(blocks) and blocks[index + 1]['type'] == 'signature'
        )
        if block['type'] == 'divider' and next_is_signature:
            skipped.add(index)
    return skipped


def render_blocks_html(
    blocks: list[dict],
    ctx: ShellContext,
    *,
    editable: bool = False,
    index_offset: int = 0,
) -> str:
    skipped = _skipped_dividers(blocks)
    parts: list[str] = []
    for index, block in enumerate(blocks):
        if index in skipped:
            continue
        token = push_edit(editable, index + index_offset)
        try:
            fragment = render_block(block, ctx)
        finally:
            pop_edit(token)
        if fragment:
            parts.append(fragment)
    return '\n'.join(parts)


def _block_text(block: dict, ctx: ShellContext) -> str:
    kind = block['type']
    if kind in ('heading', 'paragraph'):
        return block['text']
    if kind == 'hero':
        lines = []
        if block.get('eyebrow'):
            lines.append(block['eyebrow'].upper())
        headline = block['title']
        if block.get('accent'):
            headline = f'{headline} {block["accent"]}'
        lines.append(headline)
        if block.get('text'):
            lines.append(block['text'])
        return '\n'.join(lines)
    if kind == 'bullets':
        return '\n'.join(f'- {item}' for item in block.get('items', []))
    if kind == 'button':
        return f'{block["label"]}: {block["url"]}'
    if kind == 'steps':
        return '\n'.join(
            f'{position}. {entry["title"]}'
            + (f'\n   {entry["text"]}' if entry.get('text') else '')
            for position, entry in enumerate(block.get('steps') or [], start=1)
        )
    if kind == 'callout':
        label = block.get('label')
        return f'{label}: {block["text"]}' if label else block['text']
    if kind == 'image':
        caption = block.get('caption')
        return f'[{block["alt"]}]' + (f'\n{caption}' if caption else '')
    if kind == 'listing_card':
        parts = [block['address']]
        if block.get('price'):
            parts.append(block['price'])
        specs = [
            f'{block[key]} {suffix}'
            for key, suffix in (('beds', 'bd'), ('baths', 'ba'), ('sqft', 'sq ft'))
            if block.get(key)
        ]
        if specs:
            parts.append(' · '.join(specs))
        if block.get('caption'):
            parts.append(block['caption'])
        if block.get('url'):
            parts.append(block['url'])
        return '\n'.join(parts)
    if kind == 'stat_row':
        return '\n'.join(
            f'{s.get("value")} ({s.get("label")})' for s in block.get('stats', [])
        )
    if kind == 'quote':
        text = f'"{block["text"]}"'
        if block.get('attribution'):
            text += f'\n{block["attribution"]}'
        return text
    if kind == 'divider':
        return '---'
    if kind == 'signature':
        parts = [p for p in (
            ctx.agent_name, ctx.agent_title, ctx.brokerage_name,
            ' · '.join(p for p in (ctx.agent_phone, ctx.agent_email) if p),
        ) if p]
        return '\n'.join(parts)
    return ''


def render_blocks_text(blocks: list[dict], ctx: ShellContext) -> str:
    chunks = [
        chunk for chunk in (_block_text(b, ctx) for b in _collapse_rules(blocks))
        if chunk and chunk.strip()
    ]
    return '\n\n'.join(chunks)


def render(
    blocks: list[dict],
    ctx: ShellContext,
    *,
    validate: bool = True,
    editable: bool = False,
) -> RenderedEmail:
    """Render a template. Merge tokens are left in place for ``personalize``.

    A real ``text/plain`` alternative is not optional: HTML-only mail is a
    documented spam signal, and some clients show nothing without it.
    """
    if validate:
        blocks = validate_blocks(blocks)

    # The hero is full-bleed, so the shell places it as its own row rather than
    # inside the padded content cell. Validation guarantees it is first.
    hero_html = ''
    body_blocks = blocks
    index_offset = 0
    if blocks and blocks[0]['type'] == 'hero':
        token = push_edit(editable, 0)
        try:
            hero_html = hero(blocks[0])
        finally:
            pop_edit(token)
        body_blocks = blocks[1:]
        index_offset = 1

    body_html = wrap(
        render_blocks_html(
            body_blocks, ctx, editable=editable, index_offset=index_offset,
        ),
        ctx,
        hero_html,
        editable=editable,
    )
    body_text = render_blocks_text(blocks, ctx)
    footer = footer_text(ctx)
    if footer:
        body_text = f'{body_text}\n\n{"-" * 40}\n{footer}'

    return RenderedEmail(html=body_html, text=body_text)


def personalize(
    rendered: RenderedEmail,
    subject: str,
    values: dict[str, Optional[str]],
) -> tuple[str, str, str, set[str]]:
    """Fill merge tokens for one recipient.

    Returns ``(subject, html, text, missing_keys)``. The HTML pass escapes
    values because rendering already escaped the surrounding copy; skipping it
    would let a contact name carrying markup into the document.
    """
    filled_subject, missing_subject = mf.substitute(subject, values)
    filled_html, missing_html = mf.substitute(
        rendered.html, values, escape=lambda v: html.escape(v, quote=True),
    )
    filled_text, missing_text = mf.substitute(rendered.text, values)

    return (
        filled_subject,
        filled_html,
        filled_text,
        missing_subject | missing_html | missing_text,
    )


def preview(
    blocks: list[dict],
    ctx: ShellContext,
    subject: str,
    *,
    editable: bool = False,
    fill_samples: bool = True,
    sample_values: Optional[dict] = None,
) -> tuple[str, str]:
    """Render for the studio or library preview pane.

    Editable previews wrap merge tokens as chips that show the field name.
    ``fill_samples`` then swaps chip text (and leftover attribute tokens)
    for sample values so the agent can check the finished email.
    """
    rendered = render(blocks, ctx, editable=editable)
    html = rendered.html
    if editable:
        html = mf.wrap_tokens_for_preview(html)
    if not fill_samples:
        return subject, html
    values = sample_values if sample_values is not None else mf.sample_values()
    if editable:
        html = mf.fill_preview_chips(html, values)
    filled_subject, filled_html, _, _ = personalize(
        RenderedEmail(html=html, text=rendered.text),
        subject,
        values,
    )
    return filled_subject, filled_html
