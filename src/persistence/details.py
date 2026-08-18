"""전광판 판독값 repository — t_frame_baseball_board_detail 접근 (board 판독 산출·하류 board 단계 입력).

행 자체(검출 박스·kind)는 상류 img_models 가 만들고, 이 워커는 **txt 컬럼만 UPDATE** 한다.
(agent-vision3 db/details 이식 — 판독 이관에 따라 저장 주체가 이 워커로 옮겨온 것.)
"""

from persistence.db import Database
from log import get_logger

log = get_logger(__name__)

# 변화 비교에서 제외할 kind — ETC(타자·투수 기록)는 타석마다 바뀌어 넣으면
# 거의 모든 프레임이 변화가 된다. 포함 목록이 아니라 제외 목록인 이유:
# 새 kind 가 추가되면 기본으로 비교에 들어가는 쪽이 안전하다 (놓침 < 과다).
CHANGE_EXCLUDE_KINDS = ("ETC",)


class BoardDetailRepo:
    """t_frame_baseball_board_detail 접근 객체 — 판독 대상 조회·txt 저장·시계열 조회."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def fetch_kinds(self, v_id: int, idxs: list[int]) -> list[tuple[int, str]]:
        """
        Summary:
            선택된 프레임들에서 실제로 판독할 (idx, kind) 목록을 조회한다.
        Args:
            v_id (int): 영상 id. idxs (list[int]): 간격 샘플링으로 고른 프레임 순번.
        Returns:
            list[tuple[int, str]]: detect=1 인 항목만.
        Description:
            - detect=0 행에 판독값을 채우면 "검출 안 됐는데 읽은 값이 있다"는 모순이 된다.
            - detect_major_obj=5 조건상 주요 5종은 항상 detect=1 이고, 실제로 갈리는 건
              ETC 뿐이다.
        """
        if not idxs:
            return []
        ph = ",".join(["%s"] * len(idxs))
        sql = (f"SELECT idx, kind FROM t_frame_baseball_board_detail "
               f"WHERE v_id = %s AND detect = 1 AND idx IN ({ph}) ORDER BY idx, kind")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (v_id, *idxs))
                rows = [(int(i), str(k)) for i, k in await cur.fetchall()]
        log.info("판독 대상: v_id=%s → %d건 (%d프레임)", v_id, len(rows), len(idxs))
        return rows

    async def reset_txt(self, v_id: int) -> int:
        """
        Summary:
            해당 영상의 판독값(txt)을 전부 비운다 — 재실행 안전.
        Args:
            v_id (int): 대상 영상 id.
        Returns:
            int: 대상 행 수.
        Description:
            - 안 비우면 이전 런에서 판독한 프레임이 남아, 이번 런에서 빠진 프레임의
              옛 값이 변화 비교에 섞여 가짜 변화를 만든다.
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE t_frame_baseball_board_detail SET txt = '' WHERE v_id = %s", (v_id,))
                n = cur.rowcount
        log.info("txt 초기화: v_id=%s, %d행", v_id, n)
        return n

    async def update_txt_many(self, v_id: int, kind: str, idxs: list[int], txt: str) -> int:
        """
        Summary:
            같은 상태 그룹의 구성원 프레임 전체에 판독값 하나를 전파 저장한다.
        Args:
            v_id (int): 영상 id. kind (str): 항목. idxs (list[int]): 그룹 구성원 프레임 순번.
            txt (str): 다수결로 확정된 저장값.
        Returns:
            int: 갱신된 행 수.
        Description:
            - 그룹당 판독은 표본 몇 장뿐이지만 txt 는 구성원 전 행에 채운다 — 하류(변화
              마킹·board 단계)는 전량 판독과 동일한 형태의 데이터를 본다.
        """
        if not idxs:
            return 0
        ph = ",".join(["%s"] * len(idxs))
        sql = (f"UPDATE t_frame_baseball_board_detail SET txt = %s "
               f"WHERE v_id = %s AND kind = %s AND idx IN ({ph})")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (txt, v_id, kind, *idxs))
                return cur.rowcount

    async def fetch_txt_series(
        self, v_id: int, exclude: tuple[str, ...] = CHANGE_EXCLUDE_KINDS
    ) -> list[tuple[str, int, str]]:
        """
        Summary:
            변화 비교용 판독값을 (kind, idx, txt) 로 조회한다 — kind 별 idx 오름차순.
        Args:
            v_id (int): 영상 id. exclude: 비교에서 뺄 kind (기본 ETC).
        Returns:
            list[tuple[str, int, str]]: txt 가 빈 행은 제외(가짜 변화 방지).
        """
        ph = ",".join(["%s"] * len(exclude))
        sql = (f"SELECT kind, idx, txt FROM t_frame_baseball_board_detail "
               f"WHERE v_id = %s AND txt <> '' AND kind NOT IN ({ph}) "
               f"ORDER BY kind, idx")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (v_id, *exclude))
                return [(str(k), int(i), str(t)) for k, i, t in await cur.fetchall()]

    async def count_txt(self, v_id: int) -> int:
        """판독값이 채워진 행 수 — 재요청 가드(409)·상태 조회용."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM t_frame_baseball_board_detail WHERE v_id = %s AND txt <> ''",
                    (v_id,))
                row = await cur.fetchone()
        return int(row[0]) if row else 0
