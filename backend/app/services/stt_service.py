"""Speech-to-Text service using OpenAI Whisper API."""

import logging
import time
from typing import Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def transcribe_audio(audio_bytes: bytes, interview_id: Optional[int] = None, question_id: Optional[int] = None, db: Optional[Session] = None) -> Optional[str]:
    """
    Transcribe audio to text using OpenAI Whisper API.

    Args:
        audio_bytes: Raw audio file bytes (WAV, MP3, etc.)
        interview_id: Interview ID (optional, for usage logging)
        question_id: Question ID (optional, for usage logging)
        db: Database session (optional, for usage logging)

    Returns:
        Transcribed text, or None if transcription fails

    Raises:
        ValueError: If OpenAI API key not configured
        Exception: If transcription API call fails
    """
    if not settings.openai_api_key:
        raise ValueError("OpenAI API key is not configured.")

    if not audio_bytes or len(audio_bytes) == 0:
        raise ValueError("Audio data cannot be empty.")

    start_time = time.perf_counter()
    try:
        logger.info(f"Transcribing audio ({len(audio_bytes)} bytes)")

        client = OpenAI(api_key=settings.openai_api_key)

        # Use OpenAI's Whisper API for speech-to-text
        # The API accepts various audio formats: mp3, mp4, mpeg, mpga, m4a, wav, webm
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.wav", audio_bytes, "audio/wav"),
            language="en"  # Specify English language
        )

        latency_ms = int((time.perf_counter() - start_time) * 1000)
        transcribed_text = transcript.text.strip()

        logger.info(f"Successfully transcribed audio: {transcribed_text[:100]}...")

        # Log usage (non-critical, STT has no token usage)
        from app.services.llm_usage_logger import log_usage
        log_usage(
            operation="stt",
            provider="openai",
            model="whisper-1",
            latency_ms=latency_ms,
            status="success",
            interview_id=interview_id,
            question_id=question_id,
            db=db,
        )

        return transcribed_text

    except ValueError as e:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error(f"Configuration error: {str(e)}")

        # Log error (non-critical)
        from app.services.llm_usage_logger import log_usage
        log_usage(
            operation="stt",
            provider="openai",
            model="whisper-1",
            latency_ms=latency_ms,
            status="error",
            error_message="ConfigurationError",
            interview_id=interview_id,
            question_id=question_id,
            db=db,
        )

        raise
    except Exception as e:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error(f"Error transcribing audio: {type(e).__name__}: {str(e)}")

        # Log error (non-critical)
        from app.services.llm_usage_logger import log_usage
        log_usage(
            operation="stt",
            provider="openai",
            model="whisper-1",
            latency_ms=latency_ms,
            status="error",
            error_message=type(e).__name__,
            interview_id=interview_id,
            question_id=question_id,
            db=db,
        )

        raise


def get_transcript_from_audio(audio_bytes: bytes, interview_id: Optional[int] = None, question_id: Optional[int] = None, db: Optional[Session] = None) -> Optional[str]:
    """
    Get transcript from audio, handling errors gracefully.

    Args:
        audio_bytes: Raw audio file bytes
        interview_id: Interview ID (optional, for usage logging)
        question_id: Question ID (optional, for usage logging)
        db: Database session (optional, for usage logging)

    Returns:
        Transcribed text or None if transcription failed

    Note:
        This wraps transcribe_audio() with error handling suitable for API endpoints.
    """
    try:
        return transcribe_audio(audio_bytes, interview_id=interview_id, question_id=question_id, db=db)
    except Exception as e:
        logger.error(f"Failed to transcribe audio: {str(e)}")
        return None
