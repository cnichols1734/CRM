"""
PDF splitter service.

Uses PyMuPDF (fitz) to slice a source PDF byte stream into multiple
child PDFs based on 1-based start/end page ranges. Used to split
combined offer packets into the individual addenda/contracts that
the AI extraction service identifies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


@dataclass
class SplitSegment:
    """A normalized 1-based page range for a single split request."""

    start_page: int
    end_page: int
    document_type: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class SplitResult:
    """Output for a single produced child PDF."""

    segment: SplitSegment
    pdf_bytes: bytes
    page_count: int


def _coerce_page(raw: dict, *keys) -> Optional[int]:
    """Return the first present integer page value from ``raw``."""
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        return int(value)
    return None


def get_pdf_page_count(file_data: bytes) -> int:
    """Return the number of pages in a PDF byte stream."""
    if not file_data:
        return 0
    doc = fitz.open(stream=file_data, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def normalize_segments(
    raw_segments: Iterable[dict],
    *,
    total_pages: int,
) -> List[SplitSegment]:
    """
    Coerce raw AI-detected segments into clean SplitSegment instances.

    - Drops anything without a usable page range.
    - Clamps ranges to ``[1, total_pages]``.
    - Sorts segments by start page.
    - Skips segments that fully duplicate a previous one.
    """
    if total_pages <= 0:
        return []

    cleaned: List[SplitSegment] = []
    for raw in raw_segments or []:
        if not isinstance(raw, dict):
            continue
        try:
            start = _coerce_page(
                raw, 'start_page', 'start_page', 'page_start', 'page_start',
            )
            end = _coerce_page(
                raw, 'end_page', 'end_page', 'page_end', 'page_end',
            )
        except (TypeError, ValueError):
            continue
        if start is None or end is None:
            continue
        if start > end:
            start, end = end, start
        start = max(1, min(start, total_pages))
        end = max(1, min(end, total_pages))
        if end < start:
            continue

        document_type = raw.get('document_type')
        if document_type is None:
            document_type = raw.get('document_type')
        if document_type is None:
            document_type = raw.get('type')
        if isinstance(document_type, str):
            document_type = document_type.strip().lower() or None
        else:
            document_type = None

        title = raw.get('title') or raw.get('label')
        if isinstance(title, str):
            title = title.strip() or None
        else:
            title = None

        notes = raw.get('notes')
        if isinstance(notes, str):
            notes = notes.strip() or None
        else:
            notes = None

        cleaned.append(SplitSegment(
            start_page=start,
            end_page=end,
            document_type=document_type,
            title=title,
            notes=notes,
        ))

    cleaned.sort(key=lambda s: (s.start_page, s.end_page))

    # Drop exact duplicates while preserving order.
    deduped: List[SplitSegment] = []
    seen: set[tuple[int, int, Optional[str]]] = set()
    for seg in cleaned:
        key = (seg.start_page, seg.end_page, seg.document_type)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(seg)
    return deduped


def _extract_page_range(file_data: bytes, start_page: int, end_page: int) -> bytes:
    """Return a PDF holding 1-based pages ``start_page``..``end_page``.

    Keeps the source document and drops the other pages rather than grafting
    pages into an empty document. Real estate forms are fillable AcroForm PDFs,
    and grafting them is both lossy and, on some PyMuPDF releases, raises while
    copying widgets for any range that does not start at page 1. Selecting
    pages in place preserves each field's value and appearance.
    """
    child = fitz.open(stream=file_data, filetype="pdf")
    try:
        child.select(list(range(start_page - 1, end_page)))
        if child.page_count <= 0:
            return b''
        return child.tobytes(garbage=3, deflate=True)
    finally:
        child.close()


def split_pdf_by_segments(
    file_data: bytes,
    segments: Iterable[SplitSegment],
) -> List[SplitResult]:
    """
    Slice ``file_data`` into one PDF per segment.

    Returns a list of ``SplitResult`` objects whose order matches the
    input segment order. Invalid segments are skipped silently and
    logged.
    """
    if not file_data:
        return []

    seg_list = [s for s in segments if s is not None]
    if not seg_list:
        return []

    source = fitz.open(stream=file_data, filetype="pdf")
    try:
        total_pages = source.page_count
    finally:
        source.close()

    results: List[SplitResult] = []
    for seg in seg_list:
        if seg.start_page < 1 or seg.end_page > total_pages or seg.end_page < seg.start_page:
            logger.warning(
                "Skipping invalid PDF split segment %s-%s (total pages=%s)",
                seg.start_page, seg.end_page, total_pages,
            )
            continue
        try:
            pdf_bytes = _extract_page_range(file_data, seg.start_page, seg.end_page)
        except Exception:
            logger.exception(
                "Failed to extract PDF pages %s-%s", seg.start_page, seg.end_page,
            )
            continue
        if not pdf_bytes:
            continue
        results.append(SplitResult(
            segment=seg,
            pdf_bytes=pdf_bytes,
            page_count=(seg.end_page - seg.start_page + 1),
        ))
    return results


def slice_pdf_pages(file_data: bytes, start_page: int, end_page: int) -> bytes:
    """Return a PDF containing 1-based pages ``start_page`` through ``end_page``."""
    results = split_pdf_by_segments(
        file_data,
        [SplitSegment(start_page=start_page, end_page=end_page)],
    )
    return results[0].pdf_bytes if results else b''
