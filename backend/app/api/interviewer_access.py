"""Demo-only interviewer access gate. NOT production authentication."""

import secrets

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import PasscodeRequest, PasscodeResponse

router = APIRouter(prefix="/api/interviewer", tags=["interviewer-access"])


@router.post("/verify-passcode", response_model=PasscodeResponse)
def verify_passcode(request: PasscodeRequest):
    """
    Check the interviewer passcode against INTERVIEWER_PASSCODE (backend/.env).

    The passcode is never logged or echoed, and the UI never holds the expected value.
    Access fails closed when no passcode is configured.
    """
    expected = settings.interviewer_passcode
    if not expected:
        raise HTTPException(status_code=503, detail="Interviewer access is not configured.")

    if not secrets.compare_digest(request.passcode.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid passcode.")

    return PasscodeResponse(authorized=True)
