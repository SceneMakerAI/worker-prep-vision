"""헬스 체크 라우터.

라이브니스: 프로세스가 살아있는가. 레디니스: 의존 자원(DB)까지 받을 준비가 됐는가.
이 워커의 의존은 DB(t_video 조회·t_segment 등록)와 로컬 ffmpeg(프로세스 존재)뿐이다.
"""

import shutil

from fastapi import APIRouter, Request, Response, status

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    """라이브니스 — 프로세스 생존만 확인(의존 자원 미검사)."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response):
    """
    Summary:
        레디니스 — DB 응답 가능 + ffmpeg 실행파일 존재를 확인한다.
    Args:
        request (Request): app.state.db 접근용. response (Response): 미준비 시 503 세팅.
    Returns:
        dict: {"status", "db": "ok"|"down", "ffmpeg": "ok"|"missing"}.
    Description:
        - DB 미응답이면 503(로드밸런서 차단). ffmpeg 부재는 프레임 추출 불가라 게이팅에 포함.
    """
    db_ok = await request.app.state.db.ping()
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ready = db_ok and ffmpeg_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not ready",
        "db": "ok" if db_ok else "down",
        "ffmpeg": "ok" if ffmpeg_ok else "missing",
    }
