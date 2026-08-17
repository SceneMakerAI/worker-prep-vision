"""보드 상태 repository — t_board_state 데이터 접근.

DB 연결(풀)은 Database(persistence.db)가 쥐고, 여기서는 SQL·도메인 매핑만 담당한다.
이 워커는 t_board_state 를 생성만 한다 — t_video 상태는 건드리지 않는다(전처리·STT 의
선형 파이프라인 상태와 독립인 병렬 브랜치라, 여기의 성공/실패가 그쪽 게이팅을 오염시키면
안 됨). 분석 진행 여부는 이 테이블의 행 존재 자체가 나타낸다.
"""

import json

from persistence.db import Database
from log import get_logger

log = get_logger(__name__)


class BoardStateRepo:
    """t_board_state 접근 객체 — 상태 구간 등록·삭제·집계."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, v_id: int, rows: list[dict]) -> int:
        """
        Summary:
            보드 상태 구간들을 t_board_state 에 등록한다.
        Args:
            v_id (int): 대상 영상 id.
            rows (list[dict]): {kind, board_id, start, end, crop_cnt, value(dict)} 목록.
                start/end 는 초(소수 포함 가능) — SEC_TO_TIME 으로 TIME(3) 에 저장.
        Returns:
            int: 삽입된 행 수.
        Description:
            - 재요청 가드(409)·force 선행을 전제로 plain INSERT — PK 중복은 레이스/버그
              신호이므로 예외로 터뜨린다(삼키지 않음).
        """
        if not rows:
            return 0
        sql = (
            "INSERT INTO t_board_state "
            "(v_id, kind, board_id, start_time, end_time, crop_cnt, value) "
            "VALUES (%s, %s, %s, SEC_TO_TIME(%s), SEC_TO_TIME(%s), %s, %s)"
        )
        params = [
            (v_id, r["kind"], r["board_id"], r["start"], r["end"], r["crop_cnt"],
             json.dumps(r["value"], ensure_ascii=False))
            for r in rows
        ]
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(sql, params)
                inserted = cur.rowcount
        log.info("보드 상태 등록: v_id=%s, %d행", v_id, inserted)
        return inserted

    async def delete_by_video(self, v_id: int) -> int:
        """특정 영상의 모든 보드 상태를 삭제한다(force 재분석 용)."""
        sql = "DELETE FROM t_board_state WHERE v_id = %s"
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (v_id,))
                deleted = cur.rowcount
        log.info("보드 상태 삭제(force): v_id=%s, %d행", v_id, deleted)
        return deleted

    async def count(self, v_id: int) -> int:
        """특정 영상의 등록된 보드 상태 수(재요청 가드·상태 조회용)."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM t_board_state WHERE v_id = %s", (v_id,))
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def count_by_kind(self, v_id: int) -> dict[str, int]:
        """kind 별 상태 수 — 상태 조회 응답용."""
        sql = "SELECT kind, COUNT(*) FROM t_board_state WHERE v_id = %s GROUP BY kind"
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (v_id,))
                rows = await cur.fetchall()
        return {k: int(n) for k, n in rows}
