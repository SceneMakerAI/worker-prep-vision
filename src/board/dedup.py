"""크롭 시간축 그룹핑 — 연속 크롭을 '같은 보드 상태'끼리 묶어 VLM 판독 대상을 최소화한다.

방송 노이즈 때문에 같은 내용도 픽셀이 미세하게 달라 해시(md5·dHash) 중복제거는 무효 —
축소·블러 후 픽셀 MAE 로 비교한다(노이즈 ~1.0 vs 실제 상태 변화 >5, 임계 5~12 구간 플래토).
비교 기준은 항상 '그룹 첫 이미지'로 고정한다(기준 갱신 시 점진 드리프트로 경계가 밀림).
검증 근거: 4경기(v200~203) 82k장 육안 대조 — 임계 8, 32×20 축소, 3×3 블러 조합.
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from log import get_logger

log = get_logger(__name__)


@dataclass
class StateRun:
    """같은 보드 상태가 연속 유지된 한 구간(공백 없이).

    group_id 는 상태 그룹 번호 — 크롭 공백(gap)으로 갈라진 구간들은 내용이 같으므로
    같은 group_id 를 공유하고, VLM 판독도 그룹당 1회만 한다.
    """
    group_id: int
    files: list[tuple[int, Path]] = field(default_factory=list)   # (초, 경로) 시간순

    @property
    def start(self) -> int:
        return self.files[0][0]

    @property
    def end(self) -> int:
        return self.files[-1][0]

    @property
    def count(self) -> int:
        return len(self.files)


def _feature(path: Path) -> np.ndarray | None:
    """비교용 특징 — 그레이 32×20 축소 + 3×3 블러(검증된 조합, 변경 주의)."""
    g = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if g is None:
        return None
    return cv2.GaussianBlur(cv2.resize(g, (32, 20)), (3, 3), 0).astype(np.float32)


def _list_crops(crops_dir: Path) -> list[tuple[int, Path]]:
    """크롭 파일 목록 — 파일명(초 정수) 기준 시간순. 규칙 밖 파일명은 무시."""
    out = []
    for p in crops_dir.glob("*.jpg"):
        try:
            out.append((int(p.stem), p))
        except ValueError:
            continue
    return sorted(out)


def dedup_runs(crops_dir: Path, mae_th: float, min_cnt: int, gap_sec: float) -> list[StateRun]:
    """
    Summary:
        한 kind 의 크롭 디렉토리를 시간축으로 그룹핑해 상태 구간(StateRun) 목록을 만든다.
    Args:
        crops_dir (Path): {vod_root}/{v_id}/crops/{kind}.
        mae_th (float): 그룹 첫 이미지와의 MAE 가 초과하면 새 그룹.
        min_cnt (int): 그룹 총 장수가 미만이면 제거(공백 분리 '전' 그룹 기준으로 판정).
        gap_sec (float): 인접 크롭 시각 공백이 초과하면 구간 분리(보드 부재 — 상태 유지 아님).
    Returns:
        list[StateRun]: 시간순 상태 구간. 같은 그룹이 공백으로 갈라지면 group_id 를 공유한다.
    Description:
        - 필터(min_cnt)는 공백 분리 전 그룹 장수로 판정한다 — 분리로 생긴 1장짜리
          진짜 관측이 잘려나가지 않게(1장 그룹 제거의 목적은 전환 애니메이션·오탐 제거).
    """
    files = _list_crops(crops_dir)
    groups: list[list[tuple[int, Path]]] = []
    ref: np.ndarray | None = None
    for ts, p in files:
        img = _feature(p)
        if img is None:
            log.warning("크롭 읽기 실패(무시): %s", p)
            continue
        if ref is None or float(np.abs(img - ref).mean()) > mae_th:
            groups.append([(ts, p)])
            ref = img          # 기준 = 그룹 첫 이미지(갱신 안 함 — 드리프트 방지)
        else:
            groups[-1].append((ts, p))

    runs: list[StateRun] = []
    for gid, group in enumerate(g for g in groups if len(g) >= min_cnt):
        run = StateRun(group_id=gid, files=[group[0]])
        for prev, cur in zip(group, group[1:]):
            if cur[0] - prev[0] > gap_sec:
                runs.append(run)
                run = StateRun(group_id=gid, files=[])
            run.files.append(cur)
        runs.append(run)
    return runs


def vote_samples(runs: list[StateRun], group_id: int, k: int) -> list[Path]:
    """그룹의 판독 표본 — k=1 이면 최장 구간의 중간 장(전환 오염 최소),
    k≥3 이면 그룹 전체 파일에서 첫/중간/끝 등 균등 위치 k장(다수결용)."""
    files = [p for r in runs if r.group_id == group_id for _, p in r.files]
    if k <= 1:
        longest = max((r for r in runs if r.group_id == group_id), key=lambda r: r.count)
        return [longest.files[longest.count // 2][1]]
    idx = {round(i * (len(files) - 1) / (k - 1)) for i in range(k)}
    return [files[i] for i in sorted(idx)]
