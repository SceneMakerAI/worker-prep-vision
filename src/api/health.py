"""헬스 체크 라우터.

라이브니스: 프로세스가 살아있는가. 레디니스: 의존 자원(DB)까지 받을 준비가 됐는가.
이 워커의 의존은 DB·로컬 ffmpeg(prep)·VLM 엔드포인트(board 판독)다.
"""

import asyncio
import json
import shutil
import urllib.request

from fastapi import APIRouter, Request, Response, status

from config import Settings, get_settings

router = APIRouter(tags=["health"])


def _vlm_ok(settings: Settings) -> bool:
    """VLM /v1/models 응답 확인 — 블로킹(urllib)이라 호출자가 to_thread 로 감싼다."""
    try:
        url = settings.vlm_url.rstrip("/") + "/v1/models"
        with urllib.request.urlopen(url, timeout=3) as r:
            return bool(json.loads(r.read()).get("data"))
    except Exception:
        return False


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
        dict: {"status", "db": "ok"|"down", "ffmpeg": "ok"|"missing", "vlm": "ok"|"down"}.
    Description:
        - DB 미응답이면 503(로드밸런서 차단). ffmpeg 부재는 프레임 추출 불가라 게이팅에 포함.
        - vlm 은 정보성(비게이팅) — 보드 판독만 막힐 뿐 prep 기능은 무관하므로
          vLLM 장애가 prep 접수까지 차단하지 않게 한다.
    """
    db_ok = await request.app.state.db.ping()
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    vlm_ok = await asyncio.to_thread(_vlm_ok, get_settings())
    ready = db_ok and ffmpeg_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not ready",
        "db": "ok" if db_ok else "down",
        "ffmpeg": "ok" if ffmpeg_ok else "missing",
        "vlm": "ok" if vlm_ok else "down",
    }
