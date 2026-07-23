"""장면 분할 — scenedetect ContentDetector 로 동적 경계를 잡고 정수 초 타일로 후처리.

검증된 로직(agent-vision onboard_detect) 이식: min-sec 병합·max-sec 강제분할·정수 초 스냅으로
청크명·t_segment 정합을 보장한다. **고정 6초 그리드가 아니라 콘텐츠 변화 기반 동적 경계.**
탐지가 계산하는 프레임별 content_val 을 수집해 세그별 motion_score(중앙값)도 함께 낸다.
scenedetect·opencv 는 CPU 블로킹이라 호출자(pipeline)가 asyncio.to_thread 로 감싼다.
"""

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import median

from log import get_logger

log = get_logger(__name__)

# ContentDetector 가 프레임마다 기록하는 변화 점수 키 — 세그 모션 점수의 원천
_SCORE_KEY = "content_val"


def _to_frame_skip(fps: float, detect_fps: float) -> int:
    """검사 밀도(detect_fps, 초당 검사 장수)를 원본 fps 기준 frame_skip 으로 환산(0=전 프레임)."""
    return max(0, round(fps / detect_fps) - 1) if detect_fps > 0 else 0


def _collect_scores(stats, fps: float, f_lo: int, f_hi: int,
                    lo_open: float, hi: float) -> list[tuple[float, float]]:
    """StatsManager 에서 (lo_open, hi] 초 범위의 (초, content_val) 목록을 수집한다."""
    out = []
    for f in range(f_lo, f_hi + 1):
        if not stats.metrics_exist(f, (_SCORE_KEY,)):
            continue
        sec = f / fps
        if lo_open < sec <= hi:
            out.append((sec, float(stats.get_metrics(f, (_SCORE_KEY,))[0])))
    return out


def _motion_scores(windows: list[tuple[int, int, int]],
                   frame_scores: list[tuple[float, float]]) -> list[float | None]:
    """세그 타일별 content_val **중앙값** — 경계 컷 프레임의 스파이크에 강건한 대표 모션 점수."""
    fs = sorted(frame_scores)
    out: list[float | None] = []
    i = 0
    for _seg, start, end in windows:
        while i < len(fs) and fs[i][0] < start:
            i += 1
        j = i
        while j < len(fs) and fs[j][0] < end:
            j += 1
        vals = [v for _, v in fs[i:j]]
        out.append(round(median(vals), 2) if vals else None)
        i = j
    return out


def _detect_cuts_range(path_str: str, threshold: float, min_sec: float, detect_fps: float,
                       start: float, end: float,
                       pad: float) -> tuple[list[float], list[tuple[float, float]]]:
    """
    Summary:
        [start-pad, end+pad] 구간만 탐지해 (start, end] 안의 컷 시각 목록과
        프레임별 (초, content_val) 점수 목록을 반환한다.
    Description:
        - 병렬 워커(별도 프로세스)용. 패딩은 탐지 문맥 확보용이고, 반환은 담당 구간의
          '실제 감지된 컷'뿐 — 청크 이음새 자체는 컷 후보가 아니므로 강제 경계가 생기지 않는다.
        - 경계·점수 소속 모두 반개구간 (start, end] — 인접 청크와 중복 반환 없음
          (첫 청크만 0초 프레임 포함). 점수는 탐지가 이미 계산한 값의 수집이라 추가 비용 미미.
    """
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import ContentDetector
    from scenedetect.frame_timecode import FrameTimecode
    from scenedetect.stats_manager import StatsManager

    video = open_video(path_str)
    fps = video.frame_rate
    lo = max(0.0, start - pad)
    hi = min(video.duration.get_seconds(), end + pad)
    video.seek(FrameTimecode(lo, fps))
    stats = StatsManager()
    sm = SceneManager(stats_manager=stats)
    sm.add_detector(
        ContentDetector(threshold=threshold, min_scene_len=max(1, round(min_sec * fps))))
    sm.detect_scenes(video, end_time=FrameTimecode(hi, fps),
                     frame_skip=_to_frame_skip(fps, detect_fps), show_progress=False)
    cuts = [s.get_seconds() for s, _ in sm.get_scene_list()[1:]]   # 첫 장면 시작=seek 지점 제외
    lo_open = start if start > 0 else -1.0   # 첫 청크는 0초 프레임 포함
    scores = _collect_scores(stats, fps, FrameTimecode(lo, fps).frame_num,
                             FrameTimecode(hi, fps).frame_num, lo_open, end)
    return [c for c in cuts if start < c <= end], scores


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
    path: Path, threshold: float, min_sec: float, max_sec: float,
    detect_fps: float = 0,
    workers: int = 0) -> tuple[list[tuple[int, int, int]], list[float | None]]:
    """
    Summary:
        원본 영상을 scenedetect 로 분석해 세그먼트 타일과 세그별 모션 점수를 반환한다.
    Args:
        path (Path): 원본 영상 로컬 경로.
        threshold (float): ContentDetector threshold(낮을수록 촘촘).
        min_sec (float): 세그 최소 길이 — 탐지 단계 최소 컷 간격으로도 쓰이고, 미만이면 이웃과 병합.
        max_sec (float): 세그 최대 길이 — 초과하면 균등분할.
        detect_fps (float): 검사 밀도(초당 검사 프레임 수, 0=전 프레임). 영상 fps 에서 skip 환산.
        workers (int): 병렬 탐지 프로세스 수(0·1=단일 패스). 시간축 청크 분담, 컷 합집합 병합.
    Returns:
        tuple: ((seg_id, start_sec, end_sec) 정수 초 타일 목록(틈·겹침 없음),
                타일과 같은 순서의 세그별 motion_score 목록(구간 content_val 중앙값, 없으면 None)).
    Description:
        - CPU 블로킹(디코딩) — 호출자가 asyncio.to_thread 로 감싼다.
        - 병렬 모드: 청크는 '컷 후보'가 아니라 '탐지 분담 구간' — 이음새는 경계로 승격되지 않고,
          각 청크가 패딩 문맥으로 감지한 실제 컷의 합집합만 장면 경계가 된다.
        - 장면이 하나도 안 잡히면(단조 영상) 전체를 한 장면으로 보고 후처리한다.
        - 모션 점수는 탐지가 계산한 프레임별 content_val 의 세그 내 중앙값 — 픽셀 변화 기반
          근사치로, 슬로모·정지 구간 선별 같은 하류 필터링 용도(정밀 옵티컬 플로우 아님).
    """
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import ContentDetector
    from scenedetect.stats_manager import StatsManager

    video = open_video(str(path))
    total = video.duration.get_seconds()

    # 병렬 모드 — 청크당 최소 30초는 되도록 워커 수 자체 조정(짧은 영상은 단일 패스로 폴백)
    n = max(1, min(workers, int(total // 30))) if workers > 1 else 1
    if n > 1:
        step = total / n
        pad = max(2.0, 2 * min_sec)
        ctx = mp.get_context("spawn")   # opencv fork 비안전 회피
        with ProcessPoolExecutor(max_workers=n, mp_context=ctx) as ex:
            futs = [ex.submit(_detect_cuts_range, str(path), threshold, min_sec, detect_fps,
                              i * step, (i + 1) * step, pad) for i in range(n)]
            results = [f.result() for f in futs]
        cuts = sorted({c for cs, _ in results for c in cs})
        frame_scores = [fs for _, ss in results for fs in ss]
        pts = [0.0, *cuts, total]
        scenes = list(zip(pts[:-1], pts[1:]))
        windows = _postprocess(scenes, total, min_sec, max_sec)
        motions = _motion_scores(windows, frame_scores)
        log.info("분할(병렬 %d워커): %s (threshold=%s, detect_fps=%s) → %d세그(총 %.0fs)",
                 n, path.name, threshold, detect_fps, len(windows), total)
        return windows, motions

    # 단일 패스 — 컷 간 최소 간격은 min_sec 에서, frame_skip 은 detect_fps 에서 유도(fps 무관 일관)
    fps = video.frame_rate
    min_scene_len = max(1, round(min_sec * fps))
    frame_skip = _to_frame_skip(fps, detect_fps)
    stats = StatsManager()
    sm = SceneManager(stats_manager=stats)
    sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len))
    sm.detect_scenes(video, frame_skip=frame_skip, show_progress=False)

    scenes = [(s.get_seconds(), e.get_seconds()) for s, e in sm.get_scene_list()]
    windows = _postprocess(scenes or [(0.0, total)], total, min_sec, max_sec)
    frame_scores = _collect_scores(stats, fps, 0, int(total * fps) + 1, -1.0, total)
    motions = _motion_scores(windows, frame_scores)
    log.info("분할: %s (threshold=%s, skip=%d) → %d세그(총 %.0fs)",
             path.name, threshold, frame_skip, len(windows), total)

    return windows, motions
