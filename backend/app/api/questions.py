"""Question API endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Question
from app.schemas import QuestionResponse
from app.services.tts_service import get_speech_for_question

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/questions", tags=["questions"])


@router.get("", response_model=list[QuestionResponse])
def get_all_questions(db: Session = Depends(get_db)):
    """
    Get all interview questions.

    Returns a list of all questions in the question bank.
    """
    questions = db.query(Question).all()
    return questions


@router.get("/{question_id}", response_model=QuestionResponse)
def get_question(question_id: int, db: Session = Depends(get_db)):
    """
    Get a specific question by ID.

    Args:
        question_id: The ID of the question to retrieve

    Returns:
        The question details

    Raises:
        HTTPException: 404 if question not found
    """
    question = db.query(Question).filter(Question.id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


@router.post("/{question_id}/speak")
def speak_question(question_id: int, db: Session = Depends(get_db)):
    """
    Generate speech audio for a question using Text-to-Speech.

    Args:
        question_id: The ID of the question to speak

    Returns:
        JSON with base64-encoded audio data

    Raises:
        HTTPException: 404 if question not found
        HTTPException: 500 if TTS generation fails
    """
    question = db.query(Question).filter(Question.id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    logger.info(f"Generating speech for question {question_id}")

    try:
        # Pass question_id for usage logging (interview_id not available in this endpoint)
        audio_base64 = get_speech_for_question(question.question, question_id=question_id)

        if not audio_base64:
            logger.warning(f"Failed to generate speech for question {question_id}")
            raise HTTPException(
                status_code=500,
                detail="Text-to-Speech service unavailable. Please use Text mode."
            )

        return {
            "question_id": question_id,
            "audio_base64": audio_base64,
            "status": "success",
            "audio_format": "mp3"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating speech: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Text-to-Speech service error: {str(e)}"
        )
