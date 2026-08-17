"""board 오케스트레이션 — 크롭(앞단 산출물)을 보드 상태 타임라인으로 바꿔 t_board_state 에 등록.

흐름: 크롭 확인 → kind별 시간축 그룹핑(dedup) → 그룹 대표 VLM 판독 → t_board_state 등록.
그룹핑(cv2)·판독(HTTP×N)은 블로킹이라 asyncio.to_thread 로 감싸 이벤트 루프를 막지 않는다.
t_video 상태는 갱신하지 않는다 — 진행 여부는 t_board_state 행 존재가 나타낸다.
"""

import asyncio
import time

from config import Settings
from persistence.db import Database
from persistence.board_states import BoardStateRepo
from board.dedup import dedup_runs, vote_samples
from board.reader import read_groups
from log import get_logger

log = get_logger(__name__)

# 최근 분석의 단계별 소요(초)·결과 요약 — 상태 조회 응답용. 프로세스 메모리라 재시작 시 소실.
_last_results: dict[int, dict] = {}


def get_board_timings(v_id: int) -> dict | None:
    """최근 분석의 단계별 소요시간(dedup/read/db/total, 초)을 반환한다. 없으면 None."""
    return _last_results.get(v_id)


def _collect(
    settings: Settings, v_id: int,
) -> tuple[list[dict], list[tuple[str, list]], list[tuple[str, int]]]:
    """kind별 그룹핑 → (등록 행 목록, 그룹당 판독 job 목록, job별 (kind, group_id) 키).

    행의 value 는 아직 비어 있고, jobs 판독 결과를 group_key 로 나눠 받는다.
    CPU 블로킹(cv2×N) — 호출자가 to_thread 로 감싼다.
    """
    rows: list[dict] = []
    jobs: list[tuple[str, list]] = []
    job_key: list[tuple[str, int]] = []          # jobs[i] 가 어느 (kind, group_id) 인지
    for kind in settings.kinds:
        crops = settings.crops_dir(v_id, kind)
        if not crops.is_dir():
            log.warning("크롭 없음(kind 건너뜀): %s", crops)
            continue
        runs = dedup_runs(crops, settings.board_mae_th, settings.board_min_cnt,
                          settings.gap_sec)
        for gid in sorted({r.group_id for r in runs}):
            jobs.append((kind, vote_samples(runs, gid, settings.board_vote_k)))
            job_key.append((kind, gid))
        for i, run in enumerate(sorted(runs, key=lambda r: r.start), start=1):
            rows.append({"kind": kind, "board_id": i, "start": run.start, "end": run.end,
                         "crop_cnt": run.count, "group_key": (kind, run.group_id)})
        log.info("그룹핑: v_id=%s kind=%s → 그룹 %d / 구간 %d", v_id, kind,
                 len({r.group_id for r in runs}), len(runs))
    return rows, jobs, job_key


async def run_analyze(db: Database, settings: Settings, v_id: int, force: bool) -> dict:
    """
    Summary:
        영상 1건(v_id)의 보드 상태 분석 — 크롭 그룹핑 + VLM 판독 + t_board_state 등록.
    Args:
        db (Database): 커넥션 풀. settings (Settings): 경로·그룹핑·VLM 정책.
        v_id (int): 대상 영상. force (bool): 기존 상태를 지우고 다시 분석.
    Returns:
        dict: {v_id, states, groups, errors, timings} 집계.
    Description:
        - 크롭이 하나도 없으면 등록 없이 종료(결과에 error 표기) — t_video 는 건드리지 않음.
        - 판독 실패 그룹은 value={"error": ...} 로 등록된다(구간 정보 자체는 유효).
    """
    repo = BoardStateRepo(db)

    t0 = time.monotonic()
    rows, jobs, job_key = await asyncio.to_thread(_collect, settings, v_id)
    t_dedup = time.monotonic() - t0

    if not rows:
        log.warning("분석할 크롭 없음: v_id=%s (%s)", v_id, settings.crops_dir(v_id, "*"))
        result = {"v_id": v_id, "states": 0, "groups": 0, "errors": 0, "error": "NO_CROPS"}
        _last_results[v_id] = result
        return result

    # 그룹 대표 판독(HTTP 병렬, 블로킹 → 오프로드) → (kind, group_id) 로 값 배분
    t1 = time.monotonic()
    values = await asyncio.to_thread(read_groups, jobs, settings)
    value_by_key = dict(zip(job_key, values))
    for r in rows:
        r["value"] = value_by_key[r.pop("group_key")]
    t_read = time.monotonic() - t1

    # 등록(force 면 기존 삭제 후)
    t2 = time.monotonic()
    if force:
        await repo.delete_by_video(v_id)
    inserted = await repo.create(v_id, rows)
    t_db = time.monotonic() - t2

    errors = sum(1 for r in rows if "error" in r["value"])
    timings = {"dedup": round(t_dedup, 1), "read": round(t_read, 1),
               "db": round(t_db, 1), "total": round(time.monotonic() - t0, 1)}
    result = {"v_id": v_id, "states": inserted, "groups": len(jobs), "errors": errors,
              "timings": timings}
    _last_results[v_id] = result
    log.info("board 완료: v_id=%s, 구간 %d / 그룹 %d / 판독실패 %d "
             "(총 %.1fs = 그룹핑 %.1f + 판독 %.1f + 등록 %.1f)",
             v_id, inserted, len(jobs), errors,
             timings["total"], timings["dedup"], timings["read"], timings["db"])
    return result
