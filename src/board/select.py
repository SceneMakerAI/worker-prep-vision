"""판독 대상 프레임을 간격으로 솎아낸다 — 순수 함수 (agent-vision3 read/select 이식).

대상 조회 자체는 persistence.frames.fetch_targets 가 하고(normal=0 AND detect_major_obj=5),
여기는 그 목록을 간격(초)으로 줄이는 순수 함수만 갖는다. DB·파일시스템을 모른다.

간격을 두는 이유: 전광판은 타격 이후 다음 투구 전에 갱신되므로 연속된 매 초를 다 볼 필요가
없다. 변화 시점을 놓치지 않는 선에서 건수를 줄이는 게 목적이다. 운영값 2초는 전광판 갱신
주기 실측(2s 79~91%)으로 검증됐고, **앞단 crop 생성 대상과 이 샘플이 1:1 이어야 한다**
(상류 계약 — 간격을 바꾸면 crop 이 없는 프레임을 고르게 된다).
"""


def sample_by_interval(
    targets: list[tuple[int, int]], interval_sec: float
) -> list[tuple[int, int]]:
    """
    Summary:
        대상 (idx, idx_sec) 목록을 interval_sec 초 간격으로 솎아낸다.
    Args:
        targets (list[tuple[int, int]]): (프레임 순번, 영상 내 시각(초)) 목록. 정렬 무관.
        interval_sec (float): 간격(초). 1 이하면 전부 반환.
    Returns:
        list[tuple[int, int]]: 시각 오름차순으로 선택된 (idx, idx_sec) 목록.
    Description:
        - 규칙: 첫 프레임을 고르고, 이후로는 '직전에 고른 시각 + interval_sec 이상'인 첫
          프레임을 고른다(greedy).
        - **목록 인덱스가 아니라 idx_sec 기준**인 게 핵심이다. 대상에 공백 구간이 있어도
          (광고 등으로 프레임이 끊겨도) 공백 이후 첫 프레임은 간격 조건을 자동으로 만족해
          반드시 잡힌다. 인덱스 stride 방식이면 공백을 건너뛰며 밀려 재개 직후를 놓친다.
    """
    ordered = sorted(targets, key=lambda t: (t[1], t[0]))
    if interval_sec <= 1:
        return ordered

    picked: list[tuple[int, int]] = []
    next_sec: float | None = None
    for idx, sec in ordered:
        if next_sec is None or sec >= next_sec:
            picked.append((idx, sec))
            next_sec = sec + interval_sec
    return picked
