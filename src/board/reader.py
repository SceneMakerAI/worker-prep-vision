"""VLM 판독 — 그룹 앵커 판독 + 불일치 시 이분탐색으로 그룹을 구간 확정한다.

프롬프트는 board.prompts(레퍼런스 검증본) — 시스템 메시지로 넣고 이미지는 유저 턴.
비교는 후처리(postprocess)까지 마친 저장값 기준 — BASE 3줄 블록의 표기 차이가
경계 판정을 흔들지 않게. 동기 urllib + 스레드풀 — 호출자가 asyncio.to_thread 로 감싼다.
"""

import base64
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from board import postprocess
from board.prompts import PROMPTS
from config import Settings
from log import get_logger

log = get_logger(__name__)

# temperature 0: 같은 이미지에 같은 답 — 재실행·검증·다수결이 성립한다.
# max_tokens 128: txt 가 varchar(128) 이라 응답이 그 안에 들어와야 한다.
_SAMPLING = {"temperature": 0, "max_tokens": 128}


class ReadFailure(Exception):
    """표본 판독 실패(재시도 소진) — 호출자가 건별 격리한다(해당 그룹 txt 만 비어 남음)."""


def _ask_one(kind: str, path: Path, settings: Settings) -> str:
    """크롭 1장 판독 — 실패 시 1회 재시도, 소진하면 ReadFailure. 응답 원문(strip)을 반환."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    body = json.dumps({
        "model": settings.vlm_model,
        **_SAMPLING,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": PROMPTS[kind]}]},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ]},
        ],
    }).encode()
    url = settings.vlm_url.rstrip("/") + "/v1/chat/completions"
    err = "unknown"
    for _ in range(2):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=settings.vlm_timeout) as r:
                content = json.loads(r.read())["choices"][0]["message"]["content"] or ""
            return content.strip()
        except Exception as e:  # 타임아웃·연결 실패 — 재시도 후 격리용 예외로 승격
            err = str(e)[:120]
    raise ReadFailure(f"{kind} {path.name}: {err}")


def read_one(kind: str, path: Path, settings: Settings) -> tuple[str, bool]:
    """크롭 1장 판독+후처리 — (저장할 txt, 후처리 성공 여부). 실패는 ReadFailure."""
    return postprocess.apply(kind, _ask_one(kind, path, settings))


def resolve_group(kind: str, files: list[Path], k: int, settings: Settings,
                  _read=read_one) -> list[tuple[int, int, str, bool]]:
    """
    Summary:
        그룹을 앵커 k점 판독으로 검증하고, 값이 갈리면 이분탐색으로 내부 경계를 찾아
        (시작위치, 끝위치, txt, 후처리성공) 구간 목록으로 확정한다.
    Args:
        kind (str): 판독 항목(대문자). files (list[Path]): 그룹 전체 크롭(시간순).
        k (int): 앵커 수(시작/중간/끝 = 3). settings: VLM 엔드포인트.
        _read: 판독 함수(테스트 주입용).
    Returns:
        list[tuple[int, int, str, bool]]: files 위치 기준 반개 아님·양끝 포함 구간들.
    Raises:
        ReadFailure: 판독 실패(호출자가 그룹 단위 격리 — 해당 그룹 txt 만 비어 남음).
    Description:
        - **만장일치면 그룹 전체 한 구간**(기존 다수결과 동일 결론, 비용 k회).
        - 앵커 값이 갈리면 픽셀 비교(dedup)가 놓친 실제 상태 변화가 그룹 안에 있다는
          확정 신호다(예: 크롭 공백 중 점수 변경, 저대비 숫자 변화). 인접 앵커 사이를
          이분탐색해 경계를 정확히 찾는다 — 추가 판독은 경계당 log₂(구간길이)회뿐.
        - 값이 같은 두 앵커 사이는 균일로 간주한다(ABA 는 dedup 경계 검출이 1차 방어).
    """
    n = len(files)
    cache: dict[int, tuple[str, bool]] = {}

    def val(pos: int) -> tuple[str, bool]:
        if pos not in cache:
            cache[pos] = _read(kind, files[pos], settings)
        return cache[pos]

    if n == 1 or k <= 1:
        pos = n // 2
        txt, ok = val(pos)
        return [(0, n - 1, txt, ok)]

    anchors = sorted({round(i * (n - 1) / (k - 1)) for i in range(max(2, k))})
    for a in anchors:
        val(a)

    bounds: list[int] = []                     # 새 구간이 '시작'되는 위치들
    stack = list(zip(anchors, anchors[1:]))
    while stack:
        lo, hi = stack.pop()
        if val(lo)[0] == val(hi)[0] or hi - lo <= 0:
            continue
        if hi - lo == 1:
            bounds.append(hi)
            continue
        mid = (lo + hi) // 2
        stack += [(lo, mid), (mid, hi)]

    starts = [0, *sorted(set(bounds))]
    segments = []
    for s, e in zip(starts, [*starts[1:], n]):
        txt, ok = val(s)
        segments.append((s, e - 1, txt, ok))
    return segments


def read_groups(jobs: list[tuple[str, list[Path]]], settings: Settings) -> list:
    """
    Summary:
        (kind, 그룹 전체 크롭) 목록을 스레드풀 병렬로 확정 판독해 같은 순서로 돌려준다.
    Args:
        jobs: 그룹당 하나 — kind 와 그룹 전체 크롭 경로(시간순).
        settings (Settings): VLM 엔드포인트·동시성·앵커 수(board_vote_k).
    Returns:
        list[list[tuple[int, int, str, bool]] | ReadFailure]: jobs 순서대로
            구간 목록 또는 실패.
    Description:
        - 블로킹(HTTP×N) — 호출자가 asyncio.to_thread 로 감싼다.
        - 실패는 예외 대신 값으로 돌려준다 — 한 그룹 실패가 배치를 못 죽이게(건별 격리).
    """
    def one(job):
        try:
            return resolve_group(job[0], job[1], settings.board_vote_k, settings)
        except ReadFailure as e:
            return e

    with ThreadPoolExecutor(max_workers=settings.board_read_concurrency) as ex:
        return list(ex.map(one, jobs))
