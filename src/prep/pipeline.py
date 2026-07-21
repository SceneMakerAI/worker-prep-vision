"""prep 오케스트레이션 — 원본 1건(v_id)을 분석 가능한 상태로 준비한다.

흐름: 원본 확인 → scenedetect 분할 → 프레임 사전추출(ffmpeg CPU) → t_segment 사전등록(2001)
      → t_video 상태 갱신(전처리 완료). 이후 STT(대사) 완료 시 상류가 agent-vision 을 호출한다.
분할·추출은 CPU 블로킹이라 asyncio.to_thread 로 감싸 이벤트 루프를 막지 않는다.
"""

import asyncio
import time

from config import Settings
from persistence.db import Database
from persistence.segments import SegmentRepo
from persistence.videos import VIDEO_STATUS_FAILED, VIDEO_STATUS_FFMPEG_DONE, VideoRepo
from prep.detect import detect_windows
from prep.frames import extract_frames
from log import get_logger

log = get_logger(__name__)

# 최근 성공 prep 의 단계별 소요(초) — 상태 조회 응답용. 프로세스 메모리라 재시작 시 소실.
_last_timings: dict[int, dict[str, float]] = {}


def get_prep_timings(v_id: int) -> dict[str, float] | None:
    """최근 성공한 prep 의 단계별 소요시간(detect/frames/db/total, 초)을 반환한다. 없으면 None."""
    return _last_timings.get(v_id)


async def run_prep(
    db: Database, settings: Settings, v_id: int, file_name: str, force: bool) -> dict:
    """
    Summary:
        영상 1건(v_id)의 전처리 — 장면 분할 + 프레임 추출 + t_segment 사전등록.
    Args:
        db (Database): 커넥션 풀. settings (Settings): 경로·분할·프레임 정책. v_id (int): 대상 영상.
        file_name (str): 원본 파일명(prep 요청으로 수신) — {vod_root}/{v_id}/ 아래.
        force (bool): 기존 세그먼트를 지우고 다시 준비.
    Returns:
        dict: {v_id, segments, frames, failed, status} 집계.
    Description:
        - 원본 파일이 없으면 t_video 를 실패(-1)로 표시하고 종료.
        - scenedetect·ffmpeg 은 to_thread 로 오프로드(블로킹 회피).
        - 성공 시 t_video → FFMPEG_DONE(1002): '전처리 완료, STT 대기'.
    """
    segrepo, vrepo = SegmentRepo(db), VideoRepo(db)
    source = settings.source_path(v_id, file_name)
    
    if not source.is_file():
        log.warning("원본 없음: v_id=%s path=%s", v_id, source)
        await vrepo.set_status(v_id, VIDEO_STATUS_FAILED)
        
        return {"v_id": v_id, "segments": 0, "status": VIDEO_STATUS_FAILED, "error": "SOURCE_NOT_FOUND"}

    # 1) 분할(scenedetect, CPU 블로킹 → 오프로드)
    t0 = time.monotonic()
    windows = await asyncio.to_thread(
        detect_windows,
        source,
        settings.prep_threshold,
        settings.prep_min_sec,
        settings.prep_max_sec,
        settings.prep_detect_fps,
        settings.prep_detect_workers
    )
    t_detect = time.monotonic() - t0

    if not windows:
        log.warning("분할 결과 없음(빈 영상?): v_id=%s", v_id)
        await vrepo.set_status(v_id, VIDEO_STATUS_FAILED)
        return {"v_id": v_id, "segments": 0, "status": VIDEO_STATUS_FAILED, "error": "NO_WINDOWS"}

    # 2) 프레임 추출(ffmpeg CPU, 스레드풀 병렬 → 오프로드)
    t1 = time.monotonic()
    fstats = await asyncio.to_thread(extract_frames, source, windows, settings, v_id)
    t_frames = time.monotonic() - t1

    # 3) t_segment 사전등록(force 면 기존 삭제 후)
    t2 = time.monotonic()
    if force:
        await segrepo.delete_by_video(v_id)
    inserted = await segrepo.create_pending(v_id, windows)
    t_db = time.monotonic() - t2

    # 4) t_video 상태 — 전처리 완료(STT 대기)
    await vrepo.set_status(v_id, VIDEO_STATUS_FFMPEG_DONE)
    timings = {"detect": round(t_detect, 1), "frames": round(t_frames, 1),
               "db": round(t_db, 1), "total": round(time.monotonic() - t0, 1)}
    _last_timings[v_id] = timings
    log.info("prep 완료: v_id=%s, 세그 %d / 프레임 %d(실패 %d) → status=%d "
             "(총 %.1fs = 분할 %.1f + 프레임 %.1f + 등록 %.1f)",
             v_id, inserted, fstats["frames"], fstats["failed"], VIDEO_STATUS_FFMPEG_DONE,
             timings["total"], timings["detect"], timings["frames"], timings["db"])
    return {"v_id": v_id, "segments": inserted, "frames": fstats["frames"],
            "failed": fstats["failed"], "status": VIDEO_STATUS_FFMPEG_DONE, "timings": timings}
