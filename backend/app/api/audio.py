"""Audio processing API endpoints (STT, etc.)."""

import logging

from fastapi import APIRouter, File, UploadFile, HTTPException

from app.services.stt_service import get_transcript_from_audio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audio", tags=["audio"])


@router.post("/speech-to-text")
async def speech_to_text(file: UploadFile = File(...)):
    """
    Transcribe speech audio to text using OpenAI Whisper.

    Args:
        file: Audio file (WAV, MP3, etc.)

    Returns:
        JSON with transcribed text

    Raises:
        HTTPException: 400 if file is empty or invalid
        HTTPException: 500 if transcription fails
    """
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Check file size (limit to 25MB - Whisper API limit)
    file_size = len(await file.read())
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    if file_size > 25 * 1024 * 1024:  # 25MB
        raise HTTPException(status_code=400, detail="Audio file is too large (max 25MB)")

    logger.info(f"Processing audio file: {file.filename} ({file_size} bytes)")

    try:
        # Read audio file bytes
        await file.seek(0)  # Reset file pointer
        audio_bytes = await file.read()

        # Transcribe audio (interview_id not available in this endpoint, will log with question_id only)
        transcript = get_transcript_from_audio(audio_bytes)

        if not transcript:
            logger.warning("Transcription returned empty result")
            raise HTTPException(
                status_code=500,
                detail="Could not transcribe audio. Please try again."
            )

        if not transcript.strip():
            logger.warning("Transcription is empty string")
            raise HTTPException(
                status_code=400,
                detail="No speech was detected. Please try recording again."
            )

        logger.info(f"Successfully transcribed audio: {transcript[:100]}...")

        return {
            "text": transcript,
            "status": "success"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing audio: {type(e).__name__}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Speech-to-text service error: {str(e)}"
        )
