"""
Normalize SendGrid Inbound Parse multipart payloads into an AI-ready bundle.

Pure function — no DB, no HTTP, no external services. Easy to unit-test
exhaustively against captured SendGrid payloads.

The output is consumed by `services.ai_service.generate_contact_extraction`
which makes a single AI call regardless of source kind.
"""
from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

import bleach

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cost ceilings — single source of truth for what the AI is allowed to see.
# ---------------------------------------------------------------------------

MAX_TEXT_BYTES = 16 * 1024            # 16 KB after HTML stripping
MAX_IMAGES = 5                        # First 5 image attachments only
IMAGE_MAX_LONG_EDGE = 1024            # px, downscale before base64
MAX_CSV_ROWS = 500                    # Hard stop in MVP — bigger → CSV importer
MAX_VCARD_BYTES = 64 * 1024           # vCard files larger than this are unusual

VCARD_MIMES = {'text/vcard', 'text/x-vcard', 'text/directory'}
CSV_MIMES = {'text/csv', 'application/csv',
             'application/vnd.ms-excel'}  # Outlook exports as ms-excel sometimes
TEXT_MIMES = {'text/plain'}
IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/jpg',
               'image/heic', 'image/heif', 'image/webp', 'image/gif'}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class NormalizedInbound:
    """Result of normalizing an inbound SendGrid payload.

    `cleaned_text` is everything textual concatenated together (body + vcf +
    csv + txt attachments). `image_blocks` are base64-encoded JPEGs sized for
    the AI vision endpoint. `source_kind` is for analytics only — the AI path
    is identical regardless of value.
    """
    cleaned_text: str = ''
    image_blocks: list[str] = field(default_factory=list)
    source_kind: str = 'text'
    plus_alias: str | None = None
    truncated_text: bool = False
    skipped_images: int = 0
    skipped_csv_rows: int = 0
    over_limit_csv: bool = False
    attachment_summary: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize_sendgrid_payload(form: dict, files: dict | None = None,
                               *, plus_alias: str | None = None
                               ) -> NormalizedInbound:
    """Normalize a SendGrid Inbound Parse POST into something the AI can chew.

    Args:
        form: ``request.form`` from a Parse webhook (text, html, subject,
              from, envelope, attachment-info, etc.). Pass a plain ``dict``
              or any mapping.
        files: ``request.files`` mapping. SendGrid sends attachments as
              ``attachment1``, ``attachment2``, …; we walk all of them.
        plus_alias: Optional plus-alias parsed from the recipient address
              (e.g. ``investors`` from ``you-token+investors@…``). Stored on
              the result so downstream callers can map it to a contact group.

    Returns:
        A ``NormalizedInbound`` ready to hand to
        ``generate_contact_extraction``.
    """
    files = files or {}

    text_chunks: list[str] = []
    image_blocks: list[str] = []
    skipped_images = 0
    skipped_csv_rows = 0
    over_limit_csv = False
    attachment_kinds: set[str] = set()
    attachment_summary: list[dict] = []

    # --- Subject + body --------------------------------------------------
    subject = (form.get('subject') or '').strip()
    if subject:
        text_chunks.append(f'SUBJECT: {subject}')

    sender = (form.get('from') or '').strip()
    if sender:
        text_chunks.append(f'FROM: {sender}')

    body_text = (form.get('text') or '').strip()
    body_html = (form.get('html') or '').strip()
    if not body_text and body_html:
        body_text = _strip_html(body_html)

    if body_text:
        text_chunks.append('BODY:\n' + body_text)

    # --- Attachments -----------------------------------------------------
    for filename, mime, raw_bytes in _iter_attachments(form, files):
        mime = (mime or '').lower()
        kind = _classify(mime, filename)
        attachment_summary.append({
            'filename': filename or '(unnamed)',
            'mime': mime or 'unknown',
            'kind': kind,
            'bytes': len(raw_bytes),
        })

        if kind == 'vcard':
            attachment_kinds.add('vcard')
            text = _decode_text(raw_bytes[:MAX_VCARD_BYTES])
            if text:
                text_chunks.append(
                    f'ATTACHMENT vCard ({filename or "card.vcf"}):\n{text}'
                )

        elif kind == 'csv':
            attachment_kinds.add('csv')
            text, rows_total, rows_kept, over = _truncate_csv(raw_bytes)
            if rows_total > MAX_CSV_ROWS:
                over_limit_csv = True
                skipped_csv_rows += max(0, rows_total - rows_kept)
            if text:
                label = f'ATTACHMENT CSV ({filename or "contacts.csv"})'
                if over:
                    label += (f' — TRUNCATED to first {MAX_CSV_ROWS} rows '
                              f'of {rows_total}')
                text_chunks.append(f'{label}:\n{text}')

        elif kind == 'text':
            attachment_kinds.add('text')
            text = _decode_text(raw_bytes[:MAX_TEXT_BYTES])
            if text:
                text_chunks.append(
                    f'ATTACHMENT text ({filename or "note.txt"}):\n{text}'
                )

        elif kind == 'image':
            attachment_kinds.add('image')
            if len(image_blocks) >= MAX_IMAGES:
                skipped_images += 1
                continue
            block = _image_to_base64_jpeg(raw_bytes)
            if block:
                image_blocks.append(block)
            else:
                skipped_images += 1

        else:
            # Unknown attachment type — skip silently. The AI gets the body
            # and any other recognized attachments; we don't want to feed
            # binary garbage to it.
            continue

    cleaned_text = '\n\n'.join(t for t in text_chunks if t)
    truncated = False
    if len(cleaned_text.encode('utf-8')) > MAX_TEXT_BYTES:
        cleaned_text = _truncate_text(cleaned_text, MAX_TEXT_BYTES)
        truncated = True

    return NormalizedInbound(
        cleaned_text=cleaned_text,
        image_blocks=image_blocks,
        source_kind=_summarize_kind(attachment_kinds, has_text=bool(body_text),
                                    has_image=bool(image_blocks)),
        plus_alias=plus_alias,
        truncated_text=truncated,
        skipped_images=skipped_images,
        skipped_csv_rows=skipped_csv_rows,
        over_limit_csv=over_limit_csv,
        attachment_summary=attachment_summary,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _iter_attachments(form, files) -> Iterable[tuple[str, str, bytes]]:
    """Yield ``(filename, mime, bytes)`` for each attachment.

    SendGrid Inbound Parse sends attachments as ``attachment1``,
    ``attachment2``, etc. with metadata in ``attachment-info``. This
    iterates over both Werkzeug FileStorage uploads and any inline parts.
    """
    for key in sorted(files.keys()):
        if not key.lower().startswith('attachment'):
            continue
        f = files[key]
        if not f:
            continue
        try:
            data = f.read()
        except Exception:
            logger.exception('Failed reading attachment %s', key)
            continue
        if not data:
            continue
        yield (
            getattr(f, 'filename', None) or '',
            getattr(f, 'mimetype', None) or getattr(f, 'content_type', None) or '',
            data,
        )


def _classify(mime: str, filename: str) -> str:
    name = (filename or '').lower()
    if mime in VCARD_MIMES or name.endswith('.vcf'):
        return 'vcard'
    if mime in CSV_MIMES or name.endswith('.csv'):
        return 'csv'
    if mime in TEXT_MIMES or name.endswith('.txt'):
        return 'text'
    if mime in IMAGE_MIMES or any(name.endswith(ext) for ext in (
            '.png', '.jpg', '.jpeg', '.heic', '.heif', '.webp', '.gif')):
        return 'image'
    return 'other'


def _decode_text(data: bytes) -> str:
    """Decode bytes as UTF-8 / latin-1 with a soft fallback."""
    if not data:
        return ''
    for enc in ('utf-8', 'utf-8-sig', 'latin-1'):
        try:
            return data.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace').strip()


def _strip_html(html: str) -> str:
    """Strip HTML to plain text via bleach + whitespace squashing."""
    if not html:
        return ''
    cleaned = bleach.clean(html, tags=[], strip=True)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


def _truncate_text(text: str, max_bytes: int) -> str:
    """Hard-truncate text to *max_bytes* of UTF-8."""
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text
    cut = encoded[:max_bytes]
    return cut.decode('utf-8', errors='ignore') + '\n…(truncated)'


def _truncate_csv(data: bytes) -> tuple[str, int, int, bool]:
    """Cap a CSV at MAX_CSV_ROWS data rows.

    Returns ``(text, total_rows_seen, rows_kept, was_truncated)``. The header
    row is always included so the AI keeps the column context.
    """
    text = _decode_text(data)
    if not text:
        return '', 0, 0, False

    lines = text.splitlines()
    if not lines:
        return '', 0, 0, False

    header = lines[0]
    body = lines[1:]
    total = len(body)
    keep = body[:MAX_CSV_ROWS]
    truncated = total > MAX_CSV_ROWS

    out = '\n'.join([header, *keep])
    return out, total, len(keep), truncated


def _image_to_base64_jpeg(data: bytes) -> str | None:
    """Downscale and re-encode an image to JPEG/base64 for the vision model.

    Returns None if the image can't be loaded (corrupted, weird format).
    HEIC support depends on the runtime; we try and silently skip on failure.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning('Pillow not installed — skipping image attachment.')
        return None

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        logger.info('Could not decode inbound image (%s) — skipping.', e)
        return None

    # Convert to RGB so JPEG encoding never fails on RGBA/CMYK/etc.
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')

    # Downscale preserving aspect ratio.
    long_edge = max(img.size)
    if long_edge > IMAGE_MAX_LONG_EDGE:
        scale = IMAGE_MAX_LONG_EDGE / long_edge
        new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
        try:
            img = img.resize(new_size, Image.LANCZOS)
        except AttributeError:
            # Pillow >= 10 renamed LANCZOS to Resampling.LANCZOS
            img = img.resize(new_size, Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=82, optimize=True)
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _summarize_kind(kinds: set[str], *, has_text: bool, has_image: bool) -> str:
    """Pick one of vcard/csv/image/text/mixed for analytics on InboundMessage."""
    distinct = set(kinds)
    if has_image:
        distinct.add('image')
    if has_text and not distinct:
        return 'text'
    if not distinct:
        return 'text'
    if len(distinct) == 1:
        return next(iter(distinct))
    return 'mixed'
