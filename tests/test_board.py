"""전광판 판독 도메인 회귀 테스트 — '검증된 상수 보호' 불변식(CLAUDE.md)의 집행 지점.

합성 크롭(노이즈 낀 단색)으로 그룹핑(MAE 경계·잡음 필터·공백 분리)을, 순수 함수로
간격 샘플링·후처리·변화 검출을 검증한다. DB·VLM 은 부르지 않는다.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from board import change, postprocess, select
from board.dedup import dedup_runs, vote_samples

RNG = np.random.default_rng(7)


def _crop(d: Path, ts: int, tone: int) -> tuple[int, int, Path]:
    """방송 노이즈(±3)를 흉내낸 단색 크롭 1장 — (idx, sec, path). 1fps 라 idx==sec."""
    img = np.full((19, 31, 3), tone, np.uint8)
    noisy = np.clip(img.astype(int) + RNG.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
    p = d / f"{ts:05d}.jpg"
    cv2.imwrite(str(p), noisy)
    return (ts, ts, p)


@pytest.fixture
def items(tmp_path: Path) -> list[tuple[int, int, Path]]:
    """상태 A(0~20s) → 공백 → A(40~58s) → B(60~78s) → 1장 반짝임(80s)."""
    out = []
    for t in range(0, 21, 2):
        out.append(_crop(tmp_path, t, 200))
    for t in range(40, 59, 2):
        out.append(_crop(tmp_path, t, 200))
    for t in range(60, 79, 2):
        out.append(_crop(tmp_path, t, 60))
    out.append(_crop(tmp_path, 80, 130))
    return out


def test_dedup_runs(items):
    runs = dedup_runs(items, mae_th=8.0, min_cnt=2, gap_sec=4.0)

    # 구간 3개: 공백으로 갈라진 A 2개(같은 그룹) + B 1개. 1장 반짝임은 제거.
    assert len(runs) == 3
    assert runs[0].group_id == runs[1].group_id != runs[2].group_id
    assert (runs[0].items[0][1], runs[0].items[-1][1]) == (0, 20)
    assert (runs[1].items[0][1], runs[1].items[-1][1]) == (40, 58)
    assert (runs[2].items[0][1], runs[2].items[-1][1]) == (60, 78)
    assert not any(r.items[0][1] == 80 for r in runs)


def test_vote_samples(items):
    runs = dedup_runs(items, mae_th=8.0, min_cnt=2, gap_sec=4.0)
    gid = runs[0].group_id
    assert len(vote_samples(runs, gid, 1)) == 1     # 최장 구간의 중간 장
    three = vote_samples(runs, gid, 3)              # 그룹 전체에서 시작/중간/끝
    assert len(three) == 3
    assert three[0].stem == "00000" and three[-1].stem == "00058"


def test_sample_by_interval():
    # 공백이 있어도 재개 직후 프레임을 놓치지 않는다(idx_sec 기준 greedy)
    targets = [(i, s) for i, s in [(1, 1), (2, 2), (3, 3), (10, 10), (11, 11), (12, 12)]]
    assert [s for _, s in select.sample_by_interval(targets, 2)] == [1, 3, 10, 12]
    assert [s for _, s in select.sample_by_interval(targets, 3)] == [1, 10]
    assert len(select.sample_by_interval(targets, 1)) == len(targets)


def test_postprocess_base():
    raw = "First (Right) : On\nSecond (Top) : On\nThird (Left) : Off"
    assert postprocess.apply("BASE", raw) == ("1루, 2루", True)
    assert postprocess.apply("BASE", "First (Right) : Off\nSecond (Top) : Off\nThird (Left) : Off") == ("없음", True)
    assert postprocess.apply("BASE", "1루, 2루") == ("1루, 2루", True)      # 멱등
    txt, ok = postprocess.apply("BASE", "이상한 응답")                       # 실패 → 원문 보존
    assert txt == "이상한 응답" and not ok
    assert postprocess.apply("COUNT", "3-2") == ("3-2", True)               # 비등록 kind 는 원문


def test_change_detect():
    series = [
        ("INNING", 10, "1회초"), ("INNING", 12, "1회초"), ("INNING", 14, "1회말"),
        ("TEAM", 10, "KIA 0: 삼성 0"), ("TEAM", 12, "삼성 0: KIA 0"),   # 순서 뒤집힘 = 무변화
        ("TEAM", 14, "KIA 1: 삼성 0"),
    ]
    changed, per_kind = change.detect(series)
    assert changed == {10, 14}
    assert per_kind == {"INNING": 2, "TEAM": 2}
