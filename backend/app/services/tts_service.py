"""Text-to-Speech service using OpenAI API."""

import base64
import logging
import time
from typing import Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def generate_speech(text: str, interview_id: Optional[int] = None, question_id: Optional[int] = None, db: Optional[Session] = None) -> Optional[str]:
    """
    Generate speech audio from text using OpenAI TTS.

    Args:
        text: The text to convert to speech
        interview_id: Interview ID (optional, for usage logging)
        question_id: Question ID (optional, for usage logging)
        db: Database session (optional, for usage logging)

    Returns:
        Base64-encoded MP3 audio data, or None if generation fails

    Raises:
        ValueError: If OpenAI API key not configured
        Exception: If TTS API call fails
    """
    if not settings.openai_api_key:
        raise ValueError("OpenAI API key is not configured.")

    if not text or not text.strip():
        raise ValueError("Text cannot be empty.")

    start_time = time.perf_counter()
    try:
        logger.info(f"Generating speech for text: {text[:50]}...")

        client = OpenAI(api_key=settings.openai_api_key)

        # Use OpenAI's Text-to-Speech API
        response = client.audio.speech.create(
            model="tts-1",  # Fast model
            voice="alloy",  # Neutral voice
            input=text,
            response_format="mp3"
        )

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # Convert audio bytes to base64 for transmission
        audio_base64 = base64.b64encode(response.content).decode("utf-8")

        logger.info(f"Successfully generated speech ({len(response.content)} bytes)")

        # Log usage (non-critical, TTS has no token usage)
        from app.services.llm_usage_logger import log_usage
        log_usage(
            operation="tts",
            provider="openai",
            model="tts-1",
            latency_ms=latency_ms,
            status="success",
            interview_id=interview_id,
            question_id=question_id,
            db=db,
        )

        return audio_base64

    except ValueError as e:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error(f"Configuration error: {str(e)}")

        # Log error (non-critical)
        from app.services.llm_usage_logger import log_usage
        log_usage(
            operation="tts",
            provider="openai",
            model="tts-1",
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
        logger.error(f"Error generating speech: {type(e).__name__}: {str(e)}")

        # Log error (non-critical)
        from app.services.llm_usage_logger import log_usage
        log_usage(
            operation="tts",
            provider="openai",
            model="tts-1",
            latency_ms=latency_ms,
            status="error",
            error_message=type(e).__name__,
            interview_id=interview_id,
            question_id=question_id,
            db=db,
        )

        raise


def get_speech_for_question(question_text: str, interview_id: Optional[int] = None, question_id: Optional[int] = None, db: Optional[Session] = None) -> Optional[str]:
    """
    Generate speech for a question, handling errors gracefully.

    Args:
        question_text: The question text to speak
        interview_id: Interview ID (optional, for usage logging)
        question_id: Question ID (optional, for usage logging)
        db: Database session (optional, for usage logging)

    Returns:
        Base64-encoded audio or None if generation failed

    Note:
        This wraps generate_speech() with error handling suitable for API endpoints.
    """
    try:
        return generate_speech(question_text, interview_id=interview_id, question_id=question_id, db=db)
    except Exception as e:
        logger.error(f"Failed to generate speech: {str(e)}")
        return None
