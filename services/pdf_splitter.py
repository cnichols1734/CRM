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
            start = int(raw.get('start_page')) if raw.get('start_page') is not None else None
            end = int(raw.get('end_page')) if raw.get('end_page') is not None else None
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
        if isinstance(document_type, str):
            document_type = document_type.strip().lower() or None
        else:
            document_type = None

        title = raw.get('title')
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

    results: List[SplitResult] = []
    source = fitz.open(stream=file_data, filetype="pdf")
    try:
        total_pages = source.page_count
        for seg in seg_list:
            if seg.start_page < 1 or seg.end_page > total_pages or seg.end_page < seg.start_page:
                logger.warning(
                    "Skipping invalid PDF split segment %s-%s (total pages=%s)",
                    seg.start_page, seg.end_page, total_pages,
                )
                continue
            child = fitz.open()
            try:
                child.insert_pdf(
                    source,
                    from_page=seg.start_page - 1,
                    to_page=seg.end_page - 1,
                )
                if child.page_count <= 0:
                    continue
                pdf_bytes = child.tobytes()
            finally:
                child.close()
            results.append(SplitResult(
                segment=seg,
                pdf_bytes=pdf_bytes,
                page_count=(seg.end_page - seg.start_page + 1),
            ))
    finally:
        source.close()
    return results
