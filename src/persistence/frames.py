"""프레임 repository — t_frame_baseball 접근 (판독 대상 선정·변화 마킹).

agent-vision3 db/frames 의 판독 관련 서브셋 이식. normal·detect_major_obj 는 상류
img_models 산출이고, 이 워커는 **is_changed 만 UPDATE** 한다.
"""

from persistence.db import Database
from log import get_logger

log = get_logger(__name__)

# 전광판 주요 항목(ETC 제외 5종) 검출 컬럼. 상류에서 이름이 바뀐 적이 있어
# (detect_ok → detect_major_obj) 한곳에 모아둔다 — 또 바뀌면 이 줄만 고치면 된다.
COL_MAJOR_OK = "detect_major_obj"
# **이 컬럼은 불리언이 아니라 '검출된 주요 항목 개수'다(0~5).** 타입이 tinyint(1) 이고
# 스키마 코멘트도 "0=none, 1=5개 detect 됨"이라 불리언처럼 보이지만 실데이터는 0~5 다
# (2026-08 개명 후 실측). `= 1` 로 필터하면 "1종만 검출된" 프레임만 잡아 대부분을
# 통째로 놓친다(레퍼런스에서 실사고 이력 있음).
MAJOR_OK_VALUE = 5
# 추론 대상 프레임 조건 — 야구 화면(normal=0) + 전광판 주요 5종 전부 검출.
# 투구 자세(pitch)는 조건이 아니다: 전광판은 타격 이후·다음 투구 전에 갱신되므로
# 투구 프레임만 보면 변화 시점을 놓친다.
_TARGET_COND = f"a.v_id = %s AND a.normal = 0 AND a.{COL_MAJOR_OK} = {MAJOR_OK_VALUE}"


class FrameRepo:
    """t_frame_baseball 접근 객체 — 판독 대상 조회·is_changed 마킹."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def fetch_targets(self, v_id: int) -> list[tuple[int, int]]:
        """
        Summary:
            판독 대상 프레임을 (idx, idx_sec) 로 조회한다 — 시각 오름차순.
        Args:
            v_id (int): 대상 영상 id.
        Returns:
            list[tuple[int, int]]: (프레임 순번, 영상 내 시각(초)) 목록.
        """
        sql = (f"SELECT a.idx, a.idx_sec FROM t_frame_baseball a "
               f"WHERE {_TARGET_COND} ORDER BY a.idx_sec, a.idx")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (v_id,))
                rows = [(int(i), int(s)) for i, s in await cur.fetchall()]
        log.info("대상 프레임 조회: v_id=%s → %d장", v_id, len(rows))
        return rows

    async def reset_is_changed(self, v_id: int) -> int:
        """해당 영상의 is_changed 를 전부 0 으로 되돌린다 — 재실행 안전(이전 런 누적 방지)."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE t_frame_baseball SET is_changed = 0 WHERE v_id = %s", (v_id,))
                n = cur.rowcount
        log.info("is_changed 초기화: v_id=%s, %d행", v_id, n)
        return n

    async def mark_is_changed(self, v_id: int, idxs: list[int]) -> int:
        """
        Summary:
            변화 프레임에 is_changed=1 을 표시한다(하류 board 단계의 입력).
        Args:
            v_id (int): 영상 id. idxs (list[int]): change.detect 가 뽑은 프레임 순번.
        Returns:
            int: 마킹된 행 수.
        """
        if not idxs:
            return 0
        ph = ",".join(["%s"] * len(idxs))
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE t_frame_baseball SET is_changed = 1 WHERE v_id = %s AND idx IN ({ph})",
                    (v_id, *idxs))
                return cur.rowcount

    async def count_changed(self, v_id: int) -> int:
        """is_changed=1 프레임 수 — 상태 조회용."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM t_frame_baseball WHERE v_id = %s AND is_changed = 1",
                    (v_id,))
                row = await cur.fetchone()
        return int(row[0]) if row else 0
