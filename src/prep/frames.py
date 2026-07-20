"""프레임 추출 — ffmpeg(CPU)으로 세그먼트별 대표 프레임 jpg 를 뽑아 디스크에 둔다.

세그당 프레임 수 = max(1, round(길이 × fps)) — **짧은 세그도 최소 1장** 보장, 긴 세그는 fps 비례.
프레임은 세그 구간을 n등분한 각 소구간의 '중앙 시각'에서 뽑는다(경계 프레임의 전환·블랙 회피).
산출 규칙 {frames_root}/{v_id}/seg{id:05d}/f{i:03d}.jpg 는 agent-vision frame_paths(file://) 와 정합.
ffmpeg 은 블로킹 subprocess — 세그별로 스레드풀 병렬, 호출자(pipeline)는 asyncio.to_thread 로 감싼다.
"""

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import Settings
from log import get_logger

log = get_logger(__name__)


def frame_count(duration: float, fps: float) -> int:
    """세그 길이·fps → 추출 프레임 수. 최소 1장(대표 프레임) 보장."""
    return max(1, round(duration * fps))


def _timestamps(start: int, end: int, n: int) -> list[float]:
    """[start, end) 를 n등분한 각 소구간의 중앙 시각 목록(경계 회피)."""
    dur = end - start
    step = dur / n
    return [start + step * (i + 0.5) for i in range(n)]


def _extract_one(source: Path, ts: float, out: Path, scale: str, quality: int) -> bool:
    """단일 프레임 추출 — 입력 시킹(-ss before -i)으로 빠르게 1장 뽑아 jpg 저장."""
    vf = [] if scale == "none" else ["-vf", f"scale={scale}"]
    cmd = ["ffmpeg", "-nostdin", "-y", "-ss", f"{ts:.3f}", "-i", str(source),
           "-frames:v", "1", *vf, "-q:v", str(quality), str(out)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        log.warning("프레임 추출 실패 ts=%.2f → %s: %s", ts, out.name, r.stderr[-200:].decode(errors="replace"))
    return r.returncode == 0 and out.is_file()


def extract_frames(source: Path, windows: list[tuple[int, int, int]], settings: Settings,
                   v_id: int) -> dict:
    """
    Summary:
        세그먼트 목록 각각에 대해 대표 프레임 jpg 를 추출해 세그 디렉토리에 저장한다.
    Args:
        source (Path): 원본 영상 로컬 경로.
        windows (list[tuple[int, int, int]]): (seg_id, start_sec, end_sec) 목록.
        settings (Settings): fps·scale·quality·동시성·frames_root.
        v_id (int): 대상 영상 id(출력 경로 구성).
    Returns:
        dict: {"segments": 처리 세그수, "frames": 추출 프레임수, "failed": 실패 프레임수}.
    Description:
        - CPU 블로킹(ffmpeg×N) — 호출자가 asyncio.to_thread 로 감싼다. 내부는 스레드풀 병렬.
        - 세그 디렉토리는 재실행 안전을 위해 비우고 새로 채운다(force 시 상위에서 v_id 째 지움).
    """
    jobs: list[tuple[float, Path]] = []
    for seg_id, start, end in windows:
        d = settings.seg_frame_dir(v_id, seg_id)
        if d.exists():
            shutil.rmtree(d)          # 재실행 안전 — 세그 프레임 새로
        d.mkdir(parents=True, exist_ok=True)
        n = frame_count(end - start, settings.prep_fps)
        for i, ts in enumerate(_timestamps(start, end, n)):
            jobs.append((ts, d / f"f{i:03d}.jpg"))

    ok = 0
    with ThreadPoolExecutor(max_workers=settings.prep_concurrency) as ex:
        results = ex.map(
            lambda job: _extract_one(source, job[0], job[1], settings.prep_scale, settings.prep_jpg_quality),
            jobs)
        ok = sum(1 for r in results if r)

    stats = {"segments": len(windows), "frames": ok, "failed": len(jobs) - ok}
    log.info("프레임 추출: v_id=%s, %d세그 → %d장(실패 %d)", v_id, stats["segments"], stats["frames"], stats["failed"])
    return stats
