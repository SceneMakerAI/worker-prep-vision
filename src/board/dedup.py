"""크롭 시간축 그룹핑 — 연속 크롭을 '같은 보드 상태'끼리 묶어 VLM 판독량을 줄인다.

**이관의 핵심 최적화**: 전량 판독(v200 실측 19,368건·17.6분)이 너무 느려서, 유사 크롭을
묶고 그룹당 시작/중간/끝 표본만 판독(다수결)해 호출량을 1/10 수준으로 줄인다.

방송 노이즈 때문에 같은 내용도 픽셀이 미세하게 달라 해시(md5·dHash) 중복제거는 무효 —
축소·블러 후 픽셀 MAE 로 비교한다(노이즈 ~1.0 vs 실제 상태 변화 >5, 임계 5~12 구간 플래토).
비교 기준은 항상 '그룹 첫 이미지'로 고정한다(기준 갱신 시 점진 드리프트로 경계가 밀림).
검증 근거: 실경기 4편(v200~203) 크롭 8.2만 장 육안 대조 — 임계 8·32×20 축소·3×3 블러 조합.
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from log import get_logger

log = get_logger(__name__)


@dataclass
class StateRun:
    """같은 보드 상태가 연속 유지된 한 구간(크롭 공백 없이).

    group_id 는 상태 그룹 번호 — 크롭 공백(gap)으로 갈라진 구간들은 내용이 같으므로
    같은 group_id 를 공유하고, VLM 판독도 그룹당 1회(표본 다수결)만 한다.
    """
    group_id: int
    items: list[tuple[int, int, Path]] = field(default_factory=list)   # (idx, sec, 경로) 시간순

    @property
    def idxs(self) -> list[int]:
        return [i for i, _, _ in self.items]

    @property
    def count(self) -> int:
        return len(self.items)


# 비교 해상도 — 높이 20 고정, 폭은 종횡비 비례(최소 32). 정사각형 강제 축소(32×20)는
# TEAM 처럼 폭이 넓은 스트립에서 점수 숫자를 1~2px 로 뭉개 변화가 소실된다
# (v200 실측: 5→6 점수 변화 미검출 → 한 그룹으로 뭉쳐 오답 23건).
_FEAT_H = 20
_TILE_W, _TILE_H = 8, 10


def _feature(path: Path) -> np.ndarray | None:
    """비교용 특징 — 그레이 높이 20(폭 비례) 축소 + 3×3 블러. 좁은 크롭은 기존 32×20 동일."""
    g = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if g is None:
        return None
    h, w = g.shape
    nw = max(32, round(w * _FEAT_H / h))
    return cv2.GaussianBlur(cv2.resize(g, (nw, _FEAT_H)), (3, 3), 0).astype(np.float32)


def _diff(a: np.ndarray, b: np.ndarray) -> float:
    """두 특징의 차이 — 8×10px 고정 격자 타일별 MAE 중 최대값(국소 변화 비희석).

    전역 평균은 넓은 스트립에서 국소(숫자 한 자리) 변화가 희석돼 임계를 못 넘는다.
    타일 최대값은 노이즈 여유(전역 ~1.0, 타일 최대 ~3)가 있어 임계 8 을 그대로 쓴다.
    크기가 다르면(크롭 박스 변경 등) 비교 불가 — 무한대로 보아 새 그룹으로 가른다.
    """
    if a.shape != b.shape:
        return float("inf")
    d = np.abs(a - b)
    h, w = d.shape
    return max(float(d[y:y + _TILE_H, x:x + _TILE_W].mean())
               for y in range(0, h, _TILE_H) for x in range(0, w, _TILE_W))


def dedup_runs(items: list[tuple[int, int, Path]], mae_th: float, min_cnt: int,
               gap_sec: float) -> list[StateRun]:
    """
    Summary:
        한 kind 의 (idx, sec, 크롭경로) 목록을 시간축으로 그룹핑해 상태 구간 목록을 만든다.
    Args:
        items (list[tuple[int, int, Path]]): 판독 대상 크롭 — (idx, idx_sec, 경로) 시간순.
        mae_th (float): 그룹 첫 이미지와의 MAE 가 초과하면 새 그룹.
        min_cnt (int): 그룹 총 장수가 미만이면 제거(공백 분리 '전' 그룹 기준으로 판정).
        gap_sec (float): 인접 크롭 시각 공백이 초과하면 구간 분리(보드 부재 — 상태 유지 아님).
    Returns:
        list[StateRun]: 시간순 상태 구간. 같은 그룹이 공백으로 갈라지면 group_id 를 공유한다.
    Description:
        - 필터(min_cnt)는 공백 분리 전 그룹 장수로 판정한다 — 분리로 생긴 1장짜리
          진짜 관측이 잘려나가지 않게(1장 그룹 제거의 목적은 전환 애니메이션·오탐 제거).
        - CPU 블로킹(cv2×N) — 호출자가 asyncio.to_thread 로 감싼다.
    """
    groups: list[list[tuple[int, int, Path]]] = []
    ref: np.ndarray | None = None
    for idx, sec, p in sorted(items, key=lambda t: t[1]):
        img = _feature(p)
        if img is None:
            log.warning("크롭 읽기 실패(무시): %s", p)
            continue
        if ref is None or _diff(img, ref) > mae_th:
            groups.append([(idx, sec, p)])
            ref = img          # 기준 = 그룹 첫 이미지(갱신 안 함 — 드리프트 방지)
        else:
            groups[-1].append((idx, sec, p))

    runs: list[StateRun] = []
    for gid, group in enumerate(g for g in groups if len(g) >= min_cnt):
        run = StateRun(group_id=gid, items=[group[0]])
        for prev, cur in zip(group, group[1:]):
            if cur[1] - prev[1] > gap_sec:
                runs.append(run)
                run = StateRun(group_id=gid, items=[])
            run.items.append(cur)
        runs.append(run)
    return runs


def vote_samples(runs: list[StateRun], group_id: int, k: int) -> list[Path]:
    """그룹의 판독 표본 — k=1 이면 최장 구간의 중간 장(전환 오염 최소),
    k≥2 면 그룹 전체에서 시작/중간/끝 등 균등 위치 k장(다수결용)."""
    files = [p for r in runs if r.group_id == group_id for _, _, p in r.items]
    if k <= 1:
        longest = max((r for r in runs if r.group_id == group_id), key=lambda r: r.count)
        return [longest.items[longest.count // 2][2]]
    idx = {round(i * (len(files) - 1) / (k - 1)) for i in range(k)}
    return [files[i] for i in sorted(idx)]
