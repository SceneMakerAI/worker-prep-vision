"""전처리 요청 라우터 — 영상(v_id) 단위 장면분할 + 프레임 추출 + t_segment 사전등록.

POST /prep {v_id, file_name, force} → 즉시 202, 실제 작업(scenedetect·ffmpeg)은 백그라운드.
GET /prep/{v_id} 로 상태. 대상·결과는 파일시스템이 아니라 t_segment(DB)가 단일 진실원천이다.
"""

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from config import Settings, get_settings
from persistence.segments import SegmentRepo
from persistence.videos import VideoRepo
from prep.pipeline import run_prep
from log import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["prep"])

# 에러 코드 — HTTP 상태가 같아도 클라이언트가 코드로 분기하게 본문에 싣는다.
ERR_VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"    # t_video 부재 (404)
ERR_ALREADY_PREPPED = "ALREADY_PREPPED"    # 이미 세그먼트 존재 — 재-prep 은 force 필요 (409)


def _error(code: str, message: str, **ctx) -> dict:
    """HTTPException.detail 용 구조화 본문 {code, message, ...ctx}."""
    return {"code": code, "message": message, **ctx}


class PrepRequest(BaseModel):
    """전처리 요청 — v_id·원본 파일명·재-prep 여부(force)."""
    v_id: int
    file_name: str        # {VOD_ROOT}/{v_id}/ 바로 아래의 원본 파일명 (경로 아님)
    force: bool = False

    @field_validator("file_name")
    @classmethod
    def _safe_file_name(cls, v: str) -> str:
        """경로 탈출(traversal) 방지 — 구분자·상위참조 없는 순수 파일명만 허용."""
        if not v or v in {".", ".."} or "\\" in v or v != Path(v).name:
            raise ValueError("file_name 은 경로 구분자 없는 순수 파일명이어야 합니다.")
        return v


class PrepAccepted(BaseModel):
    """전처리 접수 응답 — 백그라운드로 처리될 작업 개요."""
    v_id: int
    accepted: bool
    source: str


class PrepStatus(BaseModel):
    """전처리 상태 — t_video 상태 + 등록된 세그먼트 수(DB=SSOT)."""
    v_id: int
    status: int
    segments: int


@router.post("/prep", status_code=status.HTTP_202_ACCEPTED, response_model=PrepAccepted)
async def prep(
    req: PrepRequest,
    background: BackgroundTasks,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """
    Summary:
        영상 1건(v_id)의 전처리(분할+프레임+등록)를 접수해 백그라운드로 수행한다.
    Args:
        req (PrepRequest): 대상 v_id·원본 파일명(file_name)·force.
        background (BackgroundTasks): 백그라운드 실행기.
        request (Request): app.state.db 접근용. settings (Settings): 경로·정책.
    Returns:
        PrepAccepted: 접수 여부와 원본 경로(202).
    Description:
        - t_video 부재는 404. 이미 세그먼트가 있는데 force 가 아니면 409(재-prep 은 force).
        - 원본 존재·분할 결과 등 물리 검증은 백그라운드(run_prep)에서 하고 실패 시 t_video=-1.
    """
    db = request.app.state.db
    video = await VideoRepo(db).get(req.v_id)
    
    if video is None:
        log.warning("영상 정보 없음: v_id=%s", req.v_id)
        
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error(ERR_VIDEO_NOT_FOUND, "영상이 없습니다.", v_id=req.v_id)
        )

    existing = await SegmentRepo(db).count(req.v_id)
    
    if existing > 0 and not req.force:
        log.warning("이미 전처리됨(force 필요): v_id=%s, 세그 %d", req.v_id, existing)
        
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error(
                ERR_ALREADY_PREPPED, 
                "이미 전처리된 영상입니다. 다시 하려면 force=true.", 
                v_id=req.v_id, 
                segments=existing
            )
        )

    background.add_task(run_prep, db, settings, req.v_id, req.file_name, req.force)
    log.info("전처리 접수: v_id=%s file=%s (force=%s)", req.v_id, req.file_name, req.force)
    
    return PrepAccepted(
        v_id=req.v_id, accepted=True, source=str(settings.source_path(req.v_id, req.file_name))
    )


@router.get("/prep/{v_id}", response_model=PrepStatus)
async def prep_status(v_id: int, request: Request):
    """
    Summary:
        영상 1건(v_id)의 전처리 진행 상태를 조회한다(t_video 상태 + 세그먼트 수).
    Args:
        v_id (int): 대상 영상 id. request (Request): app.state.db 접근용.
    Returns:
        PrepStatus: 영상 상태코드와 등록된 세그먼트 수.
    """
    db = request.app.state.db
    video = await VideoRepo(db).get(v_id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=_error(ERR_VIDEO_NOT_FOUND, "영상이 없습니다.", v_id=v_id))
    segments = await SegmentRepo(db).count(v_id)
    return PrepStatus(v_id=v_id, status=video["status_code"], segments=segments)
