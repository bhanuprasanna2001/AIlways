"""Transcriber protocol and shared data models."""

from __future__ import annotations

from typing import Protocol, runtime_checkable, AsyncIterator
from contextlib import asynccontextmanager

from pydantic import BaseModel


@runtime_checkable
class Transcriber(Protocol):
    """Protocol for audio transcription services.

    Any class that implements ``transcribe_file`` with the correct
    signature satisfies this protocol — no inheritance needed.

    To add a new transcriber (e.g. Whisper):
        1. Create ``app/core/transcription/whisper.py``.
        2. Update ``get_transcriber()`` in ``transcription/__init__.py``.
    """

    async def transcribe_file(
        self, audio_data: bytes, mimetype: str,
    ) -> TranscriptResult:
        """Transcribe a pre-recorded audio file with speaker diarization.

        Args:
            audio_data: Raw audio bytes.
            mimetype: MIME type of the audio (e.g. ``'audio/wav'``).

        Returns:
            TranscriptResult: Full transcript with speaker-diarized segments.
        """
        ...


@runtime_checkable
class LiveConnection(Protocol):
    """Handle for a live transcription stream.

    Provides methods to send audio, receive transcript segments,
    finalize the stream, and close the connection.
    """

    async def send_audio(self, audio_chunk: bytes) -> None:
        """Send an audio chunk to the live transcription stream."""
        ...

    async def receive(self) -> TranscriptSegment | None:
        """Receive the next transcript segment from the stream.

        Returns:
            TranscriptSegment | None: Next segment, or None if the
                stream has ended or the message is non-transcript.
        """
        ...

    async def finalize(self) -> None:
        """Signal end of audio input. Remaining audio will be flushed."""
        ...

    async def close(self) -> None:
        """Close the live transcription connection."""
        ...


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TranscriptWord(BaseModel):
    """A single transcribed word with timing and speaker info."""

    word: str
    start: float
    end: float
    confidence: float
    speaker: int = 0


class TranscriptSegment(BaseModel):
    """A speaker-attributed segment of transcription.

    Groups contiguous words from the same speaker into a single
    segment with timing boundaries.

    Flags:
      - ``is_final``: The text is finalized and will not change.
        In live streaming, DeepGram sends ``is_final=True`` when the
        words for a segment are locked in.  ``is_final=False`` means
        interim (preview) text that may still change.
      - ``speech_final``: The speaker has paused or finished their
        utterance.  Only meaningful in live streaming.  Always True
        for pre-recorded transcription.  Used as the natural trigger
        point for claim detection (a complete thought has been expressed).
    """

    text: str
    speaker: int
    start: float
    end: float
    confidence: float
    is_final: bool = True
    speech_final: bool = True
    words: list[TranscriptWord] = []


class TranscriptResult(BaseModel):
    """Complete transcription result with diarization metadata."""

    segments: list[TranscriptSegment]
    full_text: str
    speakers: int
    duration: float
