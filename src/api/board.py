"""보드 분석 요청 라우터 — 영상(v_id) 단위 크롭 그룹핑 + VLM 판독 + t_board_state 등록.

POST /board/analyze {v_id[, force]} → 즉시 202, 실제 작업(cv2·VLM)은 백그라운드.
GET /board/analyze/{v_id} 로 상태. 결과의 진실원천은 t_board_state(DB)다.
크롭 존재 등 물리 검증은 백그라운드에서 한다(prep-vision 관례).
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel

from config import Settings, get_settings
from persistence.board_states import BoardStateRepo
from persistence.videos import VideoRepo
from board.pipeline import get_board_timings, run_analyze
from log import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["board"])

# 에러 코드 — HTTP 상태가 같아도 클라이언트가 코드로 분기하게 본문에 싣는다.
ERR_VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"      # t_video 부재 (404)
ERR_ALREADY_ANALYZED = "ALREADY_ANALYZED"    # 이미 보드 상태 존재 — 재분석은 force 필요 (409)


def _error(code: str, message: str, **ctx) -> dict:
    """HTTPException.detail 용 구조화 본문 {code, message, ...ctx}."""
    return {"code": code, "message": message, **ctx}


class AnalyzeRequest(BaseModel):
    """보드 분석 요청 — v_id(필수)·force(옵션). 입력은 앞단이 떨궈둔 크롭이라 파일명 불필요."""
    v_id: int
    force: bool = False


class AnalyzeAccepted(BaseModel):
    """분석 접수 응답 — 백그라운드로 처리될 작업 개요."""
    v_id: int
    accepted: bool


class AnalyzeStatus(BaseModel):
    """분석 상태 — 등록된 보드 상태 수(DB=SSOT) + kind별 집계."""
    v_id: int
    states: int
    by_kind: dict[str, int]
    # 최근 분석의 요약(states/groups/errors/timings) — 프로세스 메모리 보관이라
    # 서버 재시작 후에는 None(과거 이력은 로그 참조)
    last_result: dict | None = None


@router.post("/board/analyze", status_code=status.HTTP_202_ACCEPTED,
             response_model=AnalyzeAccepted)
async def analyze(
    req: AnalyzeRequest,
    background: BackgroundTasks,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """
    Summary:
        영상 1건(v_id)의 보드 상태 분석을 접수해 백그라운드로 수행한다.
    Args:
        req (AnalyzeRequest): 대상 v_id·force.
        background (BackgroundTasks): 백그라운드 실행기.
        request (Request): app.state.db 접근용. settings (Settings): 경로·정책.
    Returns:
        AnalyzeAccepted: 접수 여부(202).
    Description:
        - t_video 부재는 404. 이미 상태가 있는데 force 가 아니면 409.
        - 크롭 존재 등 물리 검증은 백그라운드(run_analyze)에서 한다.
    """
    db = request.app.state.db
    video = await VideoRepo(db).get(req.v_id)
    if video is None:
        log.warning("영상 정보 없음: v_id=%s", req.v_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error(ERR_VIDEO_NOT_FOUND, "영상이 없습니다.", v_id=req.v_id),
        )

    existing = await BoardStateRepo(db).count(req.v_id)
    if existing > 0 and not req.force:
        log.warning("이미 분석됨(force 필요): v_id=%s, 상태 %d", req.v_id, existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error(ERR_ALREADY_ANALYZED,
                          "이미 분석된 영상입니다. 다시 하려면 force=true.",
                          v_id=req.v_id, states=existing),
        )

    background.add_task(run_analyze, db, settings, req.v_id, req.force)
    log.info("보드 분석 접수: v_id=%s (force=%s)", req.v_id, req.force)
    return AnalyzeAccepted(v_id=req.v_id, accepted=True)


@router.get("/board/analyze/{v_id}", response_model=AnalyzeStatus)
async def analyze_status(v_id: int, request: Request):
    """
    Summary:
        영상 1건(v_id)의 보드 분석 상태를 조회한다(등록 수 + kind별 집계 + 최근 실행 요약).
    Args:
        v_id (int): 대상 영상 id. request (Request): app.state.db 접근용.
    Returns:
        AnalyzeStatus: 등록된 보드 상태 수·kind별 수·최근 실행 요약.
    """
    db = request.app.state.db
    video = await VideoRepo(db).get(v_id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=_error(ERR_VIDEO_NOT_FOUND, "영상이 없습니다.", v_id=v_id))
    repo = BoardStateRepo(db)
    return AnalyzeStatus(v_id=v_id, states=await repo.count(v_id),
                         by_kind=await repo.count_by_kind(v_id),
                         last_result=get_board_timings(v_id))
