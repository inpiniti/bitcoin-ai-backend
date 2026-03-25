"""
Gemini AI 채팅 라우터

Endpoints:
    POST /api/simple/gemini   사용자 메시지를 Gemini에 전달하고 응답 반환
"""
import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("gemini_router")
router = APIRouter(prefix="/api/simple", tags=["gemini"])


class GeminiRequest(BaseModel):
    message: str
    context: str = ""


class GeminiResponse(BaseModel):
    response: str


@router.post("/gemini", response_model=GeminiResponse)
async def ask_gemini(req: GeminiRequest):
    """
    Google Gemini AI에 질문을 전달하고 응답을 반환합니다.

    - **message**: 사용자 메시지
    - **context**: 선택적 시장 컨텍스트
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY 환경변수가 설정되지 않았습니다. HuggingFace Space 설정에서 추가해주세요.",
        )

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")

        prompt = req.message
        if req.context:
            prompt = f"[컨텍스트]\n{req.context}\n\n[질문]\n{req.message}"

        result = model.generate_content(prompt)
        return GeminiResponse(response=result.text)

    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="google-generativeai 패키지가 설치되지 않았습니다.",
        )
    except Exception as e:
        logger.exception(f"Gemini API 오류: {e}")
        raise HTTPException(status_code=500, detail=f"Gemini API 오류: {str(e)}")
