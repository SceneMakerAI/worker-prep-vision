"""장면 분할 — scenedetect ContentDetector 로 동적 경계를 잡고 정수 초 타일로 후처리.

검증된 로직(agent-vision onboard_detect) 이식: min-sec 병합·max-sec 강제분할·정수 초 스냅으로
청크명·t_segment 정합을 보장한다. **고정 6초 그리드가 아니라 콘텐츠 변화 기반 동적 경계.**
scenedetect·opencv 는 CPU 블로킹이라 호출자(pipeline)가 asyncio.to_thread 로 감싼다.
"""

from pathlib import Path

from log import get_logger

log = get_logger(__name__)


def _postprocess(
    scenes: list[tuple[float, float]], total: float, min_sec: float, max_sec: float) -> list[tuple[int, int, int]]:
    """float 장면 → min-sec 병합 → max-sec 강제분할 → 정수 초 틈없는 타일 (seg_id, start, end)."""
    merged: list[list[float]] = []
    for s, e in scenes:
        if merged and (e - s) < min_sec:
            merged[-1][1] = e          # 너무 짧으면 직전 세그에 흡수
        else:
            merged.append([s, e])
    if len(merged) >= 2 and (merged[0][1] - merged[0][0]) < min_sec:
        merged[1][0] = merged[0][0]    # 첫 세그가 짧으면 다음 세그가 흡수
        merged.pop(0)

    split: list[tuple[float, float]] = []
    for s, e in merged:
        dur = e - s
        
        if dur <= max_sec:
            split.append((s, e))
            continue
        
        n = int(dur // max_sec) + (1 if dur % max_sec else 0)   # 균등 분할 개수
        step = dur / n
        split.extend((s + step * i, s + step * (i + 1)) for i in range(n))

    hi = int(round(total))
    pts = sorted({0} | {int(round(e)) for _, e in split} | {hi})
    pts = [p for p in pts if 0 <= p <= hi]
    
    return [(i + 1, pts[i], pts[i + 1]) for i in range(len(pts) - 1) if pts[i + 1] > pts[i]]


def detect_windows(
    path: Path, threshold: float, min_sec: float, max_sec: float, min_scene_frames: int,
    frame_skip: int = 0) -> list[tuple[int, int, int]]:
    """
    Summary:
        원본 영상을 scenedetect 로 분석해 (seg_id, start_sec, end_sec) 세그먼트 타일을 반환한다.
    Args:
        path (Path): 원본 영상 로컬 경로.
        threshold (float): ContentDetector threshold(낮을수록 촘촘).
        min_sec (float): 세그 최소 길이 — 미만이면 이웃과 병합.
        max_sec (float): 세그 최대 길이 — 초과하면 균등분할.
        min_scene_frames (int): 컷 간 최소 프레임 간격.
        frame_skip (int): N프레임 건너뛰고 1장만 검사(0=전 프레임). 속도↑·컷 정밀도↓.
    Returns:
        list[tuple[int, int, int]]: (seg_id, start_sec, end_sec) 정수 초 타일(틈·겹침 없음).
    Description:
        - CPU 블로킹(디코딩) — 호출자가 asyncio.to_thread 로 감싼다.
        - 장면이 하나도 안 잡히면(단조 영상) 전체를 한 장면으로 보고 후처리한다.
    """
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import ContentDetector

    video = open_video(str(path))
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_frames))
    sm.detect_scenes(video, frame_skip=frame_skip, show_progress=False)
    
    scenes = [(s.get_seconds(), e.get_seconds()) for s, e in sm.get_scene_list()]
    total = video.duration.get_seconds()
    
    windows = _postprocess(scenes or [(0.0, total)], total, min_sec, max_sec)
    log.info("분할: %s (threshold=%s) → %d세그(총 %.0fs)", path.name, threshold, len(windows), total)
    
    return windows
