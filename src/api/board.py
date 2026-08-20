"""전광판 판독 라우터 — 영상(v_id) 단위 크롭 그룹핑 + VLM 다수결 판독 + txt·is_changed 갱신.

POST /board/analyze {v_id[, force]} → 즉시 202, 실제 작업(cv2·VLM)은 백그라운드.
GET /board/analyze/{v_id} 로 상태. 결과의 진실원천은 t_frame_baseball_board_detail.txt(DB)다.
크롭·검출 선행 여부 등 물리 검증은 백그라운드에서 한다(prep 관례).
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel

from config import Settings, get_settings
from persistence.details import BoardDetailRepo
from persistence.frames import FrameRepo
from persistence.videos import VideoRepo
from board.pipeline import get_board_result, run_analyze
from log import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["board"])

# 에러 코드 — HTTP 상태가 같아도 클라이언트가 코드로 분기하게 본문에 싣는다.
ERR_VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"  # t_video 부재 (404)
ERR_ALREADY_ANALYZED = "ALREADY_ANALYZED"  # 이미 판독값 존재 — 재실행은 force 필요 (409)


def _error(code: str, message: str, **ctx) -> dict:
    """HTTPException.detail 용 구조화 본문 {code, message, ...ctx}."""
    return {"code": code, "message": message, **ctx}


class AnalyzeRequest(BaseModel):
    """판독 요청 — v_id(필수)·force(옵션). 입력은 앞단이 떨궈둔 크롭·검출행이라 파일명 불필요."""

    v_id: int
    force: bool = False


class AnalyzeAccepted(BaseModel):
    """판독 접수 응답."""

    v_id: int
    accepted: bool


class AnalyzeStatus(BaseModel):
    """판독 상태 — txt 채워진 행 수·변화 프레임 수(DB=SSOT) + 최근 실행 요약."""

    v_id: int
    txt_rows: int
    changed: int
    # 최근 실행 요약(그룹·실패·timings) — 프로세스 메모리 보관이라 재시작 후 None(이력은 로그)
    last_result: dict | None = None


@router.post("/board/analyze", status_code=status.HTTP_202_ACCEPTED, response_model=AnalyzeAccepted)
async def analyze(
    req: AnalyzeRequest,
    background: BackgroundTasks,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """
    Summary:
        영상 1건(v_id)의 전광판 판독을 접수해 백그라운드로 수행한다.
    Args:
        req (AnalyzeRequest): 대상 v_id·force.
        background (BackgroundTasks): 백그라운드 실행기.
        request (Request): app.state.db 접근용. settings (Settings): 경로·정책.
    Returns:
        AnalyzeAccepted: 접수 여부(202).
    Description:
        - t_video 부재는 404. 이미 판독값(txt)이 있는데 force 가 아니면 409.
        - 크롭·검출 선행 여부는 백그라운드(run_analyze)에서 검증한다.
    """
    db = request.app.state.db
    video = await VideoRepo(db).get(req.v_id)
    if video is None:
        log.warning("영상 정보 없음: v_id=%s", req.v_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error(ERR_VIDEO_NOT_FOUND, "영상이 없습니다.", v_id=req.v_id),
        )

    existing = await BoardDetailRepo(db).count_txt(req.v_id)
    if existing > 0 and not req.force:
        log.warning("이미 판독됨(force 필요): v_id=%s, txt %d행", req.v_id, existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error(
                ERR_ALREADY_ANALYZED,
                "이미 판독된 영상입니다. 다시 하려면 force=true.",
                v_id=req.v_id,
                txt_rows=existing,
            ),
        )

    background.add_task(run_analyze, db, settings, req.v_id, req.force)
    log.info("전광판 판독 접수: v_id=%s (force=%s)", req.v_id, req.force)
    return AnalyzeAccepted(v_id=req.v_id, accepted=True)


@router.get("/board/analyze/{v_id}", response_model=AnalyzeStatus)
async def analyze_status(v_id: int, request: Request):
    """
    Summary:
        영상 1건(v_id)의 판독 상태를 조회한다(txt 행 수 + 변화 프레임 수 + 최근 실행 요약).
    Args:
        v_id (int): 대상 영상 id. request (Request): app.state.db 접근용.
    Returns:
        AnalyzeStatus: 판독·변화 집계.
    """
    db = request.app.state.db
    video = await VideoRepo(db).get(v_id)
    if video is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error(ERR_VIDEO_NOT_FOUND, "영상이 없습니다.", v_id=v_id),
        )
    return AnalyzeStatus(
        v_id=v_id,
        txt_rows=await BoardDetailRepo(db).count_txt(v_id),
        changed=await FrameRepo(db).count_changed(v_id),
        last_result=get_board_result(v_id),
    )
