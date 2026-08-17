"""dedup·pipeline 배선 회귀 테스트 — CLAUDE.md '검증된 상수 보호' 불변식의 집행 지점.

합성 크롭(노이즈 낀 단색)으로: 상태 경계(MAE), 1장 그룹 제거, 크롭 공백 구간 분리,
그룹-값 배분 배선(_collect)을 검증한다. DB·VLM 은 부르지 않는다.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from board.dedup import dedup_runs, vote_samples
from board.pipeline import _collect
from config import Settings

RNG = np.random.default_rng(7)


def _save(d: Path, ts: int, tone: int) -> None:
    """방송 노이즈를 흉내낸(±3) 단색 크롭 1장 저장."""
    img = np.full((19, 31, 3), tone, np.uint8)
    noisy = np.clip(img.astype(int) + RNG.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
    cv2.imwrite(str(d / f"{ts:05d}.jpg"), noisy)


@pytest.fixture
def crops(tmp_path: Path) -> Path:
    """상태 A(0~20s) → 공백 → A(40~58s) → B(60~78s) → 1장 반짝임(80s)."""
    d = tmp_path / "1" / "crops" / "inning"
    d.mkdir(parents=True)
    for t in range(0, 21, 2):
        _save(d, t, 200)
    for t in range(40, 59, 2):
        _save(d, t, 200)
    for t in range(60, 79, 2):
        _save(d, t, 60)
    _save(d, 80, 130)
    return d


def test_dedup_runs(crops: Path):
    runs = dedup_runs(crops, mae_th=8.0, min_cnt=2, gap_sec=4.0)

    # 구간 3개: 공백으로 갈라진 A 2개(같은 그룹) + B 1개. 1장 반짝임은 제거.
    assert len(runs) == 3
    assert runs[0].group_id == runs[1].group_id != runs[2].group_id
    assert (runs[0].start, runs[0].end) == (0, 20)
    assert (runs[1].start, runs[1].end) == (40, 58)
    assert (runs[2].start, runs[2].end) == (60, 78)
    assert not any(r.start == 80 for r in runs)


def test_vote_samples(crops: Path):
    runs = dedup_runs(crops, mae_th=8.0, min_cnt=2, gap_sec=4.0)
    gid = runs[0].group_id

    one = vote_samples(runs, gid, 1)
    assert len(one) == 1        # 최장 구간의 중간 장(전환 오염 최소)

    three = vote_samples(runs, gid, 3)
    assert len(three) == 3      # 그룹 전체(공백 구간 포함)에서 균등 위치


def test_collect_wiring(crops: Path, tmp_path: Path):
    """_collect 배선 — rows 시간순 board_id, jobs/job_key 정렬, group_key 전량 매핑 가능."""
    settings = Settings(
        app_port=1, vod_root=str(tmp_path), board_kinds="inning",
        vlm_url="http://placeholder", vlm_model="m", db_user="u", _env_file=None,
    )
    rows, jobs, job_key = _collect(settings, 1)

    assert [r["board_id"] for r in rows] == [1, 2, 3]                  # 시간순 부여
    assert [r["start"] for r in rows] == sorted(r["start"] for r in rows)
    assert len(jobs) == len(job_key) == 2                              # 그룹 2개 → 판독 2회
    assert {r["group_key"] for r in rows} == set(job_key)              # 값 배분 누락 없음
    assert all(r["crop_cnt"] >= 2 for r in rows)
