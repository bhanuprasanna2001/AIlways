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
from app.core.utils import singleton

logger = setup_logger(__name__)


@singleton
def get_transcriber() -> Transcriber:
    """Return the shared transcriber instance (lazily initialised)."""
    settings = get_settings()
    logger.info(
        f"Initialised transcriber: model={settings.DEEPGRAM_MODEL}, "
        f"language={settings.DEEPGRAM_LANGUAGE}",
    )
    return DeepgramTranscriber(
        api_key=settings.DEEPGRAM_API_KEY,
        model=settings.DEEPGRAM_MODEL,
        language=settings.DEEPGRAM_LANGUAGE,
    )


__all__ = [
    "Transcriber",
    "TranscriptSegment",
    "TranscriptResult",
    "DeepgramTranscriber",
    "TranscriptionError",
    "get_transcriber",
]
