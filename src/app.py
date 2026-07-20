"""Worker Prep Vision API 서버 애플리케이션.

앱 조립 지점: lifespan(공유 자원 수명) + 라우터 등록. 실제 전처리 로직은 여기 두지 않는다
(prep/ 가 담당). 의존 자원은 DB 하나뿐 — 분할·프레임은 로컬 ffmpeg/scenedetect(외부 서비스 없음).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.router import api_router
from config import Settings, get_settings
from persistence.db import Database
from log import get_logger, setup_logging

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Summary:
        FastAPI 수명 관리 — 부팅 시 설정·로깅·DB 풀 준비, 종료 시 정리.
    Args:
        app (FastAPI): 애플리케이션 인스턴스.
    Description:
        - 공유 자원(DB 풀)은 앱 수명 동안 재사용(요청마다 만들지 않음, app.state.db).
    """
    settings: Settings = get_settings()
    setup_logging(level=settings.log_level, log_file=settings.log_path)
    log.info("== startup WORKER PREP VISION ==")
    log.debug("Loaded settings: %s", settings)

    app.state.db = await Database.connect(settings)
    db_ready = await app.state.db.ping()
    log.info(
        "DB 접속 테스트: %s | vod_root=%s frames_root=%s fps=%s",
        db_ready, settings.vod_root, settings.frames_root, settings.prep_fps
    )
    log.info("WORKER PREP VISION 준비 완료.")

    try:
        yield
    finally:
        await app.state.db.close()
        log.info("== shutdown WORKER PREP VISION ==")


app = FastAPI(title="Worker Prep Vision", version="0.1.5", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """미처리 예외를 일관된 JSON 500 으로 변환(스택트레이스는 로그만, 응답 미노출)."""
    log.exception("처리되지 않은 오류: %s %s", request.method, request.url.path)
    
    return JSONResponse(
        status_code=500,
        content={"detail": {"code": "INTERNAL_ERROR", "message": "서버 내부 오류가 발생했습니다."}},
    )


# 집계 라우터 — 라우터 추가/변경은 api/router.py 에서.
app.include_router(api_router)
