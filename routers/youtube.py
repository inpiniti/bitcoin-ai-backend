"""
YouTube MP3 다운로드 라우터

GET /youtube/download?url=<youtube_url>   yt-dlp로 변환 후 MP3 파일을 클라이언트에 스트리밍
"""
import logging
import os
import re
import tempfile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

logger = logging.getLogger("youtube_router")
router = APIRouter(prefix="/youtube", tags=["youtube"])

try:
    import yt_dlp
except ImportError:
    yt_dlp = None
    logger.warning("yt-dlp not installed. Run: pip install yt-dlp")


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
    if yt_dlp is None:
        raise HTTPException(status_code=503, detail="yt-dlp not installed on server")

    video_id = _extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효한 YouTube URL이 아닙니다")

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

    mp3_files = [f for f in os.listdir(tmp_dir) if f.endswith(".mp3")]
    if not mp3_files:
        raise HTTPException(status_code=500, detail="MP3 변환 실패 (ffmpeg 확인 필요)")

    mp3_path = os.path.join(tmp_dir, mp3_files[0])

    return FileResponse(
        path=mp3_path,
        media_type="audio/mpeg",
        filename=f"{title}.mp3",
    )
