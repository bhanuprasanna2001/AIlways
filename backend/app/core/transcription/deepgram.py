"""DeepGram Nova 3 transcription — pre-recorded and live streaming."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from deepgram import AsyncDeepgramClient
from deepgram.listen.v1.types.listen_v1results import ListenV1Results

from app.core.transcription.base import (
    TranscriptSegment,
    TranscriptResult,
    TranscriptWord,
)
from app.core.transcription.exceptions import TranscriptionError
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)
SETTINGS = get_settings()


class DeepgramTranscriber:
    """Transcribes audio using DeepGram Nova 3 with speaker diarization.

    Supports two modes:
      - **Pre-recorded:** ``transcribe_file()`` sends a complete audio file
        to DeepGram's REST API and returns a full ``TranscriptResult``.
      - **Live streaming:** ``live_session()`` opens an async-context-managed
        WebSocket to DeepGram for real-time transcription.
    """

    def __init__(self, api_key: str, model: str = "nova-3", language: str = "en") -> None:
        if not api_key:
            raise TranscriptionError("DeepGram API key is required")
        self._client = AsyncDeepgramClient(api_key=api_key)
        self._model = model
        self._language = language
        logger.info(f"Initialised DeepGram transcriber: model={model}, language={language}")

    # ------------------------------------------------------------------
    # Pre-recorded transcription
    # ------------------------------------------------------------------

    async def transcribe_file(self, audio_data: bytes, mimetype: str) -> TranscriptResult:
        """Transcribe a pre-recorded audio file with speaker diarization.

        Sends the audio to DeepGram's pre-recorded REST API and parses
        the response into speaker-diarized segments.

        Args:
            audio_data: Raw audio bytes.
            mimetype: MIME type (e.g. ``'audio/wav'``, ``'audio/mp3'``).

        Returns:
            TranscriptResult: Full transcript with speaker-diarized segments.

        Raises:
            TranscriptionError: If the transcription fails.
        """
        if not audio_data:
            raise TranscriptionError("Audio data is empty")

        try:
            response = await self._client.listen.v1.media.transcribe_file(
                request=audio_data,
                model=self._model,
                language=self._language,
                smart_format=True,
                diarize=True,
                punctuate=True,
                utterances=True,
            )
            return self._parse_prerecorded(response)
        except TranscriptionError:
            raise
        except Exception as e:
            logger.error(f"DeepGram transcription failed: {e}")
            raise TranscriptionError(f"Transcription failed: {e}") from e

    def _parse_prerecorded(self, response: Any) -> TranscriptResult:
        """Parse DeepGram pre-recorded response into TranscriptResult.

        Uses ``utterances`` for speaker-diarized segments when available,
        falling back to word-level speaker grouping.
        """
        try:
            channel = response.results.channels[0]
            alternative = channel.alternatives[0]
        except (AttributeError, IndexError) as e:
            raise TranscriptionError(f"Unexpected response structure: {e}") from e

        # Extract duration from metadata
        duration = getattr(response.metadata, "duration", 0.0) or 0.0

        # Build segments from utterances (preferred — includes speaker info)
        segments: list[TranscriptSegment] = []
        utterances = getattr(response.results, "utterances", None)

        if utterances:
            for utt in utterances:
                words = [
                    TranscriptWord(
                        word=getattr(w, "punctuated_word", None) or w.word,
                        start=w.start,
                        end=w.end,
                        confidence=w.confidence,
                        speaker=int(getattr(w, "speaker", 0) or 0),
                    )
                    for w in getattr(utt, "words", [])
                ]
                segments.append(TranscriptSegment(
                    text=utt.transcript,
                    speaker=int(getattr(utt, "speaker", 0) or 0),
                    start=utt.start,
                    end=utt.end,
                    confidence=getattr(utt, "confidence", 0.0),
                    is_final=True,
                    words=words,
                ))
        else:
            # Fallback: group words by speaker from alternative
            segments = self._group_words_by_speaker(alternative.words)

        # Build full text and count speakers
        full_text = alternative.transcript or " ".join(s.text for s in segments)
        speaker_ids = {s.speaker for s in segments}

        return TranscriptResult(
            segments=segments,
            full_text=full_text,
            speakers=len(speaker_ids) if speaker_ids else 1,
            duration=duration,
        )

    @staticmethod
    def _group_words_by_speaker(words: list) -> list[TranscriptSegment]:
        """Group consecutive words by speaker into segments.

        Fallback when utterances are not available.
        """
        if not words:
            return []

        segments: list[TranscriptSegment] = []
        current_speaker = int(getattr(words[0], "speaker", 0) or 0)
        current_words: list[TranscriptWord] = []
        current_start = words[0].start

        for w in words:
            speaker = int(getattr(w, "speaker", 0) or 0)
            tw = TranscriptWord(
                word=getattr(w, "punctuated_word", None) or w.word,
                start=w.start,
                end=w.end,
                confidence=w.confidence,
                speaker=speaker,
            )

            if speaker != current_speaker and current_words:
                # Flush current segment
                segments.append(TranscriptSegment(
                    text=" ".join(cw.word for cw in current_words),
                    speaker=current_speaker,
                    start=current_start,
                    end=current_words[-1].end,
                    confidence=sum(cw.confidence for cw in current_words) / len(current_words),
                    is_final=True,
                    words=current_words,
                ))
                current_words = []
                current_speaker = speaker
                current_start = w.start

            current_words.append(tw)

        # Flush final segment
        if current_words:
            segments.append(TranscriptSegment(
                text=" ".join(cw.word for cw in current_words),
                speaker=current_speaker,
                start=current_start,
                end=current_words[-1].end,
                confidence=sum(cw.confidence for cw in current_words) / len(current_words),
                is_final=True,
                words=current_words,
            ))

        return segments

    # ------------------------------------------------------------------
    # Live streaming transcription
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def live_session(
        self,
        sample_rate: int = 16000,
        encoding: str = "linear16",
        channels: int = 1,
    ) -> AsyncIterator["DeepgramLiveConnection"]:
        """Open a live streaming transcription session via WebSocket.

        Used as an async context manager. The underlying DeepGram
        WebSocket is automatically closed on exit.

        When ``channels > 1``, multichannel mode is enabled so that
        DeepGram processes each channel independently.  This is used
        for meeting capture where channel 0 = microphone (local user)
        and channel 1 = system/tab audio (remote participants).

        Args:
            sample_rate: Audio sample rate in Hz.
            encoding: Audio encoding format.
            channels: Number of audio channels.

        Yields:
            DeepgramLiveConnection: Handle for sending and receiving.

        Raises:
            TranscriptionError: If the connection fails to start.
        """
        multichannel = channels > 1
        try:
            options: dict[str, str] = {
                "model": self._model,
                "language": self._language,
                "smart_format": "true",
                "diarize": str(SETTINGS.DEEPGRAM_DIARIZE).lower(),
                "punctuate": "true",
                "encoding": encoding,
                "sample_rate": str(sample_rate),
                "channels": str(channels),
                "interim_results": "true",
                "endpointing": str(SETTINGS.DEEPGRAM_ENDPOINTING_MS),
            }
            if multichannel:
                options["multichannel"] = "true"

            async with self._client.listen.v1.connect(**options) as ws:
                logger.info(
                    f"DeepGram live connection started "
                    f"(channels={channels}, multichannel={multichannel})"
                )
                yield DeepgramLiveConnection(ws, multichannel=multichannel)
                logger.info("DeepGram live connection closing")
        except Exception as e:
            logger.error(f"DeepGram live session error: {e}")
            raise TranscriptionError(f"Live session failed: {e}") from e


class DeepgramLiveConnection:
    """Handle for a DeepGram live transcription WebSocket stream.

    Wraps the DeepGram ``AsyncV1SocketClient`` to provide typed
    ``send_audio``, ``receive``, ``finalize``, and ``close`` methods.

    In multichannel mode, speaker IDs are remapped so that:
      - Channel 0 (mic / local user) → always Speaker 0.
      - Channel 1 (system / remote)  → Speaker ``diarised_id + 1``
        to avoid collision with the local speaker.

    Args:
        ws: The underlying DeepGram WebSocket client.
        multichannel: Whether multichannel mode is active.
    """

    def __init__(self, ws: Any, multichannel: bool = False) -> None:
        self._ws = ws
        self._closed = False
        self._multichannel = multichannel

    async def send_audio(self, audio_chunk: bytes) -> None:
        """Send an audio chunk to the live transcription stream."""
        if self._closed:
            return
        try:
            await self._ws.send_media(audio_chunk)
        except Exception as e:
            logger.error(f"Failed to send audio chunk: {e}")

    async def receive(self) -> TranscriptSegment | None:
        """Receive the next transcript segment from the stream.

        Blocks until a message is available. Returns None for
        non-transcript messages (metadata, utterance-end, etc.).

        Returns:
            TranscriptSegment | None: Parsed segment, or None.
        """
        if self._closed:
            return None
        try:
            result = await self._ws.recv()

            # Only process transcript results
            if not isinstance(result, ListenV1Results):
                return None

            channel = result.channel
            if not channel or not channel.alternatives:
                return None

            alternative = channel.alternatives[0]
            transcript_text = alternative.transcript

            if not transcript_text or not transcript_text.strip():
                return None

            # DeepGram streaming flags:
            #   is_final=True   → words are locked in, won't change
            #   is_final=False  → interim preview, text may still change
            #   speech_final    → speaker has paused/finished (utterance boundary)
            #
            # CRITICAL: is_final determines UI permanence (show as
            # permanent segment vs faded preview).  speech_final is an
            # additional signal for claim-detection timing.
            is_final = getattr(result, "is_final", True)
            speech_final = getattr(result, "speech_final", is_final)

            # Build words with speaker info
            words = [
                TranscriptWord(
                    word=getattr(w, "punctuated_word", None) or w.word,
                    start=w.start,
                    end=w.end,
                    confidence=w.confidence,
                    speaker=int(getattr(w, "speaker", 0) or 0),
                )
                for w in getattr(alternative, "words", [])
            ]

            # Determine primary speaker (majority vote from words)
            if words:
                speaker_counts: dict[int, int] = {}
                for w in words:
                    speaker_counts[w.speaker] = speaker_counts.get(w.speaker, 0) + 1
                primary_speaker = max(speaker_counts, key=speaker_counts.get)
            else:
                primary_speaker = 0

            # Multichannel speaker remapping:
            #   Ch 0 (mic)    → always Speaker 0 (local user)
            #   Ch 1 (system) → Speaker N + 1 (remote participants)
            #
            # NOTE: DeepGram's ``channel_index`` is ``List[float]``
            # (e.g. ``[0.0]``), not a plain int.  We must extract
            # the first element before comparing.
            if self._multichannel:
                raw_ci = getattr(result, "channel_index", [0])
                ch_idx = int(raw_ci[0]) if isinstance(raw_ci, (list, tuple)) and raw_ci else 0
                if ch_idx == 0:
                    primary_speaker = 0
                else:
                    # Offset remote speakers to avoid collision with
                    # local speaker 0.
                    primary_speaker = primary_speaker + 1

            return TranscriptSegment(
                text=transcript_text,
                speaker=primary_speaker,
                start=words[0].start if words else getattr(result, "start", 0.0),
                end=words[-1].end if words else 0.0,
                confidence=sum(w.confidence for w in words) / len(words) if words else 0.0,
                is_final=is_final,
                speech_final=speech_final,
                words=words,
            )

        except StopAsyncIteration:
            self._closed = True
            return None
        except Exception as e:
            logger.error(f"Error receiving transcript: {e}")
            return None

    async def finalize(self) -> None:
        """Signal end of audio input. Remaining audio will be flushed."""
        if self._closed:
            return
        try:
            await self._ws.send_finalize()
        except Exception as e:
            logger.warning(f"Error sending finalize: {e}")

    async def close(self) -> None:
        """Close the live transcription connection."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.send_close_stream()
        except Exception as e:
            logger.warning(f"Error closing live connection: {e}")
