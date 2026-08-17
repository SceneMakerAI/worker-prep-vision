"""board 오케스트레이션 — 전광판 크롭을 판독해 t_frame_board_detail.txt 를 채운다.

흐름: 대상 선정(t_frame_adv → 간격 샘플 → detect=1 항목) → kind별 시간축 그룹핑(dedup)
      → 그룹 표본 다수결 판독(VLM) → txt 그룹 전파 저장 → 변화 마킹(is_changed).
전량 판독(agent-vision3 read 레퍼런스) 대비 판독량을 1/10 수준으로 줄이는 것이 이관 목적.
그룹핑(cv2)·판독(HTTP×N)은 블로킹이라 asyncio.to_thread 로 감싼다.
t_video 상태는 갱신하지 않는다 — prep 선형 파이프라인 소유(게이팅 오염 방지).
"""

import asyncio
import time
from pathlib import Path

from config import Settings
from persistence.db import Database
from persistence.details import BoardDetailRepo
from persistence.frames import FrameRepo
from board import change, select
from board.dedup import StateRun, dedup_runs, vote_samples
from board.reader import ReadFailure, read_groups
from log import get_logger

log = get_logger(__name__)

# 최근 분석의 단계별 소요(초)·결과 요약 — 상태 조회 응답용. 프로세스 메모리라 재시작 시 소실.
_last_results: dict[int, dict] = {}


def get_board_result(v_id: int) -> dict | None:
    """최근 분석의 요약(판독·실패·변화·단계별 소요초)을 반환한다. 없으면 None."""
    return _last_results.get(v_id)


def _crop_path(settings: Settings, v_id: int, kind: str, idx: int) -> Path:
    """크롭 파일 경로 — {vod_root}/{v_id}/crops/{kind 소문자}/{idx:05d}.jpg (상류 계약)."""
    return settings.crops_dir(v_id, kind.lower()) / f"{idx:05d}.jpg"


def _group_kinds(settings: Settings, v_id: int, items: list[tuple[int, str]],
                 sec_by_idx: dict[int, int]) -> tuple[dict[str, list[StateRun]], int]:
    """kind별 dedup 그룹핑 — (kind → StateRun 목록, 크롭 파일 부재 건수).

    CPU 블로킹(cv2×N) — 호출자가 to_thread 로 감싼다.
    """
    by_kind: dict[str, list[tuple[int, int, Path]]] = {}
    missing = 0
    for idx, kind in items:
        p = _crop_path(settings, v_id, kind, idx)
        if not p.is_file():
            missing += 1        # detect=1 인데 크롭 없음 — 해당 항목만 빈 txt 로 남는다
            continue
        by_kind.setdefault(kind, []).append((idx, sec_by_idx[idx], p))
    if missing:
        log.warning("크롭 파일 부재: v_id=%s, %d건(항목별 격리 — txt 빈 채 유지)", v_id, missing)
    return {k: dedup_runs(v, settings.board_mae_th, settings.board_min_cnt,
                          settings.gap_sec)
            for k, v in by_kind.items()}, missing


async def run_analyze(db: Database, settings: Settings, v_id: int, force: bool) -> dict:
    """
    Summary:
        영상 1건(v_id)의 전광판 판독 — 그룹핑 + 표본 다수결 판독 + txt 전파 + 변화 마킹.
    Args:
        db (Database): 커넥션 풀. settings (Settings): 경로·그룹핑·VLM 정책.
        v_id (int): 대상 영상. force (bool): 기존 판독값 무시하고 재실행(가드 통과용 —
            실행 자체는 항상 reset_txt 로 시작하므로 동작 차이는 없음).
    Returns:
        dict: {v_id, groups, read_ok, read_failed, txt_rows, changed, timings} 집계.
    Description:
        - 대상이 없으면(상류 미선행) 등록 없이 종료(결과에 error 표기).
        - 그룹 판독 실패는 건별 격리 — 해당 그룹 구성원의 txt 만 비어 남는다.
    """
    frames, details = FrameRepo(db), BoardDetailRepo(db)

    # 1) 대상 선정 — t_frame_adv 조건 → 간격 샘플 → detect=1 (idx, kind)
    t0 = time.monotonic()
    targets = await frames.fetch_targets(v_id)
    if not targets:
        log.warning("판독 대상 없음(상류 미선행?): v_id=%s", v_id)
        result = {"v_id": v_id, "error": "NO_TARGETS"}
        _last_results[v_id] = result
        return result
    
    picked = select.sample_by_interval(targets, settings.board_crop_interval)
    items = await details.fetch_kinds(v_id, [i for i, _ in picked])
    if not items:
        log.warning("판독 항목 없음(img_models 검출 미선행?): v_id=%s", v_id)
        result = {"v_id": v_id, "error": "NO_ITEMS"}
        _last_results[v_id] = result
        return result

    # 2) kind별 시간축 그룹핑 (cv2 블로킹 → 오프로드)
    sec_by_idx = dict((idx, sec) for idx, sec in picked)
    runs_by_kind, missing = await asyncio.to_thread(
        _group_kinds, settings, v_id, items, sec_by_idx)
    t_dedup = time.monotonic() - t0

    # 3) 그룹 표본 다수결 판독 (HTTP 병렬 블로킹 → 오프로드)
    t1 = time.monotonic()
    jobs: list[tuple[str, list[Path]]] = []
    job_key: list[tuple[str, int]] = []
    for kind, runs in runs_by_kind.items():
        for gid in sorted({r.group_id for r in runs}):
            jobs.append((kind, vote_samples(runs, gid, settings.board_vote_k)))
            job_key.append((kind, gid))
    log.info("판독 시작: v_id=%s — 항목 %d건 → 그룹 %d개 (표본 %d장/그룹)",
             v_id, len(items), len(jobs), settings.board_vote_k)
    values = await asyncio.to_thread(read_groups, jobs, settings)
    t_read = time.monotonic() - t1

    # 4) txt 저장 — 초기화 후 그룹 값을 구성원 전 행에 전파 (실패 그룹은 빈 채 유지)
    t2 = time.monotonic()
    await details.reset_txt(v_id)
    read_ok = read_failed = txt_rows = 0
    value_by_key = dict(zip(job_key, values))
    for kind, runs in runs_by_kind.items():
        for gid in sorted({r.group_id for r in runs}):
            val = value_by_key[(kind, gid)]
            if isinstance(val, ReadFailure):
                read_failed += 1
                log.warning("  판독 실패(격리): v_id=%s %s", v_id, val)
                continue
            txt, parsed = val
            if not parsed:                 # 원문 저장 — 조용히 버리면 추적 불가
                log.warning("  후처리 실패(원문 저장): v_id=%s %s g%d: %r", v_id, kind, gid, txt)
            idxs = [i for r in runs if r.group_id == gid for i in r.idxs]
            txt_rows += await details.update_txt_many(v_id, kind, idxs, txt)
            read_ok += 1

    # 5) 변화 마킹 — 판독값 시계열에서 달라진 프레임에 is_changed=1
    series = await details.fetch_txt_series(v_id)
    changed, per_kind = change.detect(series)
    await frames.reset_is_changed(v_id)
    marked = await frames.mark_is_changed(v_id, sorted(changed))
    t_db = time.monotonic() - t2

    timings = {"dedup": round(t_dedup, 1), "read": round(t_read, 1),
               "db": round(t_db, 1), "total": round(time.monotonic() - t0, 1)}
    result = {"v_id": v_id, "groups": len(jobs), "read_ok": read_ok,
              "read_failed": read_failed, "crop_missing": missing,
              "txt_rows": txt_rows, "changed": marked, "timings": timings}
    _last_results[v_id] = result
    log.info("board 완료: v_id=%s — 그룹 %d(실패 %d) → txt %d행 / 변화 %d프레임 (kind별 %s) "
             "(총 %.1fs = 그룹핑 %.1f + 판독 %.1f + 저장·마킹 %.1f)",
             v_id, len(jobs), read_failed, txt_rows, marked, per_kind,
             timings["total"], timings["dedup"], timings["read"], timings["db"])
    return result
