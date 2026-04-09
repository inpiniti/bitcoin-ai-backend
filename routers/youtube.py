"""
YouTube MP3 다운로드 라우터

GET /youtube/download?url=<youtube_url>   yt-dlp로 변환 후 MP3 파일을 클라이언트에 스트리밍
"""
import logging
import os
import re
import tempfile

import yt_dlp
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

logger = logging.getLogger("youtube_router")
router = APIRouter(prefix="/youtube", tags=["youtube"])


def _extract_video_id(url: str) -> str | None:
    patterns = [
        r"[?&]v=([^&]+)",
        r"youtu\.be/([^?#]+)",
        r"youtube\.com/shorts/([^?#]+)",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


@router.get("/download")
async def download(url: str = Query(..., description="YouTube URL")):
    video_id = _extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효한 YouTube URL이 아닙니다")

    # 임시 폴더에 다운로드 후 클라이언트로 전송
    tmp_dir = tempfile.mkdtemp()
    outtmpl = os.path.join(tmp_dir, "%(title)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "download") if info else "download"
    except Exception as e:
        logger.error(f"[youtube] 다운로드 실패: {e}")
        raise HTTPException(status_code=500, detail=f"다운로드 실패: {e}")

    # 변환된 mp3 파일 찾기
    mp3_files = [f for f in os.listdir(tmp_dir) if f.endswith(".mp3")]
    if not mp3_files:
        raise HTTPException(status_code=500, detail="MP3 변환 실패")

    mp3_path = os.path.join(tmp_dir, mp3_files[0])
    filename = f"{title}.mp3"

    return FileResponse(
        path=mp3_path,
        media_type="audio/mpeg",
        filename=filename,
        background=None,
    )
