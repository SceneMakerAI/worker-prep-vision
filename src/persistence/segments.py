"""세그먼트 repository — t_segment 데이터 접근(사전등록 전용 서브셋).

DB 연결(풀)은 Database(persistence.db)가 쥐고, 여기서는 SQL·도메인 매핑만 담당한다.
이 워커는 t_segment 를 '생성'만 한다(분석 결과 write 는 agent-vision 몫). 분석 대상 목록은
파일시스템이 아니라 이 테이블이 단일 진실원천(SSOT)이다.
"""

from persistence.db import Database
from log import get_logger

log = get_logger(__name__)

# t_segment.status_code (t_code, object=SEGMENT)
STATUS_INPUT = 2001       # 장면 입력(분석 대기) — prep 이 등록하는 상태
STATUS_ERROR = -1         # ERROR


class SegmentRepo:
    """t_segment 접근 객체 — 세그먼트 사전등록·삭제·집계."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_pending(self, v_id: int, windows: list[tuple[int, int, int]],
                             frame_counts: dict[int, int] | None = None) -> int:
        """
        Summary:
            세그먼트 윈도우를 t_segment 에 '장면 입력'(2001, 분석 대기) 상태로 사전 등록한다.
        Args:
            v_id (int): 대상 영상 id.
            windows (list[tuple[int, int, int]]): (seg_id, start_sec, end_sec) 목록.
            frame_counts (dict[int, int] | None): seg_id → 실제 추출 성공한 프레임(jpg) 수.
                None(또는 누락 seg)은 NULL 로 들어간다.
        Returns:
            int: 삽입된 행 수.
        Description:
            - 재요청 차단(상태 가드)·force 선행을 전제로 plain INSERT. 중복(PK)은 레이스/버그
              신호이므로 예외로 터진다(삼키지 않음).
            - start/end 초는 SEC_TO_TIME() 으로 TIME 컬럼에 넣는다.
            - frame_cnt 는 추출 결과의 부산물이라 등록 시점에 함께 INSERT 한다(사후 UPDATE
              아님 — '생성만 한다' 계약 유지).
        """
        if not windows:
            return 0
        counts = frame_counts or {}
        sql = (
            "INSERT INTO t_segment "
            "(v_id, seg_id, start_time, end_time, frame_cnt, status_code, status_reason) "
            "VALUES (%s, %s, SEC_TO_TIME(%s), SEC_TO_TIME(%s), %s, %s, %s)"
        )
        params = [(v_id, seg_id, start, end, counts.get(seg_id), STATUS_INPUT, "PENDING")
                  for seg_id, start, end in windows]
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(sql, params)
                inserted = cur.rowcount
        log.info("세그먼트 사전등록: v_id=%s, %d행", v_id, inserted)
        return inserted

    async def delete_by_video(self, v_id: int) -> int:
        """
        Summary:
            특정 영상의 모든 세그먼트를 삭제한다(force 재-prep 용).
        Args:
            v_id (int): 대상 영상 id.
        Returns:
            int: 삭제된 행 수.
        """
        sql = "DELETE FROM t_segment WHERE v_id = %s"
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (v_id,))
                deleted = cur.rowcount
        log.info("세그먼트 삭제(force): v_id=%s, %d행", v_id, deleted)
        return deleted

    async def count(self, v_id: int) -> int:
        """특정 영상의 등록된 세그먼트 수(진행/상태 조회용)."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM t_segment WHERE v_id = %s", (v_id,))
                row = await cur.fetchone()
        return int(row[0]) if row else 0
