"""영상 repository — t_video 데이터 접근(prep 용 서브셋).

DB 연결(풀)은 Database(persistence.db)가 쥐고, 여기서는 SQL·도메인 매핑만 담당한다.
prep 은 t_video 존재를 전제로 세그먼트를 만들고, 완료 시 진행상태를 갱신한다.
"""

from asyncmy.cursors import DictCursor

from persistence.db import Database
from log import get_logger

log = get_logger(__name__)

# t_video.status_code (t_code, object=VIDEO)
VIDEO_STATUS_FAILED = -1            # 처리 실패
VIDEO_STATUS_FFMPEG_INPUT = 1001    # FFMPEG(전처리) 입력
VIDEO_STATUS_FFMPEG_DONE = 1002     # FFMPEG(전처리) 완료 — prep(분할+프레임) 성공 시 여기로
VIDEO_STATUS_DIALOGUE_INPUT = 1005  # 대사 처리 입력
VIDEO_STATUS_DIALOGUE_DONE = 1006   # 대사 처리 완료 — 이후 agent-vision 분석 가능
VIDEO_STATUS_SCENE_INPUT = 1010     # 장면 분석 입력
VIDEO_STATUS_SCENE_DONE = 1011      # 장면 분석 완료
VIDEO_STATUS_ALL_DONE = 1000        # 모든 처리 완료


class VideoRepo:
    """t_video 접근 객체 — 영상 메타 조회·상태 갱신."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, v_id: int) -> dict | None:
        """
        Summary:
            v_id 로 영상 1건을 조회한다.
        Args:
            v_id (int): 대상 영상 id.
        Returns:
            dict | None: 영상 행(v_id, cate_id, name, segment_sec, dir, status_code)
                또는 없으면 None.
        Description:
            - 영상 길이는 t_video 에 없다 — 실제 경계는 scenedetect 가 원본에서 직접 정한다.
        """
        sql = (
            "SELECT v_id, cate_id, name, segment_sec, dir, status_code "
            "FROM t_video WHERE v_id = %s"
        )
        async with self._db.acquire() as conn:
            async with conn.cursor(cursor=DictCursor) as cur:
                await cur.execute(sql, (v_id,))
                row = await cur.fetchone()
        return row

    async def set_status(self, v_id: int, status_code: int) -> None:
        """
        Summary:
            영상의 진행상태(status_code)를 갱신한다.
        Args:
            v_id (int): 대상 영상 id.
            status_code (int): t_code.code 값(예: 1002 전처리 완료, -1 실패).
        """
        sql = "UPDATE t_video SET status_code = %s WHERE v_id = %s"
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status_code, v_id))
        log.info("영상 상태 갱신: v_id=%s → status_code=%s", v_id, status_code)
