"""Telegram voice-note → text for B.O.B.

Downloads the audio from Telegram and runs it through the same Whisper helper
used for contact voice memos. The transcript then enters the normal tool loop
as if the agent had typed it.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Telegram voice notes are usually short; refuse monsters before paying Whisper.
MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_VOICE_DURATION_SECONDS = 180


class VoiceTranscriptionError(Exception):
    """Raised when a voice note cannot be turned into usable text."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def transcribe_telegram_voice(
    transport: Any,
    file_id: str,
    *,
    duration_seconds: Optional[int] = None,
) -> str:
    """Download a Telegram voice/audio file and return its transcript."""
    if not file_id:
        raise VoiceTranscriptionError('That voice note looked empty.')

    if duration_seconds is not None and duration_seconds > MAX_VOICE_DURATION_SECONDS:
        raise VoiceTranscriptionError(
            f'Voice notes need to be under {MAX_VOICE_DURATION_SECONDS // 60} '
            'minutes. Try a shorter one, or type it.'
        )

    try:
        meta = transport.get_file(file_id)
    except Exception as exc:
        logger.warning('Telegram getFile failed: %s', exc)
        raise VoiceTranscriptionError(
            "Couldn't download that voice note. Try again or type it."
        ) from exc

    file_path = (meta or {}).get('file_path') or ''
    file_size = (meta or {}).get('file_size') or 0
    if file_size and int(file_size) > MAX_VOICE_BYTES:
        raise VoiceTranscriptionError(
            'That voice note is too large. Try a shorter one, or type it.'
        )
    if not file_path:
        raise VoiceTranscriptionError(
            "Couldn't find that voice note file. Try again or type it."
        )

    try:
        audio_bytes = transport.download_file(file_path)
    except Exception as exc:
        logger.warning('Telegram file download failed: %s', exc)
        raise VoiceTranscriptionError(
            "Couldn't download that voice note. Try again or type it."
        ) from exc

    if not audio_bytes:
        raise VoiceTranscriptionError('That voice note looked empty.')
    if len(audio_bytes) > MAX_VOICE_BYTES:
        raise VoiceTranscriptionError(
            'That voice note is too large. Try a shorter one, or type it.'
        )

    filename = file_path.rsplit('/', 1)[-1] or 'voice.oga'
    try:
        from services.ai_service import transcribe_audio
        text = transcribe_audio(audio_data=audio_bytes, filename=filename)
    except Exception as exc:
        logger.warning('Whisper failed for Telegram voice: %s', exc)
        raise VoiceTranscriptionError(
            "Couldn't catch that. Try again or type it."
        ) from exc

    text = (text or '').strip()
    if not text:
        raise VoiceTranscriptionError(
            "Couldn't catch that. Try again or type it."
        )
    return text
