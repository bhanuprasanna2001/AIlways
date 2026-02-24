"""Transcription package — audio-to-text with speaker diarization.

Provides a ``get_transcriber`` factory and convenience functions
for pre-recorded and live transcription via DeepGram Nova 3.

Usage::

    from app.core.transcription import get_transcriber

    transcriber = get_transcriber()
    result = await transcriber.transcribe_file(audio_bytes, "audio/wav")
"""

from app.core.transcription.base import (
    Transcriber,
    TranscriptSegment,
    TranscriptResult,
)
from app.core.transcription.deepgram import DeepgramTranscriber
from app.core.transcription.exceptions import TranscriptionError
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_transcriber: Transcriber | None = None


def get_transcriber() -> Transcriber:
    """Return the shared transcriber instance.

    Lazily initialised on first call.

    Returns:
        Transcriber: Configured DeepGram transcriber instance.
    """
    global _transcriber
    if _transcriber is None:
        settings = get_settings()
        _transcriber = DeepgramTranscriber(
            api_key=settings.DEEPGRAM_API_KEY,
            model=settings.DEEPGRAM_MODEL,
            language=settings.DEEPGRAM_LANGUAGE,
        )
        logger.info(
            f"Initialised transcriber: model={settings.DEEPGRAM_MODEL}, "
            f"language={settings.DEEPGRAM_LANGUAGE}",
        )
    return _transcriber


__all__ = [
    "Transcriber",
    "TranscriptSegment",
    "TranscriptResult",
    "DeepgramTranscriber",
    "TranscriptionError",
    "get_transcriber",
]
